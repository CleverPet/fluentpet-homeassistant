"""Tests for the FluentPet Connect last interaction and last seen sensors."""

from dataclasses import replace
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.fluentpet.const import signal_interaction
from custom_components.fluentpet.fluentpet_api import (
    Event,
    FluentPetConnectionError,
    InteractionUpdatedData,
)
from custom_components.fluentpet.sensor import FluentPetBaseLastSeenSensor
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .common import load_json_fixture, setup_integration

SENSOR_ENTITY = "sensor.test_household_last_interaction"


def _interaction(
    interaction_id: str = "int_testinteractionaaaaaaaaaaa",
    occurred_at: str = "2026-10-17T11:59:58.412Z",
    words: list[str | None] | None = None,
    press_count: int | None = None,
) -> InteractionUpdatedData:
    """Return the payload of an interaction.updated event."""
    data = load_json_fixture("events/interaction_updated.json")
    interaction = data["payload"]["interaction"]
    interaction["id"] = interaction_id
    interaction["occurred_at"] = occurred_at
    if words is not None:
        interaction["presses"] = [
            {"button_id": None, "name": word, "normalized_word": word} for word in words
        ]
        interaction["press_count"] = len(words)
    if press_count is not None:
        interaction["press_count"] = press_count
    event = Event.from_dict(data)
    assert isinstance(event.data, InteractionUpdatedData)
    return event.data


