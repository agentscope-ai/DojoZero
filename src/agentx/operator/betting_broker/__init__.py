"""Betting Broker Package

A generic sports betting broker system for managing accounts, events, and bets.
"""

from .broker import BrokerOperator, BrokerOperatorConfig
from .model import (
    Account,
    Bet,
    BetRequest,
    BettingPhase,
    BetStatus,
    BetOutcome,
    Event,
    EventStatus,
    OrderType,
    Statistics,
)

__all__ = [
    # Broker
    "BrokerOperator",
    "BrokerOperatorConfig",
    # Models
    "Account",
    "Bet",
    "BetRequest",
    "BettingPhase",
    "BetStatus",
    "BetOutcome",
    "Event",
    "EventStatus",
    "OrderType",
    "Statistics",
]
