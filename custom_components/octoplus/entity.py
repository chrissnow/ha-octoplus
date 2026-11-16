"""Base entity for Octoplus."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OctoplusCoordinator


class OctoplusEntity(CoordinatorEntity[OctoplusCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: OctoplusCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.account}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.account)},
            name=f"Octoplus {coordinator.account}",
            manufacturer="Chris Snow",
            model="Octoplus",
            entry_type=DeviceEntryType.SERVICE,
        )
