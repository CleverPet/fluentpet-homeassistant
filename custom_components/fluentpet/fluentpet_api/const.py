"""Constants of the FluentPet API."""

from enum import StrEnum
from typing import Final

__version__: Final = "0.1.0"

DEFAULT_BASE_URL: Final = "https://app.fluent.pet"
"""FluentPet API host."""

DEFAULT_REQUEST_TIMEOUT: Final = 30.0
"""Total time in seconds one API request may take."""

USER_AGENT: Final = f"fluentpet-api/{__version__}"

HEADER_EVENT: Final = "X-FluentPet-Event"
HEADER_DELIVERY: Final = "X-FluentPet-Delivery"
HEADER_TIMESTAMP: Final = "X-FluentPet-Timestamp"
HEADER_SIGNATURE: Final = "X-FluentPet-Signature"

SIGNATURE_PREFIX: Final = "sha256="
SIGNATURE_TOLERANCE: Final = 300
"""Seconds a webhook timestamp may differ from the local clock."""

SECRET_PREFIX: Final = "whsec_"

EVENTS_MAX_LIMIT: Final = 500
"""Largest page size the event feed accepts."""


class EventType(StrEnum):
    """Type of a household event."""

    PRESS_CREATED = "press.created"
    INTERACTION_UPDATED = "interaction.updated"
    BASE_ADDED = "base.added"
    BASE_REMOVED = "base.removed"
    BASE_UPDATED = "base.updated"
    BUTTON_ADDED = "button.added"
    BUTTON_REMOVED = "button.removed"
    BUTTON_UPDATED = "button.updated"
    BUTTON_LINKED = "button.linked"
    BUTTON_UNLINKED = "button.unlinked"
    PUSHER_ADDED = "pusher.added"
    PUSHER_REMOVED = "pusher.removed"
    PUSHER_UPDATED = "pusher.updated"
    PING = "ping"


BASE_EVENT_TYPES: Final = frozenset(
    {EventType.BASE_ADDED, EventType.BASE_REMOVED, EventType.BASE_UPDATED}
)
BUTTON_EVENT_TYPES: Final = frozenset(
    {EventType.BUTTON_ADDED, EventType.BUTTON_REMOVED, EventType.BUTTON_UPDATED}
)
BUTTON_LINK_EVENT_TYPES: Final = frozenset(
    {EventType.BUTTON_LINKED, EventType.BUTTON_UNLINKED}
)
PUSHER_EVENT_TYPES: Final = frozenset(
    {EventType.PUSHER_ADDED, EventType.PUSHER_REMOVED, EventType.PUSHER_UPDATED}
)

LIFECYCLE_EVENT_TYPES: Final = (
    BASE_EVENT_TYPES | BUTTON_EVENT_TYPES | BUTTON_LINK_EVENT_TYPES | PUSHER_EVENT_TYPES
)
"""The 11 base, button and pusher event types."""

EVENT_TYPES: Final = tuple(t for t in EventType if t is not EventType.PING)
"""The 13 types a webhook can subscribe to and the feed returns."""
