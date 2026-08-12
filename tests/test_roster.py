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


def _entity(entity_id, device_id=None, name=None, original_name=None, area_id=None):
    e = MagicMock()
    e.entity_id = entity_id
    e.device_id = device_id
    e.name = name
    e.original_name = original_name
    e.area_id = area_id
    return e


def _device(name=None, name_by_user=None, area_id=None, configuration_url=None):
    d = MagicMock()
    d.name = name
    d.name_by_user = name_by_user
    d.area_id = area_id
    d.configuration_url = configuration_url
    return d


def _hass(entities, devices=None, areas=None):
    devices = devices or {}
    areas = areas or {}
    roster.er.async_get = lambda h: types.SimpleNamespace(
        entities={e.entity_id: e for e in entities})
    roster.dr.async_get = lambda h: types.SimpleNamespace(
        async_get=lambda did: devices.get(did))
    roster.ar.async_get = lambda h: types.SimpleNamespace(
        async_get_area=lambda aid: types.SimpleNamespace(name=areas[aid]) if aid in areas else None)
    return MagicMock()


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
