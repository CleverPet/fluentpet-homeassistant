"""Tests for the FluentPet Connect diagnostics."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from syrupy.assertion import SnapshotAssertion
from syrupy.filters import props

from custom_components.fluentpet.const import CONF_API_BASE_URL, DOMAIN
from homeassistant.core import HomeAssistant

from .common import setup_integration
from .conftest import HOUSEHOLD_ID


@pytest.mark.usefixtures("external_url")
async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Diagnostics redact tokens, the webhook secret, names, words and serials."""
    await setup_integration(hass, mock_config_entry)
    result = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )
    assert result == snapshot(exclude=props("created_at", "modified_at", "expires_at"))
    assert result["custom_api_base_url"] is False
    text = str(result)
    for secret in (
        "mock-access-token",
        "mock-refresh-token",
        "whsec_",
        "TESTSERIAL",
        "Test Household",
        "Test Learner",
        "'outside'",
        "aud_",
    ):
        assert secret not in text


async def test_diagnostics_custom_api_base_url(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: AsyncMock,
    entry_data: dict[str, Any],
) -> None:
    """A custom API host is reported as set, without the URL."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Household",
        unique_id=HOUSEHOLD_ID,
        data=entry_data,
        options={CONF_API_BASE_URL: "https://example.com"},
    )
    await setup_integration(hass, entry)
    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert result["custom_api_base_url"] is True
    assert result["entry"]["options"][CONF_API_BASE_URL] == "**REDACTED**"
    assert "example.com" not in str(result)
