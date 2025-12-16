# Query NBA scoreboard using ScoreboardV3 endpoint
from __future__ import annotations

from datetime import timezone, datetime, timedelta
from typing import Any
from dateutil import parser
from nba_api.live.nba.endpoints import playbyplay
from nba_api.stats.static import players
from nba_api.stats.static import teams
from nba_api.stats.endpoints import scoreboardv3

# Try to load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, skip .env loading

# Import proxy utilities from agentx
from agentx.data.nba._utils import with_proxy, get_game_info_by_id
from agentx.data.nba._events import (
    GameUpdateEvent,
)
from agentx.data.nba._store import NBAStore
from agentx.data.nba._api import NBAExternalAPI


game_id = "0022501226"

def get_current_games():
    """
    Get all current games from the NBA live scoreboard (today's games).
    
    Returns:
        list[dict]: List of game dictionaries with the following structure:
        {
            'gameId': str,
            'gameStatus': int,
            'gameStatusText': str,
            'period': int,
            'gameClock': str,
            'gameTimeUTC': str,
            'gameTimeLTZ': datetime,  # Local timezone
            'homeTeam': {
                'teamId': int,
                'teamName': str,
                'teamCity': str,
                'teamTricode': str,
                'score': int,
                'wins': int,
                'losses': int
            },
            'awayTeam': {...},
            'gameLeaders': {...}  # May not be present
        }
    """
    return get_games_for_date(datetime.now(), print_games=False)


@with_proxy
def get_games_for_date(game_date: datetime | str, print_games: bool = False):
    """
    Get games for a specific date using ScoreboardV3 endpoint.
    
    Args:
        game_date: Date as datetime object or string in 'YYYY-MM-DD' format
        print_games: Whether to print game information (default: False)
    
    Returns:
        list[dict]: List of game dictionaries, or empty list if no games found
    """
    try:
        
        # Parse the requested date
        if isinstance(game_date, datetime):
            requested_date = game_date.date()
        elif isinstance(game_date, str):
            try:
                parsed_date = parser.parse(game_date).replace(hour=0, minute=0, second=0, microsecond=0)
                requested_date = parsed_date.date()
            except:
                requested_date = None
        else:
            requested_date = None
        
        if not requested_date:
            if print_games:
                print(f"Error: Could not parse date: {game_date}")
            return []
        
        date_str = requested_date.strftime('%Y-%m-%d')
        
        # Use ScoreboardV3 for all dates
        # Proxy is handled by @with_proxy decorator
        from agentx.data.nba._utils import get_proxy
        proxy = get_proxy()
        board = scoreboardv3.ScoreboardV3(game_date=date_str, proxy=proxy) if proxy else scoreboardv3.ScoreboardV3(game_date=date_str)
        games_data = board.get_dict()
        
        if not games_data or 'scoreboard' not in games_data:
            return []
        
        scoreboard_data = games_data['scoreboard']
        games_list = scoreboard_data.get('games', [])
        
        # Convert ScoreboardV3 format to our standard format
        games = []
        for game_data in games_list:
            game = {
                'gameId': game_data.get('gameId', ''),
                'gameStatus': game_data.get('gameStatus', 0),
                'gameStatusText': game_data.get('gameStatusText', 'Unknown'),
                'period': game_data.get('period', 0),
                'gameClock': game_data.get('gameClock', ''),
                'gameTimeUTC': game_data.get('gameTimeUTC', ''),
                'homeTeam': {
                    'teamId': game_data.get('homeTeam', {}).get('teamId', 0),
                    'teamName': game_data.get('homeTeam', {}).get('teamName', ''),
                    'teamCity': game_data.get('homeTeam', {}).get('teamCity', ''),
                    'teamTricode': game_data.get('homeTeam', {}).get('teamTricode', ''),
                    'score': game_data.get('homeTeam', {}).get('score', 0),
                    'wins': game_data.get('homeTeam', {}).get('wins', 0),
                    'losses': game_data.get('homeTeam', {}).get('losses', 0)
                },
                'awayTeam': {
                    'teamId': game_data.get('awayTeam', {}).get('teamId', 0),
                    'teamName': game_data.get('awayTeam', {}).get('teamName', ''),
                    'teamCity': game_data.get('awayTeam', {}).get('teamCity', ''),
                    'teamTricode': game_data.get('awayTeam', {}).get('teamTricode', ''),
                    'score': game_data.get('awayTeam', {}).get('score', 0),
                    'wins': game_data.get('awayTeam', {}).get('wins', 0),
                    'losses': game_data.get('awayTeam', {}).get('losses', 0)
                }
            }
            
            # Parse game time
            if game.get('gameTimeUTC'):
                try:
                    game_time_utc = parser.parse(game['gameTimeUTC'])
                    game_time_ltz = game_time_utc.replace(tzinfo=timezone.utc).astimezone(tz=None)
                    game['gameTimeLTZ'] = game_time_ltz
                except Exception as e:
                    game['gameTimeLTZ'] = None
            
            games.append(game)
        
        if print_games:
            print(f"Date: {date_str}")
            print(f"Found {len(games)} game(s)\n")
            for game in games:
                time_str = game['gameTimeLTZ'].strftime("%Y-%m-%d %H:%M:%S %Z") if game.get('gameTimeLTZ') else 'N/A'
                print(f"{game['gameId']}: {game['awayTeam']['teamName']} vs. {game['homeTeam']['teamName']} @ {time_str} [{game['gameStatusText']}]")
        
        return games
    
    except Exception as e:
        print(f"Error fetching games for date {game_date}: {e}")
        import traceback
        traceback.print_exc()
        return []


