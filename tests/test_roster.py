"""Candidate satellite ids derived from what Home Assistant knows.

The id a device sends (`App.get_name()`, e.g. `atoms3r-echo-bca1a8`) and the id
HA exposes (an entity slug carrying the friendly name and platform suffix) are
different strings for the same device. These tests pin the derivation so the
best guess stays the ESPHome-ish one rather than the entity slug, which no
device ever sends.
"""
from __future__ import annotations

import pathlib
import sys
import types
from unittest.mock import MagicMock

# Stub the HA helper modules so roster.py imports without Home Assistant.
for name in ("homeassistant", "homeassistant.core", "homeassistant.helpers",
             "homeassistant.helpers.area_registry",
             "homeassistant.helpers.device_registry",
             "homeassistant.helpers.entity_registry"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["homeassistant.core"].HomeAssistant = object

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]
                       / "custom_components" / "ctrlable_snapcast_tts"))
import roster  # noqa: E402


def _entity(entity_id, device_id=None, name=None, original_name=None, area_id=None,
            disabled_by=None):
    e = MagicMock()
    e.entity_id = entity_id
    e.device_id = device_id
    e.name = name
    e.original_name = original_name
    e.area_id = area_id
    # Explicitly None, not left to MagicMock. An auto-created attribute is never
    # None, so collect() would read every satellite as disabled and the tests
    # would still pass -- they simply do not assert on status.
    e.disabled_by = disabled_by
    return e


def _device(name=None, name_by_user=None, area_id=None, configuration_url=None):
    d = MagicMock()
    d.name = name
    d.name_by_user = name_by_user
    d.area_id = area_id
    d.configuration_url = configuration_url
    return d


def _hass(entities, devices=None, areas=None, states=None):
    devices = devices or {}
    areas = areas or {}
    states = states or {}
    roster.er.async_get = lambda h: types.SimpleNamespace(
        entities={e.entity_id: e for e in entities})
    roster.dr.async_get = lambda h: types.SimpleNamespace(
        async_get=lambda did: devices.get(did))
    roster.ar.async_get = lambda h: types.SimpleNamespace(
        async_get_area=lambda aid: types.SimpleNamespace(name=areas[aid]) if aid in areas else None)
    h = MagicMock()
    # Default "idle" so a test that does not care about status gets a live
    # satellite rather than a MagicMock state that compares equal to nothing.
    h.states.get = lambda eid: (
        types.SimpleNamespace(state=states.get(eid, "idle"))
        if states.get(eid, "idle") is not None else None)
    return h


def test_ignores_non_satellite_entities():
    h = _hass([_entity("sensor.temperature"), _entity("light.kitchen")])
    assert roster.collect(h) == []


def test_device_name_is_preferred_over_the_entity_slug():
    """The entity slug carries a platform suffix no device ever sends.

    `assist_satellite.atoms3r_echo_base_voice_assistant_assist_satellite` would
    yield 'atoms3r-echo-base-voice-assistant-assist-satellite'. The device
    actually sends 'atoms3r-echo-bca1a8', which the DEVICE name is far closer to.
    """
    dev = _device(name="atoms3r echo bca1a8")
    h = _hass(
        [_entity("assist_satellite.atoms3r_echo_base_voice_assistant_assist_satellite",
                 device_id="d1")],
        devices={"d1": dev},
    )
    got = roster.collect(h)[0]
    assert got["candidates"][0] == "atoms3r-echo-bca1a8"
    assert got["id"] == "atoms3r-echo-bca1a8"
    # the slug is still offered as a fallback, just not first
    assert any("assist-satellite" in c for c in got["candidates"])


def test_configuration_url_contributes_the_exact_node_name():
    dev = _device(name="CoreS3 VA", configuration_url="http://cores3-va.local")
    h = _hass([_entity("assist_satellite.cores3_va", device_id="d1")], devices={"d1": dev})
    assert "cores3-va" in roster.collect(h)[0]["candidates"]


