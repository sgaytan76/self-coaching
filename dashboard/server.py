"""Tiny local HTTP server for the dashboard (standard library only)."""

from __future__ import annotations

import datetime as dt
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import metrics, store, sync

STATIC = Path(__file__).parent / "static"
STALE_AFTER = dt.timedelta(hours=1)


class State:
    def __init__(self):
        self.conn = store.connect()
        self.lock = threading.Lock()
        self.sync_error: str | None = None

    def sync(self) -> dict:
        with self.lock:
            try:
                counts = sync.sync(self.conn)
                self.sync_error = None
                return counts
            except Exception as exc:  # surface Garmin/network errors in the page
                self.sync_error = str(exc)
                raise

    def status(self) -> dict:
        last = sync.last_sync_at(self.conn)
        return {
            "last_sync_at": last.isoformat() if last else None,
            "stale": last is None or dt.datetime.now() - last > STALE_AFTER,
            "error": self.sync_error,
        }


def make_handler(state: State):
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

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/status":
                self._json(state.status())
            elif url.path == "/api/data":
                query = parse_qs(url.query)
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
            if urlparse(self.path).path != "/api/sync":
                return self._send(404, b"Not found", "text/plain")
            try:
                counts = state.sync()
                self._json({"ok": True, "counts": counts, **state.status()})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 502)

        def log_message(self, *args):
            pass

    return Handler


def serve(port: int = 8000, sync_on_start: bool = True) -> None:
    state = State()
    if sync_on_start and state.status()["stale"]:
        try:
            state.sync()
        except Exception as exc:
            print(f"Sync failed, serving cached data: {exc}")
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    print(f"Dashboard running at http://localhost:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
