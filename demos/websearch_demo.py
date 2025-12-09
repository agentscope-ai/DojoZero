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
from agentx.data.websearch._events import RawWebSearchEvent, WebSearchEvent


class DemoAgent(AgentBase):
    """Simple demo agent that subscribes to web search events."""
    
    def __init__(self, agent_id: str):
        """Initialize demo agent."""
        self.agent_id = agent_id
        self.received_events = []
    
    def handle_event(self, event):
        """Handle received event."""
        self.received_events.append(event)
        if event.event_type == "web_search":  # type: ignore[attr-defined]
            # Type checker issue with decorator, but runtime works correctly
            web_event = event  # type: ignore[assignment]
            print(f"  [{self.agent_id}] {event.event_type}: '{web_event.query}' ({len(web_event.results)} results)")  # type: ignore[attr-defined]


async def demo_websearch_stack():
    """Demonstrate the complete websearch stack."""
    print("WebSearch Stack Demo\n")
    
    # Setup
    hub = DataHub(
        hub_id="demo_hub",
        persistence_file="data/demo_events.jsonl",
        enable_persistence=True,
    )
    print(f"✓ DataHub: {hub.hub_id} (persist: {hub.persistence_file})")
    
    try:
        api = WebSearchAPI(use_tavily=True)
        if not api.tavily_adapter:
            raise ValueError(
                "Tavily SDK not available. Install: pip install tavily-python\n"
                "Set TAVILY_API_KEY in .env file"
            )
        print("✓ WebSearchAPI: Tavily enabled")
    except (ValueError, ImportError) as e:
        print(f"✗ Error: {e}")
        raise
    
    store = WebSearchStore(store_id="demo_websearch_store", api=api, poll_interval_seconds=1.0)
    hub.connect_store(store)
    print(f"✓ WebSearchStore: {store.store_id} (streams: {', '.join(store.list_registered_streams())})")
    
    # Agents
    agent1 = DemoAgent("Agent1")
    agent2 = DemoAgent("Agent2")
    hub.subscribe_agent("Agent1", event_types=["web_search", "raw_web_search"], callback=agent1.handle_event)
    hub.subscribe_agent("Agent2", event_types=["web_search"], callback=agent2.handle_event)
    print("✓ Agents subscribed: Agent1 (all), Agent2 (cooked only)\n")
    
    # Perform searches
    print("Searching...")
    search_queries = [
        "Lakers vs Celtics betting odds",
        "NBA injury updates",
        "Polymarket prediction markets",
    ]
    
    for query in search_queries:
        print(f"  • {query}")
        await store.search(query)
        await asyncio.sleep(0.3)
    
    # Results
    print(f"\nResults:")
    raw_count = sum(1 for e in agent1.received_events if e.event_type == "raw_web_search")
    cooked_count = sum(1 for e in agent1.received_events if e.event_type == "web_search")
    cooked_count2 = sum(1 for e in agent2.received_events if e.event_type == "web_search")
    print(f"  Agent1: {len(agent1.received_events)} events ({raw_count} raw, {cooked_count} cooked)")
    print(f"  Agent2: {len(agent2.received_events)} events ({cooked_count2} cooked)")
    
    # Persistence
    if hub.persistence_file.exists():
        with open(hub.persistence_file, "r") as f:
            lines = f.readlines()
        print(f"  Persisted: {len(lines)} events ({hub.persistence_file.stat().st_size} bytes)")
    
    # Replay
    if hub.persistence_file.exists():
        print("\nReplaying events...")
        await hub.start_replay(str(hub.persistence_file))
        replay_agent = DemoAgent("ReplayAgent")
        hub.subscribe_agent("ReplayAgent", event_types=["web_search"], callback=replay_agent.handle_event)
        await hub.replay_all()
        print(f"  ReplayAgent: {len(replay_agent.received_events)} events")
        hub.stop_replay()
    
    print("\n✓ Demo complete")



if __name__ == "__main__":
    print("\n")
    asyncio.run(demo_websearch_stack())