def is_game_started_or_finished(game: dict) -> bool:
    """
    Check if a game has started or finished using ScoreboardV3 data.
    
    ScoreboardV3 GAME_STATUS_ID values:
    - 1 = Not Started
    - 2 = In Progress
    - 3 = Finished
    
    Args:
        game: Game dictionary from ScoreboardV3 API
    
    Returns:
        bool: True if game has started or finished, False otherwise
    """
    game_status_id = game.get('gameStatus', 0)
    game_status_text = game.get('gameStatusText', '').lower()
    period = game.get('period', 0)
    
    # Game is finished if status is 3
    if game_status_id == 3:
        return True
    
    # Game has started if status is 2 (in progress)
    if game_status_id == 2:
        return True
    
    # Game has started if period > 0 (game is in progress)
    if period > 0:
        return True
    
    # Check status text for finished indicators
    if game_status_text in ['finished', 'final', 'final/ot']:
        return True
    
    # Status 1 means not started
    if game_status_id == 1:
        return False
    
    # If status text contains time like "7:00 pm ET", it hasn't started
    if any(x in game_status_text for x in ['pm', 'am', 'et', 'pt', 'ct']):
        return False
    
    return False


def get_most_recent_finished_games(max_days_back: int = 7, print_games: bool = True):
    """
    Get games from the most recent day that has finished games.
    
    Args:
        max_days_back: Maximum number of days to look back (default: 7)
        print_games: Whether to print game information (default: True)
    
    Returns:
        tuple[list[dict], datetime]: Tuple of (finished games list, date of games), or (None, None) if not found
    """
    # Start from yesterday (most recent completed day)
    current_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    for days_back in range(1, max_days_back + 1):
        check_date = current_date - timedelta(days=days_back)
        date_str = check_date.strftime('%Y-%m-%d')
        
        if print_games:
            print(f"Checking date: {date_str}...")
        
        games = get_games_for_date(check_date, print_games=False)
        
        if games:
            # Filter for finished games (gameStatus == 3 typically means finished)
            finished_games = [
                game for game in games 
                if game.get('gameStatus') == 3 or 
                   game.get('gameStatusText', '').lower() in ['finished', 'final']
            ]
            
            if finished_games:
                if print_games:
                    print(f"\n{'='*80}")
                    print(f"Found {len(finished_games)} finished game(s) on {date_str}")
                    print(f"{'='*80}")
                    for game in finished_games:
                        time_str = game['gameTimeLTZ'].strftime("%Y-%m-%d %H:%M:%S %Z") if game.get('gameTimeLTZ') else 'N/A'
                        score = f"{game['awayTeam']['score']} - {game['homeTeam']['score']}"
                        print(f"{game['gameId']}: {game['awayTeam']['teamName']} @ {game['homeTeam']['teamName']} | {score} | {time_str}")
                    print()
                
                return finished_games, check_date
        
        if print_games and not games:
            print(f"  No games found for {date_str}")
    
    if print_games:
        print(f"\nNo finished games found in the last {max_days_back} days.")
    
    return None, None


