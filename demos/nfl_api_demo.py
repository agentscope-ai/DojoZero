#!/usr/bin/env python3
"""Demo script showcasing the ESPN API integration.

This script demonstrates the generic ESPN API that works with any sport/league:
1. Fetching scoreboards for different sports (NFL, NBA, Soccer, etc.)
2. Fetching detailed game summaries with boxscores
3. Fetching play-by-play data
4. Parsing API responses into typed events

Usage:
    # Run with default settings (NFL)
    uv run python demos/nfl_api_demo.py

    # Run with a different sport/league
    uv run python demos/nfl_api_demo.py --sport basketball --league nba
    uv run python demos/nfl_api_demo.py --sport soccer --league eng.1
    uv run python demos/nfl_api_demo.py --sport hockey --league nhl

    # Run with proxy
    DOJOZERO_PROXY_URL="http://proxy:8080" uv run python demos/nfl_api_demo.py

    # Specify a game event ID
    uv run python demos/nfl_api_demo.py --event-id 401671827
"""

import argparse
import asyncio
import logging
from datetime import datetime

from dojozero.data.espn import (
    ESPNExternalAPI,
    ESPNGameInitializeEvent,
    ESPNOddsUpdateEvent,
    ESPNPlayEvent,
    ESPNStore,
    get_proxy,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Common sport/league combinations for reference
SPORT_LEAGUES = {
    "nfl": ("football", "nfl"),
    "ncaaf": ("football", "college-football"),
    "nba": ("basketball", "nba"),
    "ncaab": ("basketball", "mens-college-basketball"),
    "wnba": ("basketball", "wnba"),
    "mlb": ("baseball", "mlb"),
    "nhl": ("hockey", "nhl"),
    "epl": ("soccer", "eng.1"),
    "mls": ("soccer", "usa.1"),
    "laliga": ("soccer", "esp.1"),
}


def print_separator(title: str) -> None:
    """Print a section separator."""
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


async def demo_scoreboard(api: ESPNExternalAPI) -> list[dict]:
    """Demonstrate fetching the scoreboard for any sport."""
    print_separator(f"{api.sport.upper()}/{api.league.upper()} Scoreboard")

    # Fetch current scoreboard
    data = await api.fetch("scoreboard")
    scoreboard = data.get("scoreboard", {})

    # Show league info
    leagues = scoreboard.get("leagues", [])
    if leagues:
        league = leagues[0]
        season = league.get("season", {})
        print(
            f"\nSeason: {season.get('year', 'N/A')} - {season.get('displayName', 'N/A')}"
        )
        season_type = season.get("type", {})
        print(f"Type: {season_type.get('name', 'N/A')}")

    # List all games
    events = scoreboard.get("events", [])
    print(f"\nFound {len(events)} games:\n")

    for event in events:
        event_id = event.get("id", "")
        short_name = event.get("shortName", "")

        # Get competition details
        competitions = event.get("competitions", [])
        if competitions:
            comp = competitions[0]
            status = comp.get("status", {}).get("type", {})
            status_desc = status.get("description", "Unknown")

            # Get scores
            competitors = comp.get("competitors", [])
            home_score = away_score = "0"
            for c in competitors:
                if c.get("homeAway") == "home":
                    home_score = c.get("score", "0")
                else:
                    away_score = c.get("score", "0")

            # Get odds if available
            odds_info = ""
            odds_list = comp.get("odds", [])
            if odds_list:
                odds = odds_list[0]
                spread = odds.get("spread", 0)
                ou = odds.get("overUnder", 0)
                if spread or ou:
                    odds_info = f" | Spread: {spread:+.1f}, O/U: {ou}"

            print(f"  [{event_id}] {short_name}")
            print(
                f"    Status: {status_desc} | Score: {away_score} - {home_score}{odds_info}"
            )
            print()

    return events


async def demo_game_summary(api: ESPNExternalAPI, event_id: str) -> None:
    """Demonstrate fetching game summary with boxscore."""
    print_separator(f"Game Summary: {event_id}")

    data = await api.fetch("summary", {"event_id": event_id})
    summary = data.get("summary", {})

    if not summary or not summary.get("header"):
        print(f"No summary data available for event {event_id}")
        return

    # Header info
    header = summary.get("header", {})
    competitions = header.get("competitions", [])
    if competitions:
        comp = competitions[0]
        competitors = comp.get("competitors", [])

        print("\nTeams:")
        for c in competitors:
            team = c.get("team", {})
            home_away = c.get("homeAway", "")
            score = c.get("score", "0")
            record = c.get("record", [])
            record_str = record[0].get("summary", "") if record else ""
            print(
                f"  [{home_away.upper():4}] {team.get('displayName', 'Unknown'):25} Score: {score:3} ({record_str})"
            )

        # Line scores (quarters/periods/innings)
        print("\n  Period Scores:")
        for c in competitors:
            team = c.get("team", {})
            abbrev = team.get("abbreviation", "???")
            linescores = c.get("linescores", [])
            scores = [str(ls.get("value", 0)) for ls in linescores]
            print(f"    {abbrev}: {' | '.join(scores) if scores else 'N/A'}")

    # Boxscore team stats
    boxscore = summary.get("boxscore", {})
    teams = boxscore.get("teams", [])
    if teams:
        print("\nTeam Stats:")
        for team_data in teams:
            team = team_data.get("team", {})
            stats = team_data.get("statistics", [])
            print(f"\n  {team.get('displayName', 'Unknown')}:")

            # Show key stats
            for stat in stats[:10]:  # First 10 stats
                name = stat.get("name", "")
                value = stat.get("displayValue", "")
                print(f"    {name}: {value}")

    # Drives summary (for football)
    drives = summary.get("drives", {})
    previous_drives = drives.get("previous", [])
    if previous_drives:
        print(f"\nDrives ({len(previous_drives)} total):")
        for drive in previous_drives[-5:]:  # Last 5 drives
            team = drive.get("team", {})
            result = drive.get("result", "")
            yards = drive.get("yards", 0)
            plays = drive.get("offensivePlays", 0)
            print(
                f"  {team.get('abbreviation', '???')}: {result} ({plays} plays, {yards} yards)"
            )


async def demo_plays(api: ESPNExternalAPI, event_id: str) -> None:
    """Demonstrate fetching play-by-play data."""
    print_separator(f"Play-by-Play: {event_id}")

    data = await api.fetch("plays", {"event_id": event_id, "limit": 20})
    plays_data = data.get("plays", {})

    items = plays_data.get("items", [])
    total = plays_data.get("count", 0)

    print(f"\nTotal plays: {total} (showing last {min(len(items), 10)})")
    print()

    for play in items[-10:]:  # Show last 10 plays
        play_type = play.get("type", {}).get("text", "Unknown")
        text = play.get("text", "")[:80]
        period = play.get("period", {}).get("number", 0)
        clock = play.get("clock", {}).get("displayValue", "")
        home_score = play.get("homeScore", 0)
        away_score = play.get("awayScore", 0)

        is_scoring = play.get("scoringPlay", False)
        scoring_marker = " *SCORE*" if is_scoring else ""

        print(f"  P{period} {clock:>5} | [{play_type:15}] {text}{scoring_marker}")
        print(f"           Score: {away_score} - {home_score}")
        print()


async def demo_store_parsing(sport: str, league: str, event_id: str | None) -> None:
    """Demonstrate parsing API responses into typed events using ESPNStore."""
    print_separator("Event Parsing Demo")

    # Create store for the sport
    store = ESPNStore(sport=sport, league=league, store_id="demo_store")

    # Fetch real data and parse it
    api = ESPNExternalAPI(sport=sport, league=league)
    try:
        # Fetch scoreboard
        scoreboard_data = await api.fetch("scoreboard")
        events = store._parse_api_response(scoreboard_data)

        print(f"\nParsed {len(events)} events from scoreboard:\n")
        for event in events[:10]:  # First 10
            print(f"  {event.__class__.__name__}:")
            if isinstance(event, ESPNGameInitializeEvent):
                print(f"    {event.away_team} @ {event.home_team}")
                print(f"    Event ID: {event.event_id}")
                print(f"    Sport: {event.sport}/{event.league}")
                print(f"    Venue: {event.venue}")
            elif isinstance(event, ESPNOddsUpdateEvent):
                print(f"    Provider: {event.provider}")
                print(f"    Spread: {event.spread:+.1f}, O/U: {event.over_under}")
                print(
                    f"    ML Home: {event.moneyline_home}, ML Away: {event.moneyline_away}"
                )
            print()

        # If we have an event_id, parse plays too
        if event_id:
            plays_data = await api.fetch("plays", {"event_id": event_id, "limit": 50})
            play_events = store._parse_api_response(plays_data)

            print(f"\nParsed {len(play_events)} events from plays:\n")
            for event in play_events[:5]:  # First 5
                if isinstance(event, ESPNPlayEvent):
                    print("  ESPNPlayEvent:")
                    print(f"    P{event.period} {event.clock} - {event.play_type}")
                    desc = (
                        event.description[:60] + "..."
                        if len(event.description) > 60
                        else event.description
                    )
                    print(f"    {desc}")
                    print(f"    Score: {event.away_score} - {event.home_score}")
                    print()
    finally:
        await api.close()


async def demo_multi_sport() -> None:
    """Demonstrate the ESPN API working with multiple sports."""
    print_separator("Multi-Sport Demo")

    sports_to_demo = [
        ("football", "nfl", "NFL"),
        ("basketball", "nba", "NBA"),
        ("hockey", "nhl", "NHL"),
        ("soccer", "eng.1", "Premier League"),
    ]

    for sport, league, display_name in sports_to_demo:
        api = ESPNExternalAPI(sport=sport, league=league)
        try:
            data = await api.fetch("scoreboard")
            scoreboard = data.get("scoreboard", {})
            events = scoreboard.get("events", [])
            print(f"\n  {display_name} ({sport}/{league}): {len(events)} games found")

            # Show first game if available
            if events:
                first_game = events[0]
                short_name = first_game.get("shortName", "Unknown")
                competitions = first_game.get("competitions", [])
                if competitions:
                    status = (
                        competitions[0]
                        .get("status", {})
                        .get("type", {})
                        .get("description", "Unknown")
                    )
                    print(f"    First game: {short_name} ({status})")
        except Exception as e:
            print(f"\n  {display_name}: Error - {e}")
        finally:
            await api.close()


async def main():
    """Main demo entry point."""
    parser = argparse.ArgumentParser(description="ESPN API Demo")
    parser.add_argument(
        "--sport",
        type=str,
        default="football",
        help="Sport type (e.g., football, basketball, soccer, hockey)",
    )
    parser.add_argument(
        "--league",
        type=str,
        default="nfl",
        help="League (e.g., nfl, nba, eng.1, nhl)",
    )
    parser.add_argument(
        "--event-id",
        type=str,
        default=None,
        help="Specific game event ID to fetch details for",
    )
    parser.add_argument(
        "--skip-scoreboard",
        action="store_true",
        help="Skip scoreboard demo",
    )
    parser.add_argument(
        "--skip-parsing",
        action="store_true",
        help="Skip event parsing demo",
    )
    parser.add_argument(
        "--multi-sport",
        action="store_true",
        help="Run multi-sport demo showing ESPN API works with various sports",
    )
    args = parser.parse_args()

    # Check proxy configuration
    proxy = get_proxy()
    if proxy:
        print(f"Using proxy: {proxy}")
    else:
        print("No proxy configured (set DOJOZERO_PROXY_URL to use one)")

    print(f"Demo started at {datetime.now().isoformat()}")

    # Multi-sport demo
    if args.multi_sport:
        await demo_multi_sport()
        print_separator("Demo Complete")
        return

    # Single sport demo
    sport = args.sport
    league = args.league
    print(f"Sport: {sport}/{league}")

    api = ESPNExternalAPI(sport=sport, league=league)
    try:
        # Demo 1: Scoreboard
        events = []
        if not args.skip_scoreboard:
            events = await demo_scoreboard(api)

        # Get an event_id for detailed demos
        event_id = args.event_id
        if not event_id and events:
            # Use first available game
            event_id = events[0].get("id")
            print(f"\nUsing event ID: {event_id} for detailed demos")

        # Demo 2: Game Summary
        if event_id:
            await demo_game_summary(api, event_id)

            # Demo 3: Play-by-Play
            await demo_plays(api, event_id)

        # Demo 4: Store Parsing
        if not args.skip_parsing:
            await demo_store_parsing(sport, league, event_id)

    finally:
        await api.close()

    print_separator("Demo Complete")


if __name__ == "__main__":
    asyncio.run(main())
