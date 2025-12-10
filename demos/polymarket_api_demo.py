"""Demo script for querying Polymarket market data via Gamma API and CLOB API."""

import json
import requests
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BookParams
from py_clob_client.exceptions import PolyApiException


market_url = "https://polymarket.com/sports/nba/games/week/3/nba-sas-lal-2025-12-10"


def get_market_by_slug(slug: str) -> dict:
    """Query Gamma API for market data by slug."""
    url = f"https://gamma-api.polymarket.com/markets/slug/{slug}"
    response = requests.get(url)
    response.raise_for_status()
    return response.json()


def parse_tokens(tokens) -> list:
    """Parse tokens from stringified JSON list to Python list if needed."""
    if isinstance(tokens, str):
        return json.loads(tokens)
    return tokens or []


def get_orderbook_data(client: ClobClient, token_id: str) -> dict:
    """Get all orderbook data for a given token ID."""
    return {
        "midpoint": client.get_midpoint(token_id),
        "price": client.get_price(token_id, side="BUY"),
        "order_book": client.get_order_book(token_id),
        "order_books": client.get_order_books([BookParams(token_id=token_id)]),
    }


def find_active_token(client: ClobClient, tokens: list) -> tuple[str, dict] | tuple[None, None]:
    """Try each token until we find one with an active orderbook."""
    for token in tokens:
        try:
            print(f"Trying token: {token}")
            data = get_orderbook_data(client, token)
            print(f"✓ Found orderbook for token: {token}")
            return token, data
        except PolyApiException as e:
            if e.status_code == 404:
                print(f"  No orderbook exists for token {token}, trying next...")
                continue
            else:
                print(f"  Error for token {token}: {e}")
                raise
    
    return None, None


def main():
    # Configuration
    slug = market_url.split("/")[-1]
    
    # Get market data from Gamma API
    print(f"Fetching market data for slug: {slug}")
    market = get_market_by_slug(slug)
    
    market_id = market.get("id")
    tokens = parse_tokens(market.get("clobTokenIds"))
    
    print(f"Market ID: {market_id}")
    print(f"Tokens: {tokens}\n")
    
    # Initialize CLOB client
    client = ClobClient("https://clob.polymarket.com")
    
    # Check API status
    ok = client.get_ok()
    server_time = client.get_server_time()
    print(f"API Status: {ok}, Server Time: {server_time}\n")
    
    # Find active token and get orderbook data
    token_id, orderbook_data = find_active_token(client, tokens)
    
    if token_id is None:
        print("⚠ Warning: No orderbook found for any token. The market may not have active trading yet.")
        return
    
    # Display results
    print(f"\n{'='*60}")
    print(f"Orderbook Data for Token: {token_id}")
    print(f"{'='*60}")
    print(f"Midpoint: {orderbook_data['midpoint']}")
    print(f"Buy Price: {orderbook_data['price']}")
    print(f"Market: {orderbook_data['order_book'].market}")
    print(f"Order Books Count: {len(orderbook_data['order_books'])}")


if __name__ == "__main__":
    main()
