"""Polymarket data store implementation."""

from typing import Any, Sequence

from agentx.data._models import DataEvent
from agentx.data._stores import DataStore, ExternalAPI
from agentx.data.polymarket._api import PolymarketAPI
from agentx.data.polymarket._events import OddsUpdateEvent


class PolymarketStore(DataStore):
    """Polymarket data store for polling Polymarket API and emitting events.
    
    Polls for odds updates every poll_interval_seconds (default 5s).
    
    Can be initialized with either:
    - market_url: Direct URL to Polymarket market (e.g., "https://polymarket.com/sports/nba/games/week/3/nba-sas-lal-2025-12-10")
    - Or will auto-construct slug from game info (away_tricode, home_tricode, game_date)
    """
    
    def __init__(
        self,
        store_id: str = "polymarket_store",
        api: ExternalAPI | None = None,
        poll_intervals: dict[str, float] | None = None,
        event_emitter=None,
        market_url: str | None = None,
        slug: str | None = None,
    ):
        """Initialize Polymarket store.
        
        Default polling intervals:
        - odds: 5.0 seconds
        
        Args:
            store_id: Store identifier
            api: External API instance (defaults to PolymarketAPI)
            poll_intervals: Per-endpoint polling intervals (e.g., {"odds": 5.0})
                           Defaults to {"odds": 5.0} if not provided
            event_emitter: Event emitter for publishing events
            market_url: Optional Polymarket market URL (e.g., "https://polymarket.com/sports/nba/games/week/3/nba-sas-lal-2025-12-10")
            slug: Optional market slug (e.g., "nba-sas-lal-2025-12-10"). If market_url is provided, slug is extracted from it.
        """
        # Set default poll_intervals if not provided
        if poll_intervals is None:
            poll_intervals = {
                "odds": 5.0,  # 5 seconds
            }
        
        super().__init__(
            store_id,
            api or PolymarketAPI(),
            poll_intervals,
            event_emitter,
        )
        
        # Extract slug from market_url if provided
        if market_url and not slug:
            slug = market_url.split("/")[-1]
        
        self._market_url = market_url
        self._slug = slug
    
    def _parse_api_response(self, data: dict[str, Any]) -> Sequence[DataEvent]:
        """Parse Polymarket API response into DataEvents."""
        from datetime import datetime, timezone
        
        events = []
        
        # Handle odds update events (for broker)
        if "odds_update" in data:
            odds_data = data["odds_update"]
            timestamp = datetime.now(timezone.utc)
            # Use market_id as event_id if event_id is not provided
            event_id = odds_data.get("event_id") or odds_data.get("market_id", "")
            events.append(
                OddsUpdateEvent(
                    timestamp=timestamp,
                    event_id=event_id,
                    home_odds=float(odds_data.get("home_odds", 1.0)),
                    away_odds=float(odds_data.get("away_odds", 1.0)),
                    home_probability=float(odds_data.get("home_probability", 0.0)),
                    away_probability=float(odds_data.get("away_probability", 0.0)),
                )
            )
        
        return events
    
    async def _poll_api(
        self,
        event_type: str | None = None,
        identifier: dict[str, Any] | None = None,
    ) -> Sequence[DataEvent]:
        """Poll the API for odds updates."""
        if not self._api:
            return []
        
        # Check if enough time has passed since last poll
        if not self._should_poll_endpoint("odds"):
            return []
        
        # Poll odds endpoint (for OddsUpdateEvent)
        params: dict[str, Any] = {}
        
        # Use market_url or slug if available (from initialization)
        if self._market_url:
            params["market_url"] = self._market_url
        elif self._slug:
            params["slug"] = self._slug
        elif identifier:
            # Try to construct slug from game info
            if "away_tricode" in identifier and "home_tricode" in identifier and "game_date" in identifier:
                away_tricode = identifier["away_tricode"].lower()
                home_tricode = identifier["home_tricode"].lower()
                game_date = identifier["game_date"]  # Expected format: YYYY-MM-DD
                params["slug"] = f"nba-{away_tricode}-{home_tricode}-{game_date}"
            elif "event_id" in identifier:
                params["event_id"] = identifier["event_id"]
            elif "game_id" in identifier:
                params["event_id"] = identifier["game_id"]
        
        # Fetch odds from API
        data = await self._api.fetch("odds", params if params else None)
        
        # Convert to DataEvents
        events = self._parse_api_response(data)
        
        # Record poll time after API call (regardless of whether events were returned)
        # This ensures we don't poll too frequently even if API returns no events
        self._record_poll_time("odds")
        
        return events

