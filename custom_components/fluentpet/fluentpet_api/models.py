"""Data models of the FluentPet API.

Every model is a frozen dataclass built with `from_dict` from the JSON the API returns. Keys the
model does not know are ignored, optional keys may be missing, and ids are kept as opaque strings.
A required key that is missing or of the wrong type raises FluentPetParseError.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Self, override

from .const import (
    BASE_EVENT_TYPES,
    BUTTON_EVENT_TYPES,
    BUTTON_LINK_EVENT_TYPES,
    EventType,
)
from .exceptions import FluentPetParseError

type JsonObject = Mapping[str, Any]


def _object(value: Any, what: str) -> JsonObject:
    """Return value as a JSON object or raise."""
    if not isinstance(value, Mapping):
        raise FluentPetParseError(f"{what} is not a JSON object")
    return value


def _str(data: JsonObject, key: str) -> str:
    """Return a required string."""
    value = data.get(key)
    if not isinstance(value, str):
        raise FluentPetParseError(f"{key!r} is missing or not a string")
    return value


def _opt_str(data: JsonObject, key: str) -> str | None:
    """Return an optional string; any other type reads as None."""
    value = data.get(key)
    return value if isinstance(value, str) else None


def _bool(data: JsonObject, key: str) -> bool:
    """Return a required boolean."""
    value = data.get(key)
    if not isinstance(value, bool):
        raise FluentPetParseError(f"{key!r} is missing or not a boolean")
    return value


def _opt_bool(data: JsonObject, key: str) -> bool | None:
    """Return an optional boolean; any other type reads as None."""
    value = data.get(key)
    return value if isinstance(value, bool) else None


def _opt_int(data: JsonObject, key: str) -> int | None:
    """Return an optional integer; any other type reads as None."""
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _percent(data: JsonObject, key: str) -> int | None:
    """Return an optional percentage, 0 to 100; anything else reads as None."""
    value = _opt_int(data, key)
    return value if value is not None and 0 <= value <= 100 else None


def parse_datetime(value: str) -> datetime:
    """Parse an ISO 8601 time such as 2026-10-17T11:59:58.412Z into an aware datetime.

    A time without an offset is taken as UTC.

    Raises:
        FluentPetParseError: The value is not an ISO 8601 time.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as err:
        raise FluentPetParseError(f"{value!r} is not an ISO 8601 time") from err
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _datetime(data: JsonObject, key: str) -> datetime:
    """Return a required time."""
    return parse_datetime(_str(data, key))


def _opt_datetime(data: JsonObject, key: str) -> datetime | None:
    """Return an optional time; a missing, null or unparsable value reads as None."""
    value = data.get(key)
    if not isinstance(value, str):
        return None
    try:
        return parse_datetime(value)
    except FluentPetParseError:
        return None


def _list(data: JsonObject, key: str) -> list[Any]:
    """Return a required list."""
    value = data.get(key)
    if not isinstance(value, list):
        raise FluentPetParseError(f"{key!r} is missing or not a list")
    return value


def _str_tuple(data: JsonObject, key: str) -> tuple[str, ...]:
    """Return the strings of an optional list."""
    value = data.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _ref[R](factory: Callable[[JsonObject], R], data: JsonObject, key: str) -> R | None:
    """Return factory(nested object), or None when the object is absent or has no id."""
    value = data.get(key)
    if not isinstance(value, Mapping) or not isinstance(value.get("id"), str):
        return None
    return factory(value)


def _changes(data: JsonObject) -> dict[str, tuple[Any, Any]]:
    """Return the `changes` of an update event as {field: (old, new)}."""
    value = data.get("changes")
    if not isinstance(value, Mapping):
        return {}
    return {
        key: (pair[0], pair[1])
        for key, pair in value.items()
        if isinstance(key, str) and isinstance(pair, list) and len(pair) == 2
    }


# Resources


