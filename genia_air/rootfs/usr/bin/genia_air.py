#!/usr/bin/env python3
"""Vaillant Genia Air — standalone HA addon.

One file. Loop pattern modeled on Energy Optimizer:
  * MQTT client subscribed to ebusd/+/+ (paho)
  * STATE dict (circuit, msg) -> last decoded payload + timestamp
  * SQLite at /data/history.db for snapshots, decisions, errors
  * APScheduler: initial sync, snapshot, optimizer cycle, health
  * Flask app with /api/* JSON + / serving the embedded PANEL HTML
  * MQTT Discovery — six minimal entities back into HA
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, abort, g, jsonify, request

# ───────────────────────────────────────────────────────────────────────────
# Config & logging
# ───────────────────────────────────────────────────────────────────────────

VERSION = "0.2.0"


def _load_options() -> dict:
    """Read /data/options.json — written by the supervisor from the user config."""
    try:
        with open("/data/options.json") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logging.warning("Cannot read /data/options.json: %s", exc)
        return {}


def _query_supervisor_mqtt(retries: int = 12, backoff: float = 2.0) -> dict:
    """Ask the supervisor for the MQTT broker config.

    Run at boot, so the supervisor may not be ready yet — retry with backoff.
    Logging isn't configured yet at this point; print to stderr so it survives.
    """
    import sys as _sys
    import time as _t

    token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN") or ""
    if not token:
        print("[boot] No SUPERVISOR_TOKEN/HASSIO_TOKEN — running outside HA?", file=_sys.stderr)
        return {}
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(
                "http://supervisor/services/mqtt",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            )
            if r.status_code == 200:
                data = r.json().get("data", {})
                print(
                    f"[boot] Got MQTT creds from supervisor on attempt {attempt} "
                    f"(host={data.get('host')}, user={data.get('username')})",
                    file=_sys.stderr,
                )
                return {
                    "host": data.get("host", "core-mosquitto"),
                    "port": int(data.get("port", 1883)),
                    "username": data.get("username", ""),
                    "password": data.get("password", ""),
                }
            print(
                f"[boot] Supervisor /services/mqtt attempt {attempt} → HTTP {r.status_code}",
                file=_sys.stderr,
            )
        except Exception as exc:
            print(
                f"[boot] Supervisor /services/mqtt attempt {attempt} failed: {exc}",
                file=_sys.stderr,
            )
        _t.sleep(backoff)
    print("[boot] Giving up on supervisor MQTT introspection", file=_sys.stderr)
    return {}


_opts = _load_options()
_mqtt = _query_supervisor_mqtt()

CONF = {
    "ebus_device":           _opts.get("ebus_device", "ens:192.168.1.171:9999"),
    "ebusd_log_level":       str(_opts.get("ebusd_log_level", "notice")),
    "topic_prefix":          _opts.get("topic_prefix", "ebusd"),
    "zone_count":            int(_opts.get("zone_count", 1)),
    "optimize_flow_temp":    bool(_opts.get("optimize_flow_temp", True)),
    "target_delta_t":        float(_opts.get("target_delta_t", 5.0)),
    "min_flow_temp_safe":    float(_opts.get("min_flow_temp_safe", 14.0)),
    "max_flow_temp_safe":    float(_opts.get("max_flow_temp_safe", 35.0)),
    "summer_temp_limit":     float(_opts.get("summer_temp_limit", 19.0)),
    "optimize_cycle_min":    int(_opts.get("optimize_cycle_minutes", 5)),
    "mqtt_host":             _mqtt.get("host", "core-mosquitto"),
    "mqtt_port":             _mqtt.get("port", 1883),
    "mqtt_user":             _mqtt.get("username", ""),
    "mqtt_pass":             _mqtt.get("password", ""),
    "log_level":             str(_opts.get("log_level", "info")).upper(),
}

logging.basicConfig(
    level=getattr(logging, CONF["log_level"], logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("genia_air")
log.info("Genia Air addon v%s starting", VERSION)
log.info("Config: %s", {k: v for k, v in CONF.items() if k != "mqtt_pass"})

DATA = Path("/data")
DATA.mkdir(exist_ok=True)
DB_PATH = DATA / "history.db"
DECISIONS_PATH = DATA / "decisions.jsonl"

# ───────────────────────────────────────────────────────────────────────────
# STATE — live snapshot of ebusd readings
# ───────────────────────────────────────────────────────────────────────────

STATE_LOCK = threading.Lock()
STATE: dict[tuple[str, str], dict] = {}   # (circuit, msg) → {fields, raw, ts}
LAST_DECISIONS: "OrderedDict[float, dict]" = OrderedDict()
OPTIMIZER_ENABLED = CONF["optimize_flow_temp"]
HEALTH = {"ok": True, "reasons": [], "since": time.time()}

# (circuit, msg) entries the addon cares about. Drives the initial sync and
# the "Diagnostic" tab. Case must match what ebusd publishes (case rule:
# lowercase first char only if second is a digit, e.g. Z1ManualTemp→z1ManualTemp,
# Hc1MaxFlowTempDesired stays).
SUBSCRIBED_MSGS: list[tuple[str, str]] = [
    # HMU — telemetry
    ("hmu", "CurrentConsumedPower"),
    ("hmu", "CurrentYieldPower"),
    ("hmu", "CurrentCompressorUtil"),
    ("hmu", "WaterThroughput"),
    ("hmu", "Status01"),
    ("hmu", "State"),
    ("hmu", "Hours"),
    ("hmu", "HoursHc"),
    ("hmu", "HoursCool"),
    ("hmu", "errorhistory"),
    # CTLS2 — zone 1 (multi-zone is post-v0.1)
    ("ctls2", "z1RoomTemp"),
    ("ctls2", "z1ActualRoomTempDesired"),
    ("ctls2", "z1ManualTemp"),
    ("ctls2", "z1DayTemp"),
    ("ctls2", "z1NightTemp"),
    ("ctls2", "z1HolidayTemp"),
    ("ctls2", "z1CoolingTemp"),
    ("ctls2", "z1QuickVetoTemp"),
    ("ctls2", "z1OpMode"),
    ("ctls2", "z1OpModeCooling"),
    ("ctls2", "z1SfMode"),
    ("ctls2", "OutsideTempAvg"),
    ("ctls2", "MaintenanceDue"),
    ("ctls2", "GlobalSystemOff"),
    ("ctls2", "YieldTotal"),
    ("ctls2", "Hc1MaxFlowTempDesired"),
    ("ctls2", "Hc1MinFlowTempDesired"),
    ("ctls2", "Hc1SummerTempLimit"),
    ("ctls2", "ContinuosHeating"),
]

# ───────────────────────────────────────────────────────────────────────────
# Persistence — SQLite for snapshots and decisions
# ───────────────────────────────────────────────────────────────────────────

DB_LOCK = threading.Lock()


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def db_init() -> None:
    with DB_LOCK, db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                ts          INTEGER PRIMARY KEY,
                series      TEXT,
                value       REAL,
                PRIMARY KEY (ts, series)
            ) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS ix_snapshots_series ON snapshots(series, ts);

            CREATE TABLE IF NOT EXISTS decisions (
                ts          INTEGER PRIMARY KEY,
                kind        TEXT,
                reason      TEXT,
                detail      TEXT
            );

            CREATE TABLE IF NOT EXISTS errors (
                ts          INTEGER PRIMARY KEY,
                code        TEXT,
                detail      TEXT
            );
            """
        )


def db_init_safe() -> None:
    """SQLite up to 3.38 doesn't accept the composite PK syntax above. Fallback
    to a simpler schema if creation failed."""
    try:
        db_init()
    except sqlite3.OperationalError as exc:
        log.warning("db_init: composite PK failed (%s), using fallback", exc)
        with DB_LOCK, db_connect() as conn:
            conn.executescript(
                """
                DROP TABLE IF EXISTS snapshots;
                CREATE TABLE snapshots (
                    ts      INTEGER,
                    series  TEXT,
                    value   REAL
                );
                CREATE INDEX IF NOT EXISTS ix_snapshots_series_ts
                    ON snapshots(series, ts);
                CREATE TABLE IF NOT EXISTS decisions (
                    ts INTEGER PRIMARY KEY, kind TEXT, reason TEXT, detail TEXT
                );
                CREATE TABLE IF NOT EXISTS errors (
                    ts INTEGER PRIMARY KEY, code TEXT, detail TEXT
                );
                """
            )


