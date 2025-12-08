"""NBA ExternalAPI implementation."""

from typing import Any

from agentx.data._stores import ExternalAPI


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
        if endpoint == "events":
            # Simulated play-by-play events
            return {
                "events": [
                    {
                        "event_type": "raw_play_by_play",
                        "timestamp": "2024-01-15T20:00:00Z",
                        "game_id": params.get("game_id", "game_123") if params else "game_123",
                        "points": 2,
                        "home_score": 45,
                        "away_score": 42,
                        "description": "Made 2-point shot",
                    }
                ]
            }
        return {}
    
    async def place_bet(self, market_id: str, outcome: str, amount: float) -> dict[str, Any]:
        """Not applicable for NBA API."""
        raise NotImplementedError("NBA API does not support betting")

