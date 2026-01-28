"""
Integration test: DataStream -> Agent (LLM) -> place_bet -> BrokerOperator
for NFL moneyline betting.
"""

import os

import pytest

from dojozero.core import StreamEvent
from dojozero.data.nfl._events import (
    NFLGameInitializeEvent,
    NFLGameResultEvent,
    NFLOddsUpdateEvent,
)

# Import shared fixtures - conftest.py is auto-loaded by pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    DEFAULT_LLM_CONFIG_PATH,
    BASIC_PERSONA_PATH,
    TEST_API_KEY_ENV,
    create_broker_fixture,
    create_nfl_test_agent,
)

AGENT_ID = "basic"


@pytest.fixture
def broker():
    """Create BrokerOperator with initial balance for test agent."""
    return create_broker_fixture("nfl-broker")


@pytest.fixture
def agent():
    """Create NFL BettingAgent with test-specific env vars."""
    return create_nfl_test_agent(DEFAULT_LLM_CONFIG_PATH, BASIC_PERSONA_PATH)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get(TEST_API_KEY_ENV), reason=f"{TEST_API_KEY_ENV} not set"
)
async def test_nfl_agent_receives_event_and_places_bet(
    broker, agent, nfl_game_init_data, nfl_odds_data
):
    """Test full flow: DataStream -> Agent -> place_bet -> Operator for NFL."""
    await agent.register_operators([broker])
    broker.register_agents([agent])
    await broker.start()
    await agent.start()

    print("toolkit:", agent.toolkit.get_json_schemas())

    # Initial state
    initial_balance = await broker.get_balance(AGENT_ID)
    print(f"\nInitial balance: ${initial_balance}")

    # Initialize NFL game event in broker first
    game_init_event = NFLGameInitializeEvent(**nfl_game_init_data)
    await broker.handle_stream_event(
        StreamEvent(stream_id="nfl-stream", payload=game_init_event, sequence=-2)
    )

    # NFL uses American moneyline odds (e.g., -150, +130)
    odds_update_event = NFLOddsUpdateEvent(**nfl_odds_data)
    await broker.handle_stream_event(
        StreamEvent(stream_id="nfl-stream", payload=odds_update_event, sequence=-1)
    )

    # Simulate DataStream sending match data
    game_id = nfl_game_init_data["game_id"]
    event_list = [
        StreamEvent(
            stream_id="nfl-stream",
            payload={
                "game_id": game_id,
                "home_team": "Baltimore Ravens",
                "away_team": "Kansas City Chiefs",
                "moneyline_home": "-150",
                "moneyline_away": "+130",
                "spread": "-3.0",
            },
            sequence=0,
        ),
        StreamEvent(
            stream_id="nfl-stream",
            payload={
                "game_id": game_id,
                "analysis": "Ravens have strong running game. Chiefs have Mahomes.",
            },
            sequence=1,
        ),
    ]

    for event in event_list:
        await agent.handle_stream_event(event)

    # Simulate game result - Ravens win
    game_result_event = NFLGameResultEvent(
        game_id=game_id,
        winner="home",
        final_score={"home": 28, "away": 24},
        home_team="Baltimore Ravens",
        away_team="Kansas City Chiefs",
    )
    final_event = StreamEvent(
        stream_id="nfl-stream",
        payload=game_result_event,
        sequence=2,
    )

    await broker.handle_stream_event(final_event)

    # Check results
    final_balance = await broker.get_balance(AGENT_ID)
    active_bets = await broker.get_active_bets(AGENT_ID)
    stats = await broker.get_statistics(AGENT_ID)

    print("\nResults:")
    print(f"  Events processed: {agent.event_count}")
    print(f"  Final balance: ${final_balance}")
    print(f"  Active bets: {len(active_bets)}")
    print(f"  Total wagered: {stats}")

    await agent.stop()
    await broker.stop()
