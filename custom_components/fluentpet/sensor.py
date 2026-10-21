"""Sensors of the FluentPet Connect integration.

The last interaction of the household, when each Base was last seen, and the
battery level of each Base and linked button.
"""

from datetime import datetime
from functools import partial
from typing import Any, override

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, POWER_MODE_PLUGGED_IN, STATE_MAX_LENGTH, signal_interaction
from .coordinator import FluentPetConfigEntry, FluentPetCoordinator
from .entity import FluentPetCoordinatorEntity, FluentPetPushEntity, device_info
from .fluentpet_api import Base, Button, InteractionUpdatedData

PARALLEL_UPDATES = 0

ATTR_SEQUENCE = "sequence"
ATTR_PRESS_COUNT = "press_count"
ATTR_OCCURRED_AT = "occurred_at"
ATTR_PRESSED_BY = "pressed_by"
ATTR_INTERACTION_ID = "interaction_id"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FluentPetConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the last interaction sensor and the sensors of Bases and buttons.

    Each Base gets a last seen and a battery sensor, each button linked to a Base
    a battery sensor. Bases added later, and buttons linked later, get theirs at
    the refresh that returns them; a button that is unlinked loses its battery
    sensor.
    """
    coordinator = entry.runtime_data.coordinator
    async_add_entities([FluentPetLastInteractionSensor(coordinator)])
    known_bases: set[str] = set()
    known_buttons: set[str] = set()
    entity_registry = er.async_get(hass)

    @callback
    def _async_add_new() -> None:
        # Forget ids that left the household, so their sensors are built again
        # when they return; an id kept through an incomplete refresh, or whose
        # sensor is disabled, stays known and is not built again on every update.
        known_bases.intersection_update(coordinator.known_base_ids)
        known_buttons.intersection_update(coordinator.known_button_ids)
        data = coordinator.data
        # A button the refresh returned unlinked has no battery: remove its
        # sensor and forget it, so linking it again builds the sensor again. A
        # button missing from an incomplete refresh keeps its sensor.
        unlinked = {
            button_id
            for button_id in known_buttons
            if (button := data.buttons.get(button_id)) is not None
            and button.base_id is None
        }
        known_buttons.difference_update(unlinked)
        for button_id in unlinked:
            if entity_id := entity_registry.async_get_entity_id(
                Platform.SENSOR, DOMAIN, f"{button_id}_battery"
            ):
                entity_registry.async_remove(entity_id)
        new_bases = set(data.bases) - known_bases
        new_buttons = {
            button_id
            for button_id, button in data.buttons.items()
            if button.base_id is not None
        } - known_buttons
        if not new_bases and not new_buttons:
            return
        known_bases.update(new_bases)
        known_buttons.update(new_buttons)
        entities: list[SensorEntity] = []
        for base_id in new_bases:
            entities.append(FluentPetBaseLastSeenSensor(coordinator, base_id))
            entities.append(FluentPetBaseBatterySensor(coordinator, base_id))
        entities.extend(
            FluentPetButtonBatterySensor(coordinator, button_id)
            for button_id in new_buttons
        )
        async_add_entities(entities)

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
    """When FluentPet last heard from a Base."""

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


class FluentPetBatterySensor(FluentPetCoordinatorEntity, SensorEntity):
    """The battery level of a Base or button, in percent.

    FluentPet reports the level in the steps of its app (100, 75, 50, 25 and 0).
    Unavailable while refreshes fail or the device is not returned.
    """

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: FluentPetCoordinator, device_id: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.device_id = device_id
        self._attr_unique_id = f"{device_id}_battery"
        self._attr_device_info = device_info(device_id)

    @property
    def device(self) -> Base | Button | None:
        """Return the Base or button of the last refresh, or None."""
        raise NotImplementedError

    @property
    @override
    def available(self) -> bool:
        """Return whether the last refresh succeeded and returned the device."""
        return super().available and self.device is not None

    @property
    @override
    def native_value(self) -> int | None:
        """Return the battery level."""
        device = self.device
        return device.battery_level if device is not None else None


class FluentPetBaseBatterySensor(FluentPetBatterySensor):
    """The battery level of a Base.

    Unknown while the Base runs plugged in (FluentPet then reports no level) or
    reports no level; it stays available then, like the other sensors of the
    Base.
    """

    @property
    @override
    def device(self) -> Base | None:
        """Return the Base of the last refresh, or None."""
        return self.coordinator.data.bases.get(self.device_id)

    @property
    @override
    def native_value(self) -> int | None:
        """Return the battery level, or None while the Base runs plugged in."""
        base = self.device
        if base is None or base.power_mode == POWER_MODE_PLUGGED_IN:
            return None
        return base.battery_level


class FluentPetButtonBatterySensor(FluentPetBatterySensor):
    """The battery level of a button, as its Base reports it.

    Unavailable also while FluentPet reports no level: for a button that has not
    been pressed since it started reporting button levels, or whose battery is
    empty.
    """

    @property
    @override
    def available(self) -> bool:
        """Return whether the last refresh succeeded and returned a level."""
        return (
            super().available
            and (button := self.device) is not None
            and button.battery_level is not None
        )

    @property
    @override
    def device(self) -> Button | None:
        """Return the button of the last refresh, or None."""
        return self.coordinator.data.buttons.get(self.device_id)
