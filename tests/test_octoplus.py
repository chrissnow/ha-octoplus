import re
from datetime import timedelta
import pytest
from homeassistant.exceptions import HomeAssistantError
from pathlib import Path
from unittest.mock import patch, AsyncMock
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from graphql import build_schema, parse, validate
from custom_components.octoplus import api as A
from custom_components.octoplus.const import DOMAIN

SPINS = {"electricity": {"spinsAllowed": 2, "maxSpins": 5}, "gas": {"spinsAllowed": 0, "maxSpins": 5}, "wheelOfFortunePrizeType": "LOYALTY_POINTS"}
HISTORY = [{"entryType": "CREDIT", "reason": "WHEEL", "value": 50, "balanceAfter": 1234, "postedAt": "2026-11-16T10:00:00Z", "note": None}]

@pytest.fixture(autouse=True)
def _extras():
    with patch.object(A.OctoplusApi, "happy_hours", AsyncMock(return_value={"account": {"hasJoinedCampaign": True, "tokenBalance": 0, "joinedEvents": []}, "events": []})), \
         patch.object(A.OctoplusApi, "spins", AsyncMock(return_value=SPINS)) as s, \
         patch.object(A.OctoplusApi, "points_balance", AsyncMock(return_value=1234)), \
         patch.object(A.OctoplusApi, "points_history", AsyncMock(return_value=HISTORY)):
        yield s

def test_queries_valid():
    t = open(Path(__file__).parent / 'octopus-backend.graphql').read()
    t = t[re.search(r'^("""|schema|type|directive|scalar)', t, re.M).start():]
    s = build_schema(t)
    for q in (A.Q_STATUS, A.M_SCRATCH, A.M_CLAIM, A.Q_POINTS_BALANCE, A.Q_POINTS_HISTORY, A.Q_SPINS, A.M_SPIN, A.Q_HAPPY_HOURS, A.M_BOOK_HAPPY_HOUR, A.M_CANCEL_HAPPY_HOUR):
        assert validate(s, parse(q)) == []

SESSION = {"externalReference": "11111111-1111-1111-1111-111111111111", "startsAt": "2026-11-16T00:00:00Z", "endsAt": "2026-11-23T00:00:00Z"}
OFFER = {"slug": "free-coffee", "name": "Free coffee", "description": "d", "pointsCost": 0, "imageUrls": {"logo": "https://x/l.png"}, "partnerName": "Caffe Nero", "priceTag": "£3", "claimBy": None, "claimAbility": {"canClaimOffer": True, "cannotClaimReason": None}}
OFFER2 = {**OFFER, "slug": "zoo", "name": "Zoo 2 for 1", "claimAbility": {"canClaimOffer": False, "cannotClaimReason": "OUT_OF_STOCK"}}
def status(scratch=None, rewards=(), offers=(OFFER,)):
    return {
        "isOctoplusEnrolled": {"isEnrolled": True},
        "octoplusActiveScratchcardData": {"activeSession": SESSION, "scratchcard": scratch},
        "octoplusRewards": list(rewards),
        "octoplusOfferGroups": {"edges": [{"node": {"id": 1, "name": "Weekly", "groupType": "STANDARD", "category": {"name": "Food"}, "octoplusOffers": list(offers)}}, {"node": {"id": 2, "name": "Dup", "groupType": "X", "category": {"name": None}, "octoplusOffers": [OFFER]}}]},
        "octoplusStampcards": {"edges": [{"node": {"externalReference": "x", "status": "INCOMPLETE", "totalStamps": 3, "startedAt": "2026-11-01T00:00:00Z", "expiresAt": None}}]},
    }

async def test_config_flow(hass: HomeAssistant):
    with patch.object(A.OctoplusApi, "gql", AsyncMock(return_value={"isOctoplusEnrolled": {"isEnrolled": True}})):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"api_key": "sk_live_x", "account_number": "a-1d6e1e1a"})
    assert r["type"] == "create_entry" and r["data"]["account_number"] == "A-1D6E1E1A"

