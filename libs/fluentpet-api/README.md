# fluentpet-api

Async Python client for the FluentPet v3 household API: households, Bases, buttons, learners and teachers, the
household event feed and webhooks, plus verification of signed webhook requests. It depends only
on `aiohttp` and the standard library and never imports Home Assistant.

## Where the code lives

The library is vendored in the FluentPet Connect Home Assistant integration at
`custom_components/fluentpet/fluentpet_api`, and the integration imports it with
`from .fluentpet_api import ...`. This directory only holds the packaging metadata for it; nothing
is published. When the library moves to its own repository, CleverPet/fluentpet-api, the package
moves to `src/fluentpet_api` there, is published on PyPI as `fluentpet-api`, and the integration
lists it in `manifest.json` requirements instead of vendoring it.

## Usage

Implement `AbstractAuth.async_get_access_token` to return a valid OAuth access token. The caller
owns the `aiohttp.ClientSession`.

```python
import aiohttp

from fluentpet_api import AbstractAuth, EventType, FluentPetClient, PressCreatedData


class TokenAuth(AbstractAuth):
    def __init__(self, session: aiohttp.ClientSession, token: str) -> None:
        super().__init__(session)  # host defaults to DEFAULT_BASE_URL
        self._token = token

    async def async_get_access_token(self) -> str:
        return self._token


async def main(token: str) -> None:
    async with aiohttp.ClientSession() as session:
        client = FluentPetClient(TokenAuth(session, token))
        households = await client.async_get_households()
        household_id = households[0].id

        buttons = await client.async_get_buttons(household_id)
        learners = await client.async_get_learners(household_id)

        cursor = await client.async_get_tail_cursor(household_id)
        page = await client.async_get_events(
            household_id, since=cursor, types=[EventType.PRESS_CREATED]
        )
        for event in page.events:
            if isinstance(event.data, PressCreatedData) and event.data.button:
                print(event.occurred_at, event.data.button.name)
        cursor = page.next_cursor  # store it and pass it back as since
```

The list methods (`async_get_bases`, `async_get_buttons`, `async_get_learners` and
`async_get_events`) take an optional `on_invalid` callback: with it, a malformed item is skipped
and its `FluentPetParseError` passed to the callback instead of failing the whole response. A
`Base` carries `last_online_at`, `power_mode` (`"plugged_in"` or `"battery"`, another string kept
as is) and `battery_level` (0 to 100), each `None` when unknown.

Errors are `FluentPetError` subclasses: `FluentPetAuthError` (401), `FluentPetForbiddenError`
(403, for example a member who is not the household admin reading webhooks or events),
`FluentPetNotFoundError` (404), `FluentPetRateLimitError` (429), `FluentPetRequestError` (400,
422), `FluentPetServerError` (5xx) and `FluentPetConnectionError` (network error or timeout, and
the base class of `FluentPetServerError`). Requests time out after 30 seconds by default; pass
`request_timeout` to `AbstractAuth` to change it. An error raised by `async_get_access_token`
(for example a refused token refresh) propagates unchanged, so it is never mistaken for a network
error of the request.

Ids and cursors are opaque strings: store and compare them, never parse them.

## Webhooks

`async_create_webhook` returns the webhook with its signing secret, which the API returns only
once. Verify every request against the raw body before parsing it:

```python
from fluentpet_api import (
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    WebhookVerificationError,
    parse_event_body,
    verify_signature,
)


def handle(secret: str, headers: dict[str, str], raw_body: bytes) -> int:
    try:
        verify_signature(
            secret,
            headers.get(HEADER_TIMESTAMP, ""),
            raw_body,
            headers.get(HEADER_SIGNATURE, ""),
        )
    except WebhookVerificationError:
        return 401
    event = parse_event_body(raw_body)
    # Deduplicate on the X-FluentPet-Delivery header or event.id, then handle the event.
    return 200
```

`verify_signature` refuses a malformed timestamp or signature and a signature that does not match
(`InvalidSignatureError`), and a timestamp more than 300 seconds from the local clock
(`StaleTimestampError`). `compute_signature` builds a signature, for tests.

## Development

The tests live in the integration repository under `tests/fluentpet_api`.
