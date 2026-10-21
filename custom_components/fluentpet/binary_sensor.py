"""Binary sensors of the FluentPet Connect integration.

Whether each Base runs plugged in or on battery.
"""

from typing import override

from homeassistant.components.binary_sensor import BinarySensorEntity

# From .const: the package does not re-export it explicitly, which strict mypy
# (no_implicit_reexport) rejects.
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import LOGGER, POWER_MODE_BATTERY, POWER_MODE_PLUGGED_IN
from .coordinator import FluentPetConfigEntry, FluentPetCoordinator
from .entity import FluentPetCoordinatorEntity, device_info

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FluentPetConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a power sensor per Base, including Bases added later."""
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def _async_add_new() -> None:
        # Forget ids that left the household, so their sensor is built again when
        # they return; an id kept through an incomplete refresh, or whose sensor
        # is disabled, stays known and is not built again on every update.
        known.intersection_update(coordinator.known_base_ids)
        new = set(coordinator.data.bases) - known
        if not new:
            return
        known.update(new)
        async_add_entities(
            FluentPetBasePowerSensor(coordinator, base_id) for base_id in new
        )

    _async_add_new()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new))


class FluentPetBasePowerSensor(FluentPetCoordinatorEntity, BinarySensorEntity):
    """Whether a Base runs plugged in (on) or on battery (off).

    Unknown while the Base has not reported a power mode, or reports one this
    integration does not know. Unavailable while refreshes fail or the Base is
    not returned. A base.updated event requests a refresh, which updates it.
    """

    _attr_device_class = BinarySensorDeviceClass.PLUG
    _attr_translation_key = "power"

    def __init__(self, coordinator: FluentPetCoordinator, base_id: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.base_id = base_id
        self._attr_unique_id = f"{base_id}_power"
        self._attr_device_info = device_info(base_id)
        # Unrecognized power modes already logged, so each is logged once.
        self._logged_modes: set[str] = set()

    @property
    @override
    def available(self) -> bool:
        """Return whether the last refresh succeeded and returned the Base."""
        return super().available and self.base_id in self.coordinator.data.bases

    @property
    @override
    def is_on(self) -> bool | None:
        """Return whether the Base runs plugged in, or None when unknown."""
        power_mode = self.coordinator.data.bases[self.base_id].power_mode
        if power_mode == POWER_MODE_PLUGGED_IN:
            return True
        if power_mode == POWER_MODE_BATTERY:
            return False
        if power_mode is not None and power_mode not in self._logged_modes:
            self._logged_modes.add(power_mode)
            LOGGER.debug(
                "Base %s reports an unrecognized power mode %r",
                self.base_id,
                power_mode,
            )
        return None
