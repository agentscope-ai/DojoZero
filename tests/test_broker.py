"""
Unit tests for Sports Betting Broker Operator using pytest
"""

import pytest
import pytest_asyncio  # pyright: ignore
from decimal import Decimal
from datetime import datetime
from typing import Any

from dojozero.nba_moneyline._broker import (
    BrokerOperator,
    BrokerOperatorConfig,
    BetRequest,
    OrderType,
    BettingPhase,
    EventStatus,
    BetStatus,
    BetOutcome,
    BetExecutedPayload,
    BetSettledPayload,
)

from dojozero.core import ActorContext, StreamEvent
from dojozero.data.nba._events import (
    GameInitializeEvent,
    GameStartEvent,
    GameResultEvent,
)
from dojozero.data.polymarket._events import OddsUpdateEvent


# =============================================================================
# Configure pytest-asyncio
# =============================================================================

# This makes all tests in this file async by default
pytestmark = pytest.mark.asyncio


# =============================================================================
# Fixtures
# =============================================================================


class MockAgent:
    """Mock Agent implementation for testing"""

    def __init__(self, actor_id: str):
        self.actor_id = actor_id
        self.received_events = []

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        """Process asynchronous notifications from the broker."""
        self.received_events.append(event)
        payload = event.payload

        if isinstance(payload, BetExecutedPayload):
            print(
                f"[AGENT {self.actor_id}] Bet {payload.bet_id} executed "
                f"at odds {payload.execution_odds}"
            )
        elif isinstance(payload, BetSettledPayload):
            print(
                f"[AGENT {self.actor_id}] Bet {payload.bet_id} settled: "
                f"{payload.outcome}, Payout: ${payload.payout}"
            )


@pytest_asyncio.fixture  # Use pytest_asyncio.fixture for async fixtures
async def broker():
    """Create and start a broker for testing"""
    config: BrokerOperatorConfig = {
        "actor_id": "test_broker",
        "initial_balance": "1000.00",
    }
    context = ActorContext(
        trial_id="test-trial",
        data_hubs={},
        stores={},
        startup=None,
    )
    broker = BrokerOperator.from_dict(dict(config), context)
    await broker.start()
    yield broker
    await broker.stop()


@pytest.fixture  # Regular fixture (not async)
def agent():
    """Create a mock agent"""
    return MockAgent(actor_id="test_agent")


@pytest_asyncio.fixture
async def broker_with_agent(broker, agent):
    """Broker with registered agent"""
    broker.register_agents([agent])  # type: ignore[arg-type]
    return broker, agent


@pytest_asyncio.fixture
async def initialized_event(broker):
    """Create broker with initialized event"""
    game_init_event = StreamEvent(
        stream_id="nba_game_stream",
        payload=GameInitializeEvent(
            event_id="lakers_vs_warriors",
            game_id="lakers_vs_warriors",
            home_team="Lakers",
            away_team="Warriors",
            game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
        ),
        emitted_at=datetime.now(),
    )
    await broker.handle_stream_event(game_init_event)

    # Add odds update to set initial odds
    odds_event = StreamEvent(
        stream_id="nba_odds_stream",
        payload=OddsUpdateEvent(
            event_id="lakers_vs_warriors",
            home_odds=1.95,
            away_odds=2.10,
        ),
        emitted_at=datetime.now(),
    )
    await broker.handle_stream_event(odds_event)
    return broker, "lakers_vs_warriors"


# =============================================================================
# Account Management Tests
# =============================================================================


