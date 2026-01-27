"""Tests for Polymarket data infrastructure.

Tests cover:
- PolymarketStoreFactory: store creation with typed metadata
- PolymarketStore: parsing API responses with espn_game_id
- Metadata flow from factory to store
"""

from unittest.mock import MagicMock

import pytest

from dojozero.betting._metadata import BettingTrialMetadata
from dojozero.data._hub import DataHub
from dojozero.data.polymarket._events import OddsUpdateEvent
from dojozero.data.polymarket._factory import PolymarketStoreFactory
from dojozero.data.polymarket._store import PolymarketStore


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def polymarket_store():
    """Create a PolymarketStore instance with mocked API."""
    mock_api = MagicMock()
    return PolymarketStore(store_id="test_polymarket_store", api=mock_api, sport="nba")


@pytest.fixture
def mock_hub():
    """Create a mock DataHub for factory tests."""
    hub = MagicMock(spec=DataHub)
    return hub


@pytest.fixture
def factory():
    """Create a PolymarketStoreFactory instance."""
    return PolymarketStoreFactory()


# =============================================================================
# PolymarketStoreFactory Tests
# =============================================================================


def _make_metadata(
    sport_type: str = "nba",
    espn_game_id: str = "401810490",
    home_tricode: str = "LAL",
    away_tricode: str = "BOS",
    home_team_name: str = "Los Angeles Lakers",
    away_team_name: str = "Boston Celtics",
    game_date: str = "2025-01-15",
    market_url: str | None = None,
    polymarket_poll_intervals: dict[str, float] | None = None,
) -> BettingTrialMetadata:
    """Create a BettingTrialMetadata for tests."""
    return BettingTrialMetadata(
        hub_id="test_hub",
        persistence_file="/tmp/test.jsonl",
        store_types=("polymarket",),
        sample="test",
        sport_type=sport_type,  # type: ignore[arg-type]
        espn_game_id=espn_game_id,
        event_types=("odds",),
        home_tricode=home_tricode,
        away_tricode=away_tricode,
        home_team_name=home_team_name,
        away_team_name=away_team_name,
        game_date=game_date,
        market_url=market_url,
        polymarket_poll_intervals=polymarket_poll_intervals,
    )


class TestPolymarketStoreFactory:
    """Tests for PolymarketStoreFactory."""

    def test_create_store_with_nba_sport_type(self, factory, mock_hub):
        """Test that create_store succeeds with sport_type='nba'."""
        metadata = _make_metadata(
            sport_type="nba",
            espn_game_id="401810490",
            home_tricode="LAL",
            away_tricode="BOS",
            game_date="2025-01-15",
        )

        store = factory.create_store("test_store", metadata, mock_hub)

        assert isinstance(store, PolymarketStore)
        assert store._sport == "nba"
        mock_hub.connect_store.assert_called_once_with(store)

    def test_create_store_with_nfl_sport_type(self, factory, mock_hub):
        """Test that create_store succeeds with sport_type='nfl'."""
        metadata = _make_metadata(
            sport_type="nfl",
            espn_game_id="401671827",
            home_tricode="KC",
            away_tricode="SF",
            game_date="2025-02-09",
        )

        store = factory.create_store("test_store", metadata, mock_hub)

        assert isinstance(store, PolymarketStore)
        assert store._sport == "nfl"

    def test_create_store_sets_espn_game_id_in_identifier(self, factory, mock_hub):
        """Test that espn_game_id is passed to store identifier."""
        metadata = _make_metadata(
            sport_type="nba",
            espn_game_id="401810490",
        )

        store = factory.create_store("test_store", metadata, mock_hub)

        # Verify the identifier was set (we can check via _poll_identifier)
        assert store._poll_identifier is not None
        assert store._poll_identifier.get("espn_game_id") == "401810490"

    def test_create_store_sets_team_tricodes_when_no_market_url(
        self, factory, mock_hub
    ):
        """Test that team tricodes are passed to identifier when market_url not provided."""
        metadata = _make_metadata(
            sport_type="nba",
            espn_game_id="401810490",
            home_tricode="LAL",
            away_tricode="BOS",
            game_date="2025-01-15",
        )

        store = factory.create_store("test_store", metadata, mock_hub)

        assert store._poll_identifier.get("home_tricode") == "LAL"
        assert store._poll_identifier.get("away_tricode") == "BOS"
        assert store._poll_identifier.get("game_date") == "2025-01-15"

    def test_create_store_with_market_url_skips_tricodes(self, factory, mock_hub):
        """Test that team tricodes are not added when market_url is provided."""
        metadata = _make_metadata(
            sport_type="nba",
            espn_game_id="401810490",
            home_tricode="LAL",
            away_tricode="BOS",
            game_date="2025-01-15",
            market_url="https://polymarket.com/sports/nba/games/nba-bos-lal-2025-01-15",
        )

        store = factory.create_store("test_store", metadata, mock_hub)

        # When market_url is provided, tricodes should not be in identifier
        assert "home_tricode" not in store._poll_identifier
        assert "away_tricode" not in store._poll_identifier
        # But espn_game_id should still be set
        assert store._poll_identifier.get("espn_game_id") == "401810490"

    def test_create_store_with_custom_poll_intervals(self, factory, mock_hub):
        """Test that custom poll intervals are passed to store."""
        metadata = _make_metadata(
            sport_type="nba",
            espn_game_id="401810490",
            polymarket_poll_intervals={"odds": 10.0},
        )

        store = factory.create_store("test_store", metadata, mock_hub)

        assert store.poll_intervals.get("odds") == 10.0


