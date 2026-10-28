"""Tiny local HTTP server for the dashboard (standard library only)."""

from __future__ import annotations

import datetime as dt
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from garmin_client import GarminClient

from . import athletes, metrics, store, sync

STATIC = Path(__file__).parent / "static"
STALE_AFTER = dt.timedelta(hours=1)


class AthleteState:
    """One athlete's database connection and sync status."""

    def __init__(self, athlete: athletes.Athlete):
        self.athlete = athlete
        self.conn = store.connect(athlete.db_path)
        self.lock = threading.Lock()
        self.sync_error: str | None = None

    def sync(self) -> dict:
        with self.lock:
            try:
                counts = sync.sync(self.conn, GarminClient(self.athlete.settings()),
                                   log=lambda msg: print(f"[{self.athlete.id}] {msg}"))
                self.sync_error = None
                return counts
            except Exception as exc:  # surface Garmin/network errors in the page
                self.sync_error = str(exc)
                raise

    def status(self) -> dict:
        last = sync.last_sync_at(self.conn)
        return {
            "id": self.athlete.id,
            "name": self.athlete.name,
            "last_sync_at": last.isoformat() if last else None,
            "stale": last is None or dt.datetime.now() - last > STALE_AFTER,
            "error": self.sync_error,
        }


class Registry:
    """Athlete states, refreshed from athletes.json so new athletes show up without a restart."""

    def __init__(self):
        self._states: dict[str, AthleteState] = {}
        self._lock = threading.Lock()

    def all(self) -> list[AthleteState]:
        with self._lock:
            current = athletes.load()
            ids = {a.id for a in current}
            for stale_id in set(self._states) - ids:
                self._states.pop(stale_id).conn.close()
            for a in current:
                # Recreate the state if the athlete's key was changed.
                if a.id not in self._states or self._states[a.id].athlete != a:
                    self._states[a.id] = AthleteState(a)
            return [self._states[a.id] for a in current]

    def get(self, athlete_id: str) -> AthleteState | None:
        return next((s for s in self.all() if s.athlete.id == athlete_id), None)


def make_handler(registry: Registry):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status: int = 200) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _athlete(self, query: dict) -> AthleteState | None:
            state = registry.get(query.get("athlete", [athletes.SELF_ID])[0])
            if state is None:
                self._json({"error": "Unknown athlete"}, 404)
            return state

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path in ("/", "/index.html"):
                self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/athletes":
                self._json([s.status() for s in registry.all()])
            elif url.path == "/api/overview":
                today, out = dt.date.today(), []
                for state in registry.all():
                    with state.lock:
                        out.append({**state.status(), **metrics.athlete_summary(state.conn, today)})
                self._json(out)
            elif url.path == "/api/status":
                state = self._athlete(query)
                if state:
                    self._json(state.status())
            elif url.path == "/api/data":
                state = self._athlete(query)
                if state is None:
                    return
                try:
                    end = dt.date.fromisoformat(query.get("end", [dt.date.today().isoformat()])[0])
                    start = dt.date.fromisoformat(
                        query.get("start", [(end - dt.timedelta(days=27)).isoformat()])[0]
                    )
                except ValueError:
                    return self._json({"error": "start/end must be YYYY-MM-DD"}, 400)
                if start > end:
                    return self._json({"error": "start must be on or before end"}, 400)
                with state.lock:
                    data = metrics.dashboard_data(state.conn, start, end)
                data["status"] = state.status()
                self._json(data)
            else:
                self._send(404, b"Not found", "text/plain")

        def do_POST(self):
            url = urlparse(self.path)
            if url.path != "/api/sync":
                return self._send(404, b"Not found", "text/plain")
            state = self._athlete(parse_qs(url.query))
            if state is None:
                return
            try:
                counts = state.sync()
                self._json({"ok": True, "counts": counts, **state.status()})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 502)

        def log_message(self, *args):
            pass

    return Handler


def serve(port: int = 8000, sync_on_start: bool = True) -> None:
    if athletes.migrate_legacy_data():
        print("Moved data/garmin.sqlite to data/athletes/me/")
    registry = Registry()
    states = registry.all()
    if not states:
        print("No athletes yet: set GARMIN_PERSONAL_API_KEY in .env or run "
              "`python -m dashboard athletes add NAME`.")
    if sync_on_start:
        for state in states:
            if state.status()["stale"]:
                try:
                    state.sync()
                except Exception as exc:
                    print(f"[{state.athlete.id}] Sync failed, serving cached data: {exc}")
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(registry))
    print(f"Dashboard running at http://localhost:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
