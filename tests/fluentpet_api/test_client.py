"""Tests for the FluentPet API client."""

from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.fluentpet.fluentpet_api import (
    DEFAULT_BASE_URL,
    EventType,
    FluentPetApiError,
    FluentPetAuthError,
    FluentPetClient,
    FluentPetConnectionError,
    FluentPetForbiddenError,
    FluentPetNotFoundError,
    FluentPetParseError,
    FluentPetRateLimitError,
    FluentPetRequestError,
    FluentPetServerError,
    PressCreatedData,
    WebhookWithSecret,
)
from tests.common import load_json_fixture

from .conftest import ACCESS_TOKEN, HOST, FakeAuth

HH = "hh_testhouseholdaaaaaaaaaaaaa"
WEBHOOK_ID = "00000000-0000-4000-8000-000000009001"
EVENT_ID = "00000000-0000-4000-8000-000000000001"
API = f"{HOST}/api/v3/households"

# (client method, positional args, keyword args, HTTP method, URL)
ENDPOINTS: list[tuple[str, tuple[Any, ...], dict[str, Any], str, str]] = [
    ("async_get_households", (), {}, "GET", API),
    ("async_get_bases", (HH,), {}, "GET", f"{API}/{HH}/bases"),
    ("async_get_buttons", (HH,), {}, "GET", f"{API}/{HH}/buttons"),
    ("async_get_learners", (HH,), {}, "GET", f"{API}/{HH}/learners"),
    ("async_get_tail_cursor", (HH,), {}, "GET", f"{API}/{HH}/events/cursor"),
    ("async_get_events", (HH,), {}, "GET", f"{API}/{HH}/events"),
    ("async_get_event", (HH, EVENT_ID), {}, "GET", f"{API}/{HH}/events/{EVENT_ID}"),
    ("async_list_webhooks", (HH,), {}, "GET", f"{API}/{HH}/webhooks"),
    (
        "async_get_webhook",
        (HH, WEBHOOK_ID),
        {},
        "GET",
        f"{API}/{HH}/webhooks/{WEBHOOK_ID}",
    ),
    (
        "async_create_webhook",
        (HH, "https://hooks.example.com/x"),
        {},
        "POST",
        f"{API}/{HH}/webhooks",
    ),
    (
        "async_update_webhook",
        (HH, WEBHOOK_ID),
        {"enabled": True},
        "PATCH",
        f"{API}/{HH}/webhooks/{WEBHOOK_ID}",
    ),
    (
        "async_delete_webhook",
        (HH, WEBHOOK_ID),
        {},
        "DELETE",
        f"{API}/{HH}/webhooks/{WEBHOOK_ID}",
    ),
    (
        "async_test_webhook",
        (HH, WEBHOOK_ID),
        {},
        "POST",
        f"{API}/{HH}/webhooks/{WEBHOOK_ID}/test",
    ),
    (
        "async_list_webhook_deliveries",
        (HH, WEBHOOK_ID),
        {},
        "GET",
        f"{API}/{HH}/webhooks/{WEBHOOK_ID}/deliveries",
    ),
]
ENDPOINT_IDS = [endpoint[0] for endpoint in ENDPOINTS]

ERRORS: list[tuple[int, str, type[FluentPetApiError]]] = [
    (400, "bad request", FluentPetRequestError),
    (401, "not authenticated", FluentPetAuthError),
    (403, "not allowed", FluentPetForbiddenError),
    (404, "not found", FluentPetNotFoundError),
    (422, "unprocessable entity", FluentPetRequestError),
    (429, "too many requests", FluentPetRateLimitError),
    (500, "internal-server-error", FluentPetServerError),
]


def _call(
    client: FluentPetClient, name: str, args: tuple[Any, ...], kwargs: Any
) -> Any:
    """Return the coroutine of a client method."""
    return getattr(client, name)(*args, **kwargs)


def _headers(mocker: AiohttpClientMocker, index: int = -1) -> dict[str, str]:
    """Return the headers of a recorded request."""
    return dict(mocker.mock_calls[index][3])


