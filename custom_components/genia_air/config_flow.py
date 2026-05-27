"""Config flow for Vaillant Genia Air."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME
from homeassistant.core import callback

from .const import DOMAIN, LEGACY_UID_MAP

_LOGGER = logging.getLogger(__name__)

CONF_TOPIC_PREFIX = "topic_prefix"
CONF_SYSTEM_TYPE = "system_type"
CONF_MIGRATE = "migrate_legacy"
CONF_LANGUAGE = "language"

SYSTEM_TYPES = {
    "underfloor": "Underfloor heating (recommended)",
    "radiators": "Radiators",
    "mixed": "Mixed",
}


class GeniaAirConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Vaillant Genia Air."""

    VERSION = 1

    def __init__(self) -> None:
        self._topic_prefix: str = "ebusd"
        self._system_type: str = "underfloor"
        self._language: str = "en"
        self._discovered_count: int = 0
        self._legacy_entities_found: int = 0

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 1: ask the user for MQTT topic prefix and system type."""
        if not await mqtt.async_wait_for_mqtt_client(self.hass):
            return self.async_abort(reason="mqtt_unavailable")

        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required(CONF_TOPIC_PREFIX, default="ebusd"): str,
                    vol.Required(CONF_SYSTEM_TYPE, default="underfloor"): vol.In(SYSTEM_TYPES),
                    vol.Required(CONF_LANGUAGE, default="en"): vol.In(["en", "es"]),
                }),
                description_placeholders={
                    "ebusd_repo": "https://github.com/LukasGrebe/ha-addons",
                },
            )

        self._topic_prefix = user_input[CONF_TOPIC_PREFIX]
        self._system_type = user_input[CONF_SYSTEM_TYPE]
        self._language = user_input[CONF_LANGUAGE]

        return await self.async_step_discovery()

    async def async_step_discovery(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 2: subscribe briefly to confirm ebusd is actually publishing."""
        # Wait up to 30 s for at least one ebusd/+/+ message to arrive
        topic = f"{self._topic_prefix}/+/+"
        received_topics: set[str] = set()

        @callback
        def _on_msg(msg) -> None:
            received_topics.add(msg.topic)

        unsub = await mqtt.async_subscribe(self.hass, topic, _on_msg)
        try:
            for _ in range(30):
                await asyncio.sleep(1)
                if received_topics:
                    break
        finally:
            unsub()

        self._discovered_count = len(received_topics)
        if self._discovered_count == 0:
            return self.async_abort(reason="no_ebusd_traffic")

        _LOGGER.info("Detected %d ebusd topics during discovery", self._discovered_count)
        return await self.async_step_migration()

    async def async_step_migration(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 3: detect legacy ebusd MQTT discovery entities and offer to adopt."""
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(self.hass)
        legacy_uids = {v for v in LEGACY_UID_MAP.values() if v}
        legacy_found = [
            e for e in registry.entities.values()
            if e.platform == "mqtt" and e.unique_id in legacy_uids
        ]
        self._legacy_entities_found = len(legacy_found)

        if self._legacy_entities_found == 0:
            # Nothing to migrate, go straight to confirmation
            return await self.async_step_confirm({CONF_MIGRATE: False})

        if user_input is None:
            return self.async_show_form(
                step_id="migration",
                data_schema=vol.Schema({
                    vol.Required(CONF_MIGRATE, default=True): bool,
                }),
                description_placeholders={
                    "count": str(self._legacy_entities_found),
                },
            )

        return await self.async_step_confirm(user_input)

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 4: final confirmation + create entry."""
        if user_input is None:
            return self.async_show_form(
                step_id="confirm",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "topic_prefix": self._topic_prefix,
                    "system_type": SYSTEM_TYPES[self._system_type],
                    "discovered": str(self._discovered_count),
                    "to_migrate": str(self._legacy_entities_found),
                },
            )

        # Use a deterministic unique_id for the config entry to prevent dupes
        await self.async_set_unique_id(f"genia_air_{self._topic_prefix}")
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title="Vaillant Genia Air",
            data={
                CONF_TOPIC_PREFIX: self._topic_prefix,
                CONF_SYSTEM_TYPE: self._system_type,
                CONF_LANGUAGE: self._language,
                CONF_MIGRATE: user_input.get(CONF_MIGRATE, True),
            },
        )
