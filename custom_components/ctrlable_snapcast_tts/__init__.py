"""Ctrlable Snapcast TTS integration."""
from __future__ import annotations

import logging
from functools import partial

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import AddonApiClient
from .const import CONF_ADDON_URL, CONF_BEARER_TOKEN, DOMAIN, PLATFORMS
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

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
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
