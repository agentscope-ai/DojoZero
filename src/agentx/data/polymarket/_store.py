"""Polymarket data store implementation."""

from typing import Any, Sequence

from agentx.data._models import DataEvent
from agentx.data._stores import DataStore, ExternalAPI
from agentx.data.polymarket._api import PolymarketAPI
from agentx.data.polymarket._events import RawOddsChangeEvent
from agentx.data.polymarket._processors import OddsChangeProcessor


class PolymarketStore(DataStore):
    """Polymarket data store for polling Polymarket API and emitting events."""
    
    def __init__(
        self,
        store_id: str = "polymarket_store",
        api: ExternalAPI | None = None,
        poll_interval_seconds: float = 5.0,
        event_emitter=None,
    ):
        """Initialize Polymarket store."""
        super().__init__(store_id, api or PolymarketAPI(), poll_interval_seconds, event_emitter)
        
        # Register stream: raw_odds_change -> processor -> odds_change
        self.register_stream(
            "odds_change",
            OddsChangeProcessor(),
            ["raw_odds_change"],
        )
    
    def _parse_api_response(self, data: dict[str, Any]) -> Sequence[DataEvent]:
        """Parse Polymarket API response into DataEvents."""
        from datetime import datetime, timezone
        
        events = []
        
        for event_data in data.get("events", []):
            # Parse timestamp
            timestamp_str = event_data.get("timestamp", "")
            if isinstance(timestamp_str, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                except:
                    timestamp = datetime.now(timezone.utc)
            else:
                timestamp = datetime.now(timezone.utc)
            
            events.append(
                RawOddsChangeEvent(
                    timestamp=timestamp,
                    market_id=event_data.get("market_id", ""),
                    outcomes=event_data.get("outcomes", []),
                )
            )
        
        return events