async def test_setup_scratch_claim(hass: HomeAssistant):
    calls = []
    hass.services.async_register("notify", "phone", lambda c: calls.append(c.data))
    entry = MockConfigEntry(domain=DOMAIN, unique_id="A-1", data={"api_key": "k", "account_number": "A-1"},
                            options={"notify_service": "notify.phone", "auto_scratch": True, "auto_claim": True})
    entry.add_to_hass(hass)
    ev = async_capture_events(hass, "octoplus_scratchcard")
    rew = async_capture_events(hass, "octoplus_new_reward")
    card = {"externalReference": "22222222-2222-2222-2222-222222222222", "status": "PRIZE_NOT_YET_CLAIMED", "offer": OFFER, "prize": {"__typename": "OctoplusOfferType", **OFFER}}
    with patch.object(A.OctoplusApi, "status", AsyncMock(return_value=status())), \
         patch.object(A.OctoplusApi, "scratch", AsyncMock(return_value=card)) as sc, \
         patch.object(A.OctoplusApi, "claim", AsyncMock(return_value="99")) as cl:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    sc.assert_awaited_once(); cl.assert_awaited_once_with("free-coffee")
    assert ev[0].data["claimed"] is True
    assert any("Free coffee" in c["message"] for c in calls)
    st = hass.states.get("sensor.octoplus_a_1_scratchcard")
    assert st.state == "prize_not_yet_claimed", st
    assert hass.states.get("sensor.octoplus_a_1_stamps").state == "3"
    assert hass.states.get("button.octoplus_a_1_scratch_now") is not None
    # second poll: a new reward appears -> event + notify
    reward = {"id": 5, "status": "ISSUED", "claimedAt": "2026-11-16T10:00:00Z", "claimedOffer": {"slug": "free-coffee", "name": "Free coffee", "partnerName": "Caffe Nero"}, "vouchers": [{"code": "ABC123", "expiresAt": None}]}
    done = dict(card, status="PRIZE_CLAIMED")
    with patch.object(A.OctoplusApi, "status", AsyncMock(return_value=status(done, [reward]))):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
    assert rew and rew[0].data["codes"] == ["ABC123"]
    assert any("ABC123" in c["message"] for c in calls)
    assert hass.states.get("sensor.octoplus_a_1_rewards").state == "1"
    assert hass.states.get("sensor.octoplus_a_1_scratchcard").state == "prize_claimed"

async def test_defaults_off_no_scratch(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="A-2", data={"api_key": "k", "account_number": "A-2"})
    entry.add_to_hass(hass)
    with patch.object(A.OctoplusApi, "status", AsyncMock(return_value=status())), \
         patch.object(A.OctoplusApi, "scratch", AsyncMock()) as sc, \
         patch.object(A.OctoplusApi, "claim", AsyncMock()) as cl:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    sc.assert_not_awaited(); cl.assert_not_awaited()
    assert hass.states.get("sensor.octoplus_a_2_scratchcard").state == "not_scratched"
    assert hass.states.get("button.octoplus_a_2_scratch_now").state != "unavailable"


