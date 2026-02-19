"""DojoZero Client - Python SDK for external agents.

Example usage:

    from dojozero_client import DojoClient

    client = DojoClient()

    async with client.connect_trial(
        gateway_url="http://localhost:8080",
        agent_id="my-agent",
        persona="My betting agent",
    ) as trial:
        # Stream events
        async for event in trial.events():
            odds = await trial.get_current_odds()
            if should_bet(event, odds):
                await trial.place_bet(
                    market="moneyline",
                    selection="home",
                    amount=100,
                    reference_sequence=event.sequence,
                )

        balance = await trial.get_balance()
        print(f"Final balance: {balance.balance}")
"""

from dojozero_client._client import (
    Balance,
    BetResult,
    DojoClient,
    EventEnvelope,
    Odds,
    TrialConnection,
    TrialMetadata,
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
    "Odds",
    "TrialMetadata",
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
