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
from .const import (
    DOMAIN,
    EVENT_ANNOUNCED,
    SIGNAL_ANNOUNCING,
    SIGNAL_ANNOUNCING_ANY,
    SIGNAL_NEW_SATELLITE,
)

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

HOLD_SCHEMA = vol.Schema(
    {
        vol.Required("satellite_id"): cv.string,
        vol.Optional("wake_word"): cv.string,
    }
)

RELEASE_SCHEMA = vol.Schema(
    {
        vol.Required("satellite_id"): cv.string,
        vol.Optional("wake_word"): cv.string,
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


def _get_client(hass: HomeAssistant, satellite_id: str = "") -> AddonApiClient | None:
    """Pick the streamer that serves this satellite.

    WHY THIS IS NOT JUST next(iter(entries)) ANY MORE. It used to be, which meant
    one streamer served every satellite and the URL could only be changed for all
    four rooms at once. That made migrating to a second streamer an all-or-nothing
    cutover: if anything regressed, every room regressed together.

    Resolution order, most specific first:

      1. an entry that NAMES this satellite id in CONF_SATELLITES
      2. an entry with an EMPTY CONF_SATELLITES -- the catch-all
      3. the first entry, which is exactly the old behaviour

    Rule 3 is what makes this change invisible to an existing install: a lone
    entry has no satellites list, so it is the catch-all and answers for
    everything, as before.

    Matching is exact. A satellite id is what the device sends as
    App.get_name(), so a prefix or fuzzy match would let a new satellite
    silently steal another's route the moment someone names one as a prefix of
    another -- the add-on's mapping resolver already had to grow an
    ambiguity guard for precisely that reason.
    """
    entries = hass.data.get(DOMAIN, {})
    if not entries:
        return None

    if satellite_id:
        for rec in entries.values():
            if satellite_id in rec.get("satellites", ()):
                return rec["client"]

    for rec in entries.values():
        if not rec.get("satellites"):
            return rec["client"]

    return entries[next(iter(entries))]["client"]


def _source_host(hass: HomeAssistant) -> str:
    try:
        url = hass.config.internal_url or hass.config.external_url or ""
        host = urlparse(str(url)).hostname or "homeassistant"
        return host
    except Exception:
        return "homeassistant"


async def handle_announce(hass: HomeAssistant, call: ServiceCall) -> None:
    # Read the satellite id first: it is what SELECTS the streamer. A call that
    # passes target_snapclient_ids instead has no satellite, so it falls through
    # to the catch-all entry -- which is right, since those ids are meaningless
    # without knowing which streamer's snapserver they belong to.
    client = _get_client(hass, call.data.get("satellite_id", ""))
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
        async_dispatcher_send(hass, SIGNAL_ANNOUNCING_ANY, satellite_id, True)
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
            async_dispatcher_send(hass, SIGNAL_ANNOUNCING_ANY, satellite_id, False)

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
    satellite_id: str = call.data["satellite_id"]
    client = _get_client(hass, satellite_id)
    if client is None:
        _LOGGER.error("ctrlable_snapcast_tts: integration not set up")
        return

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


async def handle_hold(hass: HomeAssistant, call: ServiceCall) -> None:
    """Duck the satellite's zone for the listening window.

    Called on every on_listening, including follow-up turns of a continued
    conversation -- which is the case the wake chime does not cover. Idempotent
    and fire-and-forget; the answer or a release lets go.
    """
    satellite_id: str = call.data["satellite_id"]
    client = _get_client(hass, satellite_id)
    if client is None:
        return
    try:
        await client.hold(satellite_id, call.data.get("wake_word"))
    except Exception as exc:  # noqa: BLE001 - never let this break an exchange
        _LOGGER.debug("ctrlable_snapcast_tts: hold failed — %s", exc)


async def handle_release(hass: HomeAssistant, call: ServiceCall) -> None:
    """Exchange ended without an answer -- stop holding the zone.

    Fire-and-forget by design: the add-on treats releasing an unheld group as a
    no-op, so a device can call this on every exchange end without tracking
    whether anything was actually held. Failures are logged at debug because a
    missed release self-heals via the add-on's watchdog.
    """
    satellite_id: str = call.data["satellite_id"]
    client = _get_client(hass, satellite_id)
    if client is None:
        return
    try:
        await client.release(satellite_id, call.data.get("wake_word"))
    except Exception as exc:  # noqa: BLE001 - never let this break an exchange
        _LOGGER.debug("ctrlable_snapcast_tts: release failed — %s", exc)


async def handle_set_mapping(hass: HomeAssistant, call: ServiceCall) -> None:
    satellite_id: str = call.data["satellite_id"]
    client = _get_client(hass, satellite_id)
    if client is None:
        _LOGGER.error("ctrlable_snapcast_tts: integration not set up")
        return

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
