"""Octoplus integration: auto-scratch, claim and notify for Octopus Energy Octoplus."""
from __future__ import annotations

import hashlib
import logging
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

_LOGGER = logging.getLogger(__name__)

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


def _lovelace_resources(hass: HomeAssistant):
    """Return Lovelace's storage-mode resource collection, or None."""
    data = None
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA

        data = hass.data.get(LOVELACE_DATA)
    except ImportError:
        data = hass.data.get("lovelace")
    if data is None:
        return None
    res = getattr(data, "resources", None)
    if res is None and isinstance(data, dict):
        res = data.get("resources")
    # YAML-mode resources can't be changed via the API
    if res is None or not hasattr(res, "async_create_item"):
        return None
    return res


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the Octoplus card and register it as a dashboard resource.

    A Lovelace resource is fetched when a dashboard opens, so the card is
    available no matter when the page was loaded relative to HA start-up.
    Falls back to an extra JS URL when resources are in YAML mode.
    """
    if hass.http is None:  # e.g. in tests without http
        return
    from homeassistant.components.http import StaticPathConfig

    path = Path(__file__).parent / "frontend" / "octoplus-card.js"
    # Cache aggressively; the content hash in the URL busts the cache on any change.
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(path), cache_headers=True)]
    )
    digest = await hass.async_add_executor_job(
        lambda: hashlib.sha256(path.read_bytes()).hexdigest()[:10]
    )
    url = f"{CARD_URL}?v={digest}"

    resources = _lovelace_resources(hass)
    if resources is not None:
        try:
            if not getattr(resources, "loaded", True):
                await resources.async_load()
                resources.loaded = True
            mine = [r for r in resources.async_items() if str(r.get("url", "")).split("?")[0] == CARD_URL]
            if not mine:
                await resources.async_create_item({"res_type": "module", "url": url})
            else:
                if mine[0].get("url") != url:
                    await resources.async_update_item(mine[0]["id"], {"res_type": "module", "url": url})
                for extra in mine[1:]:
                    await resources.async_delete_item(extra["id"])
            return
        except Exception as err:  # noqa: BLE001 - fall back below
            _LOGGER.warning("Couldn't register Octoplus card as a dashboard resource: %s", err)

    from homeassistant.components.frontend import add_extra_js_url

    add_extra_js_url(hass, url)


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
