"""Client of the FluentPet v3 household API."""

from collections.abc import Callable, Iterable
from http import HTTPStatus
import json
from typing import Any
from urllib.parse import quote

from aiohttp import ClientError, ClientResponse

from .auth import AbstractAuth
from .exceptions import (
    FluentPetApiError,
    FluentPetAuthError,
    FluentPetConnectionError,
    FluentPetForbiddenError,
    FluentPetNotFoundError,
    FluentPetParseError,
    FluentPetRateLimitError,
    FluentPetRequestError,
    FluentPetServerError,
)
from .models import (
    Base,
    Button,
    Event,
    EventPage,
    Household,
    JsonObject,
    Learner,
    Webhook,
    WebhookDelivery,
    WebhookWithSecret,
)

_API = "/api/v3/households"


def _segment(value: str) -> str:
    """Quote one path segment; ids are opaque, so every reserved character is escaped."""
    return quote(value, safe="")


def _retry_after(response: ClientResponse) -> float | None:
    """Return the Retry-After header in seconds, when it is a number."""
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _error(response: ClientResponse, body: bytes) -> FluentPetApiError:
    """Return the exception for an error response.

    The message comes from the {"type", "message"} error body; no request header is used.
    """
    status = response.status
    error_type: str | None = None
    error_message: str | None = None
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    if isinstance(data, dict):
        if isinstance(data.get("type"), str):
            error_type = data["type"]
        if isinstance(data.get("message"), str):
            error_message = data["message"]
    if error_type is None:
        try:
            error_type = HTTPStatus(status).phrase.lower()
        except ValueError:
            error_type = None

    if status == HTTPStatus.UNAUTHORIZED:
        return FluentPetAuthError(status, error_type, error_message)
    if status == HTTPStatus.FORBIDDEN:
        return FluentPetForbiddenError(status, error_type, error_message)
    if status == HTTPStatus.NOT_FOUND:
        return FluentPetNotFoundError(status, error_type, error_message)
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        return FluentPetRateLimitError(
            status, error_type, error_message, _retry_after(response)
        )
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return FluentPetServerError(status, error_type, error_message)
    return FluentPetRequestError(status, error_type, error_message)


def _item(item: Any) -> JsonObject:
    """Return a data list item, which must be a JSON object."""
    if not isinstance(item, dict):
        raise FluentPetParseError("Response data item is not a JSON object")
    return item


type InvalidItemCallback = Callable[[FluentPetParseError], None]


def _data_list[T](
    data: Any,
    factory: Callable[[JsonObject], T],
    on_invalid: InvalidItemCallback | None = None,
) -> list[T]:
    """Parse a {"data": [...]} response with factory.

    A malformed item raises FluentPetParseError, or, when on_invalid is given, is
    skipped and passed to on_invalid.
    """
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise FluentPetParseError("Response has no data list")
    items: list[T] = []
    for item in data["data"]:
        try:
            items.append(factory(_item(item)))
        except FluentPetParseError as err:
            if on_invalid is None:
                raise
            on_invalid(err)
    return items


def _object(data: Any) -> JsonObject:
    """Return a response that must be a JSON object."""
    if not isinstance(data, dict):
        raise FluentPetParseError("Response is not a JSON object")
    return data


