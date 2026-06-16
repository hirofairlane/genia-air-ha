# Loop notes — low-priority backlog

> Tracking notes for the 10-day autonomous loop started 2026-06-13.
> Each cron fire is one short work session. Append a `## Session N — date`
> heading with what was done.

## Backlog (priority order)

### 1. Stabilization
- [ ] Watch `docker logs addon_local_genia_air` for new errors each session
- [ ] Validate `/api/state` responds in <500 ms
- [ ] Capture & fix any new exceptions before doing other work

### 2. Soft-launch polish
- [ ] Addon `icon.png` (currently `icon: false` in config.yaml)
- [ ] Addon `logo.png` (store header)
- [ ] Screenshots of all 5 tabs for README
- [ ] genia_air/README.md polish (install GIF, config example, troubleshooting)
- [ ] GitHub repo topics: `home-assistant`, `addon`, `vaillant`, `heat-pump`, `ebusd`, `mqtt`
- [ ] Repo description + website link on GitHub
- [ ] Pin a "v0.1.4 released" GitHub issue with install instructions

### 3. Soft-launch posts (draft, do NOT publish)
- [ ] `marketing/ha-forum-post.md` — Home Assistant Community Forum
- [ ] `marketing/reddit-r-homeassistant.md` — Reddit post + screenshot strategy
- [ ] `marketing/ebusd-issue.md` — friendly issue on LukasGrebe/ha-addons
- [ ] All three include screenshot placeholders, real install steps

### 4. v0.1.5 — bug fixes from real use
Wait for soft-launch feedback. Likely candidates:
- [ ] Better handling when ebusd addon goes down (don't spam errors)
- [ ] Setpoint slider initial value sometimes wrong if state hasn't arrived
- [ ] Optimizer enabled by default? (currently `optimize_flow_temp: true`)

### 5. v0.2 features (post-soft-launch)
- [ ] Multi-zone (z2/z3) when `zone_count > 1`
- [ ] Translations infrastructure (en, es, de) without polluting source
- [ ] `panel_admin: true` option (some users may want admin-only access)
- [ ] HACS submission (yes, HACS supports add-on repositories since 2024)
- [ ] PR to `frenck/awesome-home-assistant`

### 6. v0.3+ ideas
- [ ] Tariff-aware optimizer (read price-of-electricity from HA, charge thermal mass in valley)
- [ ] Integration with Energy Optimizer addon (read its decisions for synergy)
- [ ] ML-based COP prediction (when we have enough history)
- [ ] DHW (Magna Aqua + Meross) optional companion module

## Session rules
- One or two small items per fire. **Bias toward polish/docs over implementation.**
- If you touch code, deploy + verify on Sergio's HA before declaring done.
- Version bumps: config.yaml + CHANGELOG.md + `VERSION =` in genia_air.py together.
- Commit every meaningful change. Push to main is OK.
- Keep the user-facing report to 2-3 sentences per session.

## Session history

### Session 0 — 2026-06-13 (setup)
- Wrote LOOP-NOTES.md to coordinate future sessions
- Confirmed addon healthy in prod (v0.1.4, MQTT subs working, no errors)
- Polished `genia_air/README.md` (install steps, troubleshooting, screenshot anchors)
- Scheduled daily cron (auto-expires in 7 days; Sergio re-prompts for last 3)

### Session 1 — 2026-06-16 (architectural pivot to self-contained)
**Sergio uninstalled the LukasGrebe ebusd add-on** to force the add-on
to be standalone. v0.2.0 work:

- Switched base image: Alpine → Debian bookworm.
- Bundled `ebusd v25.1` (john30) via the upstream `.deb` per arch.
- Bundled the Vaillant CSV definitions at `/usr/share/ebusd/vaillant/`
  (08.hmu, 15.ctls2, 76.vwz, broadcast) — copied from the NAS backup.
- Python now supervises ebusd as a subprocess (spawn, log forwarding,
  watchdog every 15 s, clean signal teardown via `tini` as PID 1).
- New config option `ebus_device` (defaults to Sergio's adapter at
  `ens:192.168.1.171:9999`); also `ebusd_log_level`.
- New `/api/ebusd` endpoint (start/stop/restart) and an ebusd status
  card + Restart button in the Diagnostics tab.
- `/api/health` now reports `ebusd_running`, `ebusd_pid`, `ebusd_restarts`.
- Updated CHANGELOG + README.

**Pending in next session(s) — historical data migration:**
Old entities still live in HA's registry as `restored=True` because
the LukasGrebe add-on left retained MQTT discovery messages. The
historical data is in HA Recorder and InfluxDB. Two-step migration:

1. **Within HA** — rename our 5 addon discovery entities so they
   inherit the legacy `unique_id`s (sensor.aerotermia_*) and keep
   the Recorder history attached. Use the WS `config/entity_registry/update`
   API. The HACS code in `_reference/custom_components_legacy/migration.py`
   has the pattern to copy.
2. **In InfluxDB** — write a one-shot script that renames the
   `entity_id` tag on existing measurements (or inserts pointers) so
   long-term graphs still see the new names. Plan: leverage
   `entity_ids` mapping documented in
   `/mnt/18T/MIO/Proyectos/ebusd/BASELINE_CALIBRADO_2026-05-07.md`.

Then verify Energy Optimizer (which reads InfluxDB for thermal model
fits) still finds the data under the new names.
