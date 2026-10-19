"""Authentication of the FluentPet API bound to Home Assistant."""

from collections.abc import Mapping
from typing import Any, cast, override

from aiohttp import ClientSession
from yarl import URL

from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session

from .const import CONF_API_BASE_URL
from .fluentpet_api import DEFAULT_BASE_URL, AbstractAuth


def api_base_url(options: Mapping[str, Any]) -> str:
    """Return the API base URL of an entry's options, or the default."""
    return str(options.get(CONF_API_BASE_URL, DEFAULT_BASE_URL))


def normalize_api_base_url(value: str) -> str | None:
    """Return an https API base URL without a trailing slash, or None when invalid.

    The URL needs the https scheme and a host, and no credentials, query or
    fragment; a path prefix is kept.
    """
    try:
        url = URL(value.strip())
    except ValueError:
        return None
    if (
        url.scheme != "https"
        or not url.host
        or url.user is not None
        or url.query_string
        or url.fragment
    ):
        return None
    return str(url).rstrip("/")


class AsyncConfigEntryAuth(AbstractAuth):
    """FluentPet authentication tied to an OAuth2 config entry."""

    def __init__(
        self,
        websession: ClientSession,
        oauth_session: OAuth2Session,
        host: str = DEFAULT_BASE_URL,
    ) -> None:
        """Initialize the auth."""
        super().__init__(websession, host)
        self._oauth_session = oauth_session

    @override
    async def async_get_access_token(self) -> str:
        """Return a valid access token, refreshing it when needed.

        A failed refresh raises Home Assistant's OAuth2 token errors unchanged:
        OAuth2TokenRequestReauthError (a ConfigEntryAuthFailed, after the session
        started reauth) when the refresh token was refused, and other
        OAuth2TokenRequestBaseError subclasses (ConfigEntryNotReady) when the
        token endpoint failed for the moment. The client does not wrap them.
        """
        await self._oauth_session.async_ensure_token_valid()
        return cast(str, self._oauth_session.token[CONF_ACCESS_TOKEN])


class StaticTokenAuth(AbstractAuth):
    """FluentPet authentication with a fixed access token, for the config flow."""

    def __init__(
        self,
        websession: ClientSession,
        access_token: str,
        host: str = DEFAULT_BASE_URL,
    ) -> None:
        """Initialize the auth."""
        super().__init__(websession, host)
        self._access_token = access_token

    @override
    async def async_get_access_token(self) -> str:
        """Return the access token."""
        return self._access_token
