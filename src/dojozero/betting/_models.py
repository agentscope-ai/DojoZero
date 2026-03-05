"""Betting domain models — accounts, bets, events, statistics, and enums.

Extracted from ``_broker.py`` so that consumers can import lightweight data
contracts without pulling in the full broker operator implementation.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, computed_field, field_serializer


# =============================================================================
# Enums
# =============================================================================


class EventStatus(Enum):
    """Status of a betting event"""

    SCHEDULED = "SCHEDULED"  # Pre-game, accepting bets
    LIVE = "LIVE"  # In-game, accepting live bets only
    CLOSED = "CLOSED"  # Game ended, no more bets
    SETTLED = "SETTLED"  # All bets settled


# Valid status transitions for betting events
VALID_STATUS_TRANSITIONS = {
    EventStatus.SCHEDULED: {EventStatus.LIVE, EventStatus.CLOSED},
    EventStatus.LIVE: {EventStatus.CLOSED},
    EventStatus.CLOSED: {EventStatus.SETTLED},
    EventStatus.SETTLED: set(),
}


class OrderType(Enum):
    """Type of bet order"""

    MARKET = "MARKET"  # Execute immediately at current odds
    LIMIT = "LIMIT"  # Execute only when odds reach limit_odds


class BetStatus(Enum):
    """Status of a bet"""

    PENDING = "PENDING"  # Limit order waiting for execution
    ACTIVE = "ACTIVE"  # Executed, waiting for settlement
    SETTLED = "SETTLED"  # Completed with outcome
    CANCELLED = "CANCELLED"  # Cancelled before execution


class BetOutcome(Enum):
    """Outcome of a settled bet"""

    WIN = "WIN"
    LOSS = "LOSS"


class BetType(Enum):
    """Type of bet"""

    MONEYLINE = "MONEYLINE"  # Default: win/lose
    SPREAD = "SPREAD"  # Point spread
    TOTAL = "TOTAL"  # Over/under total points


# =============================================================================
# Account
# =============================================================================


class Holding(BaseModel):
    """Information about an aggregated holding (total shares for a position)."""

    shares: Decimal
    selection: Literal["home", "away", "over", "under"]
    event_id: str
    bet_type: BetType
    spread_value: Optional[Decimal] = None  # For SPREAD bets
    total_value: Optional[Decimal] = None  # For TOTAL bets


class Account(BaseModel):
    """Agent account information"""

    agent_id: str
    balance: Decimal
    created_at: datetime
    last_updated: datetime
    # True if account was created via gateway (external agent with API key)
    is_external: bool = False
    # Holdings: aggregated positions (shares aggregated by event_id + selection + bet_type + spread_value/total_value)
    holdings: List[Holding] = Field(
        default_factory=list,
        description="Current holdings: aggregated positions showing total shares for each unique position (event_id + selection + bet_type + spread_value/total_value). Multiple bets on the same position are combined into a single holding.",
    )


# =============================================================================
# Event
# =============================================================================


class BettingEvent(BaseModel):
    """Betting event (e.g., a sports game)"""

    event_id: str
    home_team: str
    away_team: str
    game_time: datetime
    status: EventStatus
    home_probability: Optional[Decimal] = Field(
        default=None,
        ge=0,
        le=1,
        description="Home team win probability (0-1). Can be None initially, filled in when odds arrive",
    )
    away_probability: Optional[Decimal] = Field(
        default=None,
        ge=0,
        le=1,
        description="Away team win probability (0-1). Can be None initially, filled in when odds arrive",
    )
    # Multiple spreads: spread_value -> {home_probability, away_probability}
    spread_lines: Dict[Decimal, Dict[str, Decimal]] = Field(
        default_factory=dict,
        description="Spread betting lines: {spread_value: {'home_probability': Decimal, 'away_probability': Decimal}}",
    )
    # Multiple totals: total_value -> {over_probability, under_probability}
    total_lines: Dict[Decimal, Dict[str, Decimal]] = Field(
        default_factory=dict,
        description="Total (over/under) betting lines: {total_value: {'over_probability': Decimal, 'under_probability': Decimal}}",
    )
    last_odds_update: Optional[datetime] = None
    betting_closed_at: Optional[datetime] = None

    @computed_field
    def can_bet(self) -> bool:
        """True if betting is allowed (status is SCHEDULED or LIVE)."""
        return self.status in {EventStatus.SCHEDULED, EventStatus.LIVE}


# =============================================================================
# Bet Request
# =============================================================================


@dataclass
class BetRequestMoneyline:
    """Moneyline bet request from agent to broker"""

    amount: Decimal
    selection: Literal["home", "away"]
    event_id: str
    order_type: OrderType
    limit_probability: Optional[Decimal] = (
        None  # Required if order_type == LIMIT (0-1 range)
    )

    def validate(self) -> None:
        """Validate bet request parameters"""
        if self.amount <= 0:
            raise ValueError(f"Bet amount must be positive, got {self.amount}")

        if self.order_type == OrderType.LIMIT:
            if self.limit_probability is None:
                raise ValueError("limit_probability required for LIMIT orders")
            if self.limit_probability < 0 or self.limit_probability > 1:
                raise ValueError(
                    f"limit_probability must be between 0 and 1, got {self.limit_probability}"
                )


@dataclass
class BetRequestSpread:
    """Spread bet request from agent to broker"""

    amount: Decimal
    selection: Literal["home", "away"]
    event_id: str
    order_type: OrderType
    spread_value: Decimal  # Required for SPREAD bets
    limit_probability: Optional[Decimal] = (
        None  # Required if order_type == LIMIT (0-1 range)
    )

    def validate(self) -> None:
        """Validate bet request parameters"""
        if self.amount <= 0:
            raise ValueError(f"Bet amount must be positive, got {self.amount}")

        if self.order_type == OrderType.LIMIT:
            if self.limit_probability is None:
                raise ValueError("limit_probability required for LIMIT orders")
            if self.limit_probability < 0 or self.limit_probability > 1:
                raise ValueError(
                    f"limit_probability must be between 0 and 1, got {self.limit_probability}"
                )


@dataclass
class BetRequestTotal:
    """Total (over/under) bet request from agent to broker"""

    amount: Decimal
    selection: Literal["over", "under"]
    event_id: str
    order_type: OrderType
    total_value: Decimal  # Required for TOTAL bets
    limit_probability: Optional[Decimal] = (
        None  # Required if order_type == LIMIT (0-1 range)
    )

    def validate(self) -> None:
        """Validate bet request parameters"""
        if self.amount <= 0:
            raise ValueError(f"Bet amount must be positive, got {self.amount}")

        if self.order_type == OrderType.LIMIT:
            if self.limit_probability is None:
                raise ValueError("limit_probability required for LIMIT orders")
            if self.limit_probability < 0 or self.limit_probability > 1:
                raise ValueError(
                    f"limit_probability must be between 0 and 1, got {self.limit_probability}"
                )


# Union type for all bet request types
BetRequest = Union[BetRequestMoneyline, BetRequestSpread, BetRequestTotal]


# =============================================================================
# Bet
# =============================================================================


class Bet(BaseModel):
    """Bet record"""

    bet_id: str
    agent_id: str
    event_id: str
    amount: Decimal  # Amount wagered (cost to buy shares)
    selection: Literal["home", "away", "over", "under"]
    probability: Decimal = Field(
        ge=0,
        le=1,
        description="Actual execution probability (0-1). Price per share when bet was placed.",
    )
    shares: Decimal = Field(
        description="Number of shares purchased. Shares = amount / probability"
    )
    order_type: OrderType
    limit_probability: Optional[Decimal] = Field(
        default=None,
        ge=0,
        le=1,
        description="Limit probability threshold (0-1). None for market orders.",
    )
    create_time: datetime
    execution_time: Optional[datetime] = None  # None until executed
    status: BetStatus
    bet_type: BetType = Field(
        default=BetType.MONEYLINE,
        description="Type of bet (default: MONEYLINE for backward compatibility)",
    )
    spread_value: Optional[Decimal] = None  # For SPREAD bets
    total_value: Optional[Decimal] = None  # For TOTAL bets
    actual_payout: Optional[Decimal] = None
    outcome: Optional[BetOutcome] = None
    settlement_time: Optional[datetime] = None


class BetExecutedPayload(BaseModel):
    """Bet execution payload for tracing (broker.bet span)."""

    bet_id: str = ""
    agent_id: str = ""
    event_id: str = ""
    selection: str = ""
    amount: str = ""
    execution_probability: str = ""  # Probability (0-1) at which bet was executed
    shares: str = ""  # Number of shares purchased
    execution_time: str = ""


class BetSettledPayload(BaseModel):
    """Bet settlement payload for tracing."""

    bet_id: str = ""
    event_id: str = ""
    outcome: Optional[BetOutcome] = None  # Store the enum directly
    payout: str = ""
    winner: str = ""


class BrokerStateUpdate(BaseModel):
    """Broker state snapshot for tracing (broker.state_update span)."""

    change_type: str = ""
    accounts_count: int = 0
    bets_count: int = 0
    accounts: Dict[str, Account] = Field(default_factory=dict)
    bets: Dict[str, Bet] = Field(default_factory=dict)
    estimated_net_values: Dict[str, str] = Field(
        default_factory=dict,
        description="Estimated net values for each account (account_id -> net_value)",
    )


# =============================================================================
# Statistics
# =============================================================================


class Statistics(BaseModel):
    """Agent performance statistics"""

    total_bets: int
    total_wagered: Decimal
    wins: int
    losses: int
    win_rate: float
    net_profit: Decimal
    roi: float


class StatisticsList(BaseModel):
    """Statistics for all agents, keyed by agent_id."""

    statistics: Dict[str, Statistics] = Field(
        default_factory=dict, description="Statistics for each agent"
    )


class BrokerFinalStats(BaseModel):
    """Broker state update payload for tracing."""

    accounts_count: int = 0
    bets_count: int = 0
    accounts: Dict[str, Account] = Field(default_factory=dict)
    bets: Dict[str, Bet] = Field(default_factory=dict)
    estimated_net_values: Dict[str, str] = Field(
        default_factory=dict,
        description="Estimated net values for each account (account_id -> net_value)",
    )

    statistics: Dict[str, Statistics] = Field(
        default_factory=dict, description="Statistics for each agent"
    )


# =============================================================================
# Agent Message Models for Tracing
# =============================================================================


class ReasoningStep(BaseModel):
    """Reasoning step in Chain of Thought"""

    step_type: Literal["reasoning"] = "reasoning"
    text: str = Field(default="", description="Reasoning text")


class ToolCallStep(BaseModel):
    """Tool call step in Chain of Thought"""

    step_type: Literal["tool_call"] = "tool_call"
    name: str = Field(default="", description="Tool name")
    input_display: str = Field(default="", description="Formatted input")


class ToolResultStep(BaseModel):
    """Tool result step in Chain of Thought"""

    step_type: Literal["tool_result"] = "tool_result"
    name: str = Field(default="", description="Tool name")
    output_display: str = Field(default="", description="Formatted output")


# Discriminated union for CoT steps
CoTStep = Union[ReasoningStep, ToolCallStep, ToolResultStep]


class AgentResponseMessage(BaseModel):
    """Main agent response with CoT and bet info for OTLP tracing"""

    sequence: int = Field(default=0, description="Event sequence number")
    stream_id: str = Field(default="", description="Stream identifier")
    agent_id: str = Field(default="", description="Agent id")
    content: str = Field(default="", description="Main text response from agent")
    cot_steps: list[CoTStep] = Field(
        default_factory=list, description="Chain of thought process steps"
    )
    trigger: str = Field(default="", description="Event that triggered this response")
    game_id: str = Field(default="", description="Game identifier")
    # Optional bet fields - only included in tracing when a bet is placed
    bet_type: Optional[Literal["MONEYLINE", "SPREAD", "TOTAL"]] = Field(
        default=None, description="Type of bet"
    )
    bet_amount: Optional[float] = Field(default=None, description="Bet amount")
    bet_selection: Optional[Literal["home", "away", "over", "under"]] = Field(
        default=None, description="Selection: 'home', 'away', 'over', or 'under'"
    )
    bet_order_type: Optional[Literal["MARKET", "LIMIT"]] = Field(
        default=None, description="Order type: 'MARKET' or 'LIMIT'"
    )
    bet_limit_probability: Optional[float] = Field(
        default=None, description="Limit probability for LIMIT orders (0-1)"
    )
    bet_spread_value: Optional[float] = Field(
        default=None, description="Spread value for SPREAD bets"
    )
    bet_total_value: Optional[float] = Field(
        default=None, description="Total value for TOTAL (over/under) bets"
    )

    @field_serializer("content")
    def _sanitize_content(self, value: str) -> str:
        """Sanitize content for compliance and UI copy."""
        sanitized = value.replace("$", "")
        sanitized = re.sub(r"\bBet\b", "Play", sanitized)
        sanitized = re.sub(r"\bbet\b", "play", sanitized)
        sanitized = re.sub(r"\bbets\b", "plays", sanitized)
        sanitized = re.sub(r"\bbetting\b", "playing", sanitized)
        return sanitized


class AgentInfo(BaseModel):
    """Agent registration payload for tracing (agent.agent_initialize span)."""

    agent_id: str = Field(default="", description="Unique ID for the agent")
    persona: str = Field(
        default="", description="Agent persona tag (e.g., 'degen', 'whale', 'shark')"
    )
    model: str = Field(default="", description="Exact model name (e.g., qwen3-max)")
    model_display_name: str = Field(
        default="", description="Human-readable model name (e.g., qwen, claude)"
    )
    system_prompt: str = Field(default="", description="Agent's system prompt")
    cdn_url: str = Field(default="", description="Avatar image URL")

    @computed_field  # type: ignore[misc]
    @property
    def avatar(self) -> str:
        """Computed avatar: first character of persona, uppercased."""
        return self.persona[0].upper() if self.persona else "?"

    @computed_field  # type: ignore[misc]
    @property
    def color(self) -> str:
        """Computed color: deterministic color based on agent_id."""
        colors = [
            "#3B82F6",  # blue
            "#8B5CF6",  # purple
            "#10B981",  # green
            "#F59E0B",  # amber
            "#EF4444",  # red
            "#EC4899",  # pink
            "#14B8A6",  # teal
            "#6366F1",  # indigo
        ]
        # Hash agent_id to index
        hash_val = sum(ord(c) for c in self.agent_id)
        return colors[hash_val % len(colors)]


class AgentList(BaseModel):
    """Batch agent registration payload for initializing multiple agents at once."""

    agents: List[AgentInfo] = Field(
        default_factory=list,
        description="List of agents to register. Each agent is identified by agent_id.",
    )


__all__ = [
    # Enums
    "BetOutcome",
    "BetStatus",
    "BetType",
    "EventStatus",
    "OrderType",
    "VALID_STATUS_TRANSITIONS",
    # Models
    "Account",
    "Bet",
    "BetExecutedPayload",
    "BetRequest",
    "BetRequestMoneyline",
    "BetRequestSpread",
    "BetRequestTotal",
    "BetSettledPayload",
    "BettingEvent",
    "BrokerStateUpdate",
    "BrokerFinalStats",
    "Holding",
    "Statistics",
    "StatisticsList",
    # Agent Message Models
    "ReasoningStep",
    "ToolCallStep",
    "ToolResultStep",
    "CoTStep",
    "AgentResponseMessage",
    "AgentInfo",
    "AgentList",
]
