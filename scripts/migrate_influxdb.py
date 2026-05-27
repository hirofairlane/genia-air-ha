#!/usr/bin/env python3
"""Migrate InfluxDB history from legacy ebusd-discovery entity_ids to genia-air-ha.

Idempotent. Supports dry-run. Supports InfluxDB v1.x (InfluxQL) and v2.x (Flux).

Example (v1.x):
    python migrate_influxdb.py \\
        --influx-host 192.168.1.131 --influx-port 8086 \\
        --influx-db home_assistant \\
        --influx-user homeassistant --influx-password "$INFLUX_PASSWORD" \\
        --mapping mapping.yaml --dry-run

Example (v2.x):
    python migrate_influxdb.py \\
        --influx-url http://192.168.1.131:8086 \\
        --influx-org "Sergio" --influx-bucket "home_assistant" \\
        --influx-token "$INFLUX_TOKEN" \\
        --mapping mapping.yaml

See MIGRATION.md for full documentation.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml  # PyYAML — `pip install pyyaml`

LOG = logging.getLogger("migrate_influxdb")


# ----------------------------------------------------------------------------
# Mapping file
# ----------------------------------------------------------------------------

@dataclass
class Mapping:
    old_entity_id: str
    new_entity_id: str


def load_mapping(path: Path) -> list[Mapping]:
    """Load YAML mapping file. Skip lines where old == new (no-op)."""
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    out = []
    for old, new in data.items():
        if not isinstance(old, str) or not isinstance(new, str):
            LOG.warning("Skipping non-string entry: %r → %r", old, new)
            continue
        if old == new:
            LOG.debug("Skip no-op mapping %s", old)
            continue
        out.append(Mapping(old_entity_id=old, new_entity_id=new))
    return out


# ----------------------------------------------------------------------------
# InfluxDB v1.x backend
# ----------------------------------------------------------------------------

class InfluxV1Backend:
    """Backend for InfluxDB 1.x using `influxdb` Python client."""

    def __init__(self, host: str, port: int, db: str, user: str, password: str, ssl: bool = False):
        try:
            from influxdb import InfluxDBClient
        except ImportError:
            sys.exit("Install the v1 client: pip install influxdb")
        self.client = InfluxDBClient(host=host, port=port, username=user,
                                     password=password, database=db, ssl=ssl, verify_ssl=ssl)
        self.db = db

    def list_measurements_for_entity(self, entity_id: str) -> list[str]:
        """Return all measurements that have at least one series with this entity_id tag."""
        # InfluxQL: SHOW MEASUREMENTS WITH MEASUREMENT =~ /./ WHERE entity_id = 'x'
        q = f"SHOW SERIES WHERE entity_id = '{entity_id}'"
        result = self.client.query(q)
        measurements = set()
        for point in result.get_points():
            # series format is "measurement,tag1=...,entity_id=..."
            key = point.get("key", "")
            if "," in key:
                measurements.add(key.split(",")[0])
        return sorted(measurements)

    def count_points(self, measurement: str, entity_id: str) -> int:
        q = f"SELECT count(*) FROM \"{measurement}\" WHERE entity_id = '{entity_id}'"
        try:
            result = self.client.query(q)
        except Exception as e:
            LOG.warning("count_points failed for %s/%s: %s", measurement, entity_id, e)
            return 0
        for point in result.get_points():
            # count(*) returns multiple count_<field> columns; take the max
            return max((v for k, v in point.items() if k.startswith("count_") and isinstance(v, int)), default=0)
        return 0

    def iter_points(self, measurement: str, entity_id: str, batch_size: int = 10000):
        """Yield batches of points (dicts) for the given measurement+entity_id."""
        offset = 0
        while True:
            q = (f"SELECT * FROM \"{measurement}\" "
                 f"WHERE entity_id = '{entity_id}' "
                 f"ORDER BY time ASC LIMIT {batch_size} OFFSET {offset}")
            result = self.client.query(q, epoch="ns")
            batch = list(result.get_points())
            if not batch:
                return
            yield batch
            offset += len(batch)
            if len(batch) < batch_size:
                return

    def write_points(self, measurement: str, points: list[dict], new_entity_id: str):
        """Write points back with rewritten entity_id tag."""
        body = []
        for p in points:
            timestamp = p.pop("time")
            # Re-classify tags vs fields. We know `entity_id` and `domain` are tags;
            # everything else is field. (HA's influxdb exporter conventions.)
            tags = {"entity_id": new_entity_id}
            if "domain" in p:
                tags["domain"] = p.pop("domain")
            # Other typical tags HA writes
            for tag_key in ("source",):
                if tag_key in p:
                    tags[tag_key] = p.pop(tag_key)
            # Remove the legacy entity_id if it was returned as a field
            p.pop("entity_id", None)
            body.append({
                "measurement": measurement,
                "tags": tags,
                "time": timestamp,
                "fields": p,
            })
        if body:
            self.client.write_points(body, time_precision="n")

    def delete_series(self, measurement: str, entity_id: str):
        q = f"DROP SERIES FROM \"{measurement}\" WHERE entity_id = '{entity_id}'"
        self.client.query(q)


# ----------------------------------------------------------------------------
# InfluxDB v2.x backend
# ----------------------------------------------------------------------------

class InfluxV2Backend:
    """Backend for InfluxDB 2.x using `influxdb-client` Python client."""

    def __init__(self, url: str, token: str, org: str, bucket: str):
        try:
            from influxdb_client import InfluxDBClient, Point
            from influxdb_client.client.write_api import SYNCHRONOUS
        except ImportError:
            sys.exit("Install the v2 client: pip install influxdb-client")
        self.client = InfluxDBClient(url=url, token=token, org=org)
        self.bucket = bucket
        self.org = org
        self._Point = Point
        self._write_api = self.client.write_api(write_options=SYNCHRONOUS)
        self._query_api = self.client.query_api()
        self._delete_api = self.client.delete_api()

    def list_measurements_for_entity(self, entity_id: str) -> list[str]:
        flux = (f'import "influxdata/influxdb/schema"\n'
                f'schema.tagValues(bucket: "{self.bucket}", tag: "_measurement", '
                f'predicate: (r) => r.entity_id == "{entity_id}")')
        result = self._query_api.query(flux)
        return [record.get_value() for table in result for record in table.records]

    def count_points(self, measurement: str, entity_id: str) -> int:
        flux = (f'from(bucket: "{self.bucket}") '
                f'|> range(start: 0) '
                f'|> filter(fn: (r) => r._measurement == "{measurement}" '
                f'                  and r.entity_id == "{entity_id}") '
                f'|> count() |> sum()')
        try:
            result = self._query_api.query(flux)
            for table in result:
                for record in table.records:
                    return int(record.get_value())
        except Exception as e:
            LOG.warning("count_points (v2) failed: %s", e)
        return 0

    def iter_points(self, measurement: str, entity_id: str, batch_size: int = 10000):
        # InfluxDB 2.x: range queries with offset are tricky; chunk by time windows.
        # For simplicity here we yield a single batch (full range). If needed,
        # implement windowed chunking based on count.
        flux = (f'from(bucket: "{self.bucket}") '
                f'|> range(start: 0) '
                f'|> filter(fn: (r) => r._measurement == "{measurement}" '
                f'                  and r.entity_id == "{entity_id}")')
        tables = self._query_api.query(flux)
        batch = []
        for table in tables:
            for record in table.records:
                batch.append({
                    "time": record.get_time(),
                    "field": record.get_field(),
                    "value": record.get_value(),
                    **{k: v for k, v in record.values.items() if k.startswith("_") is False and k != "result" and k != "table"},
                })
                if len(batch) >= batch_size:
                    yield batch
                    batch = []
        if batch:
            yield batch

    def write_points(self, measurement: str, points: list[dict], new_entity_id: str):
        from influxdb_client.client.write_api import SYNCHRONOUS
        ps = []
        for p in points:
            point = self._Point(measurement).time(p["time"])
            point.tag("entity_id", new_entity_id)
            for k, v in p.items():
                if k in ("time", "entity_id"):
                    continue
                if k == "field":
                    point.field(p.get("field"), p.get("value"))
                elif k == "value":
                    pass  # paired with "field"
                else:
                    if isinstance(v, (int, float, bool, str)):
                        point.field(k, v)
            ps.append(point)
        if ps:
            self._write_api.write(bucket=self.bucket, org=self.org, record=ps)

    def delete_series(self, measurement: str, entity_id: str):
        from datetime import datetime, timezone
        self._delete_api.delete(
            start=datetime(1970, 1, 1, tzinfo=timezone.utc),
            stop=datetime.now(timezone.utc),
            predicate=f'_measurement="{measurement}" AND entity_id="{entity_id}"',
            bucket=self.bucket, org=self.org,
        )


# ----------------------------------------------------------------------------
# Core migration logic
# ----------------------------------------------------------------------------

def migrate(backend, mapping: list[Mapping], *, dry_run: bool, delete_after_verify: bool,
            batch_size: int = 10000) -> dict:
    summary = {"migrated_entities": 0, "skipped_entities": 0, "total_points": 0}

    for m in mapping:
        measurements = backend.list_measurements_for_entity(m.old_entity_id)
        if not measurements:
            LOG.info("No data for old entity_id %s — skipping", m.old_entity_id)
            summary["skipped_entities"] += 1
            continue

        entity_total = 0
        for measurement in measurements:
            count = backend.count_points(measurement, m.old_entity_id)
            entity_total += count
            label = "DRY-RUN " if dry_run else ""
            LOG.info("%s%s [%s]: %d points → %s", label, m.old_entity_id,
                     measurement, count, m.new_entity_id)
            if dry_run or count == 0:
                continue

            t0 = time.time()
            written = 0
            for batch in backend.iter_points(measurement, m.old_entity_id, batch_size):
                backend.write_points(measurement, batch, m.new_entity_id)
                written += len(batch)
                if written % 50000 == 0:
                    LOG.info("  %d/%d written (%.0fs)", written, count, time.time() - t0)

            # Verify
            new_count = backend.count_points(measurement, m.new_entity_id)
            if new_count < count:
                LOG.error("VERIFY FAIL: wrote %d, found %d in %s for %s — NOT deleting source",
                          written, new_count, measurement, m.new_entity_id)
                continue

            if delete_after_verify:
                LOG.info("  Verified (%d points). Deleting source series.", new_count)
                backend.delete_series(measurement, m.old_entity_id)

        summary["total_points"] += entity_total
        summary["migrated_entities"] += 1

    return summary


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

    # InfluxDB v1
    p.add_argument("--influx-host", help="(v1) InfluxDB host", default=None)
    p.add_argument("--influx-port", type=int, default=8086, help="(v1) port")
    p.add_argument("--influx-db", help="(v1) database name", default=None)
    p.add_argument("--influx-user", default=None)
    p.add_argument("--influx-password", default=None)
    p.add_argument("--influx-ssl", action="store_true")

    # InfluxDB v2
    p.add_argument("--influx-url", help="(v2) base URL", default=None)
    p.add_argument("--influx-token", default=None)
    p.add_argument("--influx-org", default=None)
    p.add_argument("--influx-bucket", default=None)

    p.add_argument("--mapping", required=True, type=Path, help="YAML mapping file old→new")
    p.add_argument("--dry-run", action="store_true", help="Show what would happen, no writes")
    p.add_argument("--delete-after-verify", action="store_true",
                   help="Drop the old series after verifying the new one has equal point count")
    p.add_argument("--batch-size", type=int, default=10000)
    p.add_argument("-v", "--verbose", action="count", default=0)

    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose >= 2 else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    # Detect backend
    if args.influx_token:
        if not (args.influx_url and args.influx_org and args.influx_bucket):
            sys.exit("v2 mode requires --influx-url, --influx-org, --influx-bucket and --influx-token")
        backend = InfluxV2Backend(args.influx_url, args.influx_token, args.influx_org, args.influx_bucket)
        LOG.info("Backend: InfluxDB v2 (%s, bucket=%s)", args.influx_url, args.influx_bucket)
    else:
        if not (args.influx_host and args.influx_db and args.influx_user):
            sys.exit("v1 mode requires --influx-host, --influx-db, --influx-user, --influx-password")
        backend = InfluxV1Backend(args.influx_host, args.influx_port, args.influx_db,
                                  args.influx_user, args.influx_password, args.influx_ssl)
        LOG.info("Backend: InfluxDB v1 (%s:%d, db=%s)", args.influx_host, args.influx_port, args.influx_db)

    mapping = load_mapping(args.mapping)
    LOG.info("Loaded %d mapping entries", len(mapping))

    if args.dry_run:
        LOG.info("=== DRY RUN (no writes will occur) ===")

    summary = migrate(backend, mapping,
                      dry_run=args.dry_run,
                      delete_after_verify=args.delete_after_verify,
                      batch_size=args.batch_size)

    LOG.info("=== SUMMARY ===")
    LOG.info("Migrated entities: %d", summary["migrated_entities"])
    LOG.info("Skipped (no data): %d", summary["skipped_entities"])
    LOG.info("Total points: %d", summary["total_points"])


if __name__ == "__main__":
    main()
