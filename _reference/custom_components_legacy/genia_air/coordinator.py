"""DataUpdateCoordinator for Vaillant Genia Air.

Subscribes to ebusd MQTT topics, parses the JSON payloads ebusd publishes,
maintains an in-memory state map keyed by `(circuit, msg)`, and pushes updates
to listeners (entities).

Coordinator pattern is push-based here: we don't poll, we wait for MQTT to
deliver. The HA entity layer above listens via `async_add_listener()`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# eBUS messages we care about for v0.1. Other discovered messages from the
# bus are stored but no entity exposes them unless declared here or in the
# platforms.
SUBSCRIBED_TOPIC_PATTERN = "{prefix}/+/+"  # ebusd/<circuit>/<msg>


@dataclass
class EbusdMessage:
    """Decoded ebusd MQTT payload, normalised."""

    circuit: str            # "hmu", "ctls2", "broadcast"
    msg: str                # "Status01", "z1ManualTemp", etc. (case as ebusd publishes)
    raw_payload: str        # original JSON string
    fields: dict[str, Any] = field(default_factory=dict)  # field_name_or_idx → value


class GeniaAirCoordinator(DataUpdateCoordinator[dict]):
    """Maintains the live state of the Genia Air via ebusd MQTT messages."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, topic_prefix: str):
        super().__init__(
            hass,
            _LOGGER,
            name=f"genia_air[{entry.entry_id[:8]}]",
            update_interval=None,  # push-based; we don't poll
        )
        self.entry = entry
        self.topic_prefix = topic_prefix
        self._unsub: Callable[[], None] | None = None
        # state[(circuit, msg)] = EbusdMessage
        self.state: dict[tuple[str, str], EbusdMessage] = {}

    async def async_start(self) -> None:
        """Subscribe to the ebusd MQTT firehose."""
        topic = SUBSCRIBED_TOPIC_PATTERN.format(prefix=self.topic_prefix)
        _LOGGER.info("Subscribing to %s", topic)
        self._unsub = await mqtt.async_subscribe(
            self.hass, topic, self._handle_message, qos=0
        )
        # Set empty data so HA doesn't complain about unset
        self.async_set_updated_data({})
        # ebusd publishes some messages spontaneously (HMU telemetry, broadcasts)
        # but CTLS2 controller messages typically only respond to explicit reads.
        # Kick off an initial sync so entities aren't stuck unavailable.
        self.hass.async_create_task(self._async_initial_sync())

    async def _async_initial_sync(self) -> None:
        """Force-read every (circuit, msg) entities depend on, with spacing."""
        # Wait a moment for spontaneous traffic to arrive (avoids re-reading
        # messages that are already in state) and for MQTT to be fully ready.
        await asyncio.sleep(3)

        # Late import to avoid circulars and keep the catalog as source of truth
        from .entities_catalog import ENTITIES

        # Collect targets the platforms care about. The catalog covers sensor /
        # binary_sensor / number; climate + select add a handful of CTLS2 msgs.
        targets: set[tuple[str, str]] = {
            (e.circuit, e.msg) for e in ENTITIES
        }
        targets.update({
            ("ctls2", "z1OpMode"),
            ("ctls2", "z1OpModeCooling"),
            ("ctls2", "z1SfMode"),
            ("ctls2", "z1ManualTemp"),
            ("ctls2", "z1CoolingTemp"),
            ("ctls2", "z1RoomTemp"),
            ("ctls2", "GlobalSystemOff"),
            ("hmu",   "State"),
        })

        pending = [t for t in targets if t not in self.state]
        if not pending:
            _LOGGER.info("Initial sync: all %d messages already received", len(targets))
            return

        _LOGGER.info(
            "Initial sync: requesting %d/%d messages from ebusd",
            len(pending), len(targets),
        )
        # Pace the reads so we don't queue-jam ebusd's polling loop.
        for circuit, msg in pending:
            await self.async_request_read(circuit, msg)
            await asyncio.sleep(0.15)
        _LOGGER.info("Initial sync: requests dispatched")

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _handle_message(self, msg) -> None:
        """Process one incoming MQTT message from ebusd."""
        topic = msg.topic
        payload = msg.payload
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="replace")

        # Topic shape: ebusd/<circuit>/<msg>
        parts = topic.split("/", 2)
        if len(parts) != 3 or parts[0] != self.topic_prefix:
            return
        _, circuit, message = parts

        # Skip subtopics like /set, /get, /errors — only top-level messages
        if "/" in message or message in ("set", "get", "errors"):
            return

        # Parse JSON payload. Ebusd publishes either:
        #   {"<fieldname>": {"value": <v>}}  (single-field)
        #   {"0": {"name":"<fname>","value":<v>}, "1":...}  (multi-field)
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            # Some special topics (global/scan, global/version) publish plain strings
            data = {"value": payload.strip('"')}

        fields: dict[str, Any] = {}
        if isinstance(data, dict):
            for key, val in data.items():
                if isinstance(val, dict) and "value" in val:
                    fields[key] = val["value"]
                else:
                    fields[key] = val

        decoded = EbusdMessage(
            circuit=circuit, msg=message, raw_payload=payload, fields=fields
        )
        self.state[(circuit, message)] = decoded

        # Notify listeners (entities) of new data.
        self.async_set_updated_data(self.state)

    async def async_publish_write(self, circuit: str, msg: str, value: Any) -> None:
        """Publish a write command to ebusd."""
        topic = f"{self.topic_prefix}/{circuit}/{msg}/set"
        payload = str(value)
        _LOGGER.debug("Write %s = %s", topic, payload)
        await mqtt.async_publish(self.hass, topic, payload, qos=0, retain=False)

    async def async_request_read(self, circuit: str, msg: str) -> None:
        """Force ebusd to read a message on demand."""
        topic = f"{self.topic_prefix}/{circuit}/{msg}/get"
        await mqtt.async_publish(self.hass, topic, "?", qos=0, retain=False)
