import base64
import datetime as dt
import json
import time
from pathlib import Path

import pytest

from garmin_client import auth
from garmin_client.auth import get_access_token, jwt_expiry
from garmin_client.client import date_windows
from garmin_client.config import Settings


def make_jwt(exp: float) -> str:
    def b64(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64({'exp': exp})}.sig"


def make_settings(tmp_path: Path, key: str = "key-1") -> Settings:
    return Settings(api_key=key, base_url="https://example.test", token_file=tmp_path / "token.json")


def test_jwt_expiry_reads_exp_claim():
    assert jwt_expiry(make_jwt(1234567890)) == 1234567890
    with pytest.raises(RuntimeError):
        jwt_expiry("not-a-jwt")


def test_date_windows_are_inclusive_and_cover_range():
    windows = list(date_windows("2026-01-01", "2026-01-17", 7))
    assert windows == [
        (dt.date(2026, 1, 1), dt.date(2026, 1, 7)),
        (dt.date(2026, 1, 8), dt.date(2026, 1, 14)),
        (dt.date(2026, 1, 15), dt.date(2026, 1, 17)),
    ]
    assert list(date_windows("2026-01-02", "2026-01-01", 7)) == []


def test_token_is_cached_until_close_to_expiry(tmp_path, monkeypatch):
    calls = []

    def fake_exchange(settings):
        calls.append(settings.api_key)
        return make_jwt(time.time() + 24 * 3600)

    monkeypatch.setattr(auth, "_exchange_key", fake_exchange)
    settings = make_settings(tmp_path)

    first = get_access_token(settings)
    assert get_access_token(settings) == first
    assert len(calls) == 1
    assert "key-1" not in settings.token_file.read_text()

    # A different key must not reuse the cached token.
    get_access_token(make_settings(tmp_path, key="key-2"))
    assert calls == ["key-1", "key-2"]


def test_stale_token_is_exchanged_again(tmp_path, monkeypatch):
    tokens = iter([make_jwt(time.time() + 600), make_jwt(time.time() + 24 * 3600)])
    monkeypatch.setattr(auth, "_exchange_key", lambda settings: next(tokens))
    settings = make_settings(tmp_path)

    first = get_access_token(settings)
    assert get_access_token(settings) != first
