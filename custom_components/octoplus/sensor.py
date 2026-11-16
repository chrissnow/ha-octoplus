"""Octoplus sensors."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import OctoplusConfigEntry
from .const import SCRATCH_STATES
from .coordinator import describe_offer
from .entity import OctoplusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OctoplusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    c = entry.runtime_data
    async_add_entities(
        [
            ScratchcardSensor(c, "scratchcard"),
            RewardsSensor(c, "rewards"),
            ActiveRewardsSensor(c, "active_rewards"),
            OffersSensor(c, "offers"),
            StampsSensor(c, "stamps"),
            OctopointsSensor(c, "octopoints"),
            WheelSpinsSensor(c, "wheel_spins"),
            HappyHourTokensSensor(c, "happy_hour_tokens"),
            HappyHourSensor(c, "happy_hour"),
        ]
    )


class ScratchcardSensor(OctoplusEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = SCRATCH_STATES
    _attr_icon = "mdi:ticket-percent"

    @property
    def native_value(self) -> str:
        sc = self.coordinator.data["scratch"]
        if not sc["activeSession"]:
            return "no_session"
        if not sc["scratchcard"]:
            return "not_scratched"
        return sc["scratchcard"]["status"].lower()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        sc = self.coordinator.data["scratch"]
        s = sc["activeSession"] or {}
        card = sc["scratchcard"] or {}
        return {
            "session_starts": s.get("startsAt"),
            "session_ends": s.get("endsAt"),
            "prize": describe_offer(card["offer"]) if card.get("offer") else None,
        }


class RewardsSensor(OctoplusEntity, SensorEntity):
    _attr_icon = "mdi:gift"
    _attr_native_unit_of_measurement = "rewards"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data["rewards"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "recent": [
                {
                    "name": describe_offer(r["claimedOffer"]),
                    "status": r["status"].lower(),
                    "claimed_at": r["claimedAt"],
                    "codes": [v.get("code") for v in r.get("vouchers", []) if v.get("code")],
                    "expires": next(
                        (v.get("expiresAt") for v in r.get("vouchers", []) if v.get("expiresAt")),
                        None,
                    ),
                }
                for r in self.coordinator.data["rewards"][:10]
            ]
        }


def _reward_summary(r: dict) -> dict[str, Any]:
    vouchers = r.get("vouchers", [])
    return {
        "name": describe_offer(r["claimedOffer"]),
        "status": r["status"].lower(),
        "claimed_at": r["claimedAt"],
        "codes": [v.get("code") for v in vouchers if v.get("code")],
        "expires": next((v.get("expiresAt") for v in vouchers if v.get("expiresAt")), None),
    }


class ActiveRewardsSensor(OctoplusEntity, SensorEntity):
    """Rewards whose vouchers have not yet expired."""

    _attr_icon = "mdi:ticket-confirmation"
    _attr_native_unit_of_measurement = "rewards"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data["active_rewards"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"rewards": [_reward_summary(r) for r in self.coordinator.data["active_rewards"]]}


REASONS = {
    "OUT_OF_STOCK": "Out of stock",
    "MAX_CLAIMS_PER_PERIOD_REACHED": "Already claimed",
    "INSUFFICIENT_POINTS": "Not enough points",
    "NO_ACTIVE_SCRATCHCARD_SESSION": "Scratchcard prize",
    "HAS_NOT_WON_OFFER": "Scratchcard prize",
    "NO_VALID_SCRATCHCARD_FOUND": "Scratchcard prize",
    "NO_EXCHANGEABLE_STAMPCARD": "Needs a full stampcard",
}


class OffersSensor(OctoplusEntity, SensorEntity):
    _attr_icon = "mdi:tag-multiple"
    _attr_native_unit_of_measurement = "offers"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data["offers"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        offers = []
        for o in self.coordinator.data["offers"][:40]:
            ability = o.get("claimAbility") or {}
            reason = ability.get("cannotClaimReason")
            offers.append(
                {
                    "slug": o["slug"],
                    "name": o.get("name"),
                    "partner": o.get("partnerName"),
                    "description": (o.get("description") or "")[:200],
                    "claim_by": o.get("claimBy"),
                    "points_cost": o.get("pointsCost") or 0,
                    "logo": (o.get("imageUrls") or {}).get("logo"),
                    "can_claim": ability.get("canClaimOffer") is not False,
                    "reason": REASONS.get(reason, reason.replace("_", " ").capitalize())
                    if reason else None,
                    "group_id": (o.get("group") or {}).get("id"),
                }
            )
        return {
            "account_number": self.coordinator.account,
            "config_entry_id": self.coordinator.config_entry.entry_id,
            "groups": self.coordinator.data.get("offer_groups", []),
            "offers": offers,
        }


class StampsSensor(OctoplusEntity, SensorEntity):
    """Stamps on the current (incomplete) stampcard."""

    _attr_icon = "mdi:stamper"
    _attr_native_unit_of_measurement = "stamps"

    @property
    def native_value(self) -> int:
        cards = self.coordinator.data["stampcards"]
        current = next((c for c in cards if c["status"] == "INCOMPLETE"), None)
        return current["totalStamps"] if current else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        cards = self.coordinator.data["stampcards"]
        ready = [c for c in cards if c["status"] == "EXCHANGEABLE"]
        return {
            "exchangeable_cards": len(ready),
            "next_expiry": min((c["expiresAt"] for c in ready if c.get("expiresAt")), default=None),
        }


class OctopointsSensor(OctoplusEntity, SensorEntity):
    _attr_icon = "mdi:star-circle"
    _attr_native_unit_of_measurement = "points"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.get("points") is not None

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.get("points")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "recent": [
                {
                    "type": h["entryType"].lower(),
                    "reason": h["reason"],
                    "value": h["value"],
                    "balance_after": h["balanceAfter"],
                    "posted_at": h["postedAt"],
                    "note": h.get("note"),
                }
                for h in self.coordinator.data.get("points_history", [])
            ]
        }


class WheelSpinsSensor(OctoplusEntity, SensorEntity):
    """Wheel of fortune spins available (electricity + gas)."""

    _attr_icon = "mdi:ship-wheel"
    _attr_native_unit_of_measurement = "spins"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.get("spins") is not None

    @property
    def native_value(self) -> int | None:
        sp = self.coordinator.data.get("spins") or {}
        return sum(((sp.get(f) or {}).get("spinsAllowed") or 0) for f in ("electricity", "gas"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        sp = self.coordinator.data.get("spins") or {}
        return {
            "electricity": (sp.get("electricity") or {}).get("spinsAllowed"),
            "gas": (sp.get("gas") or {}).get("spinsAllowed"),
            "prize_type": (sp.get("wheelOfFortunePrizeType") or "").lower() or None,
        }


class HappyHourTokensSensor(OctoplusEntity, SensorEntity):
    """Weekend Happy Hour tokens."""

    _attr_icon = "mdi:ticket"
    _attr_native_unit_of_measurement = "tokens"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.get("happy_hours") is not None

    @property
    def native_value(self) -> int | None:
        return (self.coordinator.data.get("happy_hours") or {}).get("tokens")


class HappyHourSensor(OctoplusEntity, SensorEntity):
    """Start of the next booked Weekend Happy Hour; slots listed as attributes."""

    _attr_icon = "mdi:party-popper"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.get("happy_hours") is not None

    def _next_booked(self) -> dict | None:
        hh = self.coordinator.data.get("happy_hours") or {}
        return next((sl for sl in hh.get("slots", []) if sl["booked"]), None)

    @property
    def native_value(self):
        sl = self._next_booked()
        return dt_util.parse_datetime(sl["start"]) if sl else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        hh = self.coordinator.data.get("happy_hours") or {}
        sl = self._next_booked() or {}
        return {
            "end": sl.get("end"),
            "event_code": sl.get("code"),
            "tokens": hh.get("tokens"),
            "joined_campaign": hh.get("joined_campaign"),
            "config_entry_id": self.coordinator.config_entry.entry_id,
            "slots": hh.get("slots", []),
        }
