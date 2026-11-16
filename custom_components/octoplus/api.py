"""Minimal async client for the Octopus Kraken GraphQL APIs (Octoplus parts)."""
from __future__ import annotations

import time
from typing import Any

import aiohttp

from .const import BACKEND_API, PUBLIC_API

TOKEN_LIFETIME = 50 * 60  # Kraken JWTs last ~60 min; refresh a bit early

OFFER_FIELDS = (
    "slug name description partnerName priceTag claimBy pointsCost "
    "imageUrls { logo } claimAbility { canClaimOffer cannotClaimReason }"
)

Q_STATUS = f"""
query($acc: String!) {{
  isOctoplusEnrolled(accountNumber: $acc) {{ isEnrolled }}
  octoplusActiveScratchcardData(accountNumber: $acc) {{
    activeSession {{ externalReference startsAt endsAt }}
    scratchcard {{
      externalReference status
      offer {{ {OFFER_FIELDS} }}
    }}
  }}
  octoplusRewards(accountNumber: $acc) {{
    id status claimedAt
    claimedOffer {{ slug name partnerName }}
    vouchers {{
      ... on OctoplusVoucherType {{ code expiresAt }}
      ... on ShoptopusVoucherType {{ code }}
    }}
  }}
  octoplusOfferGroups(accountNumber: $acc, first: 50) {{
    edges {{ node {{
      id name groupType category {{ name }}
      octoplusOffers {{ {OFFER_FIELDS} }}
    }} }}
  }}
  octoplusStampcards(accountNumber: $acc, first: 10) {{
    edges {{ node {{ externalReference status totalStamps startedAt expiresAt }} }}
  }}
}}"""

M_SCRATCH = f"""
mutation($acc: String!, $sess: UUID!) {{
  scratchOctoplusScratchcard(input: {{accountNumber: $acc,
                                     scratchcardSessionExternalReference: $sess}}) {{
    scratchcard {{
      externalReference status
      offer {{ {OFFER_FIELDS} }}
      prize {{
        __typename
        ... on OctoplusOfferType {{ {OFFER_FIELDS} }}
        ... on StampsAwardedType {{ totalStampsAwarded }}
      }}
    }}
  }}
}}"""

M_CLAIM = """
mutation($acc: String!, $slug: String!) {
  claimOctoplusReward(accountNumber: $acc, offerSlug: $slug) { rewardId }
}"""

Q_POINTS_BALANCE = """
query($acc: String!) { loyaltyPointsBalance(accountNumber: $acc) { balance } }"""

Q_POINTS_HISTORY = """
query($acc: String!) {
  loyaltyPointsHistory(accountNumber: $acc, first: 10) {
    edges { node { entryType reason value balanceAfter postedAt note } }
  }
}"""

Q_SPINS = """
query($acc: String!) {
  electricity: wheelOfFortuneSpinsAllowed(accountNumber: $acc, fuelType: ELECTRICITY) {
    spinsAllowed maxSpins
  }
  gas: wheelOfFortuneSpinsAllowed(accountNumber: $acc, fuelType: GAS) {
    spinsAllowed maxSpins
  }
  wheelOfFortunePrizeType(accountNumber: $acc)
}"""

M_SPIN = """
mutation($acc: String!, $fuel: FuelType!) {
  spinWheelOfFortune(input: {accountNumber: $acc, fuelType: $fuel}) {
    spinReference prize { value display }
  }
}"""

M_TOKEN = """
mutation($k: String!) { obtainKrakenToken(input: {APIKey: $k}) { token } }"""


class OctoplusError(Exception):
    """Generic API error."""


class OctoplusAuthError(OctoplusError):
    """API key rejected."""


class OctoplusApi:
    def __init__(self, session: aiohttp.ClientSession, api_key: str, account: str):
        self._session = session
        self._api_key = api_key
        self.account = account
        self._token: str | None = None
        self._token_at = 0.0

    async def _post(self, url: str, query: str, variables: dict, token: str | None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = token
        try:
            async with self._session.post(
                url,
                json={"query": query, "variables": variables},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status >= 500:
                    raise OctoplusError(f"HTTP {resp.status}")
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise OctoplusError(f"Request failed: {err}") from err
        if data.get("errors"):
            raise OctoplusError(str(data["errors"])[:500])
        return data["data"]

    async def _token_valid(self) -> str:
        if self._token and time.monotonic() - self._token_at < TOKEN_LIFETIME:
            return self._token
        try:
            data = await self._post(PUBLIC_API, M_TOKEN, {"k": self._api_key}, None)
        except OctoplusError as err:
            raise OctoplusAuthError(str(err)) from err
        self._token = data["obtainKrakenToken"]["token"]
        self._token_at = time.monotonic()
        return self._token

    async def gql(self, query: str, variables: dict | None = None) -> dict[str, Any]:
        variables = {"acc": self.account, **(variables or {})}
        token = await self._token_valid()
        try:
            return await self._post(BACKEND_API, query, variables, token)
        except OctoplusError as err:
            if "JWT" not in str(err) and "KT-CT-11" not in str(err):
                raise
            self._token = None  # expired/invalid token: retry once
            return await self._post(BACKEND_API, query, variables, await self._token_valid())

    async def status(self) -> dict[str, Any]:
        return await self.gql(Q_STATUS)

    async def scratch(self, session_ref: str) -> dict[str, Any]:
        d = await self.gql(M_SCRATCH, {"sess": session_ref})
        return d["scratchOctoplusScratchcard"]["scratchcard"]

    async def points_balance(self) -> int | None:
        d = await self.gql(Q_POINTS_BALANCE)
        return (d.get("loyaltyPointsBalance") or {}).get("balance")

    async def points_history(self) -> list[dict[str, Any]]:
        d = await self.gql(Q_POINTS_HISTORY)
        return [e["node"] for e in (d.get("loyaltyPointsHistory") or {}).get("edges", [])]

    async def spins(self) -> dict[str, Any]:
        return await self.gql(Q_SPINS)

    async def spin(self, fuel: str) -> dict[str, Any]:
        d = await self.gql(M_SPIN, {"fuel": fuel})
        return d["spinWheelOfFortune"]

    async def claim(self, slug: str) -> str | None:
        d = await self.gql(M_CLAIM, {"slug": slug})
        return d["claimOctoplusReward"]["rewardId"]