class TestAccountManagement:
    """Test account creation and balance operations"""

    async def test_create_account(self, broker):
        """Test creating a new account"""
        account = broker.create_account("agent1", Decimal("500.00"))

        assert account.agent_id == "agent1"
        assert account.balance == Decimal("500.00")
        assert account.created_at is not None

    async def test_create_duplicate_account_raises_error(self, broker):
        """Test that creating duplicate account raises error"""
        broker.create_account("agent1", Decimal("500.00"))

        with pytest.raises(ValueError, match="already exists"):
            broker.create_account("agent1", Decimal("500.00"))

    async def test_create_account_negative_balance_raises_error(self, broker):
        """Test that negative initial balance raises error"""
        with pytest.raises(ValueError, match="non-negative"):
            broker.create_account("agent1", Decimal("-100.00"))

    async def test_get_balance(self, broker_with_agent):
        """Test retrieving account balance"""
        broker, agent = broker_with_agent

        balance = await broker.get_balance(agent.actor_id)
        assert balance == Decimal("1000.00")

    async def test_get_balance_nonexistent_account_raises_error(self, broker):
        """Test that getting balance for nonexistent account raises error"""
        with pytest.raises(ValueError, match="Account not found"):
            await broker.get_balance("nonexistent_agent")

    async def test_deposit(self, broker_with_agent):
        """Test depositing funds"""
        broker, agent = broker_with_agent

        new_balance = await broker.deposit(agent.actor_id, Decimal("500.00"))

        assert new_balance == Decimal("1500.00")
        assert await broker.get_balance(agent.actor_id) == Decimal("1500.00")

    async def test_deposit_negative_amount_raises_error(self, broker_with_agent):
        """Test that depositing negative amount raises error"""
        broker, agent = broker_with_agent

        with pytest.raises(ValueError, match="must be positive"):
            await broker.deposit(agent.actor_id, Decimal("-100.00"))

    async def test_withdraw(self, broker_with_agent):
        """Test withdrawing funds"""
        broker, agent = broker_with_agent

        new_balance = await broker.withdraw(agent.actor_id, Decimal("300.00"))

        assert new_balance == Decimal("700.00")
        assert await broker.get_balance(agent.actor_id) == Decimal("700.00")

    async def test_withdraw_insufficient_balance_raises_error(self, broker_with_agent):
        """Test that withdrawing more than balance raises error"""
        broker, agent = broker_with_agent

        with pytest.raises(ValueError, match="Insufficient balance"):
            await broker.withdraw(agent.actor_id, Decimal("2000.00"))


# =============================================================================
# Event Management Tests
# =============================================================================


class TestEventManagement:
    """Test event lifecycle management"""

    async def test_initialize_event(self, broker):
        """Test creating a new betting event"""
        event = await broker.initialize_event(
            event_id="game1",
            home_team="Lakers",
            away_team="Warriors",
            game_time=datetime.now(),
            initial_home_odds=Decimal("1.95"),
            initial_away_odds=Decimal("2.10"),
        )

        assert event.event_id == "game1"
        assert event.home_team == "Lakers"
        assert event.away_team == "Warriors"
        assert event.status == EventStatus.SCHEDULED
        assert event.home_odds == Decimal("1.95")
        assert event.away_odds == Decimal("2.10")

    async def test_initialize_duplicate_event_raises_error(self, broker):
        """Test that creating duplicate event raises error"""
        await broker.initialize_event(
            event_id="game1",
            home_team="Lakers",
            away_team="Warriors",
            game_time=datetime.now(),
            initial_home_odds=Decimal("1.95"),
            initial_away_odds=Decimal("2.10"),
        )

        with pytest.raises(ValueError, match="already exists"):
            await broker.initialize_event(
                event_id="game1",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.now(),
                initial_home_odds=Decimal("1.95"),
                initial_away_odds=Decimal("2.10"),
            )

    async def test_initialize_event_invalid_odds_raises_error(self, broker):
        """Test that odds <= 1.0 raises error"""
        with pytest.raises(ValueError, match="greater than 1.0"):
            await broker.initialize_event(
                event_id="game1",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.now(),
                initial_home_odds=Decimal("0.95"),
                initial_away_odds=Decimal("2.10"),
            )

    async def test_get_quote(self, initialized_event):
        """Test getting odds quote for an event"""
        broker, event_id = initialized_event

        quote = await broker.get_quote(event_id)

        assert quote["event_id"] == event_id
        assert quote["home_team"] == "Lakers"
        assert quote["away_team"] == "Warriors"
        assert quote["status"] == "SCHEDULED"
        assert Decimal(quote["home_odds"]) == Decimal("1.95")
        assert Decimal(quote["away_odds"]) == Decimal("2.10")

    async def test_get_quote_nonexistent_event_raises_error(self, broker):
        """Test that getting quote for nonexistent event raises error"""
        with pytest.raises(ValueError, match="not found"):
            await broker.get_quote("nonexistent_event")

    async def test_update_odds(self, initialized_event):
        """Test updating event odds"""
        broker, event_id = initialized_event

        event = await broker.update_odds(
            event_id=event_id,
            home_odds=Decimal("2.00"),
            away_odds=Decimal("2.20"),
        )

        assert event.home_odds == Decimal("2.00")
        assert event.away_odds == Decimal("2.20")

    async def test_update_odds_invalid_values_raises_error(self, initialized_event):
        """Test that invalid odds update raises error"""
        broker, event_id = initialized_event

        with pytest.raises(ValueError, match="greater than 1.0"):
            await broker.update_odds(
                event_id=event_id,
                home_odds=Decimal("0.50"),
                away_odds=Decimal("2.20"),
            )

    async def test_update_event_status_to_live(self, initialized_event):
        """Test transitioning event to LIVE status"""
        broker, event_id = initialized_event

        await broker.update_event_status(event_id, EventStatus.LIVE)

        quote = await broker.get_quote(event_id)
        assert quote["status"] == "LIVE"

    async def test_update_event_status_to_closed(self, initialized_event):
        """Test transitioning event to CLOSED status"""
        broker, event_id = initialized_event

        await broker.update_event_status(event_id, EventStatus.CLOSED)

        quote = await broker.get_quote(event_id)
        assert quote["status"] == "CLOSED"

    async def test_get_available_events(self, broker):
        """Test getting list of events accepting bets"""
        # Create multiple events
        await broker.initialize_event(
            event_id="game1",
            home_team="Lakers",
            away_team="Warriors",
            game_time=datetime.now(),
            initial_home_odds=Decimal("1.95"),
            initial_away_odds=Decimal("2.10"),
        )
        await broker.initialize_event(
            event_id="game2",
            home_team="Celtics",
            away_team="Heat",
            game_time=datetime.now(),
            initial_home_odds=Decimal("1.85"),
            initial_away_odds=Decimal("2.00"),
        )

        # Close one event
        await broker.update_event_status("game2", EventStatus.CLOSED)

        available = await broker.get_available_events()

        assert len(available) == 1
        assert available[0].event_id == "game1"


