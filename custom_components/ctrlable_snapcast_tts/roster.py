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


def _slug_to_id(slug: str) -> str:
    """HA slugs use underscores; ESPHome node names use hyphens."""
    return slug.replace("_", "-").strip("-")


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

        name = (
            entry.name
            or entry.original_name
            or (device.name_by_user or device.name if device else "")
            or entry.entity_id
        )

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

        out.append({
            "entity_id": entry.entity_id,
            "name": name,
            "area": area,
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
