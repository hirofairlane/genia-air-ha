# 🔥 Vaillant Genia Air for Home Assistant

> **Native Home Assistant integration for the Vaillant Genia Air family of heat pumps. Plug-and-play install through HACS, automatic eBUS device discovery, and zero-loss migration of your existing history from the raw ebusd MQTT discovery.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![hacs_custom](https://img.shields.io/badge/HACS-custom-orange.svg)](https://hacs.xyz/)
[![Status: alpha](https://img.shields.io/badge/status-alpha-red.svg)](#status)

---

## What you get

Installing **Genia Air** gives you, in your HA Devices & Services:

```
🌡️ Vaillant Genia Air [HMU 0901/5103 + CTLS2 0509/1304]
   ├─ climate.genia_air_zone_1                    "Thermostat zone 1"
   ├─ sensor.heat_pump_cop_instantaneous          "COP instantáneo"
   ├─ sensor.heat_pump_compressor_state           "Standby/Heating 65%/Error"
   ├─ sensor.heat_pump_heating_delta_t            "ΔT ida-vuelta"
   ├─ sensor.heat_pump_power_input / output       kW eléctrica / térmica
   ├─ sensor.heat_pump_flow_temp_supply / return
   ├─ sensor.heat_pump_water_throughput           caudal l/h
   ├─ sensor.heat_pump_compressor_modulation      % modulación
   ├─ sensor.heat_pump_outside_temp_avg
   ├─ sensor.heat_pump_hours_total / heating / cooling
   ├─ number.heat_pump_setpoint_manual            setpoint manual zona
   ├─ number.heat_pump_setpoint_night             setpoint reducción nocturna
   ├─ number.heat_pump_setpoint_cooling           setpoint refrigeración
   ├─ number.heat_pump_heat_curve                 curva 0.1–4.0
   ├─ number.heat_pump_max_flow_temp              límite máximo impulsión
   ├─ binary_sensor.heat_pump_active_fault
   ├─ binary_sensor.heat_pump_low_flow
   ├─ binary_sensor.heat_pump_delta_t_anomaly
   └─ binary_sensor.heat_pump_maintenance_due
```

**Why a native integration instead of just a YAML pack?** Because the right experience is "Settings → Add Integration → Vaillant Genia Air → finished" with auto-discovery, proper devices, native climate UI, no YAML editing, no MQTT topic memorization, no case-sensitivity gotchas, no `restored:true` zombies after a reload.

## Status

**Alpha.** v0.1 in progress. Designed for the **HMU 0901 / CTLS2 0509** firmware combo (validated). Other Genia Air firmware revisions might need eBUS CSV adjustments — please open an issue with your scan output.

## Requirements

- Home Assistant OS / Supervised, **2026.5.0** or newer
- The **eBUS daemon add-on** ([LukasGrebe/ha-addons](https://github.com/LukasGrebe/ha-addons)) installed and running
- **MQTT integration** (Mosquitto broker add-on or external) — the eBUS add-on must be publishing to MQTT
- An eBUS hardware adapter — [Lukas Grebe sells solid ones](http://lukas.grebe.me/ref=github)

## Installation

1. **HACS → Integrations → ⋮ → Custom repositories**
   ```
   https://github.com/hirofairlane/genia-air-ha
   ```
   Category: Integration
2. Search "Vaillant Genia Air" → Install
3. Restart Home Assistant
4. **Settings → Devices & Services → Add Integration → Vaillant Genia Air**
5. The config flow will:
   - Detect the eBUS add-on automatically
   - Identify your HMU/CTLS2 firmware via the MQTT scan topics
   - **Detect any pre-existing ebusd MQTT entities and offer to migrate them** (full history preservation — see [MIGRATION.md](MIGRATION.md))
   - Apply a sane default config tuned for underfloor heating (configurable)

That's it. No YAML.

## Migrating from `genia-air-pack` (the YAML pack) or raw ebusd MQTT discovery

If you previously used:
- [`genia-air-pack`](https://github.com/hirofairlane/genia-air-pack) — the YAML pack (this repo's predecessor)
- Raw discovery from the LukasGrebe ebusd add-on

… the integration will **claim your existing entities by `unique_id`** so:
- `entity_id` is preserved (or renamed cleanly with HA's built-in flow if you want the new naming)
- Custom `friendly_name` you set in HA UI survives
- **Recorder history is automatically preserved by HA** (entity_id continuity)
- **InfluxDB history needs one extra step** — we ship `scripts/migrate_influxdb.py` that copies all measurements from old entity_ids to new ones idempotently. See [MIGRATION.md](MIGRATION.md).

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design decisions (why subscribe MQTT vs read eBUS directly, how unique_ids are computed, how multi-zone is modelled, how cooling-with-solar-surplus integrates with the [AI Energy Optimizer](https://github.com/hirofairlane/ha-energy-optimizer) add-on).

## Acknowledgements

Built on the work of two people without whom none of this exists. Please support them directly:

- **[@john30](https://github.com/john30)** — author of [`ebusd`](https://github.com/john30/ebusd) and the [`ebusd-configuration`](https://github.com/john30/ebusd-configuration) CSV definitions (LGPL-3.0+). The integration ships the relevant Vaillant CSV subset in `custom_components/genia_air/ebusd_csv/` as derivative work and tracks upstream commits in [NOTICE.md](NOTICE.md).
- **[@LukasGrebe](https://github.com/LukasGrebe)** — maintainer of the [Home Assistant ebusd add-on](https://github.com/LukasGrebe/ha-addons) and the eBUS hardware adapter that makes this work in practice. **Buy his adapter** — cheap clones cause exactly the parsing errors this integration tries to avoid.

This integration is the spiritual successor of [`genia-air-pack`](https://github.com/hirofairlane/genia-air-pack), the YAML-only predecessor of the same project.

## License

Apache 2.0 for code. The bundled eBUS CSV definitions retain their original LGPL-3.0+ license — see `custom_components/genia_air/ebusd_csv/LICENSE`.
