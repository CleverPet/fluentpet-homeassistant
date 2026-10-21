"""Tests for the FluentPet API models."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from custom_components.fluentpet.fluentpet_api import (
    EVENT_TYPES,
    LIFECYCLE_EVENT_TYPES,
    Base,
    BaseEventData,
    Button,
    ButtonEventData,
    ButtonLinkEventData,
    Event,
    EventPage,
    EventType,
    FluentPetParseError,
    Household,
    InteractionUpdatedData,
    Learner,
    LifecycleEventData,
    PingData,
    PressCreatedData,
    PusherEventData,
    Webhook,
    WebhookWithSecret,
    parse_datetime,
    parse_event,
)
from tests.common import load_json_fixture

HH = "hh_testhouseholdaaaaaaaaaaaaa"
BASE_ID = "base_testbaseoneaaaaaaaaaaaaaaa"
BTN_OUT = "btn_testbuttonoutsideaaaaaaaaa"
LEARNER_ID = "psh_testlearneraaaaaaaaaaaaaaa"
INT_ID = "int_testinteractionaaaaaaaaaaa"


def _event(name: str) -> Event:
    """Parse an event fixture."""
    return parse_event(load_json_fixture(f"events/{name}.json"))


def test_event_types() -> None:
    """There are 13 subscribable types, 11 of them lifecycle types, plus ping."""
    assert len(EVENT_TYPES) == 13
    assert EventType.PING not in EVENT_TYPES
    assert len(LIFECYCLE_EVENT_TYPES) == 11
    assert EventType.PRESS_CREATED not in LIFECYCLE_EVENT_TYPES
    assert EventType.INTERACTION_UPDATED not in LIFECYCLE_EVENT_TYPES
    assert len(EventType) == 14


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-17T11:59:58.412Z", datetime(2026, 10, 17, 11, 59, 58, 412000, UTC)),
        ("2026-10-17T11:59:58Z", datetime(2026, 10, 17, 11, 59, 58, tzinfo=UTC)),
        ("2026-10-17T11:59:58", datetime(2026, 10, 17, 11, 59, 58, tzinfo=UTC)),
        (
            "2026-10-17T13:59:58+02:00",
            datetime(2026, 10, 17, 13, 59, 58, tzinfo=timezone(timedelta(hours=2))),
        ),
    ],
)
def test_parse_datetime(value: str, expected: datetime) -> None:
    """ISO 8601 times parse into aware datetimes."""
    parsed = parse_datetime(value)
    assert parsed == expected
    assert parsed.tzinfo is not None


def test_parse_datetime_invalid() -> None:
    """A non ISO time is a parse error."""
    with pytest.raises(FluentPetParseError):
        parse_datetime("yesterday")


def test_press_created() -> None:
    """press.created has typed base, button, learner, press and interaction."""
    event = _event("press_created")
    assert event.event_type is EventType.PRESS_CREATED
    assert event.occurred_at == datetime(2026, 10, 17, 11, 59, 58, 412000, UTC)
    assert event.created_at == datetime(2026, 10, 17, 11, 59, 58, 530000, UTC)
    data = event.data
    assert isinstance(data, PressCreatedData)
    assert data.version == 1
    assert event.household is not None
    assert event.household.id == HH
    assert event.household.name == "Test Household"
    assert data.base is not None
    assert data.base.id == BASE_ID
    assert data.base.serial == "TESTSERIAL0001"
    assert data.base.fw_version is None
    assert data.button is not None
    assert data.button.id == BTN_OUT
    assert data.button.normalized_word == "outside"
    assert data.button.audio_id is None
    assert data.pusher is not None
    assert data.pusher.name == "Test Learner"
    assert data.pusher.is_teacher is False
    assert data.press.press_order == 0
    assert data.press.source == "press_topic"
    assert data.press.occurred_at == event.occurred_at
    assert data.interaction is not None
    assert data.interaction.id == INT_ID
    assert event.payload["version"] == 1


def test_press_created_without_pusher() -> None:
    """A press without a learner or teacher has pusher None."""
    raw = load_json_fixture("events/press_created.json")
    raw["payload"]["pusher"] = None
    data = parse_event(raw).data
    assert isinstance(data, PressCreatedData)
    assert data.pusher is None


def test_interaction_updated() -> None:
    """interaction.updated carries the presses in order, deleted buttons as None."""
    event = _event("interaction_updated")
    data = event.data
    assert isinstance(data, InteractionUpdatedData)
    assert data.base is not None
    assert data.pusher is not None
    interaction = data.interaction
    assert interaction.id == INT_ID
    assert interaction.press_count == 3
    assert interaction.occurred_at == datetime(2026, 10, 17, 11, 59, 58, 412000, UTC)
    assert [p.normalized_word for p in interaction.presses] == [
        "outside",
        "play",
        None,
    ]
    assert [p.press_order for p in interaction.presses] == [0, 1, 2]
    assert interaction.presses[2].name is None
    assert interaction.presses[2].button_id == "btn_testbuttonwalkaaaaaaaaaaaa"


def test_interaction_updated_tolerant() -> None:
    """A missing base, press_count and malformed presses are tolerated."""
    raw = load_json_fixture("events/interaction_updated.json")
    raw["payload"]["base"] = None
    del raw["payload"]["interaction"]["press_count"]
    raw["payload"]["interaction"]["presses"].append("garbage")
    data = parse_event(raw).data
    assert isinstance(data, InteractionUpdatedData)
    assert data.base is None
    assert data.interaction.press_count == 3


@pytest.mark.parametrize(
    ("name", "reason"),
    [("base_added", "registered"), ("base_removed", "moved_out")],
)
def test_base_added_removed(name: str, reason: str) -> None:
    """base.added and base.removed carry the Base with its firmware version."""
    event = _event(name)
    data = event.data
    assert isinstance(data, BaseEventData)
    assert isinstance(data, LifecycleEventData)
    assert event.event_type in LIFECYCLE_EVENT_TYPES
    assert data.reason == reason
    assert data.base.fw_version == "2.4.20260901"
    assert data.changes == {}


def test_base_added_default_learner() -> None:
    """A Base without a default learner has default_learner None."""
    data = _event("base_added").data
    assert isinstance(data, BaseEventData)
    assert data.default_learner is None
    removed = _event("base_removed").data
    assert isinstance(removed, BaseEventData)
    assert removed.default_learner is not None
    assert removed.default_learner.id == LEARNER_ID


def test_base_updated() -> None:
    """base.updated carries changes as (old, new) pairs."""
    data = _event("base_updated").data
    assert isinstance(data, BaseEventData)
    assert data.reason == "updated"
    assert data.changes == {
        "name": ("Base 1", "Test Base"),
        "default_learner": (None, LEARNER_ID),
        "group_interactions_within_seconds": (10, 15),
        "battery_level": (100, 75),
        "power_mode": ("plugged_in", "battery"),
    }


def test_button_added() -> None:
    """button.added carries the button's lifecycle fields."""
    data = _event("button_added").data
    assert isinstance(data, ButtonEventData)
    assert data.reason == "created"
    assert data.button.audio_id == "aud_testaudiooutsideaaaaaaaaaa"
    assert data.button.origin == "connect"
    assert data.button.type == "connect"
    assert data.merged_into is None


