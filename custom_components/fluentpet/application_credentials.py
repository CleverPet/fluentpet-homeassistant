"""Application credentials platform for the FluentPet Connect integration."""

from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_entry_oauth2_flow import (
    LocalOAuth2ImplementationWithPkce,
)

from .const import CLIENT_ID, CLIENT_NAME, DOMAIN, OAUTH2_AUTHORIZE, OAUTH2_TOKEN


async def async_import_public_credential(hass: HomeAssistant) -> None:
    """Import the public FluentPet application credential, when it is defined.

    Home Assistant skips the import when the credential exists already.
    """
    if CLIENT_ID:
        await async_import_client_credential(
            hass, DOMAIN, ClientCredential(CLIENT_ID, "", name=CLIENT_NAME)
        )


async def async_get_auth_implementation(
    hass: HomeAssistant, auth_domain: str, credential: ClientCredential
) -> LocalOAuth2ImplementationWithPkce:
    """Return a FluentPet OAuth2 implementation with PKCE.

    FluentPet uses a public Auth0 client, so a client secret entered in the
    credentials form is never sent.
    """
    return LocalOAuth2ImplementationWithPkce(
        hass,
        auth_domain,
        credential.client_id,
        OAUTH2_AUTHORIZE,
        OAUTH2_TOKEN,
        "",
    )


async def async_get_description_placeholders(hass: HomeAssistant) -> dict[str, str]:
    """Return placeholders for the application credentials dialog."""
    return {"more_info_url": "https://github.com/CleverPet/fluentpet-homeassistant"}
