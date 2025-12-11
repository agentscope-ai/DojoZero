"""
Integration test: BettingAgentGroup with multiple agents communicating via MsgHub

Run with: python -m pytest tests/test_group.py -v -s
Requires OPENAI_API_KEY and OPENAI_BASE_URL in .env
"""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from agentx.agents.group import BettingAgentGroup
from agentx.core import StreamEvent
from agentx.samples.nba_moneyline._broker import BrokerOperator

load_dotenv()

CONFIG_DIR = Path(__file__).parent.parent / "configs" / "agents"
WHALE_CONFIG = CONFIG_DIR / "whale.yaml"
SHEEP_CONFIG = CONFIG_DIR / "sheep.yaml"
SHARK_CONFIG = CONFIG_DIR / "shark.yaml"


@pytest.fixture
def broker():
    """Create BrokerOperator with initial balance for all agents."""
    return BrokerOperator.from_dict(
        {
            "actor_id": "nba-broker",
            "initial_balances": {
                "whale": "10000.00",
                "sheep": "1000.00",
                "shark": "5000.00",
            },
        }
    )


@pytest.fixture
def agent_group():
    """Create BettingAgentGroup with whale, sheep, shark agents."""
    return BettingAgentGroup(config_paths=[WHALE_CONFIG, SHEEP_CONFIG, SHARK_CONFIG])


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set"
)
async def test_group_receives_event_and_discusses(broker, agent_group):
    """Test group communication: all agents discuss a market event."""

    # TBD: broker.register_agents(group.agents)
    await agent_group.register_operators([broker])

    await agent_group.start()

    # Check agents are initialized
    assert len(agent_group.agents) == 3
    assert set(agent_group.agent_ids) == {"whale", "sheep", "shark"}

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
    messages = await agent_group.handle_stream_event(event, max_rounds=1)

    print(f"\nGroup discussion produced {len(messages)} messages")
    for msg in messages:
        content = msg.content[0]["text"]
        print(f"  [{msg.name}]: {content}...")

    final_event = StreamEvent(
        stream_id="nba-stream",
        payload={"event_id": "lakers_vs_warriors_2024", "winner": "home"},
        sequence=2,
    )
    await broker.settle_event(final_event)

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
                "initial_balances": {
                    AGENT_ID: "1000.00" for AGENT_ID in ["whale", "sheep", "shark"]
                },
            }
        )

        group = BettingAgentGroup(
            config_paths=[WHALE_CONFIG, SHEEP_CONFIG, SHARK_CONFIG],
        )

        await test_group_receives_event_and_discusses(broker, group)

    asyncio.run(main())
