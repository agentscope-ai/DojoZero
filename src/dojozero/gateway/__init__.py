"""Agent Gateway module for external agent HTTP API.

Provides HTTP API (REST + SSE) for third-party agents to participate in trials.
"""

from dojozero.gateway._adapter import ExternalAgentAdapter, ExternalAgentState
from dojozero.gateway._auth import (
    AgentCredentials,
    AuthConfig,
    AuthProvider,
    create_auth_dependency,
)
from dojozero.gateway._models import (
    AgentRegistrationRequest,
    AgentRegistrationResponse,
    AgentResult,
    BalanceResponse,
    BetRequest,
    BetResponse,
    BetsListResponse,
    CurrentOddsResponse,
    ErrorCodes,
    ErrorDetail,
    ErrorResponse,
    EventEnvelope,
    HeartbeatMessage,
    HoldingResponse,
    RecentEventsResponse,
    SpreadLine,
    TotalLine,
    TrialEndedMessage,
    TrialMetadataResponse,
    TrialResultsResponse,
)
from dojozero.gateway._rate_limit import (
    RateLimitConfig,
    RateLimitMiddleware,
    RateLimitType,
    RateLimiter,
    create_rate_limit_dependency,
)
from dojozero.gateway._server import GatewayState, create_gateway_app
from dojozero.gateway._sse import SSEConnection, create_sse_response

__all__ = [
    # Server
    "create_gateway_app",
    "GatewayState",
    # Adapter
    "ExternalAgentAdapter",
    "ExternalAgentState",
    # Auth
    "AgentCredentials",
    "AuthConfig",
    "AuthProvider",
    "create_auth_dependency",
    # Rate Limiting
    "RateLimitConfig",
    "RateLimitMiddleware",
    "RateLimitType",
    "RateLimiter",
    "create_rate_limit_dependency",
    # SSE
    "SSEConnection",
    "create_sse_response",
    # Models - Registration
    "AgentRegistrationRequest",
    "AgentRegistrationResponse",
    # Models - Trial
    "TrialMetadataResponse",
    # Models - Events
    "AgentResult",
    "EventEnvelope",
    "HeartbeatMessage",
    "RecentEventsResponse",
    "TrialEndedMessage",
    "TrialResultsResponse",
    # Models - Odds
    "CurrentOddsResponse",
    "SpreadLine",
    "TotalLine",
    # Models - Betting
    "BetRequest",
    "BetResponse",
    "BetsListResponse",
    "BalanceResponse",
    "HoldingResponse",
    # Models - Errors
    "ErrorCodes",
    "ErrorDetail",
    "ErrorResponse",
]
