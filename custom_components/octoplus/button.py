"""Octoplus buttons: scratch the scratchcard, spin the wheel."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import OctoplusConfigEntry
from .api import OctoplusError
from .entity import OctoplusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OctoplusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([ScratchButton(coordinator, "scratch"), SpinButton(coordinator, "spin")])

    # Per-offer claim buttons were replaced by the Octoplus card; remove leftovers.
    registry = er.async_get(hass)
    for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg.domain == "button" and reg.unique_id.startswith(f"{coordinator.account}_claim_"):
            registry.async_remove(reg.entity_id)


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
