"""Demo script for querying Polymarket market data via Gamma API and CLOB API."""

import asyncio
import json
import requests
from datetime import datetime
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BookParams
from py_clob_client.exceptions import PolyApiException

from dojozero.data.polymarket._store import PolymarketStore
from dojozero.data.polymarket._api import PolymarketAPI
from dojozero.data.polymarket._events import OddsUpdateEvent
from dojozero.data.nba._events import GameInitializeEvent


market_url = "https://polymarket.com/event/nba-lac-okc-2025-12-17"


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


def find_active_tokens(client: ClobClient, tokens: list) -> list[tuple[str, dict]]:
    """Find all tokens with active orderbooks.

    Returns:
        List of (token_id, orderbook_data) tuples for all active tokens
    """
    active_tokens = []
    for token in tokens:
        try:
            print(f"Trying token: {token}")
            data = get_orderbook_data(client, token)
            print(f"✓ Found orderbook for token: {token}")
            active_tokens.append((token, data))
        except PolyApiException as e:
            if e.status_code == 404:
                print(f"  No orderbook exists for token {token}, trying next...")
                continue
            else:
                print(f"  Error for token {token}: {e}")
                raise

    return active_tokens


def main():
    # Configuration
    slug = market_url.split("/")[-1]

    # Get market data from Gamma API
    print(f"Fetching market data for slug: {slug}")
    market = get_market_by_slug(slug)

    market_id = market.get("id")
    tokens = parse_tokens(market.get("clobTokenIds"))

    print(f"Market ID: {market_id}")
    print(f"Tokens: {tokens}")

    # Inspect market metadata for outcome information
    print(f"\nMarket metadata keys: {list(market.keys())}")
    if "outcomes" in market:
        print(f"Outcomes: {market['outcomes']}")
    if "question" in market:
        print(f"Question: {market['question']}")
    if "description" in market:
        print(f"Description: {market.get('description', 'N/A')[:200]}...")
    print()

    # Initialize CLOB client
    client = ClobClient("https://clob.polymarket.com")

    # Check API status
    ok = client.get_ok()
    server_time = client.get_server_time()
    print(f"API Status: {ok}, Server Time: {server_time}\n")

    # Find all active tokens and get orderbook data
    active_tokens = find_active_tokens(client, tokens)

    if not active_tokens:
        print(
            "⚠ Warning: No orderbook found for any token. The market may not have active trading yet."
        )
        return

    # Display results for all active tokens
    print(f"\n{'=' * 60}")
    print(f"Found {len(active_tokens)} active token(s)")
    print(f"{'=' * 60}\n")

    for i, (token_id, orderbook_data) in enumerate(active_tokens, 1):
        print(f"Token {i}: {token_id}")
        print(f"  Midpoint: {orderbook_data['midpoint']}")
        print(f"  Buy Price: {orderbook_data['price']}")
        print(f"  Market: {orderbook_data['order_book'].market}")
        print(f"  Order Books Count: {len(orderbook_data['order_books'])}")
        if i < len(active_tokens):
            print()  # Blank line between tokens


