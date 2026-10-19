"""Slow retry schedule of the FluentPet Connect integration."""

from datetime import datetime, timedelta

from .const import SLOW_RETRY_MAX, SLOW_RETRY_MIN


class SlowRetry:
    """A retry time that backs off from SLOW_RETRY_MIN, doubling up to SLOW_RETRY_MAX.

    Used for what the next refresh would not fix: no usable webhook URL, a webhook
    FluentPet refused, or a feed only the household admin may read.
    """

    def __init__(self) -> None:
        """Initialize an idle schedule."""
        self._delay: timedelta | None = None
        self.next_at: datetime | None = None

    def schedule(self, now: datetime) -> None:
        """Schedule the next attempt, waiting twice as long as the last time."""
        self._delay = (
            SLOW_RETRY_MIN
            if self._delay is None
            else min(self._delay * 2, SLOW_RETRY_MAX)
        )
        self.next_at = now + self._delay

    def due(self, now: datetime) -> bool:
        """Return whether a scheduled attempt is due."""
        return self.next_at is not None and now >= self.next_at

    def reset(self) -> None:
        """Forget the schedule after a success."""
        self._delay = None
        self.next_at = None