def test_button_removed_merged() -> None:
    """button.removed of a merge names the button it was merged into."""
    data = _event("button_removed").data
    assert isinstance(data, ButtonEventData)
    assert data.reason == "merged"
    assert data.button.audio_id is None
    assert data.merged_into is not None
    assert data.merged_into.id == BTN_OUT


def test_button_updated() -> None:
    """button.updated carries changes."""
    data = _event("button_updated").data
    assert isinstance(data, ButtonEventData)
    assert data.changes["normalized_word"] == ("out", "outside")
    assert data.changes["audio_id"] == (None, "aud_testaudiooutsideaaaaaaaaaa")
    assert data.changes["battery_level"] == (75, 50)


def test_button_linked() -> None:
    """button.linked carries the button, its serial and the Base."""
    data = _event("button_linked").data
    assert isinstance(data, ButtonLinkEventData)
    assert data.reason == "paired"
    assert data.serial == "TESTBTNSER01"
    assert data.button is not None
    assert data.base is not None
    assert data.base.id == BASE_ID


def test_button_unlinked() -> None:
    """button.unlinked may have no Base."""
    data = _event("button_unlinked").data
    assert isinstance(data, ButtonLinkEventData)
    assert data.reason == "base_removed"
    assert data.base is None
    assert data.button is not None
    assert data.button.type == "classic"


