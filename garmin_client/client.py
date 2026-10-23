"""Thin read-only wrapper over the Garmin Connect Personal API."""

from __future__ import annotations

import datetime as dt
from typing import Union

import requests

from .auth import get_access_token
from .config import Settings

DateLike = Union[dt.date, str]


def as_date(value: DateLike) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(value)


def date_windows(start: DateLike, end: DateLike, max_days: int):
    """Split the inclusive range [start, end] into windows of at most `max_days` days."""
    start, end = as_date(start), as_date(end)
    while start <= end:
        window_end = min(start + dt.timedelta(days=max_days - 1), end)
        yield start, window_end
        start = window_end + dt.timedelta(days=1)


class GarminClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.session = requests.Session()

    def _request(self, path: str, params: dict | None = None, accept: str = "application/json"):
        resp = None
        # Retry once with a freshly exchanged token if the cached one was rejected.
        for force_refresh in (False, True):
            token = get_access_token(self.settings, force_refresh=force_refresh)
            resp = self.session.get(
                self.settings.base_url + path,
                params=params,
                headers={"Authorization": f"Bearer {token}", "Accept": accept},
                timeout=60,
            )
            if resp.status_code != 401:
                break
        if not resp.ok:
            raise RuntimeError(f"GET {path} failed ({resp.status_code}): {resp.text[:500]}")
        return resp

    def get(self, path: str, params: dict | None = None):
        """GET any supported endpoint and return the decoded JSON."""
        resp = self._request(path, params)
        return resp.json() if resp.content else None

    def _ranged(self, max_days: int, start: DateLike, end: DateLike, fetch) -> list:
        """Call `fetch(start, end)` per window of at most `max_days` days and merge the lists."""
        items: list = []
        for s, e in date_windows(start, end, max_days):
            result = fetch(s.isoformat(), e.isoformat())
            if isinstance(result, list):
                items.extend(result)
            elif result is not None:
                items.append(result)
        return items

    # Profile

    def profile_settings(self) -> dict:
        return self.get("/userprofile-service/userprofile/settings")

    def heart_rate_zones(self):
        return self.get("/biometric-service/heartRateZones/v2")

    # Daily health summaries (date ranges are inclusive, YYYY-MM-DD or date objects)

    def daily_summaries(self, start: DateLike, end: DateLike) -> list:
        return self._ranged(180, start, end, lambda s, e: self.get(
            "/usersummary-service/v2/dailysummaries", {"startDate": s, "endDate": e}))

    def sleep_summaries(self, start: DateLike, end: DateLike) -> list:
        return self._ranged(28, start, end, lambda s, e: self.get(
            "/sleep-service/sleep/dailySleepSummariesByDate", {"startDate": s, "endDate": e}))

    def hrv_summaries(self, start: DateLike, end: DateLike) -> list:
        return self._ranged(180, start, end, lambda s, e: self.get(
            f"/hrv-service/hrv/daily/{s}/{e}"))

    def body_battery(self, start: DateLike, end: DateLike) -> list:
        return self._ranged(28, start, end, lambda s, e: self.get(
            "/wellness-service/wellness/bodyBattery/reports/daily", {"startDate": s, "endDate": e}))

    def daily_stress(self, start: DateLike, end: DateLike) -> list:
        return self._ranged(7, start, end, lambda s, e: self.get(
            f"/usersummary-service/stats/stress/daily/{s}/{e}"))

    def daily_steps(self, start: DateLike, end: DateLike) -> list:
        return self._ranged(7, start, end, lambda s, e: self.get(
            f"/usersummary-service/stats/steps/daily/{s}/{e}"))

    def weight(self, start: DateLike, end: DateLike):
        return self.get(
            f"/weight-service/weight/range/{as_date(start)}/{as_date(end)}", {"includeAll": "true"}
        )

    # Single-day detail

    def sleep(self, day: DateLike):
        return self.get("/sleep-service/sleep/dailySleep", {"date": as_date(day).isoformat()})

    def hrv(self, day: DateLike):
        return self.get(f"/hrv-service/hrv/{as_date(day)}")

    def heart_rate(self, day: DateLike):
        return self.get("/wellness-service/wellness/dailyHeartRate", {"date": as_date(day).isoformat()})

    # Activities

    def activities(
        self,
        start: int = 0,
        limit: int = 20,
        start_date: DateLike | None = None,
        end_date: DateLike | None = None,
        activity_type: str | None = None,
    ) -> list:
        """Search activities, newest first. Garmin caps a page at about 50 items."""
        params: dict = {"start": start, "limit": limit}
        if start_date:
            params["startDate"] = as_date(start_date).isoformat()
        if end_date:
            params["endDate"] = as_date(end_date).isoformat()
        if activity_type:
            params["activityType"] = activity_type
        return self.get("/activitylist-service/activities/search/activities", params)

    def activity(self, activity_id: int) -> dict:
        return self.get(f"/activity-service/activity/{activity_id}")

    def download_activity(self, activity_id: int) -> bytes:
        """Return the original activity file as ZIP bytes (usually a .FIT inside)."""
        return self._request(
            f"/download-service/files/activity/{activity_id}", accept="application/octet-stream"
        ).content