class FluentPetClient:
    """Async client of the FluentPet v3 household API.

    Every method raises a FluentPetError subclass on failure:
    FluentPetAuthError (401), FluentPetForbiddenError (403), FluentPetNotFoundError (404),
    FluentPetRateLimitError (429), FluentPetRequestError (400, 422, other 4xx),
    FluentPetServerError (5xx, also a FluentPetConnectionError), FluentPetConnectionError
    (network error or timeout) and FluentPetParseError (a response of the wrong shape).
    An error raised by the auth's async_get_access_token propagates unchanged.

    The list methods take an optional on_invalid callback: with it, a malformed item is
    skipped and its FluentPetParseError passed to the callback instead of failing the
    whole response.
    """

    def __init__(self, auth: AbstractAuth) -> None:
        """Initialize the client with an auth that sends the requests."""
        self._auth = auth

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Send a request and return the decoded JSON body, or None for an empty body."""
        kwargs: dict[str, Any] = {}
        if params:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["json"] = json_body
        # Outside the try: an error getting the token (for example a refused token
        # refresh) is not a network error of this request and propagates unchanged.
        access_token = await self._auth.async_get_access_token()
        try:
            response = await self._auth.request(
                method, path, access_token=access_token, **kwargs
            )
            try:
                body = await response.read()
            finally:
                response.release()
        except TimeoutError as err:
            raise FluentPetConnectionError(
                f"Timeout talking to the FluentPet API ({method} {path})"
            ) from err
        except ClientError as err:
            raise FluentPetConnectionError(
                f"Error talking to the FluentPet API ({method} {path}): {type(err).__name__}"
            ) from err

        if response.status >= HTTPStatus.BAD_REQUEST:
            raise _error(response, body)
        if not body:
            return None
        try:
            return json.loads(body)
        except ValueError as err:
            raise FluentPetParseError(
                f"Response of {method} {path} is not valid JSON"
            ) from err

    @staticmethod
    def _household_path(household_id: str, *parts: str) -> str:
        """Return the path of a household resource, quoting every segment."""
        return "/".join(
            [_API, _segment(household_id), *(_segment(part) for part in parts)]
        )

    async def async_get_households(self) -> list[Household]:
        """Return the households of the user."""
        return _data_list(await self._request("GET", _API), Household.from_dict)

    async def async_get_bases(
        self, household_id: str, *, on_invalid: InvalidItemCallback | None = None
    ) -> list[Base]:
        """Return the Bases of a household."""
        data = await self._request("GET", self._household_path(household_id, "bases"))
        return _data_list(data, Base.from_dict, on_invalid)

    async def async_get_buttons(
        self, household_id: str, *, on_invalid: InvalidItemCallback | None = None
    ) -> list[Button]:
        """Return the buttons of a household."""
        data = await self._request("GET", self._household_path(household_id, "buttons"))
        return _data_list(data, Button.from_dict, on_invalid)

    async def async_get_learners(
        self, household_id: str, *, on_invalid: InvalidItemCallback | None = None
    ) -> list[Learner]:
        """Return the learners and teachers of a household."""
        data = await self._request(
            "GET", self._household_path(household_id, "learners")
        )
        return _data_list(data, Learner.from_dict, on_invalid)

    async def async_get_tail_cursor(self, household_id: str) -> str | None:
        """Return the feed cursor after the newest event, to read on without history.

        Returns None when the household has no events yet.
        """
        data = _object(
            await self._request(
                "GET", self._household_path(household_id, "events", "cursor")
            )
        )
        cursor = data.get("next_cursor")
        if cursor is not None and not isinstance(cursor, str):
            raise FluentPetParseError("next_cursor is not a string")
        return cursor

    async def async_get_events(
        self,
        household_id: str,
        since: str | None = None,
        types: Iterable[str] | None = None,
        limit: int | None = None,
        *,
        on_invalid: InvalidItemCallback | None = None,
    ) -> EventPage:
        """Return a page of the event feed, oldest first.

        Args:
            household_id: The household.
            since: The next_cursor of the previous page; None starts at the oldest event.
            types: Event types to return; None returns every type.
            limit: Page size, 1 to 500 (the server default is 100).
            on_invalid: Skip events with a malformed envelope and pass their error to
                it; without it such an event fails the page. The page's next_cursor
                still moves past skipped events.
        """
        params: dict[str, str] = {}
        if since is not None:
            params["since"] = since
        if types is not None:
            params["types"] = ",".join(str(event_type) for event_type in types)
        if limit is not None:
            params["limit"] = str(limit)
        data = await self._request(
            "GET", self._household_path(household_id, "events"), params=params
        )
        return EventPage.from_dict(_object(data), on_invalid=on_invalid)

    async def async_get_event(self, household_id: str, event_id: str) -> Event:
        """Return one event of a household."""
        data = await self._request(
            "GET", self._household_path(household_id, "events", event_id)
        )
        return Event.from_dict(_object(data))

    async def async_list_webhooks(self, household_id: str) -> list[Webhook]:
        """Return the webhooks of a household, oldest first."""
        data = await self._request(
            "GET", self._household_path(household_id, "webhooks")
        )
        return _data_list(data, Webhook.from_dict)

    async def async_get_webhook(
        self, household_id: str, webhook_id: str
    ) -> Webhook | None:
        """Return one webhook, or None when it does not exist (404)."""
        try:
            data = await self._request(
                "GET", self._household_path(household_id, "webhooks", webhook_id)
            )
        except FluentPetNotFoundError:
            return None
        return Webhook.from_dict(_object(data))

    async def async_create_webhook(
        self,
        household_id: str,
        url: str,
        event_types: Iterable[str] | None = None,
    ) -> WebhookWithSecret:
        """Create an enabled webhook and return it with its signing secret.

        Args:
            household_id: The household.
            url: The public https URL to deliver to.
            event_types: Types to subscribe to; None subscribes to all of them.
        """
        webhook: dict[str, Any] = {"url": url, "enabled": True}
        if event_types is not None:
            webhook["event_types"] = [str(event_type) for event_type in event_types]
        data = await self._request(
            "POST",
            self._household_path(household_id, "webhooks"),
            json_body={"webhook": webhook},
        )
        return WebhookWithSecret.from_dict(_object(data))

    async def async_update_webhook(
        self,
        household_id: str,
        webhook_id: str,
        *,
        url: str | None = None,
        enabled: bool | None = None,
        event_types: Iterable[str] | None = None,
    ) -> Webhook:
        """Change a webhook and return it. Only the given fields are sent.

        Setting enabled to True on a disabled webhook resets its failure count.

        Raises:
            ValueError: No field to change was given.
        """
        webhook: dict[str, Any] = {}
        if url is not None:
            webhook["url"] = url
        if enabled is not None:
            webhook["enabled"] = enabled
        if event_types is not None:
            webhook["event_types"] = [str(event_type) for event_type in event_types]
        if not webhook:
            raise ValueError("Nothing to update")
        data = await self._request(
            "PATCH",
            self._household_path(household_id, "webhooks", webhook_id),
            json_body={"webhook": webhook},
        )
        return Webhook.from_dict(_object(data))

    async def async_delete_webhook(self, household_id: str, webhook_id: str) -> None:
        """Delete a webhook. Raises FluentPetNotFoundError when it does not exist."""
        await self._request(
            "DELETE", self._household_path(household_id, "webhooks", webhook_id)
        )

    async def async_test_webhook(
        self, household_id: str, webhook_id: str
    ) -> WebhookDelivery:
        """Queue a signed ping to a webhook and return the queued delivery."""
        data = await self._request(
            "POST", self._household_path(household_id, "webhooks", webhook_id, "test")
        )
        return WebhookDelivery.from_dict(_object(data))

    async def async_list_webhook_deliveries(
        self, household_id: str, webhook_id: str
    ) -> list[WebhookDelivery]:
        """Return the 50 newest deliveries of a webhook, newest first."""
        data = await self._request(
            "GET",
            self._household_path(household_id, "webhooks", webhook_id, "deliveries"),
        )
        return _data_list(data, WebhookDelivery.from_dict)
