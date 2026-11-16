"""Octoplus 'Scratch now' button."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import OctoplusConfigEntry
from .api import OctoplusError
from .entity import OctoplusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OctoplusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ScratchButton(entry.runtime_data, "scratch")])


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