async def test_offers_attrs_service_and_old_button_cleanup(hass: HomeAssistant):
    from homeassistant.helpers import entity_registry as er
    entry = MockConfigEntry(domain=DOMAIN, unique_id="A-3", data={"api_key": "k", "account_number": "A-3"})
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    reg.async_get_or_create("button", DOMAIN, "A-3_claim_old-offer", config_entry=entry)
    reward = {"id": 7, "status": "ISSUED", "claimedAt": "2026-11-16T10:00:00Z", "claimedOffer": {"slug": "free-coffee", "name": "Free coffee", "partnerName": "Caffe Nero"}, "vouchers": [{"code": "XYZ", "expiresAt": "2099-01-01T00:00:00+00:00"}]}
    old = {**reward, "id": 6, "vouchers": [{"code": "OLD", "expiresAt": "2020-01-01T00:00:00+00:00"}]}
    st = AsyncMock(return_value=status(offers=(OFFER, OFFER2), rewards=[old]))
    claimed = async_capture_events(hass, "octoplus_offer_claimed")
    with patch.object(A.OctoplusApi, "status", st), \
         patch.object(A.OctoplusApi, "claim", AsyncMock(return_value="7")) as cl:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert not [e for e in er.async_entries_for_config_entry(reg, entry.entry_id) if "_claim_" in e.unique_id]
        attrs = hass.states.get("sensor.octoplus_a_3_offers").attributes
        assert attrs["config_entry_id"] == entry.entry_id
        by = {o["slug"]: o for o in attrs["offers"]}
        assert by["free-coffee"]["can_claim"] is True and by["free-coffee"]["partner"] == "Caffe Nero"
        assert by["zoo"]["can_claim"] is False and by["zoo"]["reason"] == "Out of stock"
        assert by["free-coffee"]["group_id"] == 1 and len(attrs["offers"]) == 2  # deduped across groups
        assert attrs["groups"][0] == {"id": 1, "name": "Weekly", "type": "STANDARD", "category": "Food"}
        assert hass.states.get("sensor.octoplus_a_3_active_rewards").state == "0"
        st.return_value = status(offers=(OFFER, OFFER2), rewards=[old, reward])
        resp = await hass.services.async_call("octoplus", "claim_offer", {"offer_slug": "free-coffee", "config_entry_id": entry.entry_id}, blocking=True, return_response=True)
        cl.assert_awaited_with("free-coffee")
        assert resp["codes"] == ["XYZ"] and resp["reward_id"] == "7"
        assert claimed[-1].data["codes"] == ["XYZ"]
        assert hass.states.get("sensor.octoplus_a_3_active_rewards").attributes["rewards"][0]["codes"] == ["XYZ"]


async def test_points_and_auto_spin(hass: HomeAssistant, _extras):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="A-4", data={"api_key": "k", "account_number": "A-4"}, options={"auto_spin": True})
    entry.add_to_hass(hass)
    spun = async_capture_events(hass, "octoplus_wheel_spun")
    after = {**SPINS, "electricity": {"spinsAllowed": 0, "maxSpins": 5}}
    _extras.side_effect = [SPINS, SPINS, SPINS, after]  # update, spin#1, spin#2, re-fetch
    with patch.object(A.OctoplusApi, "status", AsyncMock(return_value=status())), \
         patch.object(A.OctoplusApi, "spin", AsyncMock(return_value={"spinReference": "x", "prize": {"value": 50, "display": "50"}})) as sp:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert sp.await_count == 2 and sp.await_args.args == ("ELECTRICITY",)
    assert len(spun) == 2 and spun[0].data["prize"]["value"] == 50
    assert hass.states.get("sensor.octoplus_a_4_octopoints").state == "1234"
    assert hass.states.get("sensor.octoplus_a_4_wheel_spins").state == "0"
    assert hass.states.get("button.octoplus_a_4_spin_wheel").state == "unavailable"

async def test_extras_failure_does_not_break(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="A-5", data={"api_key": "k", "account_number": "A-5"})
    entry.add_to_hass(hass)
    with patch.object(A.OctoplusApi, "status", AsyncMock(return_value=status())), \
         patch.object(A.OctoplusApi, "spins", AsyncMock(side_effect=A.OctoplusError("nope"))), \
         patch.object(A.OctoplusApi, "points_balance", AsyncMock(side_effect=A.OctoplusError("nope"))), \
         patch.object(A.OctoplusApi, "points_history", AsyncMock(side_effect=A.OctoplusError("nope"))):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert hass.states.get("sensor.octoplus_a_5_octopoints").state == "unavailable"
    assert hass.states.get("sensor.octoplus_a_5_offers").state == "1"


