# Query nba.live.endpoints.scoreboard and  list games in localTimeZone
from datetime import datetime, timezone
from dateutil import parser
from nba_api.live.nba.endpoints import scoreboard
from nba_api.live.nba.endpoints import playbyplay
from nba_api.stats.static import players
from nba_api.live.nba.endpoints import Odds


def get_current_games(print_games: bool = True):
    """
    Get all current games from the NBA scoreboard.
    
    Args:
        print_games (bool): Whether to print game information (default: True)
    
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
            'gameLeaders': {...}
        }
    """
    try:
        board = scoreboard.ScoreBoard()
        games = board.games.get_dict()
        
        if print_games:
            print(f"ScoreBoardDate: {board.score_board_date}")
            print(f"Found {len(games)} game(s)\n")
            
            f = "{gameId}: {awayTeam} vs. {homeTeam} @ {gameTimeLTZ} [{status}]"
            for game in games:
                game_time_utc = parser.parse(game["gameTimeUTC"])
                game_time_ltz = game_time_utc.replace(tzinfo=timezone.utc).astimezone(tz=None)
                
                print(f.format(
                    gameId=game['gameId'],
                    awayTeam=game['awayTeam']['teamName'],
                    homeTeam=game['homeTeam']['teamName'],
                    gameTimeLTZ=game_time_ltz.strftime("%Y-%m-%d %H:%M:%S %Z"),
                    status=game.get('gameStatusText', 'Unknown')
                ))
        
        # Enrich games with local timezone
        enriched_games = []
        for game in games:
            game_time_utc = parser.parse(game["gameTimeUTC"])
            game_time_ltz = game_time_utc.replace(tzinfo=timezone.utc).astimezone(tz=None)
            
            enriched_game = dict(game)
            enriched_game['gameTimeLTZ'] = game_time_ltz
            enriched_games.append(enriched_game)
        
        return enriched_games
    
    except Exception as e:
        print(f"Error fetching current games: {e}")
        return []


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
        pbp = playbyplay.PlayByPlay(game_id)
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


def get_odds(game_id):
    """
    Get betting odds for a specific game by game_id.
    
    Args:
        game_id (str): The NBA game ID (e.g., '0022500290')
    
    Returns:
        dict: Odds data for the game, or None if not found
        Structure:
        {
            'gameId': str,
            'sr_id': str,
            'srMatchId': str,
            'homeTeamId': str,
            'awayTeamId': str,
            'markets': [
                {
                    'name': str,  # e.g., '2way', 'spread'
                    'odds_type_id': int,
                    'group_name': str,
                    'books': [
                        {
                            'id': str,
                            'name': str,  # e.g., 'FanDuel', 'Betplay'
                            'outcomes': [
                                {
                                    'odds_field_id': int,
                                    'type': str,  # 'home' or 'away'
                                    'odds': str,  # e.g., '1.067'
                                    'opening_odds': str,
                                    'odds_trend': str,  # 'up', 'down'
                                    'spread': str | None,  # For spread markets
                                    'opening_spread': float | None
                                }
                            ]
                        }
                    ]
                }
            ]
        }
    """
    odds = Odds()
    games_list = odds.get_games().get_dict()
    
    # Find the game with matching game_id
    for game in games_list:
        if game.get('gameId') == game_id:
            return {
                'gameId': game.get('gameId'),
                'sr_id': game.get('sr_id'),
                'srMatchId': game.get('srMatchId'),
                'homeTeamId': game.get('homeTeamId'),
                'awayTeamId': game.get('awayTeamId'),
                'markets': game.get('markets', [])
            }
    
    return None

# Example usage
if __name__ == "__main__":
    import json
    
    # Get current games
    current_games = get_current_games()
    
    if current_games:
        # Get play-by-play for first game
        game_id = current_games[0]['gameId']
        pbp_data = get_play_by_play(game_id, include_player_names=True)
        
        if pbp_data and pbp_data.get('actions'):
            print(f"\nPlay-by-Play for Game {game_id}:")
            print(f"Total actions: {pbp_data['total_actions']}\n")
            
            line = "{action_number}: {period}:{clock} {player_name} ({action_type})"
            for action in pbp_data['actions'][:10]:  # Show first 10 actions
                player_name = action.get('playerName', 'Unknown')
                print(line.format(
                    action_number=action.get('actionNumber', 'N/A'),
                    period=action.get('period', 'N/A'),
                    clock=action.get('clock', 'N/A'),
                    action_type=action.get('actionType', 'N/A'),
                    player_name=player_name
                ))
        
        # Get odds for first game
        odds_data = get_odds(game_id)
        if odds_data:
            print(f"\n\nOdds for Game {game_id}:")
            print(json.dumps(odds_data, indent=2))
    