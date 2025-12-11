"""
Test BettingAgent as Ray actor with BrokerOperator (also as Ray actor).

Run with: python -m pytest tests/test_agent_ray.py -v -s
Requires OPENAI_API_KEY and OPENAI_BASE_URL in .env
"""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
import ray

from agentx.agents.agent import BettingAgent
from agentx.agents.config import load_agent_config
from agentx.core import AgentSpec, OperatorSpec, StreamEvent
from agentx.ray_runtime import RayActorRuntimeProvider
from agentx.samples.nba_moneyline_op import BrokerOperator

load_dotenv()

AGENT_ID = "basic"
BROKER_ID = "nba-broker"
CONFIG_PATH = Path(__file__).parent.parent / "configs" / "agents" / "basic.yaml"


@pytest.fixture(scope="module")
def ray_env():
    """Initialize Ray for the test module."""
    if ray.is_initialized():
        ray.shutdown()
    ray.init(include_dashboard=False)
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
            "initial_balances": {AGENT_ID: "1000.00"},
        },
        agent_ids=(AGENT_ID,),
    )


@pytest.fixture
def agent_spec() -> AgentSpec:
    """Create AgentSpec for Ray runtime."""
    config = load_agent_config(CONFIG_PATH)
    llm_config = config.get("llm", {})
    agent_config = {
        "actor_id": config["agent_id"],
        "name": config.get("name", config["agent_id"]),
        "sys_prompt": config.get("sys_prompt", ""),
        "model_type": llm_config.get("model_type", "openai"),
        "model_name": llm_config.get("model_name", "qwen3-max"),
        "api_key_env": llm_config.get("api_key_env", "OPENAI_API_KEY"),
        "base_url_env": llm_config.get("base_url_env", "OPENAI_BASE_URL"),
    }
    return AgentSpec(
        actor_id=agent_config["actor_id"],
        actor_cls=BettingAgent,
        config=agent_config,
        operator_ids=(BROKER_ID,),
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set"
)
async def test_betting_agent_as_ray_actor(broker_spec, agent_spec):
    """Test full flow: DataStream -> Ray Agent -> place_bet -> Ray Operator."""
    provider = RayActorRuntimeProvider(auto_init=False)

    # Create broker as Ray actor
    broker_handler = await provider.create_handler(broker_spec)
    print(f"\nCreated Ray broker: {broker_handler.actor_id}")
    await broker_handler.start()
    print("Broker started in Ray")

    # Create agent as Ray actor
    agent_handler = await provider.create_handler(agent_spec)
    print(f"Created Ray agent: {agent_handler.actor_id}")

    # Register broker as operator for the agent
    await agent_handler.instance.register_operators([broker_handler.instance])

    await agent_handler.start()
    print("Agent started in Ray")

    # Initial state (via Ray proxy)
    initial_balance = await broker_handler.instance.get_balance(AGENT_ID)
    print(f"\nInitial balance: ${initial_balance}")

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
        await agent_handler.instance.handle_stream_event(event)

    final_event = StreamEvent(
        stream_id="nba-stream",
        payload={"event_id": "lakers_vs_warriors_2024", "winner": "home"},
        sequence=2,
    )

    await broker_handler.instance.settle_event(final_event)

    # Check results (via Ray proxy)
    final_balance = await broker_handler.instance.get_balance(AGENT_ID)
    active_bets = await broker_handler.instance.get_active_bets(AGENT_ID)
    stats = await broker_handler.instance.get_statistics(AGENT_ID)

    # Get events_processed from Ray agent
    state = await agent_handler.save_state()

    print("\nResults:")
    print(f"  Events processed: {state['events']}")
    print(f"  Final balance: ${final_balance}")
    print(f"  Active bets: {len(active_bets)}")
    print(f"  Stats: {stats}")

    # Verify
    assert state["events"] == 2

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
                "initial_balances": {AGENT_ID: "1000.00"},
            },
            agent_ids=(AGENT_ID,),
        )

        config = load_agent_config(CONFIG_PATH)
        llm_config = config.get("llm", {})
        agent_config = {
            "actor_id": config["agent_id"],
            "name": config.get("name", config["agent_id"]),
            "sys_prompt": config.get("sys_prompt", ""),
            "model_type": llm_config.get("model_type", "openai"),
            "model_name": llm_config.get("model_name", "qwen3-max"),
            "api_key_env": llm_config.get("api_key_env", "OPENAI_API_KEY"),
            "base_url_env": llm_config.get("base_url_env", "OPENAI_BASE_URL"),
        }

        agent_spec = AgentSpec(
            actor_id=agent_config["actor_id"],
            actor_cls=BettingAgent,
            config=agent_config,
            operator_ids=(BROKER_ID,),
        )

        await test_betting_agent_as_ray_actor(broker_spec, agent_spec)
        ray.shutdown()

    asyncio.run(main())
