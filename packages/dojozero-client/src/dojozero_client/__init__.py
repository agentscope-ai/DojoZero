"""DojoZero Client - Python SDK for external agents.

Standalone mode (dojo0 run):

    from dojozero_client import DojoClient

    client = DojoClient()

    async with client.connect_trial(
        gateway_url="http://localhost:8080",
        agent_id="my-agent",
    ) as trial:
        async for event in trial.events():
            ...

Dashboard mode (dojo0 serve):

    from dojozero_client import DojoClient

    client = DojoClient()

    # Discover available trials
    gateways = await client.list_gateways("http://localhost:8000")

    # Connect using same connect_trial method
    gateway_url = f"http://localhost:8000{gateways[0].endpoint}"
    async with client.connect_trial(gateway_url, agent_id="my-agent") as trial:
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
    Daemon,
    DaemonConfig,
    DaemonState,
    get_daemon_status,
    is_daemon_running,
    list_running_trials,
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
    "Daemon",
    "DaemonConfig",
    "DaemonState",
    "get_daemon_status",
    "is_daemon_running",
    "list_running_trials",
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
