"""Constants for the Octoplus integration."""

DOMAIN = "octoplus"

CONF_ACCOUNT = "account_number"
CONF_AUTO_SCRATCH = "auto_scratch"
CONF_AUTO_CLAIM = "auto_claim"
CONF_SCAN_MINUTES = "scan_interval_minutes"
CONF_NOTIFY = "notify_service"

DEFAULT_AUTO_SCRATCH = False
DEFAULT_AUTO_CLAIM = False
DEFAULT_SCAN_MINUTES = 60

PUBLIC_API = "https://api.octopus.energy/v1/graphql/"
BACKEND_API = "https://api.backend.octopus.energy/v1/graphql/"

# Events fired on the HA bus
EVENT_SCRATCHCARD = f"{DOMAIN}_scratchcard"
EVENT_PRIZE_CLAIMED = f"{DOMAIN}_prize_claimed"
EVENT_NEW_REWARD = f"{DOMAIN}_new_reward"
EVENT_NEW_OFFER = f"{DOMAIN}_new_offer"
EVENT_STAMPCARD_COMPLETE = f"{DOMAIN}_stampcard_complete"

SCRATCH_STATES = [
    "no_session",
    "not_scratched",
    "did_not_win",
    "prize_not_yet_claimed",
    "prize_claimed",
    "prize_rejected",
    "prize_not_claimed_in_time",
]
