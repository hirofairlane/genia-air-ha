"""Sensor platform for Vaillant Genia Air."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
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
    sensors = []
    for ed in by_platform("sensor"):
        if ed.computed:
            sensors.append(_make_computed(coordinator, ed))
        else:
            sensors.append(GeniaAirSensor(coordinator, ed))
    async_add(sensors)


def _make_computed(coordinator: GeniaAirCoordinator, ed: EntityDef) -> SensorEntity:
    """Factory for computed sensors (COP, ΔT, compressor_state)."""
    if ed.unique_id == "heat_pump_heating_delta_t":
        return DeltaTSensor(coordinator, ed)
    if ed.unique_id == "heat_pump_cop_instantaneous":
        return CopSensor(coordinator, ed)
    if ed.unique_id == "heat_pump_compressor_state":
        return CompressorStateSensor(coordinator, ed)
    raise ValueError(f"Unknown computed sensor {ed.unique_id}")


class GeniaAirSensor(GeniaAirEntity, SensorEntity):
    """Generic MQTT-backed sensor."""

    def __init__(self, coordinator: GeniaAirCoordinator, ed: EntityDef) -> None:
        super().__init__(
            coordinator,
            unique_id=ed.unique_id,
            translation_key=ed.translation_key,
            circuit=ed.circuit,
            msg=ed.msg,
            field_key=ed.field_key,
        )
        self._attr_icon = ed.icon
        self._attr_device_class = ed.device_class
        self._attr_state_class = ed.state_class
        self._attr_native_unit_of_measurement = ed.unit
        if ed.entity_category == "diagnostic":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        elif ed.entity_category == "config":
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def native_value(self) -> Any:
        return self._ebusd_value


# ---------------------------------------------------------------------------
# Computed sensors
# ---------------------------------------------------------------------------

class DeltaTSensor(GeniaAirSensor):
    """ΔT = supply − return temperature (from Status01 fields 0 and 1)."""

    @property
    def native_value(self) -> float | None:
        s = self.coordinator.state.get(("hmu", "Status01"))
        if s is None:
            return None
        try:
            supply = float(s.fields.get("0"))
            ret = float(s.fields.get("1"))
            return round(supply - ret, 2)
        except (TypeError, ValueError):
            return None

    @property
    def available(self) -> bool:
        return ("hmu", "Status01") in self.coordinator.state


class CopSensor(GeniaAirSensor):
    """COP = (yield + consumed) / consumed when consumed > 0."""

    @property
    def native_value(self) -> float | None:
        y_msg = self.coordinator.state.get(("hmu", "CurrentYieldPower"))
        c_msg = self.coordinator.state.get(("hmu", "CurrentConsumedPower"))
        if y_msg is None or c_msg is None:
            return None
        try:
            y = float(y_msg.fields.get("0"))
            c = float(c_msg.fields.get("0"))
            if c < 0.05:
                return None
            return round((y + c) / c, 2)
        except (TypeError, ValueError):
            return None

    @property
    def available(self) -> bool:
        if not (self.coordinator.state.get(("hmu", "CurrentConsumedPower"))):
            return False
        try:
            c = float(self.coordinator.state[("hmu", "CurrentConsumedPower")].fields["0"])
            return c >= 0.05
        except (TypeError, ValueError, KeyError):
            return False


class CompressorStateSensor(GeniaAirSensor):
    """Human-readable compressor state from State + CurrentCompressorUtil."""

    _STATE_MAP = {
        "0": "standby", "1": "ready",
        "9": "heating", "17": "cooling",
        "129": "heating_dhw", "11": "error",
    }

    @property
    def native_value(self) -> str | None:
        s_msg = self.coordinator.state.get(("hmu", "State"))
        if s_msg is None:
            return None
        state_raw = str(s_msg.fields.get("3", "?"))
        label = self._STATE_MAP.get(state_raw, f"state_{state_raw}")

        # Append modulation if heating/cooling
        if label in ("heating", "cooling"):
            util_msg = self.coordinator.state.get(("hmu", "CurrentCompressorUtil"))
            if util_msg:
                try:
                    util = float(util_msg.fields.get("0", 0))
                    return f"{label}_{int(util)}"
                except (TypeError, ValueError):
                    pass
        return label
