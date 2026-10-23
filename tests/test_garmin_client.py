import base64
import hashlib

from garmin_client.auth import make_pkce_pair, _extract_code
from garmin_client.client import time_windows

import pytest


def test_pkce_challenge_matches_verifier():
    verifier, challenge = make_pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert challenge == expected.decode()


def test_time_windows_split_and_cover_range():
    assert list(time_windows(0, 250, 100)) == [(0, 100), (100, 200), (200, 250)]
    assert list(time_windows(5, 5, 100)) == []


def test_extract_code_checks_state():
    assert _extract_code("/callback?code=abc&state=s1", "s1") == "abc"
    with pytest.raises(RuntimeError):
        _extract_code("/callback?code=abc&state=other", "s1")
    with pytest.raises(RuntimeError):
        _extract_code("/callback?error=access_denied", "s1")
