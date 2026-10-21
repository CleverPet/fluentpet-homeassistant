"""Tests for the FluentPet Connect last interaction, last seen and battery sensors."""

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
    FluentPetParseError,
    InteractionUpdatedData,
)
from homeassistant.const import (
    PERCENTAGE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
    Platform,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .common import load_json_fixture, setup_integration
from .conftest import buttons

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
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_last_seen(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Each Base has a last seen timestamp without attributes of its own.

    The power mode is the plugged in binary sensor, not an attribute.
    """
    await setup_integration(hass, mock_config_entry)
    plugged_in = hass.states.get(PLUGGED_IN)
    assert plugged_in.state == "2026-10-17T12:00:00+00:00"
    assert plugged_in.attributes["device_class"] == "timestamp"
    assert "power_mode" not in plugged_in.attributes
    assert "battery_level" not in plugged_in.attributes
    on_battery = hass.states.get(ON_BATTERY)
    assert on_battery.state == "2026-10-17T11:45:00+00:00"
    assert "power_mode" not in on_battery.attributes
    assert "battery_level" not in on_battery.attributes
    entry = entity_registry.async_get(PLUGGED_IN)
    assert entry.unique_id == "base_testbaseoneaaaaaaaaaaaaaaa_last_seen"
    assert entry.entity_category is None

    first, second = mock_client.async_get_bases.return_value
    mock_client.async_get_bases.return_value = [
        replace(first, last_online_at=None, power_mode=None),
        replace(second, power_mode="plugged_in"),
    ]
    await _refresh(hass, freezer)
    plugged_in = hass.states.get(PLUGGED_IN)
    assert plugged_in.state == STATE_UNKNOWN
    assert "power_mode" not in plugged_in.attributes
    assert "power_mode" not in hass.states.get(ON_BATTERY).attributes


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


BASE_BATTERY = "sensor.test_base_battery"
OTHER_BASE_BATTERY = "sensor.base_testserial0002_battery"
BUTTON_BATTERY = "sensor.outside_battery"
UNREPORTED_BUTTON_BATTERY = "sensor.play_battery"


async def test_base_battery(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Each Base has a diagnostic battery sensor without attributes of its own.

    It is unknown, not unavailable, while the Base runs plugged in or reports no
    level.
    """
    await setup_integration(hass, mock_config_entry)
    # Plugged in.
    state = hass.states.get(BASE_BATTERY)
    assert state.state == STATE_UNKNOWN
    assert state.attributes["device_class"] == "battery"
    assert state.attributes["unit_of_measurement"] == PERCENTAGE
    assert state.attributes["state_class"] == "measurement"
    assert "power_mode" not in state.attributes
    assert state.attributes["friendly_name"] == "Test Base Battery"
    assert hass.states.get(OTHER_BASE_BATTERY).state == "50"
    entry = entity_registry.async_get(BASE_BATTERY)
    assert entry.unique_id == "base_testbaseoneaaaaaaaaaaaaaaa_battery"
    assert entry.entity_category is EntityCategory.DIAGNOSTIC

    first, second = mock_client.async_get_bases.return_value
    mock_client.async_get_bases.return_value = [
        replace(first, power_mode="battery", battery_level=None),
        replace(second, battery_level=25),
    ]
    await _refresh(hass, freezer)
    assert hass.states.get(BASE_BATTERY).state == STATE_UNKNOWN
    assert hass.states.get(OTHER_BASE_BATTERY).state == "25"


async def test_button_battery(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Each linked button has a battery sensor, unavailable without a level."""
    await setup_integration(hass, mock_config_entry)
    state = hass.states.get(BUTTON_BATTERY)
    assert state.state == "75"
    assert state.attributes["device_class"] == "battery"
    assert state.attributes["unit_of_measurement"] == PERCENTAGE
    assert "power_mode" not in state.attributes
    entry = entity_registry.async_get(BUTTON_BATTERY)
    assert entry.unique_id == "btn_testbuttonoutsideaaaaaaaaa_battery"
    assert entry.entity_category is EntityCategory.DIAGNOSTIC
    # Not pressed since FluentPet started reporting button levels.
    assert hass.states.get(UNREPORTED_BUTTON_BATTERY).state == STATE_UNAVAILABLE
    # An unlinked button has no battery sensor.
    assert hass.states.get("sensor.walk_battery") is None

    outside, play, walk = buttons()
    mock_client.async_get_buttons.return_value = [
        outside,
        replace(play, battery_level=25),
        replace(walk, base_id="base_testbaseoneaaaaaaaaaaaaaaa", battery_level=100),
    ]
    await _refresh(hass, freezer)
    assert hass.states.get(UNREPORTED_BUTTON_BATTERY).state == "25"
    assert hass.states.get("sensor.walk_battery").state == "100"


@pytest.mark.parametrize(
    ("fixture", "entity_id", "expected"),
    [
        ("base_updated", BASE_BATTERY, "25"),
        ("button_updated", BUTTON_BATTERY, "25"),
    ],
)
async def test_processed_battery_event_refreshes(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    fixture: str,
    entity_id: str,
    expected: str,
) -> None:
    """A processed base.updated or button.updated updates the battery sensor.

    This is the path of a pushed (webhook) event: processing it requests a
    refresh right away, without waiting for the refresh interval. The feed path
    is covered in test_push.
    """
    await setup_integration(hass, mock_config_entry)
    first, second = mock_client.async_get_bases.return_value
    mock_client.async_get_bases.return_value = [
        replace(first, battery_level=25, power_mode="battery"),
        second,
    ]
    outside, *rest = mock_client.async_get_buttons.return_value
    mock_client.async_get_buttons.return_value = [
        replace(outside, battery_level=25),
        *rest,
    ]
    event = Event.from_dict(load_json_fixture(f"events/{fixture}.json"))
    mock_config_entry.runtime_data.coordinator.processor.async_process(event)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == expected


async def test_base_battery_power_modes(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The Base battery sensor follows the power mode across refreshes.

    Plugged in it is unknown but available, also if FluentPet still reports a
    level; on battery, or without a known power mode, it shows the level.
    """
    await setup_integration(hass, mock_config_entry)
    _, second = mock_client.async_get_bases.return_value
    assert hass.states.get(OTHER_BASE_BATTERY).state == "50"

    for power_mode, battery_level, expected in (
        ("plugged_in", None, STATE_UNKNOWN),
        ("plugged_in", 50, STATE_UNKNOWN),
        ("battery", 50, "50"),
        (None, 75, "75"),
        ("unrecognized", 25, "25"),
    ):
        mock_client.async_get_bases.return_value = [
            replace(second, power_mode=power_mode, battery_level=battery_level)
        ]
        await _refresh(hass, freezer)
        assert hass.states.get(OTHER_BASE_BATTERY).state == expected, power_mode


async def test_battery_unavailable_while_refresh_fails(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The battery sensors follow the refresh, not the webhook."""
    await setup_integration(hass, mock_config_entry)
    mock_client.async_get_bases.side_effect = FluentPetConnectionError("boom")
    await _refresh(hass, freezer)
    assert hass.states.get(BASE_BATTERY).state == STATE_UNAVAILABLE
    assert hass.states.get(OTHER_BASE_BATTERY).state == STATE_UNAVAILABLE
    assert hass.states.get(BUTTON_BATTERY).state == STATE_UNAVAILABLE
    mock_client.async_get_bases.side_effect = None
    await _refresh(hass, freezer)
    assert hass.states.get(BASE_BATTERY).state == STATE_UNKNOWN
    assert hass.states.get(OTHER_BASE_BATTERY).state == "50"
    assert hass.states.get(BUTTON_BATTERY).state == "75"


async def test_unlinked_button_loses_battery_sensor(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Unlinking a button removes its battery sensor; linking it again restores it."""
    await setup_integration(hass, mock_config_entry)
    assert entity_registry.async_get(BUTTON_BATTERY) is not None
    outside, *rest = buttons()

    mock_client.async_get_buttons.return_value = [
        replace(outside, base_id=None, battery_level=None),
        *rest,
    ]
    await _refresh(hass, freezer)
    assert entity_registry.async_get(BUTTON_BATTERY) is None
    assert hass.states.get(BUTTON_BATTERY) is None
    # The button and its press entity stay.
    assert hass.states.get("event.outside_press") is not None

    mock_client.async_get_buttons.return_value = [outside, *rest]
    await _refresh(hass, freezer)
    assert entity_registry.async_get(BUTTON_BATTERY) is not None
    assert hass.states.get(BUTTON_BATTERY).state == "75"


async def test_disabled_button_battery_sensor_relinked(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A battery sensor the user disabled stays disabled after unlink and relink."""
    await setup_integration(hass, mock_config_entry)
    entity_registry.async_update_entity(
        BUTTON_BATTERY, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    outside, *rest = buttons()

    mock_client.async_get_buttons.return_value = [
        replace(outside, base_id=None, battery_level=None),
        *rest,
    ]
    await _refresh(hass, freezer)
    assert entity_registry.async_get(BUTTON_BATTERY) is None

    mock_client.async_get_buttons.return_value = [outside, *rest]
    await _refresh(hass, freezer)
    entry = entity_registry.async_get(BUTTON_BATTERY)
    assert entry is not None
    assert entry.unique_id == "btn_testbuttonoutsideaaaaaaaaa_battery"
    assert entry.disabled_by is er.RegistryEntryDisabler.USER
    assert hass.states.get(BUTTON_BATTERY) is None


async def test_removed_button_battery_sensor_rebuilt(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A button that leaves the household and returns gets its sensor again."""
    await setup_integration(hass, mock_config_entry)
    outside, *rest = buttons()
    mock_client.async_get_buttons.return_value = rest
    await _refresh(hass, freezer)
    assert entity_registry.async_get(BUTTON_BATTERY) is None
    assert hass.states.get(BUTTON_BATTERY) is None

    mock_client.async_get_buttons.return_value = [outside, *rest]
    await _refresh(hass, freezer)
    assert entity_registry.async_get(BUTTON_BATTERY) is not None
    assert hass.states.get(BUTTON_BATTERY).state == "75"


async def test_battery_sensor_incomplete_refresh(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A Base or button skipped as malformed leaves its sensor unavailable."""
    await setup_integration(hass, mock_config_entry)
    _, second = mock_client.async_get_bases.return_value
    outside, *rest = buttons()

    def _skipping(result: list[Any]) -> Any:
        def _get(household_id: str, *, on_invalid: Any = None) -> list[Any]:
            on_invalid(FluentPetParseError("malformed"))
            return result

        return _get

    mock_client.async_get_bases.side_effect = _skipping([second])
    mock_client.async_get_buttons.side_effect = _skipping(rest)
    await _refresh(hass, freezer)
    assert hass.states.get(BASE_BATTERY).state == STATE_UNAVAILABLE
    assert hass.states.get(BUTTON_BATTERY).state == STATE_UNAVAILABLE
    assert entity_registry.async_get(BASE_BATTERY) is not None
    assert entity_registry.async_get(BUTTON_BATTERY) is not None
    assert "Traceback" not in caplog.text
    assert "Error" not in caplog.text

    mock_client.async_get_bases.side_effect = None
    mock_client.async_get_buttons.side_effect = None
    mock_client.async_get_buttons.return_value = [outside, *rest]
    await _refresh(hass, freezer)
    assert hass.states.get(BASE_BATTERY).state == STATE_UNKNOWN
    assert hass.states.get(BUTTON_BATTERY).state == "75"
