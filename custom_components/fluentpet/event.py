"""Button press events of the FluentPet Connect integration."""

from datetime import datetime
from functools import partial
from typing import Any, override

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EVENT_PRESS, signal_press
from .coordinator import FluentPetConfigEntry, FluentPetCoordinator
from .entity import FluentPetPushEntity, device_info
from .fluentpet_api import PressCreatedData

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FluentPetConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up an event entity per button, including buttons added later."""
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def _async_add_new() -> None:
        # Forget ids that left the household, so their entity is built again when
        # they return; an id kept through an incomplete refresh, or whose entity
        # is disabled, stays known and is not built again on every update.
        known.intersection_update(coordinator.known_button_ids)
        new = set(coordinator.data.buttons) - known
        if not new:
            return
        known.update(new)
        async_add_entities(
            FluentPetButtonEvent(coordinator, button_id) for button_id in new
        )

    _async_add_new()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new))


def press_attributes(
    data: PressCreatedData, occurred_at: datetime, replayed: bool
) -> dict[str, Any]:
    """Return the event attributes of a press.

    `occurred_at` is the time of the press.created event. Replayed marks a
    press read from the feed during catch-up (see
    FluentPetCoordinator.catching_up), for example after Home Assistant was
    offline.
    """
    button = data.button
    pusher = data.pusher
    interaction = data.interaction
    return {
        "button": {
            "name": button.name if button else None,
            "normalized_word": button.normalized_word if button else None,
        },
        "pressed_by": {"name": pusher.name, "type": pusher.type} if pusher else None,
        "press_order": data.position,
        "occurred_at": occurred_at.isoformat(),
        "interaction_id": interaction.id if interaction else None,
        "replayed": replayed,
    }


class FluentPetButtonEvent(FluentPetPushEntity, EventEntity):
    """Fires when a button is pressed.

    Unavailable only while refreshes fail and the webhook is not active; a press
    that arrives then fires once the entity is available again.
    """

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = [EVENT_PRESS]
    _attr_translation_key = "press"

    def __init__(self, coordinator: FluentPetCoordinator, button_id: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._entry_id = coordinator.config_entry.entry_id
        self._processor = coordinator.processor
        self.button_id = button_id
        self._attr_unique_id = f"{button_id}_press"
        self._attr_device_info = device_info(button_id)

    @override
    async def async_added_to_hass(self) -> None:
        """Listen for presses, then fire the presses that arrived before.

        The presses that arrived before fire once the entity is added: Home
        Assistant writes no state before that, so a press fired here would only
        show in the first state, and only the last of several would be seen.
        Until then the processor keeps new presses behind the waiting ones.
        """
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_press(self._entry_id, self.button_id),
                self._async_handle_press,
            )
        )
        # Scheduled from here, the call runs after Home Assistant marked the
        # entity added and wrote its first state.
        self.async_on_remove(self.hass.loop.call_soon(self._async_ready).cancel)

    @callback
    def _async_ready(self) -> None:
        """Fire the presses that arrived before the entity was added."""
        self.async_on_remove(self._processor.async_button_ready(self.button_id))

    @callback
    def _async_handle_press(
        self,
        event_id: str,
        occurred_at: datetime,
        data: PressCreatedData,
        replayed: bool,
    ) -> None:
        """Fire a press, or hold it while the entity is unavailable."""
        self._async_apply(
            event_id, partial(self._async_fire, data, occurred_at, replayed)
        )

    @callback
    def _async_fire(
        self, data: PressCreatedData, occurred_at: datetime, replayed: bool
    ) -> None:
        """Fire a press."""
        self._trigger_event(EVENT_PRESS, press_attributes(data, occurred_at, replayed))
        self.async_write_ha_state()
