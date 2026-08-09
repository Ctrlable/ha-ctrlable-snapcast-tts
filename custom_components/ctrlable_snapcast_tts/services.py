"""Service handlers for ctrlable_snapcast_tts."""
from __future__ import annotations

import logging
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .api import (
    AddonApiClient,
    CannotConnectError,
    InvalidAuthError,
    NoMatchingMappingError,
    SatelliteNotMappedError,
)
from .const import DOMAIN, EVENT_ANNOUNCED, SIGNAL_ANNOUNCING, SIGNAL_NEW_SATELLITE

_LOGGER = logging.getLogger(__name__)

ANNOUNCE_SCHEMA = vol.Schema(
    {
        vol.Required("url"): cv.string,
        vol.Optional("satellite_id"): cv.string,
        vol.Optional("wake_word"): cv.string,
        vol.Optional("target_snapclient_ids"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("volume"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
    }
)

CHIME_SCHEMA = vol.Schema(
    {
        vol.Required("satellite_id"): cv.string,
        vol.Optional("wake_word"): cv.string,
        vol.Optional("chime", default="wake"): cv.string,
        vol.Optional("volume"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
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
    volume: int | None = call.data.get("volume")
    target_ids: list[str] = call.data.get("target_snapclient_ids", [])

    if not target_ids:
        satellite_id: str = call.data.get("satellite_id", "")
        wake_word: str | None = call.data.get("wake_word")
        if not satellite_id:
            _LOGGER.error(
                "ctrlable_snapcast_tts.announce: provide satellite_id or target_snapclient_ids"
            )
            return
        # Hold the satellite's "announcing" sensor ON for exactly the span of the
        # call. announce_by_satellite awaits playback, so switching it off in the
        # finally IS the end-of-playback signal a Snapcast-routed device needs.
        # try/finally, not try/except: every error path below returns early, and
        # a sensor stuck ON would leave that satellite wedged in "replying".
        async_dispatcher_send(hass, SIGNAL_NEW_SATELLITE, satellite_id)
        async_dispatcher_send(hass, SIGNAL_ANNOUNCING.format(satellite_id), True)
        try:
            results = await client.announce_by_satellite(
                satellite_id, wake_word, url, source_host, volume
            )
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
        finally:
            async_dispatcher_send(hass, SIGNAL_ANNOUNCING.format(satellite_id), False)

        hass.bus.async_fire(
            EVENT_ANNOUNCED,
            {"url": url, "satellite_id": satellite_id, "wake_word": wake_word, "results": results},
        )
        _LOGGER.debug("ctrlable_snapcast_tts: announced via satellite %r — %s", satellite_id, results)
        return

    try:
        if len(target_ids) == 1:
            result = await client.announce(target_ids[0], url, source_host, volume)
            results = [{"client_id": target_ids[0], **result}]
        else:
            results = await client.announce_multi(target_ids, url, source_host, volume)
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


async def handle_chime(hass: HomeAssistant, call: ServiceCall) -> None:
    """Play a bundled chime on the satellite's zone.

    Deliberately does NOT touch the announcing sensor. That sensor means "an
    answer is playing, hold the device in replying"; a wake chime is the
    opposite -- it fires as the exchange BEGINS. Raising it here would make the
    device think its answer had already arrived and finished.
    """
    client = _get_client(hass)
    if client is None:
        _LOGGER.error("ctrlable_snapcast_tts: integration not set up")
        return

    satellite_id: str = call.data["satellite_id"]
    try:
        await client.announce_chime(
            satellite_id,
            call.data.get("wake_word"),
            call.data.get("chime", "wake"),
            call.data.get("volume"),
        )
    except SatelliteNotMappedError:
        _LOGGER.debug("ctrlable_snapcast_tts: chime for unmapped satellite %r", satellite_id)
    except NoMatchingMappingError:
        _LOGGER.debug("ctrlable_snapcast_tts: no chime mapping for %r", satellite_id)
    except CannotConnectError:
        _LOGGER.warning("ctrlable_snapcast_tts: cannot reach add-on for chime")
    except Exception as exc:  # noqa: BLE001 - a missed chime must never break an exchange
        _LOGGER.warning("ctrlable_snapcast_tts: chime failed — %s", exc)


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
