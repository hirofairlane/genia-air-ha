# Roadmap — genia-air-ha

## v0.1.0 — alpha (target: 2-3 weeks from now)

Goal: **installable from HACS, claims existing legacy entities, no data loss**.

- [x] Repo structure (this commit)
- [x] LICENSE + NOTICE
- [x] README + ARCHITECTURE + MIGRATION docs
- [x] InfluxDB migration script (`scripts/migrate_influxdb.py`) — needs end-to-end test
- [ ] `__init__.py` async_setup_entry actually wired
- [ ] `config_flow.py` with the 5-step UI
- [ ] `migration.py` doing the entity_registry claim
- [ ] `mqtt_client.py` subscribing to ebusd topics and routing payloads to entities
- [ ] `sensor.py` platform (read-only telemetry)
- [ ] `binary_sensor.py` platform (4 alerts)
- [ ] `number.py` platform (writable setpoints with safe ranges)
- [ ] `select.py` platform (OpMode / preset)
- [ ] First-pass `climate.py` (zone 1 only)
- [ ] Bundle eBUS CSVs under `custom_components/genia_air/ebusd_csv/` with LGPL LICENSE
- [ ] Unit tests for the migration mapping logic
- [ ] Test on Sergio's live HA (only validated installation)
- [ ] Tag v0.1.0, submit to HACS default

## v0.2.0 — beta (target: 1-2 weeks after v0.1)

- [ ] Multi-zone support (Z2/Z3 if user has them physically)
- [ ] Multi-circuit (Hc2/Hc3)
- [ ] DHW (Hwc) support behind a flag (off by default — most Genia Air installs use separate DHW)
- [ ] Spanish + English translations
- [ ] Repair flows for common issues (ebusd addon offline, MQTT broker down, scan never finishes)
- [ ] InfluxDB v2.x verified (currently only v1.x tested)
- [ ] CI: GitHub Actions running tests + hassfest

## v1.0.0 — stable (target: after one full winter of operation)

- [ ] Stable API contract (no breaking changes within 1.x)
- [ ] Documented integration with [ha-energy-optimizer](https://github.com/hirofairlane/ha-energy-optimizer): tier-aware setpoint adjustments, COP exposure, solar-surplus-driven cooling
- [ ] Energy dashboard wiring (statistics on consumption + production)
- [ ] Service `genia_air.rollback_migration` (restore legacy state)
- [ ] Service `genia_air.export_diagnostics` (one-click bug report)
- [ ] Tested on at least 3 different Genia Air firmware combos (community feedback needed)

## v2.0 — speculative (not committed)

- [ ] Support other Vaillant heat pump families: aroTHERM Plus, FlexoTHERM, geniaSet
- [ ] Optional direct bus access (skip ebusd dependency) — large effort, only if community demand
- [ ] ML-driven curve auto-tuning (own COP curve learning, like the AdaptHeatCurve but client-side)

## Non-goals

- Replacing or competing with `ebusd` itself
- Supporting non-Vaillant heat pumps (this is intentionally Vaillant-specific)
- Building a UI dashboard inside the integration (dashboards belong in Lovelace, separately)
