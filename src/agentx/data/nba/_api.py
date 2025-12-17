"""NBA ExternalAPI implementation."""

from datetime import datetime
from typing import Any
import logging

from agentx.data._stores import ExternalAPI
from agentx.data.nba._utils import get_proxy

logger = logging.getLogger(__name__)


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
            # DEPRECATED: Use boxscore endpoint instead
            # This is kept for backward compatibility but will be removed
            # For new code, use "boxscore" endpoint
            return {"scoreboard": []}
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
                logger.debug(f"JSON parsing error for game {game_id_param}: {json_error}")
                return {"play_by_play": {"gameId": game_id_param, "actions": []}}
            except Exception as e:
                # Other errors - log but return empty result to avoid crashing the poll loop
                logger.warning(f"Error fetching play-by-play data for game {game_id_param}: {e}")
                return {"play_by_play": {"gameId": game_id_param, "actions": []}}
        elif endpoint == "boxscore":
            # Fetch box score data using BoxScoreTraditionalV3
            # This replaces ScoreboardV3 and provides complete game data including all leaders
            game_id_param = params.get("game_id") if params else None
            
            if not game_id_param:
                return {"boxscore": {}}
            
            try:
                from nba_api.stats.endpoints import boxscoretraditionalv3
                
                proxy = get_proxy()
                if proxy:
                    box_score = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id_param, proxy=proxy)
                else:
                    box_score = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id_param)
                
                # Get full dict response (not just dataframes)
                box_score_dict = box_score.get_dict()
                
                if not box_score_dict or "boxScoreTraditional" not in box_score_dict:
                    return {"boxscore": {}}
                
                boxscore_data = box_score_dict["boxScoreTraditional"]
                
                # Before game starts, boxScoreTraditional may be None or empty
                # Return empty dict in that case (store will handle gracefully)
                if not boxscore_data or not isinstance(boxscore_data, dict):
                    return {"boxscore": {}}
                
                # Return full boxscore data including teams, players, and statistics
                return {
                    "boxscore": boxscore_data
                }
            except ImportError as e:
                raise RuntimeError(f"nba_api package not available: {e}") from e
            except (AttributeError, TypeError) as e:
                # These errors often occur when boxscore data is None/empty before game starts
                # This is expected behavior, so we suppress the warning and return empty result
                # Only log at debug level to avoid noise in logs
                logger.debug(
                    "Boxscore data not available for game %s (likely pre-game): %s",
                    game_id_param,
                    e,
                )
                return {"boxscore": {}}
            except Exception as e:
                logger.warning(f"Error fetching boxscore data for game {game_id_param}: {e}")
                return {"boxscore": {}}
        return {}
    

