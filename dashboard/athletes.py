"""Athletes the dashboard can show, each with their own Garmin Personal API key.

You (the coach) are always the athlete "me", using GARMIN_PERSONAL_API_KEY from
.env. Athletes you coach are kept in athletes.json (gitignored, readable only by
your user), and every athlete gets their own folder under data/athletes/<id>/
for their database and token cache, so nobody's data is ever mixed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from garmin_client.config import PROJECT_ROOT, Settings

ATHLETES_FILE = PROJECT_ROOT / "athletes.json"
DATA_ROOT = PROJECT_ROOT / "data" / "athletes"
LEGACY_DB = PROJECT_ROOT / "data" / "garmin.sqlite"
SELF_ID = "me"


@dataclass(frozen=True)
class Athlete:
    id: str
    name: str
    api_key: str

    @property
    def is_self(self) -> bool:
        return self.id == SELF_ID

    @property
    def folder(self) -> Path:
        return DATA_ROOT / self.id

    @property
    def db_path(self) -> Path:
        return self.folder / "garmin.sqlite"

    def settings(self) -> Settings:
        self.folder.mkdir(parents=True, exist_ok=True)
        return Settings(
            api_key=self.api_key,
            base_url=os.getenv(
                "GARMIN_PERSONAL_API_BASE_URL", "https://personal.connectapi.garmin.com"
            ).rstrip("/"),
            token_file=self.folder / "token.json",
        )


def _read_file() -> list[dict]:
    if not ATHLETES_FILE.exists():
        return []
    return json.loads(ATHLETES_FILE.read_text()).get("athletes", [])


def _write_file(entries: list[dict]) -> None:
    ATHLETES_FILE.write_text(json.dumps({"athletes": entries}, indent=2))
    ATHLETES_FILE.chmod(0o600)


def load() -> list[Athlete]:
    """Everyone the dashboard can show: you first, then your athletes in the order added."""
    load_dotenv(PROJECT_ROOT / ".env")
    # Athletes' health data: only your user may open the folder that holds it.
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    DATA_ROOT.chmod(0o700)
    athletes = []
    own_key = os.getenv("GARMIN_PERSONAL_API_KEY", "").strip()
    if own_key:
        athletes.append(Athlete(SELF_ID, "Me", own_key))
    athletes += [Athlete(e["id"], e["name"], e["api_key"]) for e in _read_file()]
    return athletes


def get(athlete_id: str) -> Athlete | None:
    return next((a for a in load() if a.id == athlete_id), None)


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-") or "athlete"


def add(name: str, api_key: str) -> Athlete:
    name, api_key = name.strip(), api_key.strip()
    if not name or not api_key:
        raise ValueError("Both a name and an API key are required.")
    entries = _read_file()
    taken = {SELF_ID} | {e["id"] for e in entries}
    base = slugify(name)
    athlete_id, n = base, 2
    while athlete_id in taken:
        athlete_id, n = f"{base}-{n}", n + 1
    entries.append({"id": athlete_id, "name": name, "api_key": api_key})
    _write_file(entries)
    return Athlete(athlete_id, name, api_key)


def update_key(athlete_id: str, api_key: str) -> None:
    """Replace an athlete's key, e.g. after their old one expired."""
    entries = _read_file()
    for e in entries:
        if e["id"] == athlete_id:
            e["api_key"] = api_key.strip()
            _write_file(entries)
            return
    raise KeyError(athlete_id)


def remove(athlete_id: str, delete_data: bool = False) -> None:
    if athlete_id == SELF_ID:
        raise ValueError("'me' comes from GARMIN_PERSONAL_API_KEY in .env; remove it there.")
    entries = _read_file()
    remaining = [e for e in entries if e["id"] != athlete_id]
    if len(remaining) == len(entries):
        raise KeyError(athlete_id)
    _write_file(remaining)
    if delete_data:
        shutil.rmtree(DATA_ROOT / athlete_id, ignore_errors=True)


def migrate_legacy_data() -> bool:
    """Move the single-athlete database (data/garmin.sqlite) into data/athletes/me/."""
    target = DATA_ROOT / SELF_ID / "garmin.sqlite"
    if not LEGACY_DB.exists() or target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    LEGACY_DB.rename(target)
    return True
