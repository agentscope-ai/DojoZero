"""
Integration test: DataStream -> Agent (LLM) -> place_bet -> BrokerOperator
for NFL moneyline betting.
"""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from dojozero.nfl_moneyline._agent import BettingAgent
from dojozero.betting import BrokerOperator
from dojozero.agents import load_agent_config, create_model, create_formatter
from dojozero.core import RuntimeContext, StreamEvent
from dojozero.data.nfl._events import (
    NFLGameInitializeEvent,
    NFLGameResultEvent,
    NFLOddsUpdateEvent,
)
from datetime import datetime

load_dotenv()

# Test-specific environment variable names to avoid conflicts with other apps
TEST_API_KEY_ENV = "DOJOZERO_OPENAI_API_KEY"
TEST_BASE_URL_ENV = "DOJOZERO_OPENAI_BASE_URL"

AGENT_ID = "basic"
CONFIG_PATH = Path(__file__).parent.parent / "configs" / "agents" / "basic.yaml"


def create_test_agent(config_path: Path, trial_id: str = "test-trial") -> BettingAgent:
    """Create NFL agent with test-specific env vars, overriding YAML config."""
    config = load_agent_config(config_path)
    llm_config = config["llm"].copy()
    llm_config["api_key_env"] = TEST_API_KEY_ENV
    llm_config["base_url_env"] = TEST_BASE_URL_ENV
    model_type = llm_config.get("model_type", "openai")
    return BettingAgent(
        actor_id=config["name"],
        trial_id=trial_id,
        name=config["name"],
        sys_prompt=config["sys_prompt"],
        model=create_model(llm_config),
        formatter=create_formatter(model_type),
    )


@pytest.fixture
def broker():
    """Create BrokerOperator with initial balance for test agent."""
    context = RuntimeContext(
        trial_id="test-trial",
        data_hubs={},
        stores={},
        startup=None,
    )
    return BrokerOperator.from_dict(
        {
            "actor_id": "nfl-broker",
            "initial_balance": "1000.00",
        },
        context,
    )


@pytest.fixture
def agent():
    """Create NFL BettingAgent with test-specific env vars."""
    return create_test_agent(CONFIG_PATH)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(TEST_API_KEY_ENV), reason=f"{TEST_API_KEY_ENV} not set"
)
async def test_nfl_agent_receives_event_and_places_bet(broker, agent):
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
    game_init_event = NFLGameInitializeEvent(
        event_id="chiefs_vs_ravens_2024",
        home_team="Baltimore Ravens",
        away_team="Kansas City Chiefs",
        home_team_abbreviation="BAL",
        away_team_abbreviation="KC",
        venue="M&T Bank Stadium",
        game_time=datetime.now(),
        week=1,
    )
    await broker.handle_stream_event(
        StreamEvent(stream_id="nfl-stream", payload=game_init_event, sequence=-2)
    )

    # NFL uses American moneyline odds (e.g., -150, +130)
    odds_update_event = NFLOddsUpdateEvent(
        event_id="chiefs_vs_ravens_2024",
        provider="Draft Kings",
        moneyline_home=-150,  # Ravens favored
        moneyline_away=+130,  # Chiefs underdog
        spread=-3.0,
        over_under=47.5,
        home_team="Baltimore Ravens",
        away_team="Kansas City Chiefs",
    )
    await broker.handle_stream_event(
        StreamEvent(stream_id="nfl-stream", payload=odds_update_event, sequence=-1)
    )

    # Simulate DataStream sending match data
    event_list = [
        StreamEvent(
            stream_id="nfl-stream",
            payload={
                "event_id": "chiefs_vs_ravens_2024",
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
                "event_id": "chiefs_vs_ravens_2024",
                "analysis": "Ravens have strong running game. Chiefs have Mahomes.",
            },
            sequence=1,
        ),
    ]

    for event in event_list:
        await agent.handle_stream_event(event)

    # Simulate game result - Ravens win
    game_result_event = NFLGameResultEvent(
        event_id="chiefs_vs_ravens_2024",
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


if __name__ == "__main__":
    import asyncio

    async def main():
        context = RuntimeContext(
            trial_id="test-trial",
            data_hubs={},
            stores={},
            startup=None,
        )
        broker = BrokerOperator.from_dict(
            {
                "actor_id": "nfl-broker",
                "initial_balance": "1000.00",
            },
            context,
        )
        agent = create_test_agent(CONFIG_PATH)

        await test_nfl_agent_receives_event_and_places_bet(broker, agent)

    asyncio.run(main())