async def test_two_accounts(hass: HomeAssistant):
    e1 = MockConfigEntry(domain=DOMAIN, unique_id="A-6", data={"api_key": "k1", "account_number": "A-6"})
    e2 = MockConfigEntry(domain=DOMAIN, unique_id="A-7", data={"api_key": "k2", "account_number": "A-7"})
    e1.add_to_hass(hass); e2.add_to_hass(hass)
    with patch.object(A.OctoplusApi, "status", AsyncMock(return_value=status())), \
         patch.object(A.OctoplusApi, "claim", AsyncMock(return_value="1")) as cl:
        assert await hass.config_entries.async_setup(e1.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get("sensor.octoplus_a_6_offers") and hass.states.get("sensor.octoplus_a_7_offers")
        assert hass.states.get("sensor.octoplus_a_7_octopoints").state == "1234"
        with pytest.raises(Exception, match="config_entry_id"):
            await hass.services.async_call("octoplus", "claim_offer", {"offer_slug": "free-coffee"}, blocking=True, return_response=True)
        await hass.services.async_call("octoplus", "claim_offer", {"offer_slug": "free-coffee", "config_entry_id": e2.entry_id}, blocking=True, return_response=True)
        assert cl.await_count == 1
    assert e1.runtime_data.api.account == "A-6" and e2.runtime_data.api.account == "A-7"


async def test_points_fall_back_to_history(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="A-8", data={"api_key": "k", "account_number": "A-8"})
    entry.add_to_hass(hass)
    with patch.object(A.OctoplusApi, "status", AsyncMock(return_value=status())), \
         patch.object(A.OctoplusApi, "points_balance", AsyncMock(side_effect=A.OctoplusError("denied"))):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert hass.states.get("sensor.octoplus_a_8_octopoints").state == "1234"


async def test_unsupported_history_not_retried(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="A-9", data={"api_key": "k", "account_number": "A-9"})
    entry.add_to_hass(hass)
    with patch.object(A.OctoplusApi, "status", AsyncMock(return_value=status())), \
         patch.object(A.OctoplusApi, "points_history", AsyncMock(side_effect=A.OctoplusError("OE-0001 territory"))) as h:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await entry.runtime_data.async_refresh()
    assert h.await_count == 1
    assert hass.states.get("sensor.octoplus_a_9_octopoints").state == "1234"
    from homeassistant.helpers import device_registry as dr
    dev = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "A-9")})
    assert dev.manufacturer == "Chris Snow"


class _FakeResources:
    loaded = True

    def __init__(self, items):
        self.items = [dict(i) for i in items]

    def async_items(self):
        return self.items

    async def async_create_item(self, data):
        self.items.append({"id": "new", **data})

    async def async_update_item(self, item_id, data):
        next(i for i in self.items if i["id"] == item_id).update(data)

    async def async_delete_item(self, item_id):
        self.items = [i for i in self.items if i["id"] != item_id]


async def test_card_resource_registration(hass: HomeAssistant):
    from unittest.mock import MagicMock
    import custom_components.octoplus as integ

    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    # create when missing
    res = _FakeResources([{"id": "x", "url": "/local/other.js", "res_type": "module"}])
    with patch.object(integ, "_lovelace_resources", return_value=res):
        await integ._async_register_card(hass)
    mine = [i for i in res.items if i["url"].startswith("/octoplus/")]
    assert len(mine) == 1 and mine[0]["url"].startswith("/octoplus/octoplus-card.js?v=")
    # update stale version and drop duplicates; leave others alone
    res = _FakeResources([
        {"id": "a", "url": "/octoplus/octoplus-card.js?v=old", "res_type": "module"},
        {"id": "b", "url": "/octoplus/octoplus-card.js?v=older", "res_type": "module"},
        {"id": "x", "url": "/local/other.js", "res_type": "module"},
    ])
    with patch.object(integ, "_lovelace_resources", return_value=res):
        await integ._async_register_card(hass)
    ids = [i["id"] for i in res.items]
    assert ids == ["a", "x"] and res.items[0]["url"] == mine[0]["url"]
    # YAML mode -> falls back to extra JS url
    with patch.object(integ, "_lovelace_resources", return_value=None), \
         patch("homeassistant.components.frontend.add_extra_js_url") as add:
        await integ._async_register_card(hass)
    add.assert_called_once()



