"""The FluentPet Connect integration."""

from homeassistant.components import persistent_notification
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    OAuth2TokenRequestReauthError,
)
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    issue_registry as ir,
    translation,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    OAuth2Session,
    async_get_config_entry_implementation,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from . import cloudhook
from .api import AsyncConfigEntryAuth, StaticTokenAuth, api_base_url
from .application_credentials import async_import_public_credential
from .const import (
    CONF_HOUSEHOLD_ID,
    CONF_SCAN_INTERVAL,
    CONF_WEBHOOK_ENDPOINT_ID,
    CONF_WEBHOOK_ID,
    DOMAIN,
    LOGGER,
    MIN_SCAN_INTERVAL,
    MY_FLUENTPET_URL,
    STORAGE_VERSION,
)
from .coordinator import (
    FluentPetConfigEntry,
    FluentPetCoordinator,
    FluentPetData,
    not_admin_issue_id,
)
from .fluentpet_api import (
    FluentPetAuthError,
    FluentPetClient,
    FluentPetError,
    FluentPetNotFoundError,
)
from .webhook import FluentPetWebhookManager, webhook_issue_id, webhook_limit_issue_id

PLATFORMS: list[Platform] = [Platform.EVENT, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Import the public FluentPet application credential, when it is defined."""
    await async_import_public_credential(hass)
    return True


async def _async_create_client(
    hass: HomeAssistant, entry: FluentPetConfigEntry
) -> FluentPetClient:
    """Return an API client authenticated with the entry's OAuth token."""
    implementation = await async_get_config_entry_implementation(hass, entry)
    oauth_session = OAuth2Session(hass, entry, implementation)
    return FluentPetClient(
        AsyncConfigEntryAuth(
            async_get_clientsession(hass), oauth_session, api_base_url(entry.options)
        )
    )


async def _async_create_removal_client(
    hass: HomeAssistant, entry: FluentPetConfigEntry
) -> FluentPetClient:
    """Return an API client for removing the entry.

    An expired token is refreshed with the OAuth implementation directly, not the
    entry's OAuth2 session, which would start reauthentication for an entry that
    is being removed. A refused refresh raises OAuth2TokenRequestReauthError.
    """
    implementation = await async_get_config_entry_implementation(hass, entry)
    token = entry.data[CONF_TOKEN]
    if not OAuth2Session(hass, entry, implementation).valid_token:
        token = await implementation.async_refresh_token(token)
    return FluentPetClient(
        StaticTokenAuth(
            async_get_clientsession(hass),
            token[CONF_ACCESS_TOKEN],
            api_base_url(entry.options),
        )
    )


async def async_migrate_entry(hass: HomeAssistant, entry: FluentPetConfigEntry) -> bool:
    """Migrate an entry to the current version.

    Home Assistant does not load an entry of a newer major version.
    """
    if entry.minor_version < 2:
        # 1.2 raised the minimum refresh interval; raise a lower stored value.
        options = dict(entry.options)
        interval = options.get(CONF_SCAN_INTERVAL)
        if isinstance(interval, int | float) and interval < MIN_SCAN_INTERVAL:
            LOGGER.info(
                "Raising the refresh interval of %s from %s to %s seconds",
                entry.title,
                interval,
                MIN_SCAN_INTERVAL,
            )
            options[CONF_SCAN_INTERVAL] = MIN_SCAN_INTERVAL
        hass.config_entries.async_update_entry(entry, options=options, minor_version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: FluentPetConfigEntry) -> bool:
    """Set up FluentPet Connect from a config entry."""
    client = await _async_create_client(hass, entry)
    coordinator = FluentPetCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    manager = FluentPetWebhookManager(
        hass, entry, client, coordinator.household_id, coordinator.processor
    )
    entry.async_on_unload(manager.async_unload)
    coordinator.webhook = manager
    try:
        await manager.async_setup()
    except (FluentPetAuthError, OAuth2TokenRequestReauthError) as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="authentication_failed"
        ) from err

    entry.runtime_data = FluentPetData(client, coordinator, manager)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FluentPetConfigEntry) -> bool:
    """Unload a config entry; the local webhook is unregistered on unload."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: FluentPetConfigEntry) -> None:
    """Delete the FluentPet webhook, the cloudhook and the stored feed cursor."""
    if (endpoint_id := entry.data.get(CONF_WEBHOOK_ENDPOINT_ID)) is not None:
        try:
            client = await _async_create_removal_client(hass, entry)
            await client.async_delete_webhook(
                entry.data[CONF_HOUSEHOLD_ID], endpoint_id
            )
        except FluentPetNotFoundError:
            LOGGER.debug("The FluentPet webhook was already deleted")
        except (FluentPetError, HomeAssistantError) as err:
            LOGGER.warning("Could not delete the FluentPet webhook: %s", err)
            await _async_notify_webhook_left(hass, entry)

    # Also covers a cloudhook left over from an earlier URL choice.
    if CONF_WEBHOOK_ID in entry.data:
        await cloudhook.async_delete_cloudhook(hass, entry.data[CONF_WEBHOOK_ID])

    for issue_id in (
        webhook_issue_id(entry.entry_id),
        webhook_limit_issue_id(entry.entry_id),
        not_admin_issue_id(entry.entry_id),
    ):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
    await Store[dict[str, object]](
        hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
    ).async_remove()


async def _async_notify_webhook_left(
    hass: HomeAssistant, entry: FluentPetConfigEntry
) -> None:
    """Tell the user to delete the FluentPet webhook that removal could not delete."""
    # English fills in keys missing in the user's language.
    translations = await translation.async_get_translations(
        hass, hass.config.language, "exceptions", [DOMAIN]
    )
    message = translations[f"component.{DOMAIN}.exceptions.webhook_not_deleted.message"]
    persistent_notification.async_create(
        hass,
        message.format(household=entry.title, url=MY_FLUENTPET_URL),
        title="FluentPet Connect",
        notification_id=f"{DOMAIN}_webhook_not_deleted_{entry.entry_id}",
    )


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: FluentPetConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing a device the household no longer has."""
    current = entry.runtime_data.coordinator.current_ids()
    return not any(
        domain == DOMAIN and identifier in current
        for domain, identifier in device_entry.identifiers
    )
