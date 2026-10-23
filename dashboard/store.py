"""Local SQLite store for the Garmin data the dashboard needs."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from garmin_client.config import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / "data" / "garmin.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,            -- local calendar date of the start
    start_local TEXT NOT NULL,
    name TEXT,
    type_key TEXT,
    sport TEXT NOT NULL,           -- Swim / Bike / Run / Strength / Other
    duration_s REAL,
    distance_m REAL,
    load REAL,                     -- Garmin activityTrainingLoad
    aerobic_te REAL,
    anaerobic_te REAL,
    avg_hr REAL,
    max_hr REAL,
    z1_s REAL, z2_s REAL, z3_s REAL, z4_s REAL, z5_s REAL,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS activities_date ON activities(date);

CREATE TABLE IF NOT EXISTS daily (
    date TEXT PRIMARY KEY,
    resting_hr REAL,
    body_battery_max REAL,
    body_battery_min REAL,
    stress_avg REAL,
    steps REAL
);

-- Sleep and HRV are keyed by the date you woke up (Garmin's calendarDate).
CREATE TABLE IF NOT EXISTS sleep (
    date TEXT PRIMARY KEY,
    sleep_s REAL,
    deep_s REAL,
    light_s REAL,
    rem_s REAL,
    awake_s REAL,
    score REAL,
    avg_hr REAL
);

CREATE TABLE IF NOT EXISTS hrv (
    date TEXT PRIMARY KEY,
    last_night REAL,
    weekly_avg REAL,
    baseline_low REAL,
    baseline_high REAL,
    status TEXT
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))


def sport_group(type_key: str) -> str:
    key = (type_key or "").lower()
    if "swim" in key:
        return "Swim"
    if "cycl" in key or "bik" in key or "ride" in key:
        return "Bike"
    if "run" in key:
        return "Run"
    if "strength" in key:
        return "Strength"
    return "Other"


def upsert_activity(conn: sqlite3.Connection, a: dict) -> None:
    type_key = (a.get("activityType") or {}).get("typeKey", "")
    conn.execute(
        """INSERT OR REPLACE INTO activities VALUES
           (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            a["activityId"],
            a["startTimeLocal"][:10],
            a["startTimeLocal"],
            a.get("activityName"),
            type_key,
            sport_group(type_key),
            a.get("duration"),
            a.get("distance"),
            a.get("activityTrainingLoad"),
            a.get("aerobicTrainingEffect"),
            a.get("anaerobicTrainingEffect"),
            a.get("averageHR"),
            a.get("maxHR"),
            *(a.get(f"hrTimeInZone_{z}") for z in range(1, 6)),
            json.dumps(a),
        ),
    )


def upsert_daily(conn: sqlite3.Connection, d: dict) -> None:
    hr = d.get("heartRate") or {}
    bb = d.get("bodyBattery") or {}
    stress = d.get("stress") or {}
    steps = ((d.get("movement") or {}).get("steps") or {})
    conn.execute(
        "INSERT OR REPLACE INTO daily VALUES (?, ?, ?, ?, ?, ?)",
        (
            d["calendarDate"],
            hr.get("restingValue"),
            bb.get("maxValue"),
            bb.get("minValue"),
            stress.get("avgLevel"),
            steps.get("value"),
        ),
    )


def upsert_sleep(conn: sqlite3.Connection, s: dict) -> None:
    if not s.get("sleepTimeSeconds"):
        return
    score = ((s.get("sleepScores") or {}).get("overall") or {}).get("value")
    conn.execute(
        "INSERT OR REPLACE INTO sleep VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            s["calendarDate"],
            s.get("sleepTimeSeconds"),
            s.get("deepSleepSeconds"),
            s.get("lightSleepSeconds"),
            s.get("remSleepSeconds"),
            s.get("awakeSleepSeconds"),
            score,
            s.get("avgHeartRate"),
        ),
    )


def upsert_hrv(conn: sqlite3.Connection, h: dict) -> None:
    baseline = h.get("baseline") or {}
    conn.execute(
        "INSERT OR REPLACE INTO hrv VALUES (?, ?, ?, ?, ?, ?)",
        (
            h["calendarDate"],
            h.get("lastNightAvg"),
            h.get("weeklyAvg"),
            baseline.get("balancedLow"),
            baseline.get("balancedUpper"),
            h.get("status"),
        ),
    )
