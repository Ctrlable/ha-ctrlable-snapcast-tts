"""Config flow for Ctrlable Snapcast TTS."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .api import AddonApiClient, CannotConnectError, InvalidAuthError
from .const import CONF_ADDON_URL, CONF_BEARER_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)


def _default_addon_url(hass) -> str:
    try:
        from urllib.parse import urlparse
        raw = hass.config.internal_url or hass.config.external_url or ""
        host = urlparse(str(raw)).hostname or "homeassistant.local"
    except Exception:
        host = "homeassistant.local"
    return f"http://{host}:8099"


class CtrlableSnapcastTtsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        errors: dict[str, str] = {}
        if user_input is not None:
            client = AddonApiClient(
                user_input[CONF_ADDON_URL], user_input[CONF_BEARER_TOKEN]
            )
            try:
                await client.get_health()
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except InvalidAuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during config flow")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title="Ctrlable Snapcast TTS",
                    data=user_input,
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_ADDON_URL, default=_default_addon_url(self.hass)): str,
                vol.Required(CONF_BEARER_TOKEN): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CtrlableSnapcastTtsOptionsFlow(config_entry)


class CtrlableSnapcastTtsOptionsFlow(OptionsFlow):
    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = AddonApiClient(
                user_input[CONF_ADDON_URL], user_input[CONF_BEARER_TOKEN]
            )
            try:
                await client.get_health()
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except InvalidAuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error in options flow")
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    data={
                        CONF_ADDON_URL: user_input[CONF_ADDON_URL],
                        CONF_BEARER_TOKEN: user_input[CONF_BEARER_TOKEN],
                    },
                )
                return self.async_create_entry(title="", data={})

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ADDON_URL,
                    default=self._config_entry.data.get(CONF_ADDON_URL, ""),
                ): str,
                vol.Required(
                    CONF_BEARER_TOKEN,
                    default=self._config_entry.data.get(CONF_BEARER_TOKEN, ""),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
