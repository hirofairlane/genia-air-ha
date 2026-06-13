# Vaillant Genia Air

Standalone Home Assistant add-on to **control and optimize** a Vaillant
Genia Air heat pump.

This add-on is autonomous: it ships with its own UI, its own history
database and its own optimizer. The only external dependency is the
[ebusd add-on by LukasGrebe](https://github.com/LukasGrebe/ha-addons)
publishing eBUS messages to MQTT (the standard setup for talking to a
Vaillant heat pump from Home Assistant).

## What you get

- **Live dashboard** with KPI cards: ΔT, COP, modulation, power in/out,
  flow temps, runtime hours.
- **Thermostat** for zone 1 with mode switching (off/heat/cool/auto) and
  setpoint editing inline.
- **Charts** (last 6 h / 24 h / 72 h / 7 days): temperatures, ΔT, electric
  vs thermal power, COP.
- **Controls** for every zone setpoint, flow temperature curve, safety
  limits.
- **Optimizer** — deterministic control loop that:
  - Computes a weather-compensated max flow temp and writes it to the
    Genia Air every cycle when outdoor changes.
  - Clamps any unsafe user-set max/min flow temp back into the safe range.
  - Performs a seasonal switchover (heat↔cool) based on the outdoor
    rolling average vs. the configured summer limit, with anti-flap
    hysteresis.
  - Alerts when ΔT(supply-return) drifts from the target by > 0.8 K.
- **Diagnostic** tab with every ebusd message seen, ages, force-read.
- **MQTT Discovery** publishes a minimal `Genia Air (addon)` device into
  Home Assistant with 5 entities for automations.

## Installation

1. Make sure the **ebusd** add-on is installed and publishing to MQTT.
2. Add this repository to Home Assistant: Settings → Add-ons → Add-on Store
   → ⋯ → Repositories →
   `https://github.com/hirofairlane/genia-air-ha`.
3. Install **Vaillant Genia Air**, leave the defaults, **Start**.
4. Open the side-bar entry **Genia Air**.

## Configuration

| Option | Default | Notes |
|---|---|---|
| `topic_prefix` | `ebusd` | Must match your ebusd add-on |
| `zone_count` | `1` | v0.1 controls zone 1 only |
| `optimize_flow_temp` | `true` | Master switch for the optimizer |
| `target_delta_t` | `5.0` K | Heating-side ΔT target for alerts |
| `min_flow_temp_safe` | `14.0` °C | Anti-condensation floor in cooling |
| `max_flow_temp_safe` | `35.0` °C | Underfloor heating ceiling |
| `summer_temp_limit` | `19.0` °C | Heat↔cool switchover pivot |
| `optimize_cycle_minutes` | `5` | How often the optimizer runs |

## What's intentionally NOT here in v0.1

- ML-based optimization (the optimizer is deterministic; ML is planned).
- Multi-zone (Z2/Z3) support.
- Domestic hot water control (the Genia Air doesn't drive ACS in many
  installations).
- Direct pump-speed actuation (the HMU manages it internally and does
  not expose a PWM control over eBUS).

## License

MIT.
