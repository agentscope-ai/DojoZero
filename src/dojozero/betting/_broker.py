"""Sports Betting Broker Operator

This module implements a generic betting broker operator that manages:
- Account balances
- Event lifecycle (pregame, odds updates, game start/end, settlement)
- Bet placement and execution (market and limit orders)
- Bet settlement

This broker is sport-agnostic and can be used for NBA, NFL, or any other sports betting.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Literal, Optional, Union

import asyncio
import logging
import uuid
from collections import defaultdict
from typing import List, Sequence, Set, TypedDict

from pydantic import BaseModel, Field, TypeAdapter, computed_field

from dojozero.core import (
    Agent,
    Operator,
    OperatorBase,
    RuntimeContext,
    StreamEvent,
)
from dojozero.core._tracing import create_span_from_event, emit_span
from dojozero.data._models import (
    BaseGameUpdateEvent,
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
    OddsInfo,
    OddsUpdateEvent,
)

# Logger for broker operations
logger = logging.getLogger(__name__)

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


class BettingPhase(Enum):
    """Phase when bet is placed"""

    PRE_GAME = "PRE_GAME"  # Before game starts
    IN_GAME = "IN_GAME"  # During game (live betting)


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


class Account(BaseModel):
    """Agent account information"""

    agent_id: str
    balance: Decimal
    created_at: datetime
    last_updated: datetime


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
    home_odds: Optional[Decimal] = Field(
        default=None, description="Can be None initially, filled in when odds arrive"
    )
    away_odds: Optional[Decimal] = Field(
        default=None, description="Can be None initially, filled in when odds arrive"
    )
    # Multiple spreads: spread_value -> {home_odds, away_odds}
    spread_lines: Dict[Decimal, Dict[str, Decimal]] = Field(
        default_factory=dict, description="Spread betting lines"
    )
    # Multiple totals: total_value -> {over_odds, under_odds}
    total_lines: Dict[Decimal, Dict[str, Decimal]] = Field(
        default_factory=dict, description="Total (over/under) betting lines"
    )
    last_odds_update: Optional[datetime] = None
    betting_closed_at: Optional[datetime] = None

    @computed_field
    def can_bet_pregame(self) -> bool:
        """True if PRE_GAME betting is allowed (status=SCHEDULED)."""
        return self.status == EventStatus.SCHEDULED

    @computed_field
    def can_bet_ingame(self) -> bool:
        """True if IN_GAME betting is allowed (status=LIVE)."""
        return self.status == EventStatus.LIVE


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
    betting_phase: BettingPhase
    limit_odds: Optional[Decimal] = None  # Required if order_type == LIMIT

    def validate(self) -> None:
        """Validate bet request parameters"""
        if self.amount <= 0:
            raise ValueError(f"Bet amount must be positive, got {self.amount}")

        if self.order_type == OrderType.LIMIT:
            if self.limit_odds is None:
                raise ValueError("limit_odds required for LIMIT orders")
            if self.limit_odds <= 1.0:
                raise ValueError(
                    f"limit_odds must be greater than 1.0, got {self.limit_odds}"
                )


@dataclass
class BetRequestSpread:
    """Spread bet request from agent to broker"""

    amount: Decimal
    selection: Literal["home", "away"]
    event_id: str
    order_type: OrderType
    betting_phase: BettingPhase
    spread_value: Decimal  # Required for SPREAD bets
    limit_odds: Optional[Decimal] = None  # Required if order_type == LIMIT

    def validate(self) -> None:
        """Validate bet request parameters"""
        if self.amount <= 0:
            raise ValueError(f"Bet amount must be positive, got {self.amount}")

        if self.order_type == OrderType.LIMIT:
            if self.limit_odds is None:
                raise ValueError("limit_odds required for LIMIT orders")
            if self.limit_odds <= 1.0:
                raise ValueError(
                    f"limit_odds must be greater than 1.0, got {self.limit_odds}"
                )


@dataclass
class BetRequestTotal:
    """Total (over/under) bet request from agent to broker"""

    amount: Decimal
    selection: Literal["over", "under"]
    event_id: str
    order_type: OrderType
    betting_phase: BettingPhase
    total_value: Decimal  # Required for TOTAL bets
    limit_odds: Optional[Decimal] = None  # Required if order_type == LIMIT

    def validate(self) -> None:
        """Validate bet request parameters"""
        if self.amount <= 0:
            raise ValueError(f"Bet amount must be positive, got {self.amount}")

        if self.order_type == OrderType.LIMIT:
            if self.limit_odds is None:
                raise ValueError("limit_odds required for LIMIT orders")
            if self.limit_odds <= 1.0:
                raise ValueError(
                    f"limit_odds must be greater than 1.0, got {self.limit_odds}"
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
    amount: Decimal
    selection: str
    odds: Decimal  # Actual execution odds
    order_type: OrderType
    limit_odds: Optional[Decimal] = None  # None for market orders
    betting_phase: BettingPhase
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


@dataclass
class BetExecutedPayload:
    bet_id: str
    agent_id: str
    event_id: str
    selection: str
    amount: str
    execution_odds: str
    execution_time: str


@dataclass
class BetSettledPayload:
    bet_id: str
    agent_id: str
    event_id: str
    outcome: BetOutcome  # Store the enum directly
    payout: str
    winner: str


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


# =============================================================================
# Configuration
# =============================================================================


class _ActorIdConfig(TypedDict):
    actor_id: str


class BrokerOperatorConfig(_ActorIdConfig, total=False):
    """Configuration for BrokerOperator"""

    initial_balance: str  # Initial balance for all agents (as string for Decimal)
    allowed_tools: list[str]  # List of allowed agent tool names (default: all tools)


# =============================================================================
# Broker Operator
# =============================================================================


class BrokerOperator(OperatorBase, Operator[BrokerOperatorConfig]):
    """
    Betting Broker Operator for Sports Betting.

    Manages:
    - Agent account balances
    - Event lifecycle (pregame → live → closed → settled)
    - Bet placement (market and limit orders)
    - Order matching and execution
    - Bet settlement based on event results

    This is a sport-agnostic broker that works with any sport by handling
    generic game lifecycle events (initialize, start, result) and odds updates.
    """

    def __init__(self, config: BrokerOperatorConfig, trial_id: str):
        super().__init__(config["actor_id"], trial_id)

        # Account management
        self._accounts: Dict[str, Account] = {}
        self._agent_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        # Event management
        self._events: Dict[str, BettingEvent] = {}
        self._event_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        # Pending team info from GameUpdateEvent (waiting for OddsUpdateEvent)
        # Maps event_id -> {"home_team": str, "away_team": str, "game_time": datetime}
        self._pending_team_info: Dict[str, Dict[str, Any]] = {}

        # Pending status events that arrived before GameInitializeEvent
        # Maps event_id -> list of (status, typed event) tuples to apply when event is registered
        self._pending_status_events: Dict[
            str, List[tuple[str, GameStartEvent | GameResultEvent]]
        ] = defaultdict(list)

        # Pending odds that arrived before GameInitializeEvent
        # Maps event_id -> OddsInfo (structured odds from OddsUpdateEvent)
        self._pending_odds: Dict[str, OddsInfo] = {}

        # Bet management
        self._bets: Dict[str, Bet] = {}

        # Agent-indexed bet tracking
        self._active_bets: Dict[str, List[str]] = defaultdict(list)
        self._pending_orders: Dict[str, List[str]] = defaultdict(list)
        self._bet_history: Dict[str, List[str]] = defaultdict(list)

        # Event-indexed bet tracking
        self._event_active_bets: Dict[str, Set[str]] = defaultdict(set)
        self._event_pending_orders: Dict[str, Set[str]] = defaultdict(set)

        # Global lock for atomic state snapshots during logging
        self._state_snapshot_lock: asyncio.Lock = asyncio.Lock()

        # Configuration
        self.initial_balance = config.get("initial_balance", "0")
        # Default to all tools if not specified (None means all tools allowed)
        self.allowed_tools = config.get("allowed_tools", None)

    @classmethod
    def from_dict(
        cls, config: BrokerOperatorConfig, context: RuntimeContext
    ) -> "BrokerOperator":
        """Create broker from configuration dictionary."""
        return cls(config, context.trial_id)

    async def start(self) -> None:
        """Protocol hook: called before traffic is routed"""
        logger.info(
            "Operator '%s' starting (accounts=%d, events=%d, bets=%d)",
            self.actor_id,
            len(self._accounts),
            len(self._events),
            len(self._bets),
        )

    async def stop(self) -> None:
        """Protocol hook: called during shutdown"""
        logger.info(
            "Operator '%s' stopping - accounts=%d, events=%d, bets=%d",
            self.actor_id,
            len(self._accounts),
            len(self._events),
            len(self._bets),
        )

    async def register_agents(self, agents: Sequence[Agent]) -> None:
        """Register agents and create their accounts"""
        super().register_agents(agents)
        for agent in agents:
            if agent.actor_id not in self._accounts:
                await self.create_account(agent.actor_id, Decimal(self.initial_balance))

    # =========================================================================
    # Event Stream Processing
    # =========================================================================

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        """Process incoming stream events and delegate to appropriate handlers.

        Expects StreamEvent.payload to be a typed DataEvent (GameInitializeEvent,
        OddsUpdateEvent, GameStartEvent, GameResultEvent, or BaseGameUpdateEvent).
        Uses isinstance dispatch for type-safe handling.
        """
        try:
            data_event = event.payload

            # Type check - ensure it's one of our game events
            # Events use game_id to identify the game they belong to
            if not hasattr(data_event, "game_id"):
                logger.warning(
                    "Event missing game_id attribute: type=%s, payload=%s",
                    type(data_event),
                    data_event,
                )
                return

            event_id = data_event.game_id
            if not event_id:
                logger.error("Event missing game_id: %s", data_event)
                return

            async with self._event_locks[
                event_id
            ]:  # avoid multiple streamEvent change the same event
                # Log every incoming event
                event_type_str = getattr(data_event, "event_type", "unknown")
                logger.info(
                    "Received event: type=%s, event_id=%s, stream_id=%s, timestamp=%s",
                    event_type_str,
                    event_id,
                    event.stream_id,
                    getattr(data_event, "timestamp", None),
                )

                # Dispatch using isinstance for type-safe handling
                # Order matters: check specific types before base types
                if isinstance(data_event, GameInitializeEvent):
                    await self._handle_game_initialize(data_event, event_id)

                elif isinstance(data_event, OddsUpdateEvent):
                    await self._handle_odds_update(data_event, event_id)

                elif isinstance(data_event, GameResultEvent):
                    await self._handle_game_result(data_event, event_id)

                elif isinstance(data_event, GameStartEvent):
                    await self._handle_game_start(data_event, event_id)

                elif isinstance(data_event, BaseGameUpdateEvent):
                    await self._handle_game_update(data_event, event_id)

                else:
                    logger.debug("Unhandled event type: %s", event_type_str)

        except Exception as e:
            logger.error("Failed to handle stream event: %s", e, exc_info=True)

    async def _handle_game_initialize(
        self, data_event: GameInitializeEvent, event_id: str
    ) -> None:
        """Handle game initialization event."""
        # TeamIdentity.__str__() returns .name; plain str passes through
        home_team_str = str(data_event.home_team)
        away_team_str = str(data_event.away_team)
        game_time_dt = data_event.game_time

        if not home_team_str or not away_team_str:
            logger.warning(
                "GameInitializeEvent missing team info: event_id=%s",
                event_id,
            )
            return

        if event_id in self._events:
            # Event already exists - update team info if needed
            broker_event = self._events[event_id]
            broker_event.home_team = home_team_str
            broker_event.away_team = away_team_str
            broker_event.game_time = game_time_dt
            logger.info(
                "Updated event from GameInitializeEvent: event_id=%s, home_team=%s, away_team=%s, game_time=%s",
                event_id,
                home_team_str,
                away_team_str,
                game_time_dt,
            )
        else:
            # Initialize new event without odds (will be filled in when OddsUpdateEvent arrives)
            logger.info(
                "Initializing event from GameInitializeEvent: event_id=%s, home_team=%s, away_team=%s, game_time=%s (odds pending)",
                event_id,
                home_team_str,
                away_team_str,
                game_time_dt,
            )
            await self._initialize_event(
                event_id=event_id,
                home_team=home_team_str,
                away_team=away_team_str,
                game_time=game_time_dt,
                initial_home_odds=None,
                initial_away_odds=None,
            )

            # Apply any pending status events that arrived before this GameInitializeEvent
            await self._apply_pending_status_events(event_id)

            # Apply any pending odds that arrived before this GameInitializeEvent
            await self._apply_pending_odds(event_id)

    async def _apply_pending_odds(self, event_id: str) -> None:
        """Apply any buffered odds for a newly registered event.

        This handles the race condition where OddsUpdateEvent arrives before
        GameInitializeEvent due to different API polling intervals.
        """
        if event_id not in self._pending_odds:
            return

        pending_odds = self._pending_odds.pop(event_id)

        # Extract moneyline from the stored OddsInfo
        home_odds: Decimal | None = None
        away_odds: Decimal | None = None
        if pending_odds.moneyline:
            home_odds = Decimal(str(pending_odds.moneyline.home_odds))
            away_odds = Decimal(str(pending_odds.moneyline.away_odds))

        # Extract spreads
        spread_updates: list[dict[str, Any]] = []
        for sp in pending_odds.spreads:
            spread_updates.append(
                {
                    "spread": sp.spread,
                    "home_odds": sp.home_odds,
                    "away_odds": sp.away_odds,
                }
            )

        # Extract totals
        total_updates: list[dict[str, Any]] = []
        for t in pending_odds.totals:
            total_updates.append(
                {
                    "total": t.total,
                    "over_odds": t.over_odds,
                    "under_odds": t.under_odds,
                }
            )

        logger.info(
            "Applying pending odds for event %s: home_odds=%s, away_odds=%s",
            event_id,
            home_odds,
            away_odds,
        )

        try:
            await self._update_odds(
                event_id=event_id,
                home_odds=home_odds,
                away_odds=away_odds,
                spread_updates=spread_updates,
                total_updates=total_updates,
            )
        except Exception as e:
            logger.error(
                "Failed to apply pending odds for event %s: %s",
                event_id,
                e,
            )

    async def _apply_pending_status_events(self, event_id: str) -> None:
        """Apply any buffered status events for a newly registered event.

        This handles the race condition where GameStartEvent or GameResultEvent
        arrives before GameInitializeEvent due to different API polling intervals.
        """
        if event_id not in self._pending_status_events:
            return

        pending_events = self._pending_status_events.pop(event_id)
        if not pending_events:
            return

        logger.info(
            "Applying %d pending status events for event %s",
            len(pending_events),
            event_id,
        )

        for event_type, data_event in pending_events:
            try:
                if event_type == "game_start":
                    logger.info(
                        "Applying buffered game_start for event %s",
                        event_id,
                    )
                    await self._update_event_status(
                        event_id=event_id, status=EventStatus.LIVE
                    )
                elif event_type == "game_result" and isinstance(
                    data_event, GameResultEvent
                ):
                    logger.info(
                        "Applying buffered game_result for event %s",
                        event_id,
                    )
                    await self._update_event_status(
                        event_id=event_id, status=EventStatus.CLOSED
                    )
                    await self._settle_event(
                        event_id=event_id,
                        winner=data_event.winner,
                        final_score=data_event.final_score,
                    )
            except Exception as e:
                logger.error(
                    "Failed to apply pending %s for event %s: %s",
                    event_type,
                    event_id,
                    e,
                )

    async def _handle_odds_update(
        self, data_event: OddsUpdateEvent, event_id: str
    ) -> None:
        """Handle odds update event. Supports moneyline, spreads, and totals."""
        odds_info = data_event.odds

        # Extract moneyline decimal odds from structured OddsInfo
        home_odds: float | None = None
        away_odds: float | None = None
        if odds_info.moneyline:
            home_odds = odds_info.moneyline.home_odds
            away_odds = odds_info.moneyline.away_odds

        # Extract all spread lines from OddsInfo
        spread_updates: list[dict[str, Any]] = []
        for sp in odds_info.spreads:
            spread_updates.append(
                {
                    "spread": sp.spread,
                    "home_odds": sp.home_odds,
                    "away_odds": sp.away_odds,
                }
            )

        # Extract all total lines from OddsInfo
        total_updates: list[dict[str, Any]] = []
        for t in odds_info.totals:
            total_updates.append(
                {
                    "total": t.total,
                    "over_odds": t.over_odds,
                    "under_odds": t.under_odds,
                }
            )

        # Check if we have any odds to update
        has_moneyline = home_odds is not None and away_odds is not None
        has_spreads = len(spread_updates) > 0
        has_totals = len(total_updates) > 0

        if not (has_moneyline or has_spreads or has_totals):
            logger.debug(
                "OddsUpdateEvent missing valid odds: event_id=%s",
                event_id,
            )
            return

        # Only process odds if we have team info (either from existing event or pending GameUpdateEvent)
        if event_id in self._events:
            # Event exists - update odds (supports partial updates)
            logger.info(
                "Updating odds: event_id=%s, home_odds=%s, away_odds=%s, spreads=%d, totals=%d",
                event_id,
                home_odds,
                away_odds,
                len(spread_updates),
                len(total_updates),
            )
            await self._update_odds(
                event_id=event_id,
                home_odds=Decimal(str(home_odds)) if home_odds else None,
                away_odds=Decimal(str(away_odds)) if away_odds else None,
                spread_updates=spread_updates,
                total_updates=total_updates,
            )
        elif event_id in self._pending_team_info:
            # We have team info from GameUpdateEvent - initialize event with odds
            team_info = self._pending_team_info[event_id]
            logger.info(
                "Initializing event from OddsUpdateEvent (team info already available): event_id=%s, home_team=%s, away_team=%s, home_odds=%s, away_odds=%s, spreads=%d",
                event_id,
                team_info.get("home_team"),
                team_info.get("away_team"),
                home_odds,
                away_odds,
                len(spread_updates),
            )
            # Initialize with moneyline odds (if available)
            await self._initialize_event(
                event_id=event_id,
                home_team=team_info["home_team"],
                away_team=team_info["away_team"],
                game_time=team_info["game_time"],
                initial_home_odds=Decimal(str(home_odds)) if home_odds else None,
                initial_away_odds=Decimal(str(away_odds)) if away_odds else None,
            )
            # Update spreads/totals if provided
            if spread_updates or total_updates:
                await self._update_odds(
                    event_id=event_id,
                    spread_updates=spread_updates,
                    total_updates=total_updates,
                )
            # Clear pending team info
            del self._pending_team_info[event_id]
        else:
            # No team info yet - store OddsInfo for later when event is initialized
            logger.info(
                "Storing pending odds (waiting for event initialization): event_id=%s, home_odds=%s, away_odds=%s",
                event_id,
                home_odds,
                away_odds,
            )
            self._pending_odds[event_id] = odds_info

    async def _handle_game_update(
        self, data_event: BaseGameUpdateEvent, event_id: str
    ) -> None:
        """Handle game update event (NBAGameUpdateEvent, NFLGameUpdateEvent, etc.)."""
        # Extract team names from sport-specific typed stats models
        home_team_str: str | None = None
        away_team_str: str | None = None
        game_time_dt: datetime | None = None

        # NBAGameUpdateEvent has home_team_stats: NBATeamGameStats
        # NFLGameUpdateEvent has home_team_stats: NFLTeamGameStats
        # Both have .team_city/.team_name (NBA) or .team_name/.team_abbreviation (NFL)
        if hasattr(data_event, "home_team_stats"):
            stats = data_event.home_team_stats  # type: ignore[attr-defined]
            name = getattr(stats, "team_name", "")
            city = getattr(stats, "team_city", "")
            if city and name:
                home_team_str = f"{city} {name}".strip()
            elif name:
                home_team_str = name

        if hasattr(data_event, "away_team_stats"):
            stats = data_event.away_team_stats  # type: ignore[attr-defined]
            name = getattr(stats, "team_name", "")
            city = getattr(stats, "team_city", "")
            if city and name:
                away_team_str = f"{city} {name}".strip()
            elif name:
                away_team_str = name

        # Extract game_time_utc if available (on BaseGameUpdateEvent)
        if data_event.game_time_utc:
            try:
                game_time_dt = datetime.fromisoformat(
                    data_event.game_time_utc.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                pass

        # Only process if we have both team names
        if not home_team_str or not away_team_str:
            logger.debug(
                "GameUpdateEvent missing team info: event_id=%s, home_team=%s, away_team=%s",
                event_id,
                home_team_str,
                away_team_str,
            )
            return

        # Use current time as fallback if game_time_utc not available
        if not game_time_dt:
            game_time_dt = datetime.now()

        if event_id in self._events:
            # Event exists - update team names and game_time
            broker_event = self._events[event_id]
            broker_event.home_team = home_team_str
            broker_event.away_team = away_team_str
            broker_event.game_time = game_time_dt
            logger.info(
                "Updated event from GameUpdateEvent: event_id=%s, home_team=%s, away_team=%s, game_time=%s",
                event_id,
                home_team_str,
                away_team_str,
                game_time_dt,
            )
        else:
            # Event doesn't exist yet - store team info for when OddsUpdateEvent arrives
            self._pending_team_info[event_id] = {
                "home_team": home_team_str,
                "away_team": away_team_str,
                "game_time": game_time_dt,
            }
            logger.info(
                "Stored team info from GameUpdateEvent (waiting for OddsUpdateEvent): event_id=%s, home_team=%s, away_team=%s, game_time=%s",
                event_id,
                home_team_str,
                away_team_str,
                game_time_dt,
            )

    async def _handle_game_start(
        self, data_event: GameStartEvent, event_id: str
    ) -> None:
        """Handle game start event.

        If the event is registered, update its status to LIVE.
        If not registered, buffer the event to apply when GameInitializeEvent arrives.
        """
        if event_id not in self._events:
            logger.info(
                "Buffering game_start for event %s (GameInitializeEvent not yet received)",
                event_id,
            )
            self._pending_status_events[event_id].append(("game_start", data_event))
            return

        await self._update_event_status(event_id=event_id, status=EventStatus.LIVE)

    async def _handle_game_result(
        self, data_event: GameResultEvent, event_id: str
    ) -> None:
        """Handle game result event.

        If the event is registered, close it and settle bets.
        If not registered, buffer the event to apply when GameInitializeEvent arrives.
        """
        if event_id not in self._events:
            logger.info(
                "Buffering game_result for event %s (GameInitializeEvent not yet received)",
                event_id,
            )
            self._pending_status_events[event_id].append(("game_result", data_event))
            return

        await self._update_event_status(event_id=event_id, status=EventStatus.CLOSED)
        await self._settle_event(
            event_id=event_id,
            winner=data_event.winner,
            final_score=data_event.final_score,
        )

    async def _initialize_event(
        self,
        event_id: str,
        home_team: str,
        away_team: str,
        game_time: datetime,
        initial_home_odds: Optional[Decimal] = None,
        initial_away_odds: Optional[Decimal] = None,
    ) -> BettingEvent:
        """Initialize a new betting event.

        This is an internal method. Events are initialized via GameInitializeEvent
        through handle_stream_event.

        Args:
            event_id: Unique event identifier
            home_team: Home team name
            away_team: Away team name
            game_time: Scheduled game time
            initial_home_odds: Optional initial home odds (can be None if not yet available)
            initial_away_odds: Optional initial away odds (can be None if not yet available)
        """

        if event_id in self._events:
            raise ValueError(f"Event {event_id} already exists")

        # Validate odds if provided
        if initial_home_odds is not None and initial_home_odds <= 1.0:
            raise ValueError("Odds must be greater than 1.0")
        if initial_away_odds is not None and initial_away_odds <= 1.0:
            raise ValueError("Odds must be greater than 1.0")

        now = datetime.now()
        betting_event = BettingEvent(
            event_id=event_id,
            home_team=home_team,
            away_team=away_team,
            game_time=game_time,
            status=EventStatus.SCHEDULED,
            home_odds=initial_home_odds,
            away_odds=initial_away_odds,
            last_odds_update=now if (initial_home_odds and initial_away_odds) else None,
        )

        self._events[event_id] = betting_event
        if initial_home_odds and initial_away_odds:
            logger.info(
                "Created event %s: %s vs %s (Odds: %s/%s)",
                event_id,
                home_team,
                away_team,
                initial_home_odds,
                initial_away_odds,
            )
        else:
            logger.info(
                "Created event %s: %s vs %s (Odds: pending)",
                event_id,
                home_team,
                away_team,
            )
        return betting_event

    async def _update_odds(
        self,
        event_id: str,
        home_odds: Optional[Decimal] = None,
        away_odds: Optional[Decimal] = None,
        spread_updates: Optional[List[Dict[str, Any]]] = None,
        total_updates: Optional[List[Dict[str, Any]]] = None,
    ) -> BettingEvent:
        """Update odds for an event and execute matching limit orders.

        This is an internal method. Odds are updated via OddsUpdateEvent
        through handle_stream_event.

        Supports partial updates - only provided odds are updated.
        Backward compatible: existing calls with just home_odds/away_odds still work.

        Args:
            event_id: Event identifier
            home_odds: Optional moneyline home odds
            away_odds: Optional moneyline away odds
            spread_updates: Optional list of spread updates [{"spread": -3.5, "home_odds": 1.90, "away_odds": 1.90}, ...]
            total_updates: Optional list of total updates [{"total": 220.5, "over_odds": 1.88, "under_odds": 1.88}, ...]
        """
        if event_id not in self._events:
            raise ValueError(f"Event {event_id} not found")

        betting_event = self._events[event_id]

        if betting_event.status not in [EventStatus.SCHEDULED, EventStatus.LIVE]:
            raise ValueError(
                f"Cannot update odds for {betting_event.status.value} event"
            )

        # Update moneyline odds (if provided)
        if home_odds is not None:
            if home_odds <= 1.0:
                raise ValueError("Odds must be greater than 1.0")
            betting_event.home_odds = home_odds
        if away_odds is not None:
            if away_odds <= 1.0:
                raise ValueError("Odds must be greater than 1.0")
            betting_event.away_odds = away_odds

        # Update spread lines (if provided)
        if spread_updates:
            # Ensure spread_lines is initialized (protection against None)
            if betting_event.spread_lines is None:
                betting_event.spread_lines = {}

            for update in spread_updates:
                spread_value = Decimal(str(update["spread"]))
                home_spread_odds = Decimal(str(update["home_odds"]))
                away_spread_odds = Decimal(str(update["away_odds"]))

                if home_spread_odds <= 1.0 or away_spread_odds <= 1.0:
                    raise ValueError("Odds must be greater than 1.0")

                betting_event.spread_lines[spread_value] = {
                    "home_odds": home_spread_odds,
                    "away_odds": away_spread_odds,
                }
                logger.debug(
                    "Updated spread line: event_id=%s, spread=%s, home_odds=%s, away_odds=%s",
                    event_id,
                    spread_value,
                    home_spread_odds,
                    away_spread_odds,
                )

        # Update total lines (if provided)
        if total_updates:
            # Ensure total_lines is initialized (protection against None)
            if betting_event.total_lines is None:
                betting_event.total_lines = {}

            for update in total_updates:
                total_value = Decimal(str(update["total"]))
                over_odds = Decimal(str(update["over_odds"]))
                under_odds = Decimal(str(update["under_odds"]))

                if over_odds <= 1.0 or under_odds <= 1.0:
                    raise ValueError("Odds must be greater than 1.0")

                betting_event.total_lines[total_value] = {
                    "over_odds": over_odds,
                    "under_odds": under_odds,
                }
                logger.debug(
                    "Updated total line: event_id=%s, total=%s, over_odds=%s, under_odds=%s",
                    event_id,
                    total_value,
                    over_odds,
                    under_odds,
                )

        betting_event.last_odds_update = datetime.now()

        logger.info(
            "Updated odds for event %s: home=%s, away=%s, spreads=%d, totals=%d (status=%s)",
            event_id,
            home_odds,
            away_odds,
            len(spread_updates) if spread_updates else 0,
            len(total_updates) if total_updates else 0,
            betting_event.status.value,
        )

        # Check and execute matching limit orders for all bet types
        await self._check_limit_orders(event_id)

        return betting_event

    async def _check_limit_orders(self, event_id: str) -> None:
        """Check and execute matching limit orders for all bet types.

        Backward compatible: Only checks moneyline if spreads/totals not available.
        """
        pending_bet_ids = list(self._event_pending_orders.get(event_id, set()))
        betting_event = self._events[event_id]

        for bet_id in pending_bet_ids:
            bet = self._bets[bet_id]
            if bet.status != BetStatus.PENDING:
                continue

            should_execute = False
            execution_odds = Decimal(0)

            # Default to moneyline if bet_type not set (backward compatibility)
            bet_type = getattr(bet, "bet_type", BetType.MONEYLINE)

            if bet_type == BetType.MONEYLINE:
                # Moneyline limit orders (existing behavior)
                if bet.limit_odds is None:
                    continue  # Skip if no limit odds set
                if betting_event.home_odds is not None and bet.selection == "home":
                    if betting_event.home_odds >= bet.limit_odds:
                        should_execute = True
                        execution_odds = betting_event.home_odds
                elif betting_event.away_odds is not None and bet.selection == "away":
                    if betting_event.away_odds >= bet.limit_odds:
                        should_execute = True
                        execution_odds = betting_event.away_odds

            elif bet_type == BetType.SPREAD:
                # Spread limit orders
                if bet.limit_odds is None:
                    continue  # Skip if no limit odds set
                spread_value = getattr(bet, "spread_value", None)
                # Protection: ensure spread_lines is not None
                if (
                    spread_value
                    and betting_event.spread_lines is not None
                    and spread_value in betting_event.spread_lines
                ):
                    spread_line = betting_event.spread_lines[spread_value]
                    if bet.selection == "home":
                        if spread_line["home_odds"] >= bet.limit_odds:
                            should_execute = True
                            execution_odds = spread_line["home_odds"]
                    elif bet.selection == "away":
                        if spread_line["away_odds"] >= bet.limit_odds:
                            should_execute = True
                            execution_odds = spread_line["away_odds"]

            elif bet_type == BetType.TOTAL:
                # Total limit orders
                if bet.limit_odds is None:
                    continue  # Skip if no limit odds set
                total_value = getattr(bet, "total_value", None)
                # Protection: ensure total_lines is not None
                if (
                    total_value
                    and betting_event.total_lines is not None
                    and total_value in betting_event.total_lines
                ):
                    total_line = betting_event.total_lines[total_value]
                    if bet.selection == "over":
                        if total_line["over_odds"] >= bet.limit_odds:
                            should_execute = True
                            execution_odds = total_line["over_odds"]
                    elif bet.selection == "under":
                        if total_line["under_odds"] >= bet.limit_odds:
                            should_execute = True
                            execution_odds = total_line["under_odds"]

            if should_execute:
                await self._match_bet(bet, execution_odds)

    async def _update_event_status(self, event_id: str, status: EventStatus) -> None:
        """Update event status and perform status-specific actions.

        This is an internal method. Use GameStartEvent/GameResultEvent handlers
        for proper event lifecycle management.
        """
        if event_id not in self._events:
            raise ValueError(f"Event {event_id} not found")

        betting_event = self._events[event_id]

        # No-op if already in target status (important for checkpoint resume)
        if betting_event.status == status:
            logger.debug(
                "Event %s already in status %s, skipping transition",
                event_id,
                status.value,
            )
            return

        # Validate status transition
        valid_transitions = VALID_STATUS_TRANSITIONS.get(betting_event.status, set())
        if status not in valid_transitions:
            raise ValueError(
                f"Invalid status transition: {betting_event.status.value} → {status.value}"
            )

        logger.info(
            "Event %s status changed: %s → %s",
            event_id,
            betting_event.status.value,
            status.value,
        )

        betting_event.status = status

        if status == EventStatus.LIVE:
            # Game started - reject pre-game bets and cancel unfilled pre-game orders
            betting_event.betting_closed_at = datetime.now()
            await self._cancel_pregame_orders(event_id)

        elif status == EventStatus.CLOSED:
            # Game ended - reject all bets and cancel all pending orders
            betting_event.betting_closed_at = datetime.now()
            await self._cancel_all_pending_orders(event_id)

    async def _settle_event(
        self, event_id: str, winner: str, final_score: Dict[str, int]
    ) -> None:
        """Settle all active bets for a completed event.

        This is an internal method called by _handle_game_result.
        """
        if event_id not in self._events:
            raise ValueError(f"Event {event_id} not found")

        betting_event = self._events[event_id]

        if betting_event.status != EventStatus.CLOSED:
            raise ValueError(
                f"Cannot settle event with status {betting_event.status.value}, must be CLOSED"
            )

        if winner not in ["home", "away"]:
            raise ValueError(f"Invalid winner: {winner}")

        # Validate final_score is present and has required keys (needed for spread/total betting)
        if not final_score or "home" not in final_score or "away" not in final_score:
            raise ValueError(
                f"final_score must contain 'home' and 'away' keys, got: {final_score}"
            )

        logger.info(
            "Settling event %s - Winner: %s, Score: %s",
            event_id,
            winner,
            final_score,
        )

        # Get all active bets for this event
        active_bet_ids = list(self._event_active_bets.get(event_id, set()))

        # Settle each bet
        settled_count = 0
        for bet_id in active_bet_ids:
            bet = self._bets[bet_id]
            if bet.status == BetStatus.ACTIVE:
                await self._settle_bet(bet, winner, final_score)
                settled_count += 1

        # Update event status
        betting_event.status = EventStatus.SETTLED

        logger.info(
            "Completed settlement for event %s - Settled %d bets",
            event_id,
            settled_count,
        )

    async def _settle_bet(
        self, bet: Bet, winner: str, final_score: Dict[str, int]
    ) -> None:
        """Settle a single bet. Supports moneyline, spread, and total betting.

        Backward compatible: Defaults to moneyline settlement if bet_type not set.
        """
        async with self._agent_locks[bet.agent_id]:
            # Determine bet type (default to moneyline for backward compatibility)
            bet_type = getattr(bet, "bet_type", BetType.MONEYLINE)
            is_win = False

            if bet_type == BetType.MONEYLINE:
                # Moneyline settlement (existing behavior - backward compatible)
                is_win = bet.selection == winner

            elif bet_type == BetType.SPREAD:
                # Spread settlement
                spread_value = getattr(bet, "spread_value", None)
                if spread_value is None:
                    raise ValueError(
                        f"Bet {bet.bet_id} is SPREAD type but missing spread_value"
                    )

                home_score = final_score["home"]
                away_score = final_score["away"]

                if bet.selection == "home":
                    # Home team must win by more than the spread
                    # spread_value is negative (e.g., -3.5), so we add it to home score
                    adjusted_home_score = home_score + spread_value
                    is_win = adjusted_home_score > away_score
                else:  # away
                    # Away team must win (or lose by less than spread)
                    # spread_value is positive (e.g., +3.5), so we add it to away score
                    adjusted_away_score = away_score + spread_value
                    is_win = adjusted_away_score > home_score

            elif bet_type == BetType.TOTAL:
                # Total settlement
                total_value = getattr(bet, "total_value", None)
                if total_value is None:
                    raise ValueError(
                        f"Bet {bet.bet_id} is TOTAL type but missing total_value"
                    )

                total_points = final_score["home"] + final_score["away"]

                if bet.selection == "over":
                    is_win = total_points > total_value
                else:  # under
                    is_win = total_points < total_value

            outcome = BetOutcome.WIN if is_win else BetOutcome.LOSS

            # Calculate payout
            payout = Decimal(0)
            if is_win:
                payout = bet.amount * bet.odds
                # Credit account
                account = self._accounts[bet.agent_id]
                account.balance += payout
                account.last_updated = datetime.now()

            # Update bet record
            bet.status = BetStatus.SETTLED
            bet.outcome = outcome
            bet.actual_payout = payout
            bet.settlement_time = datetime.now()

            # Update collections
            self._active_bets[bet.agent_id].remove(bet.bet_id)
            self._bet_history[bet.agent_id].append(bet.bet_id)
            self._event_active_bets[bet.event_id].discard(bet.bet_id)

            # Log settlement
            logger.info(
                "Bet %s settled - Agent %s: %s (%s), Payout: %s",
                bet.bet_id,
                bet.agent_id,
                outcome.value,
                bet_type.value,
                payout,
            )

            # Notify agent
            notification = StreamEvent(
                stream_id=f"settlement_{bet.bet_id}",
                payload=BetSettledPayload(
                    bet_id=bet.bet_id,
                    agent_id=bet.agent_id,
                    event_id=bet.event_id,
                    outcome=outcome,
                    payout=str(payout),
                    winner=winner,
                ),
                emitted_at=datetime.now(),
            )
            asyncio.create_task(self._notify_agent(bet.agent_id, notification))

            # Log state change
            await self._log_accounts_and_bets_status("bet_settled")

    async def _cancel_pregame_orders(self, event_id: str) -> None:
        """Cancel all unfilled pre-game orders for an event"""
        pending_bet_ids = list(self._event_pending_orders.get(event_id, set()))

        cancelled_count = 0
        for bet_id in pending_bet_ids:
            bet = self._bets[bet_id]
            if bet.betting_phase == BettingPhase.PRE_GAME:
                await self._cancel_pending_order(bet)
                cancelled_count += 1

        if cancelled_count > 0:
            logger.info(
                "Cancelled %d pre-game orders for event %s", cancelled_count, event_id
            )

    async def _cancel_all_pending_orders(self, event_id: str) -> None:
        """Cancel all pending orders for an event"""
        pending_bet_ids = list(self._event_pending_orders.get(event_id, set()))

        for bet_id in pending_bet_ids:
            bet = self._bets[bet_id]
            await self._cancel_pending_order(bet)

        if pending_bet_ids:
            logger.info(
                "Cancelled %d pending orders for event %s",
                len(pending_bet_ids),
                event_id,
            )

    async def _cancel_pending_order(self, bet: Bet) -> None:
        """Cancel a pending order and refund"""
        # Refund locked funds
        account = self._accounts[bet.agent_id]
        account.balance += bet.amount
        account.last_updated = datetime.now()

        # Update bet status
        bet.status = BetStatus.CANCELLED

        # Remove from collections
        self._pending_orders[bet.agent_id].remove(bet.bet_id)
        self._event_pending_orders[bet.event_id].discard(bet.bet_id)
        self._bet_history[bet.agent_id].append(bet.bet_id)

        logger.info(
            "Bet %s cancelled - Refunded %s to %s",
            bet.bet_id,
            bet.amount,
            bet.agent_id,
        )

        # Log state change
        await self._log_accounts_and_bets_status("bet_cancelled")

    # =========================================================================
    # Logging
    # =========================================================================

    async def _log_accounts_and_bets_status(self, change_type: str) -> None:
        """Emit a log to SLS about each agent's current balance and bet status.

        This is called whenever self._accounts or self._bets have changed.
        Uses a global lock to ensure atomic snapshot of broker state.

        Args:
            change_type: Description of what changed (e.g., "account_created", "bet_placed")
        """
        # Acquire global lock to ensure atomic snapshot
        async with self._state_snapshot_lock:
            # Serialize accounts and bets using Pydantic TypeAdapter
            # TypeAdapter can handle Pydantic models directly, no need for model_dump()
            accounts_adapter = TypeAdapter(Dict[str, Account])
            bets_adapter = TypeAdapter(Dict[str, Bet])

            accounts_json = accounts_adapter.dump_json(self._accounts).decode()
            bets_json = bets_adapter.dump_json(self._bets).decode()

            # Create span with all the data
            tags = {
                "dojozero.event.type": "broker.state_update",
                "broker.change_type": change_type,
                "broker.accounts_count": len(self._accounts),
                "broker.bets_count": len(self._bets),
                "broker.accounts": accounts_json,
                "broker.bets": bets_json,
            }

            span = create_span_from_event(
                trial_id=self.trial_id,
                actor_id=self.actor_id,
                operation_name="broker.state_update",
                extra_tags=tags,
            )
            emit_span(span)

    # =========================================================================
    # Account Management
    # =========================================================================

    async def create_account(self, agent_id: str, initial_balance: Decimal) -> Account:
        """Initialize a new agent account"""
        if initial_balance < 0:
            raise ValueError("Initial balance must be non-negative")

        if agent_id in self._accounts:
            raise ValueError(f"Account for agent {agent_id} already exists")

        now = datetime.now()
        account = Account(
            agent_id=agent_id,
            balance=initial_balance,
            created_at=now,
            last_updated=now,
        )
        self._accounts[agent_id] = account

        logger.info("Created account for %s with balance %s", agent_id, initial_balance)
        await self._log_accounts_and_bets_status("account_created")
        return account

    async def get_balance(self, agent_id: str) -> Decimal:
        """Retrieve current account balance"""
        if agent_id not in self._accounts:
            raise ValueError(f"Account not found for agent {agent_id}")
        return self._accounts[agent_id].balance

    async def deposit(self, agent_id: str, amount: Decimal) -> Decimal:
        """Add funds to agent account"""
        if amount <= 0:
            raise ValueError("Deposit amount must be positive")

        async with self._agent_locks[agent_id]:
            if agent_id not in self._accounts:
                raise ValueError(f"Account not found for agent {agent_id}")

            account = self._accounts[agent_id]
            account.balance += amount
            account.last_updated = datetime.now()

            logger.info(
                "Deposit for %s: +%s (balance: %s)", agent_id, amount, account.balance
            )
            await self._log_accounts_and_bets_status("deposit")
            return account.balance

    async def withdraw(self, agent_id: str, amount: Decimal) -> Decimal:
        """Remove funds from agent account"""
        if amount <= 0:
            raise ValueError("Withdrawal amount must be positive")

        async with self._agent_locks[agent_id]:
            if agent_id not in self._accounts:
                raise ValueError(f"Account not found for agent {agent_id}")

            account = self._accounts[agent_id]
            if account.balance < amount:
                raise ValueError(
                    f"Insufficient balance: requested {amount}, "
                    f"available {account.balance}"
                )

            account.balance -= amount
            account.last_updated = datetime.now()

            logger.info(
                "Withdraw for %s: -%s (balance: %s)", agent_id, amount, account.balance
            )
            await self._log_accounts_and_bets_status("withdraw")
            return account.balance

    # =========================================================================
    # Bet Management
    # =========================================================================

    async def place_bet(self, agent_id: str, bet_request: BetRequest) -> str:
        """Place a new bet (synchronous confirmation).

        Returns:
            "bet_placed" - Bet successfully placed (funds locked)
            "bet_invalid" - Bet rejected due to validation error
        """
        try:
            # Validate bet request
            bet_request.validate()

            async with self._agent_locks[agent_id]:
                # Check event exists
                if bet_request.event_id not in self._events:
                    raise ValueError(f"Event {bet_request.event_id} not found")

                betting_event = self._events[bet_request.event_id]

                # Check event is accepting bets
                if betting_event.status == EventStatus.CLOSED:
                    raise ValueError("Event is closed for betting")
                if betting_event.status == EventStatus.SETTLED:
                    raise ValueError("Event has been settled")

                # Validate betting phase matches event status
                if bet_request.betting_phase == BettingPhase.PRE_GAME:
                    if betting_event.status != EventStatus.SCHEDULED:
                        raise ValueError(
                            "Pre-game betting only allowed for scheduled events"
                        )
                elif bet_request.betting_phase == BettingPhase.IN_GAME:
                    if betting_event.status != EventStatus.LIVE:
                        raise ValueError("In-game betting only allowed for live events")

                # Check account exists and has sufficient balance
                if agent_id not in self._accounts:
                    raise ValueError(f"Account not found for agent {agent_id}")

                account = self._accounts[agent_id]
                if account.balance < bet_request.amount:
                    raise ValueError(
                        f"Insufficient balance: requested {bet_request.amount}, "
                        f"available {account.balance}"
                    )

                # Determine execution odds based on bet request type
                execution_odds = None
                bet_type: BetType

                if isinstance(bet_request, BetRequestMoneyline):
                    # Moneyline betting
                    bet_type = BetType.MONEYLINE
                    if (
                        betting_event.home_odds is None
                        or betting_event.away_odds is None
                    ):
                        raise ValueError(
                            f"Moneyline odds not yet available for event {bet_request.event_id}. "
                            "Please wait for odds to be updated."
                        )
                    if bet_request.selection == "home":
                        execution_odds = betting_event.home_odds
                    else:  # away
                        execution_odds = betting_event.away_odds

                elif isinstance(bet_request, BetRequestSpread):
                    # Spread betting
                    bet_type = BetType.SPREAD
                    # Protection: ensure spread_lines is not None
                    if betting_event.spread_lines is None:
                        raise ValueError(
                            f"Spread odds not yet available for event {bet_request.event_id}. "
                            "Please wait for odds to be updated."
                        )
                    if bet_request.spread_value not in betting_event.spread_lines:
                        available_spreads = sorted(betting_event.spread_lines.keys())
                        raise ValueError(
                            f"Spread {bet_request.spread_value} not available for event {bet_request.event_id}. "
                            f"Available spreads: {available_spreads}"
                        )
                    spread_line = betting_event.spread_lines[bet_request.spread_value]
                    if bet_request.selection == "home":
                        execution_odds = spread_line["home_odds"]
                    else:  # away
                        execution_odds = spread_line["away_odds"]

                elif isinstance(bet_request, BetRequestTotal):
                    # Total betting
                    bet_type = BetType.TOTAL
                    # Protection: ensure total_lines is not None
                    if betting_event.total_lines is None:
                        raise ValueError(
                            f"Total odds not yet available for event {bet_request.event_id}. "
                            "Please wait for odds to be updated."
                        )
                    if bet_request.total_value not in betting_event.total_lines:
                        available_totals = sorted(betting_event.total_lines.keys())
                        raise ValueError(
                            f"Total {bet_request.total_value} not available for event {bet_request.event_id}. "
                            f"Available totals: {available_totals}"
                        )
                    total_line = betting_event.total_lines[bet_request.total_value]
                    if bet_request.selection == "over":
                        execution_odds = total_line["over_odds"]
                    else:  # under
                        execution_odds = total_line["under_odds"]

                else:
                    raise ValueError(f"Unknown bet request type: {type(bet_request)}")

                if execution_odds is None:
                    raise ValueError(
                        f"Odds not available for {bet_type.value} bet on event {bet_request.event_id}"
                    )

                # Lock funds
                account.balance -= bet_request.amount
                account.last_updated = datetime.now()

                # Create bet record
                spread_value = (
                    bet_request.spread_value
                    if isinstance(bet_request, BetRequestSpread)
                    else None
                )
                total_value = (
                    bet_request.total_value
                    if isinstance(bet_request, BetRequestTotal)
                    else None
                )
                bet = Bet(
                    bet_id=str(uuid.uuid4()),
                    agent_id=agent_id,
                    event_id=bet_request.event_id,
                    amount=bet_request.amount,
                    selection=bet_request.selection,
                    odds=execution_odds,  # Will be updated for limit orders
                    order_type=bet_request.order_type,
                    limit_odds=bet_request.limit_odds,
                    betting_phase=bet_request.betting_phase,
                    bet_type=bet_type,
                    spread_value=spread_value,
                    total_value=total_value,
                    create_time=datetime.now(),
                    execution_time=None,  # Set by match_bet
                    status=BetStatus.PENDING,  # Will be updated by match_bet
                )

                # Store bet
                self._bets[bet.bet_id] = bet

                # Process based on order type
                if bet_request.order_type == OrderType.MARKET:
                    # Execute immediately
                    await self._match_bet(bet, execution_odds)
                else:
                    # Add to pending orders (order book)
                    self._pending_orders[agent_id].append(bet.bet_id)
                    self._event_pending_orders[bet_request.event_id].add(bet.bet_id)
                    logger.info(
                        "Limit order placed - %s: %s $%s on %s @ %s+",
                        bet.bet_id,
                        agent_id,
                        bet_request.amount,
                        bet_request.selection,
                        bet_request.limit_odds,
                    )

                # Log state change
                await self._log_accounts_and_bets_status("bet_placed")
                return "bet_placed"

        except (ValueError, Exception) as e:
            logger.error("Bet rejected for %s: %s", agent_id, e, exc_info=True)
            return "bet_invalid"

    async def _match_bet(self, bet: Bet, execution_odds: Decimal) -> None:
        """Execute a bet at specified odds (asynchronous notification).

        This is an internal method called by update_odds and place_bet.
        """
        # Update bet record
        bet.odds = execution_odds
        bet.execution_time = datetime.now()
        bet.status = BetStatus.ACTIVE

        # Move from pending to active
        if bet.bet_id in self._pending_orders[bet.agent_id]:
            self._pending_orders[bet.agent_id].remove(bet.bet_id)
        if bet.bet_id in self._event_pending_orders[bet.event_id]:
            self._event_pending_orders[bet.event_id].discard(bet.bet_id)

        self._active_bets[bet.agent_id].append(bet.bet_id)
        self._event_active_bets[bet.event_id].add(bet.bet_id)

        logger.info(
            "Bet %s executed - %s $%s on %s @ %s",
            bet.bet_id,
            bet.agent_id,
            bet.amount,
            bet.selection,
            execution_odds,
        )

        # Log state change
        await self._log_accounts_and_bets_status("bet_executed")

        # Send execution notification to agent
        notification = StreamEvent(
            stream_id=f"execution_{bet.bet_id}",
            payload=BetExecutedPayload(
                bet_id=bet.bet_id,
                agent_id=bet.agent_id,
                event_id=bet.event_id,
                selection=bet.selection,
                amount=str(bet.amount),
                execution_odds=str(execution_odds),
                execution_time=bet.execution_time.isoformat(),
            ),
            emitted_at=datetime.now(),
        )
        asyncio.create_task(self._notify_agent(bet.agent_id, notification))

    async def cancel_bet(self, agent_id: str, bet_id: str) -> str:
        """Cancel a pending limit order and refund locked funds.

        Returns:
            "bet_cancelled" - Bet successfully cancelled and funds refunded
            "cancel_failed" - Cancellation failed (see logs for reason)
        """
        if bet_id not in self._bets:
            logger.warning("Bet %s not found", bet_id)
            return "cancel_failed"

        bet = self._bets[bet_id]

        if bet.agent_id != agent_id:
            logger.warning("Bet %s does not belong to agent %s", bet_id, agent_id)
            return "cancel_failed"

        if bet.status != BetStatus.PENDING:
            logger.warning("Bet %s is %s, cannot cancel", bet_id, bet.status.value)
            return "cancel_failed"

        async with self._agent_locks[agent_id]:
            await self._cancel_pending_order(bet)
            return "bet_cancelled"

    # =========================================================================
    # Query Functions
    # =========================================================================

    async def get_active_bets(self, agent_id: str) -> List[Bet]:
        """Retrieve all active bets (executed, not settled)"""
        bet_ids = self._active_bets.get(agent_id, [])
        return [self._bets[bet_id] for bet_id in bet_ids]

    async def get_pending_orders(self, agent_id: str) -> List[Bet]:
        """Retrieve all pending limit orders (not yet executed)"""
        bet_ids = self._pending_orders.get(agent_id, [])
        return [self._bets[bet_id] for bet_id in bet_ids]

    async def get_bet_history(self, agent_id: str, limit: int = 100) -> List[Bet]:
        """Retrieve settled bet history"""
        bet_ids = self._bet_history.get(agent_id, [])
        recent_bet_ids = reversed(bet_ids[-limit:])
        return [self._bets[bet_id] for bet_id in recent_bet_ids]

    async def get_statistics(self, agent_id: str) -> Statistics:
        """Calculate performance metrics for an agent"""
        all_bet_ids = (
            self._active_bets.get(agent_id, [])
            + self._pending_orders.get(agent_id, [])
            + self._bet_history.get(agent_id, [])
        )

        if not all_bet_ids:
            return Statistics(
                total_bets=0,
                total_wagered=Decimal(0),
                wins=0,
                losses=0,
                win_rate=0.0,
                net_profit=Decimal(0),
                roi=0.0,
            )

        total_bets = len(all_bet_ids)
        total_wagered = Decimal(0)
        wins = 0
        losses = 0
        total_payout = Decimal(0)  # Changed: track total payouts

        for bet_id in all_bet_ids:
            bet = self._bets[bet_id]
            total_wagered += bet.amount

            if bet.status == BetStatus.SETTLED:
                if bet.outcome == BetOutcome.WIN:
                    wins += 1
                    total_payout += bet.actual_payout or Decimal(0)  # Add payout
                elif bet.outcome == BetOutcome.LOSS:
                    losses += 1
                    # Don't add to total_payout (it's 0 for losses)

        # Calculate metrics
        settled_bets = wins + losses
        win_rate = wins / settled_bets if settled_bets > 0 else 0.0
        net_profit = total_payout - total_wagered  # Fixed: payout - wagered
        roi = float(net_profit / total_wagered) if total_wagered > 0 else 0.0

        return Statistics(
            total_bets=total_bets,
            total_wagered=total_wagered,
            wins=wins,
            losses=losses,
            win_rate=win_rate,
            net_profit=net_profit,
            roi=roi,
        )

    async def get_available_event(self) -> BettingEvent | None:
        """Get the current event if it's accepting bets.

        The broker handles one event at a time. Returns the event if it's SCHEDULED or LIVE,
        otherwise returns None.
        """
        for event in self._events.values():
            if event.status in [EventStatus.SCHEDULED, EventStatus.LIVE]:
                return event
        return None

    async def get_account(self, agent_id: str) -> Account:
        """Get account information for an agent"""
        if agent_id not in self._accounts:
            raise ValueError(f"Account not found for agent {agent_id}")
        return self._accounts[agent_id]

    # =========================================================================
    # State Management
    # =========================================================================

    async def save_state(self) -> Dict[str, Any]:
        """Export operator state for persistence"""
        # Get all event and agent IDs
        event_ids = list(self._events.keys())
        agent_ids = list(self._accounts.keys())

        # Acquire all locks in a consistent order to prevent deadlocks
        all_locks = [
            (f"event_{eid}", self._event_locks[eid]) for eid in sorted(event_ids)
        ]
        all_locks += [
            (f"agent_{aid}", self._agent_locks[aid]) for aid in sorted(agent_ids)
        ]

        # Acquire locks sequentially
        for _, lock in all_locks:
            await lock.acquire()

        try:
            return {
                "actor_id": self.actor_id,
                "accounts": {
                    agent_id: account.model_dump(mode="json")
                    for agent_id, account in self._accounts.items()
                },
                "events": {
                    event_id: event.model_dump(mode="json")
                    for event_id, event in self._events.items()
                },
                "bets": {
                    bet_id: bet.model_dump(mode="json")
                    for bet_id, bet in self._bets.items()
                },
                "active_bets": dict(self._active_bets),
                "pending_orders": dict(self._pending_orders),
                "bet_history": dict(self._bet_history),
                "event_active_bets": {
                    k: list(v) for k, v in self._event_active_bets.items()
                },
                "event_pending_orders": {
                    k: list(v) for k, v in self._event_pending_orders.items()
                },
            }
        finally:
            # Release all locks in reverse order
            for _, lock in reversed(all_locks):
                lock.release()

    async def load_state(self, state: Dict[str, Any]) -> None:
        """Import operator state from persistence"""
        # Load accounts
        self._accounts = {
            agent_id: Account.model_validate(account_data)
            for agent_id, account_data in state["accounts"].items()
        }

        # Load events
        self._events = {
            event_id: BettingEvent.model_validate(event_data)
            for event_id, event_data in state["events"].items()
        }

        # Load bets
        self._bets = {
            bet_id: Bet.model_validate(bet_data)
            for bet_id, bet_data in state["bets"].items()
        }

        # Load collections
        self._active_bets = defaultdict(list, state["active_bets"])
        self._pending_orders = defaultdict(list, state["pending_orders"])
        self._bet_history = defaultdict(list, state["bet_history"])
        self._event_active_bets = defaultdict(
            set, {k: set(v) for k, v in state["event_active_bets"].items()}
        )
        self._event_pending_orders = defaultdict(
            set, {k: set(v) for k, v in state["event_pending_orders"].items()}
        )

    # =========================================================================
    # Agent Tools
    # =========================================================================
    def agent_tools(
        self, agent_id: str, operator: "BrokerOperator | None" = None
    ) -> list:
        """Return tool functions bound to agent_id for toolkit registration.

        Args:
            agent_id: The agent ID to bind tools to
            operator: Optional broker reference (e.g. Ray proxy) for tools to use.
                      If None, uses self. Pass Ray proxy for distributed execution.

        Returns:
            List of tool functions for agent to use
        """
        from dojozero.agents._toolkit import tool  # type: ignore[import-untyped]

        target = operator if operator is not None else self
        # Get allowed tools from self (the broker instance), not from target
        # None means all tools are allowed
        allowed_tools = getattr(self, "allowed_tools", None)
        allowed_tools_set = (
            {tool_name.lower() for tool_name in allowed_tools}
            if allowed_tools
            else None
        )

        @tool
        async def get_balance() -> str:
            """Get your current account balance.

            Returns:
                Balance as string (e.g., "1000.00")
            """
            balance = await target.get_balance(agent_id)
            return str(balance)

        @tool
        async def get_event() -> str:
            """Get current game information and all available betting options.

            Call this first before placing any bets. The broker handles one event at a time.

            Returns:
                JSON string (parse with JSON) or "null" if no event available. When event exists, returns object with:

                Required fields (always present):
                - event_id: Unique event identifier
                - home_team: Home team name (string)
                - away_team: Away team name (string)
                - game_time: Scheduled game time (ISO format string)
                - status: "SCHEDULED" (pre-game, can bet), "LIVE" (in-game, can bet), "CLOSED" (ended, cannot bet), or "SETTLED" (bets settled)

                Moneyline betting (may be None if odds not set yet):
                - home_odds: Decimal odds for home team as string (e.g., "1.85") or null
                - away_odds: Decimal odds for away team as string (e.g., "2.10") or null
                Use with place_bet_moneyline(selection="home" or "away")

                Spread betting (empty dict {} if not available):
                - spread_lines: Dict mapping spread values to odds
                  Format: {"-3.5": {"home_odds": "1.90", "away_odds": "1.90"}, "+3.5": {...}, ...}}
                  Keys are spread values as strings (e.g., "-3.5", "+3.5")
                  Use with place_bet_spread(spread_value must match a key from spread_lines)

                Total betting (empty dict {} if not available):
                - total_lines: Dict mapping total values to odds
                  Format: {"220.5": {"over_odds": "1.88", "under_odds": "1.88"}, "225.5": {...}, ...}
                  Keys are total point values as strings (e.g., "220.5")
                  Use with place_bet_total(total_value must match a key from total_lines, selection="over" or "under")

                Metadata:
                - last_odds_update: Last odds update time (ISO format string) or null
                - betting_closed_at: When betting closed (ISO format string) or null if still open


            """
            event = await target.get_available_event()
            if not event:
                return "null"
            return event.model_dump_json()

        @tool
        async def place_bet_moneyline(
            amount: str,
            selection: Literal["home", "away"],
            betting_phase: Literal["PRE_GAME", "IN_GAME"],
            order_type: Literal["MARKET", "LIMIT"] = "MARKET",
            limit_odds: str | None = None,
        ) -> str:
            """Bet on which team will win (moneyline).

            IMPORTANT: Check the event's can_bet_pregame/can_bet_ingame fields to know
            which betting_phase to use. For LIVE games, you MUST use betting_phase="IN_GAME".

            Args:
                amount: Bet amount as string
                selection: "home" or "away"
                betting_phase: "PRE_GAME" or "IN_GAME" (depends on the current event status, if the event status is SCHEDULED, you can only place a PRE_GAME bet, if the event status is LIVE, you can only place a IN_GAME bet)
                order_type: "MARKET" (execute immediately at current odds) or "LIMIT" (wait for odds to reach your minimum)
                limit_odds: For LIMIT only - minimum odds as string. Order executes when current odds >= this value.

            Returns:
                "bet_placed" or "bet_invalid: <reason>"
            """
            try:
                # Get the current event
                event = await target.get_available_event()
                if not event:
                    return "bet_invalid: No event available"

                bet_request = BetRequestMoneyline(
                    amount=Decimal(amount),
                    selection=selection,
                    event_id=event.event_id,
                    order_type=OrderType[order_type],
                    betting_phase=BettingPhase[betting_phase],
                    limit_odds=Decimal(limit_odds) if limit_odds else None,
                )
                result = await target.place_bet(agent_id, bet_request)
                return result
            except (ValueError, KeyError, TypeError) as e:
                return f"bet_invalid: {str(e)}"
            except Exception as e:
                logger.error(
                    "Unexpected error in place_bet_moneyline: %s", e, exc_info=True
                )
                return f"bet_invalid: Unexpected error - {str(e)}"

        @tool
        async def place_bet_spread(
            amount: str,
            selection: Literal["home", "away"],
            spread_value: str,
            betting_phase: Literal["PRE_GAME", "IN_GAME"],
            order_type: Literal["MARKET", "LIMIT"] = "MARKET",
            limit_odds: str | None = None,
        ) -> str:
            """Bet on point spread (team must win by more than spread or lose by less).

            IMPORTANT: Check the event's can_bet_pregame/can_bet_ingame fields to know
            which betting_phase to use. For LIVE games, you MUST use betting_phase="IN_GAME".

            Args:
                amount: Bet amount as string
                selection: "home" or "away"
                spread_value: Must match a key from spread_lines in get_event(). Negative values (e.g., "-3.5") mean home team is favored; positive values (e.g., "+3.5") mean away team is favored.
                betting_phase: "PRE_GAME" or "IN_GAME" (depends on the current event status, if the event status is SCHEDULED, you can only place a PRE_GAME bet, if the event status is LIVE, you can only place a IN_GAME bet)
                order_type: "MARKET" (execute immediately) or "LIMIT" (wait for odds to reach your minimum)
                limit_odds: For LIMIT only - minimum odds as string. Order executes when current odds >= this value.

            Returns:
                "bet_placed" or "bet_invalid: <reason>"
            """
            try:
                # Get the current event
                event = await target.get_available_event()
                if not event:
                    return "bet_invalid: No event available"

                bet_request = BetRequestSpread(
                    amount=Decimal(amount),
                    selection=selection,
                    event_id=event.event_id,
                    spread_value=Decimal(spread_value),
                    order_type=OrderType[order_type],
                    betting_phase=BettingPhase[betting_phase],
                    limit_odds=Decimal(limit_odds) if limit_odds else None,
                )
                result = await target.place_bet(agent_id, bet_request)
                return result
            except (ValueError, KeyError, TypeError) as e:
                return f"bet_invalid: {str(e)}"
            except Exception as e:
                logger.error(
                    "Unexpected error in place_bet_spread: %s", e, exc_info=True
                )
                return f"bet_invalid: Unexpected error - {str(e)}"

        @tool
        async def place_bet_total(
            amount: str,
            selection: Literal["over", "under"],
            total_value: str,
            betting_phase: Literal["PRE_GAME", "IN_GAME"],
            order_type: Literal["MARKET", "LIMIT"] = "MARKET",
            limit_odds: str | None = None,
        ) -> str:
            """Bet on total points scored (over/under).

            IMPORTANT: Check the event's can_bet_pregame/can_bet_ingame fields to know
            which betting_phase to use. For LIVE games, you MUST use betting_phase="IN_GAME".

            Args:
                amount: Bet amount as string
                selection: "over" or "under"
                total_value: Must match a key from total_lines in get_event(). This is the combined points both teams will score; bet "over" if you think total will exceed this, "under" if it will be less.
                betting_phase: "PRE_GAME" or "IN_GAME" (depends on the current event status, if the event status is SCHEDULED, you can only place a PRE_GAME bet, if the event status is LIVE, you can only place a IN_GAME bet)
                order_type: "MARKET" or "LIMIT"
                limit_odds: For LIMIT only - minimum odds as string

            Returns:
                "bet_placed" or "bet_invalid: <reason>"
            """
            try:
                # Get the current event
                event = await target.get_available_event()
                if not event:
                    return "bet_invalid: No event available"

                bet_request = BetRequestTotal(
                    amount=Decimal(amount),
                    selection=selection,
                    event_id=event.event_id,
                    total_value=Decimal(total_value),
                    order_type=OrderType[order_type],
                    betting_phase=BettingPhase[betting_phase],
                    limit_odds=Decimal(limit_odds) if limit_odds else None,
                )
                result = await target.place_bet(agent_id, bet_request)
                return result
            except (ValueError, KeyError, TypeError) as e:
                return f"bet_invalid: {str(e)}"
            except Exception as e:
                logger.error(
                    "Unexpected error in place_bet_total: %s", e, exc_info=True
                )
                return f"bet_invalid: Unexpected error - {str(e)}"

        @tool
        async def cancel_bet(bet_id: str) -> str:
            """Cancel a pending limit order.

            Must be a limit order. Call get_pending_orders() to get the bet_id.

            Args:
                bet_id: Unique bet identifier from get_pending_orders()

            Returns:
                "bet_cancelled" or "cancel_failed"
            """
            result = await target.cancel_bet(agent_id, bet_id)
            return result

        @tool
        async def get_active_bets() -> str:
            """Get your active bets (executed, waiting for game result).

            Returns:
                JSON array of bets with: bet_id, amount, selection, odds, bet_type, status="ACTIVE"
            """
            bets = await target.get_active_bets(agent_id)
            # Use Pydantic serialization for consistency
            bets_adapter = TypeAdapter(List[Bet])
            return bets_adapter.dump_json(bets).decode()

        @tool
        async def get_pending_orders() -> str:
            """Get your pending limit orders (waiting for odds to reach your specified minimum).

            Use bet_id with cancel_bet() to cancel an order.

            Returns:
                JSON array of orders with: bet_id, amount, selection, limit_odds, bet_type, status="PENDING"
            """
            orders = await target.get_pending_orders(agent_id)
            # Use Pydantic serialization for consistency
            bets_adapter = TypeAdapter(List[Bet])
            return bets_adapter.dump_json(orders).decode()

        @tool
        async def get_bet_history(limit: int = 20) -> str:
            """Get your settled bet history.

            Args:
                limit: Max number of bets to return (default: 20)

            Returns:
                JSON array of settled bets with: bet_id, amount, outcome, payout, status="SETTLED"
            """
            history = await target.get_bet_history(agent_id, limit)
            # Use Pydantic serialization for consistency
            bets_adapter = TypeAdapter(List[Bet])
            return bets_adapter.dump_json(history).decode()

        @tool
        async def get_statistics() -> str:
            """Get your betting performance stats.

            Returns:
                JSON object with: total_bets, wins, losses, win_rate, net_profit, roi
            """
            stats = await target.get_statistics(agent_id)
            return stats.model_dump_json()

        # Build mapping of tool names to tool functions
        all_tools_map = {
            "get_balance": get_balance,
            "get_event": get_event,
            "place_bet_moneyline": place_bet_moneyline,
            "place_bet_spread": place_bet_spread,
            "place_bet_total": place_bet_total,
            "cancel_bet": cancel_bet,
            "get_active_bets": get_active_bets,
            "get_pending_orders": get_pending_orders,
            "get_bet_history": get_bet_history,
            "get_statistics": get_statistics,
        }

        # Filter tools based on allowed_tools configuration
        if allowed_tools_set is None:
            # None means all tools are allowed
            return list(all_tools_map.values())
        else:
            # Only include tools that are in the allowed list
            return [
                tool_func
                for tool_name, tool_func in all_tools_map.items()
                if tool_name.lower() in allowed_tools_set
            ]
