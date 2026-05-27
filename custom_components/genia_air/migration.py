"""Legacy-to-new entity_id migration.

When the user previously ran the LukasGrebe ebusd MQTT discovery (or
`genia-air-pack` YAML), they have entities in their entity_registry with
`unique_id` like `ebusd_ctls2_z1ManualTemp_tempv`. We want our new
integration's `number.heat_pump_setpoint_manual` to **adopt** that entity:

  - keep its `entity_id` (so dashboards/automations don't break)
  - keep its `name` (custom friendly_name set by the user)
  - keep its `area_id`, `hidden_by`, `disabled_by`
  - transfer ownership to our integration's config_entry

HA's Recorder history follows the entity_id, so when we adopt the entity,
history is automatically preserved.

InfluxDB history is a separate concern — see scripts/migrate_influxdb.py.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, LEGACY_UID_MAP

_LOGGER = logging.getLogger(__name__)


def _snapshot_path(hass: HomeAssistant) -> Path:
    """Where to save the entity_registry snapshot before mutating it."""
    base = Path(hass.config.path(".genia_air"))
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return base / f"registry_backup_{ts}.json"


async def take_registry_snapshot(hass: HomeAssistant) -> Path:
    """Dump a JSON copy of relevant entities for rollback purposes."""
    registry = er.async_get(hass)
    relevant: list[dict] = []
    legacy_uids = {v for v in LEGACY_UID_MAP.values() if v}
    for ent in registry.entities.values():
        if ent.unique_id in legacy_uids:
            relevant.append({
                "entity_id": ent.entity_id,
                "unique_id": ent.unique_id,
                "platform": ent.platform,
                "name": ent.name,
                "config_entry_id": ent.config_entry_id,
                "area_id": ent.area_id,
                "disabled_by": str(ent.disabled_by) if ent.disabled_by else None,
                "hidden_by": str(ent.hidden_by) if ent.hidden_by else None,
            })
    path = _snapshot_path(hass)
    path.write_text(json.dumps(relevant, indent=2))
    _LOGGER.info("Registry snapshot saved (%d relevant entities) → %s", len(relevant), path)
    return path


async def claim_legacy_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """For each entry in LEGACY_UID_MAP, find the legacy entity in the registry
    and re-key it under our new unique_id, attached to our config_entry.

    Returns a count dict with 'adopted', 'skipped_not_found', 'skipped_no_legacy'.
    """
    registry = er.async_get(hass)
    counts = {"adopted": 0, "skipped_not_found": 0, "skipped_no_legacy": 0,
              "skipped_collision": 0}

    # Index entity_registry by unique_id for fast lookup
    by_uid: dict[tuple[str, str], er.RegistryEntry] = {}
    for ent in registry.entities.values():
        by_uid[(ent.platform, ent.unique_id)] = ent

    for new_uid, legacy_uid in LEGACY_UID_MAP.items():
        if legacy_uid is None:
            counts["skipped_no_legacy"] += 1
            continue

        # The legacy entity was registered under the "mqtt" platform.
        legacy = by_uid.get(("mqtt", legacy_uid))
        if legacy is None:
            counts["skipped_not_found"] += 1
            continue

        # Collision check: is anything already registered under our new uid?
        existing_new = next(
            (e for e in registry.entities.values()
             if e.platform == DOMAIN and e.unique_id == new_uid),
            None
        )
        if existing_new is not None and existing_new.entity_id != legacy.entity_id:
            _LOGGER.warning(
                "Collision: new uid %s already exists as %s; legacy %s (%s) not adopted",
                new_uid, existing_new.entity_id, legacy_uid, legacy.entity_id,
            )
            counts["skipped_collision"] += 1
            continue

        if dry_run:
            _LOGGER.info("DRY-RUN: would adopt %s (uid=%s) under platform=%s, new_uid=%s",
                         legacy.entity_id, legacy_uid, DOMAIN, new_uid)
            counts["adopted"] += 1
            continue

        registry.async_update_entity(
            legacy.entity_id,
            new_unique_id=new_uid,
            config_entry_id=entry.entry_id,
            platform=DOMAIN,
        )
        _LOGGER.info("Adopted %s: uid %s → %s (platform mqtt → %s)",
                     legacy.entity_id, legacy_uid, new_uid, DOMAIN)
        counts["adopted"] += 1

    return counts


async def generate_influxdb_mapping_yaml(
    hass: HomeAssistant,
    target_path: Path | None = None,
) -> Path:
    """After successful registry migration, write a mapping.yaml that
    `scripts/migrate_influxdb.py` can consume.

    The mapping is `old_entity_id → new_entity_id`. We compute it by walking
    LEGACY_UID_MAP and looking up the entity_id that each legacy unique_id
    *had* before adoption (captured from the snapshot).
    """
    registry = er.async_get(hass)
    base = Path(hass.config.path(".genia_air"))
    base.mkdir(parents=True, exist_ok=True)
    out_path = target_path or (base / "influxdb_mapping.yaml")

    # Find the most recent snapshot (taken just before adoption)
    snapshots = sorted(base.glob("registry_backup_*.json"), reverse=True)
    if not snapshots:
        _LOGGER.warning("No registry snapshot found; cannot generate Influx mapping")
        return out_path
    snapshot = json.loads(snapshots[0].read_text())
    legacy_by_uid = {e["unique_id"]: e for e in snapshot}

    lines = [
        "# InfluxDB migration mapping for genia-air-ha",
        f"# Generated {datetime.now().isoformat()}",
        "# Run: python scripts/migrate_influxdb.py --mapping <this-file> [--dry-run]",
        "",
    ]
    pairs = 0
    for new_uid, legacy_uid in LEGACY_UID_MAP.items():
        if legacy_uid is None:
            continue
        legacy = legacy_by_uid.get(legacy_uid)
        if legacy is None:
            continue
        old_eid = legacy["entity_id"]
        # Find the new entity_id by looking up our new_uid in current registry
        new_ent = next(
            (e for e in registry.entities.values()
             if e.platform == DOMAIN and e.unique_id == new_uid),
            None
        )
        if new_ent is None:
            continue
        new_eid = new_ent.entity_id
        if old_eid == new_eid:
            lines.append(f"# {old_eid}: {new_eid}  # no-op (same entity_id)")
        else:
            lines.append(f"{old_eid}: {new_eid}")
            pairs += 1

    out_path.write_text("\n".join(lines) + "\n")
    _LOGGER.info("InfluxDB mapping written with %d entity pairs → %s", pairs, out_path)
    return out_path


async def rollback_migration(hass: HomeAssistant, entry: ConfigEntry) -> int:
    """Restore the legacy unique_ids from the most recent snapshot.

    Used by the `genia_air.rollback_migration` service. Returns the count of
    entities rolled back.
    """
    base = Path(hass.config.path(".genia_air"))
    snapshots = sorted(base.glob("registry_backup_*.json"), reverse=True)
    if not snapshots:
        _LOGGER.error("No snapshot available for rollback")
        return 0
    snapshot = json.loads(snapshots[0].read_text())
    registry = er.async_get(hass)
    rolled = 0
    for record in snapshot:
        # Find the entity by its current entity_id (which we preserved on adoption)
        ent = registry.async_get(record["entity_id"])
        if ent is None:
            continue
        registry.async_update_entity(
            record["entity_id"],
            new_unique_id=record["unique_id"],
            config_entry_id=record["config_entry_id"],
            platform=record["platform"],
        )
        rolled += 1
    _LOGGER.info("Rolled back %d entities from snapshot %s", rolled, snapshots[0])
    return rolled
