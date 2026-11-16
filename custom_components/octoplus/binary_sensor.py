"""Octoplus binary sensor: on during your booked Weekend Happy Hour."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from . import OctoplusConfigEntry
from .entity import OctoplusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OctoplusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HappyHourActive(entry.runtime_data, "happy_hour_active")])


class HappyHourActive(OctoplusEntity, BinarySensorEntity):
    _attr_icon = "mdi:lightning-bolt-circle"
    _unsub = None

    def _booked(self) -> list[tuple[datetime, datetime]]:
        hh = (self.coordinator.data or {}).get("happy_hours") or {}
        out = []
        for sl in hh.get("slots", []):
            if sl.get("booked"):
                start, end = dt_util.parse_datetime(sl["start"]), dt_util.parse_datetime(sl["end"])
                if start and end:
                    out.append((start, end))
        return out

    @property
    def is_on(self) -> bool:
        now = dt_util.utcnow()
        return any(start <= now < end for start, end in self._booked())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        now = dt_util.utcnow()
        cur = next(((s, e) for s, e in self._booked() if s <= now < e), None)
        nxt = next(((s, e) for s, e in sorted(self._booked()) if s > now), None)
        return {
            "ends": cur[1].isoformat() if cur else None,
            "next_start": nxt[0].isoformat() if nxt else None,
            "next_end": nxt[1].isoformat() if nxt else None,
        }

    def _schedule(self) -> None:
        """Re-evaluate exactly at the next booked start/end."""
        if self._unsub:
            self._unsub()
            self._unsub = None
        now = dt_util.utcnow()
        edges = sorted(t for pair in self._booked() for t in pair if t > now)
        if edges:
            self._unsub = async_track_point_in_utc_time(self.hass, self._at_edge, edges[0])

    @callback
    def _at_edge(self, _now: datetime) -> None:
        self._unsub = None
        self.async_write_ha_state()
        self._schedule()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._schedule()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._schedule()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        await super().async_will_remove_from_hass()