@with_proxy
def get_play_by_play(game_id: str, include_player_names: bool = True):
    """
    Get real-time play-by-play data for a specific game.
    
    Args:
        game_id (str): The NBA game ID (e.g., '0022500290')
        include_player_names (bool): Whether to include player full names (default: True)
    
    Returns:
        dict: Play-by-play data with the following structure:
        {
            'game': {
                'gameId': str,
                'actions': [
                    {
                        'actionNumber': int,
                        'period': int,
                        'clock': str,
                        'actionType': str,
                        'personId': int,
                        'playerName': str,  # Added if include_player_names=True
                        'teamTricode': str,
                        'scoreHome': int,
                        'scoreAway': int,
                        'description': str,
                        # ... other fields
                    }
                ]
            }
        }
        Returns None if game_id is invalid or game not found.
    """
    if not game_id:
        print("Error: game_id is required")
        return None
    
    try:
        # Proxy is handled by @with_proxy decorator
        from agentx.data.nba._utils import get_proxy
        proxy = get_proxy()
        pbp = playbyplay.PlayByPlay(game_id, proxy=proxy) if proxy else playbyplay.PlayByPlay(game_id)
        pbp_dict = pbp.get_dict()
        
        if 'game' not in pbp_dict:
            print(f"Error: No game data found for game_id: {game_id}")
            return None
        
        game_data = pbp_dict['game']
        actions = game_data.get('actions', [])
        
        # Enrich actions with player names if requested
        if include_player_names:
            for action in actions:
                person_id = action.get('personId')
                if person_id:
                    player = players.find_player_by_id(person_id)
                    if player is not None:
                        action['playerName'] = player['full_name']
                    else:
                        action['playerName'] = None
        
        return {
            'gameId': game_id,
            'game': game_data,
            'actions': actions,
            'total_actions': len(actions)
        }
    
    except Exception as e:
        print(f"Error fetching play-by-play for game {game_id}: {e}")
        return None