def _hh(events, tokens=4, booked=()):
    return {
        "account": {"hasJoinedCampaign": True, "tokenBalance": tokens,
                    "joinedEvents": [{"eventId": i, "startAt": "x", "endAt": "y", "eventStatus": "UPCOMING", "eventType": "WEEKEND_HAPPY_HOUR"} for i in booked]},
        "events": events,
    }


def _ev(i, start, hours=1, cap="AVAILABLE", status="UPCOMING", etype="WEEKEND_HAPPY_HOUR"):
    from homeassistant.util import dt as dt_util
    s = dt_util.as_utc(start)
    return {"id": i, "code": f"HH{i}", "startAt": s.isoformat(), "endAt": (s + timedelta(hours=hours)).isoformat(),
            "status": status, "eventType": etype, "capacityStatus": cap}


async def test_happy_hours_auto_book_in_window(hass: HomeAssistant):
    from homeassistant.util import dt as dt_util
    await hass.config.async_set_time_zone("Europe/London")
    sunday = (dt_util.now() + timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    evs = [
        _ev(1, sunday.replace(hour=9)),            # outside window
        _ev(2, sunday.replace(hour=11), cap="FULL"),  # full
        _ev(3, sunday.replace(hour=12)),           # pick this
        _ev(4, sunday.replace(hour=13)),
        _ev(9, sunday.replace(hour=18), etype="TURN_DOWN"),  # not a happy hour
    ]
    calls = []
    hass.services.async_register("notify", "phone", lambda c: calls.append(c.data))
    entry = MockConfigEntry(domain=DOMAIN, unique_id="B-1", data={"api_key": "k", "account_number": "B-1"},
                            options={"notify_service": "notify.phone", "auto_book_happy_hour": True, "happy_hour_window_start": "10:30:00", "happy_hour_window_end": "15:00:00"})
    entry.add_to_hass(hass)
    hh = AsyncMock(side_effect=[_hh(evs), _hh(evs, tokens=2, booked=[3])])
    with patch.object(A.OctoplusApi, "status", AsyncMock(return_value=status())), \
         patch.object(A.OctoplusApi, "happy_hours", hh), \
         patch.object(A.OctoplusApi, "book_happy_hour", AsyncMock(return_value={"eventId": 3})) as book:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    book.assert_awaited_once_with("HH3")
    assert [c["title"] for c in calls] == ["Happy Hour auto-booked"]  # first run, but auto-bookings still notify
    st = hass.states.get("sensor.octoplus_b_1_happy_hour")
    assert st.attributes["event_code"] == "HH3"
    assert [s["code"] for s in st.attributes["slots"]] == ["HH1", "HH2", "HH3", "HH4"]
    assert hass.states.get("sensor.octoplus_b_1_happy_hour_tokens").state == "2"
    assert hass.states.get("binary_sensor.octoplus_b_1_happy_hour_active").state == "off"
    # booked -> back to normal polling
    assert entry.runtime_data.update_interval == timedelta(minutes=60)


async def test_happy_hours_no_auto_book_by_default_and_fast_poll_off(hass: HomeAssistant):
    from homeassistant.util import dt as dt_util
    start = dt_util.now() + timedelta(days=3)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="B-2", data={"api_key": "k", "account_number": "B-2"})
    entry.add_to_hass(hass)
    with patch.object(A.OctoplusApi, "status", AsyncMock(return_value=status())), \
         patch.object(A.OctoplusApi, "happy_hours", AsyncMock(return_value=_hh([_ev(5, start)]))), \
         patch.object(A.OctoplusApi, "book_happy_hour", AsyncMock()) as book:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    book.assert_not_awaited()
    assert entry.runtime_data.update_interval == timedelta(minutes=60)
    assert hass.states.get("sensor.octoplus_b_2_happy_hour").state == "unknown"