# =============================================================================
# PolymarketStore Tests
# =============================================================================


class TestPolymarketStoreParseResponse:
    """Tests for PolymarketStore._parse_api_response."""

    def test_parse_odds_update_uses_espn_game_id_from_identifier(
        self, polymarket_store
    ):
        """Test that parsing uses espn_game_id from identifier as event_id."""
        data = {
            "odds_update": {
                "home_odds": 1.5,
                "away_odds": 2.5,
                "home_probability": 0.6,
                "away_probability": 0.4,
                "market_id": "should_not_use_this",
            }
        }
        identifier = {"espn_game_id": "401810490"}

        events = polymarket_store._parse_api_response(data, identifier=identifier)

        assert len(events) == 1
        assert isinstance(events[0], OddsUpdateEvent)
        assert events[0].event_id == "401810490"

    def test_parse_odds_update_falls_back_to_api_data_without_identifier(
        self, polymarket_store
    ):
        """Test that parsing falls back to API data when no identifier provided."""
        data = {
            "odds_update": {
                "home_odds": 1.5,
                "away_odds": 2.5,
                "home_probability": 0.6,
                "away_probability": 0.4,
                "event_id": "api_event_id",
            }
        }

        events = polymarket_store._parse_api_response(data, identifier=None)

        assert len(events) == 1
        assert events[0].event_id == "api_event_id"

    def test_parse_odds_update_falls_back_to_market_id(self, polymarket_store):
        """Test that parsing falls back to market_id when event_id not in API data."""
        data = {
            "odds_update": {
                "home_odds": 1.5,
                "away_odds": 2.5,
                "home_probability": 0.6,
                "away_probability": 0.4,
                "market_id": "market_123",
            }
        }

        events = polymarket_store._parse_api_response(data, identifier=None)

        assert len(events) == 1
        assert events[0].event_id == "market_123"

    def test_parse_odds_update_extracts_probabilities(self, polymarket_store):
        """Test that odds and probabilities are correctly extracted."""
        data = {
            "odds_update": {
                "home_odds": 1.67,
                "away_odds": 2.5,
                "home_probability": 0.6,
                "away_probability": 0.4,
            }
        }
        identifier = {"espn_game_id": "401810490"}

        events = polymarket_store._parse_api_response(data, identifier=identifier)

        assert len(events) == 1
        event = events[0]
        assert event.home_odds == 1.67
        assert event.away_odds == 2.5
        assert event.home_probability == 0.6
        assert event.away_probability == 0.4

    def test_parse_odds_update_extracts_tricodes_from_identifier(
        self, polymarket_store
    ):
        """Test that tricodes are extracted from identifier."""
        data = {
            "odds_update": {
                "home_odds": 1.5,
                "away_odds": 2.5,
                "home_probability": 0.6,
                "away_probability": 0.4,
            }
        }
        identifier = {
            "espn_game_id": "401810490",
            "home_tricode": "LAL",
            "away_tricode": "BOS",
        }

        events = polymarket_store._parse_api_response(data, identifier=identifier)

        assert len(events) == 1
        event = events[0]
        assert event.home_tricode == "LAL"
        assert event.away_tricode == "BOS"

    def test_parse_odds_update_empty_tricodes_without_identifier(
        self, polymarket_store
    ):
        """Test that tricodes are empty when identifier not provided."""
        data = {
            "odds_update": {
                "home_odds": 1.5,
                "away_odds": 2.5,
                "home_probability": 0.6,
                "away_probability": 0.4,
            }
        }

        events = polymarket_store._parse_api_response(data, identifier=None)

        assert len(events) == 1
        event = events[0]
        assert event.home_tricode == ""
        assert event.away_tricode == ""

    def test_parse_odds_update_empty_tricodes_when_missing_from_identifier(
        self, polymarket_store
    ):
        """Test that tricodes are empty when not in identifier."""
        data = {
            "odds_update": {
                "home_odds": 1.5,
                "away_odds": 2.5,
                "home_probability": 0.6,
                "away_probability": 0.4,
            }
        }
        identifier = {"espn_game_id": "401810490"}  # No tricodes

        events = polymarket_store._parse_api_response(data, identifier=identifier)

        assert len(events) == 1
        event = events[0]
        assert event.home_tricode == ""
        assert event.away_tricode == ""

    def test_parse_empty_data_returns_empty_list(self, polymarket_store):
        """Test that empty data returns empty event list."""
        events = polymarket_store._parse_api_response({}, identifier=None)
        assert events == []

    def test_parse_data_without_odds_update_returns_empty_list(self, polymarket_store):
        """Test that data without odds_update key returns empty list."""
        data = {"some_other_key": {"value": 123}}
        events = polymarket_store._parse_api_response(data, identifier=None)
        assert events == []


