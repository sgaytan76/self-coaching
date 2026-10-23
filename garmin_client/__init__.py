"""Client for the Garmin Connect Developer Program (Health / Activity API)."""

from .client import GarminClient
from .config import Settings

__all__ = ["GarminClient", "Settings"]
