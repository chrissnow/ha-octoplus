# Octoplus for Home Assistant

Custom integration for Octopus Energy's **Octoplus** rewards scheme:

- Scratches the weekly scratchcard automatically (or via a **Scratch now** button)
- Claims scratchcard prizes automatically (optional)
- Notifies about scratch results, new rewards (with voucher codes), new offers and completed stampcards
- A **Claim** button for every available offer, plus an `octoplus.claim_offer` action
- Octopoints balance, and wheel-of-fortune spins (auto-spin optional)
- Fires events for your own automations

## Install

Copy `custom_components/octoplus` to `/config/custom_components/` (or add this repo to HACS as a custom repository), restart, then **Settings → Devices & services → Add integration → Octoplus**. You need your Octopus API key (dashboard → Personal details → API access) and account number.

## Options

| Option | Default |
|---|---|
| Scratch automatically | off |
| Claim prizes automatically | off |
| Spin the wheel of fortune automatically | off |
| Check every (minutes) | 60 |
| Notify service | blank (e.g. `notify.mobile_app_phone`) |

## Entities

- `sensor.*_scratchcard`: `no_session`, `not_scratched`, `did_not_win`, `prize_not_yet_claimed`, `prize_claimed`, `prize_rejected`, `prize_not_claimed_in_time`
- `sensor.*_rewards`: count; `recent` attribute with names, statuses and voucher codes
- `sensor.*_offers`: count; `offers` attribute
- `sensor.*_stamps`: stamps on the current stampcard; `exchangeable_cards`
- `sensor.*_active_rewards`: rewards whose vouchers haven't expired; `rewards` attribute with codes
- `sensor.*_octopoints`: points balance; `recent` ledger entries
- `sensor.*_wheel_spins`: spins available (electricity + gas)
- `button.*_scratch_now`, `button.*_spin_wheel`
- `button.octoplus_claim_<offer>`: one per available offer, added/removed as offers change; attributes `partner`, `claim_by`, `can_claim`, `cannot_claim_reason`

## Actions

`octoplus.claim_offer` (`offer_slug`, optional `config_entry_id`) returns `reward_id`, `codes` and `expires`.

## Events

`octoplus_scratchcard`, `octoplus_prize_claimed`, `octoplus_offer_claimed`, `octoplus_wheel_spun`, `octoplus_new_reward`, `octoplus_new_offer`, `octoplus_stampcard_complete`. The first refresh after setup records existing rewards/offers silently.

## Tests

```bash
pip install -r requirements_test.txt
pytest
```

Queries are validated against `tests/octopus-backend.graphql` (introspected from `api.backend.octopus.energy`).
