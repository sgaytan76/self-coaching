"""Command line entry point: python -m garmin_client {auth,test}."""

import argparse
import json
import time

from .auth import authorize
from .client import GarminClient
from .config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m garmin_client")
    sub = parser.add_subparsers(dest="command", required=True)
    auth = sub.add_parser("auth", help="Connect your Garmin account (OAuth2 consent)")
    auth.add_argument("--no-browser", action="store_true", help="Print the URL instead of opening it")
    sub.add_parser("test", help="Check the connection and pull the last 24h of dailies")
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.command == "auth":
        authorize(settings, open_browser=not args.no_browser)
        print(f"Connected. Tokens saved to {settings.token_file}")
    elif args.command == "test":
        client = GarminClient(settings)
        print("User ID:     ", client.user_id())
        print("Permissions: ", ", ".join(client.permissions()))
        dailies = client.pull("dailies", int(time.time()) - 86400)
        print(f"Dailies uploaded in the last 24h: {len(dailies)}")
        if dailies:
            print(json.dumps(dailies[-1], indent=2)[:1500])


if __name__ == "__main__":
    main()
