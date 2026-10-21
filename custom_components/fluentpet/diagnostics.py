"""Diagnostics of the FluentPet Connect integration."""

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_API_BASE_URL,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    CONF_WEBHOOK_URL,
)
from .coordinator import FluentPetConfigEntry

TO_REDACT = {
    "access_token",
    "refresh_token",
    "id_token",
    CONF_API_BASE_URL,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    CONF_WEBHOOK_URL,
    "cloudhook_url",
    "url",
    "secret",
    "title",
    "name",
    "serial",
    "serial_number",
    "email",
    "normalized_word",
    "audio_id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FluentPetConfigEntry
) -> dict[str, Any]:
    """Return diagnostics of a config entry."""
    coordinator = entry.runtime_data.coordinator
    manager = entry.runtime_data.webhook
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "custom_api_base_url": CONF_API_BASE_URL in entry.options,
        "data": async_redact_data(asdict(coordinator.data), TO_REDACT),
        "webhook": {
            "active": manager.active,
            "healthy": manager.healthy,
            "failing": manager.failing,
            "failure_count": manager.failure_count,
            "auto_disabled": manager.auto_disabled,
            "uses_cloudhook": manager.uses_cloudhook,
            "last_delivery": manager.last_delivery,
        },
        "feed": {
            "push_active": coordinator.push_active,
            "has_cursor": coordinator.cursor is not None,
            "update_interval": coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None,
            "pending_presses": coordinator.processor.pending_count,
        },
    }
