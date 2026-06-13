# Vaillant Genia Air — Home Assistant add-on

Standalone HA add-on to **control and optimize** a Vaillant Genia Air
heat pump. Self-contained: own dashboard, own history, own optimizer.

This repository hosts the add-on. To install it, add the repo URL to the
Home Assistant Add-on Store:

```
https://github.com/hirofairlane/genia-air-ha
```

Detailed user docs live in [`genia_air/README.md`](genia_air/README.md).
Architecture and rationale in [`PLAN-ADDON.md`](PLAN-ADDON.md).

## Repository layout

| Path | Purpose |
|---|---|
| `genia_air/` | The add-on (config.yaml, Dockerfile, source) — distributed |
| `repository.yaml` | Add-on store entry |
| `PLAN-ADDON.md` | Living design doc |
| `_reference/` | Old HACS custom integration code, kept as reference for the eBUS parser only |

## Status

v0.1.0 — first standalone release. Optimizer is deterministic, single-zone
focus, no ML yet. See [`PLAN-ADDON.md`](PLAN-ADDON.md) for the roadmap.

## License

MIT.
