"""Demo script showing Polymarket API data and OddsUpdateEvent stream construction.

This script demonstrates:
1. Fetching odds data from Polymarket API using an event slug
2. How the API response is converted to OddsUpdateEvent stream
"""

import asyncio
import json

from dojozero.data.polymarket._api import PolymarketAPI
from dojozero.data.polymarket._store import PolymarketStore
from dojozero.data.polymarket._events import OddsUpdateEvent


async def main():
    """Main demo function."""
    print("=" * 80)
    print("POLYMARKET API DATA & ODDS UPDATE EVENT STREAM DEMO")
    print("=" * 80)
    print()

    # Initialize API and Store
    api = PolymarketAPI()
    store = PolymarketStore(
        store_id="demo_store",
        api=api,
        slug="nfl-sea-ne-2026-02-08",
        sport="nfl",
    )

    # Set up identifier (as would be done by trial metadata)
    store.set_poll_identifier(
        {
            "espn_game_id": "123",
            "home_tricode": "NE",
            "away_tricode": "SEA",
        }
    )

    event_slug = "nfl-sea-ne-2026-02-08"
    print(f"Event Slug: {event_slug}\n")

    # =============================================================================
    # Part 1: Fetch data from API
    # =============================================================================
    print("=" * 80)
    print("PART 1: API Response Data")
    print("=" * 80)
    print()

    try:
        # Fetch odds for all market types
        # The API returns: {"moneyline": MarketOddsData | None, "spreads": [...], "totals": [...]}
        all_odds = await api.fetch("odds", {"slug": event_slug})

        if not all_odds:
            print("⚠ No data returned from API")
            return

        print(f"✓ API returned odds data with {len(all_odds)} market type(s)\n")
        print("API Response Structure (all_odds):")
        print("-" * 80)
        print("Structure: {")
        print('  "moneyline": MarketOddsData | None,')
        print('  "spreads": list[MarketOddsData],')
        print('  "totals": list[MarketOddsData]')
        print("}\n")

        # Show the actual data
        for key in ["moneyline", "spreads", "totals"]:
            value = all_odds.get(key)
            print(f"{key}:")
            if key == "moneyline" and value is not None:
                # Pretty print the MarketOddsData model
                print(json.dumps(value.model_dump(), indent=2, default=str))
            elif key in ["spreads", "totals"] and value:
                print(f"  [{len(value)} item(s)]")
                for idx, item in enumerate(value):
                    print(f"  [{idx}]:")
                    print(json.dumps(item.model_dump(), indent=4, default=str))
            elif value is None or (isinstance(value, list) and len(value) == 0):
                print("  None / Empty")
            print()

    except Exception as e:
        print(f"✗ Error fetching from API: {e}")
        import traceback

        traceback.print_exc()
        return

    print("\n" + "=" * 80)
    print("PART 2: OddsUpdateEvent Stream")
    print("=" * 80)
    print()

    # =============================================================================
    # Part 2: Show how API response (all_odds) is converted to OddsUpdateEvent
    # =============================================================================
    try:
        # Parse the API response (all_odds) into events (as the store would do)
        events = store._parse_api_response(all_odds, identifier=store._poll_identifier)

        if not events:
            print("⚠ No events generated from API response")
            return

        print(f"✓ Generated {len(events)} OddsUpdateEvent(s)\n")

        for event in events:
            if isinstance(event, OddsUpdateEvent):
                print("OddsUpdateEvent:")
                print("-" * 80)
                print(f"  GAME ID: {event.game_id}")
                print(f"  Timestamp: {event.timestamp}")
                print(f"  Home Tricode: {event.home_tricode}")
                print(f"  Away Tricode: {event.away_tricode}")
                print(f"  Provider: {event.odds.provider}")

                # Moneyline odds
                ml = event.odds.moneyline
                if ml:
                    print("\n  Moneyline:")
                    print(f"    Home Odds: {ml.home_odds:.4f}")
                    print(f"    Away Odds: {ml.away_odds:.4f}")
                    print(f"    Home Probability: {ml.home_probability:.4f}")
                    print(f"    Away Probability: {ml.away_probability:.4f}")
                else:
                    print("\n  Moneyline: (none)")

                # Spread odds
                if event.odds.spreads:
                    for sp in event.odds.spreads:
                        print(f"\n  Spread (line {sp.spread}):")
                        print(f"    Home Odds: {sp.home_odds:.4f}")
                        print(f"    Away Odds: {sp.away_odds:.4f}")
                        print(f"    Home Probability: {sp.home_probability:.4f}")
                        print(f"    Away Probability: {sp.away_probability:.4f}")
                else:
                    print("\n  Spread: (none)")

                # Total odds
                if event.odds.totals:
                    for tot in event.odds.totals:
                        print(f"\n  Total (line {tot.total}):")
                        print(f"    Over Odds: {tot.over_odds:.4f}")
                        print(f"    Under Odds: {tot.under_odds:.4f}")
                        print(f"    Over Probability: {tot.over_probability:.4f}")
                        print(f"    Under Probability: {tot.under_probability:.4f}")
                else:
                    print("\n  Total: (none)")

                # Show the event as it would appear in the stream
                print("\n  Event Stream Representation:")
                print("-" * 80)
                print(json.dumps(event.to_dict(), indent=2, default=str))

    except Exception as e:
        print(f"✗ Error parsing API response: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
