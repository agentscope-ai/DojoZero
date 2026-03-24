"""Rate limiting for Agent Gateway.

Provides per-agent rate limiting with configurable limits for
different operation types.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from dojozero.gateway._models import ErrorCodes, ErrorDetail, ErrorResponse

logger = logging.getLogger(__name__)


class RateLimitType(Enum):
    """Types of rate-limited operations."""

    GENERAL = "general"  # General API requests
    BET = "bet"  # Bet placement
    SSE = "sse"  # SSE connections


@dataclass
class RateLimitConfig:
    """Configuration for rate limits."""

    # Requests per minute
    general_rpm: int = 300
    bet_rpm: int = 60

    # Maximum concurrent SSE connections per agent
    max_sse_connections: int = 5

    # Window size in seconds
    window_seconds: int = 60

    # Whether to enable rate limiting
    enabled: bool = True


@dataclass
class RateLimitBucket:
    """Token bucket for rate limiting."""

    tokens: float
    capacity: float
    refill_rate: float  # tokens per second
    last_refill: float = field(default_factory=time.time)

    def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens from the bucket.

        Args:
            tokens: Number of tokens to consume

        Returns:
            True if tokens were consumed, False if rate limited
        """
        self._refill()

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    @property
    def retry_after(self) -> float:
        """Seconds until a token is available."""
        if self.tokens >= 1:
            return 0
        return (1 - self.tokens) / self.refill_rate


@dataclass
class AgentRateLimitState:
    """Rate limit state for a single agent."""

    agent_id: str
    general_bucket: RateLimitBucket
    bet_bucket: RateLimitBucket
    sse_connections: int = 0

    @classmethod
    def create(cls, agent_id: str, config: RateLimitConfig) -> "AgentRateLimitState":
        """Create rate limit state for an agent."""
        return cls(
            agent_id=agent_id,
            general_bucket=RateLimitBucket(
                tokens=config.general_rpm,
                capacity=config.general_rpm,
                refill_rate=config.general_rpm / config.window_seconds,
            ),
            bet_bucket=RateLimitBucket(
                tokens=config.bet_rpm,
                capacity=config.bet_rpm,
                refill_rate=config.bet_rpm / config.window_seconds,
            ),
        )