def create_game_update_event_from_scoreboard(game_data: dict) -> Any:
    """
    Create a GameUpdateEvent from ScoreboardV3 game data.
    
    This mimics the logic in NBAStore._parse_api_response().
    
    Args:
        game_data: Game dictionary from ScoreboardV3 API
        
    Returns:
        GameUpdateEvent instance
    """
    from datetime import datetime, timezone
    
    timestamp = datetime.now(timezone.utc)
    game_id = game_data.get("gameId", "")
    current_status = game_data.get("gameStatus", 0)
    
    # Extract home and away team data
    home_team_data = game_data.get("homeTeam", {})
    away_team_data = game_data.get("awayTeam", {})
    
    # Extract game leaders if available
    game_leaders_data = game_data.get("gameLeaders", {})
    game_leaders = {}
    if game_leaders_data:
        # Extract home and away team leaders
        home_leaders = game_leaders_data.get("homeLeaders", {})
        away_leaders = game_leaders_data.get("awayLeaders", {})
        game_leaders = {
            "home": {
                "points": {
                    "personId": home_leaders.get("points", {}).get("personId", 0),
                    "name": home_leaders.get("points", {}).get("name", ""),
                    "playerSlug": home_leaders.get("points", {}).get("playerSlug", ""),
                    "jerseyNum": home_leaders.get("points", {}).get("jerseyNum", ""),
                    "position": home_leaders.get("points", {}).get("position", ""),
                    "teamTricode": home_leaders.get("points", {}).get("teamTricode", ""),
                    "points": home_leaders.get("points", {}).get("value", 0),
                },
                "rebounds": {
                    "personId": home_leaders.get("rebounds", {}).get("personId", 0),
                    "name": home_leaders.get("rebounds", {}).get("name", ""),
                    "playerSlug": home_leaders.get("rebounds", {}).get("playerSlug", ""),
                    "jerseyNum": home_leaders.get("rebounds", {}).get("jerseyNum", ""),
                    "position": home_leaders.get("rebounds", {}).get("position", ""),
                    "teamTricode": home_leaders.get("rebounds", {}).get("teamTricode", ""),
                    "rebounds": home_leaders.get("rebounds", {}).get("value", 0),
                },
                "assists": {
                    "personId": home_leaders.get("assists", {}).get("personId", 0),
                    "name": home_leaders.get("assists", {}).get("name", ""),
                    "playerSlug": home_leaders.get("assists", {}).get("playerSlug", ""),
                    "jerseyNum": home_leaders.get("assists", {}).get("jerseyNum", ""),
                    "position": home_leaders.get("assists", {}).get("position", ""),
                    "teamTricode": home_leaders.get("assists", {}).get("teamTricode", ""),
                    "assists": home_leaders.get("assists", {}).get("value", 0),
                },
            },
            "away": {
                "points": {
                    "personId": away_leaders.get("points", {}).get("personId", 0),
                    "name": away_leaders.get("points", {}).get("name", ""),
                    "playerSlug": away_leaders.get("points", {}).get("playerSlug", ""),
                    "jerseyNum": away_leaders.get("points", {}).get("jerseyNum", ""),
                    "position": away_leaders.get("points", {}).get("position", ""),
                    "teamTricode": away_leaders.get("points", {}).get("teamTricode", ""),
                    "points": away_leaders.get("points", {}).get("value", 0),
                },
                "rebounds": {
                    "personId": away_leaders.get("rebounds", {}).get("personId", 0),
                    "name": away_leaders.get("rebounds", {}).get("name", ""),
                    "playerSlug": away_leaders.get("rebounds", {}).get("playerSlug", ""),
                    "jerseyNum": away_leaders.get("rebounds", {}).get("jerseyNum", ""),
                    "position": away_leaders.get("rebounds", {}).get("position", ""),
                    "teamTricode": away_leaders.get("rebounds", {}).get("teamTricode", ""),
                    "rebounds": away_leaders.get("rebounds", {}).get("value", 0),
                },
                "assists": {
                    "personId": away_leaders.get("assists", {}).get("personId", 0),
                    "name": away_leaders.get("assists", {}).get("name", ""),
                    "playerSlug": away_leaders.get("assists", {}).get("playerSlug", ""),
                    "jerseyNum": away_leaders.get("assists", {}).get("jerseyNum", ""),
                    "position": away_leaders.get("assists", {}).get("position", ""),
                    "teamTricode": away_leaders.get("assists", {}).get("teamTricode", ""),
                    "assists": away_leaders.get("assists", {}).get("value", 0),
                },
            },
        }
    
    return GameUpdateEvent(
        timestamp=timestamp,
        event_id=game_id,  # Use game_id as event_id
        game_id=game_id,
        game_status=current_status,
        game_status_text=game_data.get("gameStatusText", ""),
        period=game_data.get("period", 0),
        game_clock=game_data.get("gameClock", ""),
        game_time_utc=game_data.get("gameTimeUTC", ""),
        home_team={
            "teamId": home_team_data.get("teamId", 0),
            "teamName": home_team_data.get("teamName", ""),
            "teamCity": home_team_data.get("teamCity", ""),
            "teamTricode": home_team_data.get("teamTricode", ""),
            "score": home_team_data.get("score", 0),
            "wins": home_team_data.get("wins", 0),
            "losses": home_team_data.get("losses", 0),
            "seed": home_team_data.get("seed", 0),
            "timeoutsRemaining": home_team_data.get("timeoutsRemaining", 0),
            "inBonus": home_team_data.get("inBonus", False),
            "periods": home_team_data.get("periods", []),  # Quarter-by-quarter scores
        },
        away_team={
            "teamId": away_team_data.get("teamId", 0),
            "teamName": away_team_data.get("teamName", ""),
            "teamCity": away_team_data.get("teamCity", ""),
            "teamTricode": away_team_data.get("teamTricode", ""),
            "score": away_team_data.get("score", 0),
            "wins": away_team_data.get("wins", 0),
            "losses": away_team_data.get("losses", 0),
            "seed": away_team_data.get("seed", 0),
            "timeoutsRemaining": away_team_data.get("timeoutsRemaining", 0),
            "inBonus": away_team_data.get("inBonus", False),
            "periods": away_team_data.get("periods", []),  # Quarter-by-quarter scores
        },
        game_leaders=game_leaders,
    )


