"""Tests for the FluentPet Connect plugged in binary sensor."""

from dataclasses import replace
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.fluentpet.const import DOMAIN
from custom_components.fluentpet.fluentpet_api import Event, FluentPetConnectionError
from homeassistant.const import (
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .common import load_json_fixture, setup_integration

PLUGGED_IN = "binary_sensor.test_base_plugged_in"
ON_BATTERY = "binary_sensor.base_testserial0002_plugged_in"
ON_BATTERY_BATTERY = "sensor.base_testserial0002_battery"


async def _refresh(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Move time past the refresh interval."""
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_entities(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """The binary sensor platform creates a plugged in sensor per Base."""
    with patch("custom_components.fluentpet.PLATFORMS", [Platform.BINARY_SENSOR]):
        await setup_integration(hass, mock_config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


async def test_power_modes(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A Base plugged in is on, a Base on battery off; buttons have no sensor."""
    await setup_integration(hass, mock_config_entry)
    plugged_in = hass.states.get(PLUGGED_IN)
    assert plugged_in.state == STATE_ON
    assert plugged_in.attributes["device_class"] == "plug"
    assert plugged_in.attributes["friendly_name"] == "Test Base Plugged in"
    assert hass.states.get(ON_BATTERY).state == STATE_OFF
    entry = entity_registry.async_get(PLUGGED_IN)
    assert entry.unique_id == "base_testbaseoneaaaaaaaaaaaaaaa_plugged_in"
    assert entry.entity_category is None

    binary_sensors = [
        entry
        for entry in er.async_entries_for_config_entry(
            entity_registry, mock_config_entry.entry_id
        )
        if entry.domain == Platform.BINARY_SENSOR
    ]
    assert {entry.unique_id for entry in binary_sensors} == {
        "base_testbaseoneaaaaaaaaaaaaaaa_plugged_in",
        "base_testbasetwoaaaaaaaaaaaaaaa_plugged_in",
    }
    for button_id in (
        "btn_testbuttonoutsideaaaaaaaaa",
        "btn_testbuttonplayaaaaaaaaaaaa",
        "btn_testbuttonwalkaaaaaaaaaaaa",
    ):
        device = device_registry.async_get_device_by_identifier(
            (DOMAIN, button_id), mock_config_entry.entry_id
        )
        assert device is not None
        assert not [
            entry
            for entry in er.async_entries_for_device(entity_registry, device.id)
            if entry.domain == Platform.BINARY_SENSOR
        ]


async def test_transition_by_refresh(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Plugging a Base in and out shows at the next refresh.

    Plugged in, FluentPet reports no level: the battery sensor is unknown but
    stays available.
    """
    await setup_integration(hass, mock_config_entry)
    first, second = mock_client.async_get_bases.return_value
    assert hass.states.get(ON_BATTERY).state == STATE_OFF
    assert hass.states.get(ON_BATTERY_BATTERY).state == "50"

    mock_client.async_get_bases.return_value = [
        first,
        replace(second, power_mode="plugged_in", battery_level=None),
    ]
    await _refresh(hass, freezer)
    assert hass.states.get(ON_BATTERY).state == STATE_ON
    assert hass.states.get(ON_BATTERY_BATTERY).state == STATE_UNKNOWN

    mock_client.async_get_bases.return_value = [
        first,
        replace(second, power_mode="battery", battery_level=25),
    ]
    await _refresh(hass, freezer)
    assert hass.states.get(ON_BATTERY).state == STATE_OFF
    assert hass.states.get(ON_BATTERY_BATTERY).state == "25"


@pytest.mark.parametrize("power_mode", [None, "unrecognized"])
async def test_unknown_power_mode(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    power_mode: str | None,
) -> None:
    """A Base without a known power mode is unknown; its level still shows."""
    first, second = mock_client.async_get_bases.return_value
    mock_client.async_get_bases.return_value = [
        first,
        replace(second, power_mode=power_mode, battery_level=75),
    ]
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(ON_BATTERY).state == STATE_UNKNOWN
    assert hass.states.get(ON_BATTERY_BATTERY).state == "75"


async def test_unavailable_while_refresh_fails(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The plugged in sensor follows the refresh, not the webhook."""
    await setup_integration(hass, mock_config_entry)
    mock_client.async_get_bases.side_effect = FluentPetConnectionError("boom")
    await _refresh(hass, freezer)
    assert hass.states.get(PLUGGED_IN).state == STATE_UNAVAILABLE
    mock_client.async_get_bases.side_effect = None
    await _refresh(hass, freezer)
    assert hass.states.get(PLUGGED_IN).state == STATE_ON


async def test_removed_base(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A Base that leaves the household loses its sensor; it returns with it."""
    await setup_integration(hass, mock_config_entry)
    first, second = mock_client.async_get_bases.return_value
    mock_client.async_get_bases.return_value = [first]
    await _refresh(hass, freezer)
    assert hass.states.get(ON_BATTERY) is None
    mock_client.async_get_bases.return_value = [first, second]
    await _refresh(hass, freezer)
    assert hass.states.get(ON_BATTERY).state == STATE_OFF


def _base_updated(fixture: str, reason: str) -> Event:
    """Return a base.updated event of a fixture with the given reason."""
    data: dict[str, Any] = load_json_fixture(f"events/{fixture}.json")
    data["payload"]["reason"] = reason
    return Event.from_dict(data)


@pytest.mark.parametrize("reason", ["battery_reported", "power_mode_reported"])
async def test_processed_base_updated_refreshes(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    reason: str,
) -> None:
    """A processed base.updated updates the plugged in and battery sensors.

    This is the path of a pushed (webhook) event: processing it requests a
    refresh right away. The webhook request itself is covered in test_webhook.
    """
    await setup_integration(hass, mock_config_entry)
    first, second = mock_client.async_get_bases.return_value
    processor = mock_config_entry.runtime_data.coordinator.processor

    mock_client.async_get_bases.return_value = [
        first,
        replace(second, power_mode="plugged_in", battery_level=None),
    ]
    processor.async_process(_base_updated("base_plugged_in", reason))
    await hass.async_block_till_done()
    assert hass.states.get(ON_BATTERY).state == STATE_ON
    assert hass.states.get(ON_BATTERY_BATTERY).state == STATE_UNKNOWN

    # Past the cooldown of the requested refresh.
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    mock_client.async_get_bases.return_value = [first, second]
    processor.async_process(_base_updated("base_unplugged", reason))
    await hass.async_block_till_done()
    assert hass.states.get(ON_BATTERY).state == STATE_OFF
    assert hass.states.get(ON_BATTERY_BATTERY).state == "50"
