"""Service handlers for ctrlable_snapcast_tts."""
from __future__ import annotations

import logging
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .api import (
    AddonApiClient,
    CannotConnectError,
    InvalidAuthError,
    NoMatchingMappingError,
    SatelliteNotMappedError,
)
from .const import CONF_ADDON_URL, CONF_BEARER_TOKEN, DOMAIN, EVENT_ANNOUNCED

_LOGGER = logging.getLogger(__name__)

ANNOUNCE_SCHEMA = vol.Schema(
    {
        vol.Required("url"): cv.string,
        vol.Optional("satellite_id"): cv.string,
        vol.Optional("wake_word"): cv.string,
        vol.Optional("target_snapclient_ids"): vol.All(cv.ensure_list, [cv.string]),
    }
)

SET_MAPPING_SCHEMA = vol.Schema(
    {
        vol.Required("satellite_id"): cv.string,
        vol.Required("wake_word"): cv.string,
        vol.Required("target_snapclient_ids"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("notes", default=""): cv.string,
    }
)


def _get_client(hass: HomeAssistant) -> AddonApiClient | None:
    entries = hass.data.get(DOMAIN, {})
    if not entries:
        return None
    entry_id = next(iter(entries))
    return entries[entry_id]["client"]


def _source_host(hass: HomeAssistant) -> str:
    try:
        url = hass.config.internal_url or hass.config.external_url or ""
        host = urlparse(str(url)).hostname or "homeassistant"
        return host
    except Exception:
        return "homeassistant"


async def handle_announce(hass: HomeAssistant, call: ServiceCall) -> None:
    client = _get_client(hass)
    if client is None:
        _LOGGER.error("ctrlable_snapcast_tts: integration not set up")
        return

    url: str = call.data["url"]
    source_host = _source_host(hass)
    target_ids: list[str] = call.data.get("target_snapclient_ids", [])

    if not target_ids:
        satellite_id: str = call.data.get("satellite_id", "")
        wake_word: str | None = call.data.get("wake_word")
        if not satellite_id:
            _LOGGER.error(
                "ctrlable_snapcast_tts.announce: provide satellite_id or target_snapclient_ids"
            )
            return
        try:
            results = await client.announce_by_satellite(satellite_id, wake_word, url, source_host)
        except SatelliteNotMappedError:
            _LOGGER.warning(
                "ctrlable_snapcast_tts: satellite %r has no mapping configured", satellite_id
            )
            return
        except NoMatchingMappingError:
            _LOGGER.warning(
                "ctrlable_snapcast_tts: no mapping for satellite=%r wake_word=%r",
                satellite_id,
                wake_word,
            )
            return
        except CannotConnectError:
            _LOGGER.error("ctrlable_snapcast_tts: cannot reach add-on at %s", client._url)
            return
        except InvalidAuthError:
            _LOGGER.error("ctrlable_snapcast_tts: bearer token rejected by add-on")
            return
        except Exception as exc:
            _LOGGER.error("ctrlable_snapcast_tts: announce failed — %s", exc)
            return

        hass.bus.async_fire(
            EVENT_ANNOUNCED,
            {"url": url, "satellite_id": satellite_id, "wake_word": wake_word, "results": results},
        )
        _LOGGER.debug("ctrlable_snapcast_tts: announced via satellite %r — %s", satellite_id, results)
        return

    try:
        if len(target_ids) == 1:
            result = await client.announce(target_ids[0], url, source_host)
            results = [{"client_id": target_ids[0], **result}]
        else:
            results = await client.announce_multi(target_ids, url, source_host)
    except CannotConnectError:
        _LOGGER.error("ctrlable_snapcast_tts: cannot reach add-on at %s", client._url)
        return
    except InvalidAuthError:
        _LOGGER.error("ctrlable_snapcast_tts: bearer token rejected by add-on")
        return
    except Exception as exc:
        _LOGGER.error("ctrlable_snapcast_tts: announce failed — %s", exc)
        return

    hass.bus.async_fire(
        EVENT_ANNOUNCED,
        {"url": url, "satellite_id": None, "wake_word": None, "results": results},
    )
    _LOGGER.debug("ctrlable_snapcast_tts: announced to %s — %s", target_ids, results)


async def handle_set_mapping(hass: HomeAssistant, call: ServiceCall) -> None:
    client = _get_client(hass)
    if client is None:
        _LOGGER.error("ctrlable_snapcast_tts: integration not set up")
        return

    satellite_id: str = call.data["satellite_id"]
    wake_word: str = call.data["wake_word"]
    target_ids: list[str] = call.data["target_snapclient_ids"]
    notes: str = call.data.get("notes", "")

    try:
        await client.upsert_mapping(satellite_id, wake_word, target_ids, notes)
    except CannotConnectError:
        _LOGGER.error("ctrlable_snapcast_tts: cannot reach add-on to save mapping")
        return
    except InvalidAuthError:
        _LOGGER.error("ctrlable_snapcast_tts: bearer token rejected by add-on")
        return
    except Exception as exc:
        _LOGGER.error("ctrlable_snapcast_tts: set_mapping failed — %s", exc)
        return

    _LOGGER.info(
        "ctrlable_snapcast_tts: mapping set — %r [%s] → %s", satellite_id, wake_word, target_ids
    )