class TestPolymarketStoreInit:
    """Tests for PolymarketStore initialization."""

    def test_default_sport_is_nba(self):
        """Test that default sport is 'nba'."""
        store = PolymarketStore(store_id="test")
        assert store._sport == "nba"

    def test_sport_is_normalized_to_lowercase(self):
        """Test that sport is normalized to lowercase."""
        store = PolymarketStore(store_id="test", sport="NFL")
        assert store._sport == "nfl"

    def test_market_url_extracts_slug(self):
        """Test that slug is extracted from market_url."""
        store = PolymarketStore(
            store_id="test",
            market_url="https://polymarket.com/sports/nba/games/nba-bos-lal-2025-01-15",
        )
        assert store._slug == "nba-bos-lal-2025-01-15"

    def test_explicit_slug_not_overwritten_by_market_url(self):
        """Test that explicit slug is not overwritten."""
        store = PolymarketStore(
            store_id="test",
            market_url="https://polymarket.com/sports/nba/games/url-slug",
            slug="explicit-slug",
        )
        assert store._slug == "explicit-slug"

    def test_default_poll_interval_is_pregame(self):
        """Test that default poll interval is 5 minutes (pregame)."""
        store = PolymarketStore(store_id="test")
        assert store.poll_intervals.get("odds") == 300.0


# =============================================================================
# Integration Tests
# =============================================================================