@dataclass(frozen=True, slots=True)
class Household:
    """A household the user belongs to.

    Attributes:
        id: Opaque household id (hh_...).
        name: Household name.
        admin: Whether the user is the household admin. Only the admin can manage webhooks
            and read events.
    """

    id: str
    name: str
    admin: bool

    @classmethod
    def from_dict(cls, data: JsonObject) -> Self:
        """Build from the API JSON."""
        return cls(
            id=_str(data, "id"), name=_str(data, "name"), admin=_bool(data, "admin")
        )


@dataclass(frozen=True, slots=True)
class Base:
    """A FluentPet Connect Base of a household.

    Attributes:
        connected: The online flag FluentPet derives from the Base's sessions, or None
            when unknown. It flips with every session of a Base on battery.
        last_online_at: When FluentPet last heard from the Base, or None.
        power_mode: "plugged_in" or "battery" as sent (another string is kept as
            is), or None when unknown.
        battery_level: Battery charge in percent, snapped to 100, 75, 50, 25 or 0 (the
            steps of the FluentPet app), or None when unknown or not a whole number
            from 0 to 100.
        battery_updated_at: When FluentPet received the latest battery reading, or None.
    """

    id: str
    serial: str
    name: str | None
    fw_version: str | None
    model: str | None
    connected: bool | None
    last_online_at: datetime | None
    power_mode: str | None = None
    battery_level: int | None = None
    battery_updated_at: datetime | None = None

    @classmethod
    def from_dict(cls, data: JsonObject) -> Self:
        """Build from the API JSON."""
        return cls(
            id=_str(data, "id"),
            serial=_str(data, "serial"),
            name=_opt_str(data, "name"),
            fw_version=_opt_str(data, "fw_version"),
            model=_opt_str(data, "model"),
            connected=_opt_bool(data, "connected"),
            last_online_at=_opt_datetime(data, "last_online_at"),
            power_mode=_opt_str(data, "power_mode"),
            battery_level=_percent(data, "battery_level"),
            battery_updated_at=_opt_datetime(data, "battery_updated_at"),
        )


@dataclass(frozen=True, slots=True)
class Button:
    """A button of a household.

    Attributes:
        base_id: The Base the button is linked to, or None for an unlinked button.
        serial: The button's serial on its Base, or None.
        audio_id: The button's sound (aud_...), or None.
        battery_level: Battery charge in percent as reported through its Base, snapped
            like Base.battery_level, or None when unknown: an unlinked button, one not
            pressed since FluentPet started reporting it, or one whose battery is empty
            (FluentPet reports an empty button battery as None, not 0).
        battery_updated_at: When FluentPet received the latest battery reading, or None.
    """

    id: str
    name: str
    normalized_word: str | None
    base_id: str | None
    serial: str | None
    audio_id: str | None
    battery_level: int | None = None
    battery_updated_at: datetime | None = None

    @classmethod
    def from_dict(cls, data: JsonObject) -> Self:
        """Build from the API JSON."""
        return cls(
            id=_str(data, "id"),
            name=_str(data, "name"),
            normalized_word=_opt_str(data, "normalized_word"),
            base_id=_opt_str(data, "base_id"),
            serial=_opt_str(data, "serial"),
            audio_id=_opt_str(data, "audio_id"),
            battery_level=_percent(data, "battery_level"),
            battery_updated_at=_opt_datetime(data, "battery_updated_at"),
        )


@dataclass(frozen=True, slots=True)
class Learner:
    """A learner or teacher of a household (a "pusher" in the API).

    Attributes:
        type: "learner" or "teacher".
    """

    id: str
    name: str
    type: str

    @property
    def is_teacher(self) -> bool:
        """Whether this is a teacher rather than a learner."""
        return self.type == "teacher"

    @classmethod
    def from_dict(cls, data: JsonObject) -> Self:
        """Build from the API JSON."""
        return cls(
            id=_str(data, "id"), name=_str(data, "name"), type=_str(data, "type")
        )


