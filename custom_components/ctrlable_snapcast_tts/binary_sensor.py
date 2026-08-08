"""Per-satellite "announcing" state.

WHY THIS EXISTS
---------------
The add-on's /announce endpoints await playback and only then return, so the
moment `handle_announce` finishes IS the moment the answer stopped playing in
the room. Home Assistant already knew that -- EVENT_ANNOUNCED fires right there
-- but an ESPHome device cannot subscribe to HA events. It can only mirror
entity state.

Without such an entity a satellite that routes its answer to Snapcast has no way
to learn when the answer ended. It went idle the instant the pipeline finished,
which is BEFORE playback starts, so it resumed its wake word while the reply was
still coming out of the speakers beside its microphone.

So: one binary_sensor per satellite, ON for exactly the span of the announce.
An ESPHome config watches it with

    binary_sensor:
      - platform: homeassistant
        entity_id: binary_sensor.<satellite>_announcing
        on_release:
          - script.execute: finish_response
"""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_ANNOUNCING, SIGNAL_NEW_SATELLITE

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    known: set[str] = set()

    @callback
    def _add(satellite_id: str) -> None:
        if not satellite_id or satellite_id in known:
            return
        known.add(satellite_id)
        async_add_entities([SatelliteAnnouncing(entry, satellite_id)])

    # Seed from the add-on's mappings so the entity exists BEFORE the first
    # announce. A device whose entity only appears after its first reply would
    # miss the completion signal for that reply and fall back to its timeout.
    client = hass.data[DOMAIN][entry.entry_id]["client"]
    try:
        for mapping in await client.get_mappings():
            _add(mapping.get("satellite_id", ""))
    except Exception as exc:  # noqa: BLE001 - never block setup on the add-on
        _LOGGER.warning(
            "ctrlable_snapcast_tts: could not seed announcing sensors from "
            "mappings (%s); they will appear on first announce instead",
            exc,
        )

    # A satellite that announces without a pre-existing mapping still gets an
    # entity, just one exchange late.
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_SATELLITE, _add)
    )


class SatelliteAnnouncing(BinarySensorEntity):
    """ON while this satellite's answer is playing through Snapcast."""

    _attr_should_poll = False
    _attr_icon = "mdi:account-voice"
    _attr_entity_registry_enabled_default = True

    def __init__(self, entry: ConfigEntry, satellite_id: str) -> None:
        self._satellite_id = satellite_id
        self._attr_is_on = False
        self._attr_unique_id = f"{entry.entry_id}_{satellite_id}_announcing"
        self._attr_name = f"{satellite_id} announcing"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_ANNOUNCING.format(self._satellite_id),
                self._set_state,
            )
        )

    @callback
    def _set_state(self, announcing: bool) -> None:
        self._attr_is_on = announcing
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {"satellite_id": self._satellite_id}
