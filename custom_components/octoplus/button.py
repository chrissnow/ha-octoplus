"""Octoplus buttons: 'Scratch now' plus one 'Claim' button per available offer."""
from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from . import OctoplusConfigEntry
from .api import OctoplusError
from .coordinator import OctoplusCoordinator
from .entity import OctoplusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OctoplusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([ScratchButton(coordinator, "scratch"), SpinButton(coordinator, "spin")])

    current: dict[str, OfferClaimButton] = {}
    prefix = f"{coordinator.account}_claim_"
    registry = er.async_get(hass)

    @callback
    def _sync_offer_buttons() -> None:
        if not coordinator.last_update_success or coordinator.data is None:
            return
        offers = {o["slug"]: o for o in coordinator.data["offers"]}
        new = [OfferClaimButton(coordinator, slug) for slug in offers if slug not in current]
        for b in new:
            current[b.slug] = b
        if new:
            async_add_entities(new)
        # Remove buttons for offers that have gone (including ones left over
        # in the registry from before a restart).
        for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
            if reg.domain != "button" or not reg.unique_id.startswith(prefix):
                continue
            slug = reg.unique_id.removeprefix(prefix)
            if slug not in offers:
                current.pop(slug, None)
                registry.async_remove(reg.entity_id)

    _sync_offer_buttons()
    entry.async_on_unload(coordinator.async_add_listener(_sync_offer_buttons))


class ScratchButton(OctoplusEntity, ButtonEntity):
    _attr_icon = "mdi:gesture-tap"

    @property
    def available(self) -> bool:
        sc = (self.coordinator.data or {}).get("scratch") or {}
        return super().available and bool(sc.get("activeSession")) and not sc.get("scratchcard")

    async def async_press(self) -> None:
        sc = self.coordinator.data["scratch"]
        if not sc["activeSession"] or sc["scratchcard"]:
            raise HomeAssistantError("No unscratched scratchcard right now")
        try:
            await self.coordinator.async_scratch(
                sc["activeSession"]["externalReference"], from_button=True
            )
        except OctoplusError as err:
            raise HomeAssistantError(f"Scratch failed: {err}") from err


class SpinButton(OctoplusEntity, ButtonEntity):
    _attr_icon = "mdi:ship-wheel"

    @property
    def available(self) -> bool:
        sp = (self.coordinator.data or {}).get("spins") or {}
        return super().available and any(
            ((sp.get(f) or {}).get("spinsAllowed") or 0) > 0 for f in ("electricity", "gas")
        )

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_spin(from_button=True)
        except OctoplusError as err:
            raise HomeAssistantError(f"Spin failed: {err}") from err


class OfferClaimButton(OctoplusEntity, ButtonEntity):
    """Claims one Octoplus offer."""

    _attr_icon = "mdi:gift-open"

    def __init__(self, coordinator: OctoplusCoordinator, slug: str) -> None:
        super().__init__(coordinator, f"claim_{slug}")
        self.slug = slug
        self._attr_translation_key = None
        self.entity_id = f"button.octoplus_claim_{slugify(slug)}"

    @property
    def _offer(self) -> dict[str, Any] | None:
        for o in (self.coordinator.data or {}).get("offers", []):
            if o["slug"] == self.slug:
                return o
        return None

    @property
    def name(self) -> str:
        o = self._offer or {}
        return o.get("name") or self.slug

    @property
    def available(self) -> bool:
        return super().available and self._offer is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        o = self._offer or {}
        ability = o.get("claimAbility") or {}
        return {
            "offer_slug": self.slug,
            "partner": o.get("partnerName"),
            "description": o.get("description"),
            "price_tag": o.get("priceTag"),
            "claim_by": o.get("claimBy"),
            "can_claim": ability.get("canClaimOffer"),
            "cannot_claim_reason": (ability.get("cannotClaimReason") or "").lower() or None,
        }

    async def async_press(self) -> None:
        ability = (self._offer or {}).get("claimAbility") or {}
        if ability.get("canClaimOffer") is False:
            reason = (ability.get("cannotClaimReason") or "unknown").replace("_", " ").lower()
            raise HomeAssistantError(f"Can't claim this offer: {reason}")
        try:
            await self.coordinator.async_claim_offer(self.slug)
        except OctoplusError as err:
            raise HomeAssistantError(f"Claim failed: {err}") from err
