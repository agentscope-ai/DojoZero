"""Polymarket ExternalAPI implementation."""

import json
import logging
from typing import Any

import aiohttp

from agentx.data._stores import ExternalAPI


logger = logging.getLogger("agentx.data.polymarket._api")


class PolymarketAPI(ExternalAPI):
    """Polymarket API implementation using Gamma API and CLOB API."""
    
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
    
    def find_active_token(self, tokens: list[str]) -> tuple[str | None, dict[str, Any] | None]:
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
            except Exception:
                continue  # Try next token
        return None, None
    
    def find_all_active_tokens(self, tokens: list[str]) -> list[tuple[str, dict[str, Any]]]:
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
            except Exception:
                continue  # Try next token
        return active_tokens
    
    async def fetch_odds_from_market(
        self,
        market_url: str | None = None,
        slug: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetch odds from Polymarket market.
        
        Args:
            market_url: Optional market URL (e.g., "https://polymarket.com/sports/nba/games/week/3/nba-sas-lal-2025-12-10")
            slug: Optional market slug (e.g., "nba-sas-lal-2025-12-10")
            
        Returns:
            Dictionary with market_id, home_odds, away_odds, or None if market not found
        """
        # Extract slug from URL if provided
        if market_url and not slug:
            slug = market_url.split("/")[-1]
        
        if not slug:
            return None
        
        # Get market data from Gamma API
        try:
            market = await self.get_market_by_slug(slug)
        except Exception:
            logger.error(f"Failed to fetch market data for slug: {slug}")
            return None
        
        market_id = market.get("id")
        tokens = self._parse_tokens(market.get("clobTokenIds"))
        
        if not tokens:
            return None
        
        # Find all active tokens and get orderbook data
        # For moneyline markets, we require exactly 2 tokens (one for each outcome)
        active_tokens = self.find_all_active_tokens(tokens)
        
        if len(active_tokens) != 2:
            # Require exactly 2 active tokens to determine both home and away odds
            logger.error(f"Expected exactly 2 active tokens for moneyline market, found {len(active_tokens)}. Market may not be fully active yet.")
            if not active_tokens:
                return None  # No active tokens - market not ready
            raise ValueError(
                f"Expected exactly 2 active tokens for moneyline market, "
                f"found {len(active_tokens)}. Market may not be fully active yet."
            )
        
        # Extract odds from orderbooks
        # Each token's midpoint/price represents the probability of that outcome
        # Convert probability to decimal odds: odds = 1 / probability
        token1_id, orderbook1 = active_tokens[0]
        token2_id, orderbook2 = active_tokens[1]
        
        def extract_numeric_value(value: Any) -> float | None:
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
        
        # Get midpoint or price for each token
        midpoint1 = orderbook1.get("midpoint")
        midpoint2 = orderbook2.get("midpoint")
        
        # Extract numeric values
        mid1_value = extract_numeric_value(midpoint1)
        mid2_value = extract_numeric_value(midpoint2)
        
        if mid1_value is not None and mid2_value is not None:
            # Convert probabilities to odds
            odds1 = 1.0 / mid1_value if mid1_value > 0 else 1.0
            odds2 = 1.0 / mid2_value if mid2_value > 0 else 1.0
        else:
            # Fallback to price; require valid numeric prices
            price1 = orderbook1.get("price")
            price2 = orderbook2.get("price")
            
            price1_value = extract_numeric_value(price1)
            price2_value = extract_numeric_value(price2)
            
            if price1_value is not None and price2_value is not None:
                odds1 = 1.0 / price1_value if price1_value > 0 else 1.0
                odds2 = 1.0 / price2_value if price2_value > 0 else 1.0
            else:
                raise ValueError(
                    f"Missing or non-numeric midpoint/price data for tokens "
                    f"{token1_id} (midpoint={midpoint1!r}, price={price1!r}), "
                    f"{token2_id} (midpoint={midpoint2!r}, price={price2!r})"
                )

        # Token ordering: slug format is "nba-{away_tricode}-{home_tricode}-{date}"
        # We assume tokens follow slug order: token1 = away, token2 = home
        # TODO: If market metadata provides outcome information, use it to verify/correct this assumption
        logger.info(
            f"Token ordering: token1=away ({token1_id}, prob={mid1_value}, odds={odds1}), "
            f"token2=home ({token2_id}, prob={mid2_value}, odds={odds2})"
        )
        return {
            "market_id": market_id,
            "token_ids": [token1_id, token2_id],
            "home_odds": odds2,  # token2 = home (computed from probability)
            "away_odds": odds1,  # token1 = away (computed from probability)
            "home_probability": mid2_value if mid2_value is not None else (price2_value if price2_value is not None else 0.0),
            "away_probability": mid1_value if mid1_value is not None else (price1_value if price1_value is not None else 0.0),
        }
    
    async def fetch(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fetch Polymarket data."""
        if endpoint == "odds":
            # Query current odds for an event (for broker)
            market_url = params.get("market_url") if params else None
            slug = params.get("slug") if params else None
            
            if market_url or slug:
                odds_data = await self.fetch_odds_from_market(market_url=market_url, slug=slug)
                if odds_data:
                    # Use event_id from params if provided, otherwise use market_id
                    event_id = params.get("event_id") if params else None
                    if not event_id:
                        event_id = odds_data["market_id"]
                    return {
                        "odds_update": {
                            "event_id": event_id,
                            "market_id": odds_data["market_id"],
                            "home_odds": odds_data["home_odds"],
                            "away_odds": odds_data["away_odds"],
                            "home_probability": odds_data.get("home_probability", 0.0),
                            "away_probability": odds_data.get("away_probability", 0.0),
                        }
                    }
            
            # Do not fall back to mock odds; surface an explicit error instead
            event_id = params.get("event_id") if params else None
            raise RuntimeError(
                f"Failed to fetch Polymarket odds for event_id={event_id!r}, "
                "no market found for given market_url/slug"
            )
        return {}
    
    async def place_bet(self, market_id: str, outcome: str, amount: float) -> dict[str, Any]:
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