def db_insert_snapshot(ts: int, series: str, value: float) -> None:
    try:
        with DB_LOCK, db_connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO snapshots(ts, series, value) VALUES (?, ?, ?)",
                (ts, series, value),
            )
    except Exception as exc:
        log.debug("snapshot insert failed for %s: %s", series, exc)


def db_query_series(series: str, hours: int) -> list[tuple[int, float]]:
    since = int(time.time()) - hours * 3600
    with DB_LOCK, db_connect() as conn:
        rows = conn.execute(
            "SELECT ts, value FROM snapshots WHERE series=? AND ts>=? ORDER BY ts",
            (series, since),
        ).fetchall()
    return rows


def db_log_decision(kind: str, reason: str, detail: dict) -> None:
    ts = int(time.time())
    try:
        with DB_LOCK, db_connect() as conn:
            conn.execute(
                "INSERT INTO decisions(ts, kind, reason, detail) VALUES (?, ?, ?, ?)",
                (ts, kind, reason, json.dumps(detail)),
            )
    except Exception:
        pass
    record = {"ts": ts, "kind": kind, "reason": reason, "detail": detail}
    LAST_DECISIONS[ts] = record
    while len(LAST_DECISIONS) > 200:
        LAST_DECISIONS.popitem(last=False)
    try:
        with DECISIONS_PATH.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


# ───────────────────────────────────────────────────────────────────────────
# ebusd subprocess — bundled daemon, supervised
# ───────────────────────────────────────────────────────────────────────────

EBUSD_BIN = "/usr/bin/ebusd"
EBUSD_CONFIG_PATH = "/usr/share/ebusd/vaillant"
EBUSD_PROCESS: subprocess.Popen | None = None
EBUSD_LAST_RESTART = 0.0
EBUSD_RESTART_COUNT = 0


def _ebusd_argv() -> list[str]:
    """Build the ebusd command line."""
    return [
        EBUSD_BIN,
        "--foreground",
        "--device", CONF["ebus_device"],
        "--configpath", EBUSD_CONFIG_PATH,
        "--scanconfig",
        "--accesslevel", "*",
        "--mqtthost", CONF["mqtt_host"],
        "--mqttport", str(CONF["mqtt_port"]),
        "--mqttuser", CONF["mqtt_user"],
        "--mqttpass", CONF["mqtt_pass"],
        "--mqtttopic", CONF["topic_prefix"],
        "--mqttjson",
        "--mqttretain",
        "--log", f"all:{CONF['ebusd_log_level']}",
    ]


def _ebusd_pump_logs() -> None:
    """Background reader: forward ebusd stdout/stderr lines into our logger."""
    proc = EBUSD_PROCESS
    if not proc or not proc.stdout:
        return
    for line in iter(proc.stdout.readline, b""):
        try:
            text = line.decode("utf-8", errors="replace").rstrip()
        except Exception:
            continue
        if not text:
            continue
        # Tag every ebusd line so logs are easy to grep.
        log.info("[ebusd] %s", text)


def ebusd_start() -> None:
    """Spawn ebusd as a child process. Idempotent."""
    global EBUSD_PROCESS
    if EBUSD_PROCESS and EBUSD_PROCESS.poll() is None:
        return
    argv = _ebusd_argv()
    # Don't leak password into the log line, but keep enough to debug.
    safe = [a if "pass" not in argv[i - 1].lower() else "***" for i, a in enumerate(argv)]
    log.info("Spawning ebusd: %s", " ".join(safe))
    try:
        EBUSD_PROCESS = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            close_fds=True,
        )
    except FileNotFoundError:
        log.error("ebusd binary not found at %s — bad image build", EBUSD_BIN)
        return
    except Exception as exc:
        log.error("ebusd failed to spawn: %s", exc)
        return
    threading.Thread(target=_ebusd_pump_logs, name="ebusd-logs", daemon=True).start()


def ebusd_watchdog() -> None:
    """Re-spawn ebusd if it dies. Capped restart rate (max 1 every 5 s)."""
    global EBUSD_PROCESS, EBUSD_LAST_RESTART, EBUSD_RESTART_COUNT
    proc = EBUSD_PROCESS
    if proc is None:
        ebusd_start()
        return
    rc = proc.poll()
    if rc is None:
        return  # still running
    now = time.time()
    if now - EBUSD_LAST_RESTART < 5:
        return
    EBUSD_LAST_RESTART = now
    EBUSD_RESTART_COUNT += 1
    log.warning("ebusd exited with rc=%s — restart #%d", rc, EBUSD_RESTART_COUNT)
    EBUSD_PROCESS = None
    ebusd_start()


def ebusd_stop() -> None:
    global EBUSD_PROCESS
    if EBUSD_PROCESS and EBUSD_PROCESS.poll() is None:
        log.info("Stopping ebusd (SIGTERM)")
        try:
            EBUSD_PROCESS.terminate()
            EBUSD_PROCESS.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("ebusd did not exit on SIGTERM, sending SIGKILL")
            EBUSD_PROCESS.kill()
        EBUSD_PROCESS = None


def _install_signal_handlers() -> None:
    """Cleanly tear down ebusd when the container is stopped."""
    def _shutdown(signum, _frame):
        log.info("Received signal %s — shutting down", signum)
        ebusd_stop()
        SCHEDULER.shutdown(wait=False)
        sys.exit(0)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _shutdown)


# ───────────────────────────────────────────────────────────────────────────
# MQTT — subscribe to ebusd, drive STATE, write to /set, /get on demand
# ───────────────────────────────────────────────────────────────────────────

MQTT_CLIENT: mqtt.Client | None = None
MQTT_CONNECTED = threading.Event()


def _decode_payload(payload: bytes | str) -> tuple[dict, str]:
    raw = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"value": raw.strip('"')}, raw
    fields: dict = {}
    if isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, dict) and "value" in val:
                fields[key] = val["value"]
            else:
                fields[key] = val
    return fields, raw


def _on_mqtt_connect(client, _userdata, _flags, rc):
    if rc == 0:
        topic = f"{CONF['topic_prefix']}/+/+"
        client.subscribe(topic, qos=0)
        log.info("MQTT connected — subscribed to %s", topic)
        MQTT_CONNECTED.set()
    else:
        log.error("MQTT connect failed: rc=%s", rc)


def _on_mqtt_disconnect(_client, _userdata, rc):
    MQTT_CONNECTED.clear()
    log.warning("MQTT disconnected (rc=%s) — paho will reconnect", rc)


def _on_mqtt_message(_client, _userdata, msg):
    parts = msg.topic.split("/", 2)
    if len(parts) != 3 or parts[0] != CONF["topic_prefix"]:
        return
    _, circuit, message = parts
    if "/" in message or message in ("set", "get", "errors", "ebusd"):
        return
    fields, raw = _decode_payload(msg.payload)
    with STATE_LOCK:
        STATE[(circuit, message)] = {"fields": fields, "raw": raw, "ts": time.time()}


def mqtt_publish_write(circuit: str, msg: str, value) -> None:
    topic = f"{CONF['topic_prefix']}/{circuit}/{msg}/set"
    payload = str(value)
    log.info("MQTT write %s = %s", topic, payload)
    if MQTT_CLIENT:
        MQTT_CLIENT.publish(topic, payload, qos=0, retain=False)


def mqtt_request_read(circuit: str, msg: str) -> None:
    topic = f"{CONF['topic_prefix']}/{circuit}/{msg}/get"
    if MQTT_CLIENT:
        MQTT_CLIENT.publish(topic, "?", qos=0, retain=False)


def mqtt_start() -> None:
    global MQTT_CLIENT
    client = mqtt.Client(client_id=f"genia_air_addon_{int(time.time())}")
    if CONF["mqtt_user"]:
        client.username_pw_set(CONF["mqtt_user"], CONF["mqtt_pass"])
    client.on_connect = _on_mqtt_connect
    client.on_disconnect = _on_mqtt_disconnect
    client.on_message = _on_mqtt_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    MQTT_CLIENT = client
    try:
        client.connect_async(CONF["mqtt_host"], CONF["mqtt_port"], keepalive=60)
    except Exception as exc:
        log.error("MQTT initial connect_async failed: %s", exc)
    client.loop_start()


