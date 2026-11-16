"""Config and options flow for Octoplus."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OctoplusApi, OctoplusAuthError, OctoplusError
from .const import (
    CONF_ACCOUNT,
    CONF_AUTO_CLAIM,
    CONF_AUTO_SCRATCH,
    CONF_NOTIFY,
    CONF_SCAN_MINUTES,
    DEFAULT_AUTO_CLAIM,
    DEFAULT_AUTO_SCRATCH,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
)


async def _validate(hass, api_key: str, account: str) -> str | None:
    """Return an error key, or None if OK."""
    api = OctoplusApi(async_get_clientsession(hass), api_key, account)
    try:
        d = await api.gql("query($acc: String!) { isOctoplusEnrolled(accountNumber: $acc) { isEnrolled } }")
    except OctoplusAuthError:
        return "invalid_auth"
    except OctoplusError:
        return "cannot_connect"
    if not d["isOctoplusEnrolled"]["isEnrolled"]:
        return "not_enrolled"
    return None


class OctoplusConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            account = user_input[CONF_ACCOUNT].strip().upper()
            await self.async_set_unique_id(account)
            self._abort_if_unique_id_configured()
            err = await _validate(self.hass, user_input[CONF_API_KEY].strip(), account)
            if err:
                errors["base"] = err
            else:
                return self.async_create_entry(
                    title=f"Octoplus {account}",
                    data={CONF_API_KEY: user_input[CONF_API_KEY].strip(), CONF_ACCOUNT: account},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_API_KEY): str, vol.Required(CONF_ACCOUNT): str}
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            key = user_input[CONF_API_KEY].strip()
            err = await _validate(self.hass, key, entry.data[CONF_ACCOUNT])
            if err:
                errors["base"] = err
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: key}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return OctoplusOptionsFlow()


class OctoplusOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        o = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_AUTO_SCRATCH, default=o.get(CONF_AUTO_SCRATCH, DEFAULT_AUTO_SCRATCH)
                    ): bool,
                    vol.Optional(
                        CONF_AUTO_CLAIM, default=o.get(CONF_AUTO_CLAIM, DEFAULT_AUTO_CLAIM)
                    ): bool,
                    vol.Optional(
                        CONF_SCAN_MINUTES, default=o.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES)
                    ): vol.All(vol.Coerce(int), vol.Range(min=15, max=1440)),
                    vol.Optional(
                        CONF_NOTIFY, description={"suggested_value": o.get(CONF_NOTIFY, "")}
                    ): str,
                }
            ),
        )
