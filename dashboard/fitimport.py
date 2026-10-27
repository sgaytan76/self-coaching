"""Import activities recorded outside Garmin Connect (e.g. Zwift) from a .fit or .fit.gz file.

Such files lack what the dashboard gets from Garmin, so the import fills it in:
  heart rate   from the file when it has any, otherwise from the athlete's watch
               all-day readings (one sample every ~2 min) during the activity
  load         Garmin's training load can't be recomputed, so it is estimated as
               Banister TRIMP (from average HR) times this athlete's median ratio of
               Garmin load to TRIMP over their own Garmin activities
  local time   the athlete's UTC offset, read from their latest Garmin activity

Imported activities get a negative id (minus the start time in epoch seconds), so
they never collide with Garmin ids, re-importing the same file replaces the row,
and sync never overwrites them.
"""

from __future__ import annotations

import datetime as dt
import gzip
import io
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, median

from garmin_client import GarminClient

from . import store

TYPE_KEYS = {"cycling": "cycling", "running": "running", "swimming": "lap_swimming"}
VIRTUAL_TYPE_KEYS = {"cycling": "virtual_ride", "running": "virtual_run"}
SPORT_WORDS = {"cycling": "ride", "running": "run", "swimming": "swim"}
ZONE_SPORTS = {"Bike": "CYCLING", "Run": "RUNNING", "Swim": "SWIMMING"}
CALIBRATION_DAYS = 365
MIN_CALIBRATION = 3
OVERLAP = dt.timedelta(minutes=15)


@dataclass
class FitActivity:
    start_utc: dt.datetime  # naive UTC, as FIT stores it
    timer_s: float
    elapsed_s: float
    sport: str
    sub_sport: str = ""
    manufacturer: str = ""
    distance_m: float | None = None
    avg_power: float | None = None
    max_power: float | None = None
    norm_power: float | None = None
    avg_cadence: float | None = None
    ascent_m: float | None = None
    calories: float | None = None
    hr: list[tuple[dt.datetime, int]] = field(default_factory=list)

    @property
    def end_utc(self) -> dt.datetime:
        return self.start_utc + dt.timedelta(seconds=self.elapsed_s)

    @property
    def type_key(self) -> str:
        if self.sub_sport == "virtual_activity" and self.sport in VIRTUAL_TYPE_KEYS:
            return VIRTUAL_TYPE_KEYS[self.sport]
        return TYPE_KEYS.get(self.sport, self.sport or "other")


def normalized_power(power: list[float]) -> float | None:
    """Coggan NP: 4th-power mean of the 30 s rolling average, from 1 s samples."""
    if len(power) < 30:
        return None
    window = sum(power[:30])
    rolling = [window / 30]
    for i in range(30, len(power)):
        window += power[i] - power[i - 30]
        rolling.append(window / 30)
    return round(mean(x ** 4 for x in rolling) ** 0.25, 1)


def read_fit(path: Path) -> FitActivity:
    import fitparse  # only needed for imports

    data = path.read_bytes()
    if path.suffix == ".gz":
        data = gzip.decompress(data)
    fit = fitparse.FitFile(io.BytesIO(data))
    session = next(fit.get_messages("session")).get_values()
    file_id = next(fit.get_messages("file_id"), None)
    records = [m.get_values() for m in fit.get_messages("record")]
    power = [r.get("power") or 0 for r in records]
    has_power = any(r.get("power") is not None for r in records)
    return FitActivity(
        start_utc=session["start_time"],
        timer_s=session.get("total_timer_time") or 0,
        elapsed_s=session.get("total_elapsed_time") or session.get("total_timer_time") or 0,
        sport=str(session.get("sport") or ""),
        sub_sport=str(session.get("sub_sport") or ""),
        manufacturer=str(file_id.get_value("manufacturer") or "") if file_id else "",
        distance_m=session.get("total_distance"),
        avg_power=session.get("avg_power"),
        max_power=session.get("max_power"),
        norm_power=normalized_power(power) if has_power else None,
        avg_cadence=session.get("avg_cadence"),
        ascent_m=session.get("total_ascent"),
        calories=session.get("total_calories"),
        hr=[(r["timestamp"], r["heart_rate"]) for r in records if r.get("heart_rate")],
    )


