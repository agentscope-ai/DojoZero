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
        print(f"  [{self.agent_id}] Received event: {event.event_type}")
        if isinstance(event, WebSearchEvent):
            print(f"    Query: {event.query}")
            print(f"    Results: {len(event.results)}")
            for i, result in enumerate(event.results[:2], 1):  # Show first 2
                print(f"      {i}. {result.get('title', 'N/A')}")
                print(f"         {result.get('url', 'N/A')}")


async def demo_websearch_stack():
    """Demonstrate the complete websearch stack."""
    print("=" * 70)
    print("WEBSEARCH STACK DEMO")
    print("=" * 70)
    print()
    
    # Step 1: Create DataHub
    print("Step 1: Creating DataHub")
    print("-" * 70)
    hub = DataHub(
        hub_id="demo_hub",
        persistence_file="data/demo_events.jsonl",
        enable_persistence=True,
    )
    print(f"  Created DataHub: {hub.hub_id}")
    print(f"  Persistence file: {hub.persistence_file}")
    print()
    
    # Step 2: Create WebSearchAPI
    print("Step 2: Creating WebSearchAPI")
    print("-" * 70)
    try:
        api = WebSearchAPI(
            use_tavily=True,  # Use Tavily as the search engine
        )
        print(f"  Created WebSearchAPI")
        print(f"  Tavily enabled: {api.use_tavily}")
        print(f"  Tavily adapter: {'Available' if api.tavily_adapter else 'Not available'}")
        if not api.tavily_adapter:
            raise ValueError(
                "Tavily SDK not available. Please install with: pip install tavily-python\n"
                "And set TAVILY_API_KEY in your .env file."
            )
    except (ValueError, ImportError) as e:
        print(f"  Error: {e}")
        print("\n  To run this demo:")
        print("  1. Install Tavily SDK: pip install tavily-python")
        print("  2. Set TAVILY_API_KEY in your .env file")
        raise
    print()
    
    # Step 3: Create WebSearchStore
    print("Step 3: Creating WebSearchStore")
    print("-" * 70)
    store = WebSearchStore(
        store_id="demo_websearch_store",
        api=api,
        poll_interval_seconds=1.0,
    )
    print(f"  Created WebSearchStore: {store.store_id}")
    print(f"  Registered streams: {store.list_registered_streams()}")
    print()
    
    # Step 4: Connect Store to Hub
    print("Step 4: Connecting Store to DataHub")
    print("-" * 70)
    hub.connect_store(store)
    print("  Store connected to DataHub")
    print()
    
    # Step 5: Create and subscribe agents
    print("Step 5: Creating and subscribing agents")
    print("-" * 70)
    agent1 = DemoAgent("Agent1")
    agent2 = DemoAgent("Agent2")
    
    # Subscribe agents to web search events
    hub.subscribe_agent(
        "Agent1",
        event_types=["web_search", "raw_web_search"],
        callback=agent1.handle_event,
    )
    hub.subscribe_agent(
        "Agent2",
        event_types=["web_search"],  # Only cooked events
        callback=agent2.handle_event,
    )
    print("  Agent1 subscribed to: web_search, raw_web_search")
    print("  Agent2 subscribed to: web_search only")
    print()
    
    # Step 6: Perform searches
    print("Step 6: Performing searches")
    print("-" * 70)
    search_queries = [
        "Lakers vs Celtics betting odds",
        "NBA injury updates",
        "Polymarket prediction markets",
    ]
    
    for query in search_queries:
        print(f"\n  Searching: {query}")
        await store.search(query)
        # Give time for events to flow through the system
        await asyncio.sleep(0.3)  # Small delay for async processing
    
    print()
    
    # Step 7: Show results
    print("Step 7: Results summary")
    print("-" * 70)
    print(f"  Agent1 received {len(agent1.received_events)} events")
    print(f"  Agent2 received {len(agent2.received_events)} events")
    print()
    
    # Show event breakdown
    raw_count = sum(1 for e in agent1.received_events if isinstance(e, RawWebSearchEvent))
    cooked_count = sum(1 for e in agent1.received_events if isinstance(e, WebSearchEvent))
    print(f"  Agent1 breakdown:")
    print(f"    Raw events: {raw_count}")
    print(f"    Cooked events: {cooked_count}")
    print()
    
    cooked_count2 = sum(1 for e in agent2.received_events if isinstance(e, WebSearchEvent))
    print(f"  Agent2 breakdown:")
    print(f"    Cooked events: {cooked_count2}")
    print()
    
    # Step 8: Show persistence
    print("Step 8: Checking persistence")
    print("-" * 70)
    if hub.persistence_file.exists():
        with open(hub.persistence_file, "r") as f:
            lines = f.readlines()
        print(f"  Persisted {len(lines)} events to {hub.persistence_file}")
        print(f"  File size: {hub.persistence_file.stat().st_size} bytes")
    else:
        print("  No events persisted yet")
    print()
    
    # Step 9: Demonstrate replay
    print("Step 9: Demonstrating replay")
    print("-" * 70)
    if hub.persistence_file.exists():
        print("  Starting replay from file...")
        await hub.start_replay(str(hub.persistence_file))
        
        replay_agent = DemoAgent("ReplayAgent")
        hub.subscribe_agent(
            "ReplayAgent",
            event_types=["web_search"],
            callback=replay_agent.handle_event,
        )
        
        # Replay all events
        await hub.replay_all()
        
        print(f"  ReplayAgent received {len(replay_agent.received_events)} events during replay")
        hub.stop_replay()
    else:
        print("  No events to replay")
    print()
    
    print("=" * 70)
    print("DEMO COMPLETE")
    print("=" * 70)



if __name__ == "__main__":
    print("\n")
    asyncio.run(demo_websearch_stack())