def test_candidates_are_deduplicated_and_ordered():
    dev = _device(name="cores3 va", configuration_url="http://cores3-va.local")
    h = _hass([_entity("assist_satellite.cores3_va", device_id="d1")], devices={"d1": dev})
    c = roster.collect(h)[0]["candidates"]
    assert len(c) == len(set(c)), "duplicates must be collapsed"
    assert c[0] == "cores3-va"


def test_area_and_friendly_name_come_through():
    """The whole point of the roster: labels the streamer cannot infer."""
    dev = _device(name="CoreS3", area_id="a1")
    h = _hass([_entity("assist_satellite.cores3_va", device_id="d1", name="Kitchen Satellite")],
              devices={"d1": dev}, areas={"a1": "Kitchen"})
    got = roster.collect(h)[0]
    assert got["name"] == "Kitchen Satellite"
    assert got["area"] == "Kitchen"


def test_survives_a_satellite_with_no_device():
    h = _hass([_entity("assist_satellite.orphan")])
    got = roster.collect(h)[0]
    assert got["candidates"] == ["orphan"]
    assert got["area"] == ""


# ── Online/offline ───────────────────────────────────────────────────────────
#
# Only Home Assistant knows whether a satellite is reachable. The streamer sees
# a satellite exactly once, when it announces, which says nothing about now: a
# satellite that has dropped off wifi looks identical to a working one in every
# other view, until an announcement silently goes nowhere.

def test_status_online():
    h = _hass([_entity("assist_satellite.a")], states={"assist_satellite.a": "idle"})
    assert roster.collect(h)[0]["status"] == "online"


def test_status_offline_when_unavailable():
    h = _hass([_entity("assist_satellite.a")],
              states={"assist_satellite.a": "unavailable"})
    assert roster.collect(h)[0]["status"] == "offline"


def test_status_offline_when_no_state_object():
    """An entity in the registry with no state has never come up at all."""
    h = _hass([_entity("assist_satellite.a")], states={"assist_satellite.a": None})
    assert roster.collect(h)[0]["status"] == "offline"


def test_disabled_is_distinct_from_offline():
    """Somebody turned it off on purpose -- not a fault, and must not read as one."""
    h = _hass([_entity("assist_satellite.a", disabled_by="user")],
              states={"assist_satellite.a": "unavailable"})
    assert roster.collect(h)[0]["status"] == "disabled"


def test_device_name_beats_generic_platform_name():
    """ESPHome sets has_entity_name, so original_name is "Assist satellite" for
    EVERY satellite. Preferring it produced nine identically-named rows."""
    dev = _device(name="XVF3800 Assistant 1 75357c")
    h = _hass([_entity("assist_satellite.x", device_id="d1",
                       original_name="Assist satellite")], devices={"d1": dev})
    assert roster.collect(h)[0]["name"] == "XVF3800 Assistant 1 75357c"


def test_explicit_entity_rename_still_wins_over_device_name():
    dev = _device(name="XVF3800 Assistant 1 75357c")
    h = _hass([_entity("assist_satellite.x", device_id="d1", name="Bedroom Mic",
                       original_name="Assist satellite")], devices={"d1": dev})
    assert roster.collect(h)[0]["name"] == "Bedroom Mic"


# ── Which state changes are worth a push ─────────────────────────────────────

def test_availability_flip_triggers_push():
    assert roster.state_event_affects_roster(
        "assist_satellite.a", "idle", "unavailable") is True
    assert roster.state_event_affects_roster(
        "assist_satellite.a", "unavailable", "idle") is True


def test_ordinary_state_churn_does_not_trigger_push():
    """A satellite in use cycles constantly; the roster carries none of it."""
    for a, b in (("idle", "listening"), ("listening", "processing"),
                 ("processing", "responding"), ("responding", "idle")):
        assert roster.state_event_affects_roster("assist_satellite.a", a, b) is False


def test_non_satellite_state_changes_ignored():
    assert roster.state_event_affects_roster(
        "sensor.temperature", "1", "unavailable") is False


