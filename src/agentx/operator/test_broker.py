"""
Simple runnable test for Sports Betting Broker Operator
"""

from decimal import Decimal
from datetime import datetime
from typing import Any

from betting_broker import (
    BrokerOperator,
    BrokerOperatorConfig,
    BetRequest,
    OrderType,
    BettingPhase,
)

from agentx.core import StreamEvent


# Create a mock Agent class for testing
class MockAgent:
    """Mock Agent implementation for testing"""

    def __init__(self, actor_id: str):
        self.actor_id = actor_id

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        """Process asynchronous notifications from the broker."""
        event_type = event.payload.get("type")

        if event_type == "bet_executed":
            bet_id = event.payload.get("bet_id")
            odds = event.payload.get("execution_odds")
            print(f"[AGENT {self.actor_id}] Bet {bet_id} executed at odds {odds}")
        elif event_type == "bet_settled":
            bet_id = event.payload.get("bet_id")
            outcome = event.payload.get("outcome")
            payout = event.payload.get("payout")
            print(
                f"[AGENT {self.actor_id}] Bet {bet_id} settled: {outcome}, "
                f"Payout: ${payout}"
            )


def create_test_broker_config(
    broker_id: str, initial_balance: str = "1000.00"
) -> BrokerOperatorConfig:
    """Create a simple broker configuration for testing."""
    return {"actor_id": broker_id, "initial_balance": initial_balance}


def create_test_broker(
    broker_id: str = "test_broker",
    initial_balance: str = "1000.00",
) -> BrokerOperator:
    """Create a broker for testing."""
    config = create_test_broker_config(broker_id, initial_balance)
    return BrokerOperator.from_dict(dict(config))


