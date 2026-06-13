# Changelog

## 0.1.4 — 2026-06-13

- UI fully translated to English (the source-of-truth language for the
  project — Spanish copy belongs in a future translations layer).
- Fix Chart.js "Canvas is already in use" error: use `Chart.getChart()`
  to find any chart already attached to the canvas and destroy it
  before rendering, instead of relying on our local CHARTS map.
- Add a CHARTS_BUSY guard so two `loadCharts()` calls (e.g. tab click
  + manual refresh) cannot overlap.
- Chart container heights set via CSS (`.chart-wrap`) instead of being
  patched in JS each render.
- Load `chartjs-adapter-date-fns` so the time-scale X axis renders.

## 0.1.3 — 2026-06-13

- `run.sh` now uses `#!/usr/bin/with-contenv sh` — the HA base-image s6
  init scrubs env for legacy-services so SUPERVISOR_TOKEN never reached
  Python. With `with-contenv` the token is preserved.
- Python falls back to HASSIO_TOKEN if SUPERVISOR_TOKEN is missing.

## 0.1.2 — 2026-06-13

- Retry the supervisor MQTT introspection with backoff (the supervisor
  isn't always ready at the exact moment the add-on starts).
- Print boot diagnostics to stderr before the logger is configured.

## 0.1.1 — 2026-06-13

- Moved supervisor MQTT introspection from `run.sh` (broken under busybox
  + `set -e`) into the Python entrypoint with proper error handling.

## 0.1.0 — 2026-06-13

First standalone add-on release.

- Single-file Flask app with embedded dashboard (5 tabs:
  Estado / Gráficas / Controles / Optimizer / Diagnóstico).
- MQTT subscriber for the LukasGrebe `ebusd` add-on (`ebusd/+/+`).
- Initial sync on boot — force-reads every msg the add-on cares about.
- SQLite at `/data/history.db` for snapshots (per-minute, 12 series).
- Deterministic optimizer: weather-compensated max flow temp,
  seasonal switchover, safety enforcement on user writes, ΔT
  anomaly alert.
- MQTT Discovery publishes 5 entities for HA automations.
- Auth: `X-Ingress-Path` required on all routes,
  `X-Hass-User` required on writes.
