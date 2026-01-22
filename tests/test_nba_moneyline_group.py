"""
Integration test: BettingAgentGroup with multiple agents communicating via MsgHub
"""

import os
from pathlib import Path

import pytest

from dojozero.nba_moneyline._group import BettingAgentGroup
from dojozero.core import StreamEvent
from dojozero.data.nba._events import GameInitializeEvent, GameResultEvent
from dojozero.data.polymarket._events import OddsUpdateEvent

# Import shared fixtures - conftest.py is auto-loaded by pytest
import sys

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    TEST_API_KEY_ENV,
    WHALE_CONFIG_PATH,
    SHEEP_CONFIG_PATH,
    SHARK_CONFIG_PATH,
    create_broker_fixture,
    create_nba_test_agent,
)


class _BettingAgentGroupForTest(BettingAgentGroup):
    """Test-specific group that creates agents with test env vars."""

    def __init__(
        self,
        config_paths: list[Path],
        actor_id: str = "test-group",
        trial_id: str = "test-trial",
        max_rounds: int = 1,
    ) -> None:
        # Create agents with test env vars directly
        agents = [create_nba_test_agent(path, trial_id) for path in config_paths]
        # Initialize parent with actor_id, trial_id, and agents
        super().__init__(
            actor_id=actor_id,
            trial_id=trial_id,
            agents=agents,
            max_rounds=max_rounds,
        )


@pytest.fixture
def broker():
    """Create BrokerOperator with initial balance for all agents."""
    return create_broker_fixture("nba-broker")


@pytest.fixture
def agent_group():
    """Create BettingAgentGroup with whale, sheep, shark agents using test env vars."""
    return _BettingAgentGroupForTest(
        config_paths=[WHALE_CONFIG_PATH, SHEEP_CONFIG_PATH, SHARK_CONFIG_PATH],
        max_rounds=2,
    )


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(TEST_API_KEY_ENV), reason=f"{TEST_API_KEY_ENV} not set"
)
async def test_group_receives_event_and_discusses(
    broker, agent_group, nba_game_init_data, nba_odds_data
):
    """Test group communication: all agents discuss a market event."""
    await agent_group.register_operators([broker])
    broker.register_agents(agent_group.agents)

    await agent_group.start()

    # Check agents are initialized
    assert len(agent_group.agents) == 3
    assert set(agent_group.agent_ids) == {"whale", "sheep", "shark"}

    # Initialize event in broker first
    game_init_event = GameInitializeEvent(**nba_game_init_data)
    await broker.handle_stream_event(
        StreamEvent(stream_id="nba-stream", payload=game_init_event, sequence=-2)
    )

    odds_update_event = OddsUpdateEvent(**nba_odds_data)
    await broker.handle_stream_event(
        StreamEvent(stream_id="nba-stream", payload=odds_update_event, sequence=-1)
    )

    # Simulate DataStream sending match data
    event_id = nba_game_init_data["event_id"]
    event = StreamEvent(
        stream_id="nba-stream",
        payload={
            "event_id": event_id,
            "home_team": "Lakers",
            "away_team": "Warriors",
            "home_odds": "1.85",
            "away_odds": "2.10",
        },
        sequence=0,
    )

    print(f"\nSending event to group: {event.payload}")

    # Group handles event - agents discuss via MsgHub
    messages = await agent_group._handle_stream_event_with_rounds(event, max_rounds=2)

    print(f"\nGroup discussion produced {len(messages)} messages")
    for msg in messages:
        content = msg.content[0]["text"]
        print(f"  [{msg.name}]: {content}...")

    game_result_event = GameResultEvent(
        event_id=event_id,
        winner="home",
        final_score={"home": 110, "away": 105},
    )
    final_event = StreamEvent(
        stream_id="nba-stream",
        payload=game_result_event,
        sequence=2,
    )
    await broker.handle_stream_event(final_event)

    # Each agent should have processed the event
    for agent in agent_group.agents:
        assert agent.event_count >= 1, f"Agent {agent.name} didn't process event"

    # Check results
    for agent_id in agent_group.agent_ids:
        final_balance = await broker.get_balance(agent_id)
        active_bets = await broker.get_active_bets(agent_id)
        stats = await broker.get_statistics(agent_id)

        print(f"\n[{agent_id}] Results:")
        print(f"  Final balance: ${final_balance}")
        print(f"  Active bets: {len(active_bets)}")
        print(f"  Stats: {stats}")

    await agent_group.stop()
    await broker.stop()