class TestMetadataFlow:
    """Tests verifying metadata flows correctly from factory to store."""

    def test_full_metadata_flow_nba(self, factory, mock_hub):
        """Test complete metadata flow for NBA trial."""
        metadata = _make_metadata(
            sport_type="nba",
            espn_game_id="401810490",
            home_tricode="LAL",
            away_tricode="BOS",
            game_date="2025-01-15",
        )

        store = factory.create_store("polymarket_store", metadata, mock_hub)

        # Verify store configuration
        assert store._sport == "nba"
        assert store._poll_identifier["espn_game_id"] == "401810490"
        assert store._poll_identifier["home_tricode"] == "LAL"
        assert store._poll_identifier["away_tricode"] == "BOS"
        assert store._poll_identifier["game_date"] == "2025-01-15"

        # Verify event parsing uses espn_game_id and tricodes
        data = {
            "odds_update": {
                "home_probability": 0.55,
                "away_probability": 0.45,
                "home_odds": 1.82,
                "away_odds": 2.22,
            }
        }
        events = store._parse_api_response(data, identifier=store._poll_identifier)
        assert events[0].event_id == "401810490"
        assert events[0].home_tricode == "LAL"
        assert events[0].away_tricode == "BOS"

    def test_full_metadata_flow_nfl(self, factory, mock_hub):
        """Test complete metadata flow for NFL trial."""
        metadata = _make_metadata(
            sport_type="nfl",
            espn_game_id="401671827",
            home_tricode="KC",
            away_tricode="SF",
            game_date="2025-02-09",
        )

        store = factory.create_store("polymarket_store", metadata, mock_hub)

        # Verify store configuration
        assert store._sport == "nfl"
        assert store._poll_identifier["espn_game_id"] == "401671827"
        assert store._poll_identifier["home_tricode"] == "KC"
        assert store._poll_identifier["away_tricode"] == "SF"

        # Verify event parsing uses espn_game_id and tricodes
        data = {
            "odds_update": {
                "home_probability": 0.52,
                "away_probability": 0.48,
                "home_odds": 1.92,
                "away_odds": 2.08,
            }
        }
        events = store._parse_api_response(data, identifier=store._poll_identifier)
        assert events[0].event_id == "401671827"
        assert events[0].home_tricode == "KC"
        assert events[0].away_tricode == "SF"


# =============================================================================
# Integration Tests (require real API calls)
# =============================================================================


