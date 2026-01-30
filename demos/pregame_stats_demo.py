"""Demo script for fetching pre-game stats from ESPN API.

Fetches team schedule, statistics, standings, and roster data for a given
NBA game and assembles a PreGameStatsEvent.

Usage:
    uv run python demos/pregame_stats_demo.py
    uv run python demos/pregame_stats_demo.py --game-id 401810490
    uv run python demos/pregame_stats_demo.py --game-id 401810490 --json
"""

import argparse
import asyncio
import json
import logging

from dojozero.data.espn._api import ESPNExternalAPI
from dojozero.data.espn import fetch_pregame_stats
from dojozero.data.nba._utils import get_game_info_by_id_async

logger = logging.getLogger(__name__)

# Default: pick a recent NBA game ID (update as needed)
DEFAULT_GAME_ID = "401810541"


async def demo_fetch_pregame_stats(game_id: str, output_json: bool = False) -> None:
    """Fetch and display pregame stats for an NBA game."""
    print("=" * 80)
    print("PREGAME STATS DEMO")
    print("=" * 80)
    print()

    # Step 1: Get game info from ESPN
    print(f"[1/3] Fetching game info for ESPN game ID: {game_id}")
    game_info = await get_game_info_by_id_async(game_id)

    if not game_info:
        print(f"  ERROR: Could not find game info for game_id={game_id}")
        return

    home = game_info.home_team
    away = game_info.away_team
    print(f"  Game: {away.name} @ {home.name}")
    print(f"  Home team ID: {home.team_id} ({home.tricode})")
    print(f"  Away team ID: {away.team_id} ({away.tricode})")
    print(f"  Season: {game_info.season_year} ({game_info.season_type})")
    print(f"  Status: {game_info.status_text}")
    print()

    # Step 2: Fetch pregame stats
    print("[2/3] Fetching pregame stats from ESPN API (7 parallel calls)...")
    api = ESPNExternalAPI(sport="basketball", league="nba")

    game_date = game_info.get_game_date_us() or "2025-01-15"

    try:
        event = await fetch_pregame_stats(
            api,
            home_team_id=home.team_id,
            away_team_id=away.team_id,
            game_id=game_id,
            game_date=game_date,
            sport="nba",
            season_year=game_info.season_year,
            season_type=game_info.season_type or "regular",
            home_team_name=home.name,
            away_team_name=away.name,
        )
    finally:
        await api.close()

    # Step 3: Display results
    print()
    print("[3/3] Results")
    print("-" * 80)

    if output_json:
        print(json.dumps(event.to_dict(), indent=2, default=str))
        return

    # Season series
    print()
    _section("Season Series (H2H)")
    if event.season_series:
        ss = event.season_series
        print(f"  Total games: {ss.total_games}")
        print(
            f"  {home.tricode} wins: {ss.home_wins}, {away.tricode} wins: {ss.away_wins}"
        )
        for g in ss.games[:5]:
            print(
                f"    {g.get('date', '?')}: {g.get('home_score', '?')}-{g.get('away_score', '?')} ({g.get('winner', '?')})"
            )
    else:
        print("  (no data)")

    # Recent form
    print()
    _section("Recent Form")
    for label, form in [
        (home.tricode, event.home_recent_form),
        (away.tricode, event.away_recent_form),
    ]:
        if form:
            print(
                f"  {label}: {form.wins}-{form.losses} (last {form.last_n}), streak: {form.streak}"
            )
            print(
                f"    Avg scored: {form.avg_points_scored}, Avg allowed: {form.avg_points_allowed}"
            )
        else:
            print(f"  {label}: (no data)")

    # Schedule density
    print()
    _section("Schedule Density")
    for label, sched in [
        (home.tricode, event.home_schedule),
        (away.tricode, event.away_schedule),
    ]:
        if sched:
            b2b = "YES" if sched.is_back_to_back else "no"
            print(
                f"  {label}: {sched.days_rest} days rest, B2B: {b2b}, "
                f"games last 7d: {sched.games_last_7_days}, last 14d: {sched.games_last_14_days}"
            )
        else:
            print(f"  {label}: (no data)")

    # Team stats
    print()
    _section("Team Season Stats")
    for label, ts in [
        (home.tricode, event.home_team_stats),
        (away.tricode, event.away_team_stats),
    ]:
        if ts:
            # Show a few key stats
            key_stats = [
                "avgPoints",
                "avgRebounds",
                "avgAssists",
                "fieldGoalPct",
                "threePointFieldGoalPct",
            ]
            stat_strs = []
            for k in key_stats:
                v = ts.stats.get(k)
                r = ts.rank.get(k)
                if v is not None:
                    s = f"{k}={v:.1f}"
                    if r:
                        s += f" (#{r})"
                    stat_strs.append(s)
            print(
                f"  {label}: {', '.join(stat_strs) if stat_strs else '(no key stats)'}"
            )
            print(f"    Total stat keys: {len(ts.stats)}")
        else:
            print(f"  {label}: (no data)")

    # Home/away splits
    print()
    _section("Home/Away Splits")
    for label, sp in [
        (home.tricode, event.home_splits),
        (away.tricode, event.away_splits),
    ]:
        if sp:
            print(f"  {label}: Home {sp.home_record}, Away {sp.away_record}")
            for loc in ["home", "away"]:
                stats = sp.home_stats if loc == "home" else sp.away_stats
                if stats:
                    scored = stats.get("avg_points_scored", "?")
                    allowed = stats.get("avg_points_allowed", "?")
                    print(
                        f"    {loc.title()}: avg scored {scored}, avg allowed {allowed}"
                    )
        else:
            print(f"  {label}: (no data)")

    # Player stats
    print()
    _section("Key Players")
    for label, ps in [
        (home.tricode, event.home_players),
        (away.tricode, event.away_players),
    ]:
        if ps and ps.players:
            print(f"  {label}: {len(ps.players)} players (sorted by PTS+AST+REB)")
            for p in ps.players[:5]:
                name = p.get("name", "?")
                pos = p.get("position", "?")
                jersey = p.get("jersey", "")
                ppg = p.get("ppg", 0)
                apg = p.get("apg", 0)
                rpg = p.get("rpg", 0)
                print(f"    #{jersey} {name} ({pos}) — {ppg} PPG, {apg} APG, {rpg} RPG")
        else:
            print(f"  {label}: (no data)")

    # Standings
    print()
    _section("Standings")
    for label, st in [
        (home.tricode, event.home_standings),
        (away.tricode, event.away_standings),
    ]:
        if st:
            print(
                f"  {label}: {st.overall_record}, "
                f"{st.conference} #{st.conference_rank}"
                + (f", {st.division} #{st.division_rank}" if st.division else "")
                + (f", GB: {st.games_back}" if st.games_back else "")
            )
        else:
            print(f"  {label}: (no data)")

    print()
    print("=" * 80)
    print("DEMO COMPLETE")
    print("=" * 80)


def _section(title: str) -> None:
    print(f"--- {title} ---")


def main():
    parser = argparse.ArgumentParser(description="Fetch pregame stats from ESPN API")
    parser.add_argument(
        "--game-id",
        default=DEFAULT_GAME_ID,
        help=f"ESPN game/event ID (default: {DEFAULT_GAME_ID})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted text",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    asyncio.run(demo_fetch_pregame_stats(args.game_id, output_json=args.json))


if __name__ == "__main__":
    main()
