"""Authentication for the FluentPet API."""

from abc import ABC, abstractmethod
from typing import Any

from aiohttp import ClientResponse, ClientSession, ClientTimeout

from .const import DEFAULT_BASE_URL, DEFAULT_REQUEST_TIMEOUT, USER_AGENT


class AbstractAuth(ABC):
    """Makes authenticated requests to the FluentPet API.

    Subclass it and implement `async_get_access_token` to return a valid OAuth access token,
    refreshing it when needed. The caller owns the aiohttp session.
    """

    def __init__(
        self,
        websession: ClientSession,
        host: str = DEFAULT_BASE_URL,
        *,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        """Initialize the auth.

        Args:
            websession: The aiohttp session to send requests with.
            host: The API base URL; DEFAULT_BASE_URL is production.
            request_timeout: Total seconds one request may take.
        """
        self.websession = websession
        self.host = host.rstrip("/")
        self.request_timeout = request_timeout

    @abstractmethod
    async def async_get_access_token(self) -> str:
        """Return a valid access token."""

    async def request(
        self,
        method: str,
        path: str,
        *,
        access_token: str | None = None,
        **kwargs: Any,
    ) -> ClientResponse:
        """Send a request with the Bearer token and return the response.

        Args:
            method: HTTP method.
            path: Path below the host, for example /api/v3/households.
            access_token: The token to send; None gets one from async_get_access_token.
            **kwargs: Passed to aiohttp.ClientSession.request.

        The caller must release the response. aiohttp errors and errors of
        async_get_access_token propagate unchanged.
        """
        headers: dict[str, str] = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            **(kwargs.pop("headers", None) or {}),
        }
        if access_token is None:
            access_token = await self.async_get_access_token()
        headers["Authorization"] = f"Bearer {access_token}"
        kwargs.setdefault("timeout", ClientTimeout(total=self.request_timeout))
        return await self.websession.request(
            method, f"{self.host}/{path.lstrip('/')}", headers=headers, **kwargs
        )
