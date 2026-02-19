"""Exceptions for DojoZero client."""

from __future__ import annotations

from typing import Any


class DojoClientError(Exception):
    """Base exception for all DojoZero client errors."""

    pass


class ConnectionError(DojoClientError):
    """Failed to connect to the gateway."""

    pass


class AuthenticationError(DojoClientError):
    """Authentication failed."""

    pass


class RegistrationError(DojoClientError):
    """Agent registration failed."""

    pass


class NotRegisteredError(DojoClientError):
    """Agent not registered for this trial."""

    pass


class BetRejectedError(DojoClientError):
    """Bet was rejected by the gateway."""

    def __init__(
        self,
        message: str,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.details: dict[str, Any] = details or {}


class StaleReferenceError(BetRejectedError):
    """Bet rejected due to stale reference sequence."""

    pass


class InsufficientBalanceError(BetRejectedError):
    """Bet rejected due to insufficient balance."""

    pass


class BettingClosedError(BetRejectedError):
    """Bet rejected because betting is closed."""

    pass


class RateLimitedError(DojoClientError):
    """Request was rate limited."""

    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class StreamDisconnectedError(DojoClientError):
    """SSE stream was disconnected."""

    pass


__all__ = [
    "DojoClientError",
    "ConnectionError",
    "AuthenticationError",
    "RegistrationError",
    "NotRegisteredError",
    "BetRejectedError",
    "StaleReferenceError",
    "InsufficientBalanceError",
    "BettingClosedError",
    "RateLimitedError",
    "StreamDisconnectedError",
]