@pytest.mark.parametrize(
    ("name", "reason", "is_teacher"),
    [
        ("pusher_added", "created", False),
        ("pusher_removed", "hidden", True),
        ("pusher_updated", "updated", False),
    ],
)
def test_pusher_events(name: str, reason: str, is_teacher: bool) -> None:
    """pusher.* events carry the learner or teacher."""
    data = _event(name).data
    assert isinstance(data, PusherEventData)
    assert data.reason == reason
    assert data.pusher.is_teacher is is_teacher


def test_pusher_updated_changes() -> None:
    """pusher.updated carries changes; malformed change entries are dropped."""
    raw = load_json_fixture("events/pusher_updated.json")
    raw["payload"]["changes"]["broken"] = "not a pair"
    data = parse_event(raw).data
    assert isinstance(data, PusherEventData)
    assert data.changes == {
        "name": ("Learner", "Test Learner"),
        "type": ("teacher", "learner"),
    }


def test_changes_not_an_object() -> None:
    """Changes of the wrong type read as none."""
    raw = load_json_fixture("events/base_updated.json")
    raw["payload"]["changes"] = ["name"]
    data = parse_event(raw).data
    assert isinstance(data, BaseEventData)
    assert data.changes == {}


def test_ping() -> None:
    """A ping names its webhook and has no household."""
    event = _event("ping")
    assert event.event_type is EventType.PING
    assert isinstance(event.data, PingData)
    assert event.data.webhook_id == "00000000-0000-4000-8000-000000009001"
    assert event.household is None


def test_ping_without_webhook() -> None:
    """A ping without a webhook object is still a ping."""
    raw = load_json_fixture("events/ping.json")
    raw["payload"] = {"version": 1}
    data = parse_event(raw).data
    assert isinstance(data, PingData)
    assert data.webhook_id is None


def test_unknown_event_type() -> None:
    """An unknown type keeps its raw payload and has no typed data."""
    event = parse_event(
        {
            "id": "00000000-0000-4000-8000-000000000099",
            "type": "learner.celebrated",
            "occurred_at": "2026-10-17T12:00:00.000Z",
            "created_at": "2026-10-17T12:00:00.000Z",
            "payload": {"version": 1, "household": {"id": HH}},
            "extra": "ignored",
        }
    )
    assert event.type == "learner.celebrated"
    assert event.event_type is None
    assert event.data is None
    assert event.household is None
    assert event.payload["household"]["id"] == HH


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("press_created", "press"),
        ("press_created", "household"),
        ("interaction_updated", "interaction"),
        ("base_added", "base"),
        ("button_added", "button"),
        ("pusher_added", "pusher"),
    ],
)
def test_malformed_known_payload(name: str, key: str) -> None:
    """A known type whose payload misses a required object has no typed data."""
    raw = load_json_fixture(f"events/{name}.json")
    del raw["payload"][key]
    event = parse_event(raw)
    assert event.data is None
    assert event.event_type is not None


def test_unknown_payload_keys_and_newer_version() -> None:
    """Unknown keys are ignored and the version is kept."""
    raw = load_json_fixture("events/press_created.json")
    raw["payload"]["version"] = 2
    raw["payload"]["future"] = {"x": 1}
    raw["payload"]["button"]["future"] = True
    data = parse_event(raw).data
    assert isinstance(data, PressCreatedData)
    assert data.version == 2


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw.pop("id"),
        lambda raw: raw.pop("type"),
        lambda raw: raw.update(occurred_at="later"),
        lambda raw: raw.pop("created_at"),
        lambda raw: raw.update(payload=[]),
    ],
)
def test_malformed_envelope(mutate) -> None:
    """A malformed envelope is a parse error."""
    raw = load_json_fixture("events/press_created.json")
    mutate(raw)
    with pytest.raises(FluentPetParseError):
        parse_event(raw)


def test_parse_event_not_object() -> None:
    """A non-object event is a parse error."""
    with pytest.raises(FluentPetParseError):
        parse_event([])  # type: ignore[arg-type]


def test_event_page() -> None:
    """A page parses every event."""
    page = EventPage.from_dict(load_json_fixture("events_page.json"))
    assert len(page.events) == 2
    with pytest.raises(FluentPetParseError):
        EventPage.from_dict({"data": [1], "next_cursor": None})
    with pytest.raises(FluentPetParseError):
        EventPage.from_dict({"next_cursor": None})


def test_household_requires_admin_flag() -> None:
    """A household without the admin flag is a parse error."""
    assert Household.from_dict({"id": HH, "name": "Test Household", "admin": True})
    with pytest.raises(FluentPetParseError):
        Household.from_dict({"id": HH, "name": "Test Household"})


