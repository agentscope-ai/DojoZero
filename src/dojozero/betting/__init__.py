"""Shared betting infrastructure for DojoZero.

This module provides reusable components for sports betting scenarios:
- BettingAgent: Generic LLM-powered betting agent
- BrokerOperator: Sport-agnostic betting broker managing accounts, bets, and events

These components work with any sport (NBA, NFL, etc.) by:
1. Handling sport-prefixed event types (e.g., "nfl_game_start" -> "game_start")
2. Supporting pluggable event formatters for sport-specific display
3. Converting between different odds formats (decimal, American moneyline)

Example usage:
    from dojozero.betting import BettingAgent, BrokerOperator

    # Create broker
    broker_config = {"actor_id": "broker", "initial_balance": "1000"}
    broker = BrokerOperator(broker_config, trial_id)

    # Create agent with custom formatter
    agent = BettingAgent.from_yaml(
        config_path="agent.yaml",
        actor_id="agent1",
        trial_id=trial_id,
        event_formatter=my_custom_formatter,
    )
"""

from dojozero.betting._agent import (
    BettingAgent,
    BettingAgentConfig,
    EventFormatter,
)
from dojozero.betting._config import (
    TrialBrokerConfig,
)
from dojozero.betting._metadata import (
    BacktestBettingTrialMetadata,
    BettingTrialMetadata,
)
from dojozero.betting._models import (
    Account,
    Bet,
    BetExecutedPayload,
    BetOutcome,
    BetRequest,
    BetRequestMoneyline,
    BetRequestSpread,
    BetRequestTotal,
    BetSettledPayload,
    BetStatus,
    BetType,
    BettingEvent,
    EventStatus,
    OrderType,
    Statistics,
)
from dojozero.betting._broker import (
    BrokerOperator,
    BrokerOperatorConfig,
)

__all__ = [
    # Agent
    "BettingAgent",
    "BettingAgentConfig",
    "EventFormatter",
    # Broker config for trial params (Pydantic)
    "TrialBrokerConfig",
    # Metadata types
    "BettingTrialMetadata",
    "BacktestBettingTrialMetadata",
    # Broker
    "BrokerOperator",
    "BrokerOperatorConfig",
    "Account",
    "BettingEvent",
    "BetRequest",
    "BetRequestMoneyline",
    "BetRequestSpread",
    "BetRequestTotal",
    "Bet",
    "BetExecutedPayload",
    "BetSettledPayload",
    "Statistics",
    # Enums
    "EventStatus",
    "OrderType",
    "BetStatus",
    "BetOutcome",
    "BetType",
]
