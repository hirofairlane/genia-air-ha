# Vaillant Genia Air

Standalone Home Assistant add-on to **control and optimize** a Vaillant
Genia Air heat pump.

This add-on is autonomous: it ships with its own UI, its own history
database and its own optimizer. The only external dependency is the
[ebusd add-on by LukasGrebe](https://github.com/LukasGrebe/ha-addons)
publishing eBUS messages to MQTT (the standard setup for talking to a
Vaillant heat pump from Home Assistant).

## Screenshots

> Screenshots will be added shortly. Tabs are:
> Overview · Charts · Controls · Optimizer · Diagnostics.

## What you get

- **Live overview** with KPI cards: ΔT, COP, modulation, electric/thermal
  power, flow temps, runtime hours.
- **Thermostat** for zone 1 with mode switching (off/heat/cool/auto) and
  inline setpoint editing.
- **Charts** (6 h / 24 h / 72 h / 7 days): temperatures, ΔT, electric vs
  thermal power, instantaneous COP. Stored locally in SQLite, snapshotted
  every minute.
- **Controls** for every zone setpoint, the heating curve and safety
  limits.
- **Optimizer** — deterministic control loop that:
  - Computes a weather-compensated max-flow-temp target and writes it to
    the heat pump every cycle when the outdoor reading changes.
  - Clamps any unsafe user-set max/min flow temp back into the safe range
    (anti-condensation and underfloor heating ceilings).
  - Performs a seasonal heat ↔ cool switchover based on the rolling
    outdoor average, with anti-flap hysteresis.
  - Alerts when ΔT(supply − return) drifts from target by > 0.8 K.
- **Diagnostics** tab listing every ebusd message seen, their ages and a
  force-read trigger.
- **MQTT Discovery** publishes a minimal `Genia Air (addon)` device into
  Home Assistant with 5 entities for automations.

## Installation

1. Install and start the
   [LukasGrebe ebusd add-on](https://github.com/LukasGrebe/ha-addons)
   so eBUS messages reach MQTT.
2. In Home Assistant: **Settings → Add-ons → Add-on Store → ⋯ → Repositories**
   and add `https://github.com/hirofairlane/genia-air-ha`.
3. Install **Vaillant Genia Air**. Defaults work for a typical single-zone
   underfloor-heating setup. Start the add-on.
4. Click **Open Web UI**, or use the **Genia Air** entry that appears in
   the Home Assistant sidebar.

## Configuration

| Option | Default | Notes |
|---|---|---|
| `topic_prefix` | `ebusd` | Must match the prefix configured in the ebusd add-on |
| `zone_count` | `1` | v0.1 controls zone 1 only |
| `optimize_flow_temp` | `true` | Master switch for the optimizer |
| `target_delta_t` | `5.0` K | Heating-side ΔT target used for anomaly alerts |
| `min_flow_temp_safe` | `14.0` °C | Anti-condensation floor in cooling mode |
| `max_flow_temp_safe` | `35.0` °C | Underfloor heating ceiling |
| `summer_temp_limit` | `19.0` °C | Heat ↔ cool seasonal switchover pivot |
| `optimize_cycle_minutes` | `5` | How often the optimizer evaluates |

## Troubleshooting

**Charts are empty.** The add-on snapshots one row per metric every
minute into a local SQLite database. Charts will be empty for the first
few minutes after install; come back in 10–20 min.

**Most entities show "unavailable" right after install.** eBUS messages
on the CTLS2 controller side are only emitted by the heat pump on
demand. The add-on triggers a forced read at boot and every 20 min
afterwards, plus you can press **Force-read all** in the Diagnostics tab
to trigger an extra round.

**MQTT connect fails (rc=5 in the log).** Means *not authorized*. The
add-on asks the Supervisor for the broker credentials at boot. Make
sure the MQTT integration is installed and a broker is running (the
Mosquitto add-on is the easiest option).

**Setpoint slider in Controls keeps jumping back.** Charts and sliders
re-fetch state every five seconds. Click and *release* the slider; the
write goes out on `change`, not `input`.

## Architecture in one paragraph

A single Python file (`/usr/bin/genia_air.py`) runs Flask for the UI,
paho-mqtt for the eBUS data, APScheduler for the optimizer cycles and
SQLite for history. The embedded HTML panel (one big string) renders
with Chart.js. The add-on listens to `<topic_prefix>/+/+`, decodes the
ebusd JSON payloads, computes derived values (ΔT, COP, hvac action),
runs the optimizer every five minutes and publishes a tiny MQTT Discovery
device back into Home Assistant.

See [`PLAN-ADDON.md`](../PLAN-ADDON.md) in the repository for the
full design notes.

## What's intentionally NOT here in v0.1

- ML-based optimization (the optimizer is deterministic; ML is planned).
- Multi-zone (Z2/Z3) support.
- Domestic hot-water control (the Genia Air doesn't drive DHW in many
  installations).
- Direct pump-speed actuation (the HMU manages pump speed internally
  and does not expose a PWM control over eBUS).

## License

MIT.
