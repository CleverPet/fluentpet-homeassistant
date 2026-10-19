"""Home Assistant Cloud webhooks, used only when the cloud integration is loaded.

The cloud integration is imported lazily: importing it pulls in requirements that
are only installed where cloud is set up.
"""

from collections.abc import Callable
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback

from .const import LOGGER

CLOUD_DOMAIN = "cloud"


def _cloud_loaded(hass: HomeAssistant) -> bool:
    """Return whether the cloud integration is set up."""
    return CLOUD_DOMAIN in hass.config.components


def async_active_subscription(hass: HomeAssistant) -> bool:
    """Return whether Home Assistant Cloud has an active subscription."""
    if not _cloud_loaded(hass):
        return False
    from homeassistant.components import cloud  # noqa: PLC0415

    return cloud.async_active_subscription(hass)


def async_is_logged_in(hass: HomeAssistant) -> bool:
    """Return whether Home Assistant Cloud is signed in, connected or not."""
    if not _cloud_loaded(hass):
        return False
    from homeassistant.components import cloud  # noqa: PLC0415

    return cloud.async_is_logged_in(hass)


async def async_get_cloudhook_url(hass: HomeAssistant, webhook_id: str) -> str | None:
    """Return the cloudhook URL of a webhook, creating it when needed."""
    if not _cloud_loaded(hass):
        return None
    from homeassistant.components import cloud  # noqa: PLC0415

    try:
        return await cloud.async_get_or_create_cloudhook(hass, webhook_id)
    except cloud.CloudNotAvailable as err:
        LOGGER.debug("Home Assistant Cloud is not available: %s", err)
        return None


async def async_delete_cloudhook(hass: HomeAssistant, webhook_id: str) -> None:
    """Delete the cloudhook of a webhook, when there is one."""
    if not _cloud_loaded(hass):
        return
    from homeassistant.components import cloud  # noqa: PLC0415

    try:
        await cloud.async_delete_cloudhook(hass, webhook_id)
    except (cloud.CloudNotAvailable, ValueError) as err:
        LOGGER.debug("Could not delete the cloudhook: %s", err)


def async_listen_cloudhook_change(
    hass: HomeAssistant,
    webhook_id: str,
    on_change: Callable[[dict[str, Any] | None], None],
) -> CALLBACK_TYPE:
    """Call on_change when the cloudhook of a webhook changes."""
    if not _cloud_loaded(hass):
        return lambda: None
    from homeassistant.components import cloud  # noqa: PLC0415

    return cloud.async_listen_cloudhook_change(hass, webhook_id, on_change)


def async_listen_connection_change(
    hass: HomeAssistant, on_change: Callable[[bool], None]
) -> CALLBACK_TYPE:
    """Call on_change with whether Home Assistant Cloud is connected, on each change."""
    if not _cloud_loaded(hass):
        return lambda: None
    from homeassistant.components import cloud  # noqa: PLC0415

    @callback
    def _async_changed(state: cloud.CloudConnectionState) -> None:
        on_change(state is cloud.CloudConnectionState.CLOUD_CONNECTED)

    return cloud.async_listen_connection_change(hass, _async_changed)
