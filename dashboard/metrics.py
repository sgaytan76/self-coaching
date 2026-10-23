"""Turn the stored Garmin data into the series and summaries the dashboard shows.

Load model (Garmin activityTrainingLoad summed per day):
  fitness (CTL)  exponentially weighted 42-day average of daily load
  fatigue (ATL)  exponentially weighted 7-day average of daily load
  form    (TSB)  yesterday's fitness minus yesterday's fatigue
  ACWR           mean load of the last 7 days / mean load of the last 28 days
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from statistics import mean

SPORTS = ("Swim", "Bike", "Run", "Strength", "Other")
CTL_DAYS = 42
ATL_DAYS = 7
HARD_PERCENTILE = 0.75


def days_between(start: dt.date, end: dt.date) -> list[dt.date]:
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def avg(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(mean(values), 2) if values else None


def percentile(values: list[float], q: float) -> float | None:
    values = sorted(values)
    if not values:
        return None
    pos = (len(values) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def load_model(daily_load: list[float]) -> list[dict]:
    """Fitness, fatigue, form and ACWR for each day of a contiguous daily load series."""
    out = []
    ctl = atl = 0.0
    for i, load in enumerate(daily_load):
        form = ctl - atl  # from yesterday's values: freshness going into the day
        ctl += (load - ctl) / CTL_DAYS
        atl += (load - atl) / ATL_DAYS
        acute = mean(daily_load[max(0, i - 6): i + 1])
        chronic = mean(daily_load[max(0, i - 27): i + 1])
        out.append(
            {
                "ctl": round(ctl, 1),
                "atl": round(atl, 1),
                "form": round(form, 1),
                "acwr": round(acute / chronic, 2) if chronic > 0 and i >= 27 else None,
            }
        )
    return out


def classify_day(load: float, hard_threshold: float | None) -> str:
    if load <= 0:
        return "rest"
    if hard_threshold is not None and load >= hard_threshold:
        return "hard"
    return "easy"


class Dataset:
    """Everything in the store, indexed by date, plus the load model over full history."""

    def __init__(self, conn: sqlite3.Connection, through: dt.date):
        self.activities = [dict(r) for r in conn.execute("SELECT * FROM activities ORDER BY start_local")]
        self.daily = {r["date"]: dict(r) for r in conn.execute("SELECT * FROM daily")}
        self.sleep = {r["date"]: dict(r) for r in conn.execute("SELECT * FROM sleep")}
        self.hrv = {r["date"]: dict(r) for r in conn.execute("SELECT * FROM hrv")}

        dates = [a["date"] for a in self.activities] + list(self.daily) + list(self.sleep)
        first = min(dates) if dates else through.isoformat()
        self.days = days_between(dt.date.fromisoformat(first), max(through, dt.date.today()))

        self.by_day: dict[str, dict] = {
            d.isoformat(): {
                "load": 0.0,
                "hours": 0.0,
                "sports": {s: {"load": 0.0, "hours": 0.0} for s in SPORTS},
                "activities": [],
            }
            for d in self.days
        }
        for a in self.activities:
            day = self.by_day.get(a["date"])
            if day is None:
                continue
            load, hours = a["load"] or 0.0, (a["duration_s"] or 0.0) / 3600
            day["load"] += load
            day["hours"] += hours
            day["sports"][a["sport"]]["load"] += load
            day["sports"][a["sport"]]["hours"] += hours
            day["activities"].append(a)

        model = load_model([self.by_day[d.isoformat()]["load"] for d in self.days])
        for d, m in zip(self.days, model):
            self.by_day[d.isoformat()].update(m)

        training_loads = [v["load"] for v in self.by_day.values() if v["load"] > 0]
        self.hard_threshold = percentile(training_loads, HARD_PERCENTILE)

    def recovery(self, date: str) -> dict:
        daily, sleep, hrv = self.daily.get(date, {}), self.sleep.get(date, {}), self.hrv.get(date, {})
        return {
            "rhr": daily.get("resting_hr"),
            "body_battery": daily.get("body_battery_max"),
            "sleep_h": round(sleep["sleep_s"] / 3600, 2) if sleep.get("sleep_s") else None,
            "sleep_score": sleep.get("score"),
            "deep_h": round(sleep["deep_s"] / 3600, 2) if sleep.get("deep_s") else None,
            "rem_h": round(sleep["rem_s"] / 3600, 2) if sleep.get("rem_s") else None,
            "hrv": hrv.get("last_night"),
            "hrv_low": hrv.get("baseline_low"),
            "hrv_high": hrv.get("baseline_high"),
            "hrv_status": hrv.get("status"),
        }


def period_kpis(ds: Dataset, days: list[dt.date]) -> dict:
    keys = [d.isoformat() for d in days]
    rows = [ds.by_day[k] for k in keys if k in ds.by_day]
    rec = [ds.recovery(k) for k in keys]
    end = ds.by_day.get(keys[-1], {}) if keys else {}
    return {
        "hours": round(sum(r["hours"] for r in rows), 1),
        "load": round(sum(r["load"] for r in rows)),
        "sessions": sum(len(r["activities"]) for r in rows),
        "sleep_h": avg(r["sleep_h"] for r in rec),
        "sleep_score": avg(r["sleep_score"] for r in rec),
        "hrv": avg(r["hrv"] for r in rec),
        "rhr": avg(r["rhr"] for r in rec),
        "ctl": end.get("ctl"),
        "atl": end.get("atl"),
        "form": end.get("form"),
        "acwr": end.get("acwr"),
    }


def next_day_response(ds: Dataset, days: list[dt.date]) -> dict:
    """How recovery the following night/morning responds to rest, easy and hard days."""
    groups = {g: [] for g in ("rest", "easy", "hard")}
    scatter = []
    for d in days:
        key, nxt = d.isoformat(), (d + dt.timedelta(days=1)).isoformat()
        if key not in ds.by_day or nxt not in ds.by_day:
            continue
        load = ds.by_day[key]["load"]
        rec = ds.recovery(nxt)
        groups[classify_day(load, ds.hard_threshold)].append(rec)
        if rec["hrv"] is not None or rec["rhr"] is not None:
            scatter.append({"date": key, "load": round(load), "hrv": rec["hrv"], "rhr": rec["rhr"],
                            "sleep_score": rec["sleep_score"]})

    metrics = ("hrv", "rhr", "sleep_score", "sleep_h")
    everything = [r for rows in groups.values() for r in rows]
    overall = {m: avg(r[m] for r in everything) for m in metrics}
    summary = {}
    for name, rows in groups.items():
        means = {m: avg(r[m] for r in rows) for m in metrics}
        summary[name] = {
            "days": len(rows),
            **means,
            "delta": {m: round(means[m] - overall[m], 2) if None not in (means[m], overall[m]) else None
                      for m in metrics},
        }
    return {
        "hard_threshold": round(ds.hard_threshold) if ds.hard_threshold else None,
        "overall": overall,
        "groups": summary,
        "scatter": scatter,
    }


def weekly(ds: Dataset, days: list[dt.date]) -> list[dict]:
    weeks: dict[str, dict] = {}
    for d in days:
        row = ds.by_day.get(d.isoformat())
        if row is None:
            continue
        week = (d - dt.timedelta(days=d.weekday())).isoformat()
        w = weeks.setdefault(week, {"week": week, "hours": 0.0, "load": 0.0,
                                    "sports": {s: {"hours": 0.0, "load": 0.0} for s in SPORTS}})
        w["hours"] += row["hours"]
        w["load"] += row["load"]
        for s in SPORTS:
            w["sports"][s]["hours"] += row["sports"][s]["hours"]
            w["sports"][s]["load"] += row["sports"][s]["load"]
    return list(weeks.values())


def zones(activities: list[dict]) -> dict:
    by_sport = {s: [0.0] * 5 for s in SPORTS}
    for a in activities:
        for z in range(5):
            by_sport[a["sport"]][z] += (a[f"z{z + 1}_s"] or 0.0) / 3600
    total = [sum(by_sport[s][z] for s in SPORTS) for z in range(5)]
    return {"by_sport": by_sport, "total": total}


def dashboard_data(conn: sqlite3.Connection, start: dt.date, end: dt.date) -> dict:
    ds = Dataset(conn, end)
    days = days_between(start, end)
    length = len(days)
    prev_days = days_between(start - dt.timedelta(days=length), start - dt.timedelta(days=1))

    series = []
    for d in days:
        key = d.isoformat()
        row = ds.by_day.get(key)
        if row is None:
            continue
        series.append({
            "date": key,
            "load": round(row["load"], 1),
            "hours": round(row["hours"], 2),
            "sports": row["sports"],
            "ctl": row["ctl"], "atl": row["atl"], "form": row["form"], "acwr": row["acwr"],
            "day_type": classify_day(row["load"], ds.hard_threshold),
            "activities": [{"name": a["name"], "sport": a["sport"], "load": a["load"],
                            "hours": round((a["duration_s"] or 0) / 3600, 2)} for a in row["activities"]],
            **ds.recovery(key),
        })

    in_range = [a for a in ds.activities if start.isoformat() <= a["date"] <= end.isoformat()]
    return {
        "range": {"start": start.isoformat(), "end": end.isoformat(), "days": length},
        "kpis": {"current": period_kpis(ds, days), "previous": period_kpis(ds, prev_days)},
        "series": series,
        "weekly": weekly(ds, days),
        "zones": zones(in_range),
        "next_day": next_day_response(ds, days),
        "activities": [
            {k: a[k] for k in ("id", "date", "start_local", "name", "sport", "type_key", "duration_s",
                               "distance_m", "load", "aerobic_te", "anaerobic_te", "avg_hr", "max_hr")}
            for a in reversed(in_range)
        ],
        "sports": list(SPORTS),
    }
