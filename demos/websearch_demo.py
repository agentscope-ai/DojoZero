"""Demo: WebSearch stack with Tavily SDK integration.

This demo shows the complete flow:
1. WebSearchAPI with Tavily SDK integration
2. WebSearchStore polling and emitting events
3. DataHub receiving and persisting events
4. Agents subscribing to events
"""

import asyncio

from agentx.core import AgentBase
from agentx.data import DataHub, WebSearchAPI, WebSearchStore
from agentx.data.websearch._events import WebSearchIntent
from agentx.data.websearch._processors import (
    ExpertPredictionProcessor,
    InjurySummaryProcessor,
    PowerRankingProcessor,
)


team1 = "Los Angeles Lakers"
team2 = "San Antonio Spurs"
game_date = "2025-12-10"
game_info = f"{team1} vs {team2} on {game_date}"


class DemoAgent(AgentBase):
    """Simple demo agent that subscribes to web search events."""
    
    def __init__(self, agent_id: str):
        """Initialize demo agent."""
        self.agent_id = agent_id
        self.received_events = []
    
    def handle_event(self, event):
        """Handle received event."""
        self.received_events.append(event)
        if event.event_type == "raw_web_search":  # type: ignore[attr-defined]
            # Type checker issue with decorator, but runtime works correctly
            web_event = event  # type: ignore[assignment]
            print(f"  [{self.agent_id}] {event.event_type}: '{web_event.query}' ({len(web_event.results)} results)")  # type: ignore[attr-defined]


async def demo_websearch_stack():
    """Demonstrate the complete websearch stack."""
    print("WebSearch Stack Demo\n")
    
    # Setup
    hub = DataHub(
        hub_id="demo_hub",
        persistence_file="outputs/demo_events.jsonl",
        enable_persistence=True,
    )
    print(f"✓ DataHub: {hub.hub_id} (persist: {hub.persistence_file})")
    
    api = WebSearchAPI()
    store = WebSearchStore(store_id="demo_websearch_store", api=api)
    
    # Register processors
    store.register_stream("injury_summary", InjurySummaryProcessor(), ["raw_web_search"])
    store.register_stream("power_ranking", PowerRankingProcessor(), ["raw_web_search"])
    store.register_stream("expert_prediction", ExpertPredictionProcessor(), ["raw_web_search"])
    
    # Connect store to DataHub
    hub.connect_store(store)
    print(f"✓ WebSearchStore: {store.store_id} (streams: {', '.join(store.list_registered_streams())})")
    
    # Agents
    agent1 = DemoAgent("Agent1")
    agent2 = DemoAgent("Agent2")
    hub.subscribe_agent(
        "Agent1",
        event_types=["raw_web_search", "injury_summary", "power_ranking", "expert_prediction"],
        callback=agent1.handle_event,
    )
    hub.subscribe_agent("Agent2", event_types=["raw_web_search"], callback=agent2.handle_event)
    print("✓ Agents subscribed: Agent1 (all events including raw), Agent2 (raw only)\n")
    
    # Perform searches
    print("Searching...")
    search_queries = [
        # (f"NBA betting odds for {game_info}", None, None),  # No intent - will use keyword matching
        (f"NBA injury updates for {game_info}", WebSearchIntent.INJURY_SUMMARY, {"time_range": "week"}),  # Explicit intent
        (f"NBA power rankings", WebSearchIntent.POWER_RANKING, {"time_range": "week"}),  # Explicit intent
        (f"NBA expert predictions for {team1} and {team2}", WebSearchIntent.EXPERT_PREDICTION, {"time_range": "week"}),  # Explicit intent
    ]
    
    for query, intent, search_params in search_queries:
        intent_str = intent.value if intent else None
        print(f"  • {query}" + (f" [intent: {intent_str}]" if intent else ""))
        await store.search(query, intent=intent, **search_params if search_params else {})
        await asyncio.sleep(0.3)
    
    # Results summary
    print(f"\nResults: Agent1={len(agent1.received_events)} events, Agent2={len(agent2.received_events)} events")
    
    # Summary of processed events (non-raw)
    processed_events = [e for e in agent1.received_events if e.event_type != "raw_web_search"]
    if processed_events:
        print(f"\nProcessed Events Summary ({len(processed_events)}):")
        for event in processed_events:
            event_type = event.event_type  # type: ignore[attr-defined]
            if event_type == "injury_summary":
                injury_event = event  # type: ignore[assignment]
                teams = list(injury_event.injured_players.keys()) if injury_event.injured_players else []  # type: ignore[attr-defined]
                print(f"  • Injury: {len(teams)} teams, {sum(len(p) for p in injury_event.injured_players.values())} players")  # type: ignore[attr-defined]
            elif event_type == "power_ranking":
                ranking_event = event  # type: ignore[assignment]
                sources = list(ranking_event.rankings.keys()) if ranking_event.rankings else []  # type: ignore[attr-defined]
                total_teams = sum(len(teams) for teams in ranking_event.rankings.values())  # type: ignore[attr-defined]
                print(f"  • Rankings: {len(sources)} sources, {total_teams} teams")
            elif event_type == "expert_prediction":
                prediction_event = event  # type: ignore[assignment]
                print(f"  • Predictions: {len(prediction_event.predictions)} predictions")  # type: ignore[attr-defined]
    
    # Persistence
    if hub.persistence_file.exists():
        with open(hub.persistence_file, "r") as f:
            lines = f.readlines()
        print(f"  Persisted: {len(lines)} events ({hub.persistence_file.stat().st_size} bytes)")
    
    # Replay with a new hub instance
    if hub.persistence_file.exists():
        print("\nReplaying events with new hub...")
        replay_hub = DataHub(
            hub_id="replay_hub",
            persistence_file=hub.persistence_file,
            enable_persistence=False,  # Don't persist during replay
        )
        await replay_hub.start_replay(str(hub.persistence_file))
        replay_agent_1 = DemoAgent("ReplayAgent1")
        replay_hub.subscribe_agent("ReplayAgent1", event_types=["raw_web_search"], callback=replay_agent_1.handle_event)
        replay_agent_2 = DemoAgent("ReplayAgent2")
        replay_hub.subscribe_agent("ReplayAgent2", event_types=["injury_summary", "power_ranking", "expert_prediction"], callback=replay_agent_2.handle_event)
        await replay_hub.replay_all()   
        print(f"  ReplayAgent1: {len(replay_agent_1.received_events)} events")
        print(f"  ReplayAgent2: {len(replay_agent_2.received_events)} events")
        replay_hub.stop_replay()
    
    print("\n✓ Demo complete")


if __name__ == "__main__":
    print("\n")
    asyncio.run(demo_websearch_stack())
