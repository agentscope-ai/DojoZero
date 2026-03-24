"""Tests for BaseTrialMetadata dict-like access compatibility."""

from dojozero.betting._metadata import BettingTrialMetadata


def _make_metadata() -> BettingTrialMetadata:
    return BettingTrialMetadata(
        hub_id="test_hub",
        persistence_file="test.jsonl",
        store_types=("nba",),
        sample="nba",
        sport_type="nba",
        espn_game_id="401810490",
        event_types=("event.nba_game_update",),
        home_tricode="LAL",
        away_tricode="BOS",
        home_team_name="Los Angeles Lakers",
        away_team_name="Boston Celtics",
        game_date="2026-03-24",
    )


class TestMetadataDictCompat:
    """Ensure metadata dataclass supports dict-like access patterns."""

    def test_get_existing_field(self) -> None:
        m = _make_metadata()
        assert m.get("espn_game_id") == "401810490"
        assert m.get("sport_type") == "nba"

    def test_get_missing_field_returns_default(self) -> None:
        m = _make_metadata()
        assert m.get("nonexistent") is None
        assert m.get("nonexistent", "fallback") == "fallback"

    def test_get_base_class_field(self) -> None:
        m = _make_metadata()
        assert m.get("hub_id") == "test_hub"
        assert m.get("persistence_file") == "test.jsonl"

    def test_contains(self) -> None:
        m = _make_metadata()
        assert "espn_game_id" in m
        assert "sport_type" in m
        assert "nonexistent" not in m

    def test_getitem(self) -> None:
        m = _make_metadata()
        assert m["espn_game_id"] == "401810490"
        assert m["hub_id"] == "test_hub"

    def test_getitem_missing_raises_keyerror(self) -> None:
        m = _make_metadata()
        try:
            m["nonexistent"]
            assert False, "Should have raised KeyError"
        except KeyError:
            pass

    def test_dict_conversion(self) -> None:
        m = _make_metadata()
        d = dict(m)
        assert d["espn_game_id"] == "401810490"
        assert d["sport_type"] == "nba"
        assert d["hub_id"] == "test_hub"
        assert len(d) == len(list(m.keys()))

    def test_iter(self) -> None:
        m = _make_metadata()
        field_names = list(m)
        assert "espn_game_id" in field_names
        assert "hub_id" in field_names

    def test_items(self) -> None:
        m = _make_metadata()
        items = dict(m.items())
        assert items["espn_game_id"] == "401810490"

    def test_optional_field_defaults(self) -> None:
        m = _make_metadata()
        assert m.get("market_url") is None
        assert m.get("nba_poll_intervals") is None