# ───────────────────────────────────────────────────────────────────────────
# Helpers — derived values from STATE
# ───────────────────────────────────────────────────────────────────────────

def _field(circuit: str, msg: str, key: str, cast=float):
    with STATE_LOCK:
        entry = STATE.get((circuit, msg))
    if not entry:
        return None
    val = entry["fields"].get(key)
    if val is None:
        return None
    try:
        return cast(val)
    except (TypeError, ValueError):
        return None


def compute_delta_t() -> float | None:
    supply = _field("hmu", "Status01", "0")
    ret    = _field("hmu", "Status01", "1")
    if supply is None or ret is None:
        return None
    return round(supply - ret, 2)


def compute_cop() -> float | None:
    p_in  = _field("hmu", "CurrentConsumedPower", "0")
    p_out = _field("hmu", "CurrentYieldPower", "0")
    if p_in is None or p_out is None or p_in <= 0.05:
        return None
    return round(p_out / p_in, 2)


_STATE_TO_HVAC = {
    "0":   "idle",
    "1":   "idle",
    "9":   "heating",
    "17":  "cooling",
    "129": "heating",
    "11":  "off",
}


def compute_hvac_action() -> str:
    entry = None
    with STATE_LOCK:
        entry = STATE.get(("hmu", "State"))
    if not entry:
        return "unknown"
    raw = str(entry["fields"].get("3", ""))
    return _STATE_TO_HVAC.get(raw, "unknown")


def compute_hvac_mode() -> str:
    sys_off = _field("ctls2", "GlobalSystemOff", "yesno", cast=str)
    if sys_off and sys_off.lower() == "yes":
        return "off"
    heat = _field("ctls2", "z1OpMode", "opmode", cast=str) or "off"
    cool = _field("ctls2", "z1OpModeCooling", "opmode", cast=str) or "off"
    h_on = heat.lower() in ("auto", "day", "night")
    c_on = cool.lower() in ("auto", "day", "night")
    if h_on and c_on:
        return "auto"
    if c_on:
        return "cool"
    if h_on:
        return "heat"
    return "off"


def collect_snapshot() -> dict:
    return {
        "ts": time.time(),
        "version": VERSION,
        "mqtt_connected": MQTT_CONNECTED.is_set(),
        "optimizer_enabled": OPTIMIZER_ENABLED,
        "hvac_mode": compute_hvac_mode(),
        "hvac_action": compute_hvac_action(),
        "room_temp": _field("ctls2", "z1RoomTemp", "tempv"),
        "setpoint_actual": _field("ctls2", "z1ActualRoomTempDesired", "tempv"),
        "setpoint_manual": _field("ctls2", "z1ManualTemp", "tempv"),
        "setpoint_cooling": _field("ctls2", "z1CoolingTemp", "tempv"),
        "outside_temp": _field("ctls2", "OutsideTempAvg", "tempv"),
        "delta_t": compute_delta_t(),
        "cop": compute_cop(),
        "supply_temp": _field("hmu", "Status01", "0"),
        "return_temp": _field("hmu", "Status01", "1"),
        "power_in": _field("hmu", "CurrentConsumedPower", "0"),
        "power_out": _field("hmu", "CurrentYieldPower", "0"),
        "compressor_modulation": _field("hmu", "CurrentCompressorUtil", "0"),
        "water_throughput": _field("hmu", "WaterThroughput", "0"),
        "hours_total": _field("hmu", "Hours", "0"),
        "hours_heating": _field("hmu", "HoursHc", "0"),
        "hours_cooling": _field("hmu", "HoursCool", "0"),
        "yield_total": _field("ctls2", "YieldTotal", "energy4"),
        "max_flow_temp": _field("ctls2", "Hc1MaxFlowTempDesired", "tempv"),
        "min_flow_temp": _field("ctls2", "Hc1MinFlowTempDesired", "tempv"),
        "summer_temp_limit": _field("ctls2", "Hc1SummerTempLimit", "tempv"),
        "continuous_heating": _field("ctls2", "ContinuosHeating", "tempv"),
        "maintenance_due": _field("ctls2", "MaintenanceDue", "yesno", cast=str),
        "opmode_heating": _field("ctls2", "z1OpMode", "opmode", cast=str),
        "opmode_cooling": _field("ctls2", "z1OpModeCooling", "opmode", cast=str),
        "sfmode": _field("ctls2", "z1SfMode", "sfmode", cast=str),
        "health": dict(HEALTH),
    }


# ───────────────────────────────────────────────────────────────────────────
# Scheduler — initial sync, snapshot, optimizer, health
# ───────────────────────────────────────────────────────────────────────────

SCHEDULER = BackgroundScheduler(timezone="UTC")


def task_initial_sync() -> None:
    """Force ebusd to read every msg we care about, paced so we don't queue-jam."""
    if not MQTT_CONNECTED.wait(timeout=10):
        log.warning("Initial sync: MQTT not ready, skip")
        return
    with STATE_LOCK:
        pending = [t for t in SUBSCRIBED_MSGS if t not in STATE]
    if not pending:
        log.info("Initial sync: STATE already populated (%d msgs)", len(SUBSCRIBED_MSGS))
        return
    log.info("Initial sync: requesting %d/%d msgs", len(pending), len(SUBSCRIBED_MSGS))
    for circuit, msg in pending:
        mqtt_request_read(circuit, msg)
        time.sleep(0.15)


def task_snapshot_history() -> None:
    """Persist a row per series with the current value."""
    snap = collect_snapshot()
    ts = int(snap["ts"])
    for series in (
        "room_temp", "setpoint_actual", "outside_temp",
        "supply_temp", "return_temp", "delta_t", "cop",
        "power_in", "power_out", "compressor_modulation",
        "water_throughput", "max_flow_temp",
    ):
        v = snap.get(series)
        if v is not None:
            db_insert_snapshot(ts, series, float(v))


def task_optimize() -> None:
    """The actual control loop.

    Strategy v0.1 (deterministic only):
      * Weather-compensated max flow temp: derive a target from outdoor,
        clamp to [min_flow_temp_safe, max_flow_temp_safe], write only if
        the delta vs current is > 0.5 K (avoid chatter).
      * Summer/winter switchover.
      * Safety enforcement on min/max flow if the user manually set
        something unsafe.
      * Delta-T anomaly alerting (no actuation — see docs/PUMP-PWM.md).
    """
    if not OPTIMIZER_ENABLED:
        return
    snap = collect_snapshot()
    actions: list[dict] = []

    # --- safety enforcement on flow temps ---
    if snap["max_flow_temp"] is not None and snap["max_flow_temp"] > CONF["max_flow_temp_safe"] + 0.1:
        actions.append(_force_write_safe(
            "ctls2", "Hc1MaxFlowTempDesired",
            CONF["max_flow_temp_safe"],
            f"max_flow_temp {snap['max_flow_temp']} > safe limit {CONF['max_flow_temp_safe']}",
        ))
    if snap["min_flow_temp"] is not None and snap["min_flow_temp"] < CONF["min_flow_temp_safe"] - 0.1:
        actions.append(_force_write_safe(
            "ctls2", "Hc1MinFlowTempDesired",
            CONF["min_flow_temp_safe"],
            f"min_flow_temp {snap['min_flow_temp']} < safe limit {CONF['min_flow_temp_safe']}",
        ))

    # --- weather-compensated flow temp target ---
    out = snap["outside_temp"]
    if out is not None and snap["hvac_mode"] == "heat":
        # Simple linear curve: at -10°C → 35; at +15°C → 25. Clamped.
        target = 30.0 - (out + 10) * (10.0 / 25.0)
        target = max(CONF["min_flow_temp_safe"] + 4, min(CONF["max_flow_temp_safe"], round(target, 1)))
        current = snap["max_flow_temp"]
        if current is not None and abs(target - current) >= 0.5:
            mqtt_publish_write("ctls2", "Hc1MaxFlowTempDesired", target)
            db_log_decision(
                "flow_curve",
                f"max_flow_temp {current}→{target} based on outdoor {out:.1f}°C",
                {"target": target, "outside": out, "previous": current},
            )
            actions.append({"kind": "flow_curve", "to": target})

    # --- delta-T anomaly alert (no actuation) ---
    dt = snap["delta_t"]
    if dt is not None and snap["hvac_action"] == "heating":
        if abs(dt - CONF["target_delta_t"]) > 0.8:
            HEALTH["ok"] = False
            reason = f"ΔT={dt} K off-target ({CONF['target_delta_t']} K ±0.8)"
            if reason not in HEALTH["reasons"]:
                HEALTH["reasons"].append(reason)
            db_log_decision("alert", reason, {"delta_t": dt, "target": CONF["target_delta_t"]})

    # --- summer/winter switchover ---
    if out is not None:
        if out > CONF["summer_temp_limit"] + 2 and snap["hvac_mode"] == "heat":
            actions.append({"kind": "season_switch", "to": "cool"})
            db_log_decision(
                "season_switch",
                f"outdoor {out:.1f}°C > summer limit + 2 → switch to cooling",
                {"outside": out},
            )
            mqtt_publish_write("ctls2", "z1OpMode", "off")
            mqtt_publish_write("ctls2", "z1OpModeCooling", "auto")
        elif out < CONF["summer_temp_limit"] - 5 and snap["hvac_mode"] == "cool":
            actions.append({"kind": "season_switch", "to": "heat"})
            db_log_decision(
                "season_switch",
                f"outdoor {out:.1f}°C < summer limit - 5 → switch to heating",
                {"outside": out},
            )
            mqtt_publish_write("ctls2", "z1OpModeCooling", "off")
            mqtt_publish_write("ctls2", "z1OpMode", "auto")

    log.info("optimize cycle: %d actions", len(actions))


