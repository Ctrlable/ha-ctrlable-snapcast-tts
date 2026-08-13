"""Tell the streamer which satellites Home Assistant knows about.

WHY THIS EXISTS. The streamer used to read HA's entity registry itself, via
`http://supervisor/core/api/states` with SUPERVISOR_TOKEN. That worked only
because it ran as an add-on, where the Supervisor injects that token — nothing
was ever minted. Moved out of HA it has no such access, and the obvious
replacement, a long-lived HA token, would hand it full control of every entity
in the house in exchange for populating a dropdown.

So the direction is inverted: HA pushes, using the bearer token the integration
already holds. The streamer keeps no HA credential at all.

THE HARD PART IS THE ID, and it is worth being explicit because it has already
caused silent failures. A device sends its own ESPHome node name as its
satellite id — `App.get_name()`, e.g. `atoms3r-echo-bca1a8`. Home Assistant
knows an entity slug that encodes the friendly name and area instead, e.g.
`assist_satellite.atoms3r_echo_base_voice_assistant_assist_satellite`. Those are
different strings for the same device and no amount of transformation reliably
turns one into the other.

So this module does NOT pretend to know the id. It sends every identifier it can
see, best guess first, and leaves resolution to the streamer, which can compare
against the ids it has actually observed devices send. The roster is for display
and pre-population; routing still goes through the mappings table.
"""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

_PREFIX = "assist_satellite."

# WHICH REGISTRY CHANGES CHANGE THE ROSTER.
#
# These predicates live here, beside collect(), rather than inline in the event
# listeners, for one reason: they must agree with what collect() actually SENDS.
# When they drifted apart the failure was silent and long-lived -- the listener
# fired only on create/remove, so assigning a room updated nothing until the next
# HA restart, and the panel just showed a stale room forever.
#
# If a field is added to the dict collect() builds, add it here too.
_ENTITY_FIELDS = frozenset({"area_id", "name", "original_name", "device_id", "entity_id"})
_DEVICE_FIELDS = frozenset({"area_id", "name", "name_by_user", "configuration_url"})


def entity_event_affects_roster(action: str, entity_id: str, changes) -> bool:
    """True when an entity-registry event changes what collect() would return."""
    if not str(entity_id or "").startswith(_PREFIX):
        return False
    if action in ("create", "remove"):
        return True
    if action != "update":
        return False
    return bool(set(changes or {}) & _ENTITY_FIELDS)


def _availability(state: str | None) -> str:
    """Collapse a state to the only distinction the roster reports."""
    if state is None or state == "unavailable":
        return "offline"
    if state == "unknown":
        return "unknown"
    return "online"


def state_event_affects_roster(entity_id: str, old_state: str | None,
                               new_state: str | None) -> bool:
    """True when a state change alters something the roster actually reports.

    Two different rules, because the two entity kinds carry different things:

    - `assist_satellite.*` -- ONLY the availability flip. These cycle through
      idle, listening, processing and responding constantly while in use, and a
      full roster POST per transition would be a request storm carrying no new
      information, since the roster reports online/offline and not activity.
    - `select.*wake_word*` -- ANY value change, because the roster carries the
      value itself. Changing a satellite's wake word in Home Assistant has to
      reach the panel, and it never touches the satellite entity.

    `_sensitivity` is excluded: same substring, but the roster does not report it.
    """
    eid = str(entity_id or "")
    if eid.startswith(_PREFIX):
        return _availability(old_state) != _availability(new_state)
    if eid.startswith("select.") and "wake_word" in eid and "sensitivity" not in eid:
        return old_state != new_state
    return False


def device_event_affects_roster(action: str, changes) -> bool:
    """True when a device-registry event could change a satellite's name or area.

    Area is normally set on the DEVICE, not the entity, so this is the path the
    common case actually takes. The caller still has to confirm the device owns
    an assist_satellite entity -- that needs the registry, which is not this
    module's business.
    """
    if action != "update":
        return False
    return bool(set(changes or {}) & _DEVICE_FIELDS)


def _slug_to_id(slug: str) -> str:
    """HA slugs use underscores; ESPHome node names use hyphens."""
    return slug.replace("_", "-").strip("-")


_NO_WAKE_WORD = "no_wake_word"
_DEAD_STATES = ("", "unknown", "unavailable")


def _wake_words(hass: HomeAssistant, ent_reg, device_id: str | None):
    """Wake words configured on a satellite, and where they are detected.

    ESPHome does not put this on the assist_satellite entity -- it exposes one
    `select.<node>_wake_word` per slot on the SAME DEVICE, plus
    `_wake_word_engine_location` ("On device" vs a server). So the only way to
    report it is to walk the device's other entities.

    An unused slot reads `no_wake_word`, which is a sentinel and not a wake word;
    listing it would suggest a satellite responds to something called
    "no_wake_word". `_sensitivity` is excluded -- it matches the same substring
    but is a threshold, not a word.
    """
    words: list[str] = []
    engine = ""
    if not device_id:
        return words, engine

    # Sorted so slot 1 precedes slot 2, which is the order they are configured
    # in and the order ESPHome evaluates them.
    for ent in sorted(ent_reg.entities.values(), key=lambda x: x.entity_id):
        if ent.device_id != device_id or not ent.entity_id.startswith("select."):
            continue
        eid = ent.entity_id
        if "wake_word" not in eid or "sensitivity" in eid:
            continue
        state = hass.states.get(eid)
        val = state.state if state else ""
        if val in _DEAD_STATES:
            continue
        if "engine_location" in eid:
            engine = val
        elif val != _NO_WAKE_WORD and val not in words:
            words.append(val)
    return words, engine


