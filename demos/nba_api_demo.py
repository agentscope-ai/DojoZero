# Query NBA scoreboard using ScoreboardV3 endpoint
from __future__ import annotations
import asyncio
from datetime import timezone, datetime, timedelta
from typing import Any
from dateutil import parser
from nba_api.live.nba.endpoints import playbyplay
from nba_api.stats.static import players
from nba_api.stats.endpoints import scoreboardv3

# Try to load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, skip .env loading

# Import proxy utilities from agentx
from agentx.data.nba._utils import with_proxy, get_game_info_by_id
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
            'gameLeaders': {
                'homeLeaders': {
                    'personId': int,
                    'name': str,
                    'playerSlug': str,
                    'jerseyNum': str,
                    'position': str,
                    'teamTricode': str,
                    'points': int,  # Direct numeric value
                    'rebounds': int,  # Direct numeric value
                    'assists': int   # Direct numeric value
                },
                'awayLeaders': {...}  # Same structure as homeLeaders
            }  # May not be present (empty when game hasn't started)
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
    
    Uses NBAStore's parsing logic to ensure consistency.
    
    Args:
        game_data: Game dictionary from ScoreboardV3 API
        
    Returns:
        GameUpdateEvent instance (or None if parsing fails)
    """
    # Use the actual NBAStore to parse the data
    store = NBAStore()
    
    # Convert game_data to the format expected by store._parse_api_response
    scoreboard_data = {
        "scoreboard": [game_data]
    }
    
    # Parse using store's logic
    events = store._parse_api_response(scoreboard_data)
    
    # Find the GameUpdateEvent (should be the first/last event)
    for event in events:
        if event.event_type == "game_update":
            return event
    
    # If no GameUpdateEvent found, return None
    return None


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
    events = list(store._parse_api_response(scoreboard_data))[:10]
    
    event_types = [e.event_type for e in events]
    print(f"✓ Parsed {len(events)} event(s): {', '.join(set(event_types))}")
    game_updates = [e for e in events if e.event_type == "game_update"]
    if game_updates:
        gu = game_updates[0]  # type: ignore[assignment]
        away = gu.away_team.get('teamTricode', '')  # type: ignore[attr-defined]
        away_score = gu.away_team.get('score', 0)  # type: ignore[attr-defined]
        home_score = gu.home_team.get('score', 0)  # type: ignore[attr-defined]
        home = gu.home_team.get('teamTricode', '')  # type: ignore[attr-defined]
        print(f"  Score: {away} {away_score}-{home_score} {home} | Status: {gu.game_status_text}")  # type: ignore[attr-defined]
    print()
    
    # Test 2: Play-by-play parsing
    print("TEST 2: Play-by-Play Parsing")
    print("-" * 80)
    
    # Get play-by-play data
    pbp_data = get_play_by_play(game_id, include_player_names=True)
    
    if not pbp_data or not pbp_data.get("actions"):
        print(f"✗ No play-by-play data (game not started or too old)")
        return
    
    actions = pbp_data["actions"]
    play_by_play_data = {"play_by_play": {"gameId": game_id, "actions": actions}}
    pbp_events = store._parse_api_response(play_by_play_data)
    
    print(f"✓ Found {len(actions)} actions → {len(pbp_events)} critical event(s)")
    if pbp_events:
        for event in pbp_events[:5]:
            if event.event_type == "play_by_play":
                pbp = event  # type: ignore[assignment]
                print(f"  - {pbp.action_type} ({pbp.period}Q {pbp.clock}): {pbp.description[:60]}")  # type: ignore[attr-defined]
            elif event.event_type == "in_game_critical":
                crit = event  # type: ignore[assignment]
                print(f"  - {crit.critical_type} ({crit.period}Q): {crit.player_name} - {crit.description[:50]}")  # type: ignore[attr-defined]
        if len(pbp_events) > 5:
            print(f"  ... and {len(pbp_events) - 5} more")
    print()
    
    # Test 3: Test status transitions (GameStartEvent, GameResultEvent)
    print("TEST 3: Status Transitions")
    print("-" * 80)
    
    # Test GameStartEvent: transition from 1 (Not Started) to 2 (In Progress)
    store._previous_game_status = {game_id: 1}  # Set previous status to Not Started
    game_data_live = target_game.copy()
    game_data_live["gameStatus"] = 2  # In Progress
    events_live = store._parse_api_response({"scoreboard": [game_data_live]})
    
    game_start_events = [e for e in events_live if e.event_type == "game_start"]
    if game_start_events:
        print(f"✓ GameStartEvent: 1→2 transition successful")
    else:
        print(f"✗ GameStartEvent: 1→2 transition failed (current status: {target_game.get('gameStatus')})")
    
    # Test GameResultEvent: transition from 2 (In Progress) to 3 (Finished)
    store._previous_game_status = {game_id: 2}  # Set previous status to In Progress
    game_data_finished = target_game.copy()
    game_data_finished["gameStatus"] = 3  # Finished
    events_finished = store._parse_api_response({"scoreboard": [game_data_finished]})
    
    game_result_events = [e for e in events_finished if e.event_type == "game_result"]
    if game_result_events:
        gr = game_result_events[0]  # type: ignore[assignment]
        print(f"✓ GameResultEvent: 2→3 transition successful (Winner: {gr.winner}, Score: {gr.final_score})")  # type: ignore[attr-defined]
    else:
        print(f"✗ GameResultEvent: 2→3 transition failed (current status: {target_game.get('gameStatus')})")
    print()
    
    # Test 4: Test critical event detection
    print("TEST 4: Critical Event Detection")
    print("-" * 80)
    
    # Reset store state to avoid deduplication issues with test data
    test_game_id = f"{game_id}_test"
    store._last_action_number.pop(test_game_id, None)
    
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
            "gameId": test_game_id,  # Use different game_id to avoid deduplication
            "actions": test_actions,
        }
    }
    
    test_events = store._parse_api_response(test_pbp_data)
    print(f"✓ {len(test_actions)} actions → {len(test_events)} critical event(s)")
    for event in test_events:
        if event.event_type == "play_by_play":
            pbp = event  # type: ignore[assignment]
            print(f"  - {pbp.action_type}: {pbp.description}")  # type: ignore[attr-defined]
        elif event.event_type == "in_game_critical":
            crit = event  # type: ignore[assignment]
            print(f"  - {crit.critical_type}: {crit.player_name} - {crit.description}")  # type: ignore[attr-defined]
    print()
    print("="*80)
    print("ALL TESTS COMPLETE")
    print("="*80)


# Example usage
if __name__ == "__main__":

    # Test get_game_info_by_id utility function
    print("="*80)
    print("TESTING NBA STORE LOGIC")
    print("="*80)
    game_info = get_game_info_by_id(game_id)
    if game_info:
        print(f"✓ Game {game_id}: {game_info['away_team']} @ {game_info['home_team']} ({game_info['game_date']})")
    else:
        print(f"✗ Game {game_id} not found (may be >7 days old)")
    print()
    
    # Test NBA Store logic (all endpoints)
    asyncio.run(test_nba_store_logic(game_id))
    print()
    