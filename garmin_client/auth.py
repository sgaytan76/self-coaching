"""Exchange a Garmin Personal API key for a short-lived bearer token, with caching.

The token endpoint returns a raw JWT valid for up to 24h. There are no refresh
tokens: when the cached token is close to expiry we simply exchange the key again.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time

import requests

from .config import Settings

TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"

# Exchange the key again when the cached token has less than this many seconds left.
REFRESH_MARGIN = 3600


def jwt_expiry(token: str) -> float:
    """Return the `exp` claim of a JWT (the signature is not verified)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, ValueError, KeyError) as exc:
        raise RuntimeError("Token endpoint did not return a JWT with an exp claim") from exc


def key_fingerprint(settings: Settings) -> str:
    """Identify the key + host a cached token belongs to without storing the key."""
    return hashlib.sha256(f"{settings.base_url}\0{settings.api_key}".encode()).hexdigest()


def _load_cached(settings: Settings) -> dict | None:
    if not settings.token_file.exists():
        return None
    try:
        cached = json.loads(settings.token_file.read_text())
    except ValueError:
        return None
    if cached.get("fingerprint") != key_fingerprint(settings):
        return None
    return cached


def _exchange_key(settings: Settings) -> str:
    resp = requests.post(
        settings.base_url + TOKEN_PATH,
        headers={"Accept": "*/*", "Authorization": f"ApiKey {settings.api_key}"},
        timeout=30,
    )
    if resp.status_code == 401:
        raise RuntimeError(
            "Token exchange failed (401): the Personal API key is wrong, revoked or expired."
        )
    if not resp.ok:
        raise RuntimeError(f"Token exchange failed ({resp.status_code}): {resp.text}")
    return resp.text.strip()


def get_access_token(settings: Settings, force_refresh: bool = False) -> str:
    """Return a bearer token, exchanging the API key if the cached one is stale."""
    cached = None if force_refresh else _load_cached(settings)
    if cached and cached["expires_at"] - time.time() > REFRESH_MARGIN:
        return cached["access_token"]

    token = _exchange_key(settings)
    settings.token_file.write_text(
        json.dumps(
            {
                "fingerprint": key_fingerprint(settings),
                "access_token": token,
                "expires_at": jwt_expiry(token),
            },
            indent=2,
        )
    )
    settings.token_file.chmod(0o600)
    return token