def _force_write_safe(circuit: str, msg: str, value, reason: str) -> dict:
    mqtt_publish_write(circuit, msg, value)
    db_log_decision("safety", reason, {"circuit": circuit, "msg": msg, "value": value})
    return {"kind": "safety", "msg": msg, "to": value}


def task_health_check() -> None:
    snap = collect_snapshot()
    ok = True
    reasons: list[str] = []
    if EBUSD_PROCESS is None or EBUSD_PROCESS.poll() is not None:
        ok = False
        reasons.append("ebusd not running")
    if not snap["mqtt_connected"]:
        ok = False
        reasons.append("MQTT disconnected")
    if not STATE:
        ok = False
        reasons.append("No ebusd traffic received")
    if snap["maintenance_due"] and snap["maintenance_due"].lower() == "yes":
        ok = False
        reasons.append("Maintenance due")
    HEALTH["ok"] = ok
    HEALTH["reasons"] = reasons
    if not ok and snap["mqtt_connected"]:
        log.warning("Health: %s", reasons)


# ───────────────────────────────────────────────────────────────────────────
# MQTT Discovery — publish a small device into HA so automations can hook
# ───────────────────────────────────────────────────────────────────────────

DISCOVERY_SENT = False


def publish_ha_discovery() -> None:
    """Publish 6 entities into HA via discovery (one-shot, retained)."""
    global DISCOVERY_SENT
    if not MQTT_CLIENT or DISCOVERY_SENT:
        return
    device = {
        "identifiers": ["genia_air_addon"],
        "name": "Genia Air (addon)",
        "manufacturer": "Vaillant",
        "model": "Genia Air",
        "sw_version": VERSION,
    }
    base = "homeassistant"
    avail = "genia_air/addon/availability"
    entities = [
        {
            "kind": "sensor", "id": "state",
            "config": {"name": "State", "state_topic": "genia_air/addon/state",
                       "icon": "mdi:state-machine"},
        },
        {
            "kind": "sensor", "id": "cop",
            "config": {"name": "COP", "state_topic": "genia_air/addon/cop",
                       "icon": "mdi:gauge"},
        },
        {
            "kind": "sensor", "id": "delta_t",
            "config": {"name": "ΔT", "state_topic": "genia_air/addon/delta_t",
                       "unit_of_measurement": "K", "icon": "mdi:delta"},
        },
        {
            "kind": "binary_sensor", "id": "fault",
            "config": {"name": "Fault", "state_topic": "genia_air/addon/fault",
                       "device_class": "problem"},
        },
        {
            "kind": "switch", "id": "optimizer",
            "config": {"name": "Optimizer",
                       "state_topic": "genia_air/addon/optimizer/state",
                       "command_topic": "genia_air/addon/optimizer/set",
                       "payload_on": "ON", "payload_off": "OFF",
                       "icon": "mdi:brain"},
        },
    ]
    for ent in entities:
        kind, eid, conf = ent["kind"], ent["id"], dict(ent["config"])
        conf.update({
            "unique_id": f"genia_air_addon_{eid}",
            "availability_topic": avail,
            "device": device,
        })
        topic = f"{base}/{kind}/genia_air_addon/{eid}/config"
        MQTT_CLIENT.publish(topic, json.dumps(conf), qos=0, retain=True)
    MQTT_CLIENT.publish(avail, "online", retain=True)
    MQTT_CLIENT.publish("genia_air/addon/optimizer/state",
                        "ON" if OPTIMIZER_ENABLED else "OFF", retain=True)
    DISCOVERY_SENT = True
    log.info("MQTT Discovery published for HA (5 entities)")


def task_publish_states() -> None:
    """Push the derived values to the MQTT discovery topics."""
    if not MQTT_CLIENT or not MQTT_CONNECTED.is_set():
        return
    publish_ha_discovery()
    snap = collect_snapshot()
    MQTT_CLIENT.publish("genia_air/addon/state", snap["hvac_action"], retain=True)
    if snap["cop"] is not None:
        MQTT_CLIENT.publish("genia_air/addon/cop", snap["cop"], retain=True)
    if snap["delta_t"] is not None:
        MQTT_CLIENT.publish("genia_air/addon/delta_t", snap["delta_t"], retain=True)
    MQTT_CLIENT.publish("genia_air/addon/fault",
                        "ON" if not HEALTH["ok"] else "OFF", retain=True)
    MQTT_CLIENT.publish("genia_air/addon/optimizer/state",
                        "ON" if OPTIMIZER_ENABLED else "OFF", retain=True)


# Optimizer toggle handler via MQTT command topic
def _on_optimizer_command(_client, _userdata, msg):
    global OPTIMIZER_ENABLED
    val = msg.payload.decode("utf-8", errors="replace").strip().upper()
    OPTIMIZER_ENABLED = val == "ON"
    log.info("Optimizer toggled via HA: %s", OPTIMIZER_ENABLED)


def mqtt_post_connect_subs() -> None:
    """Subscribe to discovery command topics after connect."""
    if MQTT_CLIENT and MQTT_CONNECTED.is_set():
        MQTT_CLIENT.message_callback_add(
            "genia_air/addon/optimizer/set", _on_optimizer_command
        )
        MQTT_CLIENT.subscribe("genia_air/addon/optimizer/set", qos=0)


# ───────────────────────────────────────────────────────────────────────────
# Flask app — JSON API + embedded PANEL HTML
# ───────────────────────────────────────────────────────────────────────────

app = Flask(__name__)


@app.before_request
def _ingress_guard():
    """All routes require X-Ingress-Path; writes additionally require X-Hass-User."""
    if request.path.startswith("/api/") or request.path == "/":
        if not request.headers.get("X-Ingress-Path"):
            # Allow loopback for healthchecks
            if request.remote_addr not in ("127.0.0.1", "::1"):
                abort(403)
    if request.method in ("POST", "PUT", "DELETE"):
        if not request.headers.get("X-Hass-User") and request.remote_addr not in ("127.0.0.1", "::1"):
            abort(403)


def _base_path() -> str:
    return request.headers.get("X-Ingress-Path", "").rstrip("/")


@app.route("/")
def index():
    return PANEL.replace("__BASE__", _base_path())


@app.route("/api/state")
def api_state():
    return jsonify(collect_snapshot())


@app.route("/api/messages")
def api_messages():
    with STATE_LOCK:
        out = []
        for (circuit, msg), entry in sorted(STATE.items()):
            out.append({
                "circuit": circuit, "msg": msg,
                "fields": entry["fields"],
                "last_seen": entry["ts"],
                "age_seconds": int(time.time() - entry["ts"]),
            })
    return jsonify(out)


@app.route("/api/history")
def api_history():
    series = request.args.get("series", "")
    hours = int(request.args.get("hours", "24"))
    if not series:
        abort(400)
    rows = db_query_series(series, hours)
    return jsonify([{"ts": ts, "value": v} for ts, v in rows])


