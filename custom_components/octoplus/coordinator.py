"""Coordinator: polls Octoplus, auto-scratches/claims, fires events."""
from __future__ import annotations

from datetime import time, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import OctoplusApi, OctoplusAuthError, OctoplusError
from .const import (
    CONF_ACCOUNT,
    CONF_AUTO_BOOK_HH,
    CONF_AUTO_CLAIM,
    CONF_AUTO_SCRATCH,
    CONF_AUTO_SPIN,
    CONF_HH_WINDOW_END,
    CONF_HH_WINDOW_START,
    CONF_NOTIFY,
    CONF_SCAN_MINUTES,
    DEFAULT_AUTO_BOOK_HH,
    DEFAULT_AUTO_CLAIM,
    DEFAULT_AUTO_SCRATCH,
    DEFAULT_AUTO_SPIN,
    DEFAULT_HH_WINDOW_END,
    DEFAULT_HH_WINDOW_START,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
    EVENT_HH_BOOKED,
    EVENT_HH_SLOTS,
    EVENT_NEW_OFFER,
    EVENT_NEW_REWARD,
    EVENT_OFFER_CLAIMED,
    EVENT_PRIZE_CLAIMED,
    EVENT_SCRATCHCARD,
    EVENT_STAMPCARD_COMPLETE,
    EVENT_WHEEL_SPUN,
    HH_FAST_POLL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)
STORE_VERSION = 1


