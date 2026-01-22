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
from dojozero.betting._broker import (
    Account,
    Bet,
    BetExecutedPayload,
    BetOutcome,
    BetRequest,
    BetSettledPayload,
    BetStatus,
    BettingEvent,
    BettingPhase,
    BrokerOperator,
    BrokerOperatorConfig,
    EventStatus,
    OrderType,
    Statistics,
)

__all__ = [
    # Agent
    "BettingAgent",
    "BettingAgentConfig",
    "EventFormatter",
    # Broker
    "BrokerOperator",
    "BrokerOperatorConfig",
    "Account",
    "BettingEvent",
    "BetRequest",
    "Bet",
    "BetExecutedPayload",
    "BetSettledPayload",
    "Statistics",
    # Enums
    "EventStatus",
    "OrderType",
    "BettingPhase",
    "BetStatus",
    "BetOutcome",
]