async def test_polymarket_store_logic(market_url: str):
    """
    Test PolymarketStore's _parse_api_response and _poll_api logic.

    Args:
        market_url: Polymarket market URL
    """
    print("=" * 80)
    print("TESTING POLYMARKET STORE LOGIC")
    print("=" * 80)
    print()

    # Create store instance
    api = PolymarketAPI()
    store = PolymarketStore(api=api, market_url=market_url)

    # Test 1: Fetch odds and create OddsUpdateEvent
    print("TEST 1: Fetch Odds and Create OddsUpdateEvent")
    print("-" * 80)

    try:
        # Poll API for odds
        events = await store._poll_api()

        print(f"✓ Fetched {len(events)} event(s) from Polymarket API")
        print()

        for event in events:
            if isinstance(event, OddsUpdateEvent):
                print("  - OddsUpdateEvent:")
                print(f"    Event ID: {event.event_id}")
                print(
                    f"    Home Odds: {event.home_odds:.4f} (from prob: {event.home_probability:.4f})"
                )
                print(
                    f"    Away Odds: {event.away_odds:.4f} (from prob: {event.away_probability:.4f})"
                )
                print(f"    Event Type: {event.event_type}")
                print()

                # Show event as dict
                event_dict = event.to_dict()
                print("  Event as dictionary:")
                print(f"    {json.dumps(event_dict, indent=4, default=str)}")
                print()
    except Exception as e:
        print(f"✗ Error fetching odds: {e}")
        import traceback

        traceback.print_exc()
        print()

    # Test 2: Parse API response directly
    print("TEST 2: Parse API Response Directly")
    print("-" * 80)

    # Simulate API response format
    test_odds_data = {
        "odds_update": {
            "event_id": "test_event_123",
            "market_id": "test_market",
            "home_odds": 1.85,  # Computed from probability
            "away_odds": 1.95,  # Computed from probability
            "home_probability": 0.5405,  # Raw probability (1 / 1.85)
            "away_probability": 0.5128,  # Raw probability (1 / 1.95)
        }
    }

    parsed_events = store._parse_api_response(test_odds_data)

    print(f"✓ Parsed {len(parsed_events)} event(s) from test data")
    for event in parsed_events:
        if isinstance(event, OddsUpdateEvent):
            print("  - OddsUpdateEvent:")
            print(f"    Event ID: {event.event_id}")
            print(
                f"    Home Odds: {event.home_odds:.4f} (from prob: {event.home_probability:.4f})"
            )
            print(
                f"    Away Odds: {event.away_odds:.4f} (from prob: {event.away_probability:.4f})"
            )
            print(f"    Event Type: {event.event_type}")
    print()

    # Test 3: Test broker integration (if broker is available)
    print("TEST 3: Broker Integration Test")
    print("-" * 80)

    try:
        from dojozero.betting import BrokerOperator
        from dojozero.core import StreamEvent

        # Create a test broker
        broker = BrokerOperator(
            {"actor_id": "test_broker", "initial_balance": "1000"},
            trial_id="demo-trial",
        )
        await broker.start()

        # Get odds from store
        events = await store._poll_api()
        if events:
            odds_event = None
            for event in events:
                if event.__class__.__name__ == "OddsUpdateEvent":
                    odds_event = event
                    break

            if odds_event:
                # Initialize event in broker via GameInitializeEvent
                test_event_id = odds_event.event_id or "test_event"  # type: ignore[attr-defined]

                # Create and send GameInitializeEvent
                game_init_event = StreamEvent(
                    stream_id=f"game_init_{test_event_id}",
                    payload=GameInitializeEvent(
                        event_id=test_event_id,
                        game_id=test_event_id,
                        home_team="Test Home",
                        away_team="Test Away",
                        game_time=datetime.now(),
                    ),
                    emitted_at=datetime.now(),
                )
                await broker.handle_stream_event(game_init_event)
                print(
                    f"✓ Initialized event {test_event_id} in broker via GameInitializeEvent"
                )

                # Create StreamEvent and test broker handling
                stream_event = StreamEvent(
                    stream_id=f"odds_update_{test_event_id}",
                    payload=odds_event,
                    emitted_at=datetime.now(),
                )

                # Handle the event in broker
                await broker.handle_stream_event(stream_event)
                print("✓ Broker handled OddsUpdateEvent successfully")

                # Verify odds were updated
                quote = await broker.get_quote(test_event_id)
                print("  Current odds in broker:")
                print(f"    Home: {quote['home_odds']}")
                print(f"    Away: {quote['away_odds']}")
            else:
                print("  No OddsUpdateEvent found to test broker integration")
        else:
            print("  No events found to test broker integration")

        await broker.stop()
    except ImportError:
        print("  Broker not available (skipping broker integration test)")
    except Exception as e:
        print(f"  Error testing broker integration: {e}")
        import traceback

        traceback.print_exc()
    print()

    print("=" * 80)
    print("ALL TESTS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    # Run the original demo
    main()
    print()

    # Run async tests
    print()
    asyncio.run(test_polymarket_store_logic(market_url))
