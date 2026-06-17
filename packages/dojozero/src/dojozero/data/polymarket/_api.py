"""Polymarket ExternalAPI implementation.

Polymarket API documentation: https://docs.polymarket.com/quickstart/overview

Team abbreviation mappings are sourced from Polymarket's Teams API:
- API docs: https://docs.polymarket.com/api-reference/sports/list-teams
- NBA teams: https://gamma-api.polymarket.com/teams?league=nba
- NFL teams: https://gamma-api.polymarket.com/teams?league=nfl
"""

import dataclasses
import json
import logging
from typing import Any

import aiohttp

from dojozero.data._stores import ExternalAPI
from dojozero.data.polymarket._models import (
    EventData,
    MarketData,
    MarketOddsData,
)


logger = logging.getLogger("dojozero.data.polymarket._api")


class PolymarketAPI(ExternalAPI):
    """Polymarket API implementation using Gamma API."""

    # Complete mappings from ESPN team abbreviations to Polymarket slug abbreviations
    # Source: https://gamma-api.polymarket.com/teams?league=nba and ?league=nfl

    # NBA: All 30 teams - ESPN tricode -> Polymarket abbreviation
    # Most ESPN tricodes match Polymarket (lowercase), only mismatches listed here
    ESPN_TO_POLYMARKET_TRICODE_NBA: dict[str, str] = {
        # ESPN uses shorter codes than Polymarket
        "GS": "gsw",  # Golden State Warriors
        "NO": "nop",  # New Orleans Pelicans
        "NY": "nyk",  # New York Knicks
        "SA": "sas",  # San Antonio Spurs
        # ESPN uses longer/different codes
        "UTAH": "uta",  # Utah Jazz
        "PHX": "phx",  # Phoenix Suns (ESPN sometimes uses PHO)
        "PHO": "phx",  # Phoenix Suns alternate
        "WSH": "was",  # Washington Wizards
        "BKN": "bkn",  # Brooklyn Nets (ensure consistency)
        # Standard 3-letter codes that match (lowercase conversion handles these)
        # ATL->atl, BOS->bos, CHA->cha, CHI->chi, CLE->cle, DAL->dal, DEN->den,
        # DET->det, HOU->hou, IND->ind, LAC->lac, LAL->lal, MEM->mem, MIA->mia,
        # MIL->mil, MIN->min, OKC->okc, ORL->orl, PHI->phi, POR->por, SAC->sac,
        # TOR->tor
    }

    # NFL: All 32 teams - ESPN tricode -> Polymarket abbreviation
    # Many ESPN tricodes are 2-3 letters that need specific mapping
    ESPN_TO_POLYMARKET_TRICODE_NFL: dict[str, str] = {
        # ESPN uses 3-letter codes, Polymarket uses 2-letter
        "LAR": "la",  # Los Angeles Rams
        "LVR": "lv",  # Las Vegas Raiders (alternate)
        "LV": "lv",  # Las Vegas Raiders
        "KAN": "kc",  # Kansas City Chiefs (alternate)
        "KC": "kc",  # Kansas City Chiefs
        "TAM": "tb",  # Tampa Bay Buccaneers (alternate)
        "TB": "tb",  # Tampa Bay Buccaneers
        "GNB": "gb",  # Green Bay Packers (alternate)
        "GB": "gb",  # Green Bay Packers
        "NOR": "no",  # New Orleans Saints (alternate)
        "NO": "no",  # New Orleans Saints
        "SFO": "sf",  # San Francisco 49ers (alternate)
        "SF": "sf",  # San Francisco 49ers
        "NWE": "ne",  # New England Patriots (alternate)
        "NE": "ne",  # New England Patriots
        "JAX": "jax",  # Jacksonville Jaguars
        "JAC": "jax",  # Jacksonville Jaguars (alternate)
        "WSH": "was",  # Washington Commanders
        "WAS": "was",  # Washington Commanders (alternate)
        "NYJ": "nyj",  # New York Jets
        "NYG": "nyg",  # New York Giants
        "LAC": "lac",  # Los Angeles Chargers
        "ARI": "ari",  # Arizona Cardinals
        "ARZ": "ari",  # Arizona Cardinals (alternate)
        "CAR": "car",  # Carolina Panthers
        # Standard codes that match (lowercase conversion handles these)
        # ATL->atl, BAL->bal, BUF->buf, CHI->chi, CIN->cin, CLE->cle, DAL->dal,
        # DEN->den, DET->det, HOU->hou, IND->ind, MIA->mia, MIN->min, PHI->phi,
        # PIT->pit, SEA->sea, TEN->ten
    }

    @staticmethod
    def normalize_tricode(tricode: str, sport: str = "nba") -> str:
        """Normalize ESPN team tricode to Polymarket format.

        Uses hardcoded mappings for known mismatches, falls back to lowercase first 3 chars.

        Args:
            tricode: ESPN team abbreviation (e.g., "GS", "UTAH", "LAL", "LAR")
            sport: Sport type ("nba", "nfl", or "world_cup") for sport-specific mappings

        Returns:
            Polymarket-compatible lowercase code (e.g., "gsw", "uta", "lal", "la")
        """
        upper = tricode.upper()
        sport_lower = sport.lower()

        # Check sport-specific mapping first
        if (
            sport_lower == "nba"
            and upper in PolymarketAPI.ESPN_TO_POLYMARKET_TRICODE_NBA
        ):
            return PolymarketAPI.ESPN_TO_POLYMARKET_TRICODE_NBA[upper]
        if (
            sport_lower == "nfl"
            and upper in PolymarketAPI.ESPN_TO_POLYMARKET_TRICODE_NFL
        ):
            return PolymarketAPI.ESPN_TO_POLYMARKET_TRICODE_NFL[upper]

        # Default: lowercase and take first 3 characters
        return tricode.lower()[:3]

    @staticmethod
    def _is_soccer(sport: str) -> bool:
        """True for FIFA World Cup soccer sport identifiers.

        Single source of truth for soccer detection so the slug prefix and the
        home-away team ordering can't drift apart.
        """
        return sport.lower().strip().replace("_", "-") in {
            "worldcup",
            "world-cup",
            "fifa",
            "soccer",
        }

    @staticmethod
    def sport_slug_prefix(sport: str) -> str:
        """Return the sport prefix used in Polymarket event slugs."""
        if PolymarketAPI._is_soccer(sport):
            # Polymarket uses the "fifwc" prefix for FIFA World Cup soccer
            # markets (e.g. "fifwc-aut-jor-2026-06-17"), not "world-cup".
            return "fifwc"
        return sport.lower().strip().replace("_", "-")

    @staticmethod
    def event_slug(
        away_tricode: str, home_tricode: str, game_date: str, sport: str = "nba"
    ) -> str:
        """Build the Polymarket event slug for a game.

        US sports (NBA/NFL) order the slug away-home (e.g. "nba-lal-bos-..."),
        while Polymarket's FIFA World Cup soccer markets order it home-away
        (e.g. "fifwc-aut-jor-..." for Jordan @ Austria).
        """
        away_code = PolymarketAPI.normalize_tricode(away_tricode, sport)
        home_code = PolymarketAPI.normalize_tricode(home_tricode, sport)
        sport_prefix = PolymarketAPI.sport_slug_prefix(sport)
        # Polymarket orders World Cup soccer slugs home-away; US sports keep the
        # away-home order. Key off the sport (not the prefix string) so the two
        # stay coupled to a single soccer check.
        teams = (
            f"{home_code}-{away_code}"
            if PolymarketAPI._is_soccer(sport)
            else f"{away_code}-{home_code}"
        )
        return f"{sport_prefix}-{teams}-{game_date}"

    @staticmethod
    def get_event_url(
        away_tricode: str, home_tricode: str, game_date: str, sport: str = "nba"
    ) -> str:
        """Generate Polymarket event page URL.

        Args:
            away_tricode: Away team ESPN tricode (e.g., "LAL", "LAR")
            home_tricode: Home team ESPN tricode (e.g., "BOS", "SEA")
            game_date: Game date in YYYY-MM-DD format
            sport: Sport type ("nba", "nfl", or "world_cup")

        Returns:
            Polymarket event URL (e.g., "https://polymarket.com/event/nba-lal-bos-2025-01-25").
        """
        slug = PolymarketAPI.event_slug(away_tricode, home_tricode, game_date, sport)
        return f"https://polymarket.com/event/{slug}"

    def __init__(self, api_key: str | None = None):
        """Initialize Polymarket API.

        Args:
            api_key: Optional API key (for real implementation)
        """
        self.api_key = api_key
        self._gamma_base_url = "https://gamma-api.polymarket.com"

    async def get_market_by_slug(self, slug: str) -> dict[str, Any]:
        """Query Gamma API for market data by slug.

        Args:
            slug: Market slug (e.g., "nba-sas-lal-2025-12-10")

        Returns:
            Market data dictionary with market information
        """
        url = f"{self._gamma_base_url}/markets/slug/{slug}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                response.raise_for_status()
                return await response.json()

    async def get_event_by_slug(self, slug: str) -> EventData | None:
        """Query Gamma API for event data by slug.

        Args:
            slug: Event slug (e.g., "nfl-sea-ne-2026-02-08")

        Returns:
            EventData model with event information and markets, or None if not found
        """
        url = f"{self._gamma_base_url}/events/slug/{slug}"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url) as response:
                    if response.status == 404:
                        return None
                    response.raise_for_status()
                    data = await response.json()
                    return EventData.model_validate(data)
            except aiohttp.ClientError as e:
                logger.debug("Failed to fetch event by slug %s: %s", slug, e)
                return None
            except Exception as e:
                logger.error("Failed to parse event data for slug %s: %s", slug, e)
                return None

    async def get_event_markets(self, slug: str) -> list[MarketData]:
        """Get all markets for a given event slug.

        Args:
            slug: Event slug (e.g., "nfl-sea-ne-2026-02-08")

        Returns:
            List of MarketData models
        """
        # Query by event slug (most reliable approach)
        # Event slugs are in format: sport-away-home-date (e.g., "nfl-sea-ne-2026-02-08")
        try:
            event_data = await self.get_event_by_slug(slug)
            if event_data and event_data.markets:
                logger.debug(
                    "Found %d markets for event slug %s", len(event_data.markets), slug
                )
                return event_data.markets
        except Exception as e:
            logger.debug("Failed to fetch event by slug %s: %s", slug, e)

        # If slug approach fails, return empty list
        logger.warning("Could not find markets for slug %s", slug)
        return []

    def filter_markets_by_type(
        self, markets: list[MarketData], market_types: list[str] | None = None
    ) -> dict[str, list[MarketData]]:
        """Filter markets by type and group them.

        Args:
            markets: List of MarketData models
            market_types: List of market types to filter for (e.g., ["moneyline", "spreads", "totals"]).
                         If None, defaults to ["moneyline", "spreads", "totals"]

        Returns:
            Dictionary mapping market type (lowercase) to list of MarketData with that type
        """
        if market_types is None:
            market_types = ["moneyline", "spreads", "totals"]

        # Normalize market types to lowercase for comparison
        market_types_lower = [mt.lower() for mt in market_types]
        filtered: dict[str, list[MarketData]] = {mt: [] for mt in market_types_lower}

        for market in markets:
            # Use sportsMarketType field (the actual field name in Polymarket API)
            market_type = market.sportsMarketType
            if not market_type:
                # Fallback to other possible field names (from extra fields)
                market_dict = market.model_dump()
                market_type = market_dict.get("sportsMarketTypeV2") or market_dict.get(
                    "marketType"
                )

            if market_type:
                market_type_lower = market_type.lower()
                # Check if this market type matches any of the requested types
                if market_type_lower in market_types_lower:
                    filtered[market_type_lower].append(market)

        return filtered

    def _parse_outcomes_and_prices(
        self, outcomes: str | list[str] | None, outcomePrices: str | list[str] | None
    ) -> tuple[list[str], list[float]] | None:
        """Parse outcomes and outcomePrices from JSON strings or lists.

        Expected order:
        - For totals: ["Over", "Under"] with corresponding prices
        - For moneyline/spreads: [Away, Home] with corresponding prices

        Args:
            outcomes: Outcomes as JSON string or list
            outcomePrices: Outcome prices as JSON string or list (must match outcomes order)

        Returns:
            Tuple of (outcomes_list, prices_list) or None if parsing fails.
            The order is preserved from the input.
        """
        if outcomes is None or outcomePrices is None:
            return None

        # Parse outcomes
        if isinstance(outcomes, str):
            try:
                outcomes_list = json.loads(outcomes)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse outcomes JSON: {outcomes}")
                return None
        else:
            outcomes_list = list(outcomes)

        # Parse outcomePrices
        if isinstance(outcomePrices, str):
            try:
                prices_list = json.loads(outcomePrices)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse outcomePrices JSON: {outcomePrices}")
                return None
        else:
            prices_list = list(outcomePrices)

        # Validate lengths match
        if len(outcomes_list) != len(prices_list):
            logger.warning(
                f"Mismatched outcomes/prices length: outcomes={len(outcomes_list)}, "
                f"prices={len(prices_list)}"
            )
            return None

        if len(outcomes_list) < 2:
            logger.warning(f"Expected at least 2 outcomes, got {len(outcomes_list)}")
            return None

        # Convert prices to floats
        try:
            prices_float = [float(p) for p in prices_list]
        except (ValueError, TypeError) as e:
            logger.warning(
                f"Failed to convert prices to floats: {prices_list}, error: {e}"
            )
            return None

        if len(outcomes_list) != len(prices_float) or len(outcomes_list) < 2:
            return None

        # Validate prices are between 0 and 1
        for i, price in enumerate(prices_float):
            if price < 0.0 or price > 1.0:
                logger.warning(
                    f"Price {i} out of valid range [0, 1]: {price} for outcome {outcomes_list[i]}"
                )
                return None

        return outcomes_list, prices_float

    def _map_outcomes_to_probabilities(
        self,
        outcomes: list[str],
        prices: list[float],
        market_type: str | None,
        slug: str,
    ) -> tuple[float, float] | None:
        """Map outcomes to probabilities based on market type.

        Polymarket API returns outcomes in fixed order:
        - For totals: [Over, Under] -> prices[0] = over_prob, prices[1] = under_prob
        - For moneyline/spreads: [Away, Home] -> prices[0] = away_prob, prices[1] = home_prob

        Args:
            outcomes: List of outcome names
            prices: List of outcome prices (probabilities) in same order as outcomes
            market_type: Market type ("moneyline", "spreads", "totals")
            slug: Market slug for logging

        Returns:
            For totals: (over_probability, under_probability)
            For moneyline/spreads: (home_probability, away_probability)
            None if mapping fails
        """
        if len(outcomes) != len(prices) or len(outcomes) < 2:
            return None

        if market_type == "totals":
            # For totals: outcomes order is [Over, Under]
            # prices[0] = over_probability, prices[1] = under_probability
            if len(outcomes) != 2:
                logger.warning(
                    f"Expected 2 outcomes for totals market {slug}, got {len(outcomes)}: {outcomes}"
                )
                return None

            over_prob = prices[0]
            under_prob = prices[1]
            return over_prob, under_prob
        else:
            if len(outcomes) != 2:
                logger.warning(
                    f"Expected 2 outcomes for {market_type} market {slug}, got {len(outcomes)}: {outcomes}"
                )
                return None

            normalized = [o.strip().lower() for o in outcomes]
            if set(normalized) == {"yes", "no"}:
                # A binary "Will <team> win?" soccer market cannot supply a
                # two-sided moneyline on its own: "No" includes draws and is
                # therefore not the opponent's win probability. World Cup event
                # moneylines are assembled from the two teams' Yes prices in
                # _fetch_world_cup_moneyline_from_markets().
                logger.warning(
                    "Cannot map single Yes/No market %r to home/away "
                    "moneyline probabilities; use event-level World Cup "
                    "moneyline assembly instead",
                    slug,
                )
                return None

            # Standard [Away, Home] market (e.g. NBA/NFL team-name outcomes):
            # prices[0] = away_probability, prices[1] = home_probability.
            away_prob = prices[0]
            home_prob = prices[1]
            return home_prob, away_prob

    @staticmethod
    def _yes_probability(outcomes: list[str], prices: list[float]) -> float | None:
        """Return the Yes price from a binary Yes/No market, if present."""
        if len(outcomes) != len(prices) or len(outcomes) != 2:
            return None
        normalized = [o.strip().lower() for o in outcomes]
        if set(normalized) != {"yes", "no"}:
            return None
        return prices[normalized.index("yes")]

    @staticmethod
    def _market_team_is_home(market_slug: str) -> bool | None:
        """Whether a soccer per-team market is about the home team.

        World Cup market slugs look like ``fifwc-<home>-<away>-<YYYY>-<MM>-<DD>-<team>``
        (event slug ordered home-away, with the team code appended). Returns
        ``True``/``False`` for the home/away team, or ``None`` if undeterminable.

        Format verified against Polymarket live slugs as of 2026-06-17; if the
        format changes, callers fall back to [away, home] ordering and log a
        warning (see _map_outcomes_to_probabilities).
        """
        parts = market_slug.split("-")
        # Exactly 7 parts (fifwc-<home>-<away>-YYYY-MM-DD-<team>); a different
        # count means the format drifted — bail so the caller logs + falls back.
        if len(parts) != 7 or parts[0] != "fifwc":
            return None
        home_code, away_code, team_code = parts[1], parts[2], parts[-1]
        if team_code == home_code:
            return True
        if team_code == away_code:
            return False
        return None

    async def _fetch_odds_from_slug(
        self, slug: str, market_type: str | None = None
    ) -> MarketOddsData | None:
        """Fetch odds from a market slug using Gamma API outcomes and prices.

        Args:
            slug: Market slug (e.g., "nba-sas-lal-2025-12-10")
            market_type: Optional market type for logging (e.g., "moneyline", "spreads", "totals")

        Returns:
            MarketOddsData model with odds information, or None if market not found
        """
        # Get market data from Gamma API
        try:
            market_dict = await self.get_market_by_slug(slug)
            market = MarketData.model_validate(market_dict)
        except aiohttp.ClientError as e:
            logger.error("Failed to fetch market data for slug %s: %s", slug, e)
            return None
        except Exception as e:
            logger.error(
                "Unexpected error fetching market data for slug %s: %s", slug, e
            )
            return None

        market_id = market.id
        market_type = market_type or market.sportsMarketType

        # Parse outcomes and prices
        parsed = self._parse_outcomes_and_prices(market.outcomes, market.outcomePrices)
        if parsed is None:
            logger.warning(
                f"Could not parse outcomes/prices for market {slug}: "
                f"outcomes={market.outcomes}, outcomePrices={market.outcomePrices}"
            )
            return None

        outcomes, prices = parsed

        # Map outcomes to probabilities (Polymarket API order: totals=[Over,Under], others=[Away,Home])
        probs = self._map_outcomes_to_probabilities(outcomes, prices, market_type, slug)
        if probs is None:
            return None

        if market_type == "totals":
            # For totals: returns (over_probability, under_probability)
            over_prob, under_prob = probs

            # Convert probabilities to decimal odds
            over_odds = 1.0 / over_prob if over_prob > 0 else 1.0
            under_odds = 1.0 / under_prob if under_prob > 0 else 1.0

            logger.debug(
                f"Fetched odds for totals market {slug}: "
                f"over_prob={over_prob:.4f}, under_prob={under_prob:.4f}, "
                f"over_odds={over_odds:.4f}, under_odds={under_odds:.4f}"
            )

            # Map to home/away for MarketOddsData model (home=over, away=under for totals)
            return MarketOddsData(
                market_id=market_id,
                slug=slug,
                market_type=market_type,
                line=market.line,
                home_odds=over_odds,
                away_odds=under_odds,
                home_probability=over_prob,
                away_probability=under_prob,
            )
        else:
            # For moneyline/spreads: returns (home_probability, away_probability)
            home_prob, away_prob = probs

            # Convert probabilities to decimal odds
            home_odds = 1.0 / home_prob if home_prob > 0 else 1.0
            away_odds = 1.0 / away_prob if away_prob > 0 else 1.0

            logger.debug(
                f"Fetched odds for {market_type or 'market'} market {slug}: "
                f"home_prob={home_prob:.4f}, away_prob={away_prob:.4f}, "
                f"home_odds={home_odds:.4f}, away_odds={away_odds:.4f}"
            )

            return MarketOddsData(
                market_id=market_id,
                slug=slug,
                market_type=market_type,
                line=market.line,
                home_odds=home_odds,
                away_odds=away_odds,
                home_probability=home_prob,
                away_probability=away_prob,
            )

    async def _fetch_yes_probability_from_slug(
        self,
        slug: str,
    ) -> tuple[str, float] | None:
        """Fetch a binary Yes/No market and return ``(market_id, yes_price)``."""
        try:
            market_dict = await self.get_market_by_slug(slug)
            market = MarketData.model_validate(market_dict)
        except aiohttp.ClientError as e:
            logger.error("Failed to fetch Yes/No market data for slug %s: %s", slug, e)
            return None
        except Exception as e:
            logger.error(
                "Unexpected error fetching Yes/No market data for slug %s: %s",
                slug,
                e,
            )
            return None

        parsed = self._parse_outcomes_and_prices(market.outcomes, market.outcomePrices)
        if parsed is None:
            logger.warning(
                "Could not parse Yes/No outcomes/prices for market %s: "
                "outcomes=%s, outcomePrices=%s",
                slug,
                market.outcomes,
                market.outcomePrices,
            )
            return None

        yes_prob = self._yes_probability(*parsed)
        if yes_prob is None:
            logger.warning("Market %s is not a binary Yes/No market", slug)
            return None
        return market.id, yes_prob

    async def _fetch_world_cup_moneyline_from_markets(
        self,
        event_slug: str,
        markets: list[MarketData],
    ) -> MarketOddsData | None:
        """Build World Cup home/away moneyline from team Yes prices.

        Polymarket FIFA soccer events expose one "Will <team> win?" Yes/No
        market per outcome. Only the home and away teams' Yes prices represent
        the classic home/away moneyline probabilities; No prices include draws
        and must not be mapped to the opponent.
        """
        home_market: tuple[str, float] | None = None
        away_market: tuple[str, float] | None = None

        for market in markets:
            market_slug = market.slug
            if not market_slug:
                continue
            team_is_home = self._market_team_is_home(market_slug)
            if team_is_home is None:
                logger.debug(
                    "Skipping non-team World Cup moneyline market %s for event %s",
                    market_slug,
                    event_slug,
                )
                continue
            fetched = await self._fetch_yes_probability_from_slug(market_slug)
            if fetched is None:
                continue
            if team_is_home:
                home_market = fetched
            else:
                away_market = fetched

        if home_market is None or away_market is None:
            logger.warning(
                "Incomplete World Cup moneyline markets for %s: home_present=%s, "
                "away_present=%s; omitting moneyline",
                event_slug,
                home_market is not None,
                away_market is not None,
            )
            return None

        home_market_id, home_prob = home_market
        away_market_id, away_prob = away_market
        return MarketOddsData(
            market_id=f"{home_market_id}:{away_market_id}",
            slug=event_slug,
            market_type="moneyline",
            line=None,
            home_odds=1.0 / home_prob if home_prob > 0 else 1.0,
            away_odds=1.0 / away_prob if away_prob > 0 else 1.0,
            home_probability=home_prob,
            away_probability=away_prob,
        )

    async def fetch_odds_from_market(
        self,
        market_url: str | None = None,
        slug: str | None = None,
    ) -> MarketOddsData | None:
        """Fetch odds from Polymarket market (moneyline by default).

        Args:
            market_url: Optional market URL (e.g., "https://polymarket.com/sports/nba/games/week/3/nba-sas-lal-2025-12-10")
            slug: Optional market slug (e.g., "nba-sas-lal-2025-12-10")

        Returns:
            MarketOddsData model with odds information, or None if market not found
        """
        # Extract slug from URL if provided
        if market_url and not slug:
            slug = market_url.split("/")[-1]

        if not slug:
            return None

        return await self._fetch_odds_from_slug(slug, market_type="moneyline")

    async def fetch_odds_from_event(
        self, slug: str, market_types: list[str] | None = None
    ) -> dict[str, MarketOddsData | list[MarketOddsData]]:
        """Fetch odds for all specified market types from an event.

        Args:
            slug: Event slug to query markets for (e.g., "nfl-sea-ne-2026-02-08")
            market_types: List of market types to fetch (e.g., ["moneyline", "spreads", "totals"]).
                         If None, defaults to ["moneyline", "spreads", "totals"]

        Returns:
            Dictionary mapping market type (lowercase) to MarketOddsData or list[MarketOddsData]:
            - "moneyline": Single MarketOddsData (usually only one moneyline market)
            - "spreads": List of MarketOddsData (one for each spread line, e.g., -4.5, -5.5)
            - "totals": List of MarketOddsData (one for each total line, e.g., 46.5, 47.5)
            Example:
            {
                "moneyline": MarketOddsData(market_id="...", home_odds=1.5, line=None, ...),
                "spreads": [
                    MarketOddsData(market_id="...", home_odds=1.8, line=-4.5, ...),
                    MarketOddsData(market_id="...", home_odds=1.9, line=-5.5, ...)
                ],
                "totals": [
                    MarketOddsData(market_id="...", home_odds=2.0, line=46.5, ...)
                ]
            }
        """
        if market_types is None:
            market_types = ["moneyline", "spreads", "totals"]

        # Get all markets for the event
        try:
            markets = await self.get_event_markets(slug)
        except Exception as e:
            logger.error("Failed to fetch markets for slug %s: %s", slug, e)
            return {}

        if not markets:
            logger.debug("No markets found for slug %s", slug)
            return {}

        # Filter markets by type
        filtered_markets = self.filter_markets_by_type(markets, market_types)

        # Fetch odds for each market type
        result: dict[str, MarketOddsData | list[MarketOddsData]] = {}

        for market_type, type_markets in filtered_markets.items():
            if not type_markets:
                logger.debug("No %s markets found for slug %s", market_type, slug)
                continue

            # For moneyline, there's usually only one market, so return a single MarketOddsData
            # For spreads and totals, there can be multiple markets (different lines), so return a list
            if market_type == "moneyline":
                if slug.startswith("fifwc-"):
                    odds_data = await self._fetch_world_cup_moneyline_from_markets(
                        slug, type_markets
                    )
                    if odds_data:
                        result[market_type] = odds_data
                    continue

                # Take the first (and usually only) moneyline market
                market = type_markets[0]
                market_slug = market.slug

                if not market_slug:
                    logger.warning(
                        "Market %s (type: %s) has no slug, skipping",
                        market.id,
                        market_type,
                    )
                    continue

                # Fetch odds for this market
                odds_data = await self._fetch_odds_from_slug(
                    market_slug, market_type=market_type
                )
                if odds_data:
                    result[market_type] = odds_data
                else:
                    logger.debug(
                        "Could not fetch odds for %s market (slug: %s)",
                        market_type,
                        market_slug,
                    )
            else:
                # For spreads and totals, fetch odds for ALL markets
                odds_list: list[MarketOddsData] = []

                for market in type_markets:
                    market_slug = market.slug

                    if not market_slug:
                        logger.warning(
                            "Market %s (type: %s) has no slug, skipping",
                            market.id,
                            market_type,
                        )
                        continue

                    # Fetch odds for this market
                    odds_data = await self._fetch_odds_from_slug(
                        market_slug, market_type=market_type
                    )
                    if odds_data:
                        # Extract line information from the market (for spreads and totals)
                        # and assign it to the odds data. `market.line` is already `float | None`.
                        odds_data = dataclasses.replace(odds_data, line=market.line)
                        odds_list.append(odds_data)
                    else:
                        logger.debug(
                            "Could not fetch odds for %s market (slug: %s)",
                            market_type,
                            market_slug,
                        )

                if odds_list:
                    result[market_type] = odds_list

        return result

    async def fetch(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Fetch Polymarket data."""
        if endpoint == "odds":
            params = params or {}
            market_url = params.get("market_url")
            slug = params.get("slug")
            game_id = params.get("game_id")  # Keep for backward compatibility

            # Only fetch if slug is provided (game_id cannot be used to fetch odds)
            if slug:
                try:
                    all_odds = await self.fetch_odds_from_event(slug)
                    if all_odds:
                        # Return the result directly - fetch_odds_from_event already returns
                        # the correct structure: {"moneyline": MarketOddsData, "spreads": [...], "totals": [...]}
                        return all_odds
                except Exception as e:
                    logger.error("Failed to fetch odds from slug %s: %s", slug, e)

            # Fallback to market_url only (for backward compatibility with market URLs)
            if market_url:
                odds_data = await self.fetch_odds_from_market(market_url=market_url)
                if odds_data:
                    # Return in the same format as fetch_odds_from_event
                    return {
                        "moneyline": odds_data,
                        "spreads": [],
                        "totals": [],
                    }

            # No market found - log warning and return empty (don't crash poll loop)
            # This is expected for games without Polymarket markets
            logger.warning(
                "No Polymarket market found (game_id=%r, market_url=%r, slug=%r). "
                "Slug is required to fetch odds. This is normal if the market doesn't exist yet or team info is missing.",
                game_id,
                market_url,
                slug,
            )
        return {}

    async def close(self) -> None:
        """Close the API (no-op for PolymarketAPI as it doesn't maintain persistent connections)."""
        pass