@with_proxy
def test_game_update_events(game_id: str):
    """
    Test creating GameUpdateEvent from ScoreboardV3 data.
    
    Args:
        game_id: NBA game ID to test
    """
    print(f"Fetching ScoreboardV3 data for game_id: {game_id}")
    
    # Get games for today (or use game_id to find the specific game)
    games = get_games_for_date(datetime.now(), print_games=False)
    
    # Find the specific game
    target_game = None
    for game in games:
        if game.get('gameId') == game_id:
            target_game = game
            break
    
    if not target_game:
        print(f"✗ Game {game_id} not found in today's games")
        print("  Trying to fetch by date from game info...")
        game_info = get_game_info_by_id(game_id)
        if game_info:
            game_date = game_info.get('game_date')
            if game_date:
                games = get_games_for_date(game_date, print_games=False)
                for game in games:
                    if game.get('gameId') == game_id:
                        target_game = game
                        break
    
    if not target_game:
        print(f"✗ Could not find game {game_id}")
        return
    
    print(f"✓ Found game: {target_game['awayTeam']['teamName']} @ {target_game['homeTeam']['teamName']}")
    print()
    
    # Create GameUpdateEvent
    print("Creating GameUpdateEvent from scoreboard data...")
    game_update_event = create_game_update_event_from_scoreboard(target_game)
    
    print(f"✓ Created GameUpdateEvent:")
    print(f"  Event Type: {game_update_event.event_type}")
    print(f"  Event ID: {game_update_event.event_id}")
    print(f"  Game ID: {game_update_event.game_id}")
    print(f"  Game Status: {game_update_event.game_status} ({game_update_event.game_status_text})")
    print(f"  Period: {game_update_event.period}")
    print(f"  Game Clock: {game_update_event.game_clock}")
    print(f"  Game Time UTC: {game_update_event.game_time_utc}")
    print()
    print(f"  Away Team: {game_update_event.away_team['teamCity']} {game_update_event.away_team['teamName']} ({game_update_event.away_team['teamTricode']})")
    print(f"    Score: {game_update_event.away_team['score']}")
    print(f"    Record: {game_update_event.away_team['wins']}-{game_update_event.away_team['losses']}")
    print()
    print(f"  Home Team: {game_update_event.home_team['teamCity']} {game_update_event.home_team['teamName']} ({game_update_event.home_team['teamTricode']})")
    print(f"    Score: {game_update_event.home_team['score']}")
    print(f"    Record: {game_update_event.home_team['wins']}-{game_update_event.home_team['losses']}")
    print()
    
    # Check for status transitions
    status = game_update_event.game_status
    if status == 2:  # In Progress
        print("  ⚠ Game is in progress - would emit GameStartEvent if status transitioned from 1→2")
    elif status == 3:  # Finished
        home_score = game_update_event.home_team['score']
        away_score = game_update_event.away_team['score']
        winner = "home" if home_score > away_score else "away" if away_score > home_score else ""
        print(f"  ⚠ Game is finished - would emit GameResultEvent:")
        print(f"    Winner: {winner}")
        print(f"    Final Score: Home {home_score} - Away {away_score}")
    
    # Display game leaders if available
    if game_update_event.game_leaders:
        print()
        print("  Game Leaders:")
        for team_side in ["home", "away"]:
            leaders = game_update_event.game_leaders.get(team_side, {})
            if leaders:
                print(f"    {team_side.upper()}:")
                for stat_type in ["points", "rebounds", "assists"]:
                    stat_data = leaders.get(stat_type, {})
                    if stat_data and stat_data.get("name"):
                        value = stat_data.get(stat_type, 0) or stat_data.get("points", 0) or stat_data.get("rebounds", 0) or stat_data.get("assists", 0)
                        print(f"      {stat_type.capitalize()}: {stat_data['name']} ({value})")
    
    # Show event as dict
    print()
    print("  Event as dictionary (to_dict()):")
    event_dict = game_update_event.to_dict()
    print(event_dict)


