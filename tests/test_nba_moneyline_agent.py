"""
Integration test: DataStream -> Agent (LLM) -> place_bet -> BrokerOperator
"""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from dojozero.agents.agent import BettingAgent
from dojozero.agents.config import load_agent_config
from dojozero.agents.model import _create_model_from_llm_config, _create_formatter
from dojozero.core import StreamEvent
from dojozero.nba_moneyline._broker import BrokerOperator
from dojozero.data.nba._events import GameInitializeEvent, GameResultEvent
from dojozero.data.polymarket._events import OddsUpdateEvent
from datetime import datetime

load_dotenv()

# Test-specific environment variable names to avoid conflicts with other apps
TEST_API_KEY_ENV = "DOJOZERO_TEST_OPENAI_API_KEY"
TEST_BASE_URL_ENV = "DOJOZERO_TEST_OPENAI_BASE_URL"

AGENT_ID = "basic"
CONFIG_PATH = Path(__file__).parent.parent / "configs" / "agents" / "basic.yaml"


def create_test_agent(config_path: Path) -> BettingAgent:
    """Create agent with test-specific env vars, overriding YAML config."""
    config = load_agent_config(config_path)
    llm_config = config["llm"].copy()
    llm_config["api_key_env"] = TEST_API_KEY_ENV
    llm_config["base_url_env"] = TEST_BASE_URL_ENV
    model_type = llm_config.get("model_type", "openai")
    return BettingAgent(
        actor_id=config["agent_id"],
        name=config["name"],
        sys_prompt=config["sys_prompt"],
        model=_create_model_from_llm_config(llm_config),
        formatter=_create_formatter(model_type),
    )


@pytest.fixture
def broker():
    """Create BrokerOperator with initial balance for test agent."""
    return BrokerOperator.from_dict(
        {
            "actor_id": "nba-broker",
            "initial_balance": "1000.00",
        }
    )


@pytest.fixture
def agent():
    """Create BettingAgent with test-specific env vars."""
    return create_test_agent(CONFIG_PATH)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(TEST_API_KEY_ENV), reason=f"{TEST_API_KEY_ENV} not set"
)
async def test_agent_receives_event_and_places_bet(broker, agent):
    """Test full flow: DataStream -> Agent -> place_bet -> Operator."""
    # await broker.register_agents([agent])
    await agent.register_operators([broker])
    broker.register_agents([agent])
    await broker.start()
    await agent.start()

    print("toolkit:", agent.toolkit.get_json_schemas())

    # Initial state
    initial_balance = await broker.get_balance(AGENT_ID)
    print(f"\nInitial balance: ${initial_balance}")

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
    event_list = [
        StreamEvent(
            stream_id="nba-stream",
            payload={
                "event_id": "lakers_vs_warriors_2024",
                "home_team": "Lakers",
                "away_team": "Warriors",
                "home_odds": "1.85",
                "away_odds": "2.10",
            },
            sequence=0,
        ),
        StreamEvent(
            stream_id="nba-stream",
            payload={
                "event_id": "lakers_vs_warriors_2024",
                "new market stats": "other Agent bet on away_odds. You MUST ADD more bets.",
            },
            sequence=1,
        ),
    ]

    for event in event_list:
        await agent.handle_stream_event(event)

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


if __name__ == "__main__":
    import asyncio

    async def main():
        broker = BrokerOperator.from_dict(
            {
                "actor_id": "nba-broker",
                "initial_balance": "1000.00",
            }
        )
        agent = create_test_agent(CONFIG_PATH)

        await test_agent_receives_event_and_places_bet(broker, agent)

    asyncio.run(main())