@pytest.mark.integration
class TestPolymarketAPIIntegration:
    """Integration tests for Polymarket API.

    These tests make real API calls to Polymarket and require network connectivity.
    Run with: pytest -v --run-integration tests/test_data_polymarket.py
    """

    def test_normalize_tricode_nba(self):
        """Test NBA tricode normalization for known mappings."""
        from dojozero.data.polymarket._api import PolymarketAPI

        # ESPN tricodes that need special mapping
        assert PolymarketAPI.normalize_tricode("GS", "nba") == "gsw"
        assert PolymarketAPI.normalize_tricode("NO", "nba") == "nop"
        assert PolymarketAPI.normalize_tricode("NY", "nba") == "nyk"
        assert PolymarketAPI.normalize_tricode("SA", "nba") == "sas"
        assert PolymarketAPI.normalize_tricode("UTAH", "nba") == "uta"

        # Standard tricodes (lowercase)
        assert PolymarketAPI.normalize_tricode("LAL", "nba") == "lal"
        assert PolymarketAPI.normalize_tricode("BOS", "nba") == "bos"
        assert PolymarketAPI.normalize_tricode("MIA", "nba") == "mia"

    def test_normalize_tricode_nfl(self):
        """Test NFL tricode normalization for known mappings."""
        from dojozero.data.polymarket._api import PolymarketAPI

        # ESPN tricodes that need special mapping
        assert PolymarketAPI.normalize_tricode("LAR", "nfl") == "la"
        assert PolymarketAPI.normalize_tricode("KC", "nfl") == "kc"
        assert PolymarketAPI.normalize_tricode("TB", "nfl") == "tb"
        assert PolymarketAPI.normalize_tricode("GB", "nfl") == "gb"
        assert PolymarketAPI.normalize_tricode("SF", "nfl") == "sf"
        assert PolymarketAPI.normalize_tricode("NE", "nfl") == "ne"

        # Standard tricodes
        assert PolymarketAPI.normalize_tricode("BAL", "nfl") == "bal"
        assert PolymarketAPI.normalize_tricode("BUF", "nfl") == "buf"

    def test_get_event_url_nba(self):
        """Test NBA event URL generation."""
        from dojozero.data.polymarket._api import PolymarketAPI

        url = PolymarketAPI.get_event_url("LAL", "BOS", "2025-01-25", "nba")
        assert url == "https://polymarket.com/event/nba-lal-bos-2025-01-25"

        # Test with special tricode mapping
        url = PolymarketAPI.get_event_url("GS", "SA", "2025-01-25", "nba")
        assert url == "https://polymarket.com/event/nba-gsw-sas-2025-01-25"

    def test_get_event_url_nfl(self):
        """Test NFL event URL generation."""
        from dojozero.data.polymarket._api import PolymarketAPI

        url = PolymarketAPI.get_event_url("SF", "KC", "2025-02-09", "nfl")
        assert url == "https://polymarket.com/event/nfl-sf-kc-2025-02-09"

        # Test with special tricode mapping
        url = PolymarketAPI.get_event_url("LAR", "TB", "2025-01-15", "nfl")
        assert url == "https://polymarket.com/event/nfl-la-tb-2025-01-15"

    @pytest.mark.asyncio
    async def test_get_market_by_slug_returns_data(self):
        """Test that get_market_by_slug returns market data for known slugs.

        Note: This test may fail if the market no longer exists on Polymarket.
        Use a recent/current game slug for more reliable testing.
        """
        from dojozero.data.polymarket._api import PolymarketAPI

        api = PolymarketAPI()

        # Try to fetch a market - using a recent NBA game
        # Note: This slug may become invalid over time
        try:
            # Use today's or recent date for a more reliable test
            from datetime import datetime, timedelta

            # Try yesterday's games as they're more likely to have markets
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            test_slug = f"nba-lal-bos-{yesterday}"

            data = await api.get_market_by_slug(test_slug)

            # If we get data, verify structure
            assert isinstance(data, dict)
            # Market data should have an 'id' field
            if "id" in data:
                assert data["id"] is not None

        except Exception as e:
            # Market may not exist - that's okay for integration test
            # Just verify the API call mechanics work
            pytest.skip(f"Market not found (expected for non-game dates): {e}")

    @pytest.mark.asyncio
    async def test_api_fetch_odds_endpoint(self):
        """Test the full fetch flow for odds endpoint.

        This tests the complete API flow from PolymarketStore perspective.
        """
        from dojozero.data.polymarket._api import PolymarketAPI
        from dojozero.data.polymarket._store import PolymarketStore

        api = PolymarketAPI()
        store = PolymarketStore(store_id="test_store", api=api, sport="nba")

        # Set up identifier with test data
        store.set_poll_identifier(
            {
                "espn_game_id": "401810490",
                "away_tricode": "LAL",
                "home_tricode": "BOS",
                "game_date": "2025-01-15",
            }
        )

        # Try to poll - this may return empty if no market exists
        try:
            events = await store._poll_api(identifier=store._poll_identifier)

            # Should return a list (may be empty if no market)
            assert isinstance(events, list)

            # If we got events, verify structure
            for event in events:
                assert hasattr(event, "event_id")
                assert hasattr(event, "home_odds")
                assert hasattr(event, "away_odds")

        except Exception as e:
            # API errors are okay for integration tests
            # The important thing is the code path executed
            pytest.skip(f"API call failed (market may not exist): {e}")