# ── Wake words ───────────────────────────────────────────────────────────────
#
# ESPHome does not expose these on the assist_satellite entity. Each slot is a
# separate `select.<node>_wake_word` on the SAME DEVICE, so reporting them means
# walking the device's other entities.

def _select(entity_id, device_id):
    s = MagicMock()
    s.entity_id = entity_id
    s.device_id = device_id
    s.name = None
    s.original_name = None
    s.area_id = None
    s.disabled_by = None
    return s


def test_wake_words_collected_from_the_devices_selects():
    ents = [
        _entity("assist_satellite.cores3", device_id="d1"),
        _select("select.cores3_wake_word", "d1"),
        _select("select.cores3_wake_word_2", "d1"),
    ]
    h = _hass(ents, devices={"d1": _device(name="CoreS3")}, states={
        "assist_satellite.cores3": "idle",
        "select.cores3_wake_word": "Okay Nabu",
        "select.cores3_wake_word_2": "Hey Maya",
    })
    assert roster.collect(h)[0]["wake_words"] == ["Okay Nabu", "Hey Maya"]


def test_unused_slot_sentinel_is_not_a_wake_word():
    """`no_wake_word` means the slot is empty. Listing it would claim the
    satellite responds to something called "no_wake_word"."""
    ents = [
        _entity("assist_satellite.cores3", device_id="d1"),
        _select("select.cores3_wake_word", "d1"),
        _select("select.cores3_wake_word_2", "d1"),
    ]
    h = _hass(ents, devices={"d1": _device(name="CoreS3")}, states={
        "assist_satellite.cores3": "idle",
        "select.cores3_wake_word": "Okay Nabu",
        "select.cores3_wake_word_2": "no_wake_word",
    })
    assert roster.collect(h)[0]["wake_words"] == ["Okay Nabu"]


def test_engine_location_is_separate_from_the_words():
    ents = [
        _entity("assist_satellite.cores3", device_id="d1"),
        _select("select.cores3_wake_word", "d1"),
        _select("select.cores3_wake_word_engine_location", "d1"),
    ]
    h = _hass(ents, devices={"d1": _device(name="CoreS3")}, states={
        "assist_satellite.cores3": "idle",
        "select.cores3_wake_word": "Hey Jarvis",
        "select.cores3_wake_word_engine_location": "On device",
    })
    got = roster.collect(h)[0]
    assert got["wake_words"] == ["Hey Jarvis"]
    assert got["wake_word_engine"] == "On device"


def test_sensitivity_is_not_mistaken_for_a_wake_word():
    """It matches the same substring but is a threshold."""
    ents = [
        _entity("assist_satellite.cores3", device_id="d1"),
        _select("select.cores3_wake_word", "d1"),
        _select("select.cores3_wake_word_sensitivity", "d1"),
    ]
    h = _hass(ents, devices={"d1": _device(name="CoreS3")}, states={
        "assist_satellite.cores3": "idle",
        "select.cores3_wake_word": "Hey Jarvis",
        "select.cores3_wake_word_sensitivity": "Slightly sensitive",
    })
    assert roster.collect(h)[0]["wake_words"] == ["Hey Jarvis"]


def test_selects_from_another_device_are_not_borrowed():
    ents = [
        _entity("assist_satellite.cores3", device_id="d1"),
        _select("select.other_wake_word", "d2"),
    ]
    h = _hass(ents, devices={"d1": _device(name="CoreS3")}, states={
        "assist_satellite.cores3": "idle",
        "select.other_wake_word": "Alexa",
    })
    assert roster.collect(h)[0]["wake_words"] == []


def test_changing_a_wake_word_triggers_a_push():
    assert roster.state_event_affects_roster(
        "select.cores3_wake_word", "Okay Nabu", "Hey Maya") is True


def test_sensitivity_change_does_not_trigger_a_push():
    assert roster.state_event_affects_roster(
        "select.cores3_wake_word_sensitivity", "Slightly sensitive", "Very") is False
