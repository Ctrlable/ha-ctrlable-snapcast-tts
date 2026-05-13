"""Constants for Ctrlable Snapcast TTS."""
from __future__ import annotations

DOMAIN = "ctrlable_snapcast_tts"

CONF_ADDON_URL = "addon_url"
CONF_BEARER_TOKEN = "bearer_token"

DEFAULT_ADDON_URL = "http://localhost:8099"

SCHEMA_VERSION = 1

EVENT_ANNOUNCED = f"{DOMAIN}_announced"