class RateLimiter:
    """Per-agent rate limiter.

    Uses token bucket algorithm for smooth rate limiting.
    """

    def __init__(self, config: RateLimitConfig | None = None):
        """Initialize rate limiter.

        Args:
            config: Rate limit configuration. Uses defaults if None.
        """
        self.config = config or RateLimitConfig()
        self._agents: dict[str, AgentRateLimitState] = {}

        logger.info(
            "RateLimiter initialized: enabled=%s, general=%d/min, bet=%d/min, sse=%d",
            self.config.enabled,
            self.config.general_rpm,
            self.config.bet_rpm,
            self.config.max_sse_connections,
        )

    def _get_or_create_state(self, agent_id: str) -> AgentRateLimitState:
        """Get or create rate limit state for an agent."""
        if agent_id not in self._agents:
            self._agents[agent_id] = AgentRateLimitState.create(agent_id, self.config)
        return self._agents[agent_id]

    def check_rate_limit(
        self,
        agent_id: str,
        limit_type: RateLimitType = RateLimitType.GENERAL,
    ) -> None:
        """Check if request is rate limited.

        Args:
            agent_id: Agent making the request
            limit_type: Type of operation

        Raises:
            HTTPException: 429 if rate limited
        """
        if not self.config.enabled:
            return

        state = self._get_or_create_state(agent_id)

        if limit_type == RateLimitType.GENERAL:
            bucket = state.general_bucket
        elif limit_type == RateLimitType.BET:
            bucket = state.bet_bucket
        else:
            return  # SSE handled separately

        if not bucket.consume():
            retry_after = int(bucket.retry_after) + 1
            raise HTTPException(
                status_code=429,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code=ErrorCodes.RATE_LIMITED,
                        message=f"Rate limit exceeded. Retry after {retry_after} seconds.",
                        details={
                            "retry_after": retry_after,
                            "limit_type": limit_type.value,
                        },
                    )
                ).model_dump(by_alias=True),
                headers={"Retry-After": str(retry_after)},
            )

    def check_sse_limit(self, agent_id: str) -> None:
        """Check if SSE connection limit reached.

        Args:
            agent_id: Agent requesting SSE connection

        Raises:
            HTTPException: 429 if too many connections
        """
        if not self.config.enabled:
            return

        state = self._get_or_create_state(agent_id)

        if state.sse_connections >= self.config.max_sse_connections:
            raise HTTPException(
                status_code=429,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code=ErrorCodes.RATE_LIMITED,
                        message=f"Maximum SSE connections ({self.config.max_sse_connections}) reached",
                        details={
                            "current": state.sse_connections,
                            "max": self.config.max_sse_connections,
                        },
                    )
                ).model_dump(by_alias=True),
            )

    def acquire_sse_connection(self, agent_id: str) -> None:
        """Acquire an SSE connection slot.

        Args:
            agent_id: Agent ID

        Raises:
            HTTPException: 429 if limit reached
        """
        self.check_sse_limit(agent_id)
        state = self._get_or_create_state(agent_id)
        state.sse_connections += 1
        logger.debug(
            "SSE connection acquired: agent=%s, connections=%d",
            agent_id,
            state.sse_connections,
        )

    def release_sse_connection(self, agent_id: str) -> None:
        """Release an SSE connection slot.

        Args:
            agent_id: Agent ID
        """
        state = self._agents.get(agent_id)
        if state and state.sse_connections > 0:
            state.sse_connections -= 1
            logger.debug(
                "SSE connection released: agent=%s, connections=%d",
                agent_id,
                state.sse_connections,
            )

    def get_stats(self, agent_id: str) -> dict[str, Any]:
        """Get rate limit stats for an agent.

        Args:
            agent_id: Agent ID

        Returns:
            Dict with rate limit statistics
        """
        state = self._agents.get(agent_id)
        if state is None:
            return {
                "agent_id": agent_id,
                "general_remaining": self.config.general_rpm,
                "bet_remaining": self.config.bet_rpm,
                "sse_connections": 0,
                "sse_remaining": self.config.max_sse_connections,
            }

        return {
            "agent_id": agent_id,
            "general_remaining": int(state.general_bucket.tokens),
            "bet_remaining": int(state.bet_bucket.tokens),
            "sse_connections": state.sse_connections,
            "sse_remaining": self.config.max_sse_connections - state.sse_connections,
        }

    def reset(self, agent_id: str) -> None:
        """Reset rate limits for an agent.

        Args:
            agent_id: Agent ID
        """
        self._agents.pop(agent_id, None)
        logger.debug("Rate limits reset for agent: %s", agent_id)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for rate limiting.

    Applies general rate limiting to all requests. Specific
    limits (betting, SSE) should be checked in endpoint handlers.
    """

    def __init__(self, app, rate_limiter: RateLimiter):
        """Initialize middleware.

        Args:
            app: FastAPI application
            rate_limiter: RateLimiter instance
        """
        super().__init__(app)
        self.rate_limiter = rate_limiter

    async def dispatch(self, request: Request, call_next):
        """Process request with rate limiting.

        Args:
            request: Incoming request
            call_next: Next middleware/handler

        Returns:
            Response
        """
        # Extract agent ID from headers
        agent_id = request.headers.get("X-Agent-ID")

        # Skip rate limiting for unauthenticated requests
        # (they'll fail auth anyway)
        if agent_id:
            try:
                self.rate_limiter.check_rate_limit(agent_id, RateLimitType.GENERAL)
            except HTTPException as e:
                return JSONResponse(
                    status_code=e.status_code,
                    content=e.detail,
                    headers=e.headers,
                )

        return await call_next(request)


def create_rate_limit_dependency(rate_limiter: RateLimiter, limit_type: RateLimitType):
    """Create a FastAPI dependency for rate limiting.

    Args:
        rate_limiter: RateLimiter instance
        limit_type: Type of limit to check

    Returns:
        Dependency function
    """

    def check_limit(request: Request) -> None:
        """FastAPI dependency to check rate limit."""
        agent_id = request.headers.get("X-Agent-ID")
        if agent_id:
            rate_limiter.check_rate_limit(agent_id, limit_type)

    return check_limit


__all__ = [
    "AgentRateLimitState",
    "RateLimitBucket",
    "RateLimitConfig",
    "RateLimitMiddleware",
    "RateLimitType",
    "RateLimiter",
    "create_rate_limit_dependency",
]
