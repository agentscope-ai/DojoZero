"""Polymarket ExternalAPI implementation.

Polymarket API documentation: https://docs.polymarket.com/quickstart/overview

Team abbreviation mappings are sourced from Polymarket's Teams API:
- API docs: https://docs.polymarket.com/api-reference/sports/list-teams
- NBA teams: https://gamma-api.polymarket.com/teams?league=nba
- NFL teams: https://gamma-api.polymarket.com/teams?league=nfl
"""

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
    """Polymarket API implementation using Gamma API and CLOB API."""

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
            sport: Sport type ("nba" or "nfl") for sport-specific mappings

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
    def get_event_url(
        away_tricode: str, home_tricode: str, game_date: str, sport: str = "nba"
    ) -> str:
        """Generate Polymarket event page URL.

        Args:
            away_tricode: Away team ESPN tricode (e.g., "LAL", "LAR")
            home_tricode: Home team ESPN tricode (e.g., "BOS", "SEA")
            game_date: Game date in YYYY-MM-DD format
            sport: Sport type ("nba" or "nfl")

        Returns:
            Polymarket event URL (e.g., "https://polymarket.com/event/nba-lal-bos-2025-01-25")
        """
        away_code = PolymarketAPI.normalize_tricode(away_tricode, sport)
        home_code = PolymarketAPI.normalize_tricode(home_tricode, sport)
        slug = f"{sport.lower()}-{away_code}-{home_code}-{game_date}"
        return f"https://polymarket.com/event/{slug}"

    def __init__(self, api_key: str | None = None):
        """Initialize Polymarket API.

        Args:
            api_key: Optional API key (for real implementation)
        """
        self.api_key = api_key
        self._gamma_base_url = "https://gamma-api.polymarket.com"
        self._clob_base_url = "https://clob.polymarket.com"
        self._clob_client = None  # Lazy initialization

    def _get_clob_client(self):
        """Lazy initialization of CLOB client."""
        if self._clob_client is None:
            try:
                from py_clob_client.client import ClobClient

                self._clob_client = ClobClient(self._clob_base_url)
            except ImportError:
                raise ImportError(
                    "py-clob-client is required for Polymarket API. "
                    "Install it with: pip install py-clob-client"
                )
        return self._clob_client

    async def get_market_by_slug(self, slug: str) -> dict[str, Any]:
        """Query Gamma API for market data by slug.

        Args:
            slug: Market slug (e.g., "nba-sas-lal-2025-12-10")

        Returns:
            Market data dictionary with 'id' and 'clobTokenIds'
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

    def _parse_tokens(self, tokens: str | list[str] | None) -> list[str]:
        """Parse tokens from stringified JSON list to Python list if needed.

        Args:
            tokens: Tokens as string, list, or None

        Returns:
            List of token IDs
        """
        if tokens is None:
            return []
        if isinstance(tokens, str):
            try:
                return json.loads(tokens)
            except json.JSONDecodeError:
                return [tokens] if tokens else []
        return list(tokens) if tokens else []

    def get_orderbook_data(self, token_id: str) -> dict[str, Any] | None:
        """Get orderbook data for a given token ID using CLOB API.

        Args:
            token_id: Token ID to query

        Returns:
            Dictionary with 'midpoint', 'price', 'order_book', 'order_books', or None if not found
        """
        client = self._get_clob_client()
        try:
            from py_clob_client.clob_types import BookParams
            from py_clob_client.exceptions import PolyApiException

            return {
                "midpoint": client.get_midpoint(token_id),
                "price": client.get_price(token_id, side="BUY"),
                "order_book": client.get_order_book(token_id),
                "order_books": client.get_order_books([BookParams(token_id=token_id)]),
            }
        except PolyApiException as e:
            # Handle 404 or other errors
            if e.status_code == 404:
                return None  # No orderbook exists
            raise
        except Exception:
            # Re-raise other exceptions
            raise

    def find_active_token(
        self, tokens: list[str]
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Try each token until we find one with an active orderbook.

        Args:
            tokens: List of token IDs to try

        Returns:
            Tuple of (token_id, orderbook_data) or (None, None) if none found
        """
        for token in tokens:
            try:
                data = self.get_orderbook_data(token)
                if data is not None:
                    return token, data
            except Exception as e:
                logger.debug("Token %s has no active orderbook: %s", token, e)
                continue  # Try next token
        return None, None

    def find_all_active_tokens(
        self, tokens: list[str]
    ) -> list[tuple[str, dict[str, Any]]]:
        """Find all tokens with active orderbooks.

        Args:
            tokens: List of token IDs to try

        Returns:
            List of (token_id, orderbook_data) tuples for all active tokens
        """
        active_tokens = []
        for token in tokens:
            try:
                data = self.get_orderbook_data(token)
                if data is not None:
                    active_tokens.append((token, data))
            except Exception as e:
                logger.debug("Token %s has no active orderbook: %s", token, e)
                continue  # Try next token
        return active_tokens

    def _extract_numeric_value(self, value: Any) -> float | None:
        """Extract numeric value from midpoint/price, handling dict format.

        Args:
            value: Can be a number, dict with 'mid' key, or string

        Returns:
            Numeric value or None if not extractable
        """
        if value is None:
            return None

        # Handle dict format like {'mid': '0.135'}
        if isinstance(value, dict):
            mid_value = value.get("mid")
            if mid_value is not None:
                try:
                    return float(mid_value)
                except (ValueError, TypeError):
                    return None
            return None

        # Handle string format
        if isinstance(value, str):
            try:
                return float(value)
            except (ValueError, TypeError):
                return None

        # Handle numeric format
        if isinstance(value, (int, float)):
            return float(value)

        return None

    async def _fetch_odds_from_slug(
        self, slug: str, market_type: str | None = None
    ) -> MarketOddsData | None:
        """Fetch odds from a market slug.

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
        tokens = self._parse_tokens(market.clobTokenIds)

        if not tokens:
            return None

        # Find all active tokens and get orderbook data
        active_tokens = self.find_all_active_tokens(tokens)

        if len(active_tokens) < 2:
            logger.debug(
                f"Found {len(active_tokens)} active tokens for market {slug} "
                f"(type: {market_type}). Market may not be fully active yet."
            )
            if not active_tokens:
                return None  # No active tokens - market not ready
            # For markets with fewer than 2 tokens, we can't determine both sides
            # This might happen for spread/total markets with different structures
            return None

        # Extract odds from orderbooks
        # Each token's midpoint/price represents the probability of that outcome
        # Convert probability to decimal odds: odds = 1 / probability
        token1_id, orderbook1 = active_tokens[0]
        token2_id, orderbook2 = active_tokens[1]

        # Get midpoint or price for each token
        midpoint1 = orderbook1.get("midpoint")
        midpoint2 = orderbook2.get("midpoint")

        # Extract numeric values
        mid1_value = self._extract_numeric_value(midpoint1)
        mid2_value = self._extract_numeric_value(midpoint2)

        if mid1_value is not None and mid2_value is not None:
            # Convert probabilities to odds
            odds1 = 1.0 / mid1_value if mid1_value > 0 else 1.0
            odds2 = 1.0 / mid2_value if mid2_value > 0 else 1.0
        else:
            # Fallback to price; require valid numeric prices
            price1 = orderbook1.get("price")
            price2 = orderbook2.get("price")

            price1_value = self._extract_numeric_value(price1)
            price2_value = self._extract_numeric_value(price2)

            if price1_value is not None and price2_value is not None:
                odds1 = 1.0 / price1_value if price1_value > 0 else 1.0
                odds2 = 1.0 / price2_value if price2_value > 0 else 1.0
            else:
                logger.warning(
                    f"Missing or non-numeric midpoint/price data for tokens "
                    f"{token1_id} (midpoint={midpoint1!r}, price={price1!r}), "
                    f"{token2_id} (midpoint={midpoint2!r}, price={price2!r})"
                )
                return None

        # Token ordering: slug format is "nba-{away_tricode}-{home_tricode}-{date}"
        # We assume tokens follow slug order: token1 = away, token2 = home
        # TODO: If market metadata provides outcome information, use it to verify/correct this assumption
        logger.debug(
            f"Token ordering for {market_type or 'market'}: token1=away ({token1_id}, prob={mid1_value}, odds={odds1}), "
            f"token2=home ({token2_id}, prob={mid2_value}, odds={odds2})"
        )

        home_prob = (
            mid2_value
            if mid2_value is not None
            else (price2_value if price2_value is not None else 0.0)
        )
        away_prob = (
            mid1_value
            if mid1_value is not None
            else (price1_value if price1_value is not None else 0.0)
        )

        return MarketOddsData(
            market_id=market_id,
            slug=slug,
            market_type=market_type,
            line=None,  # Will be set later from market data if available
            home_odds=odds2,  # token2 = home (computed from probability)
            away_odds=odds1,  # token1 = away (computed from probability)
            home_probability=home_prob,
            away_probability=away_prob,
            token_ids=[token1_id, token2_id],
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
                        line = market.line
                        if line is not None:
                            # Update the odds_data with the line value
                            odds_dict = odds_data.model_dump()
                            odds_dict["line"] = (
                                float(line)
                                if isinstance(line, (int, float, str))
                                else None
                            )
                            odds_data = MarketOddsData(**odds_dict)
                        # If line is None, odds_data already has line=None, so no update needed

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
            event_id = params.get("event_id")  # Keep for backward compatibility

            # If slug is provided, fetch odds for all market types (moneyline, spreads, totals)
            if slug:
                try:
                    all_odds = await self.fetch_odds_from_event(slug)
                    if all_odds:
                        # Return odds for all market types
                        result: dict[str, Any] = {}
                        event_id_value = params.get("event_id") or slug

                        for market_type, odds_data in all_odds.items():
                            # Handle lists (for spreads and totals) vs single values (for moneyline)
                            if isinstance(odds_data, list):
                                # For spreads and totals, create multiple entries
                                # The store will collect all of them into spread_updates/total_updates lists
                                for idx, odds_item in enumerate(odds_data):
                                    odds_dict = odds_item.model_dump()
                                    result[f"odds_update_{market_type}_{idx}"] = {
                                        "event_id": event_id_value,
                                        "market_type": market_type,
                                        **odds_dict,
                                    }
                                # Also create a combined entry for backward compatibility (first one)
                                if odds_data:
                                    first_odds_dict = odds_data[0].model_dump()
                                    result[f"odds_update_{market_type}"] = {
                                        "event_id": event_id_value,
                                        "market_type": market_type,
                                        **first_odds_dict,
                                    }
                            else:
                                # Single MarketOddsData (for moneyline)
                                odds_dict = odds_data.model_dump()
                                result[f"odds_update_{market_type}"] = {
                                    "event_id": event_id_value,
                                    "market_type": market_type,
                                    **odds_dict,
                                }

                        # Also include a general odds_update for backward compatibility (moneyline if available)
                        if "moneyline" in all_odds:
                            ml_odds = all_odds["moneyline"]
                            if isinstance(ml_odds, MarketOddsData):
                                ml_dict = ml_odds.model_dump()
                                result["odds_update"] = {
                                    "event_id": event_id_value,
                                    "market_type": "moneyline",
                                    **ml_dict,
                                }
                        return result
                except Exception as e:
                    logger.error("Failed to fetch odds from slug %s: %s", slug, e)

            # Fallback: if event_id is provided (for backward compatibility), try to use it as slug
            if event_id and not slug:
                try:
                    all_odds = await self.fetch_odds_from_event(event_id)
                    if all_odds:
                        # Return odds for all market types
                        result_fallback: dict[str, Any] = {}

                        for market_type, odds_data in all_odds.items():
                            # Handle lists (for spreads and totals) vs single values (for moneyline)
                            if isinstance(odds_data, list):
                                # For spreads and totals, create multiple entries
                                for idx, odds_item in enumerate(odds_data):
                                    odds_dict = odds_item.model_dump()
                                    result_fallback[
                                        f"odds_update_{market_type}_{idx}"
                                    ] = {
                                        "event_id": event_id,
                                        "market_type": market_type,
                                        **odds_dict,
                                    }
                                # Also create a combined entry for backward compatibility (first one)
                                if odds_data:
                                    first_odds_dict = odds_data[0].model_dump()
                                    result_fallback[f"odds_update_{market_type}"] = {
                                        "event_id": event_id,
                                        "market_type": market_type,
                                        **first_odds_dict,
                                    }
                            else:
                                # Single MarketOddsData (for moneyline)
                                odds_dict = odds_data.model_dump()
                                result_fallback[f"odds_update_{market_type}"] = {
                                    "event_id": event_id,
                                    "market_type": market_type,
                                    **odds_dict,
                                }

                        if "moneyline" in all_odds:
                            ml_odds = all_odds["moneyline"]
                            if isinstance(ml_odds, MarketOddsData):
                                ml_dict = ml_odds.model_dump()
                                result_fallback["odds_update"] = {
                                    "event_id": event_id,
                                    "market_type": "moneyline",
                                    **ml_dict,
                                }
                        return result_fallback
                except Exception as e:
                    logger.error(
                        "Failed to fetch odds from event_id %s: %s", event_id, e
                    )

            # Fallback to market_url or slug (for backward compatibility)
            if market_url or slug:
                odds_data = await self.fetch_odds_from_market(
                    market_url=market_url, slug=slug
                )
                if odds_data:
                    # Use event_id from params if provided, otherwise use market_id
                    if not event_id:
                        event_id = odds_data.market_id
                    odds_dict = odds_data.model_dump()
                    return {
                        "odds_update": {
                            "event_id": event_id,
                            **odds_dict,
                        }
                    }

            # No market found - log warning and return empty (don't crash poll loop)
            # This is expected for games without Polymarket markets
            logger.warning(
                "No Polymarket market found for event_id=%r (market_url=%r, slug=%r). "
                "This is normal if the market doesn't exist yet or team info is missing.",
                event_id,
                market_url,
                slug,
            )
        return {}

    async def place_bet(
        self, market_id: str, outcome: str, amount: float
    ) -> dict[str, Any]:
        """Place a bet on Polymarket.

        Args:
            market_id: Market identifier
            outcome: Outcome to bet on (e.g., "Yes", "No")
            amount: Bet amount

        Returns:
            Bet confirmation
        """
        # Simulated bet placement
        return {
            "bet_id": f"bet_{market_id}_{outcome}",
            "market_id": market_id,
            "outcome": outcome,
            "amount": amount,
            "status": "placed",
        }

    async def close(self) -> None:
        """Close the API (no-op for PolymarketAPI as it doesn't maintain persistent connections)."""
        pass
