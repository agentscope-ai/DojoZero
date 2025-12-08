"""NBA data store implementation."""

from typing import Any, Sequence

from agentx.data._models import DataEvent
from agentx.data._stores import DataStore, ExternalAPI
from agentx.data.nba._api import NBAExternalAPI
from agentx.data.nba._events import RawPlayByPlayEvent
from agentx.data.nba._processors import PlayByPlayProcessor


class NBAStore(DataStore):
    """NBA data store for polling NBA API and emitting events."""
    
    def __init__(
        self,
        store_id: str = "nba_store",
        api: ExternalAPI | None = None,
        poll_interval_seconds: float = 5.0,
        event_emitter=None,
    ):
        """Initialize NBA store."""
        super().__init__(store_id, api or NBAExternalAPI(), poll_interval_seconds, event_emitter)
        
        # Register stream: raw_play_by_play -> processor -> play_by_play
        self.register_stream(
            "play_by_play",
            PlayByPlayProcessor(),
            ["raw_play_by_play"],
        )
    
    def _parse_api_response(self, data: dict[str, Any]) -> Sequence[DataEvent]:
        """Parse NBA API response into DataEvents."""
        events = []
        
        for event_data in data.get("events", []):
            # Parse timestamp
            from datetime import datetime, timezone
            timestamp_str = event_data.get("timestamp", "")
            if isinstance(timestamp_str, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                except:
                    timestamp = datetime.now(timezone.utc)
            else:
                timestamp = datetime.now(timezone.utc)
            
            events.append(
                RawPlayByPlayEvent(
                    timestamp=timestamp,
                    game_id=event_data.get("game_id", ""),
                    points=event_data.get("points", 0),
                    home_score=event_data.get("home_score", 0),
                    away_score=event_data.get("away_score", 0),
                    description=event_data.get("description", ""),
                )
            )
        
        return events