@dataclass(frozen=True, slots=True)
class Webhook:
    """A webhook endpoint of a household.

    Attributes:
        failure_count: Consecutive failed delivery attempts.
        disabled_at: When FluentPet disabled the webhook after repeated failures, or None.
    """

    id: str
    url: str
    event_types: tuple[str, ...]
    enabled: bool
    failure_count: int
    disabled_at: datetime | None
    last_delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def auto_disabled(self) -> bool:
        """Whether FluentPet disabled the webhook after repeated failures."""
        return not self.enabled and self.disabled_at is not None

    @classmethod
    def _fields(cls, data: JsonObject) -> dict[str, Any]:
        """Return the constructor arguments shared with WebhookWithSecret."""
        return {
            "id": _str(data, "id"),
            "url": _str(data, "url"),
            "event_types": _str_tuple(data, "event_types"),
            "enabled": _bool(data, "enabled"),
            "failure_count": _opt_int(data, "failure_count") or 0,
            "disabled_at": _opt_datetime(data, "disabled_at"),
            "last_delivered_at": _opt_datetime(data, "last_delivered_at"),
            "created_at": _datetime(data, "created_at"),
            "updated_at": _datetime(data, "updated_at"),
        }

    @classmethod
    def from_dict(cls, data: JsonObject) -> Self:
        """Build from the API JSON."""
        return cls(**cls._fields(data))


@dataclass(frozen=True, slots=True)
class WebhookWithSecret(Webhook):
    """A webhook as returned by create, with its signing secret.

    The secret is returned only once and is left out of repr.
    """

    secret: str = field(repr=False, kw_only=True)

    @classmethod
    @override
    def from_dict(cls, data: JsonObject) -> Self:
        """Build from the API JSON."""
        return cls(**cls._fields(data), secret=_str(data, "secret"))


@dataclass(frozen=True, slots=True)
class WebhookDelivery:
    """One delivery of an event or ping to a webhook.

    Attributes:
        delivery_uuid: Sent as X-FluentPet-Delivery; for a ping also the body's event id.
        household_event_id: The delivered event's id, or None for a ping.
        status: pending, succeeded, failed or skipped.
    """

    delivery_uuid: str
    event_type: str
    household_event_id: str | None
    status: str
    attempts: int
    response_status: int | None
    response_time_ms: int | None
    response_body: str | None
    error: str | None
    last_attempted_at: datetime | None
    next_attempt_at: datetime | None
    created_at: datetime

    @classmethod
    def from_dict(cls, data: JsonObject) -> Self:
        """Build from the API JSON."""
        return cls(
            delivery_uuid=_str(data, "delivery_uuid"),
            event_type=_str(data, "event_type"),
            household_event_id=_opt_str(data, "household_event_id"),
            status=_str(data, "status"),
            attempts=_opt_int(data, "attempts") or 0,
            response_status=_opt_int(data, "response_status"),
            response_time_ms=_opt_int(data, "response_time_ms"),
            response_body=_opt_str(data, "response_body"),
            error=_opt_str(data, "error"),
            last_attempted_at=_opt_datetime(data, "last_attempted_at"),
            next_attempt_at=_opt_datetime(data, "next_attempt_at"),
            created_at=_datetime(data, "created_at"),
        )


# References inside event payloads


@dataclass(frozen=True, slots=True)
class HouseholdRef:
    """The household an event belongs to."""

    id: str
    name: str | None

    @classmethod
    def from_dict(cls, data: JsonObject) -> Self:
        """Build from the payload JSON."""
        return cls(id=_str(data, "id"), name=_opt_str(data, "name"))


@dataclass(frozen=True, slots=True)
class BaseRef:
    """A Base as an event payload names it.

    Attributes:
        fw_version: Present on base.* events only.
    """

    id: str
    serial: str | None
    name: str | None
    fw_version: str | None = None

    @classmethod
    def from_dict(cls, data: JsonObject) -> Self:
        """Build from the payload JSON."""
        return cls(
            id=_str(data, "id"),
            serial=_opt_str(data, "serial"),
            name=_opt_str(data, "name"),
            fw_version=_opt_str(data, "fw_version"),
        )