def utc_offset(conn: sqlite3.Connection) -> dt.timedelta | None:
    """The athlete's local-minus-UTC offset, from their latest Garmin activity."""
    row = conn.execute(
        """SELECT json_extract(raw, '$.startTimeLocal'), json_extract(raw, '$.startTimeGMT')
           FROM activities WHERE id > 0 AND json_extract(raw, '$.startTimeGMT') IS NOT NULL
           ORDER BY start_local DESC LIMIT 1"""
    ).fetchone()
    if row is None:
        return None
    local, gmt = (dt.datetime.fromisoformat(v) for v in row)
    return local - gmt


def watch_hr(client: GarminClient, start: dt.datetime, end: dt.datetime,
             offset: dt.timedelta) -> list[tuple[dt.datetime, int]]:
    """All-day watch heart rate between two naive UTC times."""
    days = {(start + offset).date(), (end + offset).date()}
    samples = []
    for day in sorted(days):
        for ms, bpm in (client.heart_rate(day) or {}).get("heartRateValues") or []:
            t = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).replace(tzinfo=None)
            if bpm and start <= t <= end:
                samples.append((t, bpm))
    return sorted(samples)


def trimp(minutes: float, avg_hr: float, rest_hr: float, max_hr: float) -> float:
    """Banister TRIMP from average heart rate."""
    x = max(0.0, min(1.0, (avg_hr - rest_hr) / (max_hr - rest_hr)))
    return minutes * x * 0.64 * math.exp(1.92 * x)


def load_factor(conn: sqlite3.Connection, sport: str, rest_hr: float, max_hr: float,
                before: str) -> tuple[float, int]:
    """Median Garmin load / TRIMP over the athlete's own activities. Returns (factor, n)."""
    since = (dt.date.fromisoformat(before) - dt.timedelta(days=CALIBRATION_DAYS)).isoformat()
    rows = conn.execute(
        """SELECT sport, duration_s, load, avg_hr FROM activities
           WHERE id > 0 AND load > 0 AND avg_hr > 0 AND duration_s >= 1200 AND date BETWEEN ? AND ?""",
        (since, before),
    ).fetchall()

    def ratios(rs):
        out = []
        for _, dur, load, hr in rs:
            t = trimp(dur / 60, hr, rest_hr, max_hr)
            if t > 0:
                out.append(load / t)
        return out

    same = ratios([r for r in rows if r[0] == sport])
    found = same if len(same) >= MIN_CALIBRATION else ratios(rows)
    return (median(found), len(found)) if found else (1.0, 0)


def inferred_ftp(conn: sqlite3.Connection) -> float | None:
    """FTP Garmin used on the latest ride with power (NP / IF), ignoring implausible IFs."""
    row = conn.execute(
        """SELECT json_extract(raw, '$.normPower') / json_extract(raw, '$.intensityFactor')
           FROM activities WHERE id > 0 AND sport = 'Bike'
             AND json_extract(raw, '$.normPower') > 0
             AND json_extract(raw, '$.intensityFactor') BETWEEN 0.6 AND 1.15
           ORDER BY start_local DESC LIMIT 1"""
    ).fetchone()
    return round(row[0]) if row and row[0] else None


def heart_rate_profile(conn: sqlite3.Connection, client: GarminClient, sport: str,
                       date: str) -> tuple[float, float, list[int]]:
    """(resting HR, max HR, zone floors) for TRIMP and time in zones."""
    zones = client.heart_rate_zones() or []
    wanted = ZONE_SPORTS.get(sport)
    zone = (next((z for z in zones if z.get("sport") == wanted), None)
            or next((z for z in zones if z.get("sport") == "DEFAULT"), None) or {})
    row = conn.execute(
        "SELECT resting_hr FROM daily WHERE date <= ? AND resting_hr > 0 ORDER BY date DESC LIMIT 1",
        (date,),
    ).fetchone()
    rest = (row[0] if row else None) or zone.get("restingHeartRateUsed") or 60
    peak = conn.execute("SELECT MAX(max_hr) FROM activities WHERE id > 0").fetchone()[0]
    top = zone.get("maxHeartRateUsed") or peak or 190
    floors = [zone[f"zone{i}Floor"] for i in range(1, 6) if zone.get(f"zone{i}Floor")]
    return rest, top, floors