# =============================================================================
# Bet Placement Tests
# =============================================================================


class TestBetPlacement:
    """Test bet placement and execution"""

    async def test_place_market_bet(self, broker_with_agent):
        """Test placing a market order (immediate execution)"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                event_id="test_event",
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                event_id="test_event",
                home_odds=1.95,
                away_odds=2.10,
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        result = await broker.place_bet(
            agent.actor_id,
            BetRequest(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
                betting_phase=BettingPhase.PRE_GAME,
            ),
        )

        assert result == "bet_placed"

        # Check balance was deducted
        balance = await broker.get_balance(agent.actor_id)
        assert balance == Decimal("900.00")

        # Check bet was executed
        active_bets = await broker.get_active_bets(agent.actor_id)
        assert len(active_bets) == 1
        assert active_bets[0].amount == Decimal("100.00")
        assert active_bets[0].status == BetStatus.ACTIVE

    async def test_place_limit_order(self, broker_with_agent):
        """Test placing a limit order (conditional execution)"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                event_id="test_event",
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                event_id="test_event",
                home_odds=1.95,
                away_odds=2.10,
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        result = await broker.place_bet(
            agent.actor_id,
            BetRequest(
                amount=Decimal("50.00"),
                selection="away",
                event_id="test_event",
                order_type=OrderType.LIMIT,
                betting_phase=BettingPhase.PRE_GAME,
                limit_odds=Decimal("2.20"),
            ),
        )

        assert result == "bet_placed"

        # Check balance was deducted
        balance = await broker.get_balance(agent.actor_id)
        assert balance == Decimal("950.00")

        # Check order is pending (not executed yet)
        pending = await broker.get_pending_orders(agent.actor_id)
        assert len(pending) == 1
        assert pending[0].amount == Decimal("50.00")
        assert pending[0].status == BetStatus.PENDING

    async def test_limit_order_execution_on_odds_update(self, broker_with_agent):
        """Test that limit order executes when odds reach threshold"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                event_id="test_event",
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                event_id="test_event",
                home_odds=1.95,
                away_odds=2.10,
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        # Place limit order
        await broker.place_bet(
            agent.actor_id,
            BetRequest(
                amount=Decimal("50.00"),
                selection="away",
                event_id="test_event",
                order_type=OrderType.LIMIT,
                betting_phase=BettingPhase.PRE_GAME,
                limit_odds=Decimal("2.20"),
            ),
        )

        # Update odds to trigger execution
        odds_update = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                event_id="test_event",
                home_odds=1.90,
                away_odds=2.25,  # Now >= 2.20, should trigger
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_update)

        # Check order executed
        pending = await broker.get_pending_orders(agent.actor_id)
        assert len(pending) == 0

        active = await broker.get_active_bets(agent.actor_id)
        assert len(active) == 1
        assert active[0].status == BetStatus.ACTIVE
        assert active[0].odds == Decimal("2.25")

    async def test_place_bet_insufficient_balance(self, broker_with_agent):
        """Test that bet is rejected with insufficient balance"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                event_id="test_event",
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                event_id="test_event",
                home_odds=1.95,
                away_odds=2.10,
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        result = await broker.place_bet(
            agent.actor_id,
            BetRequest(
                amount=Decimal("10000.00"),  # More than balance
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
                betting_phase=BettingPhase.PRE_GAME,
            ),
        )

        assert result == "bet_invalid"

        # Balance should be unchanged
        balance = await broker.get_balance(agent.actor_id)
        assert balance == Decimal("1000.00")

    async def test_place_bet_on_closed_event(self, broker_with_agent):
        """Test that bet is rejected on closed event"""
        broker, agent = broker_with_agent

        # Initialize and close event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                event_id="test_event",
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                event_id="test_event",
                home_odds=1.95,
                away_odds=2.10,
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)
        await broker.update_event_status("test_event", EventStatus.CLOSED)

        result = await broker.place_bet(
            agent.actor_id,
            BetRequest(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
                betting_phase=BettingPhase.PRE_GAME,
            ),
        )

        assert result == "bet_invalid"

    async def test_cancel_pending_bet(self, broker_with_agent):
        """Test cancelling a pending limit order"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                event_id="test_event",
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                event_id="test_event",
                home_odds=1.95,
                away_odds=2.10,
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        # Place limit order
        await broker.place_bet(
            agent.actor_id,
            BetRequest(
                amount=Decimal("50.00"),
                selection="away",
                event_id="test_event",
                order_type=OrderType.LIMIT,
                betting_phase=BettingPhase.PRE_GAME,
                limit_odds=Decimal("2.20"),
            ),
        )

        pending = await broker.get_pending_orders(agent.actor_id)
        bet_id = pending[0].bet_id

        # Cancel the bet
        result = await broker.cancel_bet(agent.actor_id, bet_id)

        assert result == "bet_cancelled"

        # Check funds refunded
        balance = await broker.get_balance(agent.actor_id)
        assert balance == Decimal("1000.00")

        # Check no pending orders
        pending = await broker.get_pending_orders(agent.actor_id)
        assert len(pending) == 0

    async def test_cancel_active_bet_fails(self, broker_with_agent):
        """Test that cancelling an active bet fails"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                event_id="test_event",
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                event_id="test_event",
                home_odds=1.95,
                away_odds=2.10,
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        # Place and execute market bet
        await broker.place_bet(
            agent.actor_id,
            BetRequest(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
                betting_phase=BettingPhase.PRE_GAME,
            ),
        )

        active = await broker.get_active_bets(agent.actor_id)
        bet_id = active[0].bet_id

        # Try to cancel active bet
        result = await broker.cancel_bet(agent.actor_id, bet_id)

        assert result == "cancel_failed"


