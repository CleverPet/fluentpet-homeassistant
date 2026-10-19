"""Tests for the FluentPet Connect button events."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.fluentpet.const import signal_press
from custom_components.fluentpet.fluentpet_api import Event, PressCreatedData
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .common import load_json_fixture, setup_integration


async def test_entities(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """One event entity per button."""
    with patch("custom_components.fluentpet.PLATFORMS", [Platform.EVENT]):
        await setup_integration(hass, mock_config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


async def test_press_without_pusher(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A press without a learner or teacher, or interaction, has empty attributes."""
    await setup_integration(hass, mock_config_entry)
    data = load_json_fixture("events/press_created.json")
    data["payload"]["pusher"] = None
    data["payload"]["interaction"] = None
    event = Event.from_dict(data)
    assert isinstance(event.data, PressCreatedData)
    async_dispatcher_send(
        hass,
        signal_press(mock_config_entry.entry_id, "btn_testbuttonoutsideaaaaaaaaa"),
        event.id,
        event.data,
    )
    state = hass.states.get("event.outside_press")
    assert state.attributes["pressed_by"] is None
    assert state.attributes["interaction_id"] is None


async def test_replayed_attribute(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A press fired more than 2 minutes after it happened is marked replayed."""
    await setup_integration(hass, mock_config_entry)
    signal = signal_press(mock_config_entry.entry_id, "btn_testbuttonoutsideaaaaaaaaa")
    for age, replayed in (
        (timedelta(seconds=5), False),
        (timedelta(minutes=2, seconds=1), True),
    ):
        data = load_json_fixture("events/press_created.json")
        data["payload"]["press"]["occurred_at"] = (dt_util.utcnow() - age).isoformat()
        event = Event.from_dict(data)
        assert isinstance(event.data, PressCreatedData)
        async_dispatcher_send(hass, signal, event.id, event.data)
        state = hass.states.get("event.outside_press")
        assert state.attributes["replayed"] is replayed


async def test_press_by_teacher(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A press by a teacher names the teacher with type teacher."""
    await setup_integration(hass, mock_config_entry)
    data = load_json_fixture("events/press_created.json")
    data["payload"]["pusher"] = {
        "id": "psh_testteacheraaaaaaaaaaaaaaa",
        "name": "Test Teacher",
        "type": "teacher",
    }
    event = Event.from_dict(data)
    assert isinstance(event.data, PressCreatedData)
    async_dispatcher_send(
        hass,
        signal_press(mock_config_entry.entry_id, "btn_testbuttonoutsideaaaaaaaaa"),
        event.id,
        event.data,
    )
    state = hass.states.get("event.outside_press")
    assert state.attributes["pressed_by"] == {"name": "Test Teacher", "type": "teacher"}
