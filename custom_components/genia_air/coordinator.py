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