def _parse_time(value: str | None, default: str) -> time:
    for v in (value, default):
        try:
            return time.fromisoformat(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return time(0, 0)


def _fmt_slot(sl: dict) -> str:
    start = dt_util.as_local(dt_util.parse_datetime(sl["start"]))
    end = dt_util.as_local(dt_util.parse_datetime(sl["end"]))
    return f"{start:%a %-d %b %H:%M}–{end:%H:%M}"


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
        self.auto_book_hh = opts.get(CONF_AUTO_BOOK_HH, DEFAULT_AUTO_BOOK_HH)
        self.hh_window = (
            _parse_time(opts.get(CONF_HH_WINDOW_START), DEFAULT_HH_WINDOW_START),
            _parse_time(opts.get(CONF_HH_WINDOW_END), DEFAULT_HH_WINDOW_END),
        )
        self._normal_interval = self.update_interval
        self._store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}.{self.account}")
        self._seen: dict[str, list] | None = None
        self._extra_errors: dict[str, str] = {}
        self._unsupported: set[str] = set()
        self._auto_booked: set[int] = set()

    # ------------------------------------------------------------ helpers

    @property
    def label(self) -> str:
        """Friendly name for this account: the device's user-set name, else the account number."""
        dev = dr.async_get(self.hass).async_get_device(identifiers={(DOMAIN, self.account)})
        if dev and dev.name_by_user:
            return dev.name_by_user.removeprefix("Octoplus ").strip() or self.account
        return self.account

    async def _notify(self, title: str, message: str) -> None:
        if not self.notify_service:
            return
        loaded = [
            e for e in self.hass.config_entries.async_entries(DOMAIN)
            if e.state is ConfigEntryState.LOADED
        ]
        if len(loaded) > 1:
            title = f"{title} · {self.label}"
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

    # ------------------------------------------------------------ happy hours

    async def _fetch_happy_hours(self) -> dict[str, Any] | None:
        if "happy hours" in self._unsupported:
            return None
        try:
            raw = await self.api.happy_hours()
            self._extra_errors.pop("happy hours", None)
        except OctoplusError as err:
            self._extra_failed("happy hours", err)
            return None
        now = dt_util.utcnow()
        acct = raw.get("account") or {}
        booked = {
            e["eventId"]
            for e in acct.get("joinedEvents") or []
            if e.get("eventType") == "WEEKEND_HAPPY_HOUR"
        }
        slots = []
        for ev in raw.get("events") or []:
            if ev.get("eventType") != "WEEKEND_HAPPY_HOUR":
                continue
            end = dt_util.parse_datetime(ev["endAt"])
            if end is None or end <= now:
                continue
            slots.append(
                {
                    "id": ev["id"],
                    "code": ev["code"],
                    "start": ev["startAt"],
                    "end": ev["endAt"],
                    "status": (ev.get("status") or "").lower(),
                    "capacity": (ev.get("capacityStatus") or "").lower() or None,
                    "booked": ev["id"] in booked,
                }
            )
        slots.sort(key=lambda x: x["start"])
        return {
            "joined_campaign": acct.get("hasJoinedCampaign"),
            "tokens": acct.get("tokenBalance"),
            "slots": slots,
        }

    def _in_window(self, slot: dict) -> bool:
        start = dt_util.as_local(dt_util.parse_datetime(slot["start"]))
        end = dt_util.as_local(dt_util.parse_datetime(slot["end"]))
        w_start, w_end = self.hh_window
        return start.time() >= w_start and (end - timedelta(seconds=1)).time() <= w_end

    async def async_book_happy_hour(self, code: str, *, refresh: bool = True) -> dict | None:
        res = await self.api.book_happy_hour(code)
        if refresh:
            await self.async_refresh()
        return res

    async def async_cancel_happy_hour(self, code: str) -> None:
        await self.api.cancel_happy_hour(code)
        await self.async_refresh()

    async def _auto_book(self, hh: dict[str, Any]) -> bool:
        """Book the earliest bookable slot in the window if none is booked yet."""
        if any(sl["booked"] for sl in hh["slots"]) or not hh.get("tokens"):
            return False
        for sl in hh["slots"]:
            if sl["status"] != "upcoming" or sl["capacity"] == "full" or not self._in_window(sl):
                continue
            try:
                await self.async_book_happy_hour(sl["code"], refresh=False)
                self._auto_booked.add(sl["id"])
                return True
            except OctoplusError as err:
                _LOGGER.info("Auto-booking Happy Hour %s failed: %s", sl["code"], err)
        return False

    def _adjust_interval(self, hh: dict[str, Any] | None) -> None:
        """Poll faster while auto-booking is waiting for (or racing for) a slot."""
        fast = False
        if self.auto_book_hh and hh and hh.get("tokens"):
            if not any(sl["booked"] for sl in hh["slots"]):
                # slots are usually announced on Thursdays for the weekend
                weekday = dt_util.now().weekday()
                fast = bool(hh["slots"]) or weekday in (3, 4, 5)
        self.update_interval = (
            timedelta(minutes=HH_FAST_POLL_MINUTES)
            if fast and self._normal_interval > timedelta(minutes=HH_FAST_POLL_MINUTES)
            else self._normal_interval
        )

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

        hh = await self._fetch_happy_hours()
        if hh and self.auto_book_hh and await self._auto_book(hh):
            hh = await self._fetch_happy_hours() or hh
        self._adjust_interval(hh)

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

        seen.setdefault("hh_slots", [])
        seen.setdefault("hh_booked", [])
        if hh:
            new_slots = [sl for sl in hh["slots"] if sl["id"] not in seen["hh_slots"]]
            for sl in new_slots:
                seen["hh_slots"].append(sl["id"])
            if new_slots and not first_run:
                self._fire(EVENT_HH_SLOTS, {"slots": new_slots, "tokens": hh.get("tokens")})
                await self._notify(
                    "Happy Hour slots announced",
                    ", ".join(_fmt_slot(sl) for sl in new_slots)
                    + f". You have {hh.get('tokens') or 0} token(s)."
                    + (" Auto-booking is on." if self.auto_book_hh else ""),
                )
            for sl in hh["slots"]:
                if sl["booked"] and sl["id"] not in seen["hh_booked"]:
                    seen["hh_booked"].append(sl["id"])
                    auto = sl["id"] in self._auto_booked
                    if auto or not first_run:
                        self._fire(EVENT_HH_BOOKED, {"slot": sl, "auto": auto})
                        await self._notify(
                            "Happy Hour auto-booked" if auto else "Happy Hour booked",
                            f"Free electricity {_fmt_slot(sl)}.",
                        )
            self._auto_booked.clear()

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
            "happy_hours": hh,
            "points": points,
            "points_history": points_history,
            "rewards": rewards,
            "active_rewards": [r for r in rewards if _usable(r)],
            "offers": offers,
            "offer_groups": groups,
            "stampcards": stampcards,
        }