@app.route("/api/decisions")
def api_decisions():
    limit = int(request.args.get("limit", "50"))
    return jsonify(list(LAST_DECISIONS.values())[-limit:])


@app.route("/api/health")
def api_health():
    ebusd_alive = EBUSD_PROCESS is not None and EBUSD_PROCESS.poll() is None
    return jsonify({
        "ok": HEALTH["ok"],
        "reasons": HEALTH["reasons"],
        "since": HEALTH["since"],
        "mqtt_connected": MQTT_CONNECTED.is_set(),
        "ebusd_running": ebusd_alive,
        "ebusd_pid": EBUSD_PROCESS.pid if ebusd_alive else None,
        "ebusd_restarts": EBUSD_RESTART_COUNT,
        "state_size": len(STATE),
        "version": VERSION,
        "uptime_seconds": int(time.time() - HEALTH["since"]),
    })


@app.route("/api/ebusd", methods=["POST"])
def api_ebusd_action():
    """Manual control: {action: 'restart'|'stop'|'start'}."""
    action = (request.get_json(force=True, silent=True) or {}).get("action", "")
    if action == "restart":
        ebusd_stop()
        time.sleep(1)
        ebusd_start()
    elif action == "stop":
        ebusd_stop()
    elif action == "start":
        ebusd_start()
    else:
        abort(400)
    db_log_decision("user_ebusd", f"ebusd {action}", {"action": action})
    return jsonify({"ok": True, "action": action,
                    "running": EBUSD_PROCESS is not None and EBUSD_PROCESS.poll() is None})


@app.route("/api/write", methods=["POST"])
def api_write():
    body = request.get_json(force=True, silent=True) or {}
    circuit = body.get("circuit")
    msg = body.get("msg")
    value = body.get("value")
    if not circuit or not msg or value is None:
        abort(400)
    # Safety clamp on known flow-temp keys
    if msg == "Hc1MaxFlowTempDesired":
        value = max(CONF["min_flow_temp_safe"] + 4, min(CONF["max_flow_temp_safe"], float(value)))
    if msg == "Hc1MinFlowTempDesired":
        value = max(CONF["min_flow_temp_safe"], min(CONF["max_flow_temp_safe"] - 4, float(value)))
    mqtt_publish_write(circuit, msg, value)
    user = request.headers.get("X-Hass-User", "unknown")
    db_log_decision("user_write", f"{circuit}/{msg}={value} by user {user[:8]}",
                    {"circuit": circuit, "msg": msg, "value": value, "user": user})
    return jsonify({"ok": True, "circuit": circuit, "msg": msg, "value": value})


@app.route("/api/mode", methods=["POST"])
def api_mode():
    mode = (request.get_json(force=True, silent=True) or {}).get("mode", "")
    if mode not in ("off", "heat", "cool", "auto"):
        abort(400)
    z = 1
    pairs = {
        "off":  [("z1OpMode", "off"),  ("z1OpModeCooling", "off")],
        "heat": [("z1OpMode", "auto"), ("z1OpModeCooling", "off")],
        "cool": [("z1OpMode", "off"),  ("z1OpModeCooling", "auto")],
        "auto": [("z1OpMode", "auto"), ("z1OpModeCooling", "auto")],
    }[mode]
    for msg, val in pairs:
        mqtt_publish_write("ctls2", msg, val)
    db_log_decision("user_mode", f"HVAC mode → {mode}", {"mode": mode})
    return jsonify({"ok": True, "mode": mode})


@app.route("/api/setpoint", methods=["POST"])
def api_setpoint():
    body = request.get_json(force=True, silent=True) or {}
    target = float(body.get("target_c", 0))
    if not (5 <= target <= 30):
        abort(400)
    hvac = compute_hvac_mode()
    msg = "z1CoolingTemp" if hvac == "cool" else "z1ManualTemp"
    mqtt_publish_write("ctls2", msg, target)
    db_log_decision("user_setpoint", f"{msg} = {target}°C", {"msg": msg, "value": target})
    return jsonify({"ok": True, "msg": msg, "value": target})


@app.route("/api/optimizer", methods=["POST"])
def api_optimizer():
    global OPTIMIZER_ENABLED
    body = request.get_json(force=True, silent=True) or {}
    OPTIMIZER_ENABLED = bool(body.get("enable", True))
    db_log_decision("user_optimizer", f"Optimizer = {OPTIMIZER_ENABLED}",
                    {"enabled": OPTIMIZER_ENABLED})
    return jsonify({"ok": True, "enabled": OPTIMIZER_ENABLED})


