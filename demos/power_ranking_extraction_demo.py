"""Test power ranking extraction with real data.

Uses cached search results from demos/data/ and runs them through
the full PowerRankingEvent.from_web_search() pipeline (search → LLM → typed event).
"""

import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

from dojozero.data.websearch._api import WebSearchAPI
from dojozero.data.websearch._context import GameContext
from dojozero.data.websearch._events import PowerRankingEvent


async def demo_power_ranking_extraction():
    """Demo power ranking extraction using from_web_search() lifecycle."""
    load_dotenv()

    # Load cached search results
    json_file = Path("demos/data/power_ranking_search_event.json")
    with open(json_file, "r") as f:
        raw_event_data = json.load(f)

    cached_results = raw_event_data.get("raw_results", [])
    print(f"Loaded {len(cached_results)} cached search results")
    print()

    # Create a WebSearchAPI backed by cached data
    def cached_search(**kwargs):
        return {"results": cached_results, "total_results": len(cached_results)}

    api = WebSearchAPI(use_tavily=False, custom_search_fn=cached_search)
    context = GameContext(sport="nba")

    # Run full lifecycle: search → LLM → typed event
    print("Running PowerRankingEvent.from_web_search() ...")
    print()
    ranking_event = await PowerRankingEvent.from_web_search(
        api=api, context=context, model="qwen-plus"
    )

    if ranking_event is None:
        print("Error: from_web_search() returned None")
        return

    print(f"Extracted rankings from {len(ranking_event.rankings)} sources:")
    for source, teams in ranking_event.rankings.items():
        print(f"\n  {source} ({len(teams)} teams):")
        for team in teams[:5]:
            rank = team.get("rank", "?")
            name = team.get("team", "?")
            record = team.get("record", "")
            notes = team.get("notes", "")
            print(f"    #{rank} {name} ({record}) - {notes}")
        if len(teams) > 5:
            print(f"    ... and {len(teams) - 5} more")


if __name__ == "__main__":
    asyncio.run(demo_power_ranking_extraction())
