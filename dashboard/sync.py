"""Pull Garmin data into the local store.

The first sync downloads everything since HISTORY_START; later syncs re-fetch
only the last few days, since recent days keep changing (late uploads,
sleep finalised in the morning, HRV status updates).
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import threading

from garmin_client import GarminClient

from . import store

HISTORY_START = dt.date(2024, 1, 1)
RESYNC_DAYS = 4
ACTIVITY_PAGE = 50

_lock = threading.Lock()


def sync(conn: sqlite3.Connection, client: GarminClient | None = None, log=print) -> dict:
    """Bring the store up to date. Returns counts of rows fetched per table."""
    with _lock:
        client = client or GarminClient()
        today = dt.date.today()
        last = store.get_meta(conn, "last_sync_date")
        start = HISTORY_START
        if last:
            start = max(HISTORY_START, dt.date.fromisoformat(last) - dt.timedelta(days=RESYNC_DAYS))
        log(f"Syncing Garmin data {start} -> {today}")

        profile = client.profile_settings()
        store.set_meta(conn, "measurement_system", profile.get("measurementSystem") or "metric")
        store.set_meta(conn, "display_name", profile.get("displayName") or "")

        counts = {}
        dailies = client.daily_summaries(start, today)
        for d in dailies:
            store.upsert_daily(conn, d)
        counts["daily"] = len(dailies)

        sleeps = client.sleep_summaries(start, today)
        for s in sleeps:
            store.upsert_sleep(conn, s)
        counts["sleep"] = len(sleeps)

        hrvs = client.hrv_summaries(start, today)
        for h in hrvs:
            store.upsert_hrv(conn, h)
        counts["hrv"] = len(hrvs)

        counts["activities"] = 0
        offset = 0
        while True:
            page = client.activities(start=offset, limit=ACTIVITY_PAGE, start_date=start, end_date=today)
            for a in page:
                store.upsert_activity(conn, a)
            counts["activities"] += len(page)
            if len(page) < ACTIVITY_PAGE:
                break
            offset += ACTIVITY_PAGE

        store.set_meta(conn, "last_sync_date", today.isoformat())
        store.set_meta(conn, "last_sync_at", dt.datetime.now().isoformat(timespec="seconds"))
        conn.commit()
        log(f"Synced: {counts}")
        return counts


def last_sync_at(conn: sqlite3.Connection) -> dt.datetime | None:
    value = store.get_meta(conn, "last_sync_at")
    return dt.datetime.fromisoformat(value) if value else None