@app.route("/api/force_read", methods=["POST"])
def api_force_read():
    """Trigger initial_sync on demand."""
    threading.Thread(target=task_initial_sync, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/config")
def api_config():
    out = dict(CONF)
    out.pop("mqtt_pass", None)
    return jsonify(out)


# ───────────────────────────────────────────────────────────────────────────
# Embedded UI — single HTML string (PANEL) served from /
# ───────────────────────────────────────────────────────────────────────────

PANEL = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Genia Air</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<style>
:root{--bg:#0f172a;--s:#1e293b;--b:#334155;--a:#38bdf8;--g:#4ade80;--y:#fbbf24;--r:#f87171;--o:#fb923c;--t:#e2e8f0;--m:#94a3b8;--p:#a78bfa}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--t);font-family:system-ui,sans-serif;padding:1rem;font-size:14px}
h1{color:var(--a);font-size:1.3rem;margin-bottom:.8rem;display:flex;align-items:center;gap:.5rem}
h2{font-size:.7rem;color:var(--m);text-transform:uppercase;letter-spacing:.08em;margin:.8rem 0 .5rem}
.tabs{display:flex;gap:.25rem;margin-bottom:1rem;border-bottom:1px solid var(--b);padding-bottom:.5rem;flex-wrap:wrap}
.tab{background:transparent;border:none;color:var(--m);padding:.4rem .9rem;border-radius:.4rem;cursor:pointer;font-size:.8rem;font-weight:600;transition:.15s}
.tab:hover{color:var(--t);background:rgba(255,255,255,.05)}
.tab.active{color:var(--a);background:rgba(56,189,248,.1)}
.tab-content{display:none}
.tab-content.active{display:block}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.6rem;margin-bottom:1rem}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:.8rem;margin-bottom:1rem}
@media(max-width:700px){.grid2{grid-template-columns:1fr}}
.card{background:var(--s);border-radius:.75rem;padding:.9rem;border:1px solid var(--b)}
.chart-card{display:flex;flex-direction:column}
.chart-wrap{position:relative;height:240px;width:100%}
.metric{font-size:1.8rem;font-weight:700;color:var(--a);line-height:1}
.metric.g{color:var(--g)}.metric.y{color:var(--y)}.metric.r{color:var(--r)}.metric.o{color:var(--o)}.metric.p{color:var(--p)}
.label{font-size:.72rem;color:var(--m);margin-top:.3rem}
.sub{font-size:.78rem;color:var(--m);margin-top:.15rem}
.badge{display:inline-block;padding:.15rem .5rem;border-radius:.3rem;font-size:.72rem;font-weight:600}
.bg-r{color:var(--r);background:rgba(248,113,113,.12)}
.bg-g{color:var(--g);background:rgba(74,222,128,.12)}
.bg-y{color:var(--y);background:rgba(251,191,36,.12)}
.bg-a{color:var(--a);background:rgba(56,189,248,.12)}
.bg-m{color:var(--m);background:rgba(148,163,184,.12)}
.btn{background:var(--a);color:#0f172a;border:none;padding:.45rem 1rem;border-radius:.5rem;font-weight:700;cursor:pointer;font-size:.8rem;transition:.15s opacity}
.btn:hover{opacity:.85}.btn:disabled{opacity:.4;cursor:default}
.btn-y{background:var(--y)}.btn-g{background:var(--g)}.btn-r{background:var(--r)}.btn-p{background:var(--p)}.btn-sm{padding:.3rem .7rem;font-size:.72rem}
.actions{display:flex;gap:.5rem;margin-bottom:1rem;flex-wrap:wrap;align-items:center}
.thermo{background:var(--s);border-radius:.75rem;padding:1.1rem;border:1px solid var(--b);margin-bottom:1rem}
.thermo-row{display:flex;gap:1.5rem;align-items:center;flex-wrap:wrap;margin-bottom:.8rem}
.thermo-temp{font-size:3rem;font-weight:700;color:var(--a);line-height:1}
.thermo-meta{display:flex;flex-direction:column;gap:.3rem;flex:1;min-width:160px}
.thermo-line{font-size:.78rem;color:var(--m)}
.thermo-line span{color:var(--t);font-weight:600}
.mode-pill{display:inline-flex;align-items:center;gap:.3rem;padding:.3rem .65rem;border-radius:999px;font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em}
.action-pill{display:inline-block;padding:.15rem .55rem;border-radius:.3rem;font-size:.7rem;font-weight:700;text-transform:uppercase}
.setp-row{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap}
.setp-row input[type=number]{background:var(--b);border:1px solid #475569;border-radius:.4rem;padding:.4rem .55rem;color:var(--t);width:80px;font-size:.85rem;font-weight:600}
.range-row{padding:.55rem 0;border-bottom:1px solid rgba(51,65,85,.5)}
.range-row:last-child{border-bottom:none}
.range-h{display:flex;justify-content:space-between;margin-bottom:.3rem}
.range-name{font-size:.82rem;color:var(--t)}
.range-val{font-size:.82rem;color:var(--a);font-weight:700}
input[type=range]{width:100%;accent-color:var(--a);cursor:pointer}
select{background:var(--b);border:1px solid #475569;color:var(--t);border-radius:.4rem;padding:.4rem .55rem;font-size:.82rem;font-weight:600}
.toast{position:fixed;bottom:1.2rem;right:1.2rem;padding:.6rem 1.2rem;border-radius:.5rem;font-weight:600;font-size:.85rem;opacity:0;transition:.25s opacity;pointer-events:none;z-index:999;max-width:340px}
.toast.show{opacity:1}
.toast.ok{background:rgba(74,222,128,.95);color:#0f172a}
.toast.err{background:rgba(248,113,113,.95);color:#0f172a}
.toast.info{background:rgba(56,189,248,.95);color:#0f172a}
table{width:100%;border-collapse:collapse;font-size:.78rem}
td,th{padding:.4rem .5rem;border-bottom:1px solid var(--b);text-align:left}
th{color:var(--m);font-weight:500}
.diag-old{color:var(--y)}.diag-stale{color:var(--r)}.diag-fresh{color:var(--g)}
.dec-time{color:var(--m);font-size:.7rem;white-space:nowrap}
.health-card{padding:.8rem 1rem;border-radius:.6rem;margin-bottom:1rem;font-size:.85rem;border:1px solid}
.health-card.ok{background:rgba(74,222,128,.07);border-color:rgba(74,222,128,.3);color:var(--g)}
.health-card.warn{background:rgba(248,113,113,.07);border-color:rgba(248,113,113,.3);color:var(--r)}
.toggle{position:relative;display:inline-block;width:42px;height:22px;vertical-align:middle}
.toggle input{opacity:0;width:0;height:0}
.tslide{position:absolute;cursor:pointer;inset:0;background:var(--b);border-radius:22px;transition:.2s}
.tslide:before{position:absolute;content:"";height:16px;width:16px;left:3px;bottom:3px;background:var(--m);border-radius:50%;transition:.2s}
input:checked+.tslide{background:var(--a)}
input:checked+.tslide:before{transform:translateX(20px);background:#0f172a}
.empty{text-align:center;padding:2rem 1rem;color:var(--m);font-size:.85rem}
</style>
</head>
<body>
<h1>🌡️ Genia Air <span id="version" style="font-size:.7rem;color:var(--m);font-weight:400">v...</span></h1>
<div id="toast" class="toast"></div>
<div class="tabs">
  <button class="tab active" data-tab="overview">📊 Overview</button>
  <button class="tab" data-tab="charts">📈 Charts</button>
  <button class="tab" data-tab="controls">🎛️ Controls</button>
  <button class="tab" data-tab="optimizer">🧠 Optimizer</button>
  <button class="tab" data-tab="diag">🔧 Diagnostics</button>
</div>

<!-- Overview -->
<div id="t-overview" class="tab-content active">
  <div id="health-banner"></div>
  <div class="thermo">
    <h2 style="margin-top:0">Zone 1 thermostat</h2>
    <div class="thermo-row">
      <div>
        <div class="thermo-temp" id="room-temp">--</div>
        <div class="label">Room temperature</div>
      </div>
      <div class="thermo-meta">
        <div class="thermo-line">Current setpoint: <span id="setp-actual">--</span></div>
        <div class="thermo-line">Mode: <span id="hvac-mode" class="mode-pill bg-m">--</span> <span id="hvac-action" class="action-pill bg-m">--</span></div>
        <div class="thermo-line">Outdoor (averaged): <span id="outside">--</span></div>
      </div>
    </div>
    <div class="setp-row">
      <span class="label" style="margin:0">Change setpoint:</span>
      <input id="setp-input" type="number" step="0.5" min="12" max="28">
      <button class="btn btn-sm" onclick="setSetpoint()">Apply</button>
    </div>
  </div>
  <h2>System</h2>
  <div id="kpis" class="grid"></div>
  <h2>Optimizer</h2>
  <div id="opt-status" class="card" style="font-size:.85rem"></div>
</div>

<!-- Charts -->
<div id="t-charts" class="tab-content">
  <div class="actions">
    <span class="label" style="margin:0">Window:</span>
    <select id="chart-window">
      <option value="6">6 h</option>
      <option value="24" selected>24 h</option>
      <option value="72">72 h</option>
      <option value="168">7 days</option>
    </select>
    <button class="btn btn-sm" onclick="loadCharts()">🔄 Refresh</button>
  </div>
  <div class="grid2">
    <div class="card chart-card"><h2 style="margin-top:0">Temperatures (°C)</h2><div class="chart-wrap"><canvas id="ch-temps"></canvas></div></div>
    <div class="card chart-card"><h2 style="margin-top:0">ΔT supply − return (K)</h2><div class="chart-wrap"><canvas id="ch-dt"></canvas></div></div>
  </div>
  <div class="grid2">
    <div class="card chart-card"><h2 style="margin-top:0">Electric vs thermal power (kW)</h2><div class="chart-wrap"><canvas id="ch-pow"></canvas></div></div>
    <div class="card chart-card"><h2 style="margin-top:0">COP (instantaneous)</h2><div class="chart-wrap"><canvas id="ch-cop"></canvas></div></div>
  </div>
</div>

<!-- Controls -->
<div id="t-controls" class="tab-content">
  <div class="card">
    <h2 style="margin-top:0">HVAC mode</h2>
    <div class="actions">
      <button class="btn btn-sm" onclick="setMode('off')">⏻ Off</button>
      <button class="btn btn-sm btn-g" onclick="setMode('heat')">🔥 Heat</button>
      <button class="btn btn-sm btn-p" onclick="setMode('cool')">❄ Cool</button>
      <button class="btn btn-sm btn-y" onclick="setMode('auto')">🔄 Auto</button>
    </div>
  </div>
  <div class="card">
    <h2 style="margin-top:0">Zone setpoints</h2>
    <div id="setp-sliders"></div>
  </div>
  <div class="card">
    <h2 style="margin-top:0">Flow curve &amp; safety limits</h2>
    <div id="flow-sliders"></div>
  </div>
</div>

<!-- Optimizer -->
<div id="t-optimizer" class="tab-content">
  <div class="card" style="display:flex;align-items:center;gap:1rem;flex-wrap:wrap">
    <label class="toggle"><input type="checkbox" id="opt-toggle" onchange="toggleOptimizer()"><span class="tslide"></span></label>
    <div>
      <div style="font-weight:600">Active control</div>
      <div class="sub" style="margin-top:.2rem">Dynamic flow curve, seasonal switchover, safety enforcement</div>
    </div>
  </div>
  <h2>Recent decisions</h2>
  <div class="card" style="overflow-x:auto">
    <table><thead><tr><th>Time</th><th>Type</th><th>Reason</th></tr></thead><tbody id="dec-tbody"></tbody></table>
  </div>
</div>

<!-- Diagnostics -->
<div id="t-diag" class="tab-content">
  <div id="ebusd-card" class="card" style="margin-bottom:1rem;display:flex;align-items:center;gap:1rem;flex-wrap:wrap"></div>
  <div class="actions">
    <button class="btn btn-sm" onclick="forceRead()">📡 Force-read all</button>
    <button class="btn btn-sm btn-y" onclick="ebusdAction('restart')">🔁 Restart ebusd</button>
    <button class="btn btn-sm" onclick="loadDiag()">🔄 Refresh</button>
  </div>
  <div class="card" style="overflow-x:auto">
    <table><thead><tr><th>Circuit</th><th>Message</th><th>Fields</th><th>Age</th></tr></thead><tbody id="diag-tbody"></tbody></table>
  </div>
</div>

<script>
const BASE = "__BASE__";
const $ = id => document.getElementById(id);
const fmt = (v, suf="", d=1) => (v==null||isNaN(v)) ? "—" : (typeof v==="number" ? v.toFixed(d) : v)+suf;
const fmtAge = s => s<60?s+"s":s<3600?Math.floor(s/60)+"m":Math.floor(s/3600)+"h";
const fmtTime = ts => new Date(ts*1000).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"});

function toast(msg, kind="info"){
  const t=$("toast"); t.textContent=msg; t.className="toast "+kind+" show";
  setTimeout(()=>t.className="toast",2400);
}
async function api(path, opts={}){
  opts.headers=Object.assign({"Content-Type":"application/json"}, opts.headers||{});
  const r=await fetch(BASE+path, opts);
  if(!r.ok) throw new Error("HTTP "+r.status);
  return r.json();
}

// Tab switching
document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>{
  document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach(x=>x.classList.remove("active"));
  b.classList.add("active");
  $("t-"+b.dataset.tab).classList.add("active");
  if(b.dataset.tab==="charts") loadCharts();
  if(b.dataset.tab==="diag") loadDiag();
  if(b.dataset.tab==="controls") buildSliders();
  if(b.dataset.tab==="optimizer") loadDecisions();
});

// Overview
const MODE_CLASS = {heat:"bg-g",cool:"bg-a",auto:"bg-y",off:"bg-m",unknown:"bg-m"};
const ACTION_CLASS = {heating:"bg-g",cooling:"bg-a",idle:"bg-m",off:"bg-m",unknown:"bg-m"};

async function loadState(){
  try{
    const s = await api("/api/state");
    $("version").textContent = "v"+s.version;
    $("room-temp").textContent = fmt(s.room_temp, "°C");
    $("setp-actual").textContent = fmt(s.setpoint_actual, "°C");
    $("hvac-mode").textContent = s.hvac_mode.toUpperCase();
    $("hvac-mode").className = "mode-pill "+(MODE_CLASS[s.hvac_mode]||"bg-m");
    $("hvac-action").textContent = s.hvac_action.toUpperCase();
    $("hvac-action").className = "action-pill "+(ACTION_CLASS[s.hvac_action]||"bg-m");
    $("outside").textContent = fmt(s.outside_temp, "°C");
    if(!$("setp-input").dataset.touched) $("setp-input").value = s.setpoint_actual || s.setpoint_manual || "";
    const k = $("kpis"); k.innerHTML="";
    [
      ["ΔT", fmt(s.delta_t, " K"), s.delta_t==null?"":"y"],
      ["COP", fmt(s.cop, "", 2), s.cop>3?"g":s.cop>2?"y":"r"],
      ["Modulation", fmt(s.compressor_modulation, " %", 0), "p"],
      ["Electric power", fmt(s.power_in, " kW", 2), "y"],
      ["Thermal power", fmt(s.power_out, " kW", 2), "g"],
      ["Flow rate", fmt(s.water_throughput, " L/h", 0), "a"],
      ["Supply temp", fmt(s.supply_temp, "°C"), "o"],
      ["Return temp", fmt(s.return_temp, "°C"), "a"],
      ["Total hours", fmt(s.hours_total, " h", 0), ""],
    ].forEach(([lbl,val,kind])=>{
      const div=document.createElement("div"); div.className="card";
      div.innerHTML=`<div class="metric ${kind||""}">${val}</div><div class="label">${lbl}</div>`;
      k.appendChild(div);
    });
    const hb = $("health-banner");
    if(!s.health.ok && s.health.reasons.length){
      hb.className="health-card warn";
      hb.innerHTML="⚠ "+s.health.reasons.map(r=>`<div>${r}</div>`).join("");
    } else {
      hb.className="health-card ok";
      hb.innerHTML="✓ System healthy · MQTT "+(s.mqtt_connected?"connected":"disconnected");
    }
    $("opt-status").innerHTML =
      `<div>Active control: <span class="badge ${s.optimizer_enabled?'bg-g':'bg-m'}">${s.optimizer_enabled?'ON':'OFF'}</span></div>`+
      `<div class="sub" style="margin-top:.3rem">Dynamic flow-temperature curve, seasonal switchover and safety enforcement. See Optimizer tab for the decision log.</div>`;
    $("opt-toggle").checked = !!s.optimizer_enabled;
  } catch(e){ toast("State load failed: "+e.message, "err"); }
}

async function setSetpoint(){
  const v = parseFloat($("setp-input").value);
  if(isNaN(v)) return toast("Invalid setpoint","err");
  try{
    const r = await api("/api/setpoint", {method:"POST", body:JSON.stringify({target_c:v})});
    toast("Setpoint → "+r.value+"°C", "ok");
    $("setp-input").dataset.touched="";
    setTimeout(loadState, 800);
  } catch(e){ toast("Error: "+e.message,"err"); }
}
$("setp-input").addEventListener("input", e=>e.target.dataset.touched="1");

async function setMode(mode){
  try{ await api("/api/mode", {method:"POST", body:JSON.stringify({mode})});
       toast("Mode → "+mode.toUpperCase(),"ok"); setTimeout(loadState,800);
  } catch(e){ toast("Error: "+e.message,"err"); }
}

// Controls — dynamic sliders
const SETPOINT_DEFS = [
  ["z1ManualTemp",  "Manual",       12, 28, 0.5],
  ["z1DayTemp",     "Day",          14, 26, 0.5],
  ["z1NightTemp",   "Night",        16, 26, 0.5],
  ["z1HolidayTemp", "Holiday",       5, 22, 0.5],
  ["z1CoolingTemp", "Cooling",      16, 26, 0.5],
];
const FLOW_DEFS = [
  ["Hc1MaxFlowTempDesired", "Max flow temp",            25, 40, 0.5],
  ["Hc1MinFlowTempDesired", "Min flow temp",            14, 30, 0.5],
  ["Hc1SummerTempLimit",    "Summer temp limit",        12, 28, 0.5],
  ["ContinuosHeating",      "Continuous heating temp",  -26, 15, 0.5],
];
async function buildSliders(){
  const s = await api("/api/state");
  const fieldFor = {z1ManualTemp:"setpoint_manual", z1DayTemp:null, z1NightTemp:null,
                    z1HolidayTemp:null, z1CoolingTemp:"setpoint_cooling",
                    Hc1MaxFlowTempDesired:"max_flow_temp",
                    Hc1MinFlowTempDesired:"min_flow_temp",
                    Hc1SummerTempLimit:"summer_temp_limit",
                    ContinuosHeating:"continuous_heating"};
  function render(target, defs){
    target.innerHTML="";
    defs.forEach(([msg, name, lo, hi, step])=>{
      const cur = fieldFor[msg] ? s[fieldFor[msg]] : null;
      const val = cur==null ? (lo+hi)/2 : cur;
      const div = document.createElement("div"); div.className="range-row";
      div.innerHTML = `<div class="range-h"><span class="range-name">${name}</span><span class="range-val" id="rv-${msg}">${val}°C</span></div>
        <input type="range" min="${lo}" max="${hi}" step="${step}" value="${val}" data-msg="${msg}">`;
      target.appendChild(div);
      const inp = div.querySelector("input");
      const out = div.querySelector(".range-val");
      inp.oninput = ()=> out.textContent = inp.value+"°C";
      inp.onchange = async ()=> {
        try{ await api("/api/write", {method:"POST", body:JSON.stringify({circuit:"ctls2", msg, value:parseFloat(inp.value)})});
             toast(name+" → "+inp.value+"°C","ok");
        } catch(e){ toast("Error: "+e.message,"err"); }
      };
    });
  }
  render($("setp-sliders"), SETPOINT_DEFS);
  render($("flow-sliders"), FLOW_DEFS);
}

// Optimizer
async function toggleOptimizer(){
  const enable = $("opt-toggle").checked;
  try{ await api("/api/optimizer", {method:"POST", body:JSON.stringify({enable})});
       toast("Optimizer "+(enable?"ON":"OFF"), "ok"); loadState();
  } catch(e){ toast("Error: "+e.message,"err"); }
}
async function loadDecisions(){
  try{
    const list = await api("/api/decisions?limit=80");
    const tb = $("dec-tbody"); tb.innerHTML="";
    if(!list.length){ tb.innerHTML='<tr><td colspan="3" class="empty">No decisions logged yet.</td></tr>'; return; }
    list.slice().reverse().forEach(d=>{
      const tr = document.createElement("tr");
      tr.innerHTML = `<td class="dec-time">${fmtTime(d.ts)}</td><td><span class="badge bg-a">${d.kind}</span></td><td>${d.reason}</td>`;
      tb.appendChild(tr);
    });
  } catch(e){ toast("Decisions load failed: "+e.message,"err"); }
}

// Charts — Chart.js with a guard against the "canvas in use" race
let CHARTS_BUSY = false;
async function loadSeries(series, hours){
  const data = await api(`/api/history?series=${series}&hours=${hours}`);
  return data.map(p=>({x: p.ts*1000, y: p.value}));
}
function destroyChart(canvasId){
  // Use Chart.js' own global registry so we catch any chart attached to this
  // canvas, even one we never tracked in our local CHARTS map.
  const existing = Chart.getChart(canvasId);
  if (existing) existing.destroy();
}
function mkChart(canvasId, datasets, yUnit){
  destroyChart(canvasId);
  const ctx = $(canvasId).getContext("2d");
  return new Chart(ctx, {
    type: "line",
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: false, parsing: false,
      plugins: { legend: { labels: { color: "#94a3b8", font:{size:11} } } },
      scales: {
        x: { type:"time", time:{ unit:"hour", tooltipFormat:"PP HH:mm" },
             ticks:{color:"#94a3b8",font:{size:10}}, grid:{color:"rgba(148,163,184,.1)"} },
        y: { ticks:{color:"#94a3b8",font:{size:10}, callback:v => v+(yUnit||"")},
             grid:{color:"rgba(148,163,184,.1)"} }
      }
    }
  });
}
async function loadCharts(){
  if (CHARTS_BUSY) return;
  CHARTS_BUSY = true;
  const h = parseInt($("chart-window").value);
  try{
    const [room, setp, out, supply, ret, dt, cop, pin, pout] = await Promise.all([
      loadSeries("room_temp", h),
      loadSeries("setpoint_actual", h),
      loadSeries("outside_temp", h),
      loadSeries("supply_temp", h),
      loadSeries("return_temp", h),
      loadSeries("delta_t", h),
      loadSeries("cop", h),
      loadSeries("power_in", h),
      loadSeries("power_out", h),
    ]);
    mkChart("ch-temps", [
      { label:"Room",     data:room,   borderColor:"#38bdf8", backgroundColor:"transparent", tension:.3, pointRadius:0 },
      { label:"Setpoint", data:setp,   borderColor:"#fbbf24", backgroundColor:"transparent", tension:.3, pointRadius:0, borderDash:[4,4] },
      { label:"Outdoor",  data:out,    borderColor:"#a78bfa", backgroundColor:"transparent", tension:.3, pointRadius:0 },
      { label:"Supply",   data:supply, borderColor:"#fb923c", backgroundColor:"transparent", tension:.3, pointRadius:0 },
      { label:"Return",   data:ret,    borderColor:"#4ade80", backgroundColor:"transparent", tension:.3, pointRadius:0 },
    ], "°C");
    mkChart("ch-dt",
      [{ label:"ΔT", data:dt, borderColor:"#a78bfa", backgroundColor:"rgba(167,139,250,.15)", fill:true, tension:.3, pointRadius:0 }],
      " K");
    mkChart("ch-pow", [
      { label:"Electric", data:pin,  borderColor:"#fbbf24", backgroundColor:"transparent", tension:.3, pointRadius:0 },
      { label:"Thermal",  data:pout, borderColor:"#4ade80", backgroundColor:"transparent", tension:.3, pointRadius:0 },
    ], " kW");
    mkChart("ch-cop",
      [{ label:"COP", data:cop, borderColor:"#4ade80", backgroundColor:"rgba(74,222,128,.15)", fill:true, tension:.3, pointRadius:0 }]);
  } catch(e){ toast("Chart load failed: "+e.message, "err"); }
  finally { CHARTS_BUSY = false; }
}

// Diagnostics
async function loadDiag(){
  try{
    const [msgs, h] = await Promise.all([api("/api/messages"), api("/api/health")]);
    // ebusd status card
    const ec = $("ebusd-card");
    const running = h.ebusd_running;
    ec.innerHTML =
      `<div style="font-size:2rem">${running?"🟢":"🔴"}</div>`+
      `<div style="flex:1;min-width:200px">`+
        `<div style="font-weight:600">ebusd ${running?"running":"stopped"}</div>`+
        `<div class="sub">PID ${h.ebusd_pid ?? "—"} · restarts: ${h.ebusd_restarts} · MQTT ${h.mqtt_connected?"✓":"✗"} · messages: ${h.state_size} · uptime ${fmtAge(h.uptime_seconds)}</div>`+
      `</div>`;
    // messages table
    const tb = $("diag-tbody"); tb.innerHTML="";
    if(!msgs.length){ tb.innerHTML='<tr><td colspan="4" class="empty">No ebusd messages yet.</td></tr>'; return; }
    msgs.forEach(m=>{
      const ageClass = m.age_seconds<120?"diag-fresh":m.age_seconds<600?"diag-old":"diag-stale";
      const fields = Object.entries(m.fields).map(([k,v])=>`<span class="badge bg-m">${k}=${v}</span>`).join(" ");
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${m.circuit}</td><td>${m.msg}</td><td>${fields}</td><td class="${ageClass}">${fmtAge(m.age_seconds)}</td>`;
      tb.appendChild(tr);
    });
  } catch(e){ toast("Diagnostics load failed: "+e.message,"err"); }
}
async function forceRead(){
  try{ await api("/api/force_read", {method:"POST"}); toast("Force-read dispatched","info");
       setTimeout(loadDiag, 6000);
  } catch(e){ toast("Error: "+e.message,"err"); }
}
async function ebusdAction(action){
  try{ const r = await api("/api/ebusd", {method:"POST", body:JSON.stringify({action})});
       toast("ebusd "+action+(r.running?" → running":" → stopped"),"ok");
       setTimeout(loadDiag, 4000);
  } catch(e){ toast("Error: "+e.message,"err"); }
}

// Boot
loadState();
setInterval(loadState, 5000);
setInterval(()=>{ if(document.querySelector(".tab.active").dataset.tab==="optimizer") loadDecisions(); }, 8000);
</script>
</body>
</html>
"""


# ───────────────────────────────────────────────────────────────────────────
# Boot
# ───────────────────────────────────────────────────────────────────────────

def boot() -> None:
    db_init_safe()
    _install_signal_handlers()
    ebusd_start()
    mqtt_start()

    # Initial sync runs later than before — give ebusd a few seconds to
    # finish its bus scan and seed the MQTT discovery before we ask for reads.
    SCHEDULER.add_job(task_initial_sync, "date",
                      run_date=datetime.utcnow() + timedelta(seconds=20))
    SCHEDULER.add_job(mqtt_post_connect_subs, "date",
                      run_date=datetime.utcnow() + timedelta(seconds=8))
    SCHEDULER.add_job(task_initial_sync, "interval", minutes=20,
                      id="initial_sync_periodic")
    SCHEDULER.add_job(task_snapshot_history, "interval", minutes=1,
                      id="snapshot_history", max_instances=1)
    SCHEDULER.add_job(task_optimize, "interval",
                      minutes=CONF["optimize_cycle_min"], id="optimize")
    SCHEDULER.add_job(task_health_check, "interval", minutes=2,
                      id="health_check")
    SCHEDULER.add_job(task_publish_states, "interval", seconds=20,
                      id="publish_states")
    SCHEDULER.add_job(ebusd_watchdog, "interval", seconds=15,
                      id="ebusd_watchdog")
    SCHEDULER.start()
    log.info("Scheduler started — ebusd PID=%s, initial sync in 20s",
             EBUSD_PROCESS.pid if EBUSD_PROCESS else "?")


boot()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8099)
