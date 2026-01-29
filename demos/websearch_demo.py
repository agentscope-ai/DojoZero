"""Demo: WebSearch stack with event-class lifecycle.

This demo shows the complete flow:
1. WebSearchAPI with Tavily SDK integration
2. Event classes handle search -> LLM -> typed event via from_web_search()
3. DataHub receiving and persisting events
4. Agents subscribing to events
"""

import asyncio

from dojozero.data import DataHub, WebSearchAPI
from dojozero.data._models import DataEvent
from dojozero.data.websearch._context import GameContext
from dojozero.data.websearch._events import (
    InjuryReportEvent,
    PowerRankingEvent,
    ExpertPredictionEvent,
    WebSearchEventMixin,
)


team1 = "Los Angeles Lakers"
team2 = "San Antonio Spurs"
game_date = "2025-12-10"


class DemoAgent:
    """Simple demo agent that subscribes to web search events."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.received_events: list[DataEvent] = []

    def handle_event(self, event: DataEvent) -> None:
        self.received_events.append(event)
        print(f"  [{self.agent_id}] received {event.event_type}")


async def demo_websearch_stack():
    """Demonstrate the complete websearch stack."""
    print("WebSearch Stack Demo\n")

    # Setup DataHub
    hub = DataHub(
        hub_id="demo_hub",
        persistence_file="outputs/demo_events.jsonl",
    )
    print(f"DataHub: {hub.hub_id} (persist: {hub.persistence_file})")

    # Setup search API and game context
    api = WebSearchAPI()
    context = GameContext(
        sport="nba",
        home_team=team1,
        away_team=team2,
        game_date=game_date,
    )
    print(f"GameContext: {context.teams}")

    # Subscribe agent to typed event types
    agent = DemoAgent("Agent1")
    hub.subscribe_agent(
        "Agent1",
        event_types=[
            "injury_report",
            "power_ranking",
            "expert_prediction",
        ],
        callback=agent.handle_event,
    )
    print(
        "Agent subscribed: Agent1 (injury_report, power_ranking, expert_prediction)\n"
    )

    # Each event class owns the full lifecycle: build query -> search API -> LLM -> typed event
    # Discover all WebSearchEventMixin subclasses automatically
    print("Running web searches via event class from_web_search()...")
    for event_cls in WebSearchEventMixin.__subclasses__():
        print(f"  {event_cls.__name__}...")
        try:
            result = await event_cls.from_web_search(api=api, context=context)
            if result is not None and isinstance(result, DataEvent):
                await hub.receive_event(result)
                print(f"    -> emitted {result.event_type}")
            else:
                print("    -> no results")
        except Exception as e:
            print(f"    -> error: {e}")

    # Results summary
    print(f"\nResults: {len(agent.received_events)} events received")
    for event in agent.received_events:
        if isinstance(event, InjuryReportEvent):
            teams = list(event.injured_players.keys()) if event.injured_players else []
            total_players = sum(len(p) for p in event.injured_players.values())
            print(f"  Injury: {len(teams)} teams, {total_players} players")
        elif isinstance(event, PowerRankingEvent):
            sources = list(event.rankings.keys()) if event.rankings else []
            total_teams = sum(len(t) for t in event.rankings.values())
            print(f"  Rankings: {len(sources)} sources, {total_teams} teams")
        elif isinstance(event, ExpertPredictionEvent):
            print(f"  Predictions: {len(event.predictions)} predictions")

    # Persistence check
    if hub.persistence_file.exists():
        with open(hub.persistence_file, "r") as f:
            lines = f.readlines()
        print(
            f"\nPersisted: {len(lines)} events ({hub.persistence_file.stat().st_size} bytes)"
        )

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(demo_websearch_stack())
