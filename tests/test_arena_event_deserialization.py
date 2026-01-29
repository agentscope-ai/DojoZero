"""Tests for span ↔ DataEvent round-trip deserialization.

Verifies that events serialized into OTel spans by DataHub can be
faithfully reconstructed by deserialize_event_from_span().
"""

import json
from datetime import datetime, timezone

from dojozero.core._tracing import SpanData, deserialize_event_from_span
from dojozero.data._models import (
    GameInitializeEvent,
    GameResultEvent,
    OddsUpdateEvent,
)
from dojozero.data._models import (
    MoneylineOdds,
    OddsInfo,
    TeamIdentity,
    VenueInfo,
)


# ---------------------------------------------------------------------------
# Helper: simulate DataHub._emit_event_span() serialization
# ---------------------------------------------------------------------------


def _simulate_span_from_event(event) -> SpanData:
    """Simulate DataHub._emit_event_span() → SpanData.

    Mirrors the serialization logic in DataHub._emit_event_span() (hub.py):
    - Scalar values → direct tag values
    - Dict/list values → JSON-serialized strings
    """
    event_dict = event.to_dict()
    tags: dict = {"sequence": 1, "sport.type": "nba"}

    for key, value in event_dict.items():
        if key in ("event_type", "timestamp"):
            continue
        if isinstance(value, (dict, list)):
            tags[f"event.{key}"] = json.dumps(value, default=str)
        else:
            tags[f"event.{key}"] = value

    return SpanData(
        trace_id="test-trial",
        span_id="abc123",
        operation_name=event.event_type,
        start_time=int(datetime.now(timezone.utc).timestamp() * 1_000_000),
        duration=0,
        tags=tags,
    )


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


class TestDeserializeEventFromSpan:
    """deserialize_event_from_span() reconstructs typed events."""

    def test_game_initialize_round_trip(self):
        """Full GameInitializeEvent with TeamIdentity survives span round-trip."""
        original = GameInitializeEvent(
            game_id="401584700",
            sport="nba",
            home_team=TeamIdentity(
                team_id="1610612738",
                name="Boston Celtics",
                tricode="BOS",
                location="Boston",
                color="#007A33",
                alternate_color="#BA9653",
                logo_url="https://cdn.nba.com/logos/nba/1610612738/primary/L/logo.svg",
                record="42-18",
            ),
            away_team=TeamIdentity(
                team_id="1610612761",
                name="Toronto Raptors",
                tricode="TOR",
                location="Toronto",
                color="#CE1141",
            ),
            venue=VenueInfo(
                name="TD Garden",
                city="Boston",
                state="MA",
                indoor=True,
            ),
            broadcast="ESPN",
            season_year=2025,
            season_type="regular",
        )
        span = _simulate_span_from_event(original)
        restored = deserialize_event_from_span(span)

        assert restored is not None
        assert isinstance(restored, GameInitializeEvent)
        assert restored.game_id == "401584700"
        assert restored.sport == "nba"
        assert restored.broadcast == "ESPN"
        assert restored.season_year == 2025

        # TeamIdentity round-trip
        home = restored.home_team
        assert isinstance(home, TeamIdentity)
        assert home.name == "Boston Celtics"
        assert home.tricode == "BOS"
        assert home.color == "#007A33"
        assert (
            home.logo_url
            == "https://cdn.nba.com/logos/nba/1610612738/primary/L/logo.svg"
        )
        assert home.record == "42-18"

        away = restored.away_team
        assert isinstance(away, TeamIdentity)
        assert away.name == "Toronto Raptors"
        assert away.tricode == "TOR"

        # VenueInfo round-trip
        assert restored.venue.name == "TD Garden"
        assert restored.venue.city == "Boston"

    def test_odds_update_round_trip(self):
        """OddsUpdateEvent with OddsInfo survives span round-trip."""
        original = OddsUpdateEvent(
            game_id="401584700",
            sport="nba",
            odds=OddsInfo(
                provider="polymarket",
                moneyline=MoneylineOdds(
                    home_probability=0.65,
                    away_probability=0.35,
                    home_odds=1.54,
                    away_odds=2.86,
                ),
            ),
            home_tricode="BOS",
            away_tricode="TOR",
        )
        span = _simulate_span_from_event(original)
        restored = deserialize_event_from_span(span)

        assert restored is not None
        assert isinstance(restored, OddsUpdateEvent)
        assert restored.game_id == "401584700"
        assert restored.odds.provider == "polymarket"
        assert restored.odds.moneyline is not None
        assert restored.odds.moneyline.home_probability == 0.65
        assert restored.odds.moneyline.home_odds == 1.54

    def test_game_result_round_trip(self):
        """GameResultEvent survives span round-trip."""
        original = GameResultEvent(
            game_id="401584700",
            sport="nba",
            winner="home",
            home_score=110,
            away_score=98,
            home_team_name="Boston Celtics",
            away_team_name="Toronto Raptors",
        )
        span = _simulate_span_from_event(original)
        restored = deserialize_event_from_span(span)

        assert restored is not None
        assert isinstance(restored, GameResultEvent)
        assert restored.winner == "home"
        assert restored.home_score == 110
        assert restored.away_score == 98

    def test_unrecognized_event_type_returns_none(self):
        """Unknown event_type returns None."""
        span = SpanData(
            trace_id="test",
            span_id="abc",
            operation_name="event.unknown_type",
            start_time=0,
            duration=0,
            tags={"event.foo": "bar"},
        )
        result = deserialize_event_from_span(span)
        assert result is None

    def test_span_with_no_event_tags(self):
        """Span with no event.* tags produces minimal event or None."""
        span = SpanData(
            trace_id="test",
            span_id="abc",
            operation_name="trial.started",
            start_time=0,
            duration=0,
            tags={"trial.phase": "started"},
        )
        result = deserialize_event_from_span(span)
        # trial.started is not a registered event type
        assert result is None


# ---------------------------------------------------------------------------
# Arena server helpers
# ---------------------------------------------------------------------------


class TestTeamIdentitySerialization:
    """TeamIdentity.model_dump(by_alias=True) produces camelCase frontend dict."""

    def test_full_identity(self):
        team = TeamIdentity(
            team_id="1610612738",
            name="Boston Celtics",
            tricode="BOS",
            location="Boston",
            color="#007A33",
            alternate_color="#BA9653",
            logo_url="https://cdn.nba.com/logos/nba/1610612738/primary/L/logo.svg",
            record="42-18",
        )
        d = team.model_dump(by_alias=True)
        assert d["name"] == "Boston Celtics"
        assert d["city"] == "Boston"
        assert d["color"] == "#007A33"
        assert d["abbrev"] == "BOS"
        assert d["teamId"] == "1610612738"
        assert d["logoUrl"].startswith("https://")
        assert d["record"] == "42-18"

    def test_empty_identity(self):
        d = TeamIdentity().model_dump(by_alias=True)
        assert d["name"] == ""
        assert d["city"] == ""
        assert d["color"] == ""
