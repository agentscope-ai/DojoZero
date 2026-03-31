"""DojoZero Client - Python SDK for external agents.

Usage:

    from dojozero_client import DojoClient, load_config

    client = DojoClient()
    config = load_config()

    # Gateway URL is derived from dashboard_url + trial_id
    gateway_url = config.get_gateway_url("my-trial")

    async with client.connect_trial(
        gateway_url=gateway_url,
        api_key="sk-agent-xxx",
    ) as trial:
        async for event in trial.events():
            ...
"""

from dojozero_client._client import (
    AgentResult,
    Balance,
    BetResult,
    DojoClient,
    EventEnvelope,
    GatewayInfo,
    Holding,
    Odds,
    TrialConnection,
    TrialEndedEvent,
    TrialMetadata,
    TrialResults,
)
from dojozero_client._config import (
    ClientConfig,
    load_config,
)
from dojozero_client._daemon import (
    DaemonState,
    Strategy,
    TrialHandler,
    UnifiedDaemon,
    get_daemon_status,
    is_daemon_running,
    stop_daemon,
)
from dojozero_client._exceptions import (
    AuthenticationError,
    BetRejectedError,
    BettingClosedError,
    ConnectionError,
    DojoClientError,
    InsufficientBalanceError,
    NotRegisteredError,
    RateLimitedError,
    RegistrationError,
    StaleReferenceError,
    StreamDisconnectedError,
    TrialEndedError,
)
from dojozero_client._transport import GatewayTransport, SSEEvent

__version__ = "0.1.0"

__all__ = [
    # Main client
    "DojoClient",
    "TrialConnection",
    # Data classes
    "AgentResult",
    "Balance",
    "BetResult",
    "EventEnvelope",
    "GatewayInfo",
    "Holding",
    "Odds",
    "TrialEndedEvent",
    "TrialMetadata",
    "TrialResults",
    # Config
    "ClientConfig",
    "load_config",
    # Daemon (agent mode)
    "UnifiedDaemon",
    "TrialHandler",
    "Strategy",
    "DaemonState",
    "get_daemon_status",
    "is_daemon_running",
    "stop_daemon",
    # Transport (advanced use)
    "GatewayTransport",
    "SSEEvent",
    # Exceptions
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
    "TrialEndedError",
]
