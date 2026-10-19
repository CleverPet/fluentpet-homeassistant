"""Sensors of the FluentPet Connect integration.

The last interaction of the household, and when each Base was last seen.
"""

from datetime import datetime
from functools import partial
from typing import Any, override

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import STATE_MAX_LENGTH, signal_interaction
from .coordinator import FluentPetConfigEntry, FluentPetCoordinator
from .entity import FluentPetCoordinatorEntity, FluentPetPushEntity, device_info
from .fluentpet_api import InteractionUpdatedData

PARALLEL_UPDATES = 0

ATTR_SEQUENCE = "sequence"
ATTR_PRESS_COUNT = "press_count"
ATTR_OCCURRED_AT = "occurred_at"
ATTR_PRESSED_BY = "pressed_by"
ATTR_INTERACTION_ID = "interaction_id"
ATTR_POWER_MODE = "power_mode"
ATTR_BATTERY_LEVEL = "battery_level"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FluentPetConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the last interaction sensor and a last seen sensor per Base.

    Bases added later get their sensor at the refresh that returns them.
    """
    coordinator = entry.runtime_data.coordinator
    async_add_entities([FluentPetLastInteractionSensor(coordinator)])
    known: set[str] = set()

    @callback
    def _async_add_new() -> None:
        # Forget Bases that left the household, so their sensor is built again
        # when they return; a Base kept through an incomplete refresh, or whose
        # sensor is disabled, stays known and is not built again on every update.
        known.intersection_update(coordinator.known_base_ids)
        new = set(coordinator.data.bases) - known
        if not new:
            return
        known.update(new)
        async_add_entities(
            FluentPetBaseLastSeenSensor(coordinator, base_id) for base_id in new
        )

    _async_add_new()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new))


def interaction_words(data: InteractionUpdatedData) -> list[str]:
    """Return the words of an interaction in press order."""
    return [
        word
        for press in data.interaction.presses
        if (word := press.name or press.normalized_word)
    ]


class FluentPetLastInteractionSensor(FluentPetPushEntity, RestoreSensor):
    """The words of the latest interaction of the household.

    Unavailable only while refreshes fail and the webhook is not active; an
    interaction that arrives then is shown once the sensor is available again.
    """

    _attr_translation_key = "last_interaction"

    def __init__(self, coordinator: FluentPetCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        household_id = coordinator.household_id
        self._entry_id = coordinator.config_entry.entry_id
        self._processor = coordinator.processor
        self._attr_unique_id = f"{household_id}_last_interaction"
        self._attr_device_info = device_info(household_id)
        self._attr_extra_state_attributes: dict[str, Any] = {}
        self._interaction_id: str | None = None
        self._press_count = 0
        self._occurred_at: datetime | None = None

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the last interaction and listen for updates."""
        await super().async_added_to_hass()
        if (sensor_data := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = sensor_data.native_value
        if (state := await self.async_get_last_state()) is not None:
            attributes = state.attributes
            self._attr_extra_state_attributes = {
                key: attributes.get(key)
                for key in (
                    ATTR_SEQUENCE,
                    ATTR_PRESS_COUNT,
                    ATTR_OCCURRED_AT,
                    ATTR_PRESSED_BY,
                    ATTR_INTERACTION_ID,
                )
            }
            self._interaction_id = attributes.get(ATTR_INTERACTION_ID)
            self._press_count = attributes.get(ATTR_PRESS_COUNT) or 0
            if isinstance(occurred_at := attributes.get(ATTR_OCCURRED_AT), str):
                self._occurred_at = dt_util.parse_datetime(occurred_at)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_interaction(self._entry_id), self._async_receive
            )
        )
        if (last := self._processor.last_interaction) is not None:
            self._async_receive(*last)

    @callback
    def _async_receive(self, event_id: str, data: InteractionUpdatedData) -> None:
        """Show an interaction, or hold it while the sensor is unavailable."""
        self._async_apply(event_id, partial(self._async_update, data))

    @callback
    def _async_update(self, data: InteractionUpdatedData) -> None:
        """Show an interaction unless a newer one, or a fuller version, is shown."""
        interaction = data.interaction
        if interaction.id == self._interaction_id:
            if interaction.press_count < self._press_count:
                return
        elif (
            self._occurred_at is not None
            and interaction.occurred_at is not None
            and interaction.occurred_at < self._occurred_at
        ):
            return
        words = interaction_words(data)
        self._interaction_id = interaction.id
        self._press_count = interaction.press_count
        self._occurred_at = interaction.occurred_at
        self._attr_native_value = " ".join(words)[:STATE_MAX_LENGTH]
        self._attr_extra_state_attributes = {
            ATTR_SEQUENCE: words,
            ATTR_PRESS_COUNT: interaction.press_count,
            ATTR_OCCURRED_AT: interaction.occurred_at.isoformat()
            if interaction.occurred_at
            else None,
            ATTR_PRESSED_BY: {"name": data.pusher.name, "type": data.pusher.type}
            if data.pusher
            else None,
            ATTR_INTERACTION_ID: interaction.id,
        }
        self.async_write_ha_state()


class FluentPetBaseLastSeenSensor(FluentPetCoordinatorEntity, SensorEntity):
    """When FluentPet last heard from a Base, with its power mode and battery level.

    The power mode and battery level are recorded with the state: they change
    rarely (plugging in or out) or slowly (the charge), and their history is what
    shows when a Base ran on battery.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "last_seen"

    def __init__(self, coordinator: FluentPetCoordinator, base_id: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.base_id = base_id
        self._attr_unique_id = f"{base_id}_last_seen"
        self._attr_device_info = device_info(base_id)

    @property
    @override
    def available(self) -> bool:
        """Return whether the last refresh succeeded and returned the Base."""
        return super().available and self.base_id in self.coordinator.data.bases

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return when FluentPet last heard from the Base."""
        return self.coordinator.data.bases[self.base_id].last_online_at

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the power mode and battery level of the Base."""
        base = self.coordinator.data.bases[self.base_id]
        return {
            ATTR_POWER_MODE: base.power_mode,
            ATTR_BATTERY_LEVEL: base.battery_level,
        }
