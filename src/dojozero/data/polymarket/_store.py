"""Polymarket data store implementation."""

import logging
from typing import Any, Sequence

from dojozero.data._models import DataEvent
from dojozero.data._stores import DataStore, ExternalAPI
from dojozero.data.polymarket._api import PolymarketAPI
from dojozero.data.polymarket._events import OddsUpdateEvent
from dojozero.data.polymarket._models import MarketOddsData

logger = logging.getLogger("dojozero.data.polymarket._store")


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
        sport: str = "nba",
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
            sport: Sport type for slug construction ("nba" or "nfl"). Defaults to "nba".
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
        self._sport = sport.lower()  # "nba" or "nfl"
        self.sport_type = self._sport  # Expose for DataHub trace context

        # Track game status for dynamic polling intervals
        self._game_started: bool = False

    def _parse_api_response(
        self, data: dict[str, Any], identifier: dict[str, Any] | None = None
    ) -> Sequence[DataEvent]:
        """Parse Polymarket API response into DataEvents.

        Args:
            data: API response data
            identifier: Optional identifier dict (e.g., {"espn_game_id": "401810490"})
                       Used to ensure event_id matches espn_game_id for consistency
        """
        from datetime import datetime, timezone

        events = []

        # Collect all odds data (moneyline, spreads, totals) and combine into a single OddsUpdateEvent
        moneyline_data = None
        spread_odds_list: list[MarketOddsData] = []
        total_odds_list: list[MarketOddsData] = []

        # Check for odds_update (backward compatibility - moneyline only)
        if "odds_update" in data:
            odds_data = data["odds_update"]
            # Only use if it doesn't have market_type or if market_type is moneyline
            market_type = odds_data.get("market_type", "moneyline")
            if market_type == "moneyline" or "market_type" not in odds_data:
                moneyline_data = odds_data

        # Check for odds_update_moneyline
        if "odds_update_moneyline" in data:
            moneyline_data = data["odds_update_moneyline"]

        # Check for odds_update_spreads (handle both single entry and multiple indexed entries)
        # Look for all keys matching odds_update_spreads_* pattern
        # Use a set to track seen spread values to avoid duplicates
        seen_spreads: set[float] = set()
        spread_keys = [k for k in data.keys() if k.startswith("odds_update_spreads")]
        for spread_key in spread_keys:
            spread_data = data[spread_key]
            line = spread_data.get("line")
            if line is not None:
                try:
                    spread_value = float(line)
                    # Skip if we've already seen this spread value (deduplicate)
                    if spread_value in seen_spreads:
                        continue
                    seen_spreads.add(spread_value)

                    # Create MarketOddsData from the spread data
                    spread_odds = MarketOddsData.model_validate(spread_data)
                    spread_odds_list.append(spread_odds)
                except (ValueError, TypeError) as e:
                    logger.debug(
                        "Failed to create MarketOddsData from %s: %s", spread_key, e
                    )
                    pass

        # Check for odds_update_totals (handle both single entry and multiple indexed entries)
        # Look for all keys matching odds_update_totals_* pattern
        # Use a set to track seen total values to avoid duplicates
        seen_totals: set[float] = set()
        total_keys = [k for k in data.keys() if k.startswith("odds_update_totals")]
        for total_key in total_keys:
            total_data = data[total_key]
            line = total_data.get("line")
            if line is not None:
                try:
                    total_value = float(line)
                    # Skip if we've already seen this total value (deduplicate)
                    if total_value in seen_totals:
                        continue
                    seen_totals.add(total_value)

                    # Create MarketOddsData from the total data
                    total_odds = MarketOddsData.model_validate(total_data)
                    total_odds_list.append(total_odds)
                except (ValueError, TypeError) as e:
                    logger.debug(
                        "Failed to create MarketOddsData from %s: %s", total_key, e
                    )
                    pass

        # Create a single OddsUpdateEvent with all the data combined
        if moneyline_data or spread_odds_list or total_odds_list:
            timestamp = datetime.now(timezone.utc)

            # Use espn_game_id from identifier to ensure consistency with game events
            if identifier and "espn_game_id" in identifier:
                game_id = identifier["espn_game_id"]
            else:
                # Fallback to API response data
                game_id = odds_data.get("event_id") or odds_data.get("market_id", "")

            # Extract tricodes from identifier (set by trial metadata)
            home_tricode = (identifier or {}).get("home_tricode", "")
            away_tricode = (identifier or {}).get("away_tricode", "")

            # Use moneyline data for home_odds/away_odds if available
            home_odds = 1.0
            away_odds = 1.0
            home_probability = 0.0
            away_probability = 0.0

            if moneyline_data:
                home_odds = float(moneyline_data.get("home_odds", 1.0))
                away_odds = float(moneyline_data.get("away_odds", 1.0))
                home_probability = float(moneyline_data.get("home_probability", 0.0))
                away_probability = float(moneyline_data.get("away_probability", 0.0))
                # Use event_id from moneyline data if not set
                if not event_id:
                    event_id = moneyline_data.get("event_id") or moneyline_data.get(
                        "market_id", ""
                    )

            # Convert MarketOddsData to broker-expected format
            # For spreads: {"spread": line, "home_odds": home_odds, "away_odds": away_odds}
            # For totals: {"total": line, "over_odds": home_odds, "under_odds": away_odds}
            spread_updates_dict: list[dict[str, Any]] = []
            for spread_odds in spread_odds_list:
                if spread_odds.line is not None:
                    spread_updates_dict.append(
                        {
                            "spread": spread_odds.line,
                            "home_odds": spread_odds.home_odds,
                            "away_odds": spread_odds.away_odds,
                        }
                    )

            total_updates_dict: list[dict[str, Any]] = []
            for total_odds in total_odds_list:
                if total_odds.line is not None:
                    total_updates_dict.append(
                        {
                            "total": total_odds.line,
                            "over_odds": total_odds.home_odds,  # home_odds = over_odds for totals
                            "under_odds": total_odds.away_odds,  # away_odds = under_odds for totals
                        }
                    )

            events.append(
                OddsUpdateEvent(
                    timestamp=timestamp,
                    game_id=game_id,
                    home_tricode=home_tricode,
                    away_tricode=away_tricode,
                    home_odds=home_odds,
                    away_odds=away_odds,
                    home_probability=home_probability,
                    away_probability=away_probability,
                    spread_updates=spread_updates_dict,
                    total_updates=total_updates_dict,
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
                # Normalize ESPN tricodes to Polymarket format
                away_tricode = PolymarketAPI.normalize_tricode(
                    identifier["away_tricode"], self._sport
                )
                home_tricode = PolymarketAPI.normalize_tricode(
                    identifier["home_tricode"], self._sport
                )
                game_date = identifier["game_date"]  # Expected format: YYYY-MM-DD
                # Use sport prefix (nba or nfl)
                params["slug"] = (
                    f"{self._sport}-{away_tricode}-{home_tricode}-{game_date}"
                )
            elif "espn_game_id" in identifier:
                # For backward compatibility, we can still use event_id
                # But prefer slug if we can construct it
                params["event_id"] = identifier["espn_game_id"]
                # Also try to construct slug if we have the info
                if (
                    "away_tricode" in identifier
                    and "home_tricode" in identifier
                    and "game_date" in identifier
                ):
                    away_tricode = PolymarketAPI.normalize_tricode(
                        identifier["away_tricode"], self._sport
                    )
                    home_tricode = PolymarketAPI.normalize_tricode(
                        identifier["home_tricode"], self._sport
                    )
                    game_date = identifier["game_date"]
                    params["slug"] = (
                        f"{self._sport}-{away_tricode}-{home_tricode}-{game_date}"
                    )

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

                # Handle both NBA and NFL game start events
                if event_type in ("game_start", "nfl_game_start"):
                    # Game started: switch to in-game polling (5 seconds)
                    if not self._game_started:
                        logger.info(
                            "Game started, switching odds polling from 300s (pre-game) to 5s (in-game)"
                        )
                        self.update_poll_interval("odds", 5.0)
                        self._game_started = True
                # Handle both NBA and NFL game result events
                elif event_type in ("game_result", "nfl_game_result"):
                    # Game ended: stop odds polling (no more updates needed)
                    logger.info("Game ended, stopping odds polling")
                    # Stop polling by setting _running to False
                    # This will cause the _poll_loop to exit on next iteration
                    self._running = False

            # Subscribe to game_start and game_result events (both NBA and NFL)
            self._data_hub.subscribe_agent(
                agent_id=f"{self.store_id}_game_status_monitor",
                event_types=[
                    "game_start",
                    "game_result",
                    "nfl_game_start",
                    "nfl_game_result",
                ],
                callback=game_status_callback,
            )
            logger.info(
                "PolymarketStore subscribed to game status events for dynamic polling interval adjustment"
            )

            # Check if game has already started by looking at hub's recent events
            # This handles the case where game_start was emitted before we subscribed
            recent_events = self._data_hub.get_recent_events(
                event_types=["game_start", "nfl_game_start"], limit=10
            )
            if recent_events:
                logger.info(
                    "Game already started (found %d game_start events), "
                    "switching to in-game polling (5s)",
                    len(recent_events),
                )
                self.update_poll_interval("odds", 5.0)
                self._game_started = True

        # Call parent start_polling
        await super().start_polling()
