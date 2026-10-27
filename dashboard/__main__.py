"""python -m dashboard [serve|sync|athletes|import-fit] — run the training load dashboard."""

import argparse
import getpass
import sys
from pathlib import Path

from garmin_client import GarminClient

from . import athletes, fitimport, server, store, sync


def prompt_key(name: str) -> str:
    # getpass keeps the key off the screen and out of shell history.
    key = getpass.getpass(f"Paste {name}'s Garmin Personal API key (input hidden): ").strip()
    if not key:
        sys.exit("No key entered.")
    return key


def check_key(athlete: athletes.Athlete) -> str | None:
    """Exchange the key for a token and read the profile. Returns the Garmin display name."""
    return GarminClient(athlete.settings()).profile_settings().get("displayName")


def sync_athletes(athlete_id: str | None) -> None:
    athletes.migrate_legacy_data()
    targets = athletes.load()
    if athlete_id:
        targets = [a for a in targets if a.id == athlete_id]
        if not targets:
            sys.exit(f"Unknown athlete '{athlete_id}'. See: python -m dashboard athletes list")
    for a in targets:
        try:
            sync.sync(store.connect(a.db_path), GarminClient(a.settings()),
                      log=lambda msg, a=a: print(f"[{a.id}] {msg}"))
        except Exception as exc:
            print(f"[{a.id}] Sync failed: {exc}")


def athletes_command(args) -> None:
    if args.action == "list":
        found = athletes.load()
        if not found:
            print("No athletes. Add one with: python -m dashboard athletes add NAME")
        for a in found:
            source = ".env" if a.is_self else "athletes.json"
            print(f"{a.id:<20} {a.name:<24} key from {source}")

    elif args.action == "add":
        key = prompt_key(args.name)
        athlete = athletes.add(args.name, key)
        try:
            display = check_key(athlete)
        except Exception as exc:
            athletes.remove(athlete.id, delete_data=True)
            sys.exit(f"That key did not work, nothing was saved: {exc}")
        print(f"Added '{athlete.name}' as '{athlete.id}' (Garmin user: {display}).")
        print(f"Pull their history with: python -m dashboard sync --athlete {athlete.id}")

    elif args.action == "update-key":
        athlete = athletes.get(args.id)
        if athlete is None or athlete.is_self:
            sys.exit(f"Unknown athlete '{args.id}' (your own key lives in .env).")
        old_key = athlete.api_key
        athletes.update_key(args.id, prompt_key(athlete.name))
        try:
            check_key(athletes.get(args.id))
        except Exception as exc:
            athletes.update_key(args.id, old_key)
            sys.exit(f"That key did not work, kept the old one: {exc}")
        print(f"Updated {athlete.name}'s key.")

    elif args.action == "remove":
        try:
            athletes.remove(args.id, delete_data=args.delete_data)
        except (KeyError, ValueError) as exc:
            sys.exit(str(exc) if isinstance(exc, ValueError) else f"Unknown athlete '{args.id}'.")
        print(f"Removed '{args.id}'" + (" and deleted their synced data." if args.delete_data
                                         else ". Their synced data is still in data/athletes/."))


def import_fit_command(args) -> None:
    athlete = athletes.get(args.athlete)
    if athlete is None:
        sys.exit(f"Unknown athlete '{args.athlete}'. See: python -m dashboard athletes list")
    path = Path(args.file).expanduser()
    if not path.is_file():
        sys.exit(f"No such file: {path}")
    try:
        fa = fitimport.read_fit(path)
    except Exception as exc:
        sys.exit(f"Could not read {path.name} as a FIT file: {exc}")
    try:
        a = fitimport.import_fit(store.connect(athlete.db_path), GarminClient(athlete.settings()), fa,
                                 source=path.name, name=args.name, ftp=args.ftp, force=args.force)
    except ValueError as exc:
        sys.exit(str(exc))
    hours = (a["duration"] or 0) / 3600
    power = f", NP {a['normPower']:g} W" if a["normPower"] else ""
    hr = f", HR avg {a['averageHR']} max {a['maxHR']}" if a["averageHR"] else ""
    print(f"[{athlete.id}] Imported '{a['activityName']}' on {a['startTimeLocal']}: "
          f"{int(hours)}h {round(hours % 1 * 60):02d}m{power}{hr}, load {a['activityTrainingLoad']}.")
    print(f"  {a['importNote']}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m dashboard")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Run the dashboard (default)")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--no-sync", action="store_true", help="Skip the startup sync")

    sync_p = sub.add_parser("sync", help="Pull new Garmin data into the local store and exit")
    sync_p.add_argument("--athlete", help="Only this athlete id (default: everyone)")

    ath = sub.add_parser("athletes", help="Manage the athletes you coach")
    ath_sub = ath.add_subparsers(dest="action", required=True)
    ath_sub.add_parser("list", help="Show all athletes")
    add = ath_sub.add_parser("add", help="Add an athlete (prompts for their key)")
    add.add_argument("name")
    upd = ath_sub.add_parser("update-key", help="Replace an athlete's expired key")
    upd.add_argument("id")
    rm = ath_sub.add_parser("remove", help="Remove an athlete")
    rm.add_argument("id")
    rm.add_argument("--delete-data", action="store_true", help="Also delete their synced data")

    imp = sub.add_parser("import-fit", help="Add an activity from a .fit/.fit.gz file (e.g. Zwift)")
    imp.add_argument("file")
    imp.add_argument("--athlete", default=athletes.SELF_ID, help="Athlete id (default: me)")
    imp.add_argument("--name", help="Activity name (default: e.g. 'Zwift ride (imported)')")
    imp.add_argument("--ftp", type=float, help="FTP in watts for IF/TSS (default: from their latest ride)")
    imp.add_argument("--force", action="store_true",
                     help="Import even if Garmin already has an activity at that time")

    args = parser.parse_args()
    if args.command == "sync":
        sync_athletes(args.athlete)
    elif args.command == "athletes":
        athletes_command(args)
    elif args.command == "import-fit":
        import_fit_command(args)
    else:
        server.serve(
            port=getattr(args, "port", 8000), sync_on_start=not getattr(args, "no_sync", False)
        )


if __name__ == "__main__":
    main()
