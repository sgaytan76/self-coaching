# self-coaching
self coaching tools

## Garmin connection

Uses the Garmin Connect **Personal API** (read-only, your own account only).

1. Create a key at https://connect.garmin.com/app/settings/personalApiToken
   (pick an expiry of 30 days to 1 year; the key is shown only once).
2. Set up the environment:
   `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
3. Copy `.env.example` to `.env` (gitignored) and set `GARMIN_PERSONAL_API_KEY`.
4. Verify: `.venv/bin/python -m garmin_client test` — prints your profile, the last
   7 days of daily summaries and your latest activities.

The key is exchanged for a bearer token valid for up to 24h. It is cached in
`.garmin_token.json` (gitignored) and exchanged again automatically; there are
no refresh tokens. When the key itself expires, create a new one and update `.env`.

```python
from garmin_client import GarminClient

client = GarminClient()
client.daily_summaries("2026-09-01", "2026-10-22")  # long ranges are split automatically
client.sleep_summaries("2026-10-01", "2026-10-22")
client.hrv_summaries("2026-10-01", "2026-10-22")
client.activities(limit=20)
client.download_activity(activity_id)  # ZIP with the original .FIT
```

Per-request range limits (handled by the client): daily summaries and HRV 180 days,
sleep and Body Battery 28 days, stress and steps 7 days. The API cannot send
workouts to the watch.
