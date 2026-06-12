"""Climate platform — zone thermostat for Vaillant Genia Air."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GeniaAirCoordinator
from .entity import GeniaAirEntity

_LOGGER = logging.getLogger(__name__)

# Mapping: HMU State field "3" raw value → HVACAction
_STATE_TO_ACTION = {
    "0":   HVACAction.IDLE,
    "1":   HVACAction.IDLE,
    "9":   HVACAction.HEATING,
    "17":  HVACAction.COOLING,
    "129": HVACAction.HEATING,
    "11":  HVACAction.OFF,
}

PRESET_MANUAL    = "manual"
PRESET_DAY       = "day"
PRESET_NIGHT     = "night"
PRESET_HOLIDAY   = "holiday"
PRESET_QUICK_VETO = "quick_veto"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add: AddEntitiesCallback
) -> None:
    coordinator: GeniaAirCoordinator = hass.data[DOMAIN][entry.entry_id]
    # v0.1 ships only zone 1 (single-zone Genia Air is the common case);
    # multi-zone is on the v0.2 roadmap.
    async_add([GeniaAirZoneClimate(coordinator, zone=1)])


class GeniaAirZoneClimate(GeniaAirEntity, ClimateEntity):
    """Native thermostat for a Genia Air heating zone."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_preset_modes = [PRESET_MANUAL, PRESET_DAY, PRESET_NIGHT, PRESET_HOLIDAY, PRESET_QUICK_VETO]
    _attr_target_temperature_step = 0.5

    def __init__(self, coordinator: GeniaAirCoordinator, zone: int) -> None:
        super().__init__(
            coordinator,
            unique_id=f"genia_air_zone_{zone}_thermostat",
            translation_key=f"zone_{zone}_thermostat",
            circuit="ctls2",
            msg=f"z{zone}RoomTemp",
            field_key="tempv",
        )
        self._zone = zone

    # ------------------------------------------------------------------
    # Read-only properties
    # ------------------------------------------------------------------

    @property
    def current_temperature(self) -> float | None:
        msg = self.coordinator.state.get(("ctls2", f"z{self._zone}RoomTemp"))
        if msg is None:
            return None
        try:
            return float(msg.fields.get("tempv"))
        except (TypeError, ValueError):
            return None

    @property
    def target_temperature(self) -> float | None:
        if self.hvac_mode == HVACMode.COOL:
            msg = self.coordinator.state.get(("ctls2", f"z{self._zone}CoolingTemp"))
        else:
            msg = self.coordinator.state.get(("ctls2", f"z{self._zone}ManualTemp"))
        if msg is None:
            return None
        try:
            return float(msg.fields.get("tempv"))
        except (TypeError, ValueError):
            return None

    @property
    def min_temp(self) -> float:
        return 16.0 if self.hvac_mode == HVACMode.COOL else 12.0

    @property
    def max_temp(self) -> float:
        return 26.0 if self.hvac_mode == HVACMode.COOL else 28.0

    @property
    def hvac_mode(self) -> HVACMode:
        # Global kill switch wins
        sys_off = self.coordinator.state.get(("ctls2", "GlobalSystemOff"))
        if sys_off and str(sys_off.fields.get("yesno", "")).lower() == "yes":
            return HVACMode.OFF

        heat = self.coordinator.state.get(("ctls2", f"z{self._zone}OpMode"))
        cool = self.coordinator.state.get(("ctls2", f"z{self._zone}OpModeCooling"))
        h = str(heat.fields.get("opmode", "")).lower() if heat else "off"
        c = str(cool.fields.get("opmode", "")).lower() if cool else "off"

        h_active = h in ("auto", "day", "night")
        c_active = c in ("auto", "day", "night")

        if h_active and c_active:
            return HVACMode.AUTO          # heat-or-cool by season
        if c_active:
            return HVACMode.COOL
        if h_active:
            return HVACMode.HEAT
        return HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        state_msg = self.coordinator.state.get(("hmu", "State"))
        if state_msg is None:
            return None
        raw = str(state_msg.fields.get("3", ""))
        action = _STATE_TO_ACTION.get(raw)
        # If compressor idle but the integration's hvac_mode is OFF, report OFF
        if action is HVACAction.IDLE and self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        return action

    @property
    def preset_mode(self) -> str | None:
        sf = self.coordinator.state.get(("ctls2", f"z{self._zone}SfMode"))
        if sf:
            sfm = str(sf.fields.get("sfmode", "")).lower()
            if sfm == "veto":
                return PRESET_QUICK_VETO
            if sfm in ("onedayaway",):
                return PRESET_HOLIDAY
        heat = self.coordinator.state.get(("ctls2", f"z{self._zone}OpMode"))
        if heat:
            hm = str(heat.fields.get("opmode", "")).lower()
            if hm == "day":
                return PRESET_DAY
            if hm == "night":
                return PRESET_NIGHT
        return PRESET_MANUAL

    @property
    def available(self) -> bool:
        # Need at least the room-temp reading to be useful.
        return ("ctls2", f"z{self._zone}RoomTemp") in self.coordinator.state

    # ------------------------------------------------------------------
    # Write actions
    # ------------------------------------------------------------------

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        msg_name = (
            f"z{self._zone}CoolingTemp"
            if self.hvac_mode == HVACMode.COOL
            else f"z{self._zone}ManualTemp"
        )
        _LOGGER.info("Climate set_temperature → %s = %s °C", msg_name, temp)
        await self.coordinator.async_publish_write("ctls2", msg_name, f"{temp:g}")

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        z = self._zone
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_publish_write("ctls2", f"z{z}OpMode",        "off")
            await self.coordinator.async_publish_write("ctls2", f"z{z}OpModeCooling", "off")
        elif hvac_mode == HVACMode.HEAT:
            await self.coordinator.async_publish_write("ctls2", f"z{z}OpMode",        "auto")
            await self.coordinator.async_publish_write("ctls2", f"z{z}OpModeCooling", "off")
        elif hvac_mode == HVACMode.COOL:
            await self.coordinator.async_publish_write("ctls2", f"z{z}OpMode",        "off")
            await self.coordinator.async_publish_write("ctls2", f"z{z}OpModeCooling", "auto")
        elif hvac_mode == HVACMode.AUTO:
            await self.coordinator.async_publish_write("ctls2", f"z{z}OpMode",        "auto")
            await self.coordinator.async_publish_write("ctls2", f"z{z}OpModeCooling", "auto")

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        z = self._zone
        if preset_mode == PRESET_QUICK_VETO:
            await self.coordinator.async_publish_write("ctls2", f"z{z}SfMode", "veto")
        elif preset_mode == PRESET_HOLIDAY:
            await self.coordinator.async_publish_write("ctls2", f"z{z}SfMode", "onedayaway")
        elif preset_mode == PRESET_DAY:
            await self.coordinator.async_publish_write("ctls2", f"z{z}OpMode", "day")
        elif preset_mode == PRESET_NIGHT:
            await self.coordinator.async_publish_write("ctls2", f"z{z}OpMode", "night")
        elif preset_mode == PRESET_MANUAL:
            # Return to auto/baseline; clear any special function
            await self.coordinator.async_publish_write("ctls2", f"z{z}SfMode", "auto")
        else:
            _LOGGER.warning("Unknown preset: %s", preset_mode)
