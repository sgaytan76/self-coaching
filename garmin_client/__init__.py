"""Read-only client for the Garmin Connect Personal API."""

from .client import GarminClient
from .config import Settings

__all__ = ["GarminClient", "Settings"]
