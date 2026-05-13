"""Config flow for Ctrlable Snapcast TTS."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import AddonApiClient, CannotConnectError, InvalidAuthError
from .const import CONF_ADDON_URL, CONF_BEARER_TOKEN, DEFAULT_ADDON_URL, DOMAIN
from .mapping import label, remove, upsert

_LOGGER = logging.getLogger(__name__)

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ADDON_URL, default=DEFAULT_ADDON_URL): str,
        vol.Required(CONF_BEARER_TOKEN): str,
    }
)


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

        return self.async_show_form(
            step_id="user",
            data_schema=_USER_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CtrlableSnapcastTtsOptionsFlow(config_entry)


class CtrlableSnapcastTtsOptionsFlow(OptionsFlow):
    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry
        self._mappings: list[dict] = list(config_entry.options.get("mappings", []))
        self._clients: list[dict] = []

    async def _fetch_clients(self) -> None:
        client: AddonApiClient = self.hass.data[DOMAIN][self._config_entry.entry_id]["client"]
        try:
            self._clients = await client.get_clients()
        except Exception:
            self._clients = []

    def _client_options(self) -> list[SelectOptionDict]:
        return [
            SelectOptionDict(value=c["id"], label=c.get("name", c["id"]))
            for c in self._clients
            if c.get("id")
        ]

    # ── Menu ──────────────────────────────────────────────────────────────────

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        await self._fetch_clients()
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_mapping", "remove_mapping", "done"],
        )

    # ── Add mapping ───────────────────────────────────────────────────────────

    async def async_step_add_mapping(
        self, user_input: dict | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            satellite_id: str = user_input["satellite_id"].strip()
            wake_word: str = user_input.get("wake_word", "*").strip() or "*"
            target_ids: list[str] = user_input["target_snapclient_ids"]
            if not satellite_id:
                errors["satellite_id"] = "required"
            elif not target_ids:
                errors["target_snapclient_ids"] = "required"
            else:
                self._mappings = upsert(
                    self._mappings, satellite_id, wake_word, target_ids, ""
                )
                return await self.async_step_init()

        client_opts = self._client_options()
        schema = vol.Schema(
            {
                vol.Required("satellite_id"): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
                vol.Optional("wake_word", default="*"): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
                vol.Required("target_snapclient_ids"): SelectSelector(
                    SelectSelectorConfig(
                        options=client_opts,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                        custom_value=True,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="add_mapping",
            data_schema=schema,
            errors=errors,
        )

    # ── Remove mapping ────────────────────────────────────────────────────────

    async def async_step_remove_mapping(
        self, user_input: dict | None = None
    ) -> FlowResult:
        if not self._mappings:
            return await self.async_step_init()

        if user_input is not None:
            key: str = user_input["mapping_key"]
            # key is "satellite_id||wake_word"
            parts = key.split("||", 1)
            if len(parts) == 2:
                self._mappings = remove(self._mappings, parts[0], parts[1])
            return await self.async_step_init()

        mapping_opts = [
            SelectOptionDict(
                value=f"{m['satellite_id']}||{m['wake_word']}",
                label=label(m),
            )
            for m in self._mappings
        ]
        schema = vol.Schema(
            {
                vol.Required("mapping_key"): SelectSelector(
                    SelectSelectorConfig(
                        options=mapping_opts,
                        multiple=False,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="remove_mapping", data_schema=schema)

    # ── Done ─────────────────────────────────────────────────────────────────

    async def async_step_done(self, user_input: dict | None = None) -> FlowResult:
        new_options = dict(self._config_entry.options)
        new_options["mappings"] = self._mappings
        # Sync live mappings so announce works without reload
        entry_data = self.hass.data[DOMAIN].get(self._config_entry.entry_id, {})
        entry_data["mappings"] = list(self._mappings)
        return self.async_create_entry(title="", data=new_options)
