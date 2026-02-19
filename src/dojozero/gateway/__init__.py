"""Agent Gateway module for external agent HTTP API.

Provides HTTP API (REST + SSE) for third-party agents to participate in trials.
"""

from dojozero.gateway._adapter import ExternalAgentAdapter, ExternalAgentState
from dojozero.gateway._models import (
    AgentRegistrationRequest,
    AgentRegistrationResponse,
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
    TrialMetadataResponse,
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
    # SSE
    "SSEConnection",
    "create_sse_response",
    # Models - Registration
    "AgentRegistrationRequest",
    "AgentRegistrationResponse",
    # Models - Trial
    "TrialMetadataResponse",
    # Models - Events
    "EventEnvelope",
    "RecentEventsResponse",
    "HeartbeatMessage",
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
