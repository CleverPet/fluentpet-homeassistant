"""Base entities of the FluentPet Connect integration."""

from collections import deque
from collections.abc import Callable
from typing import override

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, HELD_UPDATE_LIMIT
from .coordinator import FluentPetCoordinator


def device_info(identifier: str) -> DeviceInfo:
    """Return the device info of a device the coordinator registered."""
    return DeviceInfo(identifiers={(DOMAIN, identifier)})


class FluentPetCoordinatorEntity(CoordinatorEntity[FluentPetCoordinator]):
    """An entity updated by the coordinator."""

    _attr_has_entity_name = True


class FluentPetPushEntity(FluentPetCoordinatorEntity):
    """An entity updated by household events, from the webhook or the feed.

    Available while refreshes succeed or the webhook is active, so presses pushed
    while the resource refresh fails still reach automations. An update that
    arrives while the entity is unavailable (a webhook delivery while the webhook
    is not active and refreshes fail) is held and applied once the entity is
    available again, after the available state is written, so a state trigger
    that excludes changes from unavailable still sees it. An update that cannot
    be applied (more than HELD_UPDATE_LIMIT held, or the entity is removed) has
    its event id forgotten, so the feed delivers it again instead of losing it.
    Held ids are never stored as processed, so a restart (which does not remove
    entities) leaves them for the feed as well.
    """

    def __init__(self, coordinator: FluentPetCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._held: deque[tuple[str, Callable[[], None]]] = deque()

    @property
    @override
    def available(self) -> bool:
        """Return whether refreshes succeed or the webhook delivers."""
        webhook = self.coordinator.webhook
        return super().available or (webhook is not None and webhook.active)

    @callback
    def _async_apply(self, event_id: str, update: Callable[[], None]) -> None:
        """Apply the update of an event now, or hold it until the entity is available."""
        if self.available:
            update()
            return
        processor = self.coordinator.processor
        if len(self._held) >= HELD_UPDATE_LIMIT:
            dropped, _ = self._held.popleft()
            processor.async_forget(dropped)
        self._held.append((event_id, update))
        processor.async_hold(event_id)

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Forget the events of held updates, so the feed delivers them again."""
        await super().async_will_remove_from_hass()
        while self._held:
            event_id, _ = self._held.popleft()
            self.coordinator.processor.async_forget(event_id)

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Write the state, then apply the updates held while unavailable."""
        super()._handle_coordinator_update()
        if self.available:
            while self._held:
                event_id, update = self._held.popleft()
                update()
                self.coordinator.processor.async_applied(event_id)
