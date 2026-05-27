"""Constants — entity naming and legacy unique_id mapping for migration."""
from __future__ import annotations

DOMAIN = "genia_air"

# ---------------------------------------------------------------------------
# Legacy unique_id → new unique_id mapping.
# When the integration sets up, for each entity it owns, it checks whether the
# corresponding LEGACY unique_id (left side) is already present in the entity
# registry under the MQTT integration. If so, it claims it.
# This preserves entity_id, custom friendly_name and Recorder history.
# (InfluxDB history requires the separate scripts/migrate_influxdb.py.)
# ---------------------------------------------------------------------------

LEGACY_UID_MAP: dict[str, str | None] = {
    # ---- Power / energy ----
    "heat_pump_power_input":           "ebusd_hmu_CurrentConsumedPower_0",
    "heat_pump_power_output":          "ebusd_hmu_CurrentYieldPower_0",
    "heat_pump_compressor_modulation": "ebusd_hmu_CurrentCompressorUtil_0",
    "heat_pump_water_throughput":      "ebusd_hmu_WaterThroughput_0",
    "heat_pump_yield_total":           "ebusd_ctls2_YieldTotal_energy4",
    "heat_pump_hours_total":           "ebusd_hmu_Hours_0",
    "heat_pump_hours_heating":         "ebusd_hmu_HoursHc_0",
    "heat_pump_hours_cooling":         "ebusd_hmu_HoursCool_0",

    # ---- Temperatures (raw bus readings) ----
    "heat_pump_flow_temp_supply":      "ebusd_hmu_Status01_0",  # multi-field idx 0
    "heat_pump_flow_temp_return":      "ebusd_hmu_Status01_1",
    "heat_pump_outside_temp_avg":      "ebusd_ctls2_outsidetempavg_tempv",
    "heat_pump_outside_temp_displayed":"ebusd_ctls2_displayedoutsidetemp_tempv",
    "heat_pump_outside_temp_broadcast":"ebusd_broadcast_outsidetemp_temp2",
    "heat_pump_flow_temp_actual":      "ebusd_ctls2_hc1flowtemp_tempv",
    "heat_pump_flow_temp_desired":     "ebusd_ctls2_hc1actualflowtempdesired_tempv",

    # ---- State ----
    "heat_pump_state_raw":             "ebusd_hmu_State_3",  # multi-field idx 3 = state code
    "heat_pump_state_onoff":           "ebusd_hmu_State_2",  # idx 2 = onoff bitfield
    "heat_pump_pump_state":            "ebusd_hmu_Status01_5",  # idx 5 = pumpstate
    "heat_pump_setmode_hcmode":        "ebusd_hmu_SetMode_hcmode",

    # ---- Zone (Z1) ----
    "heat_pump_zone_room_temp":        "ebusd_ctls2_z1roomtemp_tempv",
    "heat_pump_zone_setpoint_actual":  "ebusd_ctls2_z1actualroomtempdesired_tempv",
    "heat_pump_zone_opmode":           "ebusd_ctls2_z1opmode_opmode",
    "heat_pump_zone_opmode_cooling":   "ebusd_ctls2_z1opmodecooling_opmode",
    "heat_pump_zone_valve_status":     "ebusd_ctls2_z1valvestatus",

    # ---- Errors ----
    "heat_pump_error_current":         "ebusd_hmu_currenterror_error",
    "heat_pump_error_history_last":    "ebusd_hmu_errorhistory_error",
    "heat_pump_error_history_date":    "ebusd_hmu_errorhistory_date",
    "heat_pump_maintenance_due":       "ebusd_ctls2_maintenancedue_yesno",

    # ---- Writable (numbers) ----
    "heat_pump_setpoint_manual":       "ebusd_ctls2_z1ManualTemp_tempv",
    "heat_pump_setpoint_day":          "ebusd_ctls2_z1DayTemp_tempv",
    "heat_pump_setpoint_night":        "ebusd_ctls2_z1NightTemp_tempv",
    "heat_pump_setpoint_holiday":      "ebusd_ctls2_z1HolidayTemp_tempv",
    "heat_pump_setpoint_cooling":      "ebusd_ctls2_z1CoolingTemp_tempv",
    "heat_pump_setpoint_quick_veto":   "ebusd_ctls2_z1QuickVetoTemp_tempv",
    "heat_pump_max_flow_temp":         "ebusd_ctls2_Hc1MaxFlowTempDesired_tempv",
    "heat_pump_min_flow_temp":         "ebusd_ctls2_Hc1MinFlowTempDesired_tempv",
    "heat_pump_summer_temp_limit":     "ebusd_ctls2_Hc1SummerTempLimit_tempv",
    "heat_pump_continuous_heating":    "ebusd_ctls2_ContinuosHeating_tempv",

    # ---- Derived (no legacy mapping — these are computed by the integration,
    # not by ebusd discovery; on fresh install they're created from scratch)
    "heat_pump_cop_instantaneous":     None,
    "heat_pump_compressor_state":      None,
    "heat_pump_heating_delta_t":       None,
}

# Reverse map for the migrate_influxdb.py mapping file generation
def make_influx_mapping(domain_lookup: dict[str, str]) -> dict[str, str]:
    """Given new_unique_id → new entity_id, produce old_entity_id → new entity_id
    for the InfluxDB migration script's input.

    Caller passes a dict {new_unique_id: new_entity_id_after_install}, this
    returns a dict suitable for dumping to mapping.yaml.
    """
    out = {}
    # NOTE: this needs entity_registry access at runtime to compute old entity_ids
    # from legacy unique_ids — to be implemented in migration.py. This stub
    # documents the shape.
    return out
