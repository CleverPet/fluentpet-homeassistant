"""Processing of FluentPet household events from the webhook and the feed."""

from collections import OrderedDict, deque
from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DEDUPE_SIZE,
    LOGGER,
    PENDING_PRESS_LIMIT,
    signal_interaction,
    signal_press,
)
from .fluentpet_api import (
    LIFECYCLE_EVENT_TYPES,
    Event,
    EventType,
    InteractionUpdatedData,
    PressCreatedData,
)

if TYPE_CHECKING:
    from .coordinator import FluentPetCoordinator


class LruSet:
    """A bounded set that forgets the oldest keys first."""

    def __init__(self, size: int = DEDUPE_SIZE) -> None:
        """Initialize the set."""
        self._size = size
        self._keys: OrderedDict[str, None] = OrderedDict()

    def add(self, key: str) -> bool:
        """Add a key; return False when it was already present."""
        if key in self._keys:
            self._keys.move_to_end(key)
            return False
        self._keys[key] = None
        if len(self._keys) > self._size:
            self._keys.popitem(last=False)
        return True

    def discard(self, key: str) -> None:
        """Remove a key when present."""
        self._keys.pop(key, None)

    def load(self, keys: Iterable[str]) -> None:
        """Add stored keys, oldest first."""
        for key in keys:
            self.add(key)

    def __iter__(self) -> Iterator[str]:
        """Return the keys, oldest first."""
        return iter(self._keys)

    def __contains__(self, key: object) -> bool:
        """Return whether the key is present."""
        return key in self._keys

    def __len__(self) -> int:
        """Return the number of keys."""
        return len(self._keys)


class FluentPetEventProcessor:
    """Turns household events into entity updates, once per event id."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, coordinator: FluentPetCoordinator
    ) -> None:
        """Initialize the processor."""
        self.hass = hass
        self.entry_id = entry_id
        self.coordinator = coordinator
        self.seen_events = LruSet()
        self.seen_deliveries = LruSet()
        # Events an unavailable entity holds; not stored as processed.
        self.held_events: set[str] = set()
        # The latest interaction and its event id, for the sensor when it is added
        # after the event was read.
        self.last_interaction: tuple[str, InteractionUpdatedData] | None = None
        self._ready_buttons: set[str] = set()
        # Presses (event id, time and payload) for buttons without an event entity yet,
        # with the coordinator refresh count at the time they arrived.
        # Each press keeps whether it is replayed.
        self._pending: dict[
            str, tuple[int, deque[tuple[str, datetime, PressCreatedData, bool]]]
        ] = {}

    @property
    def pending_count(self) -> int:
        """Return the number of presses waiting for their button entity."""
        return sum(len(presses) for _, presses in self._pending.values())

    @callback
    def async_process(
        self, event: Event, *, refresh: bool = True, replayed: bool = False
    ) -> None:
        """Process one event; repeated and held event ids are ignored.

        A lifecycle event, or a press of an unknown button, requests a refresh
        of the resources, unless refresh is False: the feed events of a refresh
        that read the resources itself. Replayed marks a press read from the
        feed during catch-up.
        """
        # A held id counts as seen even after it left the bounded seen set.
        if event.id in self.held_events or not self.seen_events.add(event.id):
            LOGGER.debug("Ignoring duplicate event %s", event.id)
            return
        self.coordinator.async_schedule_save()
        data = event.data
        if isinstance(data, PressCreatedData):
            self._async_press(event.id, event.occurred_at, data, refresh, replayed)
        elif isinstance(data, InteractionUpdatedData):
            self.last_interaction = (event.id, data)
            async_dispatcher_send(
                self.hass, signal_interaction(self.entry_id), event.id, data
            )
        elif event.type in LIFECYCLE_EVENT_TYPES:
            if refresh:
                LOGGER.debug("Refreshing after %s event", event.type)
                self._async_request_refresh()
        elif event.type == EventType.PING:
            LOGGER.debug("Received webhook ping %s", event.id)
        else:
            LOGGER.debug("Ignoring event %s of type %s", event.id, event.type)

    @callback
    def async_hold(self, event_id: str) -> None:
        """Record that an entity holds the update of an event."""
        self.held_events.add(event_id)
        self.coordinator.async_schedule_save()

    @callback
    def async_applied(self, event_id: str) -> None:
        """Record that an entity applied a held update.

        The id is added to the seen set again, in case it was pushed out while
        held, so a later copy from the feed or the webhook is not fired again.
        """
        self.held_events.discard(event_id)
        self.seen_events.add(event_id)
        self.coordinator.async_schedule_save()

    @callback
    def async_forget(self, event_id: str) -> None:
        """Forget that an event was processed, so the feed delivers it again.

        Called for an update an entity held and could not apply.
        """
        self.held_events.discard(event_id)
        self.seen_events.discard(event_id)
        self.coordinator.async_schedule_save()

    @callback
    def _async_press(
        self,
        event_id: str,
        occurred_at: datetime,
        data: PressCreatedData,
        refresh: bool,
        replayed: bool,
    ) -> None:
        """Fire a press on its button entity, or keep it until the entity exists.

        Without refresh, the press was read by a refresh that read the buttons
        after it: it counts as older than that read, so a button still unknown
        is dropped at the end of that refresh.
        """
        if data.button is None:
            LOGGER.debug("Ignoring a press without a button")
            return
        button_id = data.button.id
        if button_id in self._ready_buttons:
            async_dispatcher_send(
                self.hass,
                signal_press(self.entry_id, button_id),
                event_id,
                occurred_at,
                data,
                replayed,
            )
            return
        refresh_count = self.coordinator.refresh_count - (0 if refresh else 1)
        _, presses = self._pending.setdefault(
            button_id, (refresh_count, deque(maxlen=PENDING_PRESS_LIMIT))
        )
        presses.append((event_id, occurred_at, data, replayed))
        if refresh and button_id not in self.coordinator.known_button_ids:
            self._async_request_refresh()

    @callback
    def _async_request_refresh(self) -> None:
        """Schedule a debounced coordinator refresh."""
        self.coordinator.config_entry.async_create_background_task(
            self.hass,
            self.coordinator.async_request_refresh(),
            "fluentpet refresh after event",
        )

    @callback
    def async_button_ready(self, button_id: str) -> CALLBACK_TYPE:
        """Mark a button entity as listening and fire its pending presses."""
        self._ready_buttons.add(button_id)
        if (pending := self._pending.pop(button_id, None)) is not None:
            for event_id, occurred_at, data, replayed in pending[1]:
                async_dispatcher_send(
                    self.hass,
                    signal_press(self.entry_id, button_id),
                    event_id,
                    occurred_at,
                    data,
                    replayed,
                )

        def _remove() -> None:
            self._ready_buttons.discard(button_id)

        return _remove

    @callback
    def async_drop_stale_pending(self) -> None:
        """Drop presses of buttons still unknown after a later refresh."""
        known = self.coordinator.known_button_ids
        for button_id, (refresh_count, presses) in list(self._pending.items()):
            if (
                button_id not in known
                and self.coordinator.refresh_count > refresh_count
            ):
                LOGGER.debug(
                    "Dropping %s presses of unknown button %s", len(presses), button_id
                )
                del self._pending[button_id]
