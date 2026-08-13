"""Ctrlable Snapcast TTS integration."""
from __future__ import annotations

import logging
from functools import partial

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later

from .api import AddonApiClient
from . import roster
from .const import CONF_ADDON_URL, CONF_BEARER_TOKEN, CONF_SATELLITES, DOMAIN, PLATFORMS
from .services import (
    ANNOUNCE_SCHEMA,
    CHIME_SCHEMA,
    HOLD_SCHEMA,
    RELEASE_SCHEMA,
    SET_MAPPING_SCHEMA,
    handle_announce,
    handle_chime,
    handle_hold,
    handle_release,
    handle_set_mapping,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = AddonApiClient(
        entry.data[CONF_ADDON_URL],
        entry.data[CONF_BEARER_TOKEN],
    )

    # Which satellites this entry answers for. Empty means "all of them", so an
    # existing single-entry install keeps behaving exactly as it did.
    satellites = tuple(
        s.strip() for s in entry.data.get(CONF_SATELLITES, "").split(",") if s.strip()
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "satellites": satellites,
    }

    hass.services.async_register(
        DOMAIN, "announce", partial(handle_announce, hass), schema=ANNOUNCE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "chime", partial(handle_chime, hass), schema=CHIME_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "hold", partial(handle_hold, hass), schema=HOLD_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "release", partial(handle_release, hass), schema=RELEASE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "set_mapping", partial(handle_set_mapping, hass), schema=SET_MAPPING_SCHEMA
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Push the satellite roster once HA has finished starting, then whenever the
    # entity registry changes. Deferred to EVENT_HOMEASSISTANT_STARTED because at
    # setup time the ESPHome entities may not be registered yet -- pushing then
    # would send a roster missing exactly the satellites we care about.
    async def _push_roster(_event=None) -> None:
        await roster.push(hass, client)

    if hass.state == CoreState.running:
        entry.async_create_background_task(hass, _push_roster(), "ctrlable_roster_push")
    else:
        entry.async_on_unload(
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _push_roster)
        )

    # Re-push when the roster's CONTENT changes, which is not the same thing as
    # the roster's MEMBERSHIP changing.
    #
    # An earlier version listened only for create/remove, with a comment claiming
    # only add/remove could change the roster. That was wrong: the roster carries
    # `name` and `area`, and both change under action="update". So assigning a
    # room to a satellite in HA updated nothing here until the next HA restart --
    # the panel kept showing the old room, or none, and looked simply broken.
    #
    # Area is the more common case and the worse one, because it is usually set
    # on the DEVICE, not the entity. That fires the DEVICE registry event only,
    # so an entity-registry listener alone would never see the change that people
    # actually make. Both registries are watched.
    #
    # Debounced: renaming a device can emit several events, and each push is a
    # full roster POST. The delay is short enough to feel immediate.
    push_timer: list = [None]

    @callback
    def _schedule_push() -> None:
        if push_timer[0] is not None:
            push_timer[0]()
            push_timer[0] = None

        @callback
        def _fire(_now) -> None:
            push_timer[0] = None
            entry.async_create_background_task(hass, _push_roster(), "ctrlable_roster_push")

        push_timer[0] = async_call_later(hass, 2, _fire)

    entry.async_on_unload(lambda: push_timer[0] and push_timer[0]())

    @callback
    def _entity_registry_changed(event) -> None:
        if roster.entity_event_affects_roster(
            event.data.get("action"),
            event.data.get("entity_id", ""),
            event.data.get("changes"),
        ):
            _schedule_push()

    @callback
    def _device_registry_changed(event) -> None:
        if not roster.device_event_affects_roster(
            event.data.get("action"), event.data.get("changes")
        ):
            return
        # Only if this device actually carries a satellite -- most devices do not,
        # and a full roster POST per unrelated device rename is not free.
        dev_id = event.data.get("device_id")
        if not dev_id:
            return
        ent_reg = er.async_get(hass)
        for ent in ent_reg.entities.values():
            if ent.device_id == dev_id and ent.entity_id.startswith("assist_satellite."):
                _schedule_push()
                return

    entry.async_on_unload(
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _entity_registry_changed)
    )
    entry.async_on_unload(
        hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, _device_registry_changed)
    )

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data[DOMAIN].pop(entry.entry_id, None)
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, "announce")
        hass.services.async_remove(DOMAIN, "chime")
        hass.services.async_remove(DOMAIN, "hold")
        hass.services.async_remove(DOMAIN, "release")
        hass.services.async_remove(DOMAIN, "set_mapping")
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
