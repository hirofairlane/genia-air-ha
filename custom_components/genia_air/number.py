"""Number platform — writable setpoints with safe ranges for underfloor heating."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GeniaAirCoordinator
from .entities_catalog import EntityDef, by_platform
from .entity import GeniaAirEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add: AddEntitiesCallback
) -> None:
    coordinator: GeniaAirCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add(GeniaAirNumber(coordinator, ed) for ed in by_platform("number"))


class GeniaAirNumber(GeniaAirEntity, NumberEntity):
    """Writable number backed by an ebusd MQTT topic."""

    def __init__(self, coordinator: GeniaAirCoordinator, ed: EntityDef) -> None:
        super().__init__(
            coordinator,
            unique_id=ed.unique_id,
            translation_key=ed.translation_key,
            circuit=ed.circuit,
            msg=ed.msg,
            field_key=ed.field_key,
        )
        self._attr_device_class = ed.device_class
        self._attr_native_unit_of_measurement = ed.unit
        self._attr_native_min_value = ed.min_value
        self._attr_native_max_value = ed.max_value
        self._attr_native_step = ed.step
        self._attr_mode = NumberMode(ed.mode) if ed.mode else NumberMode.AUTO
        if ed.entity_category == "config":
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def native_value(self) -> float | None:
        val = self._ebusd_value
        if val in (None, "unknown", "unavailable", "?"):
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Publish a write to ebusd; let the next poll-read echo update state."""
        # ebusd expects the integer or decimal as plain text payload
        payload = f"{value:g}"
        _LOGGER.info("Write %s/%s = %s", self._circuit, self._msg, payload)
        await self.coordinator.async_publish_write(self._circuit, self._msg, payload)
        # Optionally trigger a re-read after a short delay so the UI refreshes
        # fast. ebusd echoes the write naturally on its next poll cycle anyway.
