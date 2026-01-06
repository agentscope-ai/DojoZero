"""
Integration test: BettingAgentGroup with multiple agents communicating via MsgHub
"""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from dojozero.nba_moneyline import BettingAgent, BettingAgentGroup
from dojozero.agents._config import load_agent_config, create_model, create_formatter
from dojozero.core import StreamEvent
from dojozero.nba_moneyline._broker import BrokerOperator
from dojozero.data.nba._events import GameInitializeEvent, GameResultEvent
from dojozero.data.polymarket._events import OddsUpdateEvent
from datetime import datetime

load_dotenv()

# Test-specific environment variable names to avoid conflicts with other apps
TEST_API_KEY_ENV = "DOJOZERO_OPENAI_API_KEY"
TEST_BASE_URL_ENV = "DOJOZERO_OPENAI_BASE_URL"

CONFIG_DIR = Path(__file__).parent.parent / "configs" / "agents"
WHALE_CONFIG = CONFIG_DIR / "whale.yaml"
SHEEP_CONFIG = CONFIG_DIR / "sheep.yaml"
SHARK_CONFIG = CONFIG_DIR / "shark.yaml"


def create_test_agent(config_path: Path) -> BettingAgent:
    """Create agent with test-specific env vars, overriding YAML config."""
    config = load_agent_config(config_path)
    llm_config = config["llm"].copy()
    llm_config["api_key_env"] = TEST_API_KEY_ENV
    llm_config["base_url_env"] = TEST_BASE_URL_ENV
    model_type = llm_config.get("model_type", "openai")
    return BettingAgent(
        actor_id=config["name"],
        name=config["name"],
        sys_prompt=config["sys_prompt"],
        model=create_model(llm_config),
        formatter=create_formatter(model_type),
    )


class TestBettingAgentGroup(BettingAgentGroup):
    """Test-specific group that creates agents with test env vars."""

    def __init__(self, config_paths: list[Path]) -> None:
        # Skip parent __init__, create agents with test env vars directly
        self._agents = [create_test_agent(path) for path in config_paths]
        self._agent_colors: dict[str, str] = {}


@pytest.fixture
def broker():
    """Create BrokerOperator with initial balance for all agents."""
    return BrokerOperator.from_dict(
        {
            "actor_id": "nba-broker",
            "initial_balance": "1000.00",
        }
    )


@pytest.fixture
def agent_group():
    """Create BettingAgentGroup with whale, sheep, shark agents using test env vars."""
    return TestBettingAgentGroup(
        config_paths=[WHALE_CONFIG, SHEEP_CONFIG, SHARK_CONFIG]
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(TEST_API_KEY_ENV), reason=f"{TEST_API_KEY_ENV} not set"
)
async def test_group_receives_event_and_discusses(broker, agent_group):
    """Test group communication: all agents discuss a market event."""

    # TBD: broker.register_agents(group.agents)
    await agent_group.register_operators([broker])
    broker.register_agents(agent_group.agents)

    await agent_group.start()

    # Check agents are initialized
    assert len(agent_group.agents) == 3
    assert set(agent_group.agent_ids) == {"whale", "sheep", "shark"}

    # Initialize event in broker first
    game_init_event = GameInitializeEvent(
        event_id="lakers_vs_warriors_2024",
        game_id="lakers_vs_warriors_2024",
        home_team="Lakers",
        away_team="Warriors",
        game_time=datetime.now(),
    )
    await broker.handle_stream_event(
        StreamEvent(stream_id="nba-stream", payload=game_init_event, sequence=-2)
    )

    odds_update_event = OddsUpdateEvent(
        event_id="lakers_vs_warriors_2024",
        home_odds=1.85,
        away_odds=2.10,
    )
    await broker.handle_stream_event(
        StreamEvent(stream_id="nba-stream", payload=odds_update_event, sequence=-1)
    )

    # Simulate DataStream sending match data
    event = StreamEvent(
        stream_id="nba-stream",
        payload={
            "event_id": "lakers_vs_warriors_2024",
            "home_team": "Lakers",
            "away_team": "Warriors",
            "home_odds": "1.85",
            "away_odds": "2.10",
        },
        sequence=0,
    )

    print(f"\nSending event to group: {event.payload}")

    # Group handles event - agents discuss via MsgHub
    messages = await agent_group.handle_stream_event(event, max_rounds=2)

    print(f"\nGroup discussion produced {len(messages)} messages")
    for msg in messages:
        content = msg.content[0]["text"]
        print(f"  [{msg.name}]: {content}...")

    game_result_event = GameResultEvent(
        event_id="lakers_vs_warriors_2024",
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
    for AGENT_ID in agent_group.agent_ids:
        final_balance = await broker.get_balance(AGENT_ID)
        active_bets = await broker.get_active_bets(AGENT_ID)
        stats = await broker.get_statistics(AGENT_ID)

        print(f"\n[{AGENT_ID}]Results:")
        print(f"  Events processed: {agent.event_count}")
        print(f"  Final balance: ${final_balance}")
        print(f"  Active bets: {len(active_bets)}")
        print(f"  Stats: {stats}")

    await agent_group.stop()
    await broker.stop()


if __name__ == "__main__":
    import asyncio
    import logging

    # Enable logging to see agent colors
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    async def main():
        broker = BrokerOperator.from_dict(
            {
                "actor_id": "nba-broker",
                "initial_balance": "1000.00",
            }
        )

        group = TestBettingAgentGroup(
            config_paths=[WHALE_CONFIG, SHEEP_CONFIG, SHARK_CONFIG],
        )

        await test_group_receives_event_and_discusses(broker, group)

    asyncio.run(main())