async def test_happy_hours_fast_poll_while_waiting(hass: HomeAssistant):
    from homeassistant.util import dt as dt_util
    start = dt_util.now() + timedelta(days=3)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="B-3", data={"api_key": "k", "account_number": "B-3"},
                            options={"auto_book_happy_hour": True, "happy_hour_window_start": "00:00:00", "happy_hour_window_end": "00:00:01"})
    entry.add_to_hass(hass)
    with patch.object(A.OctoplusApi, "status", AsyncMock(return_value=status())), \
         patch.object(A.OctoplusApi, "happy_hours", AsyncMock(return_value=_hh([_ev(6, start)]))), \
         patch.object(A.OctoplusApi, "book_happy_hour", AsyncMock()) as book:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    book.assert_not_awaited()  # nothing in window
    assert entry.runtime_data.update_interval == timedelta(minutes=10)


async def test_happy_hours_notifications_and_services(hass: HomeAssistant):
    from homeassistant.util import dt as dt_util
    calls = []
    hass.services.async_register("notify", "phone", lambda c: calls.append(c.data))
    start = dt_util.now() + timedelta(days=2)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="B-4", data={"api_key": "k", "account_number": "B-4"},
                            options={"notify_service": "notify.phone"})
    entry.add_to_hass(hass)
    slots_ev = async_capture_events(hass, "octoplus_happy_hour_slots")
    booked_ev = async_capture_events(hass, "octoplus_happy_hour_booked")
    hh = AsyncMock(return_value=_hh([]))
    with patch.object(A.OctoplusApi, "status", AsyncMock(return_value=status())), \
         patch.object(A.OctoplusApi, "happy_hours", hh), \
         patch.object(A.OctoplusApi, "book_happy_hour", AsyncMock(return_value={"eventId": 7})) as book, \
         patch.object(A.OctoplusApi, "cancel_happy_hour", AsyncMock()) as cancel:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        hh.return_value = _hh([_ev(7, start), _ev(8, start + timedelta(hours=1))])
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert slots_ev and len(slots_ev[0].data["slots"]) == 2
        assert any(c["title"].startswith("Happy Hour slots announced") for c in calls)
        hh.return_value = _hh([_ev(7, start), _ev(8, start + timedelta(hours=1))], booked=[7])
        await hass.services.async_call("octoplus", "book_happy_hour", {"event_code": "HH7"}, blocking=True, return_response=True)
        await hass.async_block_till_done()
        book.assert_awaited_with("HH7")
        assert booked_ev and booked_ev[0].data["slot"]["code"] == "HH7"
        assert any(c["title"].startswith("Happy Hour booked") for c in calls)
        await hass.services.async_call("octoplus", "cancel_happy_hour", {"event_code": "HH7"}, blocking=True)
        cancel.assert_awaited_with("HH7")


async def test_happy_hour_binary_sensor_flips_on_time(hass: HomeAssistant):
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed
    now = dt_util.utcnow()
    start = now + timedelta(minutes=5)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="B-5", data={"api_key": "k", "account_number": "B-5"})
    entry.add_to_hass(hass)
    with patch.object(A.OctoplusApi, "status", AsyncMock(return_value=status())), \
         patch.object(A.OctoplusApi, "happy_hours", AsyncMock(return_value=_hh([_ev(11, start)], booked=[11]))):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        eid = "binary_sensor.octoplus_b_5_happy_hour_active"
        assert hass.states.get(eid).state == "off"
        with patch("homeassistant.util.dt.utcnow", return_value=start + timedelta(seconds=1)):
            async_fire_time_changed(hass, start + timedelta(seconds=1))
            await hass.async_block_till_done()
            assert hass.states.get(eid).state == "on"
        with patch("homeassistant.util.dt.utcnow", return_value=start + timedelta(hours=1, seconds=1)):
            async_fire_time_changed(hass, start + timedelta(hours=1, seconds=1))
            await hass.async_block_till_done()
            assert hass.states.get(eid).state == "off"
