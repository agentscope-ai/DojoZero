"""Test power ranking extraction with real data."""

import asyncio
import json
from pathlib import Path

from dojozero.data._models import DataEventFactory
from dojozero.data.websearch._events import RawWebSearchEvent, PowerRankingEvent
from dojozero.data.websearch._processors import PowerRankingProcessor


async def demo_power_ranking_extraction():
    """Demo power ranking extraction."""
    # Load and deserialize event
    json_file = Path("demos/data/power_ranking_search_event.json")
    with open(json_file, "r") as f:
        raw_event_data = json.load(f)

    event = DataEventFactory.from_dict(raw_event_data)
    if not isinstance(event, RawWebSearchEvent):
        print("Error: Failed to deserialize event or wrong event type")
        return

    print(f"Query: {event.query}")
    print(f"Results: {len(event.results)}")
    print()

    # Process event
    processor = PowerRankingProcessor()
    if not processor.should_process(event):
        print("Processor skipped this event")
        return

    result = await processor.process(event)
    if not isinstance(result, PowerRankingEvent):
        print("No rankings extracted")
        return

    print(f"Extracted Rankings ({len(result.rankings)} sources):\n")

    for source, teams in result.rankings.items():
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
