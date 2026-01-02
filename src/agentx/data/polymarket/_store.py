"""Polymarket data store implementation."""

from typing import Any, Sequence

from agentx.data._models import DataEvent
from agentx.data._stores import DataStore, ExternalAPI
from agentx.data.polymarket._api import PolymarketAPI
from agentx.data.polymarket._events import OddsUpdateEvent


class PolymarketStore(DataStore):
    """Polymarket data store for polling Polymarket API and emitting events.

    Polls for odds updates with dynamic intervals based on game status:
    - Pre-game: 5 minutes (300 seconds) - default
    - In-game: 5 seconds (automatically switched when game_start event is received)

    Can be initialized with either:
    - market_url: Direct URL to Polymarket market (e.g., "https://polymarket.com/sports/nba/games/week/3/nba-sas-lal-2025-12-10")
    - Or will auto-construct slug from game info (away_tricode, home_tricode, game_date)

    The store automatically subscribes to game_start and game_result events to adjust
    polling frequency dynamically.
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
        - odds: 300.0 seconds (5 minutes) - pre-game default
        - Automatically switches to 5.0 seconds when game starts (via game_start event)

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
        # Default: pre-game interval (5 minutes = 300 seconds)
        # Will be updated to 5 seconds when game starts (via game_start event subscription)
        if poll_intervals is None:
            poll_intervals = {
                "odds": 300.0,  # Pre-game: 5 minutes (300 seconds)
            }
        elif "odds" not in poll_intervals:
            # If poll_intervals provided but "odds" not specified, use pre-game default
            poll_intervals["odds"] = 300.0  # Pre-game: 5 minutes

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

        # Track game status for dynamic polling intervals
        self._game_started: bool = False

    def _parse_api_response(
        self, data: dict[str, Any], identifier: dict[str, Any] | None = None
    ) -> Sequence[DataEvent]:
        """Parse Polymarket API response into DataEvents.

        Args:
            data: API response data
            identifier: Optional identifier dict (e.g., {"game_id": "0022500001"})
                       Used to ensure event_id matches game_id for consistency
        """
        from datetime import datetime, timezone

        events = []

        # Handle odds update events (for broker)
        if "odds_update" in data:
            odds_data = data["odds_update"]
            timestamp = datetime.now(timezone.utc)

            # Prioritize game_id from identifier to ensure consistency with NBA events
            # This ensures odds_update, game_update, and game_initialize all use the same event_id
            if identifier and "game_id" in identifier:
                event_id = identifier["game_id"]
            elif identifier and "event_id" in identifier:
                event_id = identifier["event_id"]
            else:
                # Fallback to API response data (for backward compatibility)
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
            if (
                "away_tricode" in identifier
                and "home_tricode" in identifier
                and "game_date" in identifier
            ):
                away_tricode = identifier["away_tricode"].lower()
                home_tricode = identifier["home_tricode"].lower()
                game_date = identifier["game_date"]  # Expected format: YYYY-MM-DD
                params["slug"] = f"nba-{away_tricode}-{home_tricode}-{game_date}"
            elif "game_id" in identifier:
                # Use game_id for API params (API may use "event_id" parameter name)
                params["event_id"] = identifier["game_id"]
            elif "event_id" in identifier:
                params["event_id"] = identifier["event_id"]

        # Fetch odds from API
        data = await self._api.fetch("odds", params if params else None)

        # Convert to DataEvents (pass identifier to ensure consistent event_id)
        events = self._parse_api_response(data, identifier=identifier)

        # Record poll time after API call (regardless of whether events were returned)
        # This ensures we don't poll too frequently even if API returns no events
        self._record_poll_time("odds")

        return events

    async def start_polling(self) -> None:
        """Start polling and subscribe to game status events for dynamic interval adjustment."""
        # Subscribe to game status events to adjust polling interval
        if self._data_hub:
            import logging

            logger = logging.getLogger(__name__)

            def game_status_callback(event: DataEvent) -> None:
                """Callback to adjust polling interval based on game status."""
                event_type = event.event_type

                if event_type == "game_start":
                    # Game started: switch to in-game polling (5 seconds)
                    if not self._game_started:
                        logger.info(
                            "Game started, switching odds polling from 300s (pre-game) to 5s (in-game)"
                        )
                        self.update_poll_interval("odds", 5.0)
                        self._game_started = True
                elif event_type == "game_result":
                    # Game ended: stop odds polling (no more updates needed)
                    logger.info("Game ended, stopping odds polling")
                    # Stop polling by setting _running to False
                    # This will cause the _poll_loop to exit on next iteration
                    self._running = False

            # Subscribe to game_start and game_result events
            self._data_hub.subscribe_agent(
                agent_id=f"{self.store_id}_game_status_monitor",
                event_types=["game_start", "game_result"],
                callback=game_status_callback,
            )
            logger.info(
                "PolymarketStore subscribed to game status events for dynamic polling interval adjustment"
            )

        # Call parent start_polling
        await super().start_polling()