async def main():
    # Create broker
    print("1. Creating broker...")
    broker = create_test_broker()
    await broker.start()
    print(f"Broker created: {broker.actor_id}")
    print()

    # Create and register agent
    print("2. Creating and registering test agent...")
    test_agent = MockAgent(actor_id="test_agent")
    broker.register_agents([test_agent])  # type: ignore[arg-type]
    balance = await broker.get_balance("test_agent")
    print(f"Agent registered: {test_agent.actor_id}")
    print(f"Initial balance: ${balance}")
    print()

    # Initialize an event (pregame)
    print("3. Initializing event (pregame)...")
    pregame_event = StreamEvent(
        stream_id="nba_pregame_stream",
        payload={
            "type": "pregame",
            "event_id": "lakers_vs_warriors",
            "home_team": "Lakers",
            "away_team": "Warriors",
            "game_time": "2025-12-15T19:00:00",
            "initial_home_odds": 1.95,
            "initial_away_odds": 2.10,
        },
        emitted_at=datetime.now(),
    )
    await broker.handle_stream_event(pregame_event)
    print("Event initialized: lakers_vs_warriors")
    print()

    # Get quote
    print("4. Getting odds quote...")
    quote = await broker.get_quote("lakers_vs_warriors")
    print(f"Event: {quote['home_team']} vs {quote['away_team']}")
    print(f"Status: {quote['status']}")
    print(f"Home odds: {quote['home_odds']}")
    print(f"Away odds: {quote['away_odds']}")
    print()

    # Place a market bet (executes immediately)
    print("5. Placing market bet...")
    result = await broker.place_bet(
        "test_agent",
        BetRequest(
            amount=Decimal("100.00"),
            selection="home",
            event_id="lakers_vs_warriors",
            order_type=OrderType.MARKET,
            betting_phase=BettingPhase.PRE_GAME,
        ),
    )
    print(f"Bet placement result: {result}")
    print()

    # Check balance after bet
    print("6. Checking balance after bet placement...")
    balance = await broker.get_balance("test_agent")
    print(f"Balance after bet: ${balance}")
    print("Funds locked: $100.00")
    print()

    # Place a limit order
    print("7. Placing limit order...")
    result = await broker.place_bet(
        "test_agent",
        BetRequest(
            amount=Decimal("50.00"),
            selection="away",
            event_id="lakers_vs_warriors",
            order_type=OrderType.LIMIT,
            betting_phase=BettingPhase.PRE_GAME,
            limit_odds=Decimal("2.20"),  # Will only execute if odds >= 2.20
        ),
    )
    print(f"Limit order result: {result}")
    print()

    # Check pending orders
    print("8. Checking pending orders...")
    pending = await broker.get_pending_orders("test_agent")
    print(f"Pending orders: {len(pending)}")
    if pending:
        for order in pending:
            print(
                f"  - {order.bet_id}: ${order.amount} on {order.selection} @ {order.limit_odds}+"
            )
    print()

    # Update odds (might trigger limit order)
    print("9. Updating odds...")
    odds_update_event = StreamEvent(
        stream_id="nba_odds_stream",
        payload={
            "type": "odds_update",
            "event_id": "lakers_vs_warriors",
            "home_odds": 1.90,
            "away_odds": 2.25,  # Increased, will trigger limit order
        },
        emitted_at=datetime.now(),
    )
    await broker.handle_stream_event(odds_update_event)
    print("Odds updated - limit order should execute")
    print()

    # Check active bets
    print("10. Checking active bets...")
    active_bets = await broker.get_active_bets("test_agent")
    print(f"Active bets: {len(active_bets)}")
    for bet in active_bets:
        print(f"  - {bet.bet_id}: ${bet.amount} on {bet.selection} @ {bet.odds}")
    print()

    # Game starts
    print("11. Game starting...")
    game_start_event = StreamEvent(
        stream_id="nba_game_stream",
        payload={
            "type": "game_start",
            "event_id": "lakers_vs_warriors",
        },
        emitted_at=datetime.now(),
    )
    await broker.handle_stream_event(game_start_event)
    print("Game started - event status changed to LIVE")
    print()

    # Game ends with result
    print("12. Game ending and settling bets...")
    game_result_event = StreamEvent(
        stream_id="nba_results_stream",
        payload={
            "type": "game_result",
            "event_id": "lakers_vs_warriors",
            "winner": "home",  # Lakers win
            "final_score": {"home": 110, "away": 105},
        },
        emitted_at=datetime.now(),
    )
    await broker.handle_stream_event(game_result_event)
    print("Game settled - bets resolved")
    print()

    # Check final balance
    print("13. Checking final balance...")
    balance = await broker.get_balance("test_agent")
    print(f"Final balance: ${balance}")
    print()

    # Get statistics
    print("14. Getting agent statistics...")
    stats = await broker.get_statistics("test_agent")
    print(f"Total bets: {stats.total_bets}")
    print(f"Total wagered: ${stats.total_wagered}")
    print(f"Wins: {stats.wins}")
    print(f"Losses: {stats.losses}")
    print(f"Win rate: {stats.win_rate:.2%}")
    print(f"Net profit: ${stats.net_profit}")
    print(f"ROI: {stats.roi:.2%}")
    print()

    # Check bet history
    print("15. Checking bet history...")
    history = await broker.get_bet_history("test_agent")
    print(f"Historical bets: {len(history)}")
    for bet in history:
        print(
            f"  - {bet.bet_id}: {bet.outcome.value if bet.outcome else 'N/A'}, "
            f"Payout: ${bet.actual_payout if bet.actual_payout else 0}"
        )
    print()

    # Test invalid bet
    print("16. Testing invalid bet (insufficient balance)...")
    result = await broker.place_bet(
        "test_agent",
        BetRequest(
            amount=Decimal("10000.00"),  # Too much
            selection="home",
            event_id="lakers_vs_warriors",
            order_type=OrderType.MARKET,
            betting_phase=BettingPhase.PRE_GAME,
        ),
    )
    print(f"Invalid bet result: {result}")
    print()

    # Cleanup
    await broker.stop()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
