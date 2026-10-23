"""OAuth2 Authorization Code + PKCE flow and token storage for Garmin."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .config import Settings

# Refresh this many seconds before the access token actually expires.
EXPIRY_MARGIN = 300


def make_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using the S256 method."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class TokenStore:
    def __init__(self, path):
        self.path = path

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text())

    def save(self, token: dict) -> None:
        token = dict(token)
        if "expires_in" in token:
            token["expires_at"] = int(time.time()) + int(token["expires_in"])
        if "refresh_token_expires_in" in token:
            token["refresh_expires_at"] = int(time.time()) + int(token["refresh_token_expires_in"])
        self.path.write_text(json.dumps(token, indent=2))
        self.path.chmod(0o600)


def _post_token(settings: Settings, data: dict) -> dict:
    resp = requests.post(
        settings.token_url,
        data={**data, "client_id": settings.client_id, "client_secret": settings.client_secret},
        headers={"Accept": "application/json"},
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Token request failed ({resp.status_code}): {resp.text}")
    return resp.json()


def authorize(settings: Settings, open_browser: bool = True) -> dict:
    """Run the interactive consent flow and persist the resulting tokens."""
    verifier, challenge = make_pkce_pair()
    state = secrets.token_urlsafe(16)
    url = settings.auth_url + "?" + urlencode(
        {
            "response_type": "code",
            "client_id": settings.client_id,
            "redirect_uri": settings.redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
    )

    code = _wait_for_code(settings.redirect_uri, url, state, open_browser)
    token = _post_token(
        settings,
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": settings.redirect_uri,
        },
    )
    TokenStore(settings.token_file).save(token)
    return token


def _wait_for_code(redirect_uri: str, auth_url: str, state: str, open_browser: bool) -> str:
    redirect = urlparse(redirect_uri)
    if redirect.hostname not in ("localhost", "127.0.0.1"):
        # Not a local redirect: let the user paste the URL they landed on.
        print(f"Open this URL, approve access, then paste the full redirect URL here:\n\n{auth_url}\n")
        return _extract_code(input("Redirect URL: ").strip(), state)

    result: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if urlparse(self.path).path != redirect.path:
                self.send_response(404)
                self.end_headers()
                return
            try:
                result["code"] = _extract_code(self.path, state)
                body = b"Garmin connected. You can close this tab."
                self.send_response(200)
            except RuntimeError as exc:
                result["error"] = str(exc)
                body = f"Authorization failed: {exc}".encode()
                self.send_response(400)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, *args):
            pass

    server = HTTPServer((redirect.hostname, redirect.port or 80), Handler)
    print(f"Opening Garmin consent page. If the browser does not open, visit:\n\n{auth_url}\n")
    if open_browser:
        webbrowser.open(auth_url)
    server.serve_forever()
    server.server_close()
    if "error" in result:
        raise RuntimeError(result["error"])
    return result["code"]


def _extract_code(url: str, expected_state: str) -> str:
    params = parse_qs(urlparse(url).query)
    if "error" in params:
        raise RuntimeError(params["error"][0])
    if params.get("state", [None])[0] != expected_state:
        raise RuntimeError("State mismatch in OAuth callback")
    if "code" not in params:
        raise RuntimeError("No authorization code in callback")
    return params["code"][0]


def get_access_token(settings: Settings) -> str:
    """Return a valid access token, refreshing it if it is about to expire."""
    store = TokenStore(settings.token_file)
    token = store.load()
    if token is None:
        raise RuntimeError("Not connected yet. Run: python -m garmin_client auth")
    if token.get("expires_at", 0) - EXPIRY_MARGIN > time.time():
        return token["access_token"]
    token = _post_token(
        settings, {"grant_type": "refresh_token", "refresh_token": token["refresh_token"]}
    )
    store.save(token)
    return token["access_token"]
