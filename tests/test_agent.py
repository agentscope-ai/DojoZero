"""
Integration test: DataStream -> Agent (LLM) -> place_bet -> BrokerOperator

Run with: python -m pytest tests/test_agent.py -v -s
Requires OPENAI_API_KEY and OPENAI_BASE_URL in .env
"""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from agentx.agents.agent import BettingAgent
from agentx.core import StreamEvent
from agentx.samples.nba_moneyline._broker import BrokerOperator

load_dotenv()
AGENT_ID = "basic"
CONFIG_PATH = Path(__file__).parent.parent / "configs" / "agents" / "basic.yaml"


@pytest.fixture
def broker():
    """Create BrokerOperator with initial balance for test agent."""
    return BrokerOperator.from_dict(
        {
            "actor_id": "nba-broker",
            "initial_balances": {AGENT_ID: "1000.00"},
        }
    )


@pytest.fixture
def agent():
    """Create BettingAgent from YAML config."""
    return BettingAgent.from_yaml(CONFIG_PATH)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set"
)
async def test_agent_receives_event_and_places_bet(broker, agent):
    """Test full flow: DataStream -> Agent -> place_bet -> Operator."""
    # await broker.register_agents([agent])
    await agent.register_operators([broker])
    await broker.start()
    await agent.start()

    print("toolkit:", agent.toolkit.get_json_schemas())

    # Initial state
    initial_balance = await broker.get_balance(AGENT_ID)
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
        await agent.handle_stream_event(event)

    final_event = StreamEvent(
        stream_id="nba-stream",
        payload={"event_id": "lakers_vs_warriors_2024", "winner": "home"},
        sequence=2,
    )

    await broker.settle_event(event=final_event)

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
                "initial_balances": {AGENT_ID: "1000.00"},
            }
        )
        agent = BettingAgent.from_yaml(CONFIG_PATH)

        await test_agent_receives_event_and_places_bet(broker, agent)

    asyncio.run(main())