def test_resource_tolerance() -> None:
    """Optional resource keys may be missing or of an unexpected type."""
    base = Base.from_dict(
        {"id": BASE_ID, "serial": "TESTSERIAL0001", "connected": "yes", "future": 1}
    )
    assert base.name is None
    assert base.connected is None
    assert base.last_online_at is None
    assert base.power_mode is None
    assert base.battery_level is None
    assert base.battery_updated_at is None
    base = Base.from_dict(
        {"id": BASE_ID, "serial": "TESTSERIAL0001", "last_online_at": "garbage"}
    )
    assert base.last_online_at is None
    button = Button.from_dict({"id": BTN_OUT, "name": "Outside"})
    assert button.normalized_word is None
    assert button.audio_id is None
    assert button.battery_level is None
    assert button.battery_updated_at is None
    with pytest.raises(FluentPetParseError):
        Learner.from_dict({"id": LEARNER_ID, "name": "Test Learner"})


def test_webhook_tolerance() -> None:
    """A webhook tolerates odd optional values and requires its timestamps."""
    raw = load_json_fixture("webhook.json")
    raw["event_types"] = ["press.created", 5]
    raw["failure_count"] = True
    raw["disabled_at"] = None
    webhook = Webhook.from_dict(raw)
    assert webhook.event_types == ("press.created",)
    assert webhook.failure_count == 0
    raw["event_types"] = None
    assert Webhook.from_dict(raw).event_types == ()
    del raw["created_at"]
    with pytest.raises(FluentPetParseError):
        Webhook.from_dict(raw)


def test_webhook_with_secret_requires_secret() -> None:
    """A create response without a secret is a parse error."""
    raw = load_json_fixture("webhook_created.json")
    assert WebhookWithSecret.from_dict(raw).secret.startswith("whsec_")
    del raw["secret"]
    with pytest.raises(FluentPetParseError):
        WebhookWithSecret.from_dict(raw)


def test_models_are_frozen() -> None:
    """Models cannot be changed."""
    household = Household(id=HH, name="Test Household", admin=True)
    with pytest.raises(AttributeError):
        household.name = "Other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("power_mode", "expected_mode"),
    [
        ("plugged_in", "plugged_in"),
        ("battery", "battery"),
        ("solar", "solar"),
        (None, None),
        (3, None),
    ],
)
def test_base_power_mode(power_mode: object, expected_mode: str | None) -> None:
    """The power mode is kept as sent; a non-string reads as None."""
    base = Base.from_dict(
        {"id": BASE_ID, "serial": "TESTSERIAL0001", "power_mode": power_mode}
    )
    assert base.power_mode == expected_mode


@pytest.mark.parametrize(
    ("battery_level", "expected_level"),
    [
        (62, 62),
        (0, 0),
        (100, 100),
        (None, None),
        (101, None),
        (-1, None),
        (62.5, None),
        ("62", None),
        (True, None),
    ],
)
def test_base_battery_level(battery_level: object, expected_level: int | None) -> None:
    """The battery level is a whole percentage; anything else reads as None."""
    base = Base.from_dict(
        {"id": BASE_ID, "serial": "TESTSERIAL0001", "battery_level": battery_level}
    )
    assert base.battery_level == expected_level


@pytest.mark.parametrize(
    ("battery_level", "expected_level"),
    [(75, 75), (0, 0), (None, None), (150, None), ("75", None)],
)
def test_button_battery_level(
    battery_level: object, expected_level: int | None
) -> None:
    """A button's battery level is read like a Base's."""
    button = Button.from_dict(
        {"id": BTN_OUT, "name": "Outside", "battery_level": battery_level}
    )
    assert button.battery_level == expected_level


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-17T11:30:00.000Z", datetime(2026, 10, 17, 11, 30, tzinfo=UTC)),
        (None, None),
        ("garbage", None),
    ],
)
def test_battery_updated_at(raw: object, expected: datetime | None) -> None:
    """The time of the battery reading is optional on Bases and buttons."""
    base = Base.from_dict(
        {"id": BASE_ID, "serial": "TESTSERIAL0001", "battery_updated_at": raw}
    )
    assert base.battery_updated_at == expected
    button = Button.from_dict(
        {"id": BTN_OUT, "name": "Outside", "battery_updated_at": raw}
    )
    assert button.battery_updated_at == expected
