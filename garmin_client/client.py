"""Thin wrapper over the Garmin Health / Activity REST API."""

import time

import requests

from .auth import get_access_token
from .config import Settings

WELLNESS = "/wellness-api/rest"

# Summary types available on the pull and backfill endpoints. Which ones work
# depends on the APIs and permissions enabled for your app.
SUMMARY_TYPES = (
    "dailies",
    "epochs",
    "sleeps",
    "bodyComps",
    "stressDetails",
    "userMetrics",
    "pulseox",
    "respiration",
    "hrv",
    "healthSnapshot",
    "skinTemp",
    "activities",
    "activityDetails",
    "moveiq",
)

# Garmin limits pull queries to a 24h window and backfill requests to 90 days.
MAX_PULL_WINDOW = 86400
MAX_BACKFILL_WINDOW = 90 * 86400


def time_windows(start: int, end: int, size: int):
    """Split [start, end) into consecutive windows of at most `size` seconds."""
    while start < end:
        yield start, min(start + size, end)
        start += size


class GarminClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.session = requests.Session()

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        headers = {"Authorization": f"Bearer {get_access_token(self.settings)}"}
        resp = self.session.request(
            method, self.settings.api_base + path, headers=headers, timeout=60, **kwargs
        )
        if not resp.ok:
            raise RuntimeError(f"{method} {path} failed ({resp.status_code}): {resp.text}")
        return resp

    def user_id(self) -> str:
        return self._request("GET", f"{WELLNESS}/user/id").json()["userId"]

    def permissions(self) -> list[str]:
        return self._request("GET", f"{WELLNESS}/user/permissions").json()

    def pull(self, summary_type: str, start: int, end: int | None = None) -> list[dict]:
        """Fetch summaries uploaded to Garmin between `start` and `end` (epoch seconds)."""
        end = end or int(time.time())
        items: list[dict] = []
        for s, e in time_windows(start, end, MAX_PULL_WINDOW):
            resp = self._request(
                "GET",
                f"{WELLNESS}/{summary_type}",
                params={"uploadStartTimeInSeconds": s, "uploadEndTimeInSeconds": e},
            )
            items.extend(resp.json())
        return items

    def request_backfill(self, summary_type: str, start: int, end: int) -> None:
        """Ask Garmin to re-send historical summaries for [start, end).

        Backfill is asynchronous: Garmin delivers the data to the push/ping
        endpoints configured for your app in the developer portal.
        """
        for s, e in time_windows(start, end, MAX_BACKFILL_WINDOW):
            self._request(
                "GET",
                f"{WELLNESS}/backfill/{summary_type}",
                params={"summaryStartTimeInSeconds": s, "summaryEndTimeInSeconds": e},
            )
