# Architecture — genia-air-ha

> Internal design document. Audience: contributors and Claude in future sessions.

## Layering

```
┌────────────────────────────────────────────────────────────────────────┐
│  HARDWARE                                                              │
│  Vaillant Genia Air (HMU 0901 + CTLS2 0509) + VWZIO 76                 │
│                              ↑↓ eBUS                                   │
│  eBUS network adapter (192.168.1.100:9999, enh: protocol)               │
└────────────────────────────────────────────────────────────────────────┘
                              ↑↓ TCP
┌────────────────────────────────────────────────────────────────────────┐
│  HA ADD-ON LAYER (NOT part of this integration)                        │
│  LukasGrebe/ebusd v26.x  →  ebusd daemon decoding messages             │
│                          →  publishes to MQTT under prefix `ebusd/`    │
│                          →  publishes HA discovery under `homeassistant/`│
└────────────────────────────────────────────────────────────────────────┘
                              ↑↓ MQTT
┌────────────────────────────────────────────────────────────────────────┐
│  HA INTEGRATION LAYER (THIS REPO)                                      │
│  custom_components/genia_air/                                          │
│    - subscribes to ebusd/+/+ via HA's mqtt integration                 │
│    - parses the JSON payloads ebusd publishes                          │
│    - builds typed entities (climate / sensor / number / binary_sensor) │
│    - exposes a single Device "Vaillant Genia Air" with sane grouping   │
│    - handles writes by publishing to ebusd/<circuit>/<msg>/set         │
│    - claims pre-existing ebusd_* unique_ids → preserves history        │
└────────────────────────────────────────────────────────────────────────┘
                              ↑↓ HA core
┌────────────────────────────────────────────────────────────────────────┐
│  USER / OTHER INTEGRATIONS                                             │
│  - Dashboards, automations, scripts                                    │
│  - `ha-energy-optimizer` consumes climate + power sensors              │
│  - InfluxDB exporter writes time series under entity_id tag            │
└────────────────────────────────────────────────────────────────────────┘
```

## Why MQTT-based and not direct eBUS reading

We could open a direct TCP connection to the eBUS adapter (192.168.1.100:9999) and reimplement the ebusd protocol in Python. **We don't**, for three reasons:

1. **ebusd is a battle-tested C daemon** with multi-master bus arbitration, CSV-driven message decoding, scan-config, retry logic, log levels, etc. Reimplementing that in Python is months of work for zero added value.
2. **One process owns the bus**. If both ebusd and our integration tried to drive the adapter, we'd corrupt the bus. By relying on ebusd we coexist cleanly with users who already have it set up.
3. **User installation experience**: ebusd is already packaged as an HA add-on with hardware vendor backing (LukasGrebe). We piggyback on a proven installation path.

The integration's value is **not in talking to the bus** — it's in turning the addon's MQTT firehose into a coherent HA Device with proper semantic entities, smart defaults for underfloor heating, and a config flow.

## Naming convention

All `unique_id`s use **stable English snake_case** for portability:

```
sensor.heat_pump_cop_instantaneous       unique_id: heat_pump_cop_instantaneous
sensor.heat_pump_power_input             unique_id: heat_pump_power_input
number.heat_pump_setpoint_cooling        unique_id: heat_pump_setpoint_cooling
climate.genia_air_zone_1                 unique_id: genia_air_zone_1
binary_sensor.heat_pump_low_flow         unique_id: heat_pump_low_flow
```

User-friendly `friendly_name` is localized at register time (Spanish / English) and the user can rename freely in HA UI; their rename persists.

**Why not keep the legacy `ebusd_ctls2_z1ManualTemp_tempv` style?** Because:
- It leaks the implementation (ebusd circuit names, raw eBUS field types)
- It's untranslatable and confusing (`tempv` means nothing)
- It carries the bug-prone Z1/z1 case-sensitivity issue
- It's hostile to other Genia Air users who never had the LukasGrebe addon installed

But we **must not lose any existing user's history** — see [MIGRATION.md](MIGRATION.md) for the claim-by-old-unique_id mechanism that bridges legacy → new naming without data loss.

## Device modelling

One `DeviceInfo` per Genia Air installation, with sub-areas via `via_device`:

```
Vaillant Genia Air
├─ identifiers: {("genia_air", <HMU_serial_or_synthetic>)}
├─ manufacturer: "Vaillant"
├─ model: "Genia Air HMU 0901 HW 5103"
├─ sw_version: "0901"
└─ entities (~30 active in typical install)
```

For installations with multiple heating zones (Z1/Z2/Z3), each active zone gets its own logical sub-device with `via_device` pointing to the main one, **but only if the user has more than one zone configured**. Single-zone installs (the vast majority of underfloor-heating cases) get a flatter layout.

## Climate entity

Single `climate.genia_air_zone_1` for the active zone, mapping to eBUS messages as follows:

```
HVAC mode  →  Z1OpMode (heating) + Z1OpModeCooling (cooling) + OpMode global
Setpoint   →  Z1ManualTemp (if hvac_mode=heat)
              Z1CoolingTemp (if hvac_mode=cool)
Current T  →  Z1RoomTemp
HVAC action → derived from compressor state + Hc1Status
Preset modes → "eco" / "comfort" / "boost_solar" / "valley"
                 ↑ writes Z1ManualTemp + Z1NightTemp + Z1HolidayTemp
                 ↑ designed to integrate with ha-energy-optimizer tiers
```

