"""Tests for the FluentPet Connect coordinator and event feed."""

from dataclasses import replace
from datetime import timedelta
import importlib
from typing import Any
from unittest.mock import ANY, AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.fluentpet.const import DOMAIN
from custom_components.fluentpet.coordinator import not_admin_issue_id
from custom_components.fluentpet.fluentpet_api import (
    Button,
    Event,
    EventPage,
    FluentPetAuthError,
    FluentPetConnectionError,
    FluentPetForbiddenError,
    FluentPetParseError,
    FluentPetRateLimitError,
    FluentPetRequestError,
    FluentPetServerError,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import EVENT_STATE_CHANGED, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.util import dt as dt_util

from .common import load_json_fixture, setup_integration
from .conftest import HOUSEHOLD_ID, TAIL_CURSOR, buttons, empty_page

PRESS_ENTITY = "event.outside_press"
OUTSIDE_ID = "btn_testbuttonoutsideaaaaaaaaa"
LAST_SEEN = "2026-10-17T12:00:00+00:00"
SENSOR_ENTITY = "sensor.test_household_last_interaction"
NEW_BUTTON = Button(
    id="btn_testbuttonnewaaaaaaaaaaaaa",
    name="Treat",
    normalized_word="treat",
    base_id="base_testbaseoneaaaaaaaaaaaaaaa",
    serial="TESTBTNSER09",
    audio_id=None,
)


def _event(name: str, **overrides: Any) -> Event:
    """Return an event fixture as a model."""
    return Event.from_dict({**load_json_fixture(f"events/{name}.json"), **overrides})


def _press(event_id: str, button_id: str) -> Event:
    """Return a press.created event for a button."""
    data = load_json_fixture("events/press_created.json")
    data["id"] = event_id
    data["payload"]["button"]["id"] = button_id
    return Event.from_dict(data)


async def _refresh(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int = 61
) -> None:
    """Move time past the refresh interval."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_feed_fires_entities_and_saves_cursor(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    """Events read at setup fire once their entities exist; the cursor is saved."""
    page = EventPage.from_dict(load_json_fixture("events_page.json"))
    mock_client.async_get_events.side_effect = [page, EventPage((), page.next_cursor)]
    await setup_integration(hass, mock_config_entry)

    mock_client.async_get_events.assert_awaited_once_with(
        HOUSEHOLD_ID, since=TAIL_CURSOR, limit=100, on_invalid=ANY
    )
    state = hass.states.get(PRESS_ENTITY)
    assert state.state != STATE_UNKNOWN
    assert state.attributes["interaction_id"] == "int_testinteractionaaaaaaaaaaa"
    assert hass.states.get(SENSOR_ENTITY).state == "Outside Play"
    assert hass_storage[f"{DOMAIN}.{mock_config_entry.entry_id}"]["data"] == {
        "cursor": page.next_cursor,
        "event_ids": [event.id for event in page.events],
    }


async def test_cursor_resumed_after_restart(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    """A stored cursor is used instead of the tail cursor."""
    hass_storage[f"{DOMAIN}.{mock_config_entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{mock_config_entry.entry_id}",
        "data": {"cursor": "AAAAAAAAAAtestcursor00000007"},
    }
    await setup_integration(hass, mock_config_entry)
    mock_client.async_get_tail_cursor.assert_not_awaited()
    mock_client.async_get_events.assert_awaited_once_with(
        HOUSEHOLD_ID, since="AAAAAAAAAAtestcursor00000007", limit=100, on_invalid=ANY
    )


async def test_feed_pages(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A full page is followed by the next one."""
    full = EventPage(
        tuple(
            _press(
                f"00000000-0000-4000-8000-{index:012d}",
                "btn_testbuttonplayaaaaaaaaaaaa",
            )
            for index in range(100)
        ),
        "AAAAAAAAAAtestcursor00000100",
    )
    mock_client.async_get_events.side_effect = [
        full,
        EventPage((), "AAAAAAAAAAtestcursor00000100"),
    ]
    await setup_integration(hass, mock_config_entry)
    calls = mock_client.async_get_events.await_args_list
    assert [call.kwargs["since"] for call in calls] == [
        TAIL_CURSOR,
        "AAAAAAAAAAtestcursor00000100",
    ]
    assert hass.states.get("event.play_press").state != STATE_UNKNOWN


async def test_feed_events_without_cursor(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A household without events starts reading from the beginning."""
    mock_client.async_get_tail_cursor.return_value = None
    await setup_integration(hass, mock_config_entry)
    mock_client.async_get_events.assert_awaited_once_with(
        HOUSEHOLD_ID, since=None, limit=100, on_invalid=ANY
    )


async def test_polling_fires_press(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A press read on a later refresh fires the event entity."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(PRESS_ENTITY).state == STATE_UNKNOWN
    mock_client.async_get_events.side_effect = [
        EventPage((_event("press_created"),), "AAAAAAAAAAtestcursor00000014")
    ]
    await _refresh(hass, freezer)
    assert hass.states.get(PRESS_ENTITY).state != STATE_UNKNOWN


@pytest.mark.parametrize(
    "error",
    [
        FluentPetRateLimitError(429, "too many requests", None, 30),
        FluentPetServerError(503, "service unavailable", None),
    ],
)
async def test_backoff(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    error: Exception,
) -> None:
    """Rate limits and server errors double the interval up to ten minutes."""
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    mock_client.async_get_bases.side_effect = error

    expected = [120, 240, 480, 600, 600]
    for seconds in expected:
        await _refresh(
            hass, freezer, int(coordinator.update_interval.total_seconds()) + 1
        )
        assert coordinator.update_interval == timedelta(seconds=seconds)
    assert hass.states.get("sensor.test_base_last_seen").state == STATE_UNAVAILABLE

    mock_client.async_get_bases.side_effect = None
    await _refresh(hass, freezer, 601)
    assert coordinator.update_interval == timedelta(seconds=60)
    assert hass.states.get("sensor.test_base_last_seen").state == LAST_SEEN


async def test_connection_error_keeps_interval(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A network error marks entities unavailable without backing off."""
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    mock_client.async_get_buttons.side_effect = FluentPetConnectionError("boom")
    await _refresh(hass, freezer)
    assert coordinator.update_interval == timedelta(seconds=60)
    assert not coordinator.last_update_success


async def test_auth_error_starts_reauth(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A refused token on refresh starts reauthentication."""
    await setup_integration(hass, mock_config_entry)
    mock_client.async_get_events.side_effect = FluentPetAuthError(
        401, "unauthorized", None
    )
    await _refresh(hass, freezer)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH


async def test_feed_forbidden(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A feed only the admin may read is not polled again."""
    mock_client.async_get_events.side_effect = FluentPetForbiddenError(
        403, "forbidden", None
    )
    await setup_integration(hass, mock_config_entry)
    await _refresh(hass, freezer)
    assert mock_client.async_get_events.await_count == 1
    assert caplog.text.count("only the household admin receives button presses") == 1
    assert mock_config_entry.runtime_data.coordinator.last_update_success


async def test_lifecycle_event_in_full_refresh_no_second_round(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Lifecycle events read by a full refresh request no other refresh.

    That refresh read the resources itself.
    """
    await setup_integration(hass, mock_config_entry)
    mock_client.async_get_events.side_effect = [
        EventPage(
            (_event("base_updated"), _event("button_updated")),
            "AAAAAAAAAAtestcursor00000014",
        ),
    ]
    await _refresh(hass, freezer)
    assert mock_client.async_get_bases.await_count == 2
    assert mock_client.async_get_events.await_count == 2
    mock_client.async_get_events.side_effect = empty_page
    await _refresh(hass, freezer, 11)
    assert mock_client.async_get_bases.await_count == 2
    assert mock_client.async_get_buttons.await_count == 2
    assert mock_client.async_get_learners.await_count == 2
    assert mock_client.async_get_events.await_count == 2


async def test_devices(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """The hub, Bases and buttons are linked through via_device_id."""
    await setup_integration(hass, mock_config_entry)
    entry_id = mock_config_entry.entry_id

    hub = device_registry.async_get_device_by_identifier(
        (DOMAIN, HOUSEHOLD_ID), entry_id
    )
    assert hub is not None
    assert hub.name == "Test Household"
    assert hub.model == "Household"
    assert hub.entry_type is dr.DeviceEntryType.SERVICE

    base = device_registry.async_get_device_by_identifier(
        (DOMAIN, "base_testbaseoneaaaaaaaaaaaaaaa"), entry_id
    )
    assert base is not None
    assert base.via_device_id == hub.id
    assert base.manufacturer == "FluentPet"
    assert base.model == "connect-base"
    assert base.serial_number == "TESTSERIAL0001"
    assert base.sw_version == "1.1.0"
    assert base.hw_version is None

    unnamed = device_registry.async_get_device_by_identifier(
        (DOMAIN, "base_testbasetwoaaaaaaaaaaaaaaa"), entry_id
    )
    assert unnamed is not None
    assert unnamed.name == "Base TESTSERIAL0002"
    # Not a published release, or not reported yet.
    assert unnamed.sw_version is None

    linked = device_registry.async_get_device_by_identifier(
        (DOMAIN, "btn_testbuttonoutsideaaaaaaaaa"), entry_id
    )
    assert linked is not None
    assert linked.via_device_id == base.id
    assert linked.model == "Connect Button"
    assert linked.serial_number == "TESTBTNSER01"
    assert linked.sw_version == "1.1.0"
    assert linked.hw_version is None

    unpublished = device_registry.async_get_device_by_identifier(
        (DOMAIN, "btn_testbuttonplayaaaaaaaaaaaa"), entry_id
    )
    assert unpublished is not None
    assert unpublished.sw_version is None

    unlinked = device_registry.async_get_device_by_identifier(
        (DOMAIN, "btn_testbuttonwalkaaaaaaaaaaaa"), entry_id
    )
    assert unlinked is not None
    assert unlinked.via_device_id == hub.id
    assert unlinked.model == "Connect Button"
    assert unlinked.sw_version is None


async def test_device_versions_follow_refresh(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A refresh updates the firmware versions of existing devices.

    A Base or button moving to a build that is not a published release has its
    firmware version cleared; a device registered with an earlier value keeps
    its id.
    """
    # Not setup_integration: the entry must exist before the device is registered.
    mock_config_entry.add_to_hass(hass)
    old = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "base_testbaseoneaaaaaaaaaaaaaaa")},
        manufacturer="FluentPet",
        sw_version="2.4.20260901",
    )
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    entry_id = mock_config_entry.entry_id

    base = device_registry.async_get_device_by_identifier(
        (DOMAIN, "base_testbaseoneaaaaaaaaaaaaaaa"), entry_id
    )
    assert base is not None
    assert base.id == old.id
    assert base.sw_version == "1.1.0"

    first, second = mock_client.async_get_bases.return_value
    mock_client.async_get_bases.return_value = [
        replace(first, firmware_version=None),
        replace(second, firmware_version="1.1.1"),
    ]
    outside, play, walk = buttons()
    mock_client.async_get_buttons.return_value = [
        replace(outside, firmware_version=None),
        replace(play, firmware_version="1.1.1"),
        walk,
    ]
    await mock_config_entry.runtime_data.coordinator.async_request_refresh()
    await hass.async_block_till_done()

    versions = {
        identifier: device.sw_version
        for device in dr.async_entries_for_config_entry(device_registry, entry_id)
        for _, identifier in device.identifiers
    }
    assert versions["base_testbaseoneaaaaaaaaaaaaaaa"] is None
    assert versions["base_testbasetwoaaaaaaaaaaaaaaa"] == "1.1.1"
    assert versions[OUTSIDE_ID] is None
    assert versions["btn_testbuttonplayaaaaaaaaaaaa"] == "1.1.1"
    assert versions["btn_testbuttonwalkaaaaaaaaaaaa"] is None


async def test_button_model_updated_on_existing_device(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A button device registered with the old model keeps its id and entity."""
    # Not setup_integration: the entry must exist before the device is registered.
    mock_config_entry.add_to_hass(hass)
    old = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, OUTSIDE_ID)},
        manufacturer="FluentPet",
        model="Button",
    )
    old_entity = entity_registry.async_get_or_create(
        "event",
        DOMAIN,
        f"{OUTSIDE_ID}_press",
        config_entry=mock_config_entry,
        device_id=old.id,
        suggested_object_id="outside_press",
    )
    assert old_entity.entity_id == PRESS_ENTITY

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, OUTSIDE_ID), mock_config_entry.entry_id
    )
    assert device is not None
    assert device.id == old.id
    assert device.model == "Connect Button"
    entity = entity_registry.async_get(PRESS_ENTITY)
    assert entity is not None
    assert entity.id == old_entity.id
    assert entity.unique_id == f"{OUTSIDE_ID}_press"
    assert entity.device_id == old.id
    assert [
        entry.id
        for entry in er.async_entries_for_device(entity_registry, old.id)
        if entry.domain == "event"
    ] == [old_entity.id]


async def test_stale_and_new_devices(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Removed buttons lose their device; new buttons get a device and entity."""
    await setup_integration(hass, mock_config_entry)
    entry_id = mock_config_entry.entry_id
    assert entity_registry.async_get("event.walk_press") is not None

    mock_client.async_get_buttons.return_value = [*buttons()[:2], NEW_BUTTON]
    await _refresh(hass, freezer)

    assert (
        device_registry.async_get_device_by_identifier(
            (DOMAIN, "btn_testbuttonwalkaaaaaaaaaaaa"), entry_id
        )
        is None
    )
    assert entity_registry.async_get("event.walk_press") is None
    assert (
        device_registry.async_get_device_by_identifier(
            (DOMAIN, NEW_BUTTON.id), entry_id
        )
        is not None
    )
    assert hass.states.get("event.treat_press") is not None


async def test_new_base_entity(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A new Base gets a last seen sensor; a removed one loses it."""
    await setup_integration(hass, mock_config_entry)
    bases = mock_client.async_get_bases.return_value
    mock_client.async_get_bases.return_value = bases[:1]
    await _refresh(hass, freezer)
    assert hass.states.get("sensor.base_testserial0002_last_seen") is None

    mock_client.async_get_bases.return_value = bases
    await _refresh(hass, freezer)
    assert hass.states.get("sensor.base_testserial0002_last_seen") is not None


async def test_press_for_unknown_button(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A press of a button added after setup creates it, then fires."""
    await setup_integration(hass, mock_config_entry)
    mock_client.async_get_events.side_effect = [
        EventPage(
            (_press("00000000-0000-4000-8000-0000000000f1", NEW_BUTTON.id),), "c1"
        ),
        EventPage((), "c1"),
    ]
    mock_client.async_get_buttons.return_value = [*buttons(), NEW_BUTTON]
    await _refresh(hass, freezer)
    await hass.async_block_till_done()
    state = hass.states.get("event.treat_press")
    assert state is not None
    assert state.state != STATE_UNKNOWN
    assert mock_config_entry.runtime_data.coordinator.processor.pending_count == 0


async def test_press_for_button_that_never_appears(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A feed press of a button the household does not return is dropped.

    The refresh that read it read the buttons after it, so the press is dropped
    in that refresh, without a second round.
    """
    await setup_integration(hass, mock_config_entry)
    processor = mock_config_entry.runtime_data.coordinator.processor
    mock_client.async_get_events.side_effect = [
        EventPage((_press("00000000-0000-4000-8000-0000000000f2", "btn_gone"),), "c1"),
        EventPage((), "c1"),
    ]
    await _refresh(hass, freezer)
    assert processor.pending_count == 0
    assert mock_client.async_get_buttons.await_count == 2
    await _refresh(hass, freezer, 11)
    assert mock_client.async_get_buttons.await_count == 2


async def test_duplicate_feed_event_fires_once(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An event read twice fires once."""
    await setup_integration(hass, mock_config_entry)
    events = async_capture_events(hass, EVENT_STATE_CHANGED)
    press = _event("press_created")
    mock_client.async_get_events.side_effect = [
        EventPage((press,), "c1"),
        EventPage((press,), "c2"),
    ]
    await _refresh(hass, freezer)
    await _refresh(hass, freezer)
    fired = [
        event.data["entity_id"]
        for event in events
        if event.data["entity_id"] == PRESS_ENTITY
    ]
    assert fired == [PRESS_ENTITY]


async def test_stored_event_ids_dedupe_after_restart(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    """Events processed before a restart do not fire again when read once more."""
    press = _event("press_created")
    hass_storage[f"{DOMAIN}.{mock_config_entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{mock_config_entry.entry_id}",
        "data": {"cursor": "AAAAAAAAAAtestcursor00000007", "event_ids": [press.id, 5]},
    }
    mock_client.async_get_events.side_effect = [
        EventPage((press,), "AAAAAAAAAAtestcursor00000008")
    ]
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(PRESS_ENTITY).state == STATE_UNKNOWN


STORED_CURSOR = "AAAAAAAAAAtestcursor00000007"


def _store(hass_storage: dict[str, Any], entry: MockConfigEntry, data: Any) -> None:
    """Put feed state into the store of an entry."""
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": data,
    }


async def test_rejected_cursor_restarts_at_tail(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stored position FluentPet refuses is replaced by the tail, once."""
    _store(hass_storage, mock_config_entry, {"cursor": STORED_CURSOR, "event_ids": []})
    mock_client.async_get_events.side_effect = [
        FluentPetRequestError(400, "bad request", "Invalid cursor"),
        EventPage((_event("press_created"),), "AAAAAAAAAAtestcursor00000014"),
    ]
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    mock_client.async_get_tail_cursor.assert_awaited_once_with(HOUSEHOLD_ID)
    calls = mock_client.async_get_events.await_args_list
    assert [call.kwargs["since"] for call in calls] == [STORED_CURSOR, TAIL_CURSOR]
    assert caplog.text.count("FluentPet refused the stored event feed position") == 1
    assert hass.states.get(PRESS_ENTITY).state != STATE_UNKNOWN
    stored = hass_storage[f"{DOMAIN}.{mock_config_entry.entry_id}"]["data"]
    assert stored["cursor"] == "AAAAAAAAAAtestcursor00000014"


@pytest.mark.parametrize("tail", [TAIL_CURSOR, None])
async def test_rejected_cursor_twice_fails(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    tail: str | None,
) -> None:
    """The tail is fetched once per refresh; a second refusal fails the refresh."""
    _store(hass_storage, mock_config_entry, {"cursor": STORED_CURSOR, "event_ids": []})
    mock_client.async_get_tail_cursor.return_value = tail
    mock_client.async_get_events.side_effect = FluentPetRequestError(
        400, "bad request", "Invalid cursor"
    )
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_client.async_get_tail_cursor.assert_awaited_once()
    assert mock_client.async_get_events.await_count == 2


@pytest.mark.parametrize(
    ("stored", "tail_read", "warning"),
    [
        (["not", "a", "dict"], True, "feed state"),
        ({"cursor": 5, "event_ids": []}, True, "feed position"),
        ({"event_ids": []}, True, "feed position"),
        ({"cursor": STORED_CURSOR, "event_ids": "x"}, False, "event ids"),
        ({"cursor": None, "event_ids": ["a", 5]}, False, "event ids"),
    ],
)
async def test_malformed_store(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    stored: Any,
    tail_read: bool,
    warning: str,
) -> None:
    """Invalid parts of the store are discarded with a warning."""
    _store(hass_storage, mock_config_entry, stored)
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_client.async_get_tail_cursor.await_count == int(tail_read)
    assert f"Ignoring the stored FluentPet {warning}" in caplog.text or (
        f"Ignoring invalid stored FluentPet {warning}" in caplog.text
    )
    saved = hass_storage[f"{DOMAIN}.{mock_config_entry.entry_id}"]["data"]
    assert isinstance(saved["event_ids"], list)
    assert all(isinstance(event_id, str) for event_id in saved["event_ids"])


async def test_rate_limit_retry_after(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The Retry-After of a rate limit sets when the next refresh runs."""
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    mock_client.async_get_bases.side_effect = FluentPetRateLimitError(
        429, "too many requests", None, 45
    )
    await _refresh(hass, freezer)
    assert coordinator.last_exception.retry_after == 45
    assert coordinator.update_interval == timedelta(seconds=120)
    assert mock_client.async_get_bases.await_count == 2

    await _refresh(hass, freezer, 40)
    assert mock_client.async_get_bases.await_count == 2
    await _refresh(hass, freezer, 6)
    assert mock_client.async_get_bases.await_count == 3


async def test_feed_forbidden_repair_and_recovery(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A feed lost to a 403 raises a repair issue, is retried slowly and recovers."""
    await setup_integration(hass, mock_config_entry)
    issue_id = not_admin_issue_id(mock_config_entry.entry_id)
    mock_client.async_get_events.side_effect = FluentPetForbiddenError(
        403, "not allowed", None
    )
    await _refresh(hass, freezer)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None
    calls = mock_client.async_get_events.await_count
    await _refresh(hass, freezer)
    assert mock_client.async_get_events.await_count == calls

    await _refresh(hass, freezer, 5 * 60)
    assert mock_client.async_get_events.await_count == calls + 1
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    mock_client.async_get_events.side_effect = empty_page
    await _refresh(hass, freezer, 10 * 60)
    assert mock_client.async_get_events.await_count == calls + 2
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    assert "is readable again" in caplog.text
    await _refresh(hass, freezer)
    assert mock_client.async_get_events.await_count == calls + 3


async def test_backlog_after_forbidden_feed_replayed(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The backlog read once a forbidden feed is readable again is replayed."""
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    assert not coordinator.catching_up
    mock_client.async_get_events.side_effect = FluentPetForbiddenError(
        403, "not allowed", None
    )
    await _refresh(hass, freezer)
    assert coordinator.last_update_success
    assert coordinator.catching_up

    mock_client.async_get_events.side_effect = [
        EventPage((_press("00000000-0000-4000-8000-0000000000d1", OUTSIDE_ID),), "c1"),
    ]
    await _refresh(hass, freezer, 5 * 60)
    assert hass.states.get(PRESS_ENTITY).attributes["replayed"] is True
    assert not coordinator.catching_up


async def test_tail_forbidden_at_setup_then_admin(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
    issue_registry: ir.IssueRegistry,
) -> None:
    """A feed unreadable at setup starts at the tail once access returns."""
    mock_client.async_get_tail_cursor.side_effect = FluentPetForbiddenError(
        403, "not allowed", None
    )
    await setup_integration(hass, mock_config_entry)
    issue_id = not_admin_issue_id(mock_config_entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    await _refresh(hass, freezer, 5 * 60)
    assert mock_client.async_get_tail_cursor.await_count == 2
    mock_client.async_get_events.assert_not_awaited()

    mock_client.async_get_tail_cursor.side_effect = None
    await _refresh(hass, freezer, 10 * 60)
    mock_client.async_get_events.assert_awaited_once_with(
        HOUSEHOLD_ID, since=TAIL_CURSOR, limit=100, on_invalid=ANY
    )
    assert hass_storage[f"{DOMAIN}.{mock_config_entry.entry_id}"]["data"] == {
        "cursor": TAIL_CURSOR,
        "event_ids": [],
    }
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_malformed_feed_event_skipped(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A malformed event is skipped with a warning; the rest fire, the cursor moves."""
    await setup_integration(hass, mock_config_entry)

    def _page(*args: Any, **kwargs: Any) -> EventPage:
        kwargs["on_invalid"](FluentPetParseError("'id' is missing or not a string"))
        return EventPage((_event("press_created"),), "AAAAAAAAAAtestcursor00000015")

    mock_client.async_get_events.side_effect = _page
    await _refresh(hass, freezer)
    assert "Skipped a malformed FluentPet event" in caplog.text
    assert hass.states.get(PRESS_ENTITY).state != STATE_UNKNOWN
    coordinator = mock_config_entry.runtime_data.coordinator
    assert coordinator.cursor == "AAAAAAAAAAtestcursor00000015"
    assert coordinator.last_update_success


@pytest.mark.parametrize(
    "method", ["async_get_bases", "async_get_buttons", "async_get_learners"]
)
async def test_malformed_resource_keeps_devices(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
    method: str,
) -> None:
    """A skipped malformed item keeps stale devices until a complete refresh."""
    await setup_integration(hass, mock_config_entry)
    entry_id = mock_config_entry.entry_id
    walk = (DOMAIN, "btn_testbuttonwalkaaaaaaaaaaaa")
    mock_client.async_get_buttons.return_value = buttons()[:2]
    original = getattr(mock_client, method).return_value

    async def _with_invalid(*args: Any, **kwargs: Any) -> Any:
        kwargs["on_invalid"](FluentPetParseError("'id' is missing or not a string"))
        return getattr(mock_client, method).return_value

    getattr(mock_client, method).side_effect = _with_invalid
    await _refresh(hass, freezer)
    assert "Skipped a malformed FluentPet" in caplog.text
    assert device_registry.async_get_device_by_identifier(walk, entry_id) is not None
    coordinator = mock_config_entry.runtime_data.coordinator
    assert "btn_testbuttonwalkaaaaaaaaaaaa" in coordinator.known_button_ids
    assert coordinator.last_update_success

    getattr(mock_client, method).side_effect = None
    getattr(mock_client, method).return_value = original
    await _refresh(hass, freezer)
    assert device_registry.async_get_device_by_identifier(walk, entry_id) is None


async def test_entities_unavailable_while_refresh_fails(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The press entity and the sensor follow the refresh, keeping their value."""
    mock_client.async_get_events.side_effect = [
        EventPage.from_dict(load_json_fixture("events_page.json")),
        FluentPetConnectionError("boom"),
        EventPage((), "AAAAAAAAAAtestcursor00000002"),
    ]
    await setup_integration(hass, mock_config_entry)
    press = hass.states.get(PRESS_ENTITY).state
    assert hass.states.get(SENSOR_ENTITY).state == "Outside Play"

    await _refresh(hass, freezer)
    assert hass.states.get(PRESS_ENTITY).state == STATE_UNAVAILABLE
    assert hass.states.get(SENSOR_ENTITY).state == STATE_UNAVAILABLE

    await _refresh(hass, freezer)
    assert hass.states.get(PRESS_ENTITY).state == press
    assert hass.states.get(SENSOR_ENTITY).state == "Outside Play"


async def test_press_read_in_recovering_refresh_fires(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Events read by the refresh that recovers fire after the entity is available."""
    await setup_integration(hass, mock_config_entry)
    events = async_capture_events(hass, EVENT_STATE_CHANGED)
    mock_client.async_get_bases.side_effect = FluentPetConnectionError("boom")
    await _refresh(hass, freezer)
    assert hass.states.get(PRESS_ENTITY).state == STATE_UNAVAILABLE

    mock_client.async_get_bases.side_effect = None
    mock_client.async_get_events.side_effect = [
        EventPage.from_dict(load_json_fixture("events_page.json")),
        EventPage((), "AAAAAAAAAAtestcursor00000002"),
    ]
    await _refresh(hass, freezer)
    press = hass.states.get(PRESS_ENTITY)
    assert press.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
    assert press.attributes["interaction_id"] == "int_testinteractionaaaaaaaaaaa"
    assert hass.states.get(SENSOR_ENTITY).state == "Outside Play"
    changes = [
        (event.data["entity_id"], event.data["new_state"].state) for event in events
    ]
    press_states = [state for entity, state in changes if entity == PRESS_ENTITY]
    assert press_states[:2] == [STATE_UNAVAILABLE, STATE_UNKNOWN]
    assert press_states[-1] == press.state
    sensor_states = [state for entity, state in changes if entity == SENSOR_ENTITY]
    assert sensor_states == [STATE_UNAVAILABLE, STATE_UNKNOWN, "Outside Play"]


async def test_failed_refresh_keeps_feed_position(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """Events of a refresh that fails part way are read again, and fire once."""
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    full = EventPage(
        tuple(
            _press(
                f"00000000-0000-4000-8000-{index:012d}",
                "btn_testbuttonplayaaaaaaaaaaaa",
            )
            for index in range(100)
        ),
        "AAAAAAAAAAtestcursor00000100",
    )
    mock_client.async_get_events.side_effect = [full, FluentPetConnectionError("boom")]
    await _refresh(hass, freezer)
    assert not coordinator.last_update_success
    assert coordinator.cursor == TAIL_CURSOR
    assert hass.states.get("event.play_press").state == STATE_UNAVAILABLE
    stored = hass_storage[f"{DOMAIN}.{mock_config_entry.entry_id}"]["data"]
    assert stored["cursor"] == TAIL_CURSOR

    events = async_capture_events(hass, EVENT_STATE_CHANGED)
    mock_client.async_get_events.side_effect = [
        full,
        EventPage((), "AAAAAAAAAAtestcursor00000100"),
    ]
    await _refresh(hass, freezer)
    calls = mock_client.async_get_events.await_args_list
    assert calls[-2].kwargs["since"] == TAIL_CURSOR
    assert coordinator.cursor == "AAAAAAAAAAtestcursor00000100"
    fired = [
        event
        for event in events
        if event.data["entity_id"] == "event.play_press"
        and event.data["new_state"].state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
    ]
    assert len(fired) == 100


async def test_skipped_button_not_added_twice(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A button skipped by one refresh keeps its single entity when it returns."""
    await setup_integration(hass, mock_config_entry)
    all_buttons = buttons()

    async def _skip_walk(*args: Any, **kwargs: Any) -> list[Button]:
        kwargs["on_invalid"](FluentPetParseError("'id' is missing or not a string"))
        return all_buttons[:2]

    mock_client.async_get_buttons.side_effect = _skip_walk
    await _refresh(hass, freezer)
    mock_client.async_get_buttons.side_effect = None
    mock_client.async_get_buttons.return_value = all_buttons
    await _refresh(hass, freezer)
    assert "already exists" not in caplog.text
    entries = [
        entry
        for entry in er.async_entries_for_config_entry(
            entity_registry, mock_config_entry.entry_id
        )
        if entry.unique_id == "btn_testbuttonwalkaaaaaaaaaaaa_press"
    ]
    assert len(entries) == 1
    assert hass.states.get("event.walk_press") is not None


async def test_removed_button_added_again(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A button whose entity was removed gets a new entity when it comes back."""
    await setup_integration(hass, mock_config_entry)
    all_buttons = buttons()
    mock_client.async_get_buttons.return_value = all_buttons[:2]
    await _refresh(hass, freezer)
    assert hass.states.get("event.walk_press") is None
    mock_client.async_get_buttons.return_value = all_buttons
    await _refresh(hass, freezer)
    assert hass.states.get("event.walk_press") is not None


@pytest.mark.parametrize(
    ("module", "name", "entity_id"),
    [
        ("event", "FluentPetButtonEvent", "event.walk_press"),
        (
            "sensor",
            "FluentPetBaseLastSeenSensor",
            "sensor.test_base_last_seen",
        ),
    ],
)
async def test_disabled_entity_not_built_again(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
    module: str,
    name: str,
    entity_id: str,
) -> None:
    """A disabled entity is skipped once, not rebuilt on every update."""
    await setup_integration(hass, mock_config_entry)
    entity_registry.async_update_entity(
        entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    platform = importlib.import_module(f"custom_components.fluentpet.{module}")
    cls = getattr(platform, name)
    with patch.object(platform, name, wraps=cls) as spy:
        assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        built = spy.call_count
        for index in range(2):
            mock_client.async_get_events.side_effect = [
                EventPage(
                    (
                        _press(
                            f"00000000-0000-4000-8000-0000000000d{index}",
                            "btn_testbuttonplayaaaaaaaaaaaa",
                        ),
                    ),
                    f"c{index}",
                )
            ]
            await _refresh(hass, freezer)
        assert spy.call_count == built
    assert hass.states.get("event.play_press").state != STATE_UNKNOWN
    assert hass.states.get(entity_id) is None


@pytest.mark.parametrize(
    ("method", "item_id", "entity_id"),
    [
        ("async_get_buttons", "btn_testbuttonwalkaaaaaaaaaaaa", "event.walk_press"),
        (
            "async_get_bases",
            "base_testbaseoneaaaaaaaaaaaaaaa",
            "sensor.test_base_last_seen",
        ),
    ],
)
async def test_disabled_entity_back_after_item_returns(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
    method: str,
    item_id: str,
    entity_id: str,
) -> None:
    """A disabled entity whose item left and came back gets its registry entry again."""
    await setup_integration(hass, mock_config_entry)
    unique_id = entity_registry.async_get(entity_id).unique_id
    platform = entity_id.split(".", 1)[0]
    entity_registry.async_update_entity(
        entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    items = getattr(mock_client, method).return_value
    getattr(mock_client, method).return_value = [
        item for item in items if item.id != item_id
    ]
    await _refresh(hass, freezer)
    assert entity_registry.async_get_entity_id(platform, DOMAIN, unique_id) is None

    getattr(mock_client, method).return_value = items
    await _refresh(hass, freezer)
    assert entity_registry.async_get_entity_id(platform, DOMAIN, unique_id) is not None


@pytest.mark.parametrize(
    ("method", "item_id", "entity_id", "module", "name"),
    [
        (
            "async_get_buttons",
            "btn_testbuttonwalkaaaaaaaaaaaa",
            "event.walk_press",
            "event",
            "FluentPetButtonEvent",
        ),
        (
            "async_get_bases",
            "base_testbaseoneaaaaaaaaaaaaaaa",
            "sensor.test_base_last_seen",
            "sensor",
            "FluentPetBaseLastSeenSensor",
        ),
    ],
)
async def test_departure_in_failed_refresh_kept(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
    method: str,
    item_id: str,
    entity_id: str,
    module: str,
    name: str,
) -> None:
    """An item gone in a refresh that fails later is removed only by a good refresh.

    No ghost entity or device is built while the failed refresh keeps the old data.
    """
    await setup_integration(hass, mock_config_entry)
    entry_id = mock_config_entry.entry_id
    device = device_registry.async_get_device_by_identifier((DOMAIN, item_id), entry_id)
    assert device is not None
    platform = importlib.import_module(f"custom_components.fluentpet.{module}")
    items = getattr(mock_client, method).return_value
    getattr(mock_client, method).return_value = [
        item for item in items if item.id != item_id
    ]
    mock_client.async_get_events.side_effect = FluentPetServerError(
        503, "service unavailable", None
    )
    with patch.object(platform, name, wraps=getattr(platform, name)) as spy:
        await _refresh(hass, freezer)
        coordinator = mock_config_entry.runtime_data.coordinator
        assert not coordinator.last_update_success
        assert spy.call_count == 0
    kept = device_registry.async_get_device_by_identifier((DOMAIN, item_id), entry_id)
    assert kept is not None
    assert kept.id == device.id
    assert entity_registry.async_get(entity_id) is not None

    mock_client.async_get_events.side_effect = empty_page
    await _refresh(hass, freezer, 121)
    assert coordinator.last_update_success
    assert (
        device_registry.async_get_device_by_identifier((DOMAIN, item_id), entry_id)
        is None
    )
    assert entity_registry.async_get(entity_id) is None


async def test_skipped_base_not_added_twice(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A Base skipped by one refresh keeps its single entity when it returns."""
    await setup_integration(hass, mock_config_entry)
    all_bases = mock_client.async_get_bases.return_value
    base_id = "base_testbaseoneaaaaaaaaaaaaaaa"

    async def _skip_base(*args: Any, **kwargs: Any) -> list[Any]:
        kwargs["on_invalid"](FluentPetParseError("'id' is missing or not a string"))
        return [base for base in all_bases if base.id != base_id]

    platform = importlib.import_module("custom_components.fluentpet.sensor")
    with patch.object(
        platform,
        "FluentPetBaseLastSeenSensor",
        wraps=platform.FluentPetBaseLastSeenSensor,
    ) as spy:
        mock_client.async_get_bases.side_effect = _skip_base
        await _refresh(hass, freezer)
        assert (
            device_registry.async_get_device_by_identifier(
                (DOMAIN, base_id), mock_config_entry.entry_id
            )
            is not None
        )
        mock_client.async_get_bases.side_effect = None
        mock_client.async_get_bases.return_value = all_bases
        await _refresh(hass, freezer)
        assert spy.call_count == 0
    assert "already exists" not in caplog.text
    entries = [
        entry
        for entry in er.async_entries_for_config_entry(
            entity_registry, mock_config_entry.entry_id
        )
        if entry.unique_id == f"{base_id}_last_seen"
    ]
    assert len(entries) == 1
    assert hass.states.get("sensor.test_base_last_seen").state == LAST_SEEN


async def test_feed_page_limit_per_refresh(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A refresh reads at most FEED_MAX_PAGES pages; the next one continues.

    The catch-up lasts until a refresh reads the feed to its end.
    """

    def _full_page(*args: Any, since: str | None = None, **kwargs: Any) -> EventPage:
        index = int((since or TAIL_CURSOR)[-2:])
        return EventPage(
            tuple(
                _press(
                    f"00000000-0000-4000-8000-{index:06d}{offset:06d}",
                    "btn_testbuttonplayaaaaaaaaaaaa",
                )
                for offset in range(100)
            ),
            f"AAAAAAAAAAtestcursor000000{index + 1:02d}",
        )

    mock_client.async_get_events.side_effect = _full_page
    with patch("custom_components.fluentpet.coordinator.FEED_MAX_PAGES", 2):
        await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.runtime_data.coordinator.last_update_success
    assert mock_client.async_get_events.await_count == 2
    assert (
        mock_config_entry.runtime_data.coordinator.cursor
        == "AAAAAAAAAAtestcursor00000015"
    )
    # The feed was not read to its end: still catching up.
    assert mock_config_entry.runtime_data.coordinator.catching_up

    # The next refresh reads a short page: its press is replayed, and the
    # catch-up ends there.
    mock_client.async_get_events.side_effect = [
        EventPage(
            (_press("00000000-0000-4000-8000-0000000000d2", OUTSIDE_ID),),
            "AAAAAAAAAAtestcursor00000016",
        ),
    ]
    await _refresh(hass, freezer)
    assert hass.states.get(PRESS_ENTITY).attributes["replayed"] is True
    assert not mock_config_entry.runtime_data.coordinator.catching_up


def _old_press(event_id: str) -> Event:
    """Return a press of the Outside button that happened an hour ago."""
    data = load_json_fixture("events/press_created.json")
    data["id"] = event_id
    data["payload"]["press"]["occurred_at"] = (
        dt_util.utcnow() - timedelta(hours=1)
    ).isoformat()
    return Event.from_dict(data)


async def test_press_read_at_startup_replayed(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A feed press read at startup is replayed; catch-up ends at the feed end."""
    mock_client.async_get_events.side_effect = [
        EventPage((_event("press_created"),), "AAAAAAAAAAtestcursor00000014")
    ]
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(PRESS_ENTITY).attributes["replayed"] is True
    assert not mock_config_entry.runtime_data.coordinator.catching_up


async def test_press_read_in_catch_up_after_failures_replayed(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """After failed refreshes, feed presses are replayed until the feed end."""
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    mock_client.async_get_bases.side_effect = FluentPetConnectionError("boom")
    await _refresh(hass, freezer)
    await _refresh(hass, freezer)
    assert coordinator.catching_up

    mock_client.async_get_bases.side_effect = None
    mock_client.async_get_events.side_effect = [
        EventPage((_press("00000000-0000-4000-8000-0000000000c1", OUTSIDE_ID),), "c1"),
        EventPage((_press("00000000-0000-4000-8000-0000000000c2", OUTSIDE_ID),), "c2"),
    ]
    await _refresh(hass, freezer)
    assert hass.states.get(PRESS_ENTITY).attributes["replayed"] is True
    assert not coordinator.catching_up
    await _refresh(hass, freezer)
    assert hass.states.get(PRESS_ENTITY).attributes["replayed"] is False


async def test_missed_press_in_polling_mode_live(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A press an hour old read by a polling refresh is live, not replayed."""
    await setup_integration(hass, mock_config_entry)
    mock_client.async_get_events.side_effect = [
        EventPage((_old_press("00000000-0000-4000-8000-0000000000c3"),), "c1")
    ]
    await _refresh(hass, freezer)
    state = hass.states.get(PRESS_ENTITY)
    assert state.state != STATE_UNKNOWN
    assert state.attributes["replayed"] is False
