import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    token_file: Path

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env")
        api_key = os.getenv("GARMIN_PERSONAL_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "GARMIN_PERSONAL_API_KEY must be set in .env (see .env.example). Create a key at "
                "https://connect.garmin.com/app/settings/personalApiToken"
            )
        token_file = Path(os.getenv("GARMIN_TOKEN_FILE", ".garmin_token.json"))
        if not token_file.is_absolute():
            token_file = PROJECT_ROOT / token_file
        return cls(
            api_key=api_key,
            base_url=os.getenv(
                "GARMIN_PERSONAL_API_BASE_URL", "https://personal.connectapi.garmin.com"
            ).rstrip("/"),
            token_file=token_file,
        )
