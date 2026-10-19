"""Tests for webhook signature verification and body parsing."""

import hashlib
import hmac
import json
import time

import pytest

from custom_components.fluentpet.fluentpet_api import (
    EventType,
    FluentPetParseError,
    InvalidSignatureError,
    PressCreatedData,
    StaleTimestampError,
    WebhookVerificationError,
    compute_signature,
    parse_event_body,
    verify_signature,
)
from tests.common import load_json_fixture

SECRET = "whsec_" + "0" * 64
NOW = 1_792_238_400
TIMESTAMP = str(NOW)
BODY = json.dumps(load_json_fixture("events/press_created.json")).encode()


def test_compute_signature_matches_contract() -> None:
    """The signature is sha256= and the hex HMAC of "<timestamp>.<body>"."""
    expected = hmac.new(
        SECRET.encode(), f"{TIMESTAMP}.".encode() + BODY, hashlib.sha256
    ).hexdigest()
    assert compute_signature(SECRET, TIMESTAMP, BODY) == f"sha256={expected}"


def test_valid_signature() -> None:
    """A valid, fresh request verifies."""
    signature = compute_signature(SECRET, TIMESTAMP, BODY)
    verify_signature(SECRET, TIMESTAMP, BODY, signature, now=NOW)
    verify_signature(SECRET, TIMESTAMP, BODY, signature, now=NOW + 300)
    verify_signature(SECRET, TIMESTAMP, BODY, signature, now=NOW - 300)


def test_valid_signature_default_clock() -> None:
    """Without now, the local clock is used."""
    timestamp = str(int(time.time()))
    verify_signature(
        SECRET, timestamp, BODY, compute_signature(SECRET, timestamp, BODY)
    )


@pytest.mark.parametrize(
    "signature",
    [
        compute_signature("whsec_" + "1" * 64, TIMESTAMP, BODY),
        compute_signature(SECRET, TIMESTAMP, BODY + b" "),
        compute_signature(SECRET, str(NOW + 1), BODY),
        "sha256=" + "0" * 64,
        "sha256=",
        "sha256=é",
    ],
    ids=["other-secret", "other-body", "other-timestamp", "zeros", "empty", "unicode"],
)
def test_bad_signature(signature: str) -> None:
    """A signature that does not match is refused."""
    with pytest.raises(InvalidSignatureError):
        verify_signature(SECRET, TIMESTAMP, BODY, signature, now=NOW)


def test_missing_prefix() -> None:
    """A signature without the sha256= prefix is refused."""
    signature = compute_signature(SECRET, TIMESTAMP, BODY).removeprefix("sha256=")
    with pytest.raises(InvalidSignatureError, match="sha256="):
        verify_signature(SECRET, TIMESTAMP, BODY, signature, now=NOW)


@pytest.mark.parametrize("offset", [301, -301, 86_400])
def test_stale_or_future_timestamp(offset: int) -> None:
    """A timestamp more than the tolerance away from now is refused."""
    signature = compute_signature(SECRET, TIMESTAMP, BODY)
    with pytest.raises(StaleTimestampError):
        verify_signature(SECRET, TIMESTAMP, BODY, signature, now=NOW + offset)


def test_custom_tolerance() -> None:
    """The tolerance can be changed."""
    signature = compute_signature(SECRET, TIMESTAMP, BODY)
    verify_signature(SECRET, TIMESTAMP, BODY, signature, now=NOW + 500, tolerance=600)
    with pytest.raises(StaleTimestampError):
        verify_signature(SECRET, TIMESTAMP, BODY, signature, now=NOW + 11, tolerance=10)


@pytest.mark.parametrize(
    "timestamp", ["", "abc", "1.5", "-1", " 1792238400", "1792238400\n", "١٢"]
)
def test_non_numeric_timestamp(timestamp: str) -> None:
    """A timestamp that is not ASCII digits is refused before any HMAC."""
    signature = compute_signature(SECRET, timestamp, BODY)
    with pytest.raises(InvalidSignatureError):
        verify_signature(SECRET, timestamp, BODY, signature, now=NOW)


def test_errors_are_verification_errors() -> None:
    """Both errors share a base class for callers that refuse either."""
    assert issubclass(InvalidSignatureError, WebhookVerificationError)
    assert issubclass(StaleTimestampError, WebhookVerificationError)


def test_parse_event_body() -> None:
    """A webhook body parses into the same Event as a feed entry."""
    event = parse_event_body(BODY)
    assert event.event_type is EventType.PRESS_CREATED
    assert isinstance(event.data, PressCreatedData)


def test_parse_ping_body() -> None:
    """A ping body parses."""
    body = json.dumps(load_json_fixture("events/ping.json")).encode()
    assert parse_event_body(body).event_type is EventType.PING


@pytest.mark.parametrize("body", [b"", b"{", b"\xff\xfe", b"[]", b'"x"'])
def test_parse_bad_body(body: bytes) -> None:
    """A body that is not an event object is a parse error."""
    with pytest.raises(FluentPetParseError):
        parse_event_body(body)