@dataclass(frozen=True, slots=True)
class ButtonRef:
    """A button as an event payload names it.

    Attributes:
        audio_id: Lifecycle events only: the button's sound, or None.
        origin: Lifecycle events only: app, connect or interaction_importer.
        type: Lifecycle events only: connect while linked to a Base, else classic.
    """

    id: str
    name: str | None
    normalized_word: str | None
    audio_id: str | None = None
    origin: str | None = None
    type: str | None = None

    @classmethod
    def from_dict(cls, data: JsonObject) -> Self:
        """Build from the payload JSON."""
        return cls(
            id=_str(data, "id"),
            name=_opt_str(data, "name"),
            normalized_word=_opt_str(data, "normalized_word"),
            audio_id=_opt_str(data, "audio_id"),
            origin=_opt_str(data, "origin"),
            type=_opt_str(data, "type"),
        )


@dataclass(frozen=True, slots=True)
class PusherRef:
    """A learner or teacher as an event payload names it.

    Attributes:
        type: learner or teacher.
    """

    id: str
    name: str | None
    type: str | None

    @property
    def is_teacher(self) -> bool:
        """Whether this is a teacher rather than a learner."""
        return self.type == "teacher"

    @classmethod
    def from_dict(cls, data: JsonObject) -> Self:
        """Build from the payload JSON."""
        return cls(
            id=_str(data, "id"),
            name=_opt_str(data, "name"),
            type=_opt_str(data, "type"),
        )


@dataclass(frozen=True, slots=True)
class Press:
    """The press of a press.created event.

    Attributes:
        press_order: Position in its interaction when recorded; interaction.updated has the
            final order.
        source: press_topic or device_log.
    """

    occurred_at: datetime
    press_order: int | None
    source: str | None

    @classmethod
    def from_dict(cls, data: JsonObject) -> Self:
        """Build from the payload JSON."""
        return cls(
            occurred_at=_datetime(data, "occurred_at"),
            press_order=_opt_int(data, "press_order"),
            source=_opt_str(data, "source"),
        )


@dataclass(frozen=True, slots=True)
class InteractionRef:
    """The interaction a press belongs to."""

    id: str

    @classmethod
    def from_dict(cls, data: JsonObject) -> Self:
        """Build from the payload JSON."""
        return cls(id=_str(data, "id"))


@dataclass(frozen=True, slots=True)
class InteractionPress:
    """One press of an interaction.updated event.

    Attributes:
        name: None when the button was deleted since.
        normalized_word: None when the button was deleted since.
    """

    button_id: str | None
    name: str | None
    normalized_word: str | None
    occurred_at: datetime | None
    press_order: int | None

    @classmethod
    def from_dict(cls, data: JsonObject) -> Self:
        """Build from the payload JSON."""
        return cls(
            button_id=_opt_str(data, "button_id"),
            name=_opt_str(data, "name"),
            normalized_word=_opt_str(data, "normalized_word"),
            occurred_at=_opt_datetime(data, "occurred_at"),
            press_order=_opt_int(data, "press_order"),
        )


@dataclass(frozen=True, slots=True)
class Interaction:
    """The interaction of an interaction.updated event.

    Attributes:
        occurred_at: Time of its first press.
        presses: Presses in their final order.
    """

    id: str
    occurred_at: datetime | None
    press_count: int
    presses: tuple[InteractionPress, ...]

    @classmethod
    def from_dict(cls, data: JsonObject) -> Self:
        """Build from the payload JSON."""
        presses = tuple(
            InteractionPress.from_dict(item)
            for item in data.get("presses") or ()
            if isinstance(item, Mapping)
        )
        press_count = _opt_int(data, "press_count")
        return cls(
            id=_str(data, "id"),
            occurred_at=_opt_datetime(data, "occurred_at"),
            press_count=len(presses) if press_count is None else press_count,
            presses=presses,
        )


# Typed event payloads


@dataclass(frozen=True, slots=True)
class EventData:
    """Typed payload of an event.

    Attributes:
        version: Payload version (1).
    """

    version: int


