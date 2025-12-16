"""Test power ranking extraction with real data."""

import asyncio
import json
from pathlib import Path
from typing import cast

from agentx.data._models import DataEventFactory
from agentx.data.websearch._events import RawWebSearchEvent
from agentx.data.websearch._processors import PowerRankingProcessor


async def demo_power_ranking_extraction():
    """Demo power ranking extraction."""
    # Load and deserialize event
    json_file = Path("demos/data/power_ranking_search_event.json")
    with open(json_file, "r") as f:
        raw_event_data = json.load(f)
    
    event = DataEventFactory.from_dict(raw_event_data)
    if event is None:
        print("Error: Failed to deserialize event")
        return
    
    raw_event = cast("RawWebSearchEvent", event)  # type: ignore[arg-type]
    print(f"Query: {raw_event.query}")  # type: ignore[attr-defined]
    print(f"Results: {len(raw_event.results)}")  # type: ignore[attr-defined]
    print()
    
    # Process event
    processor = PowerRankingProcessor()
    if not processor.should_process(raw_event):
        print("Processor skipped this event")
        return
    
    result = await processor.process([raw_event])
    if result is None or not hasattr(result, "rankings"):
        print("No rankings extracted")
        return
    
    rankings = result.rankings  # type: ignore[attr-defined]
    print(f"Extracted Rankings ({len(rankings)} sources):\n")
    
    for source, teams in rankings.items():
        print(f"{source}: {len(teams)} teams")
        for team_data in teams[:3]:
            rank = team_data.get("rank", "?")
            team = team_data.get("team", "?")
            record = team_data.get("record", "")
            print(f"  #{rank}: {team} {record}")
        if len(teams) > 3:
            print(f"  ... and {len(teams) - 3} more")
        print()


if __name__ == "__main__":
    asyncio.run(demo_power_ranking_extraction())
