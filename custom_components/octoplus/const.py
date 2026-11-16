"""Constants for the Octoplus integration."""

DOMAIN = "octoplus"

CONF_ACCOUNT = "account_number"
CONF_AUTO_SCRATCH = "auto_scratch"
CONF_AUTO_CLAIM = "auto_claim"
CONF_AUTO_SPIN = "auto_spin"
CONF_AUTO_BOOK_HH = "auto_book_happy_hour"
CONF_HH_WINDOW_START = "happy_hour_window_start"
CONF_HH_WINDOW_END = "happy_hour_window_end"
CONF_SCAN_MINUTES = "scan_interval_minutes"
CONF_NOTIFY = "notify_service"

DEFAULT_AUTO_SCRATCH = False
DEFAULT_AUTO_CLAIM = False
DEFAULT_AUTO_SPIN = False
DEFAULT_AUTO_BOOK_HH = False
DEFAULT_HH_WINDOW_START = "00:00:00"
DEFAULT_HH_WINDOW_END = "23:59:59"
HH_FAST_POLL_MINUTES = 10
DEFAULT_SCAN_MINUTES = 60

PUBLIC_API = "https://api.octopus.energy/v1/graphql/"
BACKEND_API = "https://api.backend.octopus.energy/v1/graphql/"

# Events fired on the HA bus
EVENT_SCRATCHCARD = f"{DOMAIN}_scratchcard"
EVENT_PRIZE_CLAIMED = f"{DOMAIN}_prize_claimed"
EVENT_NEW_REWARD = f"{DOMAIN}_new_reward"
EVENT_NEW_OFFER = f"{DOMAIN}_new_offer"
EVENT_STAMPCARD_COMPLETE = f"{DOMAIN}_stampcard_complete"
EVENT_OFFER_CLAIMED = f"{DOMAIN}_offer_claimed"
EVENT_WHEEL_SPUN = f"{DOMAIN}_wheel_spun"
EVENT_HH_SLOTS = f"{DOMAIN}_happy_hour_slots"
EVENT_HH_BOOKED = f"{DOMAIN}_happy_hour_booked"
SERVICE_BOOK_HH = "book_happy_hour"
SERVICE_CANCEL_HH = "cancel_happy_hour"
ATTR_EVENT_CODE = "event_code"

SERVICE_CLAIM_OFFER = "claim_offer"
ATTR_OFFER_SLUG = "offer_slug"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

SCRATCH_STATES = [
    "no_session",
    "not_scratched",
    "did_not_win",
    "prize_not_yet_claimed",
    "prize_claimed",
    "prize_rejected",
    "prize_not_claimed_in_time",
]