async def test_get_households(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """Households are parsed and the request carries the Bearer token."""
    mocker_aiohttp.get(API, json=load_json_fixture("households.json"))
    households = await client.async_get_households()
    assert [h.id for h in households] == [
        "hh_testhouseholdaaaaaaaaaaaaa",
        "hh_testhouseholdotheraaaaaaaa",
    ]
    assert households[0].name == "Test Household"
    assert households[0].admin is True
    assert households[1].admin is False
    headers = _headers(mocker_aiohttp)
    assert headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert headers["User-Agent"].startswith("fluentpet-api/")
    assert headers["Accept"] == "application/json"


async def test_get_bases(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """Bases are parsed with their last check-in, power mode and battery level."""
    mocker_aiohttp.get(f"{API}/{HH}/bases", json=load_json_fixture("bases.json"))
    bases = await client.async_get_bases(HH)
    assert bases[0].serial == "TESTSERIAL0001"
    assert bases[0].connected is True
    assert bases[0].last_online_at == datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    assert bases[0].fw_version == "2.4.20260901"
    assert bases[0].power_mode == "plugged_in"
    assert bases[0].battery_level is None
    assert bases[0].battery_updated_at == datetime(2026, 10, 17, 11, 30, tzinfo=UTC)
    assert bases[1].name is None
    assert bases[1].connected is False
    assert bases[1].last_online_at == datetime(2026, 10, 17, 11, 45, tzinfo=UTC)
    assert bases[1].power_mode == "battery"
    assert bases[1].battery_level == 50


async def test_get_buttons(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """Buttons are parsed with their battery level, including unlinked ones."""
    mocker_aiohttp.get(f"{API}/{HH}/buttons", json=load_json_fixture("buttons.json"))
    buttons = await client.async_get_buttons(HH)
    assert buttons[0].name == "Outside"
    assert buttons[0].normalized_word == "outside"
    assert buttons[0].base_id == "base_testbaseoneaaaaaaaaaaaaaaa"
    assert buttons[0].audio_id == "aud_testaudiooutsideaaaaaaaaaa"
    assert buttons[0].battery_level == 75
    assert buttons[0].battery_updated_at == datetime(2026, 10, 17, 11, 50, tzinfo=UTC)
    assert buttons[1].battery_level is None
    assert buttons[1].battery_updated_at is None
    assert buttons[2].base_id is None
    assert buttons[2].serial is None


async def test_get_learners(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """Learners and teachers are parsed."""
    mocker_aiohttp.get(f"{API}/{HH}/learners", json=load_json_fixture("learners.json"))
    learners = await client.async_get_learners(HH)
    assert [(lr.name, lr.is_teacher) for lr in learners] == [
        ("Test Learner", False),
        ("Test Teacher", True),
    ]


async def test_get_tail_cursor(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """The tail cursor is returned as an opaque string."""
    mocker_aiohttp.get(
        f"{API}/{HH}/events/cursor", json=load_json_fixture("events_cursor.json")
    )
    assert await client.async_get_tail_cursor(HH) == "AAAAAAAAAAtestcursor00000013"


async def test_get_tail_cursor_null(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """A household without events has no tail cursor."""
    mocker_aiohttp.get(f"{API}/{HH}/events/cursor", json={"next_cursor": None})
    assert await client.async_get_tail_cursor(HH) is None


async def test_get_tail_cursor_wrong_type(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """A cursor that is not a string is a parse error."""
    mocker_aiohttp.get(f"{API}/{HH}/events/cursor", json={"next_cursor": 5})
    with pytest.raises(FluentPetParseError):
        await client.async_get_tail_cursor(HH)


async def test_get_events(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """A feed page is parsed and the query carries since, types and limit."""
    mocker_aiohttp.get(f"{API}/{HH}/events", json=load_json_fixture("events_page.json"))
    page = await client.async_get_events(
        HH,
        since="cursor-1",
        types=[EventType.PRESS_CREATED, "interaction.updated"],
        limit=500,
    )
    assert page.next_cursor == "AAAAAAAAAAtestcursor00000002"
    assert [event.type for event in page.events] == [
        "press.created",
        "interaction.updated",
    ]
    assert isinstance(page.events[0].data, PressCreatedData)
    url = mocker_aiohttp.mock_calls[-1][1]
    assert url.query == {
        "since": "cursor-1",
        "types": "press.created,interaction.updated",
        "limit": "500",
    }


async def test_get_events_without_params(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """Without arguments no query is sent; an empty first page has no cursor."""
    mocker_aiohttp.get(f"{API}/{HH}/events", json={"data": [], "next_cursor": None})
    page = await client.async_get_events(HH)
    assert page.events == ()
    assert page.next_cursor is None
    assert mocker_aiohttp.mock_calls[-1][1].query_string == ""


async def test_get_event(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """One event is parsed."""
    mocker_aiohttp.get(
        f"{API}/{HH}/events/{EVENT_ID}",
        json=load_json_fixture("events/press_created.json"),
    )
    event = await client.async_get_event(HH, EVENT_ID)
    assert event.id == EVENT_ID
    assert event.event_type is EventType.PRESS_CREATED


async def test_list_webhooks(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """Webhooks are parsed, including an auto-disabled one."""
    mocker_aiohttp.get(f"{API}/{HH}/webhooks", json=load_json_fixture("webhooks.json"))
    webhooks = await client.async_list_webhooks(HH)
    assert webhooks[0].id == WEBHOOK_ID
    assert webhooks[0].event_types == ("press.created", "interaction.updated")
    assert webhooks[0].auto_disabled is False
    assert webhooks[1].auto_disabled is True
    assert webhooks[1].failure_count == 20


async def test_get_webhook(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """One webhook is read from its own endpoint."""
    mocker_aiohttp.get(
        f"{API}/{HH}/webhooks/{WEBHOOK_ID}", json=load_json_fixture("webhook.json")
    )
    webhook = await client.async_get_webhook(HH, WEBHOOK_ID)
    assert webhook is not None
    assert webhook.id == WEBHOOK_ID
    assert webhook.enabled is True


async def test_get_webhook_not_found(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """A missing webhook is None."""
    mocker_aiohttp.get(
        f"{API}/{HH}/webhooks/{WEBHOOK_ID}",
        status=HTTPStatus.NOT_FOUND,
        json={"type": "not found", "message": "Webhook not found"},
    )
    assert await client.async_get_webhook(HH, WEBHOOK_ID) is None


async def test_create_webhook(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """Create sends an enabled webhook and returns its secret, kept out of repr."""
    mocker_aiohttp.post(
        f"{API}/{HH}/webhooks",
        status=HTTPStatus.CREATED,
        json=load_json_fixture("webhook_created.json"),
    )
    webhook = await client.async_create_webhook(
        HH,
        "https://hooks.example.com/x",
        event_types=[EventType.PRESS_CREATED, "base.added"],
    )
    assert isinstance(webhook, WebhookWithSecret)
    assert webhook.secret == "whsec_" + "0" * 64
    assert webhook.secret not in repr(webhook)
    assert len(webhook.event_types) == 13
    assert mocker_aiohttp.mock_calls[-1][2] == {
        "webhook": {
            "url": "https://hooks.example.com/x",
            "enabled": True,
            "event_types": ["press.created", "base.added"],
        }
    }


async def test_create_webhook_all_types(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """Without event_types the body leaves them out, so the server subscribes to all."""
    mocker_aiohttp.post(
        f"{API}/{HH}/webhooks",
        status=HTTPStatus.CREATED,
        json=load_json_fixture("webhook_created.json"),
    )
    await client.async_create_webhook(HH, "https://hooks.example.com/x")
    assert mocker_aiohttp.mock_calls[-1][2] == {
        "webhook": {"url": "https://hooks.example.com/x", "enabled": True}
    }


async def test_update_webhook(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """Update sends only the given fields."""
    mocker_aiohttp.patch(
        f"{API}/{HH}/webhooks/{WEBHOOK_ID}", json=load_json_fixture("webhook.json")
    )
    webhook = await client.async_update_webhook(
        HH,
        WEBHOOK_ID,
        url="https://hooks.example.com/y",
        enabled=True,
        event_types=["press.created"],
    )
    assert webhook.id == WEBHOOK_ID
    assert mocker_aiohttp.mock_calls[-1][2] == {
        "webhook": {
            "url": "https://hooks.example.com/y",
            "enabled": True,
            "event_types": ["press.created"],
        }
    }


async def test_update_webhook_disable(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """enabled=False is sent, not dropped as falsy."""
    mocker_aiohttp.patch(
        f"{API}/{HH}/webhooks/{WEBHOOK_ID}", json=load_json_fixture("webhook.json")
    )
    await client.async_update_webhook(HH, WEBHOOK_ID, enabled=False)
    assert mocker_aiohttp.mock_calls[-1][2] == {"webhook": {"enabled": False}}


async def test_update_webhook_nothing(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """An update without fields is refused locally."""
    with pytest.raises(ValueError):
        await client.async_update_webhook(HH, WEBHOOK_ID)
    assert mocker_aiohttp.call_count == 0


async def test_delete_webhook(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """Delete accepts an empty 204."""
    mocker_aiohttp.delete(
        f"{API}/{HH}/webhooks/{WEBHOOK_ID}", status=HTTPStatus.NO_CONTENT
    )
    assert await client.async_delete_webhook(HH, WEBHOOK_ID) is None
    assert mocker_aiohttp.call_count == 1


async def test_test_webhook(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """A test ping returns the queued delivery."""
    mocker_aiohttp.post(
        f"{API}/{HH}/webhooks/{WEBHOOK_ID}/test",
        status=HTTPStatus.ACCEPTED,
        json=load_json_fixture("webhook_delivery.json"),
    )
    delivery = await client.async_test_webhook(HH, WEBHOOK_ID)
    assert delivery.event_type == "ping"
    assert delivery.status == "pending"
    assert delivery.household_event_id is None
    assert delivery.created_at == datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


async def test_list_webhook_deliveries(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """Deliveries are parsed."""
    delivery = load_json_fixture("webhook_delivery.json") | {
        "status": "succeeded",
        "attempts": 1,
        "response_status": 200,
        "response_time_ms": 142,
        "response_body": "ok",
        "last_attempted_at": "2026-10-17T12:00:01.000Z",
    }
    mocker_aiohttp.get(
        f"{API}/{HH}/webhooks/{WEBHOOK_ID}/deliveries", json={"data": [delivery]}
    )
    (result,) = await client.async_list_webhook_deliveries(HH, WEBHOOK_ID)
    assert result.response_status == 200
    assert result.response_time_ms == 142
    assert result.attempts == 1
    assert result.last_attempted_at == datetime(2026, 10, 17, 12, 0, 1, tzinfo=UTC)


async def test_path_segments_are_quoted(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """Ids are quoted as single path segments."""
    mocker_aiohttp.get(
        f"{API}/a%2Fb%3F/webhooks/c%20d", json=load_json_fixture("webhook.json")
    )
    await client.async_get_webhook("a/b?", "c d")
    assert mocker_aiohttp.mock_calls[-1][1].raw_path == (
        "/api/v3/households/a%2Fb%3F/webhooks/c%20d"
    )


async def test_default_host_and_timeout(
    session: aiohttp.ClientSession, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """The default host is the FluentPet API and a trailing slash is dropped."""
    auth = FakeAuth(session)
    assert auth.host == DEFAULT_BASE_URL
    assert auth.request_timeout == 30
    custom = FakeAuth(session, "https://example.com/", request_timeout=5)
    assert custom.host == HOST
    mocker_aiohttp.get(API, json={"data": []})
    assert await FluentPetClient(custom).async_get_households() == []


async def test_request_timeout_is_passed(session: aiohttp.ClientSession) -> None:
    """The auth passes a ClientTimeout with the configured total, and caller headers."""
    seen: dict[str, Any] = {}

    async def fake_request(method: str, url: str, **kwargs: Any) -> Any:
        seen.update(kwargs, method=method, url=url)
        raise aiohttp.ClientConnectionError

    auth = FakeAuth(session, HOST, request_timeout=7)
    object.__setattr__(session, "request", fake_request)
    with pytest.raises(aiohttp.ClientConnectionError):
        await auth.request("GET", "api/v3/households", headers={"X-Test": "1"})
    assert seen["timeout"] == aiohttp.ClientTimeout(total=7)
    assert seen["url"] == f"{HOST}/api/v3/households"
    assert seen["headers"]["X-Test"] == "1"
    assert seen["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"


@pytest.mark.parametrize(("status", "error_type", "exc_class"), ERRORS)
@pytest.mark.parametrize(
    ("name", "args", "kwargs", "method", "url"), ENDPOINTS, ids=ENDPOINT_IDS
)
async def test_error_mapping(
    client: FluentPetClient,
    mocker_aiohttp: AiohttpClientMocker,
    name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    method: str,
    url: str,
    status: int,
    error_type: str,
    exc_class: type[FluentPetApiError],
) -> None:
    """Every endpoint maps error statuses to their exception, keeping the message."""
    mocker_aiohttp.request(
        method,
        url,
        status=status,
        json={"type": error_type, "message": "Test message"},
    )
    if name == "async_get_webhook" and status == 404:
        assert await _call(client, name, args, kwargs) is None
        return
    with pytest.raises(exc_class) as exc_info:
        await _call(client, name, args, kwargs)
    err = exc_info.value
    assert err.status == status
    assert err.error_type == error_type
    assert err.error_message == "Test message"
    assert "Test message" in str(err)
    assert ACCESS_TOKEN not in str(err)
    if status >= 500:
        assert isinstance(err, FluentPetConnectionError)


@pytest.mark.parametrize(
    "exc",
    [aiohttp.ClientConnectionError("boom"), TimeoutError()],
    ids=["connection", "timeout"],
)
@pytest.mark.parametrize(
    ("name", "args", "kwargs", "method", "url"), ENDPOINTS, ids=ENDPOINT_IDS
)
async def test_connection_errors(
    client: FluentPetClient,
    mocker_aiohttp: AiohttpClientMocker,
    name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    method: str,
    url: str,
    exc: Exception,
) -> None:
    """Network errors and timeouts become FluentPetConnectionError without the token."""
    mocker_aiohttp.request(method, url, exc=exc)
    with pytest.raises(FluentPetConnectionError) as exc_info:
        await _call(client, name, args, kwargs)
    assert ACCESS_TOKEN not in str(exc_info.value)
    assert exc_info.value.__cause__ is exc


async def test_error_during_body_read(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """A connection closed while reading the body is a connection error."""
    mocker_aiohttp.get(API, json={"data": []}, closing=True)
    with pytest.raises(FluentPetConnectionError):
        await client.async_get_households()


async def test_error_without_json_body(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """An error without a JSON body is named by its status phrase."""
    mocker_aiohttp.get(API, status=502, text="<html>Bad gateway</html>")
    with pytest.raises(FluentPetServerError) as exc_info:
        await client.async_get_households()
    assert exc_info.value.error_type == "bad gateway"
    assert exc_info.value.error_message is None
    assert str(exc_info.value) == "HTTP 502: bad gateway"


async def test_error_unknown_status(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """A status without a phrase and an error body of the wrong shape still map."""
    mocker_aiohttp.get(API, status=499, json={"type": 1, "message": None})
    with pytest.raises(FluentPetRequestError) as exc_info:
        await client.async_get_households()
    assert exc_info.value.error_type is None
    assert str(exc_info.value) == "HTTP 499"


async def test_error_conflict(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """Another 4xx is a request error."""
    mocker_aiohttp.get(
        API, status=409, json={"type": "conflict", "message": "Conflict"}
    )
    with pytest.raises(FluentPetRequestError):
        await client.async_get_households()


@pytest.mark.parametrize(
    ("header", "expected"), [("12", 12.0), ("soon", None), (None, None)]
)
async def test_rate_limit_retry_after(
    client: FluentPetClient,
    mocker_aiohttp: AiohttpClientMocker,
    header: str | None,
    expected: float | None,
) -> None:
    """Retry-After is exposed when it is a number."""
    mocker_aiohttp.get(
        API,
        status=429,
        json={
            "type": "too many requests",
            "message": "Rate limit exceeded. Please slow down.",
        },
        headers={"Retry-After": header} if header is not None else None,
    )
    with pytest.raises(FluentPetRateLimitError) as exc_info:
        await client.async_get_households()
    assert exc_info.value.retry_after == expected


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        '{"data": "nope"}',
        '["a list"]',
        '{"data": ["not an object"]}',
        '{"data": [{"id": "hh_x"}]}',
    ],
)
async def test_malformed_list_response(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker, body: str
) -> None:
    """A response of the wrong shape is a parse error."""
    mocker_aiohttp.get(API, text=body)
    with pytest.raises(FluentPetParseError):
        await client.async_get_households()


async def test_object_response_not_object(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """An endpoint returning one object rejects other JSON."""
    mocker_aiohttp.get(f"{API}/{HH}/events/{EVENT_ID}", json=[1, 2])
    with pytest.raises(FluentPetParseError):
        await client.async_get_event(HH, EVENT_ID)


async def test_empty_success_body_where_object_expected(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """An empty body where an object is expected is a parse error."""
    mocker_aiohttp.get(f"{API}/{HH}/webhooks/{WEBHOOK_ID}", text="")
    with pytest.raises(FluentPetParseError):
        await client.async_get_webhook(HH, WEBHOOK_ID)


class TokenErrorAuth(FakeAuth):
    """Auth whose token refresh fails with an aiohttp error."""

    async def async_get_access_token(self) -> str:
        """Fail like a refused token refresh."""
        raise aiohttp.ClientResponseError(None, (), status=400)  # type: ignore[arg-type]


async def test_token_error_propagates_unchanged(
    session: aiohttp.ClientSession, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """An error getting the token is not turned into a connection error."""
    mocker_aiohttp.get(API, json={"data": []})
    client = FluentPetClient(TokenErrorAuth(session, HOST))
    with pytest.raises(aiohttp.ClientResponseError) as exc_info:
        await client.async_get_households()
    assert not isinstance(exc_info.value, FluentPetConnectionError)
    assert not mocker_aiohttp.mock_calls


async def test_auth_request_gets_token(
    auth: FakeAuth, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """Without a given token the auth gets one itself."""
    mocker_aiohttp.get(API, json={"data": []})
    response = await auth.request("GET", "/api/v3/households")
    response.release()
    assert _headers(mocker_aiohttp)["Authorization"] == f"Bearer {ACCESS_TOKEN}"


@pytest.mark.parametrize(
    ("method", "path", "body", "attribute", "expected"),
    [
        (
            "async_get_bases",
            "bases",
            {
                "data": [
                    "not an object",
                    {"id": "base_x"},
                    load_json_fixture("bases.json")["data"][0],
                ]
            },
            "serial",
            ["TESTSERIAL0001"],
        ),
        (
            "async_get_buttons",
            "buttons",
            {"data": [{"id": "btn_x"}, load_json_fixture("buttons.json")["data"][0]]},
            "name",
            ["Outside"],
        ),
        (
            "async_get_learners",
            "learners",
            {"data": [{"id": 5}, load_json_fixture("learners.json")["data"][0]]},
            "name",
            ["Test Learner"],
        ),
    ],
)
async def test_list_skips_invalid_items(
    client: FluentPetClient,
    mocker_aiohttp: AiohttpClientMocker,
    method: str,
    path: str,
    body: dict[str, Any],
    attribute: str,
    expected: list[str],
) -> None:
    """With on_invalid, malformed items are skipped and reported; without, they fail."""
    mocker_aiohttp.get(f"{API}/{HH}/{path}", json=body)
    errors: list[FluentPetParseError] = []
    items = await getattr(client, method)(HH, on_invalid=errors.append)
    assert [getattr(item, attribute) for item in items] == expected
    assert len(errors) == len(body["data"]) - 1
    with pytest.raises(FluentPetParseError):
        await getattr(client, method)(HH)


async def test_get_events_skips_invalid_events(
    client: FluentPetClient, mocker_aiohttp: AiohttpClientMocker
) -> None:
    """With on_invalid, malformed events are skipped and the cursor still moves."""
    page = load_json_fixture("events_page.json")
    page["data"].insert(0, {"id": "00000000-0000-4000-8000-0000000000ff"})
    page["data"].insert(1, "not an object")
    mocker_aiohttp.get(f"{API}/{HH}/events", json=page)
    errors: list[FluentPetParseError] = []
    result = await client.async_get_events(HH, since="c0", on_invalid=errors.append)
    assert [event.type for event in result.events] == [
        "press.created",
        "interaction.updated",
    ]
    assert result.next_cursor == page["next_cursor"]
    assert len(errors) == 2
    with pytest.raises(FluentPetParseError):
        await client.async_get_events(HH, since="c0")
