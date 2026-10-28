# self-coaching
self coaching tools

## Garmin connection

Uses the Garmin Connect **Personal API** (read-only, your own account only).

1. Create a key at https://connect.garmin.com/app/settings/personalApiToken
   (pick an expiry of 30 days to 1 year; the key is shown only once).
2. Set up the environment with Python 3.10+ (macOS's built-in Python 3.9 uses
   LibreSSL 2.8, which Garmin's servers reject). With [uv](https://docs.astral.sh/uv/):
   `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt`
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

## Training load dashboard

```
.venv/bin/python -m dashboard                      # sync stale athletes, serve http://localhost:8000
.venv/bin/python -m dashboard sync [--athlete ID]  # just pull new data (default: everyone)
.venv/bin/python -m dashboard serve --no-sync --port 8001
```

### Athletes

You are always the athlete `me`, using `GARMIN_PERSONAL_API_KEY` from `.env`.
To coach others, ask each athlete to create a Personal API key at
https://connect.garmin.com/app/settings/personalApiToken and send it to you
privately, then:

```
.venv/bin/python -m dashboard athletes add "Ana López"   # prompts for the key (hidden)
.venv/bin/python -m dashboard athletes list
.venv/bin/python -m dashboard athletes update-key ana-lopez   # after their key expires
.venv/bin/python -m dashboard athletes remove ana-lopez [--delete-data]
```

`add` checks the key against Garmin before saving it. Keys are stored in
`athletes.json` (gitignored, file mode 600). Each athlete's database and token
live in `data/athletes/<id>/`, so data is never mixed, and units follow each
athlete's Garmin profile. Pick who to view from the athlete dropdown in the
dashboard header; new athletes show up there after a page reload, without
restarting the server.

With two or more athletes the dropdown starts with **All athletes**: one card per
athlete with this morning's readiness cue and any warnings, HRV, resting HR, sleep,
ACWR and form, the last 7 days against the 7 before, a 14-day load sparkline, their
latest session and sync status. Cards needing attention ("Easy day or rest", no
recent data) come first; click one to open that athlete. **Sync all** syncs everyone
in turn, and opening the overview syncs anyone whose data is over an hour old.

Athletes can revoke access at any time by deleting their key in Garmin Connect.
Garmin describes the Personal API as meant for users accessing their own data;
coaching others with their keys is a gray area, so get their consent.

### Activities recorded outside Garmin (Zwift and others)

When a session never reaches Garmin Connect, download its `.fit` (or `.fit.gz`)
file and import it:

```
.venv/bin/python -m dashboard import-fit ~/Downloads/zwift-activity-123.fit.gz --athlete jose-moll
```

Options: `--name` sets the title (default e.g. "Zwift ride (imported)"), `--ftp` the
watts used for IF/TSS (default: the FTP Garmin used on their latest ride), and
`--force` imports even when Garmin already has an activity of that sport within
15 minutes. The activity is then treated like any other in the dashboard:

- Heart rate comes from the file, or, when it has none (Zwift often doesn't), from
  the athlete's watch all-day readings during the session (one every ~2 minutes).
- Garmin's training load can't be recomputed, so it is estimated from heart rate
  (Banister TRIMP), scaled by the athlete's median ratio of Garmin load to TRIMP
  over their own Garmin activities. Without any heart rate there is no load.
- Local time uses the athlete's time zone from their latest Garmin activity.

Imported activities have negative ids: syncs never touch them and re-importing the
same file replaces it. If the same session later reaches Garmin Connect too, it will
show twice; remove the import with
`sqlite3 data/athletes/<id>/garmin.sqlite "DELETE FROM activities WHERE id = <id>"`.

Each athlete's data is cached in `data/athletes/<id>/garmin.sqlite` (gitignored).
The first sync pulls everything since 2024-01-01; later syncs re-fetch only the
last few days. The page also has a Sync button and syncs the selected athlete
automatically when their data is over an hour old.

A **Today** panel at the top always shows this morning's readiness, whatever the
range: HRV vs your baseline band, resting HR vs your 28-day average, sleep, Body
Battery, yesterday's load, form and ACWR. Each trips a warning when it is off
(HRV below baseline or Garmin status LOW, resting HR 3+ bpm above average, sleep
score under 60 or under 6h, ACWR over 1.5, form below -30% of fitness), giving a
cue: 0 warnings = good to go, 1 = keep it moderate, 2+ = easy day or rest.

What it shows for the selected range (7d / 14d / 21d / 28d / 90d / 6m / 1y / custom;
14d and 21d fit a typical 2-3 week training block):

- **KPIs** with change vs the previous period of the same length.
- **Training vs recovery**: daily load or hours by sport (Swim / Bike / Run /
  Strength / Other), stacked over HRV (with your baseline band), resting HR, sleep
  hours and sleep score on the same dates.
- **Load model** from Garmin's per-activity training load: fitness (42-day EWMA),
  fatigue (7-day EWMA), form (yesterday's fitness - fatigue) and ACWR (7-day vs
  28-day mean load). These always use the full history, not just the range.
- **Weekly volume** by sport and **intensity distribution** (time in HR zones 1-5).
- **Next-day response**: days are rest / easy / hard (hard = top 25% of your
  training-day loads); shows how the following morning's HRV, resting HR and
  sleep compare with the range average.
