"""Command line entry point: python -m garmin_client test."""

import argparse
import datetime as dt
import json

from .client import GarminClient
from .config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m garmin_client")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("test", help="Check the API key and pull the last few days of data")
    args = parser.parse_args()

    client = GarminClient(Settings.from_env())
    if args.command == "test":
        profile = client.profile_settings()
        print("Connected. Profile keys:", ", ".join(sorted(profile)[:10]), "...")

        today = dt.date.today()
        dailies = client.daily_summaries(today - dt.timedelta(days=6), today)
        print(f"Daily summaries (last 7 days): {len(dailies)}")
        if dailies:
            print(json.dumps(dailies[-1], indent=2)[:1500])

        activities = client.activities(limit=5)
        print(f"\nLatest {len(activities)} activities:")
        for a in activities:
            print(f"  {a.get('startTimeLocal')}  {a.get('activityType', {}).get('typeKey')}  "
                  f"{a.get('activityName')}  (id {a.get('activityId')})")


if __name__ == "__main__":
    main()
