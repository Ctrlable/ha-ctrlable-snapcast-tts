"""Ctrlable Snapcast TTS integration."""
from __future__ import annotations

import logging
from functools import partial

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

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

    @callback
    def _registry_changed(event) -> None:
        # Only entity ADD/REMOVE can change the roster; ignore state churn.
        if event.data.get("action") in ("create", "remove") and str(
            event.data.get("entity_id", "")
        ).startswith("assist_satellite."):
            entry.async_create_background_task(hass, _push_roster(), "ctrlable_roster_push")

    entry.async_on_unload(
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _registry_changed)
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