@with_proxy
async def test_nba_store_logic(game_id: str):
    """
    Test NBAStore's _parse_api_response logic for all endpoints:
    - scoreboard (GameUpdateEvent, GameStartEvent, GameResultEvent)
    - play_by_play (PlayByPlayEvent, InGameCriticalEvent)
    
    Args:
        game_id: NBA game ID to test
    """
    print("="*80)
    print("TESTING NBA STORE LOGIC")
    print("="*80)
    print()
    
    # Create store instance
    api = NBAExternalAPI()
    store = NBAStore(api=api)
    
    # Test 1: Scoreboard parsing (GameUpdateEvent)
    print("TEST 1: Scoreboard Parsing (GameUpdateEvent)")
    print("-" * 80)
    games = get_games_for_date(datetime.now(), print_games=False)
    
    # Find the game
    target_game = None
    for game in games:
        if game.get('gameId') == game_id:
            target_game = game
            break
    
    if not target_game:
        game_info = get_game_info_by_id(game_id)
        if game_info:
            game_date = game_info.get('game_date')
            if game_date:
                games = get_games_for_date(game_date, print_games=False)
                for game in games:
                    if game.get('gameId') == game_id:
                        target_game = game
                        break
    
    if not target_game:
        print(f"✗ Game {game_id} not found")
        return
    
    # Convert to scoreboard format expected by store
    scoreboard_data = {
        "scoreboard": [target_game]
    }
    
    # Parse using store
    events = store._parse_api_response(scoreboard_data)
    
    print(f"✓ Parsed {len(events)} event(s) from scoreboard")
    for event in events:
        print(f"  - {event.event_type}: {event.__class__.__name__}")
        if event.__class__.__name__ == "GameUpdateEvent":
            game_update = event  # type: ignore[assignment]
            print(f"    Game ID: {game_update.game_id}")  # type: ignore[attr-defined]
            print(f"    Status: {game_update.game_status} ({game_update.game_status_text})")  # type: ignore[attr-defined]
            print(f"    Score: {game_update.away_team.get('teamTricode')} {game_update.away_team.get('score')} - {game_update.home_team.get('score')} {game_update.home_team.get('teamTricode')}")  # type: ignore[attr-defined]
        elif event.__class__.__name__ == "GameStartEvent":
            game_start = event  # type: ignore[assignment]
            print(f"    Event ID: {game_start.event_id}")  # type: ignore[attr-defined]
        elif event.__class__.__name__ == "GameResultEvent":
            game_result = event  # type: ignore[assignment]
            print(f"    Event ID: {game_result.event_id}")  # type: ignore[attr-defined]
            print(f"    Winner: {game_result.winner}")  # type: ignore[attr-defined]
            print(f"    Final Score: {game_result.final_score}")  # type: ignore[attr-defined]
    print()
    
    # Test 2: Play-by-play parsing (PlayByPlayEvent, InGameCriticalEvent)
    print("TEST 2: Play-by-Play Parsing (Critical Events Only)")
    print("-" * 80)
    
    # Get play-by-play data
    pbp_data = get_play_by_play(game_id, include_player_names=True)
    
    if not pbp_data or not pbp_data.get("actions"):
        print(f"✗ No play-by-play data found for game {game_id}")
        print("  (This is expected if the game hasn't started or is too old)")
        return
    
    actions = pbp_data["actions"]
    print(f"✓ Found {len(actions)} play-by-play actions")
    
    # Convert to play_by_play format expected by store
    play_by_play_data = {
        "play_by_play": {
            "gameId": game_id,
            "actions": actions,
        }
    }
    
    # Parse using store (should only emit critical events)
    pbp_events = store._parse_api_response(play_by_play_data)
    
    print(f"✓ Parsed {len(pbp_events)} critical event(s) from play-by-play")
    print(f"  (Filtered from {len(actions)} total actions)")
    print()
    
    if pbp_events:
        print("Critical Events:")
        for event in pbp_events:
            if event.__class__.__name__ == "PlayByPlayEvent":
                pbp = event  # type: ignore[assignment]
                print(f"  - PlayByPlayEvent:")
                print(f"    Action Type: {pbp.action_type}")  # type: ignore[attr-defined]
                print(f"    Period: {pbp.period}, Clock: {pbp.clock}")  # type: ignore[attr-defined]
                print(f"    Player: {pbp.player_name or 'N/A'} ({pbp.team_tricode})")  # type: ignore[attr-defined]
                print(f"    Description: {pbp.description}")  # type: ignore[attr-defined]
                print(f"    Is Critical: {pbp.is_critical()}")  # type: ignore[attr-defined]
            elif event.__class__.__name__ == "InGameCriticalEvent":
                critical = event  # type: ignore[assignment]
                print(f"  - InGameCriticalEvent:")
                print(f"    Event ID: {critical.event_id}")  # type: ignore[attr-defined]
                print(f"    Critical Type: {critical.critical_type}")  # type: ignore[attr-defined]
                print(f"    Player: {critical.player_name or 'N/A'} ({critical.team_tricode})")  # type: ignore[attr-defined]
                print(f"    Period: {critical.period}, Clock: {critical.clock}")  # type: ignore[attr-defined]
                print(f"    Description: {critical.description}")  # type: ignore[attr-defined]
        print(f"  Number of critical events: {len(pbp_events)}")
    else:
        print("  No critical events found in this game")
        print("  (This is normal if there were no injuries, ejections, or critical substitutions)")
    print()
    
    # Test 3: Test status transitions (GameStartEvent, GameResultEvent)
    print("TEST 3: Status Transitions (GameStartEvent, GameResultEvent)")
    print("-" * 80)
    
    # Simulate status transitions by manually setting previous status
    store._previous_game_status = {}
    
    # Test GameStartEvent: transition from 1 (Not Started) to 2 (In Progress)
    game_data_start = target_game.copy()
    game_data_start["gameStatus"] = 1  # Not Started
    scoreboard_data_start = {"scoreboard": [game_data_start]}
    events_start = store._parse_api_response(scoreboard_data_start)
    
    # Now transition to In Progress
    game_data_live = target_game.copy()
    game_data_live["gameStatus"] = 2  # In Progress
    scoreboard_data_live = {"scoreboard": [game_data_live]}
    events_live = store._parse_api_response(scoreboard_data_live)
    
    game_start_events = [e for e in events_live if e.__class__.__name__ == "GameStartEvent"]
    if game_start_events:
        print(f"✓ GameStartEvent emitted on status transition 1→2")
        for event in game_start_events:
            gs = event  # type: ignore[assignment]
            print(f"  Event ID: {gs.event_id}")  # type: ignore[attr-defined]
    else:
        print(f"✗ No GameStartEvent (game status is {target_game.get('gameStatus')}, not transitioning from 1→2)")
    print()
    
    # Test GameResultEvent: transition from 2 (In Progress) to 3 (Finished)
    store._previous_game_status = {game_id: 2}  # Set previous status to In Progress
    
    game_data_finished = target_game.copy()
    game_data_finished["gameStatus"] = 3  # Finished
    scoreboard_data_finished = {"scoreboard": [game_data_finished]}
    events_finished = store._parse_api_response(scoreboard_data_finished)
    
    game_result_events = [e for e in events_finished if e.__class__.__name__ == "GameResultEvent"]
    if game_result_events:
        print(f"✓ GameResultEvent emitted on status transition 2→3")
        for event in game_result_events:
            gr = event  # type: ignore[assignment]
            print(f"  Event ID: {gr.event_id}")  # type: ignore[attr-defined]
            print(f"  Winner: {gr.winner}")  # type: ignore[attr-defined]
            print(f"  Final Score: {gr.final_score}")  # type: ignore[attr-defined]
    else:
        print(f"✗ No GameResultEvent (game status is {target_game.get('gameStatus')}, not transitioning from 2→3)")
    print()
    
    # Test 4: Test critical event detection
    print("TEST 4: Critical Event Detection")
    print("-" * 80)
    
    # Create test play-by-play events with different types
    test_actions = [
        {
            "actionNumber": 1,
            "period": 1,
            "clock": "PT10M00.00S",
            "actionType": "shot",
            "personId": 0,
            "playerName": "Test Player",
            "teamTricode": "LAL",
            "scoreHome": "2",
            "scoreAway": "0",
            "description": "Made 2-point shot",
        },
        {
            "actionNumber": 2,
            "period": 1,
            "clock": "PT09M30.00S",
            "actionType": "substitution",
            "personId": 12345,
            "playerName": "Injured Player",
            "teamTricode": "GSW",
            "scoreHome": "2",
            "scoreAway": "0",
            "description": "Substitution: Injured Player leaves game",
        },
        {
            "actionNumber": 3,
            "period": 1,
            "clock": "PT09M00.00S",
            "actionType": "timeout",
            "personId": 0,
            "playerName": "",
            "teamTricode": "GSW",
            "scoreHome": "2",
            "scoreAway": "0",
            "description": "Timeout: Injury timeout",
        },
        {
            "actionNumber": 4,
            "period": 1,
            "clock": "PT08M30.00S",
            "actionType": "ejection",
            "personId": 67890,
            "playerName": "Ejected Player",
            "teamTricode": "LAL",
            "scoreHome": "2",
            "scoreAway": "0",
            "description": "Ejection: Ejected Player",
        },
    ]
    
    test_pbp_data = {
        "play_by_play": {
            "gameId": game_id,
            "actions": test_actions,
        }
    }
    
    test_events = store._parse_api_response(test_pbp_data)
    
    print(f"✓ Tested with {len(test_actions)} actions")
    print(f"✓ Emitted {len(test_events)} critical event(s)")
    print()
    
    for event in test_events:
        if event.__class__.__name__ == "PlayByPlayEvent":
            pbp = event  # type: ignore[assignment]
            print(f"  - {pbp.action_type}: {pbp.description}")  # type: ignore[attr-defined]
        elif event.__class__.__name__ == "InGameCriticalEvent":
            critical = event  # type: ignore[assignment]
            print(f"  - InGameCriticalEvent ({critical.critical_type}): {critical.player_name} - {critical.description}")  # type: ignore[attr-defined]
    
    print()
    print("="*80)
    print("ALL TESTS COMPLETE")
    print("="*80)


