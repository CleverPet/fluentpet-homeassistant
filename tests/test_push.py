"""Tests for push first: the safety poll while the webhook is healthy."""

from collections.abc import Callable
import dataclasses
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.fluentpet.const import DOMAIN, SAFETY_POLL_INTERVAL
from custom_components.fluentpet.fluentpet_api import (
    Button,
    Event,
    EventPage,
    FluentPetConnectionError,
    FluentPetParseError,
    FluentPetRequestError,
    FluentPetServerError,
)
from custom_components.fluentpet.webhook import webhook_healthy
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from .common import load_json_fixture, setup_integration
from .conftest import HOUSEHOLD_ID, TAIL_CURSOR, bases, buttons, empty_page, webhook

pytestmark = pytest.mark.usefixtures("external_url")

PRESS_ENTITY = "event.outside_press"
LAST_SEEN_ENTITY = "sensor.test_base_last_seen"
BASE_ID = "base_testbaseoneaaaaaaaaaaaaaaa"
NEW_BUTTON = Button(
    id="btn_testbuttonnewaaaaaaaaaaaaa",
    name="Treat",
    normalized_word="treat",
    base_id=BASE_ID,
    serial="TESTBTNSER09",
    audio_id=None,
)
NEXT_CURSOR = "AAAAAAAAAAtestcursor00000014"


def _event(name: str, **overrides: Any) -> Event:
    """Return an event fixture as a model."""
    return Event.from_dict({**load_json_fixture(f"events/{name}.json"), **overrides})


def _press(event_id: str, button_id: str) -> Event:
    """Return a press.created event for a button."""
    data = load_json_fixture("events/press_created.json")
    data["id"] = event_id
    data["payload"]["button"]["id"] = button_id
    return Event.from_dict(data)


