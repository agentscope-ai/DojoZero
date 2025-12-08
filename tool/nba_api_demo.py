# Query NBA scoreboard using ScoreboardV3 endpoint
import os
from datetime import timezone, datetime, timedelta
from dateutil import parser
from nba_api.live.nba.endpoints import playbyplay
from nba_api.stats.static import players
from nba_api.live.nba.endpoints import Odds
from nba_api.stats.static import teams
from nba_api.stats.endpoints import scoreboardv3

# Try to load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, skip .env loading


def get_proxy():
    """
    Get proxy configuration from environment variables or .env file.
    
    Looks for PROXY_URL environment variable. If not found, returns None.
    Supports .env file loading if python-dotenv is installed.
    
    Returns: 
        str: Proxy URL string, or None if not configured
    """
    proxy_url = os.getenv('PROXY_URL')
    if not proxy_url:
        raise ValueError("PROXY_URL environment variable is not set")
    print(f"Using proxy: {proxy_url}")
    return proxy_url


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
        # Get proxy configuration
        proxy = get_proxy()
        
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
        board = scoreboardv3.ScoreboardV3(game_date=date_str, proxy=proxy)
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


# EventMsgActionType to points mapping
# Based on NBA API PlayByPlay structure: https://github.com/swar/nba_api/blob/master/docs/examples/PlayByPlay.ipynb
# EVENTMSGTYPE: 1 = Made, 2 = Missed
# EVENTMSGACTIONTYPE determines shot type and points value
EVENT_MSG_ACTION_TYPE_POINTS = {
    # 3-point shots = 3 points
    1: 3,   # 3PT_JUMP_SHOT, JUMP_SHOT (if 3pt)
    79: 3,  # 3PT_PULLUP_JUMP_SHOT, PULLUP_JUMP_SHOT (if 3pt)
    80: 3,  # 3PT_STEP_BACK_JUMP_SHOT, STEP_BACK_JUMP_SHOT (if 3pt)
    
    # 2-point shots = 2 points
    3: 2,   # HOOK_SHOT
    5: 2,   # LAYUP
    6: 2,   # DRIVING_LAYUP
    7: 2,   # DUNK
    41: 2,  # RUNNING_LAYUP
    44: 2,  # REVERSE_LAYUP
    47: 2,  # TURNAROUND_JUMP_SHOT
    50: 2,  # RUNNING_DUNK
    52: 2,  # ALLEY_OOP_DUNK
    58: 2,  # TURNAROUND_HOOK_SHOT
    66: 2,  # JUMP_BANK_SHOT
    71: 2,  # FINGER_ROLL_LAYUP
    72: 2,  # PUTBACK_LAYUP
    73: 2,  # DRIVING_REVERSE_LAYUP
    75: 2,  # DRIVING_FINGER_ROLL_LAYUP
    76: 2,  # RUNNING_FINGER_ROLL_LAYUP
    78: 2,  # FLOATING_JUMP_SHOT
    86: 2,  # TURNAROUND_FADEAWAY
    97: 2,  # TIP_LAYUP_SHOT
    98: 2,  # CUTTING_LAYUP_SHOT
    99: 2,  # CUTTING_FINGER_ROLL_LAYUP_SHOT
    108: 2, # CUTTING_DUNK_SHOT
    
    # Free throws = 1 point (handled separately by EVENTMSGTYPE=3)
}

# EventMsgType values (from NBA API)
EVENT_MSG_TYPE = {
    1: 'FIELD_GOAL_MADE',
    2: 'FIELD_GOAL_MISSED',
    3: 'FREE_THROW',
    4: 'REBOUND',
    5: 'TURNOVER',
    6: 'FOUL',
    7: 'VIOLATION',
    8: 'SUBSTITUTION',
    9: 'TIMEOUT',
    10: 'JUMP_BALL',
    11: 'EJECTION',
    12: 'PERIOD_BEGIN',
    13: 'PERIOD_END',
}


# Example usage
if __name__ == "__main__":
    import json

    #
    print("-" * 50)
    print("ALL TEAMS:")
    print(teams.get_teams())
    print("-" * 50)

    # Get current games
    print("="*80)
    print("CHECKING FOR STARTED OR FINISHED GAMES TODAY")
    print("="*80)
    games = get_current_games()
    
    if all(not is_game_started_or_finished(game) for game in games):
        print("All games haven't started yet")
        games = get_most_recent_finished_games(max_days_back=7, print_games=True)
    
    if games:
        print(f"Found {len(games)} game(s)")
        print(games)
    else:
        print("No games found")
    