@dataclass(frozen=True, slots=True)
class HouseholdEventData(EventData):
    """Typed payload of a household event; every type but ping carries the household."""

    household: HouseholdRef


@dataclass(frozen=True, slots=True)
class PressCreatedData(HouseholdEventData):
    """Payload of press.created.

    Attributes:
        pusher: The interaction's learner or teacher, or None.
    """

    base: BaseRef | None
    button: ButtonRef | None
    pusher: PusherRef | None
    press: Press
    interaction: InteractionRef | None


@dataclass(frozen=True, slots=True)
class InteractionUpdatedData(HouseholdEventData):
    """Payload of interaction.updated.

    Attributes:
        base: None when the interaction's Base is no longer known.
    """

    base: BaseRef | None
    pusher: PusherRef | None
    interaction: Interaction


@dataclass(frozen=True, slots=True)
class LifecycleEventData(HouseholdEventData):
    """Payload fields shared by the 11 lifecycle events.

    Attributes:
        reason: Why the event happened, for example registered or merged.
        changes: For *.updated events, each changed field as (old, new).
    """

    reason: str | None
    changes: dict[str, tuple[Any, Any]]


@dataclass(frozen=True, slots=True)
class BaseEventData(LifecycleEventData):
    """Payload of base.added, base.removed and base.updated."""

    base: BaseRef
    default_learner: PusherRef | None


@dataclass(frozen=True, slots=True)
class ButtonEventData(LifecycleEventData):
    """Payload of button.added, button.removed and button.updated.

    Attributes:
        merged_into: For a button.removed with reason merged, the button it was merged into.
    """

    button: ButtonRef
    merged_into: ButtonRef | None


@dataclass(frozen=True, slots=True)
class ButtonLinkEventData(LifecycleEventData):
    """Payload of button.linked and button.unlinked.

    Attributes:
        button: None when the button no longer exists.
        serial: The button's serial on the Base.
        base: None when the Base no longer exists.
    """

    button: ButtonRef | None
    serial: str | None
    base: BaseRef | None


@dataclass(frozen=True, slots=True)
class PusherEventData(LifecycleEventData):
    """Payload of pusher.added, pusher.removed and pusher.updated."""

    pusher: PusherRef


@dataclass(frozen=True, slots=True)
class PingData(EventData):
    """Payload of a test ping."""

    webhook_id: str | None


def _event_data(event_type: str, payload: JsonObject) -> EventData | None:
    """Return the typed payload of a known event type, or None for an unknown type."""
    version = _opt_int(payload, "version") or 1
    if event_type == EventType.PING:
        webhook = payload.get("webhook")
        webhook_id = _opt_str(webhook, "id") if isinstance(webhook, Mapping) else None
        return PingData(version=version, webhook_id=webhook_id)

    try:
        known = EventType(event_type)
    except ValueError:
        return None

    household = HouseholdRef.from_dict(_object(payload.get("household"), "household"))
    if known is EventType.PRESS_CREATED:
        return PressCreatedData(
            version=version,
            household=household,
            base=_ref(BaseRef.from_dict, payload, "base"),
            button=_ref(ButtonRef.from_dict, payload, "button"),
            pusher=_ref(PusherRef.from_dict, payload, "pusher"),
            press=Press.from_dict(_object(payload.get("press"), "press")),
            interaction=_ref(InteractionRef.from_dict, payload, "interaction"),
        )
    if known is EventType.INTERACTION_UPDATED:
        return InteractionUpdatedData(
            version=version,
            household=household,
            base=_ref(BaseRef.from_dict, payload, "base"),
            pusher=_ref(PusherRef.from_dict, payload, "pusher"),
            interaction=Interaction.from_dict(
                _object(payload.get("interaction"), "interaction")
            ),
        )

    reason = _opt_str(payload, "reason")
    changes = _changes(payload)
    if known in BASE_EVENT_TYPES:
        return BaseEventData(
            version=version,
            household=household,
            reason=reason,
            changes=changes,
            base=BaseRef.from_dict(_object(payload.get("base"), "base")),
            default_learner=_ref(PusherRef.from_dict, payload, "default_learner"),
        )
    if known in BUTTON_EVENT_TYPES:
        return ButtonEventData(
            version=version,
            household=household,
            reason=reason,
            changes=changes,
            button=ButtonRef.from_dict(_object(payload.get("button"), "button")),
            merged_into=_ref(ButtonRef.from_dict, payload, "merged_into"),
        )
    if known in BUTTON_LINK_EVENT_TYPES:
        return ButtonLinkEventData(
            version=version,
            household=household,
            reason=reason,
            changes=changes,
            button=_ref(ButtonRef.from_dict, payload, "button"),
            serial=_opt_str(payload, "serial"),
            base=_ref(BaseRef.from_dict, payload, "base"),
        )
    # The remaining known types are the pusher.* events.
    return PusherEventData(
        version=version,
        household=household,
        reason=reason,
        changes=changes,
        pusher=PusherRef.from_dict(_object(payload.get("pusher"), "pusher")),
    )


