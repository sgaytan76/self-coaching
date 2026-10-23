# self-coaching
self coaching tools

## Garmin connection

Uses the official Garmin Connect Developer Program API (OAuth2 with PKCE).

1. Install dependencies: `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` (already gitignored) and fill in `GARMIN_CLIENT_ID`
   and `GARMIN_CLIENT_SECRET` from the developer portal.
3. Make sure `GARMIN_REDIRECT_URI` (default `http://localhost:8080/callback`) is
   registered as a redirect URL for your app in the portal.
4. Connect your account: `python -m garmin_client auth` — approve access in the
   browser; tokens are saved to `.garmin_tokens.json` (gitignored) and refreshed
   automatically.
5. Verify: `python -m garmin_client test` — prints your Garmin user ID, granted
   permissions and the latest daily summary.

Historical data: `GarminClient.request_backfill(...)` asks Garmin to re-send past
summaries, but Garmin delivers them asynchronously to the push/ping webhook
configured for your app, so a receiver is needed to collect them.
