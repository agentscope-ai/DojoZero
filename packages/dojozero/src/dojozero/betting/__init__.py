"""Shared betting infrastructure for DojoZero.

This module provides reusable components for sports betting scenarios:
- BettingAgent: Generic LLM-powered betting agent
- BrokerOperator: Sport-agnostic classic betting broker (accounts, bets, odds)
- PredictionBroker: Sport-agnostic prediction-contest broker with five
  fixed windows (pre-game and Q1-Q4) and shared per-window prize pools

These components work with any sport (NBA, NFL, etc.) by:
1. Handling sport-prefixed event types (e.g., "nfl_game_start" -> "game_start")
2. Supporting pluggable event formatters for sport-specific display
3. Converting between different odds formats (decimal, American moneyline)
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
    BrokerFinalStats,
    EventStatus,
    OrderType,
    Prediction,
    PredictionOutcome,
    PredictionStatistics,
    Statistics,
    StatisticsList,
    # Agent Message Models
    ReasoningStep,
    ToolCallStep,
    ToolResultStep,
    CoTStep,
    AgentResponseMessage,
    AgentInfo,
    AgentList,
)
from dojozero.betting._broker import (
    BrokerOperator,
    BrokerOperatorConfig,
)
from dojozero.betting._prediction_broker import (
    PredictionBroker,
    PredictionBrokerConfig,
)
from dojozero.betting._protocol import ContestOperator
from dojozero.betting._scoring import (
    DEFAULT_WINDOW_POOLS,
    NUM_WINDOWS,
    settle_window_predictions,
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
    # Brokers
    "BrokerOperator",
    "BrokerOperatorConfig",
    "PredictionBroker",
    "PredictionBrokerConfig",
    "ContestOperator",
    # Scoring
    "DEFAULT_WINDOW_POOLS",
    "NUM_WINDOWS",
    "settle_window_predictions",
    # Domain models
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
    "BrokerFinalStats",
    "Prediction",
    "PredictionOutcome",
    "PredictionStatistics",
    # Enums
    "EventStatus",
    "OrderType",
    "BetStatus",
    "BetOutcome",
    "BetType",
    # Agent Message Models
    "ReasoningStep",
    "ToolCallStep",
    "ToolResultStep",
    "CoTStep",
    "AgentResponseMessage",
    "AgentInfo",
    "AgentList",
    "StatisticsList",
]
