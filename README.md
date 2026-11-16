# Octoplus for Home Assistant

Custom integration for Octopus Energy's **Octoplus** rewards scheme:

- Scratches the weekly scratchcard automatically (or via a **Scratch now** button)
- Claims scratchcard prizes automatically (optional)
- Notifies about scratch results, new rewards (with voucher codes), new offers and completed stampcards
- Fires events for your own automations

## Install

Copy `custom_components/octoplus` to `/config/custom_components/` (or add this repo to HACS as a custom repository), restart, then **Settings → Devices & services → Add integration → Octoplus**. You need your Octopus API key (dashboard → Personal details → API access) and account number.

## Options

| Option | Default |
|---|---|
| Scratch automatically | on |
| Claim prizes automatically | on |
| Check every (minutes) | 60 |
| Notify service | blank (e.g. `notify.mobile_app_phone`) |

## Entities

- `sensor.*_scratchcard`: `no_session`, `not_scratched`, `did_not_win`, `prize_not_yet_claimed`, `prize_claimed`, `prize_rejected`, `prize_not_claimed_in_time`
- `sensor.*_rewards`: count; `recent` attribute with names, statuses and voucher codes
- `sensor.*_offers`: count; `offers` attribute
- `sensor.*_stamps`: stamps on the current stampcard; `exchangeable_cards`
- `button.*_scratch_now`

## Events

`octoplus_scratchcard`, `octoplus_prize_claimed`, `octoplus_new_reward`, `octoplus_new_offer`, `octoplus_stampcard_complete`. The first refresh after setup records existing rewards/offers silently.

## Tests

```bash
pip install -r requirements_test.txt
pytest
```

Queries are validated against `tests/octopus-backend.graphql` (introspected from `api.backend.octopus.energy`).
