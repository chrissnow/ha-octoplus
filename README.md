# Octoplus for Home Assistant

Custom integration for Octopus Energy's **Octoplus** rewards scheme:

- Scratches the weekly scratchcard automatically (or via a **Scratch now** button)
- Claims scratchcard prizes automatically (optional)
- Notifies about scratch results, new rewards (with voucher codes), new offers and completed stampcards
- An **Octoplus card** (bundled, no other cards needed): offers with Claim buttons, active rewards with codes, points, spins and scratchcard
- An `octoplus.claim_offer` action for automations
- Octopoints balance, and wheel-of-fortune spins (auto-spin optional)
- **Weekend Happy Hours**: tokens, this weekend's slots with capacity, book/cancel from the card, optional auto-booking inside a time window, and a binary sensor that's on during your booked hour
- Fires events for your own automations

## Install

Copy `custom_components/octoplus` to `/config/custom_components/` (or add this repo to HACS as a custom repository), restart, then **Settings → Devices & services → Add integration → Octoplus**. You need your Octopus API key (dashboard → Personal details → API access) and account number.

## Options

| Option | Default |
|---|---|
| Scratch automatically | off |
| Claim prizes automatically | off |
| Spin the wheel of fortune automatically | off |
| Book Weekend Happy Hours automatically | off |
| Auto-book window (earliest start / latest end) | any time |
| Check every (minutes) | 60 |
| Notify service | blank (e.g. `notify.mobile_app_phone`) |

## Entities

- `sensor.*_scratchcard`: `no_session`, `not_scratched`, `did_not_win`, `prize_not_yet_claimed`, `prize_claimed`, `prize_rejected`, `prize_not_claimed_in_time`
- `sensor.*_rewards`: count; `recent` attribute with names, statuses and voucher codes
- `sensor.*_offers`: count; `offers` attribute (name, partner, logo, claim_by, can_claim, reason)
- `sensor.*_stamps`: stamps on the current stampcard; `exchangeable_cards`
- `sensor.*_active_rewards`: rewards whose vouchers haven't expired; `rewards` attribute with codes
- `sensor.*_octopoints`: points balance; `recent` ledger entries
- `sensor.*_wheel_spins`: spins available (electricity + gas)
- `sensor.*_happy_hour_tokens`: Happy Hour tokens
- `sensor.*_happy_hour`: start of your next booked Happy Hour; `slots` attribute lists this weekend's slots (code, start, end, capacity, booked)
- `binary_sensor.*_happy_hour_active`: on during your booked Happy Hour (switches exactly on time) — use it to charge batteries/EVs
- `button.*_scratch_now`, `button.*_spin_wheel`

## Card

The integration serves the card itself, so it's available on every dashboard after a restart:

```yaml
type: custom:octoplus-card
# entity: sensor.octoplus_a_1234abcd_offers   # optional, auto-detected
# hide_unclaimable: true
# show_rewards: false
```

The card has a visual editor (account, title, options). Claiming takes two taps (Claim → Confirm?). Unclaimable offers are greyed out with the reason.

## Actions

`octoplus.claim_offer` (`offer_slug`, optional `config_entry_id`) returns `reward_id`, `codes` and `expires`.

`octoplus.book_happy_hour` / `octoplus.cancel_happy_hour` (`event_code`, optional `config_entry_id`).

With auto-booking on, the integration checks every 10 minutes from Thursday (when slots are usually announced) until a slot is booked, then goes back to the normal interval.

## Events

`octoplus_scratchcard`, `octoplus_prize_claimed`, `octoplus_offer_claimed`, `octoplus_wheel_spun`, `octoplus_happy_hour_slots`, `octoplus_happy_hour_booked`, `octoplus_new_reward`, `octoplus_new_offer`, `octoplus_stampcard_complete`. The first refresh after setup records existing rewards/offers silently.

## Tests

```bash
pip install -r requirements_test.txt
pytest
```

Queries are validated against `tests/octopus-backend.graphql` (introspected from `api.backend.octopus.energy`).
