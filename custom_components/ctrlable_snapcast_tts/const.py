"""Constants for Ctrlable Snapcast TTS."""
from __future__ import annotations

DOMAIN = "ctrlable_snapcast_tts"

CONF_ADDON_URL = "addon_url"
CONF_BEARER_TOKEN = "bearer_token"

DEFAULT_ADDON_URL = "http://localhost:8099"

SCHEMA_VERSION = 1

EVENT_ANNOUNCED = f"{DOMAIN}_announced"

PLATFORMS = ["binary_sensor"]

# Per-satellite announcing state. Dispatcher rather than the event bus because
# entities need a direct callback, and ESPHome can only mirror entity state --
# it cannot subscribe to HA events, which is the whole reason the binary_sensor
# platform exists.
SIGNAL_ANNOUNCING = f"{DOMAIN}_announcing_{{}}"
SIGNAL_NEW_SATELLITE = f"{DOMAIN}_new_satellite"
