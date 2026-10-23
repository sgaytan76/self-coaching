import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    redirect_uri: str
    auth_url: str
    token_url: str
    api_base: str
    token_file: Path

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env")
        client_id = os.getenv("GARMIN_CLIENT_ID", "").strip()
        client_secret = os.getenv("GARMIN_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise RuntimeError(
                "GARMIN_CLIENT_ID and GARMIN_CLIENT_SECRET must be set in .env "
                "(see .env.example)."
            )
        token_file = Path(os.getenv("GARMIN_TOKEN_FILE", ".garmin_tokens.json"))
        if not token_file.is_absolute():
            token_file = PROJECT_ROOT / token_file
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=os.getenv("GARMIN_REDIRECT_URI", "http://localhost:8080/callback"),
            auth_url=os.getenv("GARMIN_AUTH_URL", "https://connect.garmin.com/oauth2Confirm"),
            token_url=os.getenv(
                "GARMIN_TOKEN_URL", "https://diauth.garmin.com/di-oauth2-service/oauth/token"
            ),
            api_base=os.getenv("GARMIN_API_BASE", "https://apis.garmin.com").rstrip("/"),
            token_file=token_file,
        )
