"""Verifying and parsing FluentPet webhook requests."""

import hashlib
import hmac
import json
import re
import time

from .const import SIGNATURE_PREFIX, SIGNATURE_TOLERANCE
from .exceptions import FluentPetParseError, InvalidSignatureError, StaleTimestampError
from .models import Event, parse_event

_TIMESTAMP = re.compile(r"[0-9]+", re.ASCII)


def compute_signature(secret: str, timestamp: str, body: bytes) -> str:
    """Return the X-FluentPet-Signature value for a body: "sha256=" and the hex HMAC.

    The HMAC-SHA256 is keyed with the webhook secret over "<timestamp>.<body>".
    """
    signed = timestamp.encode() + b"." + body
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def verify_signature(
    secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    *,
    now: float | None = None,
    tolerance: int = SIGNATURE_TOLERANCE,
) -> None:
    """Verify a webhook request; return normally only when it is authentic and fresh.

    Args:
        secret: The webhook secret (whsec_...).
        timestamp: The X-FluentPet-Timestamp header (unix seconds).
        body: The raw request body, exactly as received.
        signature: The X-FluentPet-Signature header.
        now: The current unix time; defaults to time.time().
        tolerance: Seconds the timestamp may differ from now, either way.

    Raises:
        InvalidSignatureError: A header is malformed or the signature does not match.
        StaleTimestampError: The signature matches but the timestamp is outside the tolerance.
    """
    if not _TIMESTAMP.fullmatch(timestamp):
        raise InvalidSignatureError("Timestamp is not a whole number of seconds")
    if not signature.startswith(SIGNATURE_PREFIX):
        raise InvalidSignatureError(f"Signature does not start with {SIGNATURE_PREFIX}")
    expected = compute_signature(secret, timestamp, body)
    if not hmac.compare_digest(expected.encode(), signature.encode()):
        raise InvalidSignatureError("Signature does not match")
    current = time.time() if now is None else now
    if abs(current - int(timestamp)) > tolerance:
        raise StaleTimestampError(
            f"Timestamp is more than {tolerance} seconds from the local clock"
        )


def parse_event_body(body: bytes) -> Event:
    """Parse a webhook request body into an Event.

    Raises:
        FluentPetParseError: The body is not JSON or not an event object.
    """
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as err:
        raise FluentPetParseError("Webhook body is not valid JSON") from err
    return parse_event(data)
