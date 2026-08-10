"""One shared entity naming whichever satellites are announcing right now.

WHY THIS EXISTS, rather than the per-satellite binary_sensors next door.

A satellite needs to know when ITS answer finished playing. The obvious shape is
one sensor per satellite, and that is what binary_sensor.py does -- but it forces
every device config to name its own entity:

    snapcast_announcing_entity: binary_sensor.<this device>_announcing

which is a compile-time string. So the id the device SENDS and the entity it
SUBSCRIBES TO both have to be edited per device, by hand, in agreement. Get them
out of step and the answer plays while the device never learns it finished --
silent, and the most expensive failure mode in this project.

It also blocks `name_add_mac_suffix: true`, the one mechanism ESPHome offers for
making a single config safely flashable to many devices: the suffix is only
known at build time, so a hand-written entity id can never match it.

With one shared entity, every config carries the SAME static string, and each
device recognises itself at runtime by comparing against App.get_name() -- which
does include the MAC suffix. Nothing per-device is left to edit or mistype.

State is a comma-separated list of the satellite ids currently announcing, or
"none". A list rather than a single id because two rooms can answer at once and
a single-valued sensor would make one of them miss its completion.
"""
from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_ANNOUNCING_ANY

_LOGGER = logging.getLogger(__name__)

# HA caps a state string at 255 characters. Satellite ids run ~20-40 chars, so
# this only bites if a great many announce simultaneously; truncate rather than
# let HA reject the whole state and leave every device stuck.
_MAX_STATE = 250


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([AnnouncingSatellites(entry)])


class AnnouncingSatellites(SensorEntity):
    """Which satellites are mid-announcement, as one shared entity."""

    _attr_should_poll = False
    _attr_icon = "mdi:cast-audio"
    _attr_name = "Snapcast announcing"

    def __init__(self, entry: ConfigEntry) -> None:
        # Fixed unique_id, NOT derived from any satellite: every device config
        # references this one entity by a hard-coded id, so it must be stable
        # for the life of the integration.
        self._attr_unique_id = f"{entry.entry_id}_announcing_any"
        self._active: set[str] = set()

    @property
    def native_value(self) -> str:
        if not self._active:
            return "none"
        joined = ",".join(sorted(self._active))
        if len(joined) > _MAX_STATE:
            _LOGGER.warning(
                "Too many satellites announcing to fit in one state (%d chars); "
                "truncating, some devices may fall back to their timeout",
                len(joined),
            )
            joined = joined[:_MAX_STATE].rsplit(",", 1)[0]
        return joined

    @property
    def extra_state_attributes(self) -> dict:
        # The list is also exposed structurally, so an automation does not have
        # to parse the state string the way the devices must.
        return {"satellites": sorted(self._active)}

    async def async_added_to_hass(self) -> None:
        @callback
        def _changed(satellite_id: str, announcing: bool) -> None:
            if not satellite_id:
                return
            if announcing:
                self._active.add(satellite_id)
            else:
                self._active.discard(satellite_id)
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_ANNOUNCING_ANY, _changed)
        )
