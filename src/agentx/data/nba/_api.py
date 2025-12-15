"""NBA ExternalAPI implementation."""

from datetime import datetime
from typing import Any

from agentx.data._stores import ExternalAPI
from agentx.data.nba._utils import get_proxy


class NBAExternalAPI(ExternalAPI):
    """NBA API implementation."""
    
    def __init__(self, api_key: str | None = None):
        """Initialize NBA API.
        
        Args:
            api_key: Optional API key (for real implementation)
        """
        self.api_key = api_key
    
    async def fetch(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fetch NBA data."""
        game_id = params.get("game_id", "game_123") if params else "game_123"
        
        if endpoint == "scoreboard":
            # Query scoreboard using ScoreboardV3
            game_id_param = params.get("game_id") if params else None
            game_date = params.get("game_date") if params else None
            
            # If game_date not provided, use today
            if not game_date:
                game_date = datetime.now().strftime("%Y-%m-%d")
            
            try:
                from nba_api.stats.endpoints import scoreboardv3
                
                proxy = get_proxy()
                if proxy:
                    board = scoreboardv3.ScoreboardV3(game_date=game_date, proxy=proxy)
                else:
                    board = scoreboardv3.ScoreboardV3(game_date=game_date)
                
                games_data = board.get_dict()
                
                if not games_data or "scoreboard" not in games_data:
                    return {"scoreboard": []}
                
                scoreboard_data = games_data["scoreboard"]
                games_list = scoreboard_data.get("games", [])
                
                # If game_id specified, filter to that game
                if game_id_param:
                    games_list = [g for g in games_list if g.get("gameId") == game_id_param]
                
                return {"scoreboard": games_list}
            except ImportError as e:
                raise RuntimeError(f"nba_api package not available: {e}") from e
            except Exception as e:
                raise RuntimeError(f"Error fetching scoreboard data: {e}") from e
        elif endpoint == "play_by_play":
            # Fetch play-by-play data from NBA API
            game_id_param = params.get("game_id") if params else game_id
            
            if not game_id_param:
                return {"play_by_play": {"gameId": "", "actions": []}}
            
            try:
                from nba_api.live.nba.endpoints import playbyplay
                from nba_api.stats.static import players
                
                proxy = get_proxy()
                if proxy:
                    pbp = playbyplay.PlayByPlay(game_id_param, proxy=proxy)
                else:
                    pbp = playbyplay.PlayByPlay(game_id_param)
                
                # Handle potential JSON parsing errors
                try:
                    pbp_dict = pbp.get_dict()
                except (ValueError, TypeError) as json_error:
                    # Empty response or invalid JSON - return empty result
                    return {"play_by_play": {"gameId": game_id_param, "actions": []}}
                
                # Check if response is valid
                if not pbp_dict or not isinstance(pbp_dict, dict):
                    return {"play_by_play": {"gameId": game_id_param, "actions": []}}
                
                if "game" not in pbp_dict:
                    return {"play_by_play": {"gameId": game_id_param, "actions": []}}
                
                game_data = pbp_dict["game"]
                if not isinstance(game_data, dict):
                    return {"play_by_play": {"gameId": game_id_param, "actions": []}}
                
                actions = game_data.get("actions", [])
                if not isinstance(actions, list):
                    actions = []
                
                # Enrich actions with player names
                for action in actions:
                    if not isinstance(action, dict):
                        continue
                    person_id = action.get("personId")
                    if person_id:
                        try:
                            player = players.find_player_by_id(person_id)
                            if player is not None:
                                action["playerName"] = player["full_name"]
                        except Exception:
                            pass  # Player not found, skip
                
                return {
                    "play_by_play": {
                        "gameId": game_id_param,
                        "actions": actions,
                    }
                }
            except ImportError as e:
                raise RuntimeError(f"nba_api package not available: {e}") from e
            except (ValueError, TypeError) as json_error:
                # JSON parsing error - return empty result instead of crashing
                return {"play_by_play": {"gameId": game_id_param, "actions": []}}
            except Exception as e:
                # Other errors - log but return empty result to avoid crashing the poll loop
                import logging
                logger = logging.getLogger("agentx.data.nba._api")
                logger.warning(f"Error fetching play-by-play data for game {game_id_param}: {e}")
                return {"play_by_play": {"gameId": game_id_param, "actions": []}}
        return {}
    

