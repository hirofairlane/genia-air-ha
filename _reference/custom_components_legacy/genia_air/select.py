"""Select platform — operation modes and special function modes."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GeniaAirCoordinator
from .entity import GeniaAirEntity

_LOGGER = logging.getLogger(__name__)

# Vaillant z*OpMode UCH values
OPMODE_OPTIONS = ["off", "auto", "day", "night"]

# Vaillant z*SfMode UCH values
SFMODE_OPTIONS = [
    "auto", "ventilation", "party", "veto",
    "onedayaway", "onedayathome", "load",
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add: AddEntitiesCallback
) -> None:
    coordinator: GeniaAirCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add([
        GeniaAirOpModeSelect(coordinator, zone=1, side="heating"),
        GeniaAirOpModeSelect(coordinator, zone=1, side="cooling"),
        GeniaAirSfModeSelect(coordinator, zone=1),
    ])


class GeniaAirOpModeSelect(GeniaAirEntity, SelectEntity):
    """Operation-mode select for either heating or cooling side of a zone."""

    _attr_options = OPMODE_OPTIONS

    def __init__(self, coordinator: GeniaAirCoordinator, zone: int, side: str) -> None:
        suffix = "OpModeCooling" if side == "cooling" else "OpMode"
        super().__init__(
            coordinator,
            unique_id=f"genia_air_zone_{zone}_opmode_{side}",
            translation_key=f"zone_opmode_{side}",
            circuit="ctls2",
            msg=f"z{zone}{suffix}",
            field_key="opmode",
        )
        self._side = side
        self._zone = zone

    @property
    def current_option(self) -> str | None:
        v = self._ebusd_value
        if v in (None, "unavailable", "unknown"):
            return None
        s = str(v).lower()
        return s if s in OPMODE_OPTIONS else None

    async def async_select_option(self, option: str) -> None:
        _LOGGER.info("Select %s/%s = %s", self._circuit, self._msg, option)
        await self.coordinator.async_publish_write(self._circuit, self._msg, option)


class GeniaAirSfModeSelect(GeniaAirEntity, SelectEntity):
    """Special function mode (party, veto, vacation, etc.)."""

    _attr_options = SFMODE_OPTIONS
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GeniaAirCoordinator, zone: int) -> None:
        super().__init__(
            coordinator,
            unique_id=f"genia_air_zone_{zone}_sfmode",
            translation_key="zone_sfmode",
            circuit="ctls2",
            msg=f"z{zone}SfMode",
            field_key="sfmode",
        )
        self._zone = zone

    @property
    def current_option(self) -> str | None:
        v = self._ebusd_value
        if v in (None, "unavailable", "unknown"):
            return None
        s = str(v).lower()
        return s if s in SFMODE_OPTIONS else None

    async def async_select_option(self, option: str) -> None:
        _LOGGER.info("Select %s/%s = %s", self._circuit, self._msg, option)
        await self.coordinator.async_publish_write(self._circuit, self._msg, option)
