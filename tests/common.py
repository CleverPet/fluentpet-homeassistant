"""Helpers shared by the FluentPet tests."""

import json
from pathlib import Path
import time
from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fluentpet.fluentpet_api import (
    HEADER_DELIVERY,
    HEADER_EVENT,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    compute_signature,
)
from homeassistant.core import HomeAssistant

FIXTURES = Path(__file__).parent / "fixtures"

SECRET = "whsec_" + "0" * 64


def load_json_fixture(name: str) -> Any:
    """Return the parsed JSON of tests/fixtures/<name>."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


async def setup_integration(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the integration of a config entry."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def signed_headers(
    body: bytes,
    *,
    secret: str = SECRET,
    delivery: str = "00000000-0000-4000-8000-00000000d001",
    event_type: str = "press.created",
    timestamp: int | None = None,
) -> dict[str, str]:
    """Return the headers FluentPet sends with a webhook body."""
    stamp = str(int(time.time()) if timestamp is None else timestamp)
    return {
        HEADER_EVENT: event_type,
        HEADER_DELIVERY: delivery,
        HEADER_TIMESTAMP: stamp,
        HEADER_SIGNATURE: compute_signature(secret, stamp, body),
        "Content-Type": "application/json",
    }