async def test_entities(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """The sensor platform creates one sensor per household."""
    with patch("custom_components.fluentpet.PLATFORMS", [Platform.SENSOR]):
        await setup_integration(hass, mock_config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.fixture
async def loaded_entry(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Set up the integration."""
    await setup_integration(hass, mock_config_entry)
    return mock_config_entry


def _send(
    hass: HomeAssistant, entry: MockConfigEntry, data: InteractionUpdatedData
) -> None:
    """Send an interaction update."""
    async_dispatcher_send(
        hass,
        signal_interaction(entry.entry_id),
        "00000000-0000-4000-8000-0000000000e1",
        data,
    )


async def test_state_and_attributes(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The state is the words of the interaction; deleted buttons are skipped."""
    assert hass.states.get(SENSOR_ENTITY).state == STATE_UNKNOWN
    _send(hass, loaded_entry, _interaction())
    state = hass.states.get(SENSOR_ENTITY)
    assert state.state == "Outside Play"
    assert state.attributes["sequence"] == ["Outside", "Play"]
    assert state.attributes["press_count"] == 3
    assert state.attributes["occurred_at"] == "2026-10-17T11:59:58.412000+00:00"
    assert state.attributes["pressed_by"] == {
        "name": "Test Learner",
        "type": "learner",
    }
    assert state.attributes["interaction_id"] == "int_testinteractionaaaaaaaaaaa"


async def test_truncated(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    """A long interaction is cut at 255 characters; the sequence is complete."""
    words = ["walk"] * 100
    _send(hass, loaded_entry, _interaction(words=list(words)))
    state = hass.states.get(SENSOR_ENTITY)
    assert len(state.state) == 255
    assert state.attributes["sequence"] == words


async def test_older_updates_ignored(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A smaller press count of the same interaction, or an older one, is ignored."""
    _send(hass, loaded_entry, _interaction(words=["outside", "play", "walk"]))
    _send(hass, loaded_entry, _interaction(words=["outside"]))
    assert hass.states.get(SENSOR_ENTITY).state == "outside play walk"

    _send(
        hass,
        loaded_entry,
        _interaction(
            interaction_id="int_older",
            occurred_at="2026-10-17T10:00:00Z",
            words=["eat"],
        ),
    )
    assert hass.states.get(SENSOR_ENTITY).state == "outside play walk"

    _send(
        hass,
        loaded_entry,
        _interaction(
            interaction_id="int_newer",
            occurred_at="2026-10-17T13:00:00Z",
            words=["eat"],
        ),
    )
    assert hass.states.get(SENSOR_ENTITY).state == "eat"


async def test_without_pusher_and_time(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """An interaction without a learner or teacher, or time, is shown."""
    data = load_json_fixture("events/interaction_updated.json")
    data["payload"]["pusher"] = None
    data["payload"]["interaction"]["occurred_at"] = None
    event = Event.from_dict(data)
    assert isinstance(event.data, InteractionUpdatedData)
    _send(hass, loaded_entry, event.data)
    state = hass.states.get(SENSOR_ENTITY)
    assert state.attributes["pressed_by"] is None
    assert state.attributes["occurred_at"] is None


async def test_restore(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The last interaction survives a restart and older updates stay ignored."""
    attributes: dict[str, Any] = {
        "sequence": ["outside", "play", "walk"],
        "press_count": 3,
        "occurred_at": "2026-10-17T11:59:58.412000+00:00",
        "pressed_by": {"name": "Test Learner", "type": "learner"},
        "interaction_id": "int_testinteractionaaaaaaaaaaa",
    }
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(SENSOR_ENTITY, "outside play walk", attributes),
                {
                    "native_value": "outside play walk",
                    "native_unit_of_measurement": None,
                },
            )
        ],
    )
    await setup_integration(hass, mock_config_entry)
    state = hass.states.get(SENSOR_ENTITY)
    assert state.state == "outside play walk"
    assert state.attributes == {**state.attributes, **attributes}

    _send(hass, mock_config_entry, _interaction(words=["outside"]))
    assert hass.states.get(SENSOR_ENTITY).state == "outside play walk"


async def test_interaction_by_teacher(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """An interaction by a teacher names the teacher with type teacher."""
    data = load_json_fixture("events/interaction_updated.json")
    data["payload"]["pusher"] = {
        "id": "psh_testteacheraaaaaaaaaaaaaaa",
        "name": "Test Teacher",
        "type": "teacher",
    }
    event = Event.from_dict(data)
    assert isinstance(event.data, InteractionUpdatedData)
    _send(hass, loaded_entry, event.data)
    state = hass.states.get(SENSOR_ENTITY)
    assert state.attributes["pressed_by"] == {"name": "Test Teacher", "type": "teacher"}


PLUGGED_IN = "sensor.test_base_last_seen"
ON_BATTERY = "sensor.base_testserial0002_last_seen"


async def _refresh(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Move time past the refresh interval."""
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_last_seen(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Each Base has a last seen timestamp with its power mode and battery level."""
    await setup_integration(hass, mock_config_entry)
    plugged_in = hass.states.get(PLUGGED_IN)
    assert plugged_in.state == "2026-10-17T12:00:00+00:00"
    assert plugged_in.attributes["device_class"] == "timestamp"
    assert plugged_in.attributes["power_mode"] == "plugged_in"
    assert plugged_in.attributes["battery_level"] == 100
    on_battery = hass.states.get(ON_BATTERY)
    assert on_battery.state == "2026-10-17T11:45:00+00:00"
    assert on_battery.attributes["power_mode"] == "battery"
    assert on_battery.attributes["battery_level"] == 62
    entry = entity_registry.async_get(PLUGGED_IN)
    assert entry.unique_id == "base_testbaseoneaaaaaaaaaaaaaaa_last_seen"
    assert entry.entity_category is None

    first, second = mock_client.async_get_bases.return_value
    mock_client.async_get_bases.return_value = [
        replace(first, last_online_at=None, power_mode=None, battery_level=None),
        replace(second, battery_level=61),
    ]
    await _refresh(hass, freezer)
    plugged_in = hass.states.get(PLUGGED_IN)
    assert plugged_in.state == STATE_UNKNOWN
    assert plugged_in.attributes["power_mode"] is None
    assert plugged_in.attributes["battery_level"] is None
    assert hass.states.get(ON_BATTERY).attributes["battery_level"] == 61


async def test_last_seen_unavailable_while_refresh_fails(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The last seen sensor follows the refresh, not the webhook."""
    await setup_integration(hass, mock_config_entry)
    mock_client.async_get_bases.side_effect = FluentPetConnectionError("boom")
    await _refresh(hass, freezer)
    assert hass.states.get(PLUGGED_IN).state == STATE_UNAVAILABLE
    mock_client.async_get_bases.side_effect = None
    await _refresh(hass, freezer)
    assert hass.states.get(PLUGGED_IN).state == "2026-10-17T12:00:00+00:00"


async def test_last_seen_attributes_recorded(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The power mode and battery level are reported and kept in the recorder."""
    await setup_integration(hass, mock_config_entry)
    attributes = hass.states.get(PLUGGED_IN).attributes
    assert {"power_mode", "battery_level"} <= set(attributes)
    unrecorded = FluentPetBaseLastSeenSensor._unrecorded_attributes  # noqa: SLF001
    assert not unrecorded & {"power_mode", "battery_level"}
