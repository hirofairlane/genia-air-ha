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
