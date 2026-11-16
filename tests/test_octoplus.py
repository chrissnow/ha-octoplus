import re
from pathlib import Path
from unittest.mock import patch, AsyncMock
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from graphql import build_schema, parse, validate
from custom_components.octoplus import api as A
from custom_components.octoplus.const import DOMAIN

def test_queries_valid():
    t = open(Path(__file__).parent / 'octopus-backend.graphql').read()
    t = t[re.search(r'^("""|schema|type|directive|scalar)', t, re.M).start():]
    s = build_schema(t)
    for q in (A.Q_STATUS, A.M_SCRATCH, A.M_CLAIM):
        assert validate(s, parse(q)) == []

SESSION = {"externalReference": "11111111-1111-1111-1111-111111111111", "startsAt": "2026-11-16T00:00:00Z", "endsAt": "2026-11-23T00:00:00Z"}
OFFER = {"slug": "free-coffee", "name": "Free coffee", "partnerName": "Caffe Nero", "priceTag": "£3", "claimBy": None}
def status(scratch=None, rewards=()):
    return {
        "isOctoplusEnrolled": {"isEnrolled": True},
        "octoplusActiveScratchcardData": {"activeSession": SESSION, "scratchcard": scratch},
        "octoplusRewards": list(rewards),
        "octoplusOfferGroups": {"edges": [{"node": {"name": "Weekly", "octoplusOffers": [OFFER]}}]},
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
