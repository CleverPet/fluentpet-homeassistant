"""Tests for the FluentPet Connect webhook."""

import copy
from dataclasses import replace
from datetime import timedelta
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.fluentpet.const import (
    CONF_CLOUDHOOK,
    CONF_PREFER_CLOUDHOOK,
    CONF_WEBHOOK_ENDPOINT_ID,
    CONF_WEBHOOK_SECRET,
    CONF_WEBHOOK_URL,
    DOMAIN,
    SAFETY_POLL_INTERVAL,
    SLOW_RETRY_MIN,
)
from custom_components.fluentpet.coordinator import not_admin_issue_id
from custom_components.fluentpet.fluentpet_api import (
    Event,
    EventPage,
    FluentPetAuthError,
    FluentPetConnectionError,
    FluentPetForbiddenError,
    FluentPetNotFoundError,
    FluentPetParseError,
    FluentPetRequestError,
)
from custom_components.fluentpet.webhook import webhook_issue_id, webhook_limit_issue_id
from homeassistant.components.webhook import async_handle_webhook
from homeassistant.const import (
    EVENT_HOMEASSISTANT_FINAL_WRITE,
    EVENT_HOMEASSISTANT_STOP,
    EVENT_STATE_CHANGED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import OAuth2TokenRequestConnectionError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from homeassistant.util.aiohttp import MockRequest, MockStreamReader, serialize_response

from .common import SECRET, load_json_fixture, setup_integration, signed_headers
from .conftest import (
    CLOUDHOOK_URL,
    EXTERNAL_URL,
    HOUSEHOLD_ID,
    WEBHOOK_ID,
    WEBHOOK_URL,
    webhook,
)

ENDPOINT_ID = "00000000-0000-4000-8000-000000009001"
WEBHOOK_PATH = f"/api/webhook/{WEBHOOK_ID}"
PRESS_ENTITY = "event.outside_press"
SENSOR_ENTITY = "sensor.test_household_last_interaction"


@pytest.fixture
def registered_entry_data(entry_data: dict[str, Any]) -> dict[str, Any]:
    """Return entry data with a registered FluentPet webhook."""
    return {
        **entry_data,
        CONF_WEBHOOK_ENDPOINT_ID: ENDPOINT_ID,
        CONF_WEBHOOK_SECRET: SECRET,
        CONF_WEBHOOK_URL: WEBHOOK_URL,
        CONF_CLOUDHOOK: False,
    }


@pytest.fixture
def registered_entry(registered_entry_data: dict[str, Any]) -> MockConfigEntry:
    """Return a config entry with a registered webhook."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Household",
        unique_id=HOUSEHOLD_ID,
        data=registered_entry_data,
    )


def _body(name: str, **overrides: Any) -> bytes:
    """Return the JSON body of an event fixture, stored by FluentPet just now."""
    return json.dumps(
        {
            **load_json_fixture(f"events/{name}.json"),
            "created_at": dt_util.utcnow().isoformat(),
            **overrides,
        }
    ).encode()


async def _refresh(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Move time past the refresh interval."""
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


# URL selection and registration


@pytest.mark.usefixtures("external_url")
async def test_create_with_external_url(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A new entry registers its external https URL."""
    await setup_integration(hass, mock_config_entry)
    mock_client.async_create_webhook.assert_awaited_once_with(HOUSEHOLD_ID, WEBHOOK_URL)
    assert mock_config_entry.data[CONF_WEBHOOK_ENDPOINT_ID] == ENDPOINT_ID
    assert mock_config_entry.data[CONF_WEBHOOK_SECRET] == SECRET
    assert mock_config_entry.data[CONF_WEBHOOK_URL] == WEBHOOK_URL
    assert mock_config_entry.data[CONF_CLOUDHOOK] is False
    assert mock_config_entry.runtime_data.webhook.active


@pytest.mark.usefixtures("external_url")
async def test_create_with_cloudhook(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_cloud: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An active cloud subscription registers a cloudhook."""
    await setup_integration(hass, mock_config_entry)
    mock_cloud.async_get_or_create_cloudhook.assert_awaited_once_with(hass, WEBHOOK_ID)
    mock_client.async_create_webhook.assert_awaited_once_with(
        HOUSEHOLD_ID, CLOUDHOOK_URL
    )
    assert mock_config_entry.data[CONF_CLOUDHOOK] is True
    assert mock_config_entry.runtime_data.webhook.uses_cloudhook


@pytest.mark.usefixtures("external_url")
async def test_cloud_not_connected_falls_back(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_cloud: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A disconnected cloud falls back to the external URL."""
    mock_cloud.async_get_or_create_cloudhook.side_effect = mock_cloud.CloudNotConnected
    await setup_integration(hass, mock_config_entry)
    mock_client.async_create_webhook.assert_awaited_once_with(HOUSEHOLD_ID, WEBHOOK_URL)
    assert mock_config_entry.data[CONF_CLOUDHOOK] is False


@pytest.mark.usefixtures("external_url")
async def test_cloudhook_not_preferred(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_cloud: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """With prefer_cloudhook off the external URL is used."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_PREFER_CLOUDHOOK: False}
    )
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    mock_cloud.async_get_or_create_cloudhook.assert_not_awaited()
    mock_client.async_create_webhook.assert_awaited_once_with(HOUSEHOLD_ID, WEBHOOK_URL)


@pytest.mark.parametrize("url", [None, "http://hooks.example.com"])
async def test_no_https_url_polls(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    url: str | None,
) -> None:
    """Without an external https URL no webhook is registered and the feed is read."""
    if url is not None:
        await hass.config.async_update(external_url=url)
    await setup_integration(hass, mock_config_entry)
    mock_client.async_create_webhook.assert_not_awaited()
    assert not mock_config_entry.runtime_data.webhook.active
    assert mock_client.async_get_events.await_count == 1
    await _refresh(hass, freezer)
    assert mock_client.async_get_events.await_count == 2


@pytest.mark.usefixtures("external_url")
async def test_existing_webhook_unchanged(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A matching webhook is left alone; the safety poll still reads the feed."""
    await setup_integration(hass, registered_entry)
    mock_client.async_get_webhook.assert_awaited_once_with(HOUSEHOLD_ID, ENDPOINT_ID)
    mock_client.async_create_webhook.assert_not_awaited()
    mock_client.async_update_webhook.assert_not_awaited()
    assert mock_client.async_get_events.await_count == 1
    await _refresh(hass, freezer)
    assert mock_client.async_get_events.await_count == 1
    await _wait(hass, freezer, SAFETY_POLL_INTERVAL)
    assert mock_client.async_get_events.await_count == 2
    mock_client.async_update_webhook.assert_not_awaited()


@pytest.mark.usefixtures("external_url")
async def test_missing_webhook_recreated(
    hass: HomeAssistant, mock_client: AsyncMock, registered_entry: MockConfigEntry
) -> None:
    """A webhook deleted on the FluentPet side is created again."""
    mock_client.async_get_webhook.return_value = None
    await setup_integration(hass, registered_entry)
    mock_client.async_create_webhook.assert_awaited_once_with(HOUSEHOLD_ID, WEBHOOK_URL)


@pytest.mark.usefixtures("external_url")
@pytest.mark.parametrize(
    "remote",
    [
        {"url": "https://hooks.example.com/old/api/webhook/test-webhook-id"},
        {"enabled": False},
    ],
)
async def test_webhook_patched(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    remote: dict[str, Any],
) -> None:
    """A webhook with another URL or disabled by the user is updated."""
    mock_client.async_get_webhook.return_value = webhook(**remote)
    await setup_integration(hass, registered_entry)
    mock_client.async_update_webhook.assert_awaited_once_with(
        HOUSEHOLD_ID, ENDPOINT_ID, url=WEBHOOK_URL, enabled=True
    )
    assert registered_entry.runtime_data.webhook.active


@pytest.mark.usefixtures("external_url")
async def test_auto_disabled_webhook(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An auto-disabled webhook raises a repair issue and the feed is read."""
    mock_client.async_get_webhook.return_value = webhook(
        enabled=False, disabled_at="2026-10-16T08:00:00.000Z", failure_count=20
    )
    await setup_integration(hass, registered_entry)
    manager = registered_entry.runtime_data.webhook
    assert manager.auto_disabled
    assert not manager.active
    issue = issue_registry.async_get_issue(
        DOMAIN, webhook_issue_id(registered_entry.entry_id)
    )
    assert issue is not None
    assert issue.translation_key == "webhook_disabled"
    assert issue.translation_placeholders == {
        "household": "Test Household",
        "url": "https://my.fluent.pet",
    }
    mock_client.async_update_webhook.assert_not_awaited()

    await _refresh(hass, freezer)
    assert mock_client.async_get_events.await_count == 2
    assert mock_client.async_get_webhook.await_count == 2

    mock_client.async_get_webhook.return_value = webhook()
    await _refresh(hass, freezer)
    assert manager.active
    assert not manager.auto_disabled
    assert (
        issue_registry.async_get_issue(
            DOMAIN, webhook_issue_id(registered_entry.entry_id)
        )
        is None
    )
    # Enabled again: the safety poll replaces the refresh interval.
    await _refresh(hass, freezer)
    assert mock_client.async_get_events.await_count == 3
    await _wait(hass, freezer, SAFETY_POLL_INTERVAL)
    assert mock_client.async_get_events.await_count == 4


@pytest.mark.usefixtures("external_url")
@pytest.mark.parametrize(
    "error",
    [
        FluentPetRequestError(422, "unprocessable", "url is not reachable"),
        FluentPetForbiddenError(403, "forbidden", None),
    ],
)
async def test_webhook_refused(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    """A refused webhook falls back to the feed and is tried again slowly."""
    created = mock_client.async_create_webhook.return_value
    mock_client.async_create_webhook.side_effect = error
    await setup_integration(hass, mock_config_entry)
    assert not mock_config_entry.runtime_data.webhook.active
    assert caplog.text.count("FluentPet refused the webhook") == 1
    await _refresh(hass, freezer)
    assert mock_client.async_create_webhook.await_count == 1
    assert mock_client.async_get_events.await_count == 2

    await _wait(hass, freezer, timedelta(minutes=5))
    assert mock_client.async_create_webhook.await_count == 2
    assert caplog.text.count("FluentPet refused the webhook") == 1
    await _wait(hass, freezer, timedelta(minutes=5))
    assert mock_client.async_create_webhook.await_count == 2

    mock_client.async_create_webhook.side_effect = None
    mock_client.async_create_webhook.return_value = created
    await _wait(hass, freezer, timedelta(minutes=5))
    assert mock_client.async_create_webhook.await_count == 3
    assert mock_config_entry.runtime_data.webhook.active
    assert "FluentPet accepted the webhook again" in caplog.text


@pytest.mark.usefixtures("external_url")
async def test_webhook_limit_issue(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A household at its webhook limit raises a repair issue until a slot is free."""
    created = mock_client.async_create_webhook.return_value
    mock_client.async_create_webhook.side_effect = FluentPetRequestError(
        422,
        "unprocessable entity",
        "Validation failed: A household can have at most 5 webhooks",
    )
    await setup_integration(hass, mock_config_entry)
    issue_id = webhook_limit_issue_id(mock_config_entry.entry_id)
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "webhook_limit"
    assert issue.translation_placeholders == {
        "household": "Test Household",
        "url": "https://my.fluent.pet",
    }

    mock_client.async_create_webhook.side_effect = None
    mock_client.async_create_webhook.return_value = created
    await _wait(hass, freezer, timedelta(minutes=5))
    assert mock_config_entry.runtime_data.webhook.active
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.usefixtures("external_url")
async def test_other_refusal_raises_no_limit_issue(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A refusal for another reason raises no webhook limit issue."""
    mock_client.async_create_webhook.side_effect = FluentPetRequestError(
        422, "unprocessable entity", "Validation failed: Url must use https"
    )
    await setup_integration(hass, mock_config_entry)
    issue_id = webhook_limit_issue_id(mock_config_entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.usefixtures("external_url")
async def test_webhook_forbidden_raises_not_admin_issue(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A 403 for the webhook raises the not-admin issue until access returns."""
    mock_client.async_get_webhook.side_effect = FluentPetForbiddenError(
        403, "not allowed", None
    )
    await setup_integration(hass, registered_entry)
    issue_id = not_admin_issue_id(registered_entry.entry_id)
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "not_admin"
    assert issue.translation_placeholders == {"household": "Test Household"}
    assert registered_entry.runtime_data.webhook.forbidden

    mock_client.async_get_webhook.side_effect = None
    await _wait(hass, freezer, timedelta(minutes=5))
    assert registered_entry.runtime_data.webhook.active
    assert not registered_entry.runtime_data.webhook.forbidden
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.usefixtures("external_url")
@pytest.mark.parametrize(
    "error",
    [
        FluentPetConnectionError("boom"),
        OAuth2TokenRequestConnectionError(domain=DOMAIN),
    ],
)
async def test_webhook_retried_after_connection_error(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    error: Exception,
) -> None:
    """A connection or token refresh error is retried on the next refresh."""
    created = mock_client.async_create_webhook.return_value
    mock_client.async_create_webhook.side_effect = error
    await setup_integration(hass, mock_config_entry)
    assert not mock_config_entry.runtime_data.webhook.active

    mock_client.async_create_webhook.side_effect = None
    mock_client.async_create_webhook.return_value = created
    await _refresh(hass, freezer)
    assert mock_client.async_create_webhook.await_count == 2
    assert mock_config_entry.runtime_data.webhook.active


@pytest.mark.usefixtures("external_url")
async def test_cloudhook_change_patches_url(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_cloud: MagicMock,
    registered_entry_data: dict[str, Any],
) -> None:
    """A changed cloudhook URL is sent to FluentPet."""
    new_url = "https://hooks.example.com/cloudhook/mock-new-cloudhook-id"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Household",
        unique_id=HOUSEHOLD_ID,
        data={
            **registered_entry_data,
            CONF_WEBHOOK_URL: CLOUDHOOK_URL,
            CONF_CLOUDHOOK: True,
        },
    )
    mock_client.async_get_webhook.return_value = webhook(url=CLOUDHOOK_URL)
    await setup_integration(hass, entry)
    mock_client.async_update_webhook.assert_not_awaited()

    (listener,) = mock_cloud.listeners
    listener({"cloudhook_url": CLOUDHOOK_URL})
    await hass.async_block_till_done()
    mock_client.async_update_webhook.assert_not_awaited()

    mock_cloud.async_get_or_create_cloudhook.return_value = new_url
    listener({"cloudhook_url": new_url})
    await hass.async_block_till_done()
    mock_client.async_update_webhook.assert_awaited_once_with(
        HOUSEHOLD_ID, ENDPOINT_ID, url=new_url, enabled=True
    )
    assert entry.data[CONF_WEBHOOK_URL] == new_url


async def test_cloudhook_change_ignored_without_cloudhook(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_cloud: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Cloudhook changes do nothing when the webhook does not use the cloud."""
    mock_cloud.async_get_or_create_cloudhook.side_effect = mock_cloud.CloudNotAvailable
    await setup_integration(hass, mock_config_entry)
    (listener,) = mock_cloud.listeners
    listener(None)
    await hass.async_block_till_done()
    mock_client.async_update_webhook.assert_not_awaited()
    mock_client.async_create_webhook.assert_not_awaited()


@pytest.mark.usefixtures("external_url")
async def test_cloudhook_change_auth_error_starts_reauth(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_cloud: MagicMock,
    registered_entry_data: dict[str, Any],
) -> None:
    """An auth error while updating after a cloudhook change starts reauth."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=HOUSEHOLD_ID,
        data={
            **registered_entry_data,
            CONF_WEBHOOK_URL: CLOUDHOOK_URL,
            CONF_CLOUDHOOK: True,
        },
    )
    mock_client.async_get_webhook.return_value = webhook(url=CLOUDHOOK_URL)
    await setup_integration(hass, entry)
    mock_client.async_get_webhook.side_effect = FluentPetAuthError(
        401, "unauthorized", None
    )
    (listener,) = mock_cloud.listeners
    listener(None)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flows
    assert flows[0]["context"]["source"] == "reauth"


# Handler


async def _post(
    hass_client_no_auth: ClientSessionGenerator,
    body: bytes,
    headers: dict[str, str],
) -> int:
    """Post a webhook request and return the status."""
    client = await hass_client_no_auth()
    response = await client.post(WEBHOOK_PATH, data=body, headers=headers)
    return response.status


@pytest.mark.usefixtures("external_url")
async def test_press_delivery(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
) -> None:
    """A signed press.created fires the button's event entity."""
    await setup_integration(hass, registered_entry)
    assert hass.states.get(PRESS_ENTITY).state == STATE_UNKNOWN

    body = _body("press_created")
    assert await _post(hass_client_no_auth, body, signed_headers(body)) == 200
    await hass.async_block_till_done()

    state = hass.states.get(PRESS_ENTITY)
    assert state.state != STATE_UNKNOWN
    assert state.attributes["event_type"] == "press"
    assert state.attributes["button"] == {
        "name": "Outside",
        "normalized_word": "outside",
    }
    assert state.attributes["pressed_by"] == {
        "name": "Test Learner",
        "type": "learner",
    }
    assert state.attributes["press_order"] == 0
    assert state.attributes["occurred_at"] == "2026-10-17T11:59:58.412000+00:00"
    assert state.attributes["interaction_id"] == "int_testinteractionaaaaaaaaaaa"
    # Delivered by the webhook: live, although it happened minutes ago.
    assert state.attributes["replayed"] is False
    assert registered_entry.runtime_data.webhook.last_delivery is not None


@pytest.mark.usefixtures("external_url")
async def test_duplicate_delivery_fires_once(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
) -> None:
    """A retried delivery and a repeated event id fire once."""
    await setup_integration(hass, registered_entry)
    events = async_capture_events(hass, EVENT_STATE_CHANGED)
    body = _body("press_created")
    assert await _post(hass_client_no_auth, body, signed_headers(body)) == 200
    assert await _post(hass_client_no_auth, body, signed_headers(body)) == 200
    other_delivery = signed_headers(
        body, delivery="00000000-0000-4000-8000-00000000d002"
    )
    assert await _post(hass_client_no_auth, body, other_delivery) == 200
    await hass.async_block_till_done()
    fired = [
        event.data["entity_id"]
        for event in events
        if event.data["entity_id"] == PRESS_ENTITY
    ]
    assert fired == [PRESS_ENTITY]


@pytest.mark.usefixtures("external_url")
async def test_interaction_delivery(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
) -> None:
    """A signed interaction.updated updates the last interaction sensor."""
    await setup_integration(hass, registered_entry)
    body = _body("interaction_updated")
    headers = signed_headers(body, event_type="interaction.updated")
    assert await _post(hass_client_no_auth, body, headers) == 200
    await hass.async_block_till_done()
    state = hass.states.get(SENSOR_ENTITY)
    assert state.state == "Outside Play"
    assert state.attributes["press_count"] == 3


@pytest.mark.usefixtures("external_url")
async def test_lifecycle_delivery_refreshes(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
) -> None:
    """A lifecycle event refreshes the household."""
    await setup_integration(hass, registered_entry)
    assert mock_client.async_get_buttons.await_count == 1
    body = _body("button_added")
    headers = signed_headers(body, event_type="button.added")
    assert await _post(hass_client_no_auth, body, headers) == 200
    await hass.async_block_till_done()
    assert mock_client.async_get_buttons.await_count == 2


@pytest.mark.usefixtures("external_url")
@pytest.mark.parametrize(
    ("fixture", "event_type", "entity_id", "expected"),
    [
        ("base_updated", "base.updated", "sensor.test_base_battery", "25"),
        ("button_updated", "button.updated", "sensor.outside_battery", "25"),
    ],
)
async def test_battery_delivery_updates_sensor(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    fixture: str,
    event_type: str,
    entity_id: str,
    expected: str,
) -> None:
    """A delivered battery change in push mode updates the sensor right away.

    The delivery reads the resources once, without waiting for the safety poll.
    """
    await setup_integration(hass, registered_entry)
    await _wait(hass, freezer, SAFETY_POLL_INTERVAL)
    assert registered_entry.runtime_data.coordinator.push_active
    first, second = mock_client.async_get_bases.return_value
    mock_client.async_get_bases.return_value = [
        replace(first, battery_level=25, power_mode="battery"),
        second,
    ]
    outside, *rest = mock_client.async_get_buttons.return_value
    mock_client.async_get_buttons.return_value = [
        replace(outside, battery_level=25),
        *rest,
    ]
    buttons_read = mock_client.async_get_buttons.await_count
    body = _body(fixture)
    headers = signed_headers(body, event_type=event_type)
    assert await _post(hass_client_no_auth, body, headers) == 200
    await hass.async_block_till_done()
    assert mock_client.async_get_buttons.await_count == buttons_read + 1
    assert hass.states.get(entity_id).state == expected


async def _deliver_cloudhook(
    hass: HomeAssistant, body: bytes, headers: dict[str, str]
) -> int:
    """Deliver a webhook request the way Home Assistant Cloud does; return the status.

    Home Assistant Cloud relays the body as text and hands it to the webhook
    component as a MockRequest.
    """
    request = MockRequest(
        content=body.decode().encode("utf-8"),
        headers=headers,
        method="POST",
        query_string="",
        mock_source="cloud",
        remote=None,
    )
    response = await async_handle_webhook(hass, WEBHOOK_ID, request)
    # Home Assistant Cloud serializes the response to relay it.
    return int(serialize_response(response)["status"])


@pytest.mark.usefixtures("external_url", "mock_cloud")
async def test_cloudhook_press_delivery(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    cloudhook_entry: MockConfigEntry,
) -> None:
    """A press delivered through Home Assistant Cloud fires the event entity."""
    await setup_integration(hass, cloudhook_entry)
    body = _body("press_created")
    assert await _deliver_cloudhook(hass, body, signed_headers(body)) == 200
    await hass.async_block_till_done()
    state = hass.states.get(PRESS_ENTITY)
    assert state.state != STATE_UNKNOWN
    assert state.attributes["interaction_id"] == "int_testinteractionaaaaaaaaaaa"
    assert state.attributes["replayed"] is False
    assert cloudhook_entry.runtime_data.webhook.last_delivery is not None


@pytest.mark.usefixtures("external_url", "mock_cloud")
async def test_cloudhook_battery_delivery_updates_sensor(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    cloudhook_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A battery change delivered through Home Assistant Cloud updates the sensor."""
    await setup_integration(hass, cloudhook_entry)
    await _wait(hass, freezer, SAFETY_POLL_INTERVAL)
    assert cloudhook_entry.runtime_data.coordinator.push_active
    first, second = mock_client.async_get_bases.return_value
    mock_client.async_get_bases.return_value = [
        replace(first, battery_level=25, power_mode="battery"),
        second,
    ]
    body = _body("base_updated")
    headers = signed_headers(body, event_type="base.updated")
    assert await _deliver_cloudhook(hass, body, headers) == 200
    await hass.async_block_till_done()
    assert hass.states.get("sensor.test_base_battery").state == "25"
    assert cloudhook_entry.runtime_data.webhook.last_delivery is not None


@pytest.mark.usefixtures("external_url", "mock_cloud")
async def test_cloudhook_non_ascii_delivery(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    cloudhook_entry: MockConfigEntry,
) -> None:
    """A UTF-8 body relayed as text by Home Assistant Cloud keeps its signature."""
    await setup_integration(hass, cloudhook_entry)
    event = json.loads(_body("press_created"))
    event["payload"]["button"]["name"] = "Café 🐕"
    body = json.dumps(event, ensure_ascii=False).encode()
    assert not body.isascii()
    assert await _deliver_cloudhook(hass, body, signed_headers(body)) == 200
    await hass.async_block_till_done()
    assert hass.states.get(PRESS_ENTITY).attributes["button"]["name"] == "Café 🐕"


@pytest.mark.usefixtures("external_url", "mock_cloud")
async def test_cloudhook_unsigned_delivery_rejected(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    cloudhook_entry: MockConfigEntry,
) -> None:
    """A cloudhook delivery with a wrong signature is refused with 401."""
    await setup_integration(hass, cloudhook_entry)
    body = _body("press_created")
    headers = signed_headers(body, secret="whsec_" + "1" * 64)
    assert await _deliver_cloudhook(hass, body, headers) == 401
    await hass.async_block_till_done()
    assert hass.states.get(PRESS_ENTITY).state == STATE_UNKNOWN
    assert cloudhook_entry.runtime_data.webhook.last_delivery is None


@pytest.mark.usefixtures("external_url", "mock_cloud")
async def test_cloudhook_unreadable_body_rejected(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    cloudhook_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cloudhook body that cannot be read is answered with 400 and a warning."""
    await setup_integration(hass, cloudhook_entry)
    body = _body("press_created")
    with patch.object(MockStreamReader, "read", side_effect=OSError("broken")):
        assert await _deliver_cloudhook(hass, body, signed_headers(body)) == 400
    await hass.async_block_till_done()
    assert hass.states.get(PRESS_ENTITY).state == STATE_UNKNOWN
    assert cloudhook_entry.runtime_data.webhook.last_delivery is None
    assert (
        "Could not read the body of FluentPet webhook delivery "
        "00000000-0000-4000-8000-00000000d001: broken"
    ) in caplog.text
    assert "Error processing webhook" not in caplog.text


@pytest.mark.usefixtures("external_url")
async def test_undecodable_body_rejected(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A direct request whose body cannot be decoded is answered with 400."""
    await setup_integration(hass, registered_entry)
    body = b"not gzip"
    headers = {**signed_headers(body), "Content-Encoding": "gzip"}
    assert await _post(hass_client_no_auth, body, headers) == 400
    await hass.async_block_till_done()
    assert registered_entry.runtime_data.webhook.last_delivery is None
    assert (
        "Could not read the body of FluentPet webhook delivery "
        "00000000-0000-4000-8000-00000000d001: 400, message: "
        "Can not decode content-encoding: gzip"
    ) in caplog.text
    assert "Error processing webhook" not in caplog.text


@pytest.mark.usefixtures("external_url")
async def test_oversized_body_rejected(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A direct request over the server's body size limit is answered with 413."""
    await setup_integration(hass, registered_entry)
    body = _body("press_created")
    with patch.object(hass.http.app, "_client_max_size", 64):
        assert await _post(hass_client_no_auth, body, signed_headers(body)) == 413
    await hass.async_block_till_done()
    assert hass.states.get(PRESS_ENTITY).state == STATE_UNKNOWN
    assert registered_entry.runtime_data.webhook.last_delivery is None
    assert (
        "Rejected FluentPet webhook delivery 00000000-0000-4000-8000-00000000d001: "
        "Maximum request body size 64 exceeded"
    ) in caplog.text
    assert "Error processing webhook" not in caplog.text


@pytest.mark.usefixtures("external_url")
async def test_disconnected_body_rejected(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A direct request whose sender disconnected mid-body is answered with 400.

    A disconnect cannot be staged with the test client, so the read is mocked.
    """
    await setup_integration(hass, registered_entry)
    body = _body("press_created")
    request = MagicMock(spec=web.Request)
    request.method = "POST"
    request.remote = "203.0.113.1"
    request.headers = signed_headers(body)
    request.read = AsyncMock(side_effect=ConnectionResetError("reset"))
    response = await async_handle_webhook(hass, WEBHOOK_ID, request)
    assert response.status == 400
    await hass.async_block_till_done()
    assert hass.states.get(PRESS_ENTITY).state == STATE_UNKNOWN
    assert registered_entry.runtime_data.webhook.last_delivery is None
    assert (
        "Could not read the body of FluentPet webhook delivery "
        "00000000-0000-4000-8000-00000000d001: reset"
    ) in caplog.text
    assert "Error processing webhook" not in caplog.text


@pytest.mark.usefixtures("external_url")
async def test_lifecycle_delivery_in_push_mode_refreshes_once(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A pushed base.updated reads the resources; its feed copy reads nothing."""
    await setup_integration(hass, registered_entry)
    await _wait(hass, freezer, SAFETY_POLL_INTERVAL)
    coordinator = registered_entry.runtime_data.coordinator
    assert coordinator.push_active
    assert mock_client.async_get_bases.await_count == 2

    body = _body("base_updated")
    headers = signed_headers(body, event_type="base.updated")
    assert await _post(hass_client_no_auth, body, headers) == 200
    await hass.async_block_till_done()
    assert mock_client.async_get_bases.await_count == 3
    assert mock_client.async_get_buttons.await_count == 3
    assert mock_client.async_get_learners.await_count == 3

    mock_client.async_get_events.side_effect = [
        EventPage((Event.from_dict(json.loads(body)),), "AAAAAAAAAAtestcursor00000014")
    ]
    await _wait(hass, freezer, SAFETY_POLL_INTERVAL)
    # Setup, the first safety poll, the refresh after the delivery, this poll,
    # which reads the Bases but not the buttons again.
    assert mock_client.async_get_events.await_count == 4
    assert mock_client.async_get_bases.await_count == 4
    assert mock_client.async_get_buttons.await_count == 3


@pytest.mark.usefixtures("external_url")
async def test_ping_delivery(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
) -> None:
    """A ping is answered and changes nothing."""
    await setup_integration(hass, registered_entry)
    body = _body("ping")
    headers = signed_headers(body, event_type="ping")
    assert await _post(hass_client_no_auth, body, headers) == 200
    await hass.async_block_till_done()
    assert mock_client.async_get_buttons.await_count == 1


@pytest.mark.usefixtures("external_url")
async def test_unknown_event_type_delivery(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
) -> None:
    """An event type this version does not know is accepted and ignored."""
    await setup_integration(hass, registered_entry)
    body = _body("ping", type="learner.trained")
    assert await _post(hass_client_no_auth, body, signed_headers(body)) == 200


@pytest.mark.usefixtures("external_url")
async def test_press_without_button_ignored(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
) -> None:
    """A press without a button is accepted and ignored."""
    await setup_integration(hass, registered_entry)
    event = json.loads(_body("press_created"))
    event["payload"]["button"] = None
    body = json.dumps(event).encode()
    assert await _post(hass_client_no_auth, body, signed_headers(body)) == 200
    await hass.async_block_till_done()
    assert hass.states.get(PRESS_ENTITY).state == STATE_UNKNOWN


def _assert_not_delivered(entry: MockConfigEntry, failure_count: int) -> None:
    """Assert a refused request left the delivery time and the mode alone."""
    assert entry.runtime_data.webhook.last_delivery is None
    assert entry.runtime_data.coordinator.push_active is (failure_count == 0)


@pytest.mark.usefixtures("external_url")
@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="missing"),
        pytest.param(
            {"X-FluentPet-Timestamp": "1", "X-FluentPet-Signature": "sha256=00"},
            id="bad_signature",
        ),
    ],
)
@pytest.mark.parametrize("failure_count", [0, 3])
async def test_unsigned_delivery_rejected(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
    headers: dict[str, str],
    failure_count: int,
) -> None:
    """Missing or wrong signatures are refused without logging the body.

    A refused request does not count as a delivery for the health rule.
    """
    mock_client.async_get_webhook.return_value = webhook(failure_count=failure_count)
    await setup_integration(hass, registered_entry)
    body = _body("press_created")
    assert await _post(hass_client_no_auth, body, headers) == 401
    await hass.async_block_till_done()
    _assert_not_delivered(registered_entry, failure_count)
    assert hass.states.get(PRESS_ENTITY).state == STATE_UNKNOWN
    assert "Rejected a FluentPet webhook request" in caplog.text
    assert "btn_testbuttonoutside" not in caplog.text


@pytest.mark.usefixtures("external_url")
@pytest.mark.parametrize("failure_count", [0, 3])
async def test_wrong_secret_rejected(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    failure_count: int,
) -> None:
    """A body signed with another secret is refused and is no delivery."""
    mock_client.async_get_webhook.return_value = webhook(failure_count=failure_count)
    await setup_integration(hass, registered_entry)
    body = _body("press_created")
    headers = signed_headers(body, secret="whsec_" + "1" * 64)
    assert await _post(hass_client_no_auth, body, headers) == 401
    await hass.async_block_till_done()
    _assert_not_delivered(registered_entry, failure_count)


@pytest.mark.usefixtures("external_url")
@pytest.mark.parametrize("failure_count", [0, 3])
async def test_stale_delivery_rejected(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    failure_count: int,
) -> None:
    """A correctly signed but old delivery is refused and is no delivery."""
    mock_client.async_get_webhook.return_value = webhook(failure_count=failure_count)
    await setup_integration(hass, registered_entry)
    body = _body("press_created")
    headers = signed_headers(body, timestamp=int(time.time()) - 600)
    assert await _post(hass_client_no_auth, body, headers) == 401
    await hass.async_block_till_done()
    assert hass.states.get(PRESS_ENTITY).state == STATE_UNKNOWN
    _assert_not_delivered(registered_entry, failure_count)


@pytest.mark.usefixtures("external_url")
async def test_invalid_body_rejected(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
) -> None:
    """A signed body that is not an event is answered with 400."""
    await setup_integration(hass, registered_entry)
    body = b"not json"
    assert await _post(hass_client_no_auth, body, signed_headers(body)) == 400


async def test_delivery_without_secret_rejected(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Without a stored secret every delivery is refused."""
    await setup_integration(hass, mock_config_entry)
    body = _body("press_created")
    assert await _post(hass_client_no_auth, body, signed_headers(body)) == 401


@pytest.mark.usefixtures("external_url")
async def test_webhook_unregistered_on_unload(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
) -> None:
    """Unloading the entry unregisters the local webhook."""
    await setup_integration(hass, registered_entry)
    assert await hass.config_entries.async_unload(registered_entry.entry_id)
    await hass.async_block_till_done()
    body = _body("press_created")
    client = await hass_client_no_auth()
    response = await client.post(WEBHOOK_PATH, data=body, headers=signed_headers(body))
    assert response.status == 200
    mock_client.async_delete_webhook.assert_not_awaited()


async def test_webhook_id_generated(
    hass: HomeAssistant, mock_client: AsyncMock, entry_data: dict[str, Any]
) -> None:
    """An entry without a webhook id gets one."""
    data = dict(entry_data)
    del data["webhook_id"]
    entry = MockConfigEntry(domain=DOMAIN, unique_id=HOUSEHOLD_ID, data=data)
    await setup_integration(hass, entry)
    assert entry.data["webhook_id"]


# Stale endpoints and the cloudhook lifecycle


@pytest.fixture
def cloudhook_entry(registered_entry_data: dict[str, Any]) -> MockConfigEntry:
    """Return a config entry whose webhook uses a cloudhook."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Household",
        unique_id=HOUSEHOLD_ID,
        data={
            **registered_entry_data,
            CONF_WEBHOOK_URL: CLOUDHOOK_URL,
            CONF_CLOUDHOOK: True,
        },
    )


def _assert_endpoint_forgotten(entry: MockConfigEntry) -> None:
    """Assert the entry no longer stores a FluentPet webhook."""
    for key in (
        CONF_WEBHOOK_ENDPOINT_ID,
        CONF_WEBHOOK_SECRET,
        CONF_WEBHOOK_URL,
        CONF_CLOUDHOOK,
    ):
        assert key not in entry.data
    assert entry.data["webhook_id"] == WEBHOOK_ID


async def _wait(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    """Move time on and run the refresh that is due."""
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


@pytest.mark.parametrize("url", [None, "http://hooks.example.com"])
@pytest.mark.parametrize(
    "delete_error", [None, FluentPetNotFoundError(404, "not found", None)]
)
async def test_stored_endpoint_deleted_without_url(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    url: str | None,
    delete_error: Exception | None,
) -> None:
    """Without a usable URL the stored webhook is kept at first, deleted on a re-check."""
    if url is not None:
        await hass.config.async_update(external_url=url)
    mock_client.async_delete_webhook.side_effect = delete_error
    await setup_integration(hass, registered_entry)
    mock_client.async_delete_webhook.assert_not_awaited()
    assert registered_entry.data[CONF_WEBHOOK_ENDPOINT_ID] == ENDPOINT_ID
    assert not registered_entry.runtime_data.webhook.active
    # A re-check sooner than the slow retry, for example on a cloud connection,
    # does not delete either.
    await registered_entry.runtime_data.webhook.async_reconcile()
    await _refresh(hass, freezer)
    assert mock_client.async_get_events.await_count == 2
    mock_client.async_delete_webhook.assert_not_awaited()

    await _wait(hass, freezer, timedelta(minutes=5))
    mock_client.async_delete_webhook.assert_awaited_once_with(HOUSEHOLD_ID, ENDPOINT_ID)
    _assert_endpoint_forgotten(registered_entry)
    await _wait(hass, freezer, timedelta(minutes=10))
    assert mock_client.async_delete_webhook.await_count == 1
    mock_client.async_create_webhook.assert_not_awaited()


async def test_stored_endpoint_delete_retried(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failed delete on the re-check is retried on the next refresh."""
    mock_client.async_delete_webhook.side_effect = FluentPetConnectionError("boom")
    await setup_integration(hass, registered_entry)
    await _wait(hass, freezer, timedelta(minutes=5))
    assert mock_client.async_delete_webhook.await_count == 1
    assert registered_entry.data[CONF_WEBHOOK_ENDPOINT_ID] == ENDPOINT_ID

    mock_client.async_delete_webhook.side_effect = None
    await _refresh(hass, freezer)
    assert mock_client.async_delete_webhook.await_count == 2
    _assert_endpoint_forgotten(registered_entry)


async def test_no_url_retried_slowly(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Without a URL the webhook is tried again after 5 minutes, then less often."""
    await setup_integration(hass, mock_config_entry)
    assert caplog.text.count("No external https URL for the FluentPet webhook") == 1
    await hass.config.async_update(external_url=EXTERNAL_URL)
    await _refresh(hass, freezer)
    mock_client.async_create_webhook.assert_not_awaited()

    await _wait(hass, freezer, timedelta(minutes=5))
    mock_client.async_create_webhook.assert_awaited_once_with(HOUSEHOLD_ID, WEBHOOK_URL)
    manager = mock_config_entry.runtime_data.webhook
    assert manager.active
    assert caplog.text.count("No external https URL for the FluentPet webhook") == 1


async def test_no_url_backoff_grows(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The slow retry doubles from 5 minutes up to an hour."""
    await setup_integration(hass, mock_config_entry)
    manager = mock_config_entry.runtime_data.webhook
    previous = dt_util.utcnow()
    delays: list[float] = []
    for _ in range(6):
        scheduled = manager._retry.next_at  # noqa: SLF001
        assert scheduled is not None
        delays.append((scheduled - previous).total_seconds() / 60)
        previous = scheduled
        freezer.move_to(scheduled)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert delays == [5, 10, 20, 40, 60, 60]


async def test_cloudhook_kept_while_cloud_disconnected(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_cloud: MagicMock,
    cloudhook_entry: MockConfigEntry,
) -> None:
    """A stored cloudhook is kept while the cloud is only disconnected."""
    mock_cloud.async_get_or_create_cloudhook.side_effect = mock_cloud.CloudNotConnected
    mock_client.async_get_webhook.return_value = webhook(url=CLOUDHOOK_URL)
    await setup_integration(hass, cloudhook_entry)
    mock_client.async_update_webhook.assert_not_awaited()
    mock_client.async_create_webhook.assert_not_awaited()
    mock_client.async_delete_webhook.assert_not_awaited()
    mock_cloud.async_delete_cloudhook.assert_not_awaited()
    assert cloudhook_entry.data[CONF_WEBHOOK_URL] == CLOUDHOOK_URL
    assert cloudhook_entry.data[CONF_CLOUDHOOK] is True
    assert cloudhook_entry.runtime_data.webhook.active


@pytest.mark.usefixtures("external_url")
@pytest.mark.parametrize("reason", ["not_preferred", "no_subscription"])
@pytest.mark.parametrize("delete_fails", [False, True])
async def test_cloudhook_replaced_by_external_url(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_cloud: MagicMock,
    cloudhook_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    reason: str,
    delete_fails: bool,
) -> None:
    """Leaving the cloudhook moves the webhook to the external URL and deletes it.

    Turning the preference off moves at once; an inactive subscription moves only
    when it is still inactive on a check at least 5 minutes later.
    """
    if reason == "not_preferred":
        cloudhook_entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(
            cloudhook_entry, options={CONF_PREFER_CLOUDHOOK: False}
        )
    else:
        mock_cloud.async_active_subscription.return_value = False
    if delete_fails:
        mock_cloud.async_delete_cloudhook.side_effect = mock_cloud.CloudNotAvailable
    mock_client.async_get_webhook.return_value = webhook(url=CLOUDHOOK_URL)
    await setup_integration(hass, cloudhook_entry)
    if reason == "no_subscription":
        mock_client.async_update_webhook.assert_not_awaited()
        mock_cloud.async_delete_cloudhook.assert_not_awaited()
        assert cloudhook_entry.data[CONF_WEBHOOK_URL] == CLOUDHOOK_URL
        await _wait(hass, freezer, timedelta(minutes=2))
        mock_client.async_update_webhook.assert_not_awaited()
        await _wait(hass, freezer, timedelta(minutes=4))

    mock_client.async_update_webhook.assert_awaited_once_with(
        HOUSEHOLD_ID, ENDPOINT_ID, url=WEBHOOK_URL, enabled=True
    )
    mock_cloud.async_delete_cloudhook.assert_awaited_once_with(hass, WEBHOOK_ID)
    assert cloudhook_entry.data[CONF_WEBHOOK_URL] == WEBHOOK_URL
    assert cloudhook_entry.data[CONF_CLOUDHOOK] is False
    assert cloudhook_entry.runtime_data.webhook.active


async def test_cloudhook_kept_while_subscription_inactive(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_cloud: MagicMock,
    cloudhook_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A signed-in cloud keeps the cloudhook while the subscription reads inactive."""
    mock_cloud.async_active_subscription.return_value = False
    mock_client.async_get_webhook.return_value = webhook(url=CLOUDHOOK_URL)
    await setup_integration(hass, cloudhook_entry)
    await _wait(hass, freezer, timedelta(hours=2))
    mock_client.async_delete_webhook.assert_not_awaited()
    mock_client.async_update_webhook.assert_not_awaited()
    mock_cloud.async_delete_cloudhook.assert_not_awaited()
    assert cloudhook_entry.data[CONF_WEBHOOK_URL] == CLOUDHOOK_URL
    assert cloudhook_entry.runtime_data.webhook.active


@pytest.mark.parametrize("reason", ["signed_out", "not_preferred"])
async def test_cloudhook_removed_without_external_url(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_cloud: MagicMock,
    cloudhook_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    reason: str,
) -> None:
    """Signed out of the cloud, or not preferring it, without an external URL.

    The endpoint and the cloudhook are deleted on the re-check, not at once.
    """
    if reason == "signed_out":
        mock_cloud.async_active_subscription.return_value = False
        mock_cloud.async_is_logged_in.return_value = False
    else:
        cloudhook_entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(
            cloudhook_entry, options={CONF_PREFER_CLOUDHOOK: False}
        )
    await setup_integration(hass, cloudhook_entry)
    mock_client.async_delete_webhook.assert_not_awaited()
    mock_cloud.async_delete_cloudhook.assert_not_awaited()
    assert not cloudhook_entry.runtime_data.webhook.active

    await _wait(hass, freezer, timedelta(minutes=5))
    mock_client.async_delete_webhook.assert_awaited_once_with(HOUSEHOLD_ID, ENDPOINT_ID)
    mock_cloud.async_delete_cloudhook.assert_awaited_once_with(hass, WEBHOOK_ID)
    _assert_endpoint_forgotten(cloudhook_entry)


async def test_cloud_connect_reconciles(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_cloud: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """When Home Assistant Cloud connects, the webhook moves to a cloudhook."""
    mock_cloud.async_get_or_create_cloudhook.side_effect = mock_cloud.CloudNotConnected
    await setup_integration(hass, mock_config_entry)
    mock_client.async_create_webhook.assert_not_awaited()
    (listener,) = mock_cloud.connection_listeners

    listener(mock_cloud.CloudConnectionState.CLOUD_DISCONNECTED)
    await hass.async_block_till_done()
    mock_client.async_create_webhook.assert_not_awaited()

    mock_cloud.async_get_or_create_cloudhook.side_effect = None
    listener(mock_cloud.CloudConnectionState.CLOUD_CONNECTED)
    await hass.async_block_till_done()
    mock_client.async_create_webhook.assert_awaited_once_with(
        HOUSEHOLD_ID, CLOUDHOOK_URL
    )
    assert mock_config_entry.runtime_data.webhook.active
    assert mock_config_entry.data[CONF_CLOUDHOOK] is True


async def test_cloud_connect_ignored_when_not_preferred(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_cloud: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A cloud connection does nothing with prefer_cloudhook off."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_PREFER_CLOUDHOOK: False}
    )
    await setup_integration(hass, mock_config_entry)
    (listener,) = mock_cloud.connection_listeners
    listener(mock_cloud.CloudConnectionState.CLOUD_CONNECTED)
    await hass.async_block_till_done()
    mock_cloud.async_get_or_create_cloudhook.assert_not_awaited()
    mock_client.async_create_webhook.assert_not_awaited()


@pytest.mark.usefixtures("external_url")
async def test_restart_does_not_fire_twice(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A press delivered by webhook and read from the feed after a restart fires once."""
    await setup_integration(hass, registered_entry)
    body = _body("press_created")
    assert await _post(hass_client_no_auth, body, signed_headers(body)) == 200
    await hass.async_block_till_done()
    fired_at = hass.states.get(PRESS_ENTITY).state
    assert fired_at != STATE_UNKNOWN

    assert await hass.config_entries.async_unload(registered_entry.entry_id)
    await hass.async_block_till_done()
    stored = hass_storage[f"{DOMAIN}.{registered_entry.entry_id}"]["data"]
    press_id = load_json_fixture("events/press_created.json")["id"]
    assert stored["event_ids"] == [press_id]

    freezer.tick(timedelta(minutes=5))
    mock_client.async_get_events.side_effect = [
        EventPage((Event.from_dict(json.loads(body)),), "AAAAAAAAAAtestcursor00000014")
    ]
    assert await hass.config_entries.async_setup(registered_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(PRESS_ENTITY).state == fired_at


@pytest.mark.usefixtures("external_url")
async def test_late_delivery_not_processed(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A late retry is acknowledged but not fired; the feed still processes it."""
    await setup_integration(hass, registered_entry)
    stored = (dt_util.utcnow() - timedelta(minutes=11)).isoformat()
    body = _body("press_created", created_at=stored)
    assert await _post(hass_client_no_auth, body, signed_headers(body)) == 200
    await hass.async_block_till_done()
    assert hass.states.get(PRESS_ENTITY).state == STATE_UNKNOWN

    mock_client.async_get_events.side_effect = [
        EventPage((Event.from_dict(json.loads(body)),), "AAAAAAAAAAtestcursor00000014")
    ]
    await _wait(hass, freezer, SAFETY_POLL_INTERVAL)
    assert hass.states.get(PRESS_ENTITY).state != STATE_UNKNOWN


@pytest.mark.usefixtures("external_url")
async def test_unload_stops_webhook_changes(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_cloud: MagicMock,
    registered_entry: MockConfigEntry,
) -> None:
    """A reconcile in flight when unloading starts does not create or patch."""
    await setup_integration(hass, registered_entry)
    mock_client.async_update_webhook.reset_mock()
    manager = registered_entry.runtime_data.webhook
    remotes: list[Any] = [None, webhook(url="https://hooks.example.com/old/x")]

    async def _unload_during_read(household_id: str, endpoint_id: str) -> Any:
        manager.async_unload()
        return remotes.pop(0)

    for _ in range(2):
        manager._shutdown = False  # noqa: SLF001
        mock_client.async_get_webhook.side_effect = _unload_during_read
        await manager.async_reconcile()
    mock_client.async_create_webhook.assert_not_awaited()
    mock_client.async_update_webhook.assert_not_awaited()

    calls = mock_client.async_get_webhook.await_count
    await manager.async_reconcile()
    manager._async_cloud_connection_changed(True)  # noqa: SLF001
    await hass.async_block_till_done()
    assert mock_client.async_get_webhook.await_count == calls


@pytest.mark.usefixtures("external_url")
@pytest.mark.parametrize(
    "delete_error", [None, FluentPetNotFoundError(404, "not found", None)]
)
async def test_endpoint_without_secret_replaced(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    registered_entry_data: dict[str, Any],
    delete_error: Exception | None,
) -> None:
    """An endpoint whose secret is lost is deleted and created again."""
    data = dict(registered_entry_data)
    del data[CONF_WEBHOOK_SECRET]
    entry = MockConfigEntry(domain=DOMAIN, unique_id=HOUSEHOLD_ID, data=data)
    mock_client.async_delete_webhook.side_effect = delete_error
    await setup_integration(hass, entry)
    mock_client.async_get_webhook.assert_not_awaited()
    mock_client.async_delete_webhook.assert_awaited_once_with(HOUSEHOLD_ID, ENDPOINT_ID)
    mock_client.async_create_webhook.assert_awaited_once_with(HOUSEHOLD_ID, WEBHOOK_URL)
    assert entry.data[CONF_WEBHOOK_SECRET] == SECRET
    assert entry.runtime_data.webhook.active


@pytest.mark.usefixtures("external_url")
async def test_push_during_failed_refresh_fires(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """With an active webhook, presses fire while the resource refresh fails."""
    await setup_integration(hass, registered_entry)
    coordinator = registered_entry.runtime_data.coordinator
    mock_client.async_get_bases.side_effect = FluentPetConnectionError("boom")
    # The first safety poll after the webhook became healthy reads the resources.
    await _wait(hass, freezer, SAFETY_POLL_INTERVAL)
    assert not coordinator.last_update_success
    assert hass.states.get(PRESS_ENTITY).state == STATE_UNKNOWN
    assert hass.states.get(SENSOR_ENTITY).state == STATE_UNKNOWN

    body = _body("press_created")
    assert await _post(hass_client_no_auth, body, signed_headers(body)) == 200
    body = _body("interaction_updated")
    headers = signed_headers(
        body,
        event_type="interaction.updated",
        delivery="00000000-0000-4000-8000-00000000d002",
    )
    assert await _post(hass_client_no_auth, body, headers) == 200
    await hass.async_block_till_done()
    state = hass.states.get(PRESS_ENTITY)
    assert state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
    assert state.attributes["button"]["name"] == "Outside"
    assert hass.states.get(SENSOR_ENTITY).state == "Outside Play"

    # Without the webhook the entities follow the failed refresh.
    registered_entry.runtime_data.webhook.active = False
    await hass.async_block_till_done()
    assert hass.states.get(PRESS_ENTITY).state == STATE_UNAVAILABLE
    assert hass.states.get(SENSOR_ENTITY).state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("external_url")
async def test_webhook_disabled_after_setup_detected(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An active webhook is read again every 5 minutes and a disable is noticed."""
    await setup_integration(hass, registered_entry)
    manager = registered_entry.runtime_data.webhook
    assert manager.active
    mock_client.async_get_webhook.return_value = webhook(
        enabled=False, disabled_at="2026-10-18T08:00:00.000Z", failure_count=20
    )
    await _refresh(hass, freezer)
    assert mock_client.async_get_webhook.await_count == 1
    assert manager.active

    await _wait(hass, freezer, timedelta(minutes=5))
    assert mock_client.async_get_webhook.await_count == 2
    assert not manager.active
    assert manager.auto_disabled
    issue_id = webhook_issue_id(registered_entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None


@pytest.mark.usefixtures("external_url")
async def test_webhook_deleted_after_setup_recreated(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An endpoint deleted at FluentPet while running is created again."""
    await setup_integration(hass, registered_entry)
    mock_client.async_get_webhook.return_value = None
    await _wait(hass, freezer, timedelta(minutes=5))
    mock_client.async_create_webhook.assert_awaited_once_with(HOUSEHOLD_ID, WEBHOOK_URL)
    assert registered_entry.runtime_data.webhook.active


@pytest.mark.usefixtures("external_url")
async def test_failing_webhook_verified_every_refresh(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """While FluentPet reports failed deliveries the endpoint is read every refresh."""
    mock_client.async_get_webhook.return_value = webhook(failure_count=3)
    await setup_integration(hass, registered_entry)
    await _refresh(hass, freezer)
    assert mock_client.async_get_webhook.await_count == 2
    mock_client.async_get_webhook.return_value = webhook()
    await _refresh(hass, freezer)
    await _refresh(hass, freezer)
    assert mock_client.async_get_webhook.await_count == 3


@pytest.mark.usefixtures("external_url")
@pytest.mark.parametrize(
    "delete_error", [None, FluentPetNotFoundError(404, "not found", None)]
)
async def test_unreadable_create_replaced(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    delete_error: Exception | None,
) -> None:
    """A create answered with an unreadable body deletes webhooks of our URL first."""
    created = mock_client.async_create_webhook.return_value
    mock_client.async_create_webhook.side_effect = [
        FluentPetParseError("Response is not a JSON object"),
        created,
    ]
    mock_client.async_list_webhooks.return_value = [
        webhook(id="00000000-0000-4000-8000-000000009002", url=WEBHOOK_URL),
        webhook(
            id="00000000-0000-4000-8000-000000009003",
            url="https://hooks.example.com/other",
        ),
    ]
    mock_client.async_delete_webhook.side_effect = delete_error
    await setup_integration(hass, mock_config_entry)
    mock_client.async_delete_webhook.assert_awaited_once_with(
        HOUSEHOLD_ID, "00000000-0000-4000-8000-000000009002"
    )
    assert mock_client.async_create_webhook.await_count == 2
    assert mock_config_entry.data[CONF_WEBHOOK_ENDPOINT_ID] == ENDPOINT_ID
    assert mock_config_entry.runtime_data.webhook.active


async def _post_held_press(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    event_ids: list[str],
) -> None:
    """Post presses while refreshes fail and the webhook is not active."""
    await setup_integration(hass, entry)
    assert not entry.runtime_data.webhook.active
    mock_client.async_get_bases.side_effect = FluentPetConnectionError("boom")
    await _refresh(hass, freezer)
    assert hass.states.get(PRESS_ENTITY).state == STATE_UNAVAILABLE
    for index, event_id in enumerate(event_ids):
        body = _body("press_created", id=event_id)
        headers = signed_headers(
            body, delivery=f"00000000-0000-4000-8000-00000000d1{index:02d}"
        )
        assert await _post(hass_client_no_auth, body, headers) == 200
    await hass.async_block_till_done()


async def test_held_press_forgotten_on_unload(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A held press is not lost on unload: the feed delivers it after setup, once."""
    press_id = "00000000-0000-4000-8000-0000000000a1"
    await _post_held_press(
        hass, hass_client_no_auth, mock_client, registered_entry, freezer, [press_id]
    )
    assert press_id in registered_entry.runtime_data.coordinator.processor.seen_events
    assert await hass.config_entries.async_unload(registered_entry.entry_id)
    await hass.async_block_till_done()
    stored = hass_storage[f"{DOMAIN}.{registered_entry.entry_id}"]["data"]
    assert press_id not in stored["event_ids"]

    mock_client.async_get_bases.side_effect = None
    press = Event.from_dict(json.loads(_body("press_created", id=press_id)))
    mock_client.async_get_events.side_effect = [EventPage((press,), "c1")]
    events = async_capture_events(hass, EVENT_STATE_CHANGED)
    assert await hass.config_entries.async_setup(registered_entry.entry_id)
    await hass.async_block_till_done()
    fired = [
        event
        for event in events
        if event.data["entity_id"] == PRESS_ENTITY
        and event.data["new_state"].state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
    ]
    assert len(fired) == 1


async def test_held_presses_bounded(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Past the hold limit the oldest press is dropped and left for the feed."""
    first = "00000000-0000-4000-8000-0000000000b1"
    second = "00000000-0000-4000-8000-0000000000b2"
    with patch("custom_components.fluentpet.entity.HELD_UPDATE_LIMIT", 1):
        await _post_held_press(
            hass,
            hass_client_no_auth,
            mock_client,
            registered_entry,
            freezer,
            [first, second],
        )
    processor = registered_entry.runtime_data.coordinator.processor
    assert first not in processor.seen_events
    assert second in processor.seen_events
    assert processor.held_events == {second}

    # The recovering refresh applies the held press and reads the dropped one
    # from the feed; both fire.
    mock_client.async_get_bases.side_effect = None
    mock_client.async_get_events.side_effect = [
        EventPage(
            (Event.from_dict(json.loads(_body("press_created", id=first))),), "c1"
        )
    ]
    events = async_capture_events(hass, EVENT_STATE_CHANGED)
    await _refresh(hass, freezer)
    fired = [
        event
        for event in events
        if event.data["entity_id"] == PRESS_ENTITY
        and event.data["new_state"].state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
    ]
    assert len(fired) == 2
    assert first in processor.seen_events
    assert not processor.held_events


async def test_held_press_survives_restart(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A press held at stop is not stored as processed; the feed fires it once."""
    press_id = "00000000-0000-4000-8000-0000000000c1"
    await _post_held_press(
        hass, hass_client_no_auth, mock_client, registered_entry, freezer, [press_id]
    )
    key = f"{DOMAIN}.{registered_entry.entry_id}"
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()
    at_stop = copy.deepcopy(hass_storage[key])
    assert press_id not in at_stop["data"]["event_ids"]

    # Start again from what was stored at stop.
    assert await hass.config_entries.async_unload(registered_entry.entry_id)
    await hass.async_block_till_done()
    hass_storage[key] = at_stop
    mock_client.async_get_bases.side_effect = None
    press = Event.from_dict(json.loads(_body("press_created", id=press_id)))
    mock_client.async_get_events.side_effect = [EventPage((press,), "c1")]
    events = async_capture_events(hass, EVENT_STATE_CHANGED)
    assert await hass.config_entries.async_setup(registered_entry.entry_id)
    await hass.async_block_till_done()
    fired = [
        event
        for event in events
        if event.data["entity_id"] == PRESS_ENTITY
        and event.data["new_state"].state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
    ]
    assert len(fired) == 1


async def test_held_press_not_fired_twice_after_eviction(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A held id pushed out of the seen set still counts as seen; it fires once."""
    press_id = "00000000-0000-4000-8000-0000000000e1"
    await _post_held_press(
        hass, hass_client_no_auth, mock_client, registered_entry, freezer, [press_id]
    )
    processor = registered_entry.runtime_data.coordinator.processor
    for index in range(600):
        processor.seen_events.add(f"00000000-0000-4000-8001-{index:012d}")
    assert press_id not in processor.seen_events
    assert press_id in processor.held_events
    # A copy from another delivery while it is still held is not held twice.
    body = _body("press_created", id=press_id)
    headers = signed_headers(body, delivery="00000000-0000-4000-8000-00000000d1ff")
    assert await _post(hass_client_no_auth, body, headers) == 200
    await hass.async_block_till_done()
    assert press_id not in processor.seen_events

    mock_client.async_get_bases.side_effect = None
    press = Event.from_dict(json.loads(_body("press_created", id=press_id)))
    mock_client.async_get_events.side_effect = [EventPage((press,), "c1")]
    events = async_capture_events(hass, EVENT_STATE_CHANGED)
    await _refresh(hass, freezer)
    fired = [
        event
        for event in events
        if event.data["entity_id"] == PRESS_ENTITY
        and event.data["new_state"].state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
    ]
    assert len(fired) == 1
    assert not processor.held_events


@pytest.mark.usefixtures("external_url")
async def test_verification_runs_slightly_early(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A check a little before the 5 minutes are up (timer rounding) verifies."""
    await setup_integration(hass, registered_entry)
    manager = registered_entry.runtime_data.webhook
    assert mock_client.async_get_webhook.await_count == 1
    freezer.tick(SLOW_RETRY_MIN - timedelta(milliseconds=300))
    await manager.async_check()
    assert mock_client.async_get_webhook.await_count == 2
    await manager.async_check()
    assert mock_client.async_get_webhook.await_count == 2


@pytest.mark.usefixtures("external_url")
@pytest.mark.parametrize("name", ["ping", "press_created"])
async def test_delivery_while_failing_switches_to_push(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    name: str,
) -> None:
    """A verified delivery, a ping too, makes a failing webhook healthy.

    FluentPet keeps counting failures until a delivery other than a ping
    succeeds; a delivery that reached Home Assistant outweighs the count. The
    first safety poll after the switch reads everything once.
    """
    mock_client.async_get_webhook.return_value = webhook(failure_count=3)
    await setup_integration(hass, registered_entry)
    coordinator = registered_entry.runtime_data.coordinator
    assert not coordinator.push_active
    assert coordinator.update_interval == timedelta(seconds=60)

    body = _body(name)
    headers = signed_headers(body, event_type=name.replace("_", "."))
    assert await _post(hass_client_no_auth, body, headers) == 200
    await hass.async_block_till_done()
    assert coordinator.push_active
    assert coordinator.update_interval == SAFETY_POLL_INTERVAL

    mock_client.reset_mock(return_value=False, side_effect=False)
    await _refresh(hass, freezer)
    assert mock_client.async_get_events.await_count == 0
    await _wait(hass, freezer, SAFETY_POLL_INTERVAL - timedelta(seconds=61))
    assert mock_client.async_get_buttons.await_count == 1
    assert coordinator.push_active


@pytest.mark.usefixtures("external_url")
async def test_delivery_window_expiry_falls_back_to_polling(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Without deliveries for 10 minutes, a failing webhook polls again."""
    mock_client.async_get_webhook.return_value = webhook(failure_count=3)
    await setup_integration(hass, registered_entry)
    coordinator = registered_entry.runtime_data.coordinator
    body = _body("ping")
    assert await _post(hass_client_no_auth, body, signed_headers(body)) == 200
    await hass.async_block_till_done()
    assert coordinator.push_active

    await _wait(hass, freezer, SAFETY_POLL_INTERVAL)
    assert coordinator.push_active
    mock_client.reset_mock(return_value=False, side_effect=False)
    await _wait(hass, freezer, SAFETY_POLL_INTERVAL + timedelta(seconds=1))
    # The refresh that finds the window expired already polls everything.
    assert not coordinator.push_active
    assert mock_client.async_get_buttons.await_count == 1
    assert coordinator.update_interval == timedelta(seconds=60)


@pytest.mark.usefixtures("external_url")
async def test_pushed_press_moves_last_seen(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    mock_client: AsyncMock,
    registered_entry: MockConfigEntry,
) -> None:
    """A pushed press moves the last seen time of its Base, without a request."""
    await setup_integration(hass, registered_entry)
    mock_client.reset_mock(return_value=False, side_effect=False)
    body = _body("press_created")
    assert await _post(hass_client_no_auth, body, signed_headers(body)) == 200
    await hass.async_block_till_done()
    created_at = dt_util.parse_datetime(json.loads(body)["created_at"])
    assert created_at is not None
    assert dt_util.parse_datetime(
        hass.states.get("sensor.test_base_last_seen").state
    ) == created_at.replace(microsecond=0)
    assert mock_client.mock_calls == []
