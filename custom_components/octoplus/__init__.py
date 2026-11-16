"""Octoplus integration: auto-scratch, claim and notify for Octopus Energy Octoplus."""
from __future__ import annotations

import hashlib
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .api import OctoplusError
from .const import ATTR_CONFIG_ENTRY_ID, ATTR_OFFER_SLUG, DOMAIN, SERVICE_CLAIM_OFFER
from .coordinator import OctoplusCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type OctoplusConfigEntry = ConfigEntry[OctoplusCoordinator]

CLAIM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_OFFER_SLUG): cv.string,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


CARD_URL = "/octoplus/octoplus-card.js"


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the Octoplus card and load it on every dashboard."""
    if hass.http is None:  # e.g. in tests without http
        return
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    path = Path(__file__).parent / "frontend" / "octoplus-card.js"
    # Cache aggressively; the content hash in the URL busts the cache on any change.
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(path), cache_headers=True)]
    )
    digest = await hass.async_add_executor_job(
        lambda: hashlib.sha256(path.read_bytes()).hexdigest()[:10]
    )
    add_extra_js_url(hass, f"{CARD_URL}?v={digest}")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    await _async_register_card(hass)
    async def _claim(call: ServiceCall) -> ServiceResponse:
        entries = [
            e for e in hass.config_entries.async_entries(DOMAIN)
            if e.state is ConfigEntryState.LOADED
            and (ATTR_CONFIG_ENTRY_ID not in call.data
                 or e.entry_id == call.data[ATTR_CONFIG_ENTRY_ID])
        ]
        if len(entries) != 1:
            raise ServiceValidationError(
                "Specify config_entry_id" if entries else "No loaded Octoplus account"
            )
        try:
            return await entries[0].runtime_data.async_claim_offer(call.data[ATTR_OFFER_SLUG])
        except OctoplusError as err:
            raise HomeAssistantError(f"Claim failed: {err}") from err

    hass.services.async_register(
        DOMAIN, SERVICE_CLAIM_OFFER, _claim, schema=CLAIM_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: OctoplusConfigEntry) -> bool:
    coordinator = OctoplusCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: OctoplusConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: OctoplusConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
