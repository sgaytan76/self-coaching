"""python -m dashboard [serve|sync] — run the training load dashboard."""

import argparse

from . import server, store, sync


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m dashboard")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Run the dashboard (default)")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--no-sync", action="store_true", help="Skip the startup sync")
    sub.add_parser("sync", help="Pull new Garmin data into the local store and exit")
    args = parser.parse_args()

    if args.command == "sync":
        sync.sync(store.connect())
    else:
        server.serve(
            port=getattr(args, "port", 8000), sync_on_start=not getattr(args, "no_sync", False)
        )


if __name__ == "__main__":
    main()