async def _wait(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    """Move time on and run the refresh that is due."""
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Run the next safety poll."""
    await _wait(hass, freezer, SAFETY_POLL_INTERVAL)


def _calls(mock_client: AsyncMock) -> list[str]:
    """Return the names of the client methods called, in order."""
    return [call[0] for call in mock_client.mock_calls]


def _reset_calls(mock_client: AsyncMock) -> None:
    """Forget the recorded calls of the client, keeping its answers."""
    mock_client.reset_mock(return_value=False, side_effect=False)


def _assert_full(mock_client: AsyncMock, count: int = 1) -> None:
    """Assert the Bases, buttons and learners were each read count times."""
    assert mock_client.async_get_bases.await_count == count
    assert mock_client.async_get_buttons.await_count == count
    assert mock_client.async_get_learners.await_count == count


def _assert_safety_poll(mock_client: AsyncMock) -> None:
    """Assert a safety poll: the check when due, the feed, then the Bases."""
    assert [name for name in _calls(mock_client) if name != "async_get_webhook"] == [
        "async_get_events",
        "async_get_bases",
    ]
    mock_client.async_get_buttons.assert_not_awaited()
    mock_client.async_get_learners.assert_not_awaited()


async def _setup_push(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_client: AsyncMock,
    entry: MockConfigEntry,
) -> None:
    """Set up with a healthy webhook and run the one full refresh that follows."""
    await setup_integration(hass, entry)
    coordinator = entry.runtime_data.coordinator
    assert coordinator.push_active
    assert coordinator.update_interval == SAFETY_POLL_INTERVAL
    _reset_calls(mock_client)
    await _poll(hass, freezer)
    _assert_full(mock_client)
    _reset_calls(mock_client)


# The health rule


@pytest.mark.parametrize(
    ("active", "check_failed", "failure_count", "delivery_age", "healthy"),
    [
        (True, False, 0, None, True),
        (True, False, 3, timedelta(minutes=9), True),
        (True, False, 3, timedelta(minutes=10), True),
        (True, False, 3, timedelta(minutes=10, seconds=1), False),
        (True, False, 3, None, False),
        (False, False, 0, timedelta(seconds=1), False),
        (True, True, 0, timedelta(seconds=1), False),
    ],
    ids=[
        "count_0",
        "count_with_recent_delivery",
        "count_with_delivery_at_window",
        "count_with_old_delivery",
        "count_quiet",
        "inactive",
        "check_failed",
    ],
)
def test_webhook_healthy_rule(
    active: bool,
    check_failed: bool,
    failure_count: int,
    delivery_age: timedelta | None,
    healthy: bool,
) -> None:
    """Failed deliveries count only without a recent verified delivery."""
    now = datetime(2026, 10, 18, 12, 0, tzinfo=dt_util.UTC)
    assert (
        webhook_healthy(
            active=active,
            check_failed=check_failed,
            failure_count=failure_count,
            last_delivery=None if delivery_age is None else now - delivery_age,
            now=now,
        )
        is healthy
    )


# The safety poll


async def test_safety_poll_reads_feed_and_bases(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """With a healthy webhook the feed and the Bases are read every 5 minutes.

    The first refresh after the webhook became healthy reads everything once.
    The webhook check runs at every poll, and no ping is ever sent.
    """
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    assert coordinator.push_active
    _reset_calls(mock_client)

    await _wait(hass, freezer, timedelta(seconds=61))
    assert mock_client.mock_calls == []

    await _wait(hass, freezer, SAFETY_POLL_INTERVAL - timedelta(seconds=61))
    _assert_full(mock_client)

    for _ in range(3):
        _reset_calls(mock_client)
        await _poll(hass, freezer)
        assert _calls(mock_client) == [
            "async_get_webhook",
            "async_get_events",
            "async_get_bases",
        ]
        assert coordinator.last_update_success
        assert coordinator.update_interval == SAFETY_POLL_INTERVAL
    mock_client.async_test_webhook.assert_not_awaited()


async def test_safety_poll_updates_last_seen(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The last seen time of a Base follows the Bases read by the safety poll."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    first, second = bases()
    mock_client.async_get_bases.return_value = [
        dataclasses.replace(
            first, last_online_at=datetime(2026, 10, 17, 12, 4, tzinfo=dt_util.UTC)
        ),
        second,
    ]
    await _poll(hass, freezer)
    _assert_safety_poll(mock_client)
    assert hass.states.get(LAST_SEEN_ENTITY).state == "2026-10-17T12:04:00+00:00"


async def test_safety_poll_fires_press(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A press the webhook missed fires from the safety poll."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    mock_client.async_get_events.side_effect = [
        EventPage((_event("press_created"),), NEXT_CURSOR)
    ]
    await _poll(hass, freezer)
    _assert_safety_poll(mock_client)
    assert hass.states.get(PRESS_ENTITY).state != STATE_UNKNOWN


async def test_safety_poll_cursor_reset_reads_everything(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A feed position FluentPet refuses in a safety poll reads everything."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    mock_client.async_get_events.side_effect = [
        FluentPetRequestError(400, "bad request", "Invalid cursor"),
        EventPage((), TAIL_CURSOR),
    ]
    await _poll(hass, freezer)
    mock_client.async_get_tail_cursor.assert_awaited_once_with(HOUSEHOLD_ID)
    _assert_full(mock_client)
    assert mock_config_entry.runtime_data.coordinator.last_update_success

    _reset_calls(mock_client)
    mock_client.async_get_events.side_effect = empty_page
    await _poll(hass, freezer)
    _assert_safety_poll(mock_client)


async def test_failed_safety_poll_after_cursor_reset_next_reads_everything(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A feed restart in a poll that fails later still reads everything next."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    mock_client.async_get_events.side_effect = [
        FluentPetRequestError(400, "bad request", "Invalid cursor"),
        EventPage((), TAIL_CURSOR),
    ]
    mock_client.async_get_bases.side_effect = FluentPetConnectionError("boom")
    await _poll(hass, freezer)
    assert not coordinator.last_update_success

    _reset_calls(mock_client)
    mock_client.async_get_bases.side_effect = None
    mock_client.async_get_events.side_effect = empty_page
    await _poll(hass, freezer)
    assert coordinator.last_update_success
    _assert_full(mock_client)


async def test_failed_full_refresh_next_safety_poll_reads_everything(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """After a failed full refresh in push mode the next poll reads everything."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    mock_client.async_get_learners.side_effect = FluentPetConnectionError("boom")
    await coordinator.async_request_refresh()
    await hass.async_block_till_done()
    assert not coordinator.last_update_success

    _reset_calls(mock_client)
    mock_client.async_get_learners.side_effect = None
    await _poll(hass, freezer)
    assert coordinator.last_update_success
    _assert_full(mock_client)

    _reset_calls(mock_client)
    await _poll(hass, freezer)
    _assert_safety_poll(mock_client)


# Refreshes that read everything in push mode


async def test_update_entity_in_push_mode_reads_everything(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """homeassistant.update_entity reads the Bases, buttons and learners."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    assert await async_setup_component(hass, "homeassistant", {})
    await hass.services.async_call(
        "homeassistant",
        "update_entity",
        {"entity_id": LAST_SEEN_ENTITY},
        blocking=True,
    )
    await hass.async_block_till_done()
    _assert_full(mock_client)
    assert mock_config_entry.runtime_data.coordinator.push_active


@pytest.mark.parametrize("name", ["base_updated", "button_updated", "pusher_updated"])
async def test_pushed_lifecycle_event_reads_everything(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    name: str,
) -> None:
    """A pushed base, button or learner event reads everything right away."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    base = bases()[0]
    mock_client.async_get_bases.return_value = [
        dataclasses.replace(base, name="Renamed Base"),
        *bases()[1:],
    ]
    coordinator.processor.async_process(_event(name))
    await hass.async_block_till_done()
    _assert_full(mock_client)
    assert coordinator.data.bases[base.id].name == "Renamed Base"

    _reset_calls(mock_client)
    await _poll(hass, freezer)
    _assert_safety_poll(mock_client)


async def test_lifecycle_request_in_cooldown_reads_everything(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A lifecycle event in the debounce cooldown still reads everything later."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    processor = mock_config_entry.runtime_data.coordinator.processor
    processor.async_process(_event("base_updated"))
    await hass.async_block_till_done()
    _assert_full(mock_client)

    processor.async_process(_event("button_updated"))
    await hass.async_block_till_done()
    _assert_full(mock_client)
    await _wait(hass, freezer, timedelta(seconds=11))
    _assert_full(mock_client, 2)


async def test_unknown_button_press_in_safety_poll_reads_everything_once(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A press of an unknown button read by a safety poll reads everything once.

    The same poll reads the buttons and learners, so the press fires without a
    second round.
    """
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    mock_client.async_get_events.side_effect = [
        EventPage(
            (_press("00000000-0000-4000-8000-000000000a01", NEW_BUTTON.id),),
            NEXT_CURSOR,
        ),
    ]
    mock_client.async_get_buttons.return_value = [*buttons(), NEW_BUTTON]
    await _poll(hass, freezer)
    assert _calls(mock_client) == [
        "async_get_webhook",
        "async_get_events",
        "async_get_bases",
        "async_get_buttons",
        "async_get_learners",
    ]
    assert hass.states.get("event.treat_press").state != STATE_UNKNOWN

    mock_client.async_get_events.side_effect = empty_page
    await _wait(hass, freezer, timedelta(seconds=11))
    _assert_full(mock_client)


async def test_full_refresh_reads_resources_after_feed(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A full refresh reads the resources after the feed, in one round.

    A button added at FluentPet between the webhook check and the feed read is
    in the button list read after the feed, so its button.added event needs no
    second round and its press fires.
    """
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator

    def _feed(*args: Any, **kwargs: Any) -> EventPage:
        # The button appears at FluentPet together with its events.
        mock_client.async_get_buttons.return_value = [*buttons(), NEW_BUTTON]
        return EventPage(
            (
                _event("button_added"),
                _press("00000000-0000-4000-8000-000000000a02", NEW_BUTTON.id),
            ),
            NEXT_CURSOR,
        )

    mock_client.async_get_events.side_effect = _feed
    await coordinator.async_request_refresh()
    await hass.async_block_till_done()
    calls = _calls(mock_client)
    assert calls.index("async_get_events") < calls.index("async_get_bases")
    assert calls.index("async_get_events") < calls.index("async_get_buttons")
    assert NEW_BUTTON.id in coordinator.data.buttons
    assert hass.states.get("event.treat_press").state != STATE_UNKNOWN

    mock_client.async_get_events.side_effect = empty_page
    await _wait(hass, freezer, timedelta(seconds=11))
    _assert_full(mock_client)


async def test_pending_press_kept_through_failed_refresh(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failed refresh does not drop presses waiting for their button."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    mock_client.async_get_buttons.side_effect = FluentPetConnectionError("boom")
    coordinator.processor.async_process(
        _press("00000000-0000-4000-8000-000000000a03", NEW_BUTTON.id)
    )
    await hass.async_block_till_done()
    assert not coordinator.last_update_success
    assert coordinator.processor.pending_count == 1

    mock_client.async_get_buttons.side_effect = None
    mock_client.async_get_buttons.return_value = [*buttons(), NEW_BUTTON]
    await _poll(hass, freezer)
    assert coordinator.last_update_success
    assert coordinator.processor.pending_count == 0
    assert hass.states.get("event.treat_press").state != STATE_UNKNOWN


async def test_safety_poll_lifecycle_event_reads_everything_once(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Lifecycle events the webhook missed read everything once, in that poll."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    base = bases()[0]
    mock_client.async_get_bases.return_value = [
        dataclasses.replace(base, name="Renamed Base"),
        *bases()[1:],
    ]
    mock_client.async_get_events.side_effect = [
        EventPage((_event("base_updated"), _event("button_updated")), NEXT_CURSOR),
    ]
    await _poll(hass, freezer)
    assert coordinator.data.bases[base.id].name == "Renamed Base"
    _assert_full(mock_client)
    assert mock_client.async_get_events.await_count == 1

    mock_client.async_get_events.side_effect = empty_page
    await _wait(hass, freezer, timedelta(seconds=11))
    _assert_full(mock_client)
    assert mock_client.async_get_events.await_count == 1


# Last seen from pushed events


@pytest.mark.parametrize("name", ["press_created", "interaction_updated"])
async def test_pushed_event_moves_last_seen_forward(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    name: str,
) -> None:
    """A pushed press or interaction moves the last seen time of its Base on.

    Only forward, and without a request; an older Bases read does not move it
    back.
    """
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    assert hass.states.get(LAST_SEEN_ENTITY).state == "2026-10-17T12:00:00+00:00"

    coordinator.async_base_seen(_event(name, created_at="2026-10-17T12:03:00.000Z"))
    await hass.async_block_till_done()
    assert hass.states.get(LAST_SEEN_ENTITY).state == "2026-10-17T12:03:00+00:00"
    assert mock_client.mock_calls == []

    coordinator.async_base_seen(_event(name, created_at="2026-10-17T12:01:00.000Z"))
    await hass.async_block_till_done()
    assert hass.states.get(LAST_SEEN_ENTITY).state == "2026-10-17T12:03:00+00:00"

    await _poll(hass, freezer)
    assert hass.states.get(LAST_SEEN_ENTITY).state == "2026-10-17T12:03:00+00:00"


async def test_event_without_known_base_leaves_last_seen(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Events of another type, without a Base or of an unknown Base are ignored."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    data = coordinator.data
    later = "2026-10-17T12:30:00.000Z"
    raw = load_json_fixture("events/press_created.json")
    raw["created_at"] = later
    raw["payload"]["base"] = None
    coordinator.async_base_seen(Event.from_dict(raw))
    raw["payload"]["base"] = {"id": "base_testbaseunknownaaaaaaaaaa", "serial": None}
    coordinator.async_base_seen(Event.from_dict(raw))
    coordinator.async_base_seen(_event("base_updated", created_at=later))
    assert coordinator.data is data


# Switching between the modes


@pytest.mark.parametrize(
    ("remote", "error"),
    [
        (webhook(failure_count=3), None),
        (
            webhook(
                enabled=False, disabled_at="2026-10-18T08:00:00.000Z", failure_count=20
            ),
            None,
        ),
        (None, FluentPetRequestError(422, "unprocessable", "Url is invalid")),
        (None, FluentPetConnectionError("boom")),
    ],
    ids=["failing_quiet", "auto_disabled", "refused", "check_failed"],
)
async def test_unhealthy_webhook_falls_back_to_polling(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    remote: Any,
    error: Exception | None,
) -> None:
    """A quietly failing, disabled, refused or unverified webhook polls again."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    mock_client.async_get_webhook.return_value = remote
    mock_client.async_get_webhook.side_effect = error
    mock_client.async_create_webhook.side_effect = error
    await _poll(hass, freezer)
    mock_client.async_get_webhook.assert_awaited_once()
    assert not coordinator.push_active
    assert coordinator.update_interval == timedelta(seconds=60)

    for _ in range(2):
        _reset_calls(mock_client)
        await _wait(hass, freezer, timedelta(seconds=61))
        _assert_full(mock_client)
        assert mock_client.async_get_events.await_count == 1


async def test_webhook_unhealthy_between_refreshes_reschedules(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A webhook found unhealthy outside a refresh moves the next one earlier."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    mock_client.async_get_webhook.return_value = webhook(failure_count=3)
    await mock_config_entry.runtime_data.webhook.async_reconcile()
    assert not coordinator.push_active
    assert coordinator.update_interval == timedelta(seconds=60)

    _reset_calls(mock_client)
    await _wait(hass, freezer, timedelta(seconds=61))
    _assert_full(mock_client)


async def test_webhook_healthy_between_refreshes_reads_everything_once(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A webhook accepted outside a refresh: one full refresh, then safety polls."""
    mock_client.async_create_webhook.side_effect = FluentPetRequestError(
        422, "unprocessable", "Url is invalid"
    )
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    assert not coordinator.push_active
    assert coordinator.update_interval == timedelta(seconds=60)
    _reset_calls(mock_client)
    await _wait(hass, freezer, timedelta(seconds=61))
    _assert_full(mock_client)

    mock_client.async_create_webhook.side_effect = None
    await mock_config_entry.runtime_data.webhook.async_reconcile()
    assert coordinator.push_active
    assert coordinator.update_interval == SAFETY_POLL_INTERVAL

    _reset_calls(mock_client)
    await _wait(hass, freezer, timedelta(seconds=61))
    assert mock_client.mock_calls == []
    await _wait(hass, freezer, SAFETY_POLL_INTERVAL - timedelta(seconds=61))
    _assert_full(mock_client)

    _reset_calls(mock_client)
    await _poll(hass, freezer)
    _assert_safety_poll(mock_client)


async def test_webhook_healthy_during_refresh_switches_to_safety_poll(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A webhook accepted in a full refresh: that refresh counts as the full one."""
    mock_client.async_create_webhook.side_effect = FluentPetRequestError(
        422, "unprocessable", "Url is invalid"
    )
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    mock_client.async_create_webhook.side_effect = None
    # Full refreshes every minute until the slow webhook retry is due.
    for _ in range(4):
        _reset_calls(mock_client)
        await _wait(hass, freezer, timedelta(seconds=61))
        _assert_full(mock_client)
        assert not coordinator.push_active
    _reset_calls(mock_client)
    await _wait(hass, freezer, timedelta(seconds=61))
    mock_client.async_create_webhook.assert_awaited_once()
    _assert_full(mock_client)
    assert coordinator.push_active
    assert coordinator.update_interval == SAFETY_POLL_INTERVAL

    _reset_calls(mock_client)
    await _poll(hass, freezer)
    _assert_safety_poll(mock_client)


async def test_backoff_in_push_mode(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Backoff applies to the safety poll and is kept when push stops.

    After the backoff the interval of the mode at that time applies.
    """
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    mock_client.async_get_events.side_effect = FluentPetServerError(
        503, "service unavailable", None
    )
    await _poll(hass, freezer)
    assert not coordinator.last_update_success
    assert coordinator.update_interval == timedelta(minutes=10)

    mock_client.async_get_events.side_effect = empty_page
    await _wait(hass, freezer, timedelta(minutes=10))
    assert coordinator.last_update_success
    assert coordinator.update_interval == SAFETY_POLL_INTERVAL

    # Back off again, then the webhook stops being healthy: the backoff stays.
    mock_client.async_get_events.side_effect = FluentPetServerError(
        503, "service unavailable", None
    )
    await _poll(hass, freezer)
    assert coordinator.update_interval == timedelta(minutes=10)
    mock_client.async_get_webhook.return_value = webhook(failure_count=3)
    await mock_config_entry.runtime_data.webhook.async_reconcile()
    assert not coordinator.push_active
    assert coordinator.update_interval == timedelta(minutes=10)

    _reset_calls(mock_client)
    mock_client.async_get_events.side_effect = empty_page
    await _wait(hass, freezer, timedelta(seconds=61))
    assert mock_client.mock_calls == []
    await _wait(hass, freezer, timedelta(minutes=10))
    assert coordinator.last_update_success
    _assert_full(mock_client)
    assert coordinator.update_interval == timedelta(seconds=60)


async def test_safety_poll_commits_bases_like_full_refresh(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Bases of a safety poll go through the commit of a full refresh.

    A malformed Base keeps the devices; a complete list removes a departed one.
    """
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    entry_id = mock_config_entry.entry_id
    second = (DOMAIN, "base_testbasetwoaaaaaaaaaaaaaaa")
    mock_client.async_get_bases.return_value = bases()[:1]

    async def _with_invalid(*args: Any, **kwargs: Any) -> Any:
        kwargs["on_invalid"](FluentPetParseError("'id' is missing or not a string"))
        return bases()[:1]

    mock_client.async_get_bases.side_effect = _with_invalid
    await _poll(hass, freezer)
    _assert_safety_poll(mock_client)
    assert "Skipped a malformed FluentPet Base" in caplog.text
    assert device_registry.async_get_device_by_identifier(second, entry_id)
    coordinator = mock_config_entry.runtime_data.coordinator
    assert "base_testbasetwoaaaaaaaaaaaaaaa" in coordinator.known_base_ids

    mock_client.async_get_bases.side_effect = None
    await _poll(hass, freezer)
    assert device_registry.async_get_device_by_identifier(second, entry_id) is None
    assert coordinator.known_base_ids == {BASE_ID}


async def test_safety_poll_keeps_buttons_of_incomplete_read(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A safety poll does not remove a button skipped by the last button read."""
    await setup_integration(hass, mock_config_entry)
    entry_id = mock_config_entry.entry_id
    walk = (DOMAIN, "btn_testbuttonwalkaaaaaaaaaaaa")

    async def _with_invalid(*args: Any, **kwargs: Any) -> Any:
        kwargs["on_invalid"](FluentPetParseError("'id' is missing or not a string"))
        return buttons()[:2]

    mock_client.async_get_buttons.side_effect = _with_invalid
    await _poll(hass, freezer)
    _assert_full(mock_client, 2)
    _reset_calls(mock_client)
    await _poll(hass, freezer)
    _assert_safety_poll(mock_client)
    assert device_registry.async_get_device_by_identifier(walk, entry_id)
    coordinator = mock_config_entry.runtime_data.coordinator
    assert "btn_testbuttonwalkaaaaaaaaaaaa" in coordinator.known_button_ids


# Pushed last seen times and a refresh in flight

PUSHED_AT = "2026-10-17T12:03:00.000Z"
PUSHED_STATE = "2026-10-17T12:03:00+00:00"


async def _assert_pushed_kept(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Assert the pushed last seen time is in the data and the sensor."""
    coordinator = entry.runtime_data.coordinator
    assert coordinator.data.bases[BASE_ID].last_online_at == dt_util.parse_datetime(
        PUSHED_AT
    )
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(LAST_SEEN_ENTITY).state == PUSHED_STATE


@pytest.mark.parametrize(
    ("full", "method"),
    [
        (False, "async_get_bases"),
        (True, "async_get_bases"),
        (True, "async_get_learners"),
    ],
    ids=["safety_poll_bases", "full_bases", "full_learners"],
)
async def test_pushed_last_seen_during_read_kept(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    full: bool,
    method: str,
) -> None:
    """A last seen time pushed while a refresh reads is not moved back."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    mock = getattr(mock_client, method)
    answer = mock.return_value

    async def _push_during_read(*args: Any, **kwargs: Any) -> Any:
        coordinator.async_base_seen(_event("press_created", created_at=PUSHED_AT))
        return answer

    mock.side_effect = _push_during_read
    if full:
        await coordinator.async_request_refresh()
        await hass.async_block_till_done()
        _assert_full(mock_client)
    else:
        await _poll(hass, freezer)
        _assert_safety_poll(mock_client)
    assert coordinator.last_update_success
    await _assert_pushed_kept(hass, mock_config_entry)

    # A later read with the older time still keeps it.
    mock.side_effect = None
    await _poll(hass, freezer)
    await _assert_pushed_kept(hass, mock_config_entry)


async def test_pushed_last_seen_kept_over_unknown(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """FluentPet returning no last seen time does not clear a pushed one."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    coordinator.async_base_seen(_event("press_created", created_at=PUSHED_AT))
    first, second = bases()
    mock_client.async_get_bases.return_value = [
        dataclasses.replace(first, last_online_at=None),
        dataclasses.replace(second, last_online_at=None),
    ]
    await _poll(hass, freezer)
    await _assert_pushed_kept(hass, mock_config_entry)
    other = "sensor.base_testserial0002_last_seen"
    assert hass.states.get(other).state == STATE_UNKNOWN


async def test_pushed_last_seen_forgotten(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A pushed time is forgotten once FluentPet catches up or the Base is gone."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    first, second = bases()
    coordinator.async_base_seen(_event("press_created", created_at=PUSHED_AT))

    # FluentPet catches up: its later time applies, and then an older one too.
    caught_up = datetime(2026, 10, 17, 12, 5, tzinfo=dt_util.UTC)
    mock_client.async_get_bases.return_value = [
        dataclasses.replace(first, last_online_at=caught_up),
        second,
    ]
    await _poll(hass, freezer)
    assert coordinator.data.bases[BASE_ID].last_online_at == caught_up
    mock_client.async_get_bases.return_value = bases()
    await _poll(hass, freezer)
    assert coordinator.data.bases[BASE_ID].last_online_at == first.last_online_at

    # A Base that left the household takes its pushed time with it.
    coordinator.async_base_seen(_event("press_created", created_at=PUSHED_AT))
    mock_client.async_get_bases.return_value = [second]
    await _poll(hass, freezer)
    assert BASE_ID not in coordinator.data.bases
    mock_client.async_get_bases.return_value = bases()
    await _poll(hass, freezer)
    assert coordinator.data.bases[BASE_ID].last_online_at == first.last_online_at


async def test_pushed_last_seen_kept_when_base_skipped(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A Base missing from an incomplete read keeps its pushed time."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    second = bases()[1]
    coordinator.async_base_seen(_event("press_created", created_at=PUSHED_AT))

    async def _with_invalid(*args: Any, **kwargs: Any) -> Any:
        kwargs["on_invalid"](FluentPetParseError("'id' is missing or not a string"))
        return [second]

    mock_client.async_get_bases.side_effect = _with_invalid
    await _poll(hass, freezer)
    mock_client.async_get_bases.side_effect = None
    mock_client.async_get_bases.return_value = bases()
    await _poll(hass, freezer)
    await _assert_pushed_kept(hass, mock_config_entry)


async def test_feed_press_of_missing_button_dropped_in_same_poll(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A feed press of a button FluentPet does not return is dropped at once.

    The safety poll reads everything once, and no second round follows.
    """
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    processor = mock_config_entry.runtime_data.coordinator.processor
    mock_client.async_get_events.side_effect = [
        EventPage(
            (_press("00000000-0000-4000-8000-000000000a04", NEW_BUTTON.id),),
            NEXT_CURSOR,
        ),
    ]
    await _poll(hass, freezer)
    _assert_full(mock_client)
    assert processor.pending_count == 0
    assert hass.states.get("event.treat_press") is None

    mock_client.async_get_events.side_effect = empty_page
    await _wait(hass, freezer, timedelta(seconds=11))
    _assert_full(mock_client)


async def test_pushed_press_of_unknown_button_refreshes_once(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A pushed press of an unknown button requests one refresh, as before.

    The press waits for a button read that started after it arrived.
    """
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    processor = mock_config_entry.runtime_data.coordinator.processor
    processor.async_process(
        _press("00000000-0000-4000-8000-000000000a05", NEW_BUTTON.id)
    )
    await hass.async_block_till_done()
    _assert_full(mock_client)
    assert processor.pending_count == 0
    assert hass.states.get("event.treat_press") is None

    await _wait(hass, freezer, timedelta(seconds=11))
    _assert_full(mock_client)


@pytest.mark.parametrize("case", ["base_updated", "unknown_button_press", "held"])
async def test_processed_feed_event_keeps_safety_poll_light(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    case: str,
) -> None:
    """A feed event already pushed, or held by an entity, reads only the Bases."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    processor = mock_config_entry.runtime_data.coordinator.processor
    if case == "unknown_button_press":
        event = _press("00000000-0000-4000-8000-000000000a06", NEW_BUTTON.id)
    else:
        event = _event("base_updated")
    if case == "held":
        processor.async_hold(event.id)
    else:
        processor.async_process(event)
        await hass.async_block_till_done()
        _assert_full(mock_client)
    await _wait(hass, freezer, timedelta(seconds=11))

    _reset_calls(mock_client)
    mock_client.async_get_events.side_effect = [EventPage((event,), NEXT_CURSOR)]
    await _poll(hass, freezer)
    _assert_safety_poll(mock_client)


async def test_missed_press_in_safety_poll_live(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A press the webhook missed, read by a safety poll later, is not replayed."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    data = load_json_fixture("events/press_created.json")
    data["payload"]["press"]["occurred_at"] = (
        dt_util.utcnow() - timedelta(minutes=4)
    ).isoformat()
    mock_client.async_get_events.side_effect = [
        EventPage((Event.from_dict(data),), NEXT_CURSOR)
    ]
    await _poll(hass, freezer)
    state = hass.states.get(PRESS_ENTITY)
    assert state.state != STATE_UNKNOWN
    assert state.attributes["replayed"] is False


async def test_safety_poll_press_kept_until_entity_ready(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A press of a known button without its entity yet waits for the entity.

    The press survives the Bases-only safety poll and the next full read
    because its button is known; only presses of unknown buttons are dropped.
    The refresh_count - 1 path, a feed press of a button missing from the
    read being dropped in that same refresh, is pinned by
    test_feed_press_of_missing_button_dropped_in_same_poll.
    """
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    processor = coordinator.processor
    ready = processor.async_button_ready
    held_back: list[str] = []

    def _not_ready_yet(button_id: str) -> Callable[[], None]:
        if button_id == NEW_BUTTON.id:
            held_back.append(button_id)
            return lambda: None
        return ready(button_id)

    mock_client.async_get_buttons.return_value = [*buttons(), NEW_BUTTON]
    with patch.object(processor, "async_button_ready", _not_ready_yet):
        await coordinator.async_request_refresh()
        await hass.async_block_till_done()
    assert held_back == [NEW_BUTTON.id]
    assert NEW_BUTTON.id in coordinator.known_button_ids

    _reset_calls(mock_client)
    mock_client.async_get_events.side_effect = [
        EventPage(
            (_press("00000000-0000-4000-8000-000000000a07", NEW_BUTTON.id),),
            NEXT_CURSOR,
        ),
    ]
    await _poll(hass, freezer)
    _assert_safety_poll(mock_client)
    assert processor.pending_count == 1

    _reset_calls(mock_client)
    mock_client.async_get_events.side_effect = empty_page
    await coordinator.async_request_refresh()
    await hass.async_block_till_done()
    _assert_full(mock_client)
    assert processor.pending_count == 1

    ready(NEW_BUTTON.id)
    await hass.async_block_till_done()
    assert processor.pending_count == 0
    state = hass.states.get("event.treat_press")
    assert state.state != STATE_UNKNOWN
    assert state.attributes["replayed"] is False


# Battery levels


BASE_BATTERY_ENTITY = "sensor.test_base_battery"
BUTTON_BATTERY_ENTITY = "sensor.outside_battery"


async def test_safety_poll_keeps_button_battery(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A safety poll updates Base levels; buttons change only when read.

    The safety poll does not read the buttons, so an unlinked button keeps its
    battery sensor until a button event reads them.
    """
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    first, second = bases()
    outside, *rest = buttons()
    mock_client.async_get_bases.return_value = [
        dataclasses.replace(first, battery_level=25, power_mode="battery"),
        second,
    ]
    mock_client.async_get_buttons.return_value = [
        dataclasses.replace(outside, base_id=None, battery_level=None),
        *rest,
    ]
    await _poll(hass, freezer)
    _assert_safety_poll(mock_client)
    mock_client.async_get_buttons.assert_not_awaited()
    assert hass.states.get(BASE_BATTERY_ENTITY).state == "25"
    assert entity_registry.async_get(BUTTON_BATTERY_ENTITY) is not None
    assert hass.states.get(BUTTON_BATTERY_ENTITY).state == "75"

    coordinator = mock_config_entry.runtime_data.coordinator
    coordinator.processor.async_process(_event("button_updated"))
    await hass.async_block_till_done()
    mock_client.async_get_buttons.assert_awaited_once()
    assert entity_registry.async_get(BUTTON_BATTERY_ENTITY) is None


async def test_feed_button_updated_updates_battery_in_safety_poll(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A button.updated the webhook missed reads the buttons in that poll."""
    await _setup_push(hass, freezer, mock_client, mock_config_entry)
    outside, *rest = buttons()
    mock_client.async_get_buttons.return_value = [
        dataclasses.replace(outside, battery_level=25),
        *rest,
    ]
    mock_client.async_get_events.side_effect = [
        EventPage((_event("button_updated"),), NEXT_CURSOR)
    ]
    await _poll(hass, freezer)
    assert mock_client.async_get_buttons.await_count == 1
    assert hass.states.get(BUTTON_BATTERY_ENTITY).state == "25"