@dataclass(frozen=True, slots=True)
class Event:
    """A household event, from the feed or a webhook body.

    Attributes:
        id: Event id (a UUID string); for a ping, the delivery id.
        type: Event type as sent. Unknown types are kept as they are.
        occurred_at: When the fact happened.
        created_at: When FluentPet stored the event.
        payload: The raw payload.
        data: The typed payload, or None for an unknown type or a payload that does not match
            its type's shape (the raw payload is still in `payload`).
    """

    id: str
    type: str
    occurred_at: datetime
    created_at: datetime
    payload: JsonObject = field(repr=False)
    data: EventData | None

    @property
    def event_type(self) -> EventType | None:
        """Return the type as EventType, or None for a type this library does not know."""
        try:
            return EventType(self.type)
        except ValueError:
            return None

    @property
    def household(self) -> HouseholdRef | None:
        """Return the household the event belongs to, when the payload carries it."""
        if isinstance(self.data, HouseholdEventData):
            return self.data.household
        return None

    @classmethod
    def from_dict(cls, data: JsonObject) -> Self:
        """Build from the event JSON.

        Raises:
            FluentPetParseError: The envelope (id, type, times, payload) is malformed.
        """
        event_type = _str(data, "type")
        payload = _object(data.get("payload"), "payload")
        try:
            typed = _event_data(event_type, payload)
        except FluentPetParseError:
            typed = None
        return cls(
            id=_str(data, "id"),
            type=event_type,
            occurred_at=_datetime(data, "occurred_at"),
            created_at=_datetime(data, "created_at"),
            payload=payload,
            data=typed,
        )


@dataclass(frozen=True, slots=True)
class EventPage:
    """A page of the event feed.

    Attributes:
        events: Events oldest first, in the order they were stored.
        next_cursor: Pass as `since` to read on; equals the given `since` when the page is
            empty, or None when no `since` was given and there are no events.
    """

    events: tuple[Event, ...]
    next_cursor: str | None

    @classmethod
    def from_dict(
        cls,
        data: JsonObject,
        *,
        on_invalid: Callable[[FluentPetParseError], None] | None = None,
    ) -> Self:
        """Build from the feed JSON.

        Args:
            data: The feed JSON.
            on_invalid: Skip events with a malformed envelope and pass their error to
                it; without it a malformed event raises FluentPetParseError.
        """
        events: list[Event] = []
        for item in _list(data, "data"):
            try:
                events.append(Event.from_dict(_object(item, "event")))
            except FluentPetParseError as err:
                if on_invalid is None:
                    raise
                on_invalid(err)
        return cls(events=tuple(events), next_cursor=_opt_str(data, "next_cursor"))


def parse_event(data: JsonObject) -> Event:
    """Parse an event JSON object (a feed entry or a webhook body).

    Raises:
        FluentPetParseError: The event envelope is malformed.
    """
    return Event.from_dict(_object(data, "event"))
