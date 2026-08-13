"""Which registry events cause the roster to be re-pushed.

REGRESSION TEST. The listener originally fired only on entity create/remove,
with a comment asserting that only add/remove could change the roster. That was
wrong: the roster carries `name` and `area`, and both change under
action="update". The visible symptom was that assigning a room to a satellite in
Home Assistant changed nothing in the manager until the next HA restart, so the
panel showed a stale room -- or none -- and looked broken rather than stale.

The area case is the one that matters most in practice AND the one most easily
missed, because a room is normally assigned to the DEVICE, not the entity. That
emits only a device-registry event, so an entity-only listener never sees the
change a user actually makes.
"""
from __future__ import annotations

import pathlib
import sys
import types

for name in ("homeassistant", "homeassistant.core", "homeassistant.helpers",
             "homeassistant.helpers.area_registry",
             "homeassistant.helpers.device_registry",
             "homeassistant.helpers.entity_registry"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["homeassistant.core"].HomeAssistant = object

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]
                       / "custom_components" / "ctrlable_snapcast_tts"))
import roster  # noqa: E402

SAT = "assist_satellite.xvf3800_1_voice_assistant"
OTHER = "sensor.kitchen_temperature"


def test_area_assignment_on_entity_triggers_push():
    """The bug, stated directly: setting a room must re-push."""
    assert roster.entity_event_affects_roster("update", SAT, {"area_id": None}) is True


def test_area_assignment_on_device_triggers_push():
    """The commoner path -- a room is assigned to the device, not the entity."""
    assert roster.device_event_affects_roster("update", {"area_id": None}) is True


def test_rename_triggers_push():
    assert roster.entity_event_affects_roster("update", SAT, {"name": "old"}) is True
    assert roster.device_event_affects_roster("update", {"name_by_user": "old"}) is True


def test_create_and_remove_still_trigger():
    assert roster.entity_event_affects_roster("create", SAT, None) is True
    assert roster.entity_event_affects_roster("remove", SAT, None) is True


def test_unrelated_entity_ignored():
    """A thermostat changing area must not cost a roster POST."""
    assert roster.entity_event_affects_roster("update", OTHER, {"area_id": None}) is False
    assert roster.entity_event_affects_roster("create", OTHER, None) is False


def test_unrelated_field_ignored():
    """Churn the roster does not carry stays off the wire."""
    assert roster.entity_event_affects_roster("update", SAT, {"icon": "mdi:x"}) is False
    assert roster.entity_event_affects_roster("update", SAT, {"disabled_by": None}) is False
    assert roster.device_event_affects_roster("update", {"sw_version": "1.0"}) is False


def test_device_create_ignored():
    """A new device alone changes nothing; its ENTITY appearing is what counts,
    and that arrives as an entity create."""
    assert roster.device_event_affects_roster("create", {"area_id": None}) is False


def test_empty_and_missing_changes_are_safe():
    assert roster.entity_event_affects_roster("update", SAT, None) is False
    assert roster.entity_event_affects_roster("update", SAT, {}) is False
    assert roster.device_event_affects_roster("update", None) is False


def test_predicates_cover_every_field_collect_sends():
    """The predicates must not drift from what collect() actually builds.

    collect() reads entry.name, entry.original_name, entry.area_id,
    entry.device_id, entry.entity_id, and from the device: name, name_by_user,
    area_id, configuration_url. If a field is added there and not here, room
    changes go silently unpropagated again -- which is precisely how this bug
    shipped the first time.
    """
    entity_reads = {"area_id", "name", "original_name", "device_id", "entity_id"}
    device_reads = {"area_id", "name", "name_by_user", "configuration_url"}
    for field in entity_reads:
        assert roster.entity_event_affects_roster("update", SAT, {field: None}) is True, field
    for field in device_reads:
        assert roster.device_event_affects_roster("update", {field: None}) is True, field
