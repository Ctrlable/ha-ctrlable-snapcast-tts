"""Tests for satellite → snapclient mapping resolution."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "ctrlable_snapcast_tts"))

import pytest
from mapping import NoMatchingMappingError, SatelliteNotMappedError, resolve, upsert


SATELLITE = "m5stack-atom-echo-a14640"
CLIENT_LR = "snapclient-announcement-6#16"
CLIENT_BR = "snapclient-announcement-0#10"

MAPPINGS = [
    {"satellite_id": SATELLITE, "wake_word": "okay_nabu", "target_snapclient_ids": [CLIENT_LR], "notes": ""},
    {"satellite_id": SATELLITE, "wake_word": "hey_jarvis", "target_snapclient_ids": [CLIENT_BR], "notes": ""},
]


# ── Wake word normalisation ───────────────────────────────────────────────────

def test_exact_underscore_match():
    """User stored 'okay_nabu'; ESPHome sends 'okay_nabu' — should match."""
    assert resolve(MAPPINGS, SATELLITE, "okay_nabu") == [CLIENT_LR]


def test_esphome_space_matches_underscore():
    """ESPHome sends 'okay nabu' (space); mapping stores 'okay_nabu' — must match."""
    assert resolve(MAPPINGS, SATELLITE, "okay nabu") == [CLIENT_LR]


def test_case_insensitive():
    """Wake word comparison is case-insensitive."""
    assert resolve(MAPPINGS, SATELLITE, "Okay Nabu") == [CLIENT_LR]
    assert resolve(MAPPINGS, SATELLITE, "HEY_JARVIS") == [CLIENT_BR]


def test_hey_jarvis_space():
    """ESPHome sends 'hey jarvis'; mapping stores 'hey_jarvis'."""
    assert resolve(MAPPINGS, SATELLITE, "hey jarvis") == [CLIENT_BR]


def test_specific_beats_wildcard():
    """Specific wake word row wins over wildcard when both exist."""
    mappings = [
        {"satellite_id": SATELLITE, "wake_word": "okay_nabu", "target_snapclient_ids": [CLIENT_LR], "notes": ""},
        {"satellite_id": SATELLITE, "wake_word": "*", "target_snapclient_ids": [CLIENT_BR], "notes": ""},
    ]
    assert resolve(mappings, SATELLITE, "okay nabu") == [CLIENT_LR]
    assert resolve(mappings, SATELLITE, "okay_nabu") == [CLIENT_LR]


def test_wildcard_fallback_when_no_wakeword():
    """Empty wake word (button press) falls through to wildcard."""
    mappings = [
        {"satellite_id": SATELLITE, "wake_word": "*", "target_snapclient_ids": [CLIENT_LR], "notes": ""},
    ]
    assert resolve(mappings, SATELLITE, "") == [CLIENT_LR]
    assert resolve(mappings, SATELLITE, None) == [CLIENT_LR]


def test_wildcard_fallback_unknown_wakeword():
    """Unknown wake word with a wildcard row falls back to wildcard."""
    mappings = [
        {"satellite_id": SATELLITE, "wake_word": "okay_nabu", "target_snapclient_ids": [CLIENT_LR], "notes": ""},
        {"satellite_id": SATELLITE, "wake_word": "*", "target_snapclient_ids": [CLIENT_BR], "notes": ""},
    ]
    assert resolve(mappings, SATELLITE, "unknown phrase") == [CLIENT_BR]


def test_satellite_not_mapped():
    with pytest.raises(SatelliteNotMappedError):
        resolve(MAPPINGS, "nonexistent-satellite", "okay nabu")


def test_no_matching_mapping():
    """Satellite known, wake word not matched, no wildcard."""
    mappings = [
        {"satellite_id": SATELLITE, "wake_word": "okay_nabu", "target_snapclient_ids": [CLIENT_LR], "notes": ""},
    ]
    with pytest.raises(NoMatchingMappingError):
        resolve(mappings, SATELLITE, "unknown phrase")


# ── Upsert ────────────────────────────────────────────────────────────────────

def test_upsert_add():
    result = upsert([], SATELLITE, "okay_nabu", [CLIENT_LR])
    assert len(result) == 1
    assert result[0]["wake_word"] == "okay_nabu"


def test_upsert_replace():
    initial = [{"satellite_id": SATELLITE, "wake_word": "okay_nabu", "target_snapclient_ids": [CLIENT_BR], "notes": ""}]
    result = upsert(initial, SATELLITE, "okay_nabu", [CLIENT_LR])
    assert len(result) == 1
    assert result[0]["target_snapclient_ids"] == [CLIENT_LR]
