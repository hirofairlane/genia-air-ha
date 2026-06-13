"""Binary sensor (alert) platform for Vaillant Genia Air."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
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
    binaries = []
    for ed in by_platform("binary_sensor"):
        if ed.unique_id == "heat_pump_active_fault":
            binaries.append(ActiveFaultSensor(coordinator, ed))
        elif ed.unique_id == "heat_pump_low_flow":
            binaries.append(LowFlowSensor(coordinator, ed))
        elif ed.unique_id == "heat_pump_delta_t_anomaly":
            binaries.append(DeltaTAnomalySensor(coordinator, ed))
        elif ed.unique_id == "heat_pump_maintenance_due":
            binaries.append(MaintenanceDueSensor(coordinator, ed))
    async_add(binaries)


class _BaseBinary(GeniaAirEntity, BinarySensorEntity):
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
        self._attr_icon = ed.icon


class ActiveFaultSensor(_BaseBinary):
    """ON when heat-pump State == 11 (error) or currenterror has a non-null code."""

    @property
    def is_on(self) -> bool | None:
        state_msg = self.coordinator.state.get(("hmu", "State"))
        if state_msg and str(state_msg.fields.get("3", "")) == "11":
            return True
        cur_err = self.coordinator.state.get(("hmu", "currenterror"))
        if cur_err:
            for k, v in cur_err.fields.items():
                if v not in (None, 0, "0", ""):
                    return True
        return False


class LowFlowSensor(_BaseBinary):
    """ON when compressor > 20% utilization and flow < 200 L/h."""

    @property
    def is_on(self) -> bool | None:
        flow_msg = self.coordinator.state.get(("hmu", "WaterThroughput"))
        util_msg = self.coordinator.state.get(("hmu", "CurrentCompressorUtil"))
        if not flow_msg or not util_msg:
            return None
        try:
            flow = float(flow_msg.fields.get("0", 0))
            util = float(util_msg.fields.get("0", 0))
            return util > 20 and 0 < flow < 200
        except (TypeError, ValueError):
            return None


class DeltaTAnomalySensor(_BaseBinary):
    """ON when ΔT (supply − return) > 10 K while compressor is working."""

    @property
    def is_on(self) -> bool | None:
        s = self.coordinator.state.get(("hmu", "Status01"))
        util_msg = self.coordinator.state.get(("hmu", "CurrentCompressorUtil"))
        if not s or not util_msg:
            return None
        try:
            supply = float(s.fields.get("0"))
            ret = float(s.fields.get("1"))
            util = float(util_msg.fields.get("0", 0))
            dt = abs(supply - ret)
            return util > 20 and dt > 10
        except (TypeError, ValueError):
            return None


class MaintenanceDueSensor(_BaseBinary):
    """ON when the CTLS2 reports MaintenanceDue=yes."""

    @property
    def is_on(self) -> bool | None:
        val = self._ebusd_value
        return val in ("yes", 1, "1", True)