def time_in_zones(samples: list[tuple[dt.datetime, int]], floors: list[int]) -> list[float] | None:
    """Seconds per zone, weighting each sample by the typical gap between samples."""
    if len(samples) < 2 or len(floors) != 5:
        return None
    step = median((b[0] - a[0]).total_seconds() for a, b in zip(samples, samples[1:]))
    out = [0.0] * 5
    for _, bpm in samples:
        z = sum(1 for f in floors if bpm >= f) - 1
        if z >= 0:
            out[z] += step
    return out


def find_overlap(conn: sqlite3.Connection, start_local: dt.datetime, sport: str):
    """A Garmin activity of the same sport starting within OVERLAP, if there is one."""
    lo, hi = (start_local - OVERLAP).isoformat(" "), (start_local + OVERLAP).isoformat(" ")
    return conn.execute(
        "SELECT name, start_local FROM activities WHERE id > 0 AND sport = ? AND start_local BETWEEN ? AND ?",
        (sport, lo, hi),
    ).fetchone()


def import_fit(conn: sqlite3.Connection, client: GarminClient, fa: FitActivity, source: str,
               name: str | None = None, ftp: float | None = None, force: bool = False,
               log=print) -> dict:
    """Store a parsed FIT activity as a Garmin-shaped activity. Returns what was stored."""
    offset = utc_offset(conn)
    if offset is None:
        offset = dt.datetime.now().astimezone().utcoffset()
        log(f"No Garmin activities to read the athlete's time zone from; using this computer's ({offset}).")
    start_local = fa.start_utc + offset
    sport = store.sport_group(fa.type_key)

    clash = find_overlap(conn, start_local, sport)
    if clash and not force:
        raise ValueError(f"Garmin already has '{clash[0]}' at {clash[1]}; "
                         "re-run with --force to import anyway.")

    hr, hr_source = fa.hr, "file"
    if len(hr) < 2:
        hr, hr_source = watch_hr(client, fa.start_utc, fa.end_utc, offset), "watch all-day readings"
    date = start_local.date().isoformat()
    rest, top, floors = heart_rate_profile(conn, client, sport, date)
    avg_hr = round(mean(b for _, b in hr)) if hr else None

    load, note = None, "No heart rate in the file or from the watch, so no load estimate."
    if avg_hr:
        factor, n = load_factor(conn, sport, rest, top, date)
        load = round(factor * trimp(fa.timer_s / 60, avg_hr, rest, top))
        note = (f"Load estimated as TRIMP x {factor:.2f} (median Garmin load/TRIMP over {n} of the "
                f"athlete's Garmin activities; resting HR {rest:g}, max HR {top:g}). HR from {hr_source}.")

    ftp = ftp or inferred_ftp(conn)
    intensity = tss = None
    if ftp and fa.norm_power:
        intensity = round(fa.norm_power / ftp, 3)
        tss = round(fa.timer_s / 3600 * intensity ** 2 * 100, 1)
        note += f" TSS uses FTP {ftp:g} W."

    tiz = time_in_zones(hr, floors) or [None] * 5
    word = SPORT_WORDS.get(fa.sport, fa.sport or "activity")
    activity = {
        "activityId": -int(fa.start_utc.replace(tzinfo=dt.timezone.utc).timestamp()),
        "activityName": name or f"{(fa.manufacturer or 'Imported').title()} {word} (imported)",
        "activityType": {"typeKey": fa.type_key},
        "startTimeLocal": start_local.isoformat(" "),
        "startTimeGMT": fa.start_utc.isoformat(" "),
        "duration": fa.timer_s,
        "elapsedDuration": fa.elapsed_s,
        "distance": fa.distance_m,
        "averageHR": avg_hr,
        "maxHR": max(b for _, b in hr) if hr else None,
        "avgPower": fa.avg_power,
        "maxPower": fa.max_power,
        "normPower": fa.norm_power,
        "intensityFactor": intensity,
        "trainingStressScore": tss,
        "averageBikingCadenceInRevPerMinute": fa.avg_cadence,
        "elevationGain": fa.ascent_m,
        "calories": fa.calories,
        "activityTrainingLoad": load,
        **{f"hrTimeInZone_{i + 1}": s for i, s in enumerate(tiz)},
        "manufacturer": (fa.manufacturer or "").upper(),
        "importedFrom": source,
        "importNote": note,
    }
    store.upsert_activity(conn, activity)
    conn.commit()
    return activity