def collect(hass: HomeAssistant) -> list[dict]:
    """Every assist_satellite entity, with the identifiers HA can see."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)

    out: list[dict] = []
    for entry in ent_reg.entities.values():
        if not entry.entity_id.startswith(_PREFIX):
            continue

        device = dev_reg.async_get(entry.device_id) if entry.device_id else None

        area_id = entry.area_id or (device.area_id if device else None)
        area = ""
        if area_id:
            area_entry = area_reg.async_get_area(area_id)
            area = area_entry.name if area_entry else ""

        # THE DEVICE NAME BEATS original_name, and the order matters more than it
        # looks. ESPHome assist_satellite entities set has_entity_name, so their
        # original_name is the PLATFORM's generic label -- literally "Assist
        # satellite" for every one of them. Preferring it produced a roster of
        # nine identically-named rows, which is useless for picking a satellite.
        #
        # HA itself displays these as "<device name> <entity name>", so the device
        # name is the part that identifies the thing. entry.name still wins when
        # set, because that is an explicit rename by the user.
        dev_name = (device.name_by_user or device.name) if device else ""
        name = entry.name or dev_name or entry.original_name or entry.entity_id

        # Candidates, best guess first.
        #
        # The device NAME is listed before the entity slug deliberately. ESPHome
        # registers a device whose name tracks the node's friendly_name, so
        # slugifying it lands much closer to the node name than the entity slug
        # does -- the entity slug also carries the platform suffix
        # ("_voice_assistant_assist_satellite") which no device ever sends.
        candidates: list[str] = []
        if device and device.name:
            candidates.append(_slug_to_id(device.name.lower().replace(" ", "-")))
        candidates.append(_slug_to_id(entry.entity_id[len(_PREFIX):]))

        # configuration_url is often http://<node-name>.local for ESPHome, which
        # IS the id the device sends. Cheap to include and sometimes exact.
        if device and device.configuration_url:
            host = str(device.configuration_url).split("//")[-1].split("/")[0]
            host = host.split(":")[0]
            if host.endswith(".local"):
                candidates.append(host[: -len(".local")])

        seen: set[str] = set()
        candidates = [c for c in candidates if c and not (c in seen or seen.add(c))]

        # ONLINE/OFFLINE. Only Home Assistant knows this -- the streamer sees a
        # satellite exactly once, when it announces, which says nothing about
        # whether it is reachable now. A satellite that has dropped off wifi looks
        # identical to a working one in every other view, right up until an
        # announcement silently goes nowhere.
        #
        # `disabled` and `unavailable` are deliberately distinct: disabled means
        # somebody turned it off in HA, unavailable means it should be here and
        # is not. Only the second is a fault worth flagging.
        st_obj = hass.states.get(entry.entity_id)
        if entry.disabled_by is not None:
            status = "disabled"
        elif st_obj is None or st_obj.state == "unavailable":
            status = "offline"
        elif st_obj.state == "unknown":
            status = "unknown"
        else:
            status = "online"

        wake_words, wake_engine = _wake_words(hass, ent_reg, entry.device_id)

        out.append({
            "entity_id": entry.entity_id,
            "name": name,
            "area": area,
            "status": status,
            "state": st_obj.state if st_obj else "",
            "wake_words": wake_words,
            "wake_word_engine": wake_engine,
            "candidates": candidates,
            # Best guess, for callers that want a single value. Explicitly a
            # guess: the mapping page should prefer an observed id when one
            # matches, because that is what the device actually sends.
            "id": candidates[0] if candidates else "",
        })

    out.sort(key=lambda s: (s["area"].lower(), s["name"].lower()))
    return out


async def push(hass: HomeAssistant, client) -> None:
    """Send the roster; never let a failure disturb HA startup.

    Best-effort by design. The streamer works without this -- it records any
    satellite that calls in without a mapping, which is the backstop that always
    works. The roster only makes a satellite appear BEFORE it has spoken.
    """
    try:
        satellites = collect(hass)
    except Exception:
        _LOGGER.exception("ctrlable_snapcast_tts: could not collect the satellite roster")
        return

    if not satellites:
        _LOGGER.debug("ctrlable_snapcast_tts: no assist_satellite entities to push")
        return

    try:
        await client.push_satellites(satellites)
        _LOGGER.debug("ctrlable_snapcast_tts: pushed %d satellites", len(satellites))
    except Exception as exc:  # noqa: BLE001 - a streamer that is down must not break HA
        _LOGGER.debug("ctrlable_snapcast_tts: roster push failed — %s", exc)
