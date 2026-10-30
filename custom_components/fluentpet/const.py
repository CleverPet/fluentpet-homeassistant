"""Constants for the FluentPet Connect integration."""

from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Final

from homeassistant.util.signal_type import SignalType

if TYPE_CHECKING:
    from .fluentpet_api import InteractionUpdatedData, PressCreatedData

DOMAIN: Final = "fluentpet"
LOGGER = logging.getLogger(__package__)

MANUFACTURER: Final = "FluentPet"
MODEL_BUTTON: Final = "Connect Button"
MY_FLUENTPET_URL: Final = "https://my.fluent.pet"

OAUTH2_AUTHORIZE: Final = "https://auth.fluent.pet/authorize"
OAUTH2_TOKEN: Final = "https://auth.fluent.pet/oauth/token"
AUDIENCE: Final = "https://app.fluent.pet"
SCOPES: Final = ["openid", "email", "offline_access"]

# Public client id of the FluentPet Auth0 Native application for Home Assistant
# (PKCE, no secret). It is imported as an application credential at setup.
CLIENT_ID: Final = "jXmTI8Nrw2PHa8yQ4a9CQujHvCxvg0uL"
CLIENT_NAME: Final = "FluentPet"

# Options
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_PREFER_CLOUDHOOK: Final = "prefer_cloudhook"
# Advanced option: the FluentPet API host, for developers. Absent means the
# library's DEFAULT_BASE_URL.
CONF_API_BASE_URL: Final = "api_base_url"
# The refresh interval applies while push is unavailable; see SAFETY_POLL_INTERVAL.
DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 30
MAX_SCAN_INTERVAL: Final = 3600
DEFAULT_PREFER_CLOUDHOOK: Final = True
MAX_BACKOFF: Final = timedelta(minutes=10)

# Entry data
CONF_HOUSEHOLD_ID: Final = "household_id"
CONF_WEBHOOK_ID: Final = "webhook_id"
CONF_WEBHOOK_ENDPOINT_ID: Final = "webhook_endpoint_id"
CONF_WEBHOOK_SECRET: Final = "webhook_secret"
CONF_WEBHOOK_URL: Final = "webhook_url"
CONF_CLOUDHOOK: Final = "cloudhook"

# Event feed
FEED_PAGE_LIMIT: Final = 100
FEED_MAX_PAGES: Final = 50
STORAGE_VERSION: Final = 1
STORE_SAVE_DELAY: Final = 5
# While the webhook is healthy, the event feed (to catch up events the webhook
# missed) and the Bases (for their last seen time) are read this often.
SAFETY_POLL_INTERVAL: Final = timedelta(minutes=5)
# While the webhook is healthy, the buttons and learners are also read at a
# safety poll this long after their last read: FluentPet can change a button's
# firmware version (a release table update) without an event.
RESOURCE_POLL_INTERVAL: Final = timedelta(hours=1)

DEDUPE_SIZE: Final = 512
# Presses kept per button while its event entity is not ready, also across a
# restart; an older one is dropped.
PENDING_PRESS_LIMIT: Final = 20
# Updates an unavailable entity holds; an older one is dropped and its event id
# forgotten, so the feed, whose position stays before held updates, delivers it
# again.
HELD_UPDATE_LIMIT: Final = 100
STATE_MAX_LENGTH: Final = 255

# A webhook delivery of an event FluentPet stored longer ago than this is a late
# retry; the feed, read every refresh, has processed the event already.
WEBHOOK_STALE_EVENT_AGE: Final = timedelta(minutes=10)

# Slow retry of what will not work on the next refresh: no usable webhook URL, a
# webhook FluentPet refused, or a feed only the household admin may read.
SLOW_RETRY_MIN: Final = timedelta(minutes=5)
SLOW_RETRY_MAX: Final = timedelta(hours=1)
# An active webhook is verified every SLOW_RETRY_MIN, at the refresh that falls
# due; a refresh up to this much early (timer rounding) still verifies.
VERIFY_SLACK: Final = timedelta(seconds=30)
# A verified webhook delivery this recent keeps the webhook healthy while
# FluentPet still counts failed deliveries.
WEBHOOK_DELIVERY_WINDOW: Final = timedelta(minutes=10)

# The power modes of a Base, as FluentPet sends them in Base.power_mode.
POWER_MODE_BATTERY: Final = "battery"
POWER_MODE_PLUGGED_IN: Final = "plugged_in"

EVENT_PRESS: Final = "press"
ISSUE_WEBHOOK_DISABLED: Final = "webhook_disabled"
ISSUE_WEBHOOK_LIMIT: Final = "webhook_limit"
ISSUE_NOT_ADMIN: Final = "not_admin"


def signal_interaction(entry_id: str) -> SignalType[str, InteractionUpdatedData]:
    """Return the dispatcher signal for interaction updates of an entry.

    An update carries its event id and the payload.
    """
    return SignalType(f"{DOMAIN}_{entry_id}_interaction")


def press_unique_id(button_id: str) -> str:
    """Return the unique id of the press event entity of a button."""
    return f"{button_id}_press"


def signal_press(
    entry_id: str, button_id: str
) -> SignalType[str, datetime, PressCreatedData, bool]:
    """Return the dispatcher signal for presses of one button.

    A press carries its event id, the time of the event, the payload and
    whether it is replayed.
    """
    return SignalType(f"{DOMAIN}_{entry_id}_press_{button_id}")
