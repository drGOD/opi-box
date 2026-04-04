"""
SQLite storage for sensor history and relay events.
"""
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_FILE = Path(__file__).parent / "growbox.db"


def init_db() -> None:
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           REAL    NOT NULL,
                temperature  REAL,
                air_humidity REAL,
                eco2_ppm     INTEGER,
                tvoc_ppb     INTEGER,
                aqi          INTEGER,
                soil0_pct    REAL,
                soil1_pct    REAL
            );
            CREATE INDEX IF NOT EXISTS idx_sr_ts ON sensor_readings(ts);

            CREATE TABLE IF NOT EXISTS relay_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         REAL    NOT NULL,
                relay_id   INTEGER NOT NULL,
                relay_name TEXT,
                state      INTEGER NOT NULL,
                mode       TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_re_ts  ON relay_events(ts);
            CREATE INDEX IF NOT EXISTS idx_re_rid ON relay_events(relay_id);
        """)


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def insert_sensor_reading(data: dict) -> None:
    soil  = data.get("soil", [])
    soil0 = next((s["moisture_pct"] for s in soil if s["channel"] == 0), None)
    soil1 = next((s["moisture_pct"] for s in soil if s["channel"] == 1), None)
    with _conn() as conn:
        conn.execute(
            """INSERT INTO sensor_readings
               (ts, temperature, air_humidity, eco2_ppm, tvoc_ppb, aqi, soil0_pct, soil1_pct)
               VALUES (?,?,?,?,?,?,?,?)""",
            (time.time(), data.get("temperature"), data.get("air_humidity"),
             data.get("eco2_ppm"), data.get("tvoc_ppb"), data.get("aqi"),
             soil0, soil1),
        )


def insert_relay_event(relay_id: int, relay_name: str,
                       state: bool, mode: str = "manual") -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO relay_events (ts, relay_id, relay_name, state, mode)
               VALUES (?,?,?,?,?)""",
            (time.time(), relay_id, relay_name, int(state), mode),
        )


def get_history(hours: float = 24, max_points: int = 400) -> dict:
    since = time.time() - hours * 3600

    with _conn() as conn:
        # ── Sensor readings (downsampled) ─────────────────────────────────
        rows = conn.execute(
            """SELECT ts, temperature, air_humidity, eco2_ppm, tvoc_ppb,
                      aqi, soil0_pct, soil1_pct
               FROM sensor_readings WHERE ts >= ? ORDER BY ts""",
            (since,),
        ).fetchall()

        if len(rows) > max_points:
            step  = len(rows) / max_points
            rows  = [rows[int(i * step)] for i in range(max_points)]

        sensors = [dict(r) for r in rows]

        # ── Relay events ──────────────────────────────────────────────────
        relay_ids = conn.execute(
            "SELECT DISTINCT relay_id FROM relay_events"
        ).fetchall()

        relays_out: dict = {}
        for row in relay_ids:
            rid = row["relay_id"]
            key = str(rid)

            # Last known state before the window (for correct step-line start)
            prior = conn.execute(
                """SELECT ts, state FROM relay_events
                   WHERE relay_id=? AND ts<? ORDER BY ts DESC LIMIT 1""",
                (rid, since),
            ).fetchone()

            events: list = []
            if prior:
                events.append({"ts": since, "state": prior["state"]})

            in_range = conn.execute(
                """SELECT ts, state FROM relay_events
                   WHERE relay_id=? AND ts>=? ORDER BY ts""",
                (rid, since),
            ).fetchall()
            events.extend({"ts": r["ts"], "state": r["state"]} for r in in_range)
            relays_out[key] = events

    return {"sensors": sensors, "relays": relays_out}


def cleanup_old_data(keep_days: int = 30) -> None:
    cutoff = time.time() - keep_days * 86400
    with _conn() as conn:
        conn.execute("DELETE FROM sensor_readings WHERE ts < ?", (cutoff,))
        conn.execute("DELETE FROM relay_events   WHERE ts < ?", (cutoff,))
