"""Exceptions of the FluentPet API client."""


class FluentPetError(Exception):
    """Base class of every error this library raises."""


class FluentPetConnectionError(FluentPetError):
    """The API could not be reached, timed out, or failed on its side."""


class FluentPetParseError(FluentPetError):
    """A response or webhook body is not the JSON shape the contract describes."""


class FluentPetApiError(FluentPetError):
    """The API answered with an error status.

    Attributes:
        status: HTTP status code.
        error_type: The `type` of the error body, when there was one.
        error_message: The `message` of the error body, when there was one.
    """

    def __init__(
        self, status: int, error_type: str | None, error_message: str | None
    ) -> None:
        """Initialize the error from the response status and error body."""
        detail = ": ".join(part for part in (error_type, error_message) if part)
        super().__init__(f"HTTP {status}: {detail}" if detail else f"HTTP {status}")
        self.status = status
        self.error_type = error_type
        self.error_message = error_message


class FluentPetServerError(FluentPetApiError, FluentPetConnectionError):
    """The API answered with a 5xx status."""


class FluentPetAuthError(FluentPetApiError):
    """The access token was missing, invalid or expired (401)."""


class FluentPetForbiddenError(FluentPetApiError):
    """The user may not do this, for example is not the household admin (403)."""


class FluentPetNotFoundError(FluentPetApiError):
    """The household, webhook or event does not exist for this user (404)."""


class FluentPetRequestError(FluentPetApiError):
    """The request was refused as invalid (400, 422 or another 4xx)."""


class FluentPetRateLimitError(FluentPetApiError):
    """Too many requests (429).

    Attributes:
        retry_after: Seconds to wait from the Retry-After header, when sent.
    """

    def __init__(
        self,
        status: int,
        error_type: str | None,
        error_message: str | None,
        retry_after: float | None = None,
    ) -> None:
        """Initialize the error with an optional retry delay."""
        super().__init__(status, error_type, error_message)
        self.retry_after = retry_after


class WebhookVerificationError(FluentPetError):
    """A webhook request failed verification and must be refused."""


class InvalidSignatureError(WebhookVerificationError):
    """The signature or timestamp header is malformed or does not match."""


class StaleTimestampError(WebhookVerificationError):
    """The signed timestamp is too far from the local clock (possible replay)."""
