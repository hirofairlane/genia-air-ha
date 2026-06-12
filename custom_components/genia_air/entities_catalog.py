"""Declarative catalog of all entities the integration exposes.

Each entry maps a stable unique_id to:
  - the platform (sensor/binary_sensor/number)
  - which ebusd (circuit, msg) provides its value
  - which field of the JSON payload to read
  - device_class, unit, ranges
  - friendly_name translation key

Keeping this in one place makes it trivial to add new entities or change a
mapping without touching multiple platform files.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.number import NumberDeviceClass, NumberMode
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

Platform = Literal["sensor", "binary_sensor", "number"]


@dataclass(frozen=True)
class EntityDef:
    """A single entity declaration."""

    platform: Platform
    unique_id: str             # stable; matches keys in LEGACY_UID_MAP
    translation_key: str       # for strings.json / translations/
    circuit: str               # ebusd circuit (hmu/ctls2/broadcast)
    msg: str                   # ebusd message name (case as ebusd publishes it)
    field_key: str | None = None  # JSON key inside the payload; None → "value"

    # Visual / semantic
    icon: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    unit: str | None = None
    entity_category: str | None = None  # None (primary), "diagnostic", "config"

    # Number-specific
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None
    mode: str | None = None  # "slider" / "box" / "auto"

    # Derived/computed sensors (no MQTT subscription, computed from coordinator state)
    computed: bool = False


# ===========================================================================
# CATALOG
# ===========================================================================

ENTITIES: list[EntityDef] = [
    # -----------------------------------------------------------------------
    # SENSORS — telemetry
    # -----------------------------------------------------------------------
    EntityDef("sensor", "heat_pump_power_input", "power_input",
              "hmu", "CurrentConsumedPower", field_key="0",
              device_class=SensorDeviceClass.POWER,
              state_class=SensorStateClass.MEASUREMENT, unit="kW",
              icon="mdi:lightning-bolt"),
    EntityDef("sensor", "heat_pump_power_output", "power_output",
              "hmu", "CurrentYieldPower", field_key="0",
              device_class=SensorDeviceClass.POWER,
              state_class=SensorStateClass.MEASUREMENT, unit="kW",
              icon="mdi:fire"),
    EntityDef("sensor", "heat_pump_compressor_modulation", "compressor_modulation",
              "hmu", "CurrentCompressorUtil", field_key="0",
              state_class=SensorStateClass.MEASUREMENT, unit="%",
              icon="mdi:speedometer"),
    EntityDef("sensor", "heat_pump_water_throughput", "water_throughput",
              "hmu", "WaterThroughput", field_key="0",
              state_class=SensorStateClass.MEASUREMENT, unit="L/h",
              icon="mdi:water"),
    EntityDef("sensor", "heat_pump_flow_temp_supply", "flow_temp_supply",
              "hmu", "Status01", field_key="0",
              device_class=SensorDeviceClass.TEMPERATURE,
              state_class=SensorStateClass.MEASUREMENT, unit="°C",
              icon="mdi:thermometer-chevron-up"),
    EntityDef("sensor", "heat_pump_flow_temp_return", "flow_temp_return",
              "hmu", "Status01", field_key="1",
              device_class=SensorDeviceClass.TEMPERATURE,
              state_class=SensorStateClass.MEASUREMENT, unit="°C",
              icon="mdi:thermometer-chevron-down"),
    EntityDef("sensor", "heat_pump_outside_temp_avg", "outside_temp_avg",
              "ctls2", "OutsideTempAvg", field_key="tempv",
              device_class=SensorDeviceClass.TEMPERATURE,
              state_class=SensorStateClass.MEASUREMENT, unit="°C",
              icon="mdi:weather-cloudy"),
    EntityDef("sensor", "heat_pump_zone_room_temp", "zone_room_temp",
              "ctls2", "z1RoomTemp", field_key="tempv",
              device_class=SensorDeviceClass.TEMPERATURE,
              state_class=SensorStateClass.MEASUREMENT, unit="°C",
              icon="mdi:home-thermometer"),
    EntityDef("sensor", "heat_pump_zone_setpoint_actual", "zone_setpoint_actual",
              "ctls2", "z1ActualRoomTempDesired", field_key="tempv",
              device_class=SensorDeviceClass.TEMPERATURE,
              state_class=SensorStateClass.MEASUREMENT, unit="°C",
              icon="mdi:target"),
    EntityDef("sensor", "heat_pump_yield_total", "yield_total",
              "ctls2", "YieldTotal", field_key="energy4",
              device_class=SensorDeviceClass.ENERGY,
              state_class=SensorStateClass.TOTAL_INCREASING, unit="kWh",
              icon="mdi:chart-line"),
    EntityDef("sensor", "heat_pump_hours_total", "hours_total",
              "hmu", "Hours", field_key="0",
              state_class=SensorStateClass.TOTAL_INCREASING, unit="h",
              entity_category="diagnostic", icon="mdi:counter"),
    EntityDef("sensor", "heat_pump_hours_heating", "hours_heating",
              "hmu", "HoursHc", field_key="0",
              state_class=SensorStateClass.TOTAL_INCREASING, unit="h",
              entity_category="diagnostic", icon="mdi:radiator"),
    EntityDef("sensor", "heat_pump_hours_cooling", "hours_cooling",
              "hmu", "HoursCool", field_key="0",
              state_class=SensorStateClass.TOTAL_INCREASING, unit="h",
              entity_category="diagnostic", icon="mdi:snowflake"),
    EntityDef("sensor", "heat_pump_error_history_last", "error_history_last",
              "hmu", "errorhistory", field_key="error",
              entity_category="diagnostic", icon="mdi:alert-circle"),

    # -----------------------------------------------------------------------
    # COMPUTED SENSORS — derived from other coordinator state
    # -----------------------------------------------------------------------
    EntityDef("sensor", "heat_pump_heating_delta_t", "heating_delta_t",
              "hmu", "Status01",  # depends on Status01 fields
              device_class=SensorDeviceClass.TEMPERATURE,
              state_class=SensorStateClass.MEASUREMENT, unit="K",
              icon="mdi:delta", computed=True),
    EntityDef("sensor", "heat_pump_cop_instantaneous", "cop_instantaneous",
              "hmu", "CurrentYieldPower",  # depends on yield + consumed
              state_class=SensorStateClass.MEASUREMENT,
              icon="mdi:gauge", computed=True),
    EntityDef("sensor", "heat_pump_compressor_state", "compressor_state",
              "hmu", "State",
              icon="mdi:state-machine", computed=True),

    # -----------------------------------------------------------------------
    # BINARY SENSORS — alerts
    # -----------------------------------------------------------------------
    EntityDef("binary_sensor", "heat_pump_active_fault", "active_fault",
              "hmu", "State",
              device_class=BinarySensorDeviceClass.PROBLEM,
              icon="mdi:alert-octagon", computed=True),
    EntityDef("binary_sensor", "heat_pump_low_flow", "low_flow",
              "hmu", "WaterThroughput",
              device_class=BinarySensorDeviceClass.PROBLEM,
              icon="mdi:water-alert", computed=True),
    EntityDef("binary_sensor", "heat_pump_delta_t_anomaly", "delta_t_anomaly",
              "hmu", "Status01",
              device_class=BinarySensorDeviceClass.PROBLEM,
              icon="mdi:alert", computed=True),
    EntityDef("binary_sensor", "heat_pump_maintenance_due", "maintenance_due",
              "ctls2", "MaintenanceDue", field_key="yesno",
              device_class=BinarySensorDeviceClass.PROBLEM,
              icon="mdi:wrench-clock"),

    # -----------------------------------------------------------------------
    # NUMBERS — writable setpoints, safe ranges for underfloor heating
    # -----------------------------------------------------------------------
    EntityDef("number", "heat_pump_setpoint_manual", "setpoint_manual",
              "ctls2", "z1ManualTemp", field_key="tempv",
              device_class=NumberDeviceClass.TEMPERATURE, unit="°C",
              min_value=12, max_value=28, step=0.5, mode="slider"),
    EntityDef("number", "heat_pump_setpoint_day", "setpoint_day",
              "ctls2", "z1DayTemp", field_key="tempv",
              device_class=NumberDeviceClass.TEMPERATURE, unit="°C",
              min_value=14, max_value=26, step=0.5, mode="slider"),
    EntityDef("number", "heat_pump_setpoint_night", "setpoint_night",
              "ctls2", "z1NightTemp", field_key="tempv",
              device_class=NumberDeviceClass.TEMPERATURE, unit="°C",
              min_value=16, max_value=26, step=0.5, mode="slider"),
    EntityDef("number", "heat_pump_setpoint_holiday", "setpoint_holiday",
              "ctls2", "z1HolidayTemp", field_key="tempv",
              device_class=NumberDeviceClass.TEMPERATURE, unit="°C",
              min_value=5, max_value=22, step=0.5, mode="slider"),
    EntityDef("number", "heat_pump_setpoint_cooling", "setpoint_cooling",
              "ctls2", "z1CoolingTemp", field_key="tempv",
              device_class=NumberDeviceClass.TEMPERATURE, unit="°C",
              min_value=16, max_value=26, step=0.5, mode="slider"),
    EntityDef("number", "heat_pump_setpoint_quick_veto", "setpoint_quick_veto",
              "ctls2", "z1QuickVetoTemp", field_key="tempv",
              device_class=NumberDeviceClass.TEMPERATURE, unit="°C",
              min_value=12, max_value=28, step=0.5, mode="slider"),
    EntityDef("number", "heat_pump_max_flow_temp", "max_flow_temp",
              "ctls2", "Hc1MaxFlowTempDesired", field_key="tempv",
              device_class=NumberDeviceClass.TEMPERATURE, unit="°C",
              min_value=25, max_value=40, step=0.5, mode="slider",
              entity_category="config"),
    EntityDef("number", "heat_pump_min_flow_temp", "min_flow_temp",
              "ctls2", "Hc1MinFlowTempDesired", field_key="tempv",
              device_class=NumberDeviceClass.TEMPERATURE, unit="°C",
              min_value=14, max_value=30, step=0.5, mode="slider",
              entity_category="config"),
    EntityDef("number", "heat_pump_summer_temp_limit", "summer_temp_limit",
              "ctls2", "Hc1SummerTempLimit", field_key="tempv",
              device_class=NumberDeviceClass.TEMPERATURE, unit="°C",
              min_value=12, max_value=28, step=0.5, mode="slider",
              entity_category="config"),
    EntityDef("number", "heat_pump_continuous_heating", "continuous_heating",
              "ctls2", "ContinuosHeating", field_key="tempv",
              device_class=NumberDeviceClass.TEMPERATURE, unit="°C",
              min_value=-26, max_value=15, step=0.5, mode="box",
              entity_category="config"),
]


def by_platform(platform: Platform) -> list[EntityDef]:
    """Return all entities of a given platform."""
    return [e for e in ENTITIES if e.platform == platform]
