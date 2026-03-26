"""Tests for web search event team filtering logic."""

from dojozero.data._context import GameContext
from dojozero.data.websearch._events import _filter_teams


class TestFilterTeams:
    def test_exact_match(self):
        players = {
            "Houston Rockets": ["Fred VanVleet"],
            "Memphis Grizzlies": ["Ja Morant"],
            "Los Angeles Lakers": ["LeBron James"],
        }
        result = _filter_teams(players, "Houston Rockets", "Memphis Grizzlies")
        assert "Houston Rockets" in result
        assert "Memphis Grizzlies" in result
        assert "Los Angeles Lakers" not in result

    def test_substring_match_key_in_context(self):
        """Key 'Rockets' is a substring of context 'Houston Rockets'."""
        players = {
            "Rockets": ["Fred VanVleet"],
            "Grizzlies": ["Ja Morant"],
            "Lakers": ["LeBron James"],
        }
        result = _filter_teams(players, "Houston Rockets", "Memphis Grizzlies")
        assert "Rockets" in result
        assert "Grizzlies" in result
        assert "Lakers" not in result

    def test_substring_match_context_in_key(self):
        """Context 'Rockets' is a substring of key 'Houston Rockets'."""
        players = {
            "Houston Rockets": ["Fred VanVleet"],
            "Memphis Grizzlies": ["Ja Morant"],
            "Lakers": ["LeBron James"],
        }
        result = _filter_teams(players, "Rockets", "Grizzlies")
        assert "Houston Rockets" in result
        assert "Memphis Grizzlies" in result
        assert "Lakers" not in result

    def test_case_insensitive(self):
        players = {
            "houston rockets": ["Fred VanVleet"],
            "MEMPHIS GRIZZLIES": ["Ja Morant"],
            "Los Angeles Lakers": ["LeBron James"],
        }
        result = _filter_teams(players, "Houston Rockets", "Memphis Grizzlies")
        assert "houston rockets" in result
        assert "MEMPHIS GRIZZLIES" in result
        assert "Los Angeles Lakers" not in result

    def test_no_context_teams_returns_all(self):
        players = {
            "Houston Rockets": ["Fred VanVleet"],
            "Los Angeles Lakers": ["LeBron James"],
        }
        result = _filter_teams(players, "", "")
        assert len(result) == 2

    def test_one_context_team_only(self):
        players = {
            "Houston Rockets": ["Fred VanVleet"],
            "Memphis Grizzlies": ["Ja Morant"],
            "Lakers": ["LeBron James"],
        }
        result = _filter_teams(players, "Houston Rockets", "")
        assert "Houston Rockets" in result
        assert "Memphis Grizzlies" not in result
        assert "Lakers" not in result

    def test_empty_injured_players(self):
        result = _filter_teams({}, "Houston Rockets", "Memphis Grizzlies")
        assert result == {}

    def test_no_matching_teams(self):
        players = {
            "Los Angeles Lakers": ["LeBron James"],
            "Boston Celtics": ["Jayson Tatum"],
        }
        result = _filter_teams(players, "Houston Rockets", "Memphis Grizzlies")
        assert result == {}


class TestInjuryReportParseFiltering:
    """Test that _parse_llm_response filters to relevant teams."""

    def _make_response(
        self, structured_data: str, summary: str = "Test summary"
    ) -> dict:
        return {
            "status_code": 200,
            "output": {
                "text": f"SUMMARY:\n{summary}\n\nSTRUCTURED_DATA:\n{structured_data}"
            },
        }

    def test_filters_to_game_teams(self):
        from dojozero.data.websearch._events import InjuryReportEvent

        response = self._make_response(
            '{"Houston Rockets": ["VanVleet"], "Memphis Grizzlies": ["Morant"], "Lakers": ["James"]}'
        )
        context = GameContext(
            home_team="Houston Rockets",
            away_team="Memphis Grizzlies",
        )
        event = InjuryReportEvent._parse_llm_response(response, "test query", context)
        assert event is not None
        assert "Houston Rockets" in event.injured_players
        assert "Memphis Grizzlies" in event.injured_players
        assert "Lakers" not in event.injured_players

    def test_no_filtering_without_context_teams(self):
        from dojozero.data.websearch._events import InjuryReportEvent

        response = self._make_response(
            '{"Houston Rockets": ["VanVleet"], "Lakers": ["James"]}'
        )
        context = GameContext()  # No teams set
        event = InjuryReportEvent._parse_llm_response(response, "test query", context)
        assert event is not None
        assert len(event.injured_players) == 2
