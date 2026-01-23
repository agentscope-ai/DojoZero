"""
Integration test: DataStream -> Agent (LLM) -> place_bet -> BrokerOperator
Tests both local and Ray runtime.
"""

import os
import sys
from pathlib import Path

import pytest
import ray

from dojozero.agents import load_agent_config
from dojozero.betting import BrokerOperator
from dojozero.core import RuntimeContext, AgentSpec, OperatorSpec, StreamEvent
from dojozero.data.nba._events import GameInitializeEvent, GameResultEvent
from dojozero.data.polymarket._events import OddsUpdateEvent
from dojozero.nba_moneyline._agent import BettingAgent, BettingAgentConfig
from dojozero.ray_runtime import RayActorRuntimeProvider

# Import shared fixtures - conftest.py is auto-loaded by pytest
sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    BASIC_CONFIG_PATH,
    TEST_API_KEY_ENV,
    TEST_BASE_URL_ENV,
    create_broker_fixture,
    create_nba_test_agent,
)

AGENT_ID = "basic"
BROKER_ID = "nba-broker"


@pytest.fixture
def broker():
    """Create BrokerOperator with initial balance for test agent."""
    return create_broker_fixture("nba-broker")


@pytest.fixture
def agent():
    """Create BettingAgent with test-specific env vars."""
    return create_nba_test_agent(BASIC_CONFIG_PATH)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get(TEST_API_KEY_ENV), reason=f"{TEST_API_KEY_ENV} not set"
)
async def test_agent_receives_event_and_places_bet(
    broker, agent, nba_game_init_data, nba_odds_data
):
    """Test full flow: DataStream -> Agent -> place_bet -> Operator."""
    await agent.register_operators([broker])
    broker.register_agents([agent])
    await broker.start()
    await agent.start()

    print("toolkit:", agent.toolkit.get_json_schemas())

    # Initial state
    initial_balance = await broker.get_balance(AGENT_ID)
    print(f"\nInitial balance: ${initial_balance}")

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
    event_list = [
        StreamEvent(
            stream_id="nba-stream",
            payload={
                "event_id": event_id,
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
                "event_id": event_id,
                "new market stats": "other Agent bet on away_odds. You MUST ADD more bets.",
            },
            sequence=1,
        ),
    ]

    for event in event_list:
        await agent.handle_stream_event(event)

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


# --- Ray Runtime Tests ---


@pytest.fixture(scope="module")
def ray_env():
    """Initialize Ray for the test module."""
    if ray.is_initialized():
        ray.shutdown()
    # Exclude pyproject.toml/uv.lock to prevent Ray workers from creating
    # a new venv with wrong Python version when packaging local modules
    ray.init(
        include_dashboard=False,
        runtime_env={"excludes": ["pyproject.toml", "uv.lock"]},
    )
    yield
    ray.shutdown()


@pytest.fixture
def broker_spec() -> OperatorSpec:
    """Create OperatorSpec for broker as Ray actor."""
    return OperatorSpec(
        actor_id=BROKER_ID,
        actor_cls=BrokerOperator,
        config={
            "actor_id": BROKER_ID,
            "initial_balance": "1000.00",
        },
        agent_ids=(AGENT_ID,),
    )


def _create_ray_agent_config() -> BettingAgentConfig:
    """Create agent config with test-specific env vars for Ray."""
    config = load_agent_config(BASIC_CONFIG_PATH)
    # llm is a list of configs - use the first one for tests
    llm_config = config["llm"][0]
    return BettingAgentConfig(
        actor_id=config["name"],
        name=config.get("name", ""),
        sys_prompt=config.get("sys_prompt", ""),
        llm={
            "model_type": llm_config.get("model_type", "openai"),
            "model_name": llm_config.get("model_name", "qwen3-max"),
            "api_key_env": TEST_API_KEY_ENV,
            "base_url_env": TEST_BASE_URL_ENV,
        },
    )


@pytest.fixture
def agent_spec() -> AgentSpec:
    """Create AgentSpec for Ray runtime with test-specific env vars."""
    agent_config = _create_ray_agent_config()
    return AgentSpec(
        actor_id=agent_config.get("actor_id", AGENT_ID),
        actor_cls=BettingAgent,
        config=agent_config,
        operator_ids=(BROKER_ID,),
    )


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get(TEST_API_KEY_ENV), reason=f"{TEST_API_KEY_ENV} not set"
)
async def test_agent_with_ray_runtime(
    ray_env, broker_spec, agent_spec, nba_game_init_data, nba_odds_data
):
    """Test full flow with Ray runtime: DataStream -> Ray Agent -> place_bet -> Ray Operator."""
    provider = RayActorRuntimeProvider(auto_init=False)
    context = RuntimeContext(
        trial_id="test-trial",
        data_hubs={},
        stores={},
        startup=None,
    )

    # Create broker as Ray actor
    broker_handler = await provider.create_handler(broker_spec, context)
    print(f"\nCreated Ray broker: {broker_handler.actor_id}")
    await broker_handler.start()
    print("Broker started in Ray")

    # Create agent as Ray actor
    agent_handler = await provider.create_handler(agent_spec, context)
    print(f"Created Ray agent: {agent_handler.actor_id}")

    # Register broker as operator for the agent
    await agent_handler.instance.register_operators([broker_handler.instance])  # type: ignore[attr-defined]
    await broker_handler.instance.register_agents([agent_handler.instance])  # type: ignore[attr-defined]

    await agent_handler.start()
    print("Agent started in Ray")

    # Initial state (via Ray proxy)
    initial_balance = await broker_handler.instance.get_balance(AGENT_ID)  # type: ignore[attr-defined]
    print(f"\nInitial balance: ${initial_balance}")

    # Initialize event in broker first
    game_init_event = GameInitializeEvent(**nba_game_init_data)
    await broker_handler.instance.handle_stream_event(  # type: ignore[attr-defined]
        StreamEvent(stream_id="nba-stream", payload=game_init_event, sequence=-2)
    )

    odds_update_event = OddsUpdateEvent(**nba_odds_data)
    await broker_handler.instance.handle_stream_event(  # type: ignore[attr-defined]
        StreamEvent(stream_id="nba-stream", payload=odds_update_event, sequence=-1)
    )

    # Simulate DataStream sending match data
    event_id = nba_game_init_data["event_id"]
    event_list = [
        StreamEvent(
            stream_id="nba-stream",
            payload={
                "event_id": event_id,
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
                "event_id": event_id,
                "new market stats": "other Agent bet on away_odds. You MUST ADD more bets.",
            },
            sequence=1,
        ),
    ]

    for event in event_list:
        print(f"Sending event: {event.payload}")
        await agent_handler.instance.handle_stream_event(event)  # type: ignore[attr-defined]

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

    await broker_handler.instance.handle_stream_event(final_event)  # type: ignore[attr-defined]

    # Check results (via Ray proxy)
    final_balance = await broker_handler.instance.get_balance(AGENT_ID)  # type: ignore[attr-defined]
    active_bets = await broker_handler.instance.get_active_bets(AGENT_ID)  # type: ignore[attr-defined]
    stats = await broker_handler.instance.get_statistics(AGENT_ID)  # type: ignore[attr-defined]

    # Get events_processed from Ray agent
    state = await agent_handler.save_state()

    print("\nResults:")
    print(f"  Events processed: {state['events']}")
    print(f"  Final balance: ${final_balance}")
    print(f"  Active bets: {len(active_bets)}")
    print(f"  Stats: {stats}")

    await agent_handler.stop()
    await broker_handler.stop()
    print("Agent and broker stopped")