# =============================================================================
# Settlement Tests
# =============================================================================


class TestBetSettlement:
    """Test bet settlement and payout calculation"""

    async def test_settle_winning_bet(self, broker_with_agent):
        """Test settling a winning bet"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                event_id="test_event",
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                event_id="test_event",
                home_odds=1.95,
                away_odds=2.10,
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        # Place bet on home team
        await broker.place_bet(
            agent.actor_id,
            BetRequest(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
                betting_phase=BettingPhase.PRE_GAME,
            ),
        )

        # Start and close game
        await broker.update_event_status("test_event", EventStatus.LIVE)
        await broker.update_event_status("test_event", EventStatus.CLOSED)

        # Settle with home team winning
        game_result = StreamEvent(
            stream_id="nba_results_stream",
            payload=GameResultEvent(
                event_id="test_event",
                winner="home",
                final_score={"home": 110, "away": 105},
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_result)

        # Check bet settled with payout
        history = await broker.get_bet_history(agent.actor_id)
        assert len(history) == 1
        assert history[0].outcome == BetOutcome.WIN
        assert history[0].actual_payout == Decimal("100.00") * Decimal("1.95")

        # Check balance updated
        balance = await broker.get_balance(agent.actor_id)
        expected_balance = Decimal("900.00") + (Decimal("100.00") * Decimal("1.95"))
        assert balance == expected_balance

    async def test_settle_losing_bet(self, broker_with_agent):
        """Test settling a losing bet"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                event_id="test_event",
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                event_id="test_event",
                home_odds=1.95,
                away_odds=2.10,
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        # Place bet on home team
        await broker.place_bet(
            agent.actor_id,
            BetRequest(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
                betting_phase=BettingPhase.PRE_GAME,
            ),
        )

        # Start and close game
        await broker.update_event_status("test_event", EventStatus.LIVE)
        await broker.update_event_status("test_event", EventStatus.CLOSED)

        # Settle with away team winning
        game_result = StreamEvent(
            stream_id="nba_results_stream",
            payload=GameResultEvent(
                event_id="test_event",
                winner="away",
                final_score={"home": 105, "away": 110},
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_result)

        # Check bet settled as loss
        history = await broker.get_bet_history(agent.actor_id)
        assert len(history) == 1
        assert history[0].outcome == BetOutcome.LOSS
        assert history[0].actual_payout == Decimal("0")

        # Check balance (still at 900 after bet, no payout)
        balance = await broker.get_balance(agent.actor_id)
        assert balance == Decimal("900.00")

    async def test_cancel_pregame_orders_on_game_start(self, broker_with_agent):
        """Test that pregame limit orders are cancelled when game starts"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                event_id="test_event",
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                event_id="test_event",
                home_odds=1.95,
                away_odds=2.10,
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        # Place pregame limit order
        await broker.place_bet(
            agent.actor_id,
            BetRequest(
                amount=Decimal("50.00"),
                selection="away",
                event_id="test_event",
                order_type=OrderType.LIMIT,
                betting_phase=BettingPhase.PRE_GAME,
                limit_odds=Decimal("2.20"),
            ),
        )

        # Start game
        game_start = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameStartEvent(event_id="test_event"),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_start)

        # Check order was cancelled and funds refunded
        pending = await broker.get_pending_orders(agent.actor_id)
        assert len(pending) == 0

        balance = await broker.get_balance(agent.actor_id)
        assert balance == Decimal("1000.00")


# =============================================================================
# Statistics Tests
# =============================================================================


class TestStatistics:
    """Test performance statistics calculation"""

    async def test_statistics_with_wins_and_losses(self, broker_with_agent):
        """Test statistics calculation with multiple bets"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                event_id="test_event",
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                event_id="test_event",
                home_odds=1.95,
                away_odds=2.10,
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        # Place two bets
        await broker.place_bet(
            agent.actor_id,
            BetRequest(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
                betting_phase=BettingPhase.PRE_GAME,
            ),
        )

        await broker.place_bet(
            agent.actor_id,
            BetRequest(
                amount=Decimal("50.00"),
                selection="away",
                event_id="test_event",
                order_type=OrderType.MARKET,
                betting_phase=BettingPhase.PRE_GAME,
            ),
        )

        # Settle with home team winning
        await broker.update_event_status("test_event", EventStatus.LIVE)
        await broker.update_event_status("test_event", EventStatus.CLOSED)

        game_result = StreamEvent(
            stream_id="nba_results_stream",
            payload=GameResultEvent(
                event_id="test_event",
                winner="home",
                final_score={"home": 110, "away": 105},
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_result)

        # Get statistics
        stats = await broker.get_statistics(agent.actor_id)

        assert stats.total_bets == 2
        assert stats.total_wagered == Decimal("150.00")
        assert stats.wins == 1
        assert stats.losses == 1
        assert stats.win_rate == 0.5

        # Net profit = win_payout - total_wagered
        expected_profit = (Decimal("100.00") * Decimal("1.95")) - Decimal("150.00")
        assert stats.net_profit == expected_profit

    async def test_statistics_no_bets(self, broker_with_agent):
        """Test statistics for agent with no bets"""
        broker, agent = broker_with_agent

        stats = await broker.get_statistics(agent.actor_id)

        assert stats.total_bets == 0
        assert stats.total_wagered == Decimal("0")
        assert stats.wins == 0
        assert stats.losses == 0
        assert stats.win_rate == 0.0
        assert stats.net_profit == Decimal("0")
        assert stats.roi == 0.0


# =============================================================================
# State Management Tests
# =============================================================================


class TestStateManagement:
    """Test state save/load functionality"""

    async def test_save_and_load_state(self, broker_with_agent):
        """Test saving and loading broker state"""
        broker, agent = broker_with_agent

        # Initialize event
        # Initialize event

        game_init_event = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                event_id="test_event",
                game_id="test_event",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(game_init_event)

        # Set odds

        odds_event = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                event_id="test_event",
                home_odds=1.95,
                away_odds=2.10,
            ),
            emitted_at=datetime.now(),
        )

        await broker.handle_stream_event(odds_event)

        # Place a bet
        await broker.place_bet(
            agent.actor_id,
            BetRequest(
                amount=Decimal("100.00"),
                selection="home",
                event_id="test_event",
                order_type=OrderType.MARKET,
                betting_phase=BettingPhase.PRE_GAME,
            ),
        )

        # Save state
        state = await broker.save_state()

        # Create new broker and load state
        context = ActorContext(
            trial_id="test-trial",
            data_hubs={},
            stores={},
            startup=None,
        )
        new_broker = BrokerOperator.from_dict({"actor_id": "new_broker"}, context)
        await new_broker.load_state(state)

        # Verify state was restored
        balance = await new_broker.get_balance(agent.actor_id)
        assert balance == Decimal("900.00")

        active_bets = await new_broker.get_active_bets(agent.actor_id)
        assert len(active_bets) == 1

        quote = await new_broker.get_quote("test_event")
        assert quote["event_id"] == "test_event"


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """End-to-end integration tests"""

    async def test_complete_betting_workflow(self, broker_with_agent):
        """Test complete workflow: event creation → betting → settlement"""
        broker, agent = broker_with_agent

        # 1. Initialize event
        game_init = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameInitializeEvent(
                event_id="game1",
                game_id="game1",
                home_team="Lakers",
                away_team="Warriors",
                game_time=datetime.fromisoformat("2025-12-15T19:00:00"),
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_init)

        # Set initial odds
        odds_init = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                event_id="game1",
                home_odds=1.95,
                away_odds=2.10,
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_init)

        # 2. Place market bet
        result = await broker.place_bet(
            agent.actor_id,
            BetRequest(
                amount=Decimal("100.00"),
                selection="home",
                event_id="game1",
                order_type=OrderType.MARKET,
                betting_phase=BettingPhase.PRE_GAME,
            ),
        )
        assert result == "bet_placed"

        # 3. Place limit order
        result = await broker.place_bet(
            agent.actor_id,
            BetRequest(
                amount=Decimal("50.00"),
                selection="away",
                event_id="game1",
                order_type=OrderType.LIMIT,
                betting_phase=BettingPhase.PRE_GAME,
                limit_odds=Decimal("2.20"),
            ),
        )
        assert result == "bet_placed"

        # 4. Update odds (trigger limit order)
        odds_update = StreamEvent(
            stream_id="nba_odds_stream",
            payload=OddsUpdateEvent(
                event_id="game1",
                home_odds=1.90,
                away_odds=2.25,
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(odds_update)

        # 5. Verify both bets active
        active = await broker.get_active_bets(agent.actor_id)
        assert len(active) == 2

        # 6. Start game
        game_start = StreamEvent(
            stream_id="nba_game_stream",
            payload=GameStartEvent(event_id="game1"),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_start)

        # 7. End and settle game
        game_result = StreamEvent(
            stream_id="nba_results_stream",
            payload=GameResultEvent(
                event_id="game1",
                winner="home",
                final_score={"home": 110, "away": 105},
            ),
            emitted_at=datetime.now(),
        )
        await broker.handle_stream_event(game_result)

        # 8. Verify settlement
        history = await broker.get_bet_history(agent.actor_id)
        assert len(history) == 2

        # Home bet won
        home_bet = [b for b in history if b.selection == "home"][0]
        assert home_bet.outcome == BetOutcome.WIN

        # Away bet lost
        away_bet = [b for b in history if b.selection == "away"][0]
        assert away_bet.outcome == BetOutcome.LOSS

        # 9. Check final balance
        balance = await broker.get_balance(agent.actor_id)
        expected = Decimal("850.00") + (Decimal("100.00") * Decimal("1.95"))
        assert balance == expected

        # 10. Check statistics
        stats = await broker.get_statistics(agent.actor_id)
        assert stats.total_bets == 2
        assert stats.wins == 1
        assert stats.losses == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
