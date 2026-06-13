"""Vaillant Genia Air integration entry point."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import GeniaAirCoordinator
from .migration import (
    claim_legacy_entities,
    generate_influxdb_mapping_yaml,
    rollback_migration,
    take_registry_snapshot,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.CLIMATE,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Vaillant Genia Air from a config entry."""
    _LOGGER.info("Setting up Genia Air entry %s", entry.entry_id)

    hass.data.setdefault(DOMAIN, {})

    topic_prefix = entry.data.get("topic_prefix", "ebusd")
    coordinator = GeniaAirCoordinator(hass, entry, topic_prefix)
    await coordinator.async_start()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # ---- Migration of legacy ebusd MQTT discovery entities ----
    if entry.data.get("migrate_legacy", False):
        try:
            await take_registry_snapshot(hass)
            counts = await claim_legacy_entities(hass, entry, dry_run=False)
            _LOGGER.info(
                "Migration done: adopted=%d, not_found=%d, no_legacy=%d, collisions=%d",
                counts["adopted"], counts["skipped_not_found"],
                counts["skipped_no_legacy"], counts["skipped_collision"],
            )
            mapping_path = await generate_influxdb_mapping_yaml(hass)
            _LOGGER.info(
                "InfluxDB migration mapping ready at %s — "
                "run scripts/migrate_influxdb.py to preserve long-term history.",
                mapping_path,
            )
        except Exception as err:
            _LOGGER.exception("Migration failed; entities will be created fresh: %s", err)

    # ---- Register the rollback service (idempotent) ----
    if not hass.services.has_service(DOMAIN, "rollback_migration"):
        async def _rollback(call):
            count = await rollback_migration(hass, entry)
            _LOGGER.info("Rollback complete: %d entities restored", count)
        hass.services.async_register(DOMAIN, "rollback_migration", _rollback)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: GeniaAirCoordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
        if coordinator:
            await coordinator.async_stop()
    return unload_ok
