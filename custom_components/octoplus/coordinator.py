"""Coordinator: polls Octoplus, auto-scratches/claims, fires events."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import OctoplusApi, OctoplusAuthError, OctoplusError
from .const import (
    CONF_ACCOUNT,
    CONF_AUTO_CLAIM,
    CONF_AUTO_SCRATCH,
    CONF_AUTO_SPIN,
    CONF_NOTIFY,
    CONF_SCAN_MINUTES,
    DEFAULT_AUTO_CLAIM,
    DEFAULT_AUTO_SCRATCH,
    DEFAULT_AUTO_SPIN,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
    EVENT_NEW_OFFER,
    EVENT_NEW_REWARD,
    EVENT_OFFER_CLAIMED,
    EVENT_PRIZE_CLAIMED,
    EVENT_SCRATCHCARD,
    EVENT_STAMPCARD_COMPLETE,
    EVENT_WHEEL_SPUN,
)

_LOGGER = logging.getLogger(__name__)
STORE_VERSION = 1


def describe_offer(o: dict | None) -> str:
    if not o:
        return "unknown offer"
    name = o.get("name") or o.get("slug") or "offer"
    return f"{name} ({o['partnerName']})" if o.get("partnerName") else name


class OctoplusCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.account: str = entry.data[CONF_ACCOUNT]
        opts = entry.options
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {self.account}",
            update_interval=timedelta(
                minutes=opts.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES)
            ),
        )
        self.api = OctoplusApi(
            async_get_clientsession(hass), entry.data["api_key"], self.account
        )
        self.auto_scratch = opts.get(CONF_AUTO_SCRATCH, DEFAULT_AUTO_SCRATCH)
        self.auto_claim = opts.get(CONF_AUTO_CLAIM, DEFAULT_AUTO_CLAIM)
        self.auto_spin = opts.get(CONF_AUTO_SPIN, DEFAULT_AUTO_SPIN)
        self.notify_service: str = (opts.get(CONF_NOTIFY) or "").strip()
        self._store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}.{self.account}")
        self._seen: dict[str, list] | None = None
        self._extra_errors: dict[str, str] = {}
        self._unsupported: set[str] = set()

    # ------------------------------------------------------------ helpers

    async def _notify(self, title: str, message: str) -> None:
        if not self.notify_service:
            return
        svc = self.notify_service.removeprefix("notify.")
        try:
            await self.hass.services.async_call(
                "notify", svc, {"title": title, "message": message}, blocking=False
            )
        except Exception as err:  # noqa: BLE001 - never fail the update on notify
            _LOGGER.warning("Notify %s failed: %s", svc, err)

    def _fire(self, event: str, data: dict) -> None:
        self.hass.bus.async_fire(event, {"account_number": self.account, **data})

    async def _claim(self, offer: dict) -> bool:
        slug = offer.get("slug")
        if not slug:
            return False
        try:
            reward_id = await self.api.claim(slug)
        except OctoplusError as err:
            _LOGGER.warning("Claiming %s failed: %s", slug, err)
            await self._notify("Octoplus: claim failed", f"{describe_offer(offer)}: {err}"[:300])
            return False
        self._fire(EVENT_PRIZE_CLAIMED, {"offer": offer, "reward_id": reward_id})
        return True

    # ------------------------------------------------------------ actions

    async def async_scratch(self, session_ref: str, *, from_button: bool = False) -> dict:
        card = await self.api.scratch(session_ref)
        prize = card.get("prize") or {}
        offer = card.get("offer") or (prize if prize.get("__typename") == "OctoplusOfferType" else None)
        stamps = prize.get("totalStampsAwarded") if prize.get("__typename") == "StampsAwardedType" else None

        claimed = False
        if card["status"] == "PRIZE_NOT_YET_CLAIMED" and offer and self.auto_claim:
            claimed = await self._claim(offer)

        if card["status"] == "DID_NOT_WIN":
            msg = "Scratched: no win this time."
        elif stamps:
            msg = f"Won {stamps} stamp(s)."
        else:
            msg = f"Won {describe_offer(offer)}." + (
                " Claimed automatically." if claimed else " Open the Octopus app to claim it."
            )
        self._fire(
            EVENT_SCRATCHCARD,
            {"status": card["status"], "offer": offer, "stamps": stamps,
             "claimed": claimed, "message": msg},
        )
        await self._notify("Octoplus scratchcard", msg)
        if from_button:
            await self.async_request_refresh()
        return card

    async def async_claim_offer(self, slug: str) -> dict[str, Any]:
        """Claim an offer by slug; returns the reward (with voucher codes) if found."""
        offers = {o["slug"]: o for o in (self.data or {}).get("offers", [])}
        offer = offers.get(slug, {"slug": slug})
        reward_id = await self.api.claim(slug)
        await self.async_refresh()
        reward = next(
            (r for r in (self.data or {}).get("rewards", [])
             if str(r["id"]) == str(reward_id)
             or (reward_id is None and r["claimedOffer"]["slug"] == slug)),
            None,
        )
        codes = [v.get("code") for v in (reward or {}).get("vouchers", []) if v.get("code")]
        result = {
            "offer_slug": slug,
            "offer": describe_offer(offer),
            "reward_id": reward_id,
            "codes": codes,
            "expires": next(
                (v.get("expiresAt") for v in (reward or {}).get("vouchers", []) if v.get("expiresAt")),
                None,
            ),
        }
        self._fire(EVENT_OFFER_CLAIMED, result)
        await self._notify(
            "Octoplus offer claimed",
            describe_offer(offer) + (f" (code: {', '.join(codes)})" if codes else ""),
        )
        return result

    def _extra_failed(self, what: str, err: Exception) -> None:
        """Log an optional-query failure once per distinct error."""
        msg = str(err)
        if "OE-0001" in msg:  # "feature not configured for this territory"
            self._unsupported.add(what)
            _LOGGER.debug("Octoplus %s not available for %s; not querying again", what, self.account)
            return
        if self._extra_errors.get(what) != msg:
            self._extra_errors[what] = msg
            _LOGGER.warning("Octoplus %s query failed for %s: %s", what, self.account, msg)

    async def _fetch_spins(self) -> dict[str, Any] | None:
        try:
            res = await self.api.spins()
        except OctoplusError as err:
            self._extra_failed("wheel of fortune", err)
            return None
        self._extra_errors.pop("wheel of fortune", None)
        return res

    async def _fetch_points(self) -> tuple[int | None, list[dict[str, Any]]]:
        balance, history = None, []
        try:
            balance = await self.api.points_balance()
            self._extra_errors.pop("points balance", None)
        except OctoplusError as err:
            self._extra_failed("points balance", err)
        if "points history" not in self._unsupported:
            try:
                history = await self.api.points_history()
                self._extra_errors.pop("points history", None)
            except OctoplusError as err:
                self._extra_failed("points history", err)
        if balance is None and history:
            balance = history[0].get("balanceAfter")
        return balance, history

    async def async_spin(self, fuel: str | None = None, *, from_button: bool = False) -> dict:
        """Spin the wheel once using a fuel type with spins left."""
        spins = await self._fetch_spins() or {}
        if fuel is None:
            fuel = next(
                (f for f in ("ELECTRICITY", "GAS")
                 if ((spins.get(f.lower()) or {}).get("spinsAllowed") or 0) > 0),
                None,
            )
        if fuel is None:
            raise OctoplusError("No wheel of fortune spins available")
        res = await self.api.spin(fuel)
        prize = res.get("prize") or {}
        unit = spins.get("wheelOfFortunePrizeType")
        msg = f"Won {prize.get('display') or prize.get('value')}" + (
            f" ({unit.replace('_', ' ').lower()})" if unit else ""
        )
        self._fire(EVENT_WHEEL_SPUN, {"fuel_type": fuel.lower(), "prize": prize,
                                      "prize_type": unit, "message": msg})
        await self._notify("Octopus wheel of fortune", msg)
        if from_button:
            await self.async_request_refresh()
        return res

    # ------------------------------------------------------------ update

    async def _async_update_data(self) -> dict[str, Any]:
        if self._seen is None:
            self._seen = await self._store.async_load()
        first_run = self._seen is None
        seen = self._seen or {"rewards": [], "offers": [], "stampcards": []}

        try:
            data = await self.api.status()
            if not data["isOctoplusEnrolled"]["isEnrolled"]:
                raise UpdateFailed(f"{self.account} is not enrolled in Octoplus")

            sc = data["octoplusActiveScratchcardData"]
            if self.auto_scratch and sc["activeSession"] and sc["scratchcard"] is None:
                sc["scratchcard"] = await self.async_scratch(
                    sc["activeSession"]["externalReference"]
                )
            elif (
                self.auto_claim
                and sc["scratchcard"]
                and sc["scratchcard"]["status"] == "PRIZE_NOT_YET_CLAIMED"
                and sc["scratchcard"].get("offer")
            ):
                offer = sc["scratchcard"]["offer"]
                if await self._claim(offer):
                    sc["scratchcard"]["status"] = "PRIZE_CLAIMED"
                    await self._notify("Octoplus prize claimed", describe_offer(offer))
        except OctoplusAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except OctoplusError as err:
            raise UpdateFailed(str(err)) from err

        # ---- wheel of fortune + points (optional extras; failures don't fail the update)
        spins = await self._fetch_spins()
        if self.auto_spin and spins:
            spun = False
            for fuel in ("ELECTRICITY", "GAS"):
                left = (spins.get(fuel.lower()) or {}).get("spinsAllowed") or 0
                for _ in range(min(left, 10)):
                    try:
                        await self.async_spin(fuel)
                        spun = True
                    except OctoplusError as err:
                        _LOGGER.warning("Wheel spin (%s) failed: %s", fuel, err)
                        break
            if spun:
                spins = await self._fetch_spins()
        points, points_history = await self._fetch_points()

        # ---- diff rewards / offers / stampcards (silent on first ever run)
        rewards = [r for r in (data.get("octoplusRewards") or []) if r]
        offers: list[dict[str, Any]] = []
        groups: list[dict[str, Any]] = []
        slugs_seen: set[str] = set()
        for e in (data.get("octoplusOfferGroups") or {}).get("edges", []):
            node = (e or {}).get("node") or {}
            group = {
                "id": node.get("id"),
                "name": node.get("name"),
                "type": node.get("groupType"),
                "category": (node.get("category") or {}).get("name"),
            }
            groups.append(group)
            for o in node.get("octoplusOffers", []):
                if o["slug"] in slugs_seen:
                    continue
                slugs_seen.add(o["slug"])
                offers.append({**o, "group": group})
        stampcards = [e["node"] for e in data["octoplusStampcards"]["edges"]]

        for r in rewards:
            if r["id"] in seen["rewards"]:
                continue
            seen["rewards"].append(r["id"])
            codes = [v.get("code") for v in r.get("vouchers", []) if v.get("code")]
            if not first_run:
                self._fire(EVENT_NEW_REWARD, {"reward": r, "codes": codes})
                msg = describe_offer(r["claimedOffer"]) + (
                    f" (code: {', '.join(codes)})" if codes else ""
                )
                await self._notify("New Octoplus reward", msg)

        new_offers = [o for o in offers if o["slug"] not in seen["offers"]]
        for o in new_offers:
            seen["offers"].append(o["slug"])
            if not first_run:
                self._fire(EVENT_NEW_OFFER, {"offer": o})
        if new_offers and not first_run:
            await self._notify(
                "New Octoplus offers", "; ".join(describe_offer(o) for o in new_offers)[:500]
            )

        for c in stampcards:
            if c["status"] == "EXCHANGEABLE" and c["externalReference"] not in seen["stampcards"]:
                seen["stampcards"].append(c["externalReference"])
                if not first_run:
                    self._fire(EVENT_STAMPCARD_COMPLETE, {"stampcard": c})
                    await self._notify(
                        "Octoplus stampcard complete",
                        "A stampcard is ready to exchange in the Octopus app.",
                    )

        # keep the store bounded
        for k in seen:
            seen[k] = seen[k][-500:]
        self._seen = seen
        await self._store.async_save(seen)

        rewards = sorted(rewards, key=lambda r: r["claimedAt"], reverse=True)
        now = dt_util.utcnow()

        def _usable(r: dict) -> bool:
            if r["status"] == "CANCELLED":
                return False
            exps = [dt_util.parse_datetime(v["expiresAt"]) for v in r.get("vouchers", [])
                    if v.get("expiresAt")]
            exps = [e for e in exps if e]
            return not exps or max(exps) > now

        return {
            "scratch": data["octoplusActiveScratchcardData"],
            "spins": spins,
            "points": points,
            "points_history": points_history,
            "rewards": rewards,
            "active_rewards": [r for r in rewards if _usable(r)],
            "offers": offers,
            "offer_groups": groups,
            "stampcards": stampcards,
        }