Range constraints **specifically for underfloor heating** (preventing user from putting parquet at 50°C):
- Heating setpoints: 12–28°C
- Cooling setpoints: 16–26°C
- Max flow temp: 25–40°C (hard cap at 40°C — protects floor)
- Min flow temp in heating: 22–30°C
- Min flow temp in cooling: 14–22°C (anti-condensation depending on RH)

## Multi-platform sketch

| Platform | Purpose | Count (typical install) |
|---|---|---|
| `sensor` | Temperatures, kW, COP, hours, modulation, error codes | ~25 |
| `number` | Setpoints, curve, limits, hysteresis | ~10–15 |
| `binary_sensor` | Fault, low flow, ΔT anomaly, maintenance due | 4 |
| `climate` | Zone thermostat | 1 (or N if multi-zone) |
| `select` | OpMode (off/heat/cool/auto), preset | 1–2 |

## Config flow

```
Step 1: USER_INPUT
   - MQTT broker (auto-detected from HA's mqtt config_entry)
   - ebusd topic prefix (default: "ebusd")
   - System type: ☐ Underfloor heating ☐ Radiators ☐ Mixed (affects default ranges)
   - Localization: ES / EN (affects friendly_name only)

Step 2: DISCOVERY
   - Subscribe ebusd/global/scan, wait for HMU/CTLS2 identification
   - Verify ebusd is actually publishing (timeout 30s with progress)
   - Show user: "Detected Genia Air HMU 0901 / CTLS2 0509 / VWZIO 76 ✓"

Step 3: MIGRATION (only if old ebusd entities detected)
   - Scan entity_registry for unique_ids matching `ebusd_(ctls2|hmu|broadcast)_*`
   - Show count and let user choose:
     ◉ Migrate (recommended): claim old unique_ids → preserve history
     ◯ Skip: create new entities alongside (user can clean up later)
     ◯ Cancel install

Step 4: CONFIRMATION
   - Summary of what will be created/migrated
   - Link to InfluxDB migration script if user wants old entity_ids' Influx history

Step 5: COMMIT
   - Create config_entry
   - Setup all platforms
   - Log warnings for any orphaned entities the user might want to clean
```

## Entity claim mechanism (the migration core)

In `async_setup_entry`, before creating any entity, we run:

```python
async def migrate_legacy_unique_ids(hass, config_entry):
    """Claim entities from the legacy ebusd MQTT discovery.

    Strategy:
      For each NEW entity we're about to create:
        - look up its NEW unique_id in entity_registry → no match (fresh install case)
        - look up its LEGACY unique_id (from LEGACY_UID_MAP) in entity_registry
        - if found: update_entity() with platform=DOMAIN, new_unique_id
        - HA preserves entity_id, friendly_name, area, hidden_by, etc.
        - Recorder history follows entity_id, so it persists automatically
    """
```

`LEGACY_UID_MAP` is a static dictionary in `const.py`:

```python
LEGACY_UID_MAP = {
    # new_unique_id              : legacy_unique_id from LukasGrebe ebusd addon
    "heat_pump_cop_instantaneous": None,  # this was derived in YAML, no MQTT discovery legacy
    "heat_pump_power_input":       "ebusd_hmu_CurrentConsumedPower_0",
    "heat_pump_power_output":      "ebusd_hmu_CurrentYieldPower_0",
    "heat_pump_compressor_modulation": "ebusd_hmu_CurrentCompressorUtil_0",
    "heat_pump_water_throughput":  "ebusd_hmu_WaterThroughput_0",
    "heat_pump_flow_temp_supply":  "ebusd_hmu_Status01_0",      # multi-field idx 0
    "heat_pump_flow_temp_return":  "ebusd_hmu_Status01_1",
    "heat_pump_outside_temp_avg":  "ebusd_ctls2_outsidetempavg_tempv",
    # ... ~40 entries
    "number.heat_pump_setpoint_manual": "ebusd_ctls2_z1ManualTemp_tempv",
    "number.heat_pump_setpoint_cooling": "ebusd_ctls2_z1CoolingTemp_tempv",
    "number.heat_pump_heat_curve": "ebusd_ctls2_Hc1HeatCurve_0",  # if number, else sensor
    # etc.
}
```

When a user runs the config flow on a pristine install, the LEGACY_UID_MAP lookups all miss → fresh entities created. When a user has the LukasGrebe discovery active, they hit → entities are adopted.

## Write path

For each `number.set_value`, `climate.set_temperature`, `select.select_option`:

```python
async def async_set_native_value(self, value):
    # Publish to ebusd's command topic; ebusd writes the bus.
    await mqtt.async_publish(
        self.hass,
        f"{self._topic_prefix}/{self._circuit}/{self._msg_name}/set",
        str(value),
        retain=False,
    )
    # We *don't* optimistically update state; we wait for the next
    # poll-read echo from ebusd to confirm the write took effect.
    # This avoids showing stale values if the bus rejects the write.
```

## Discovery cleanup

The integration **publishes empty payloads to `homeassistant/+/ebusd_*/config`** during setup to remove the legacy retained discovery configs from the broker. After adoption, the entities exist under our integration's `config_entry_id`, not the MQTT integration's.

If the user uninstalls our integration:
- Migration is **non-destructive on the bus side** (we never touch ebusd's config or CSVs)
- On uninstall, we can offer to re-publish the legacy discovery configs to restore the previous state — opt-in

## Dependencies

- `homeassistant` ≥ 2026.5.0
- `paho-mqtt` (already shipped by HA's mqtt integration)
- No external Python deps for the integration itself
- `scripts/migrate_influxdb.py` requires `influxdb-client` (v2) or `influxdb` (v1) — user installs locally when running, not bundled
