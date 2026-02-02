"""Polymarket data store implementation."""

import logging
from typing import Any, Sequence

from dojozero.data._models import (
    DataEvent,
    MoneylineOdds,
    OddsInfo,
    OddsUpdateEvent,
    SpreadOdds,
    TotalOdds,
)
from dojozero.data._stores import DataStore, ExternalAPI
from dojozero.data.polymarket._api import PolymarketAPI
from dojozero.data.polymarket._models import MarketOddsData

logger = logging.getLogger(__name__)


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
        self, all_odds: dict[str, Any], identifier: dict[str, Any] | None = None
    ) -> Sequence[DataEvent]:
        """Parse Polymarket API response into DataEvents.

        Args:
            all_odds: Dictionary from fetch_odds_from_event() with structure:
                     {
                         "moneyline": MarketOddsData | None,
                         "spreads": list[MarketOddsData],
                         "totals": list[MarketOddsData]
                     }
            identifier: Optional identifier dict (e.g., {"espn_game_id": "401810490"})
                       Used to ensure game_id matches espn_game_id for consistency
        """
        import math
        from datetime import datetime, timezone

        events = []

        # Extract Pydantic models directly from the structure
        moneyline_data: MarketOddsData | None = all_odds.get("moneyline")
        spread_odds_list: list[MarketOddsData] = all_odds.get("spreads", [])
        total_odds_list: list[MarketOddsData] = all_odds.get("totals", [])

        # Deduplicate spreads and totals by line value
        seen_spreads: set[float] = set()
        deduplicated_spreads: list[MarketOddsData] = []
        for spread_odds in spread_odds_list:
            if spread_odds.line is not None:
                # Pydantic ensures line is a float, but check for NaN/inf
                if math.isfinite(spread_odds.line):
                    if spread_odds.line not in seen_spreads:
                        seen_spreads.add(spread_odds.line)
                        deduplicated_spreads.append(spread_odds)

        seen_totals: set[float] = set()
        deduplicated_totals: list[MarketOddsData] = []
        for total_odds in total_odds_list:
            if total_odds.line is not None:
                # Pydantic ensures line is a float, but check for NaN/inf
                if math.isfinite(total_odds.line):
                    if total_odds.line not in seen_totals:
                        seen_totals.add(total_odds.line)
                        deduplicated_totals.append(total_odds)

        # Use the deduplicated lists
        spread_odds_list = deduplicated_spreads
        total_odds_list = deduplicated_totals

        # Create a single OddsUpdateEvent with all the data combined
        if moneyline_data or spread_odds_list or total_odds_list:
            timestamp = datetime.now(timezone.utc)

            # Use espn_game_id from identifier - this is the ESPN game ID, not Polymarket market_id
            # The game_id in OddsUpdateEvent should always be the ESPN game ID for consistency
            if identifier and "espn_game_id" in identifier:
                game_id = identifier["espn_game_id"]
            else:
                # If no ESPN game ID is provided, use empty string (should not happen in normal operation)
                game_id = ""
                logger.warning(
                    "No espn_game_id in identifier when creating OddsUpdateEvent. "
                    "Using empty string for game_id."
                )

            # Extract tricodes from identifier (set by trial metadata)
            home_tricode = (identifier or {}).get("home_tricode", "")
            away_tricode = (identifier or {}).get("away_tricode", "")

            # Build MoneylineOdds from moneyline data
            moneyline: MoneylineOdds | None = None
            if moneyline_data:
                moneyline = MoneylineOdds(
                    home_probability=moneyline_data.home_probability,
                    away_probability=moneyline_data.away_probability,
                    home_odds=moneyline_data.home_odds,
                    away_odds=moneyline_data.away_odds,
                )

            # Build SpreadOdds list from all deduplicated spreads
            spreads: list[SpreadOdds] = []
            for spread_data in spread_odds_list:
                spreads.append(
                    SpreadOdds(
                        spread=spread_data.line or 0.0,
                        home_probability=spread_data.home_probability,
                        away_probability=spread_data.away_probability,
                        home_odds=spread_data.home_odds,
                        away_odds=spread_data.away_odds,
                    )
                )

            # Build TotalOdds list from all deduplicated totals
            totals: list[TotalOdds] = []
            for total_data in total_odds_list:
                totals.append(
                    TotalOdds(
                        total=total_data.line or 0.0,
                        over_probability=total_data.home_probability,
                        under_probability=total_data.away_probability,
                        over_odds=total_data.home_odds,
                        under_odds=total_data.away_odds,
                    )
                )

            events.append(
                OddsUpdateEvent(
                    timestamp=timestamp,
                    game_id=game_id,
                    home_tricode=home_tricode,
                    away_tricode=away_tricode,
                    odds=OddsInfo(
                        provider="polymarket",
                        moneyline=moneyline,
                        spreads=spreads,
                        totals=totals,
                    ),
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
                # Only fetch if we can construct a slug (game_id cannot be used to fetch odds)
                # Try to construct slug if we have the required info
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
                    # Store game_id for metadata in the result (not for fetching)
                    params["game_id"] = identifier["espn_game_id"]
                # If we can't construct a slug, don't set any params (will skip fetching)

        # Fetch odds from API
        all_odds = await self._api.fetch("odds", params if params else None)

        # Convert to DataEvents (pass identifier to ensure consistent game_id)
        events = self._parse_api_response(all_odds, identifier=identifier)

        # Record poll time after API call (regardless of whether events were returned)
        # This ensures we don't poll too frequently even if API returns no events
        self._record_poll_time("odds")

        return events

    async def start_polling(self) -> None:
        """Start polling and subscribe to game status events for dynamic interval adjustment."""
        # Subscribe to game status events to adjust polling interval
        if self._data_hub:

            def game_status_callback(event: DataEvent) -> None:
                """Callback to adjust polling interval based on game status."""
                event_type = event.event_type

                if event_type == "event.game_start":
                    # Game started: switch to in-game polling (5 seconds)
                    if not self._game_started:
                        logger.info(
                            "Game started, switching odds polling from 300s (pre-game) to 5s (in-game)"
                        )
                        self.update_poll_interval("odds", 5.0)
                        self._game_started = True
                elif event_type == "event.game_result":
                    # Game ended: stop odds polling (no more updates needed)
                    logger.info("Game ended, stopping odds polling")
                    # Stop polling by setting _running to False
                    # This will cause the _poll_loop to exit on next iteration
                    self._running = False

            self._data_hub.subscribe_agent(
                agent_id=f"{self.store_id}_game_status_monitor",
                event_types=[
                    "event.game_start",
                    "event.game_result",
                ],
                callback=game_status_callback,
            )
            logger.info(
                "PolymarketStore subscribed to game status events for dynamic polling interval adjustment"
            )

            # Check if game has already started/ended by looking at hub's recent events
            # This handles the case where events were emitted before we subscribed
            recent_results = self._data_hub.get_recent_events(
                event_types=["event.game_result"], limit=1
            )
            if recent_results:
                logger.info(
                    "Game already ended (found game_result event), stopping odds polling"
                )
                self._running = False
            else:
                recent_events = self._data_hub.get_recent_events(
                    event_types=["event.game_start"], limit=10
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
