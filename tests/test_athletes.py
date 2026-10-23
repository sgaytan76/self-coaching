import json
import stat

import pytest

from dashboard import athletes


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(athletes, "ATHLETES_FILE", tmp_path / "athletes.json")
    monkeypatch.setattr(athletes, "DATA_ROOT", tmp_path / "data" / "athletes")
    monkeypatch.setattr(athletes, "LEGACY_DB", tmp_path / "data" / "garmin.sqlite")
    monkeypatch.setattr(athletes, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "own-key")
    return tmp_path


def test_self_comes_first_from_env():
    athletes.add("Ana López", "ana-key")
    found = athletes.load()
    assert [(a.id, a.name, a.api_key) for a in found] == [
        ("me", "Me", "own-key"),
        ("ana-lopez", "Ana López", "ana-key"),
    ]
    assert found[0].is_self and not found[1].is_self


def test_no_self_without_env_key(monkeypatch):
    monkeypatch.delenv("GARMIN_PERSONAL_API_KEY")
    assert athletes.load() == []


def test_ids_are_unique_and_never_me():
    assert athletes.add("Ana", "k1").id == "ana"
    assert athletes.add("Ana", "k2").id == "ana-2"
    assert athletes.add("Me", "k3").id == "me-2"


def test_file_is_private():
    athletes.add("Ana", "secret")
    mode = stat.S_IMODE(athletes.ATHLETES_FILE.stat().st_mode)
    assert mode == 0o600
    assert json.loads(athletes.ATHLETES_FILE.read_text())["athletes"][0]["api_key"] == "secret"


def test_each_athlete_has_own_folder():
    ana = athletes.add("Ana", "k1")
    me = athletes.get("me")
    assert ana.db_path.parent != me.db_path.parent
    assert ana.settings().token_file == athletes.DATA_ROOT / "ana" / "token.json"
    assert ana.settings().api_key == "k1"


def test_update_key_and_remove():
    athletes.add("Ana", "old")
    athletes.update_key("ana", "new")
    assert athletes.get("ana").api_key == "new"

    folder = athletes.get("ana").folder
    folder.mkdir(parents=True)
    athletes.remove("ana", delete_data=True)
    assert athletes.get("ana") is None and not folder.exists()

    with pytest.raises(ValueError):
        athletes.remove("me")
    with pytest.raises(KeyError):
        athletes.remove("nobody")


def test_add_requires_name_and_key():
    with pytest.raises(ValueError):
        athletes.add("Ana", "  ")


def test_legacy_database_moves_to_me():
    athletes.LEGACY_DB.parent.mkdir(parents=True)
    athletes.LEGACY_DB.write_bytes(b"db")
    assert athletes.migrate_legacy_data()
    assert (athletes.DATA_ROOT / "me" / "garmin.sqlite").read_bytes() == b"db"
    assert not athletes.LEGACY_DB.exists()
    assert not athletes.migrate_legacy_data()  # nothing left to move


def test_data_folder_is_private():
    athletes.load()
    assert stat.S_IMODE(athletes.DATA_ROOT.stat().st_mode) == 0o700