# Example usage
if __name__ == "__main__":

    # Test get_game_info_by_id utility function
    print("="*80)
    print("TESTING get_game_info_by_id UTILITY")
    print("="*80)
    print(f"Looking up game info for game_id: {game_id}")
    game_info = get_game_info_by_id(game_id)
    if game_info:
        print(f"✓ Found game info:")
        print(f"  Game ID: {game_info['game_id']}")
        print(f"  Matchup: {game_info['away_team']} @ {game_info['home_team']}")
        print(f"  Date: {game_info['game_date']}")
        print(f"  Time (UTC): {game_info['game_time_utc']}")
        print(f"  Away Team Tricode: {game_info['away_team_tricode']}")
        print(f"  Home Team Tricode: {game_info['home_team_tricode']}")
    else:
        print(f"✗ Game not found for game_id: {game_id}")
        print("  (This is expected if the game is older than 7 days or doesn't exist)")
    print()

    # Test GameUpdateEvent creation from ScoreboardV3
    print("="*80)
    print("TESTING GameUpdateEvent FROM SCOREBOARDV3")
    print("="*80)
    test_game_update_events(game_id)
    print()
    
    # Test NBA Store logic (all endpoints)
    import asyncio
    asyncio.run(test_nba_store_logic(game_id))
    print()
    