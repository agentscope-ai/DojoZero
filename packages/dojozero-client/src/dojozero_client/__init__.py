"""DojoZero Client - Python SDK for external agents.

Standalone mode (dojo0 run --enable-gateway):

    from dojozero_client import DojoClient

    client = DojoClient()

    async with client.connect_trial(
        gateway_url="http://localhost:8080",
        agent_id="my-agent",
    ) as trial:
        async for event in trial.events():
            ...

Dashboard mode (dojo0 serve --enable-gateway):

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
    Balance,
    BetResult,
    DojoClient,
    EventEnvelope,
    GatewayInfo,
    Odds,
    TrialConnection,
    TrialMetadata,
)
from dojozero_client._config import (
    ClientConfig,
    load_config,
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
)
from dojozero_client._transport import GatewayTransport, SSEEvent

__version__ = "0.1.0"

__all__ = [
    # Main client
    "DojoClient",
    "TrialConnection",
    # Data classes
    "Balance",
    "BetResult",
    "EventEnvelope",
    "GatewayInfo",
    "Odds",
    "TrialMetadata",
    # Config
    "ClientConfig",
    "load_config",
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
]
