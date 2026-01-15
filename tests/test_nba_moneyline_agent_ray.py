"""
Test BettingAgent as Ray actor with BrokerOperator (also as Ray actor).
"""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
import ray


from dojozero.nba_moneyline import BettingAgent, BettingAgentConfig
from dojozero.agents import load_agent_config
from dojozero.core import ActorContext, AgentSpec, OperatorSpec, StreamEvent
from dojozero.ray_runtime import RayActorRuntimeProvider
from dojozero.nba_moneyline._broker import BrokerOperator
from dojozero.data.nba._events import GameInitializeEvent, GameResultEvent
from dojozero.data.polymarket._events import OddsUpdateEvent
from datetime import datetime

load_dotenv()

# Test-specific environment variable names to avoid conflicts with other apps
TEST_API_KEY_ENV = "DOJOZERO_OPENAI_API_KEY"
TEST_BASE_URL_ENV = "DOJOZERO_OPENAI_BASE_URL"

AGENT_ID = "basic"
BROKER_ID = "nba-broker"
CONFIG_PATH = Path(__file__).parent.parent / "configs" / "agents" / "basic.yaml"


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


def _create_agent_config() -> BettingAgentConfig:
    """Create agent config with test-specific env vars."""
    config = load_agent_config(CONFIG_PATH)
    llm_config = config.get("llm", {})
    return BettingAgentConfig(
        actor_id=config["name"],
        name=config.get("name", ""),
        sys_prompt=config.get("sys_prompt", ""),
        model_type=llm_config.get("model_type", "openai"),  # type: ignore[typeddict-item]
        model_name=llm_config.get("model_name", "qwen3-max"),
        api_key_env=TEST_API_KEY_ENV,
        base_url_env=TEST_BASE_URL_ENV,
    )


@pytest.fixture
def agent_spec() -> AgentSpec:
    """Create AgentSpec for Ray runtime with test-specific env vars."""
    agent_config = _create_agent_config()
    return AgentSpec(
        actor_id=agent_config.get("actor_id", AGENT_ID),
        actor_cls=BettingAgent,
        config=agent_config,
        operator_ids=(BROKER_ID,),
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(TEST_API_KEY_ENV), reason=f"{TEST_API_KEY_ENV} not set"
)
async def test_betting_agent_as_ray_actor(ray_env, broker_spec, agent_spec):
    """Test full flow: DataStream -> Ray Agent -> place_bet -> Ray Operator."""
    provider = RayActorRuntimeProvider(auto_init=False)
    context = ActorContext(
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
    game_init_event = GameInitializeEvent(
        event_id="lakers_vs_warriors_2024",
        game_id="lakers_vs_warriors_2024",
        home_team="Lakers",
        away_team="Warriors",
        game_time=datetime.now(),
    )
    await broker_handler.instance.handle_stream_event(  # type: ignore[attr-defined]
        StreamEvent(stream_id="nba-stream", payload=game_init_event, sequence=-2)
    )

    odds_update_event = OddsUpdateEvent(
        event_id="lakers_vs_warriors_2024",
        home_odds=1.85,
        away_odds=2.10,
    )
    await broker_handler.instance.handle_stream_event(  # type: ignore[attr-defined]
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
        print(f"Sending event: {event.payload}")
        await agent_handler.instance.handle_stream_event(event)  # type: ignore[attr-defined]

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


if __name__ == "__main__":
    import asyncio

    async def main():
        if ray.is_initialized():
            ray.shutdown()
        ray.init(include_dashboard=False)

        broker_spec = OperatorSpec(
            actor_id=BROKER_ID,
            actor_cls=BrokerOperator,
            config={
                "actor_id": BROKER_ID,
                "initial_balance": "1000.00",
            },
            agent_ids=(AGENT_ID,),
        )

        agent_config = _create_agent_config()
        agent_spec = AgentSpec(
            actor_id=agent_config.get("actor_id", AGENT_ID),
            actor_cls=BettingAgent,
            config=agent_config,
            operator_ids=(BROKER_ID,),
        )

        await test_betting_agent_as_ray_actor(None, broker_spec, agent_spec)
        ray.shutdown()

    asyncio.run(main())
