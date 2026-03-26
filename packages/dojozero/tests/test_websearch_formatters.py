"""Tests for shared web search event formatters.

These formatters are sport-agnostic and used by both NBA and NFL formatter
registries via WEBSEARCH_EVENT_FORMATTERS.
"""

from dojozero.data.websearch._events import (
    ExpertPredictionEvent,
    InjuryReportEvent,
    PowerRankingEvent,
)
from dojozero.data.websearch._formatters import (
    WEBSEARCH_EVENT_FORMATTERS,
    format_expert_prediction,
    format_injury_report,
    format_power_ranking,
)


class TestFormatInjuryReport:
    def test_with_summary_and_players(self):
        event = InjuryReportEvent(
            summary="Key injuries heading into tonight.",
            injured_players={
                "Lakers": ["LeBron James", "Anthony Davis"],
                "Celtics": ["Jaylen Brown"],
            },
        )
        result = format_injury_report(event)
        assert "[Injury Report Update]" in result
        assert "Key injuries heading into tonight." in result
        assert "Lakers: LeBron James, Anthony Davis" in result
        assert "Celtics: Jaylen Brown" in result

    def test_summary_only(self):
        event = InjuryReportEvent(
            summary="No significant injuries.",
            injured_players={},
        )
        result = format_injury_report(event)
        assert "[Injury Report Update]" in result
        assert "No significant injuries." in result
        assert "Injured Players" not in result

    def test_empty(self):
        event = InjuryReportEvent()
        result = format_injury_report(event)
        assert "[Injury Report Update]" in result

    def test_empty_player_list_skipped(self):
        event = InjuryReportEvent(
            injured_players={"Chiefs": [], "49ers": ["McCaffrey"]},
        )
        result = format_injury_report(event)
        assert "Chiefs" not in result
        assert "49ers: McCaffrey" in result


class TestFormatPowerRanking:
    def test_with_rankings(self):
        event = PowerRankingEvent(
            rankings={
                "espn.com": [
                    {"rank": 1, "team": "Celtics", "record": "42-12"},
                    {"rank": 2, "team": "Thunder", "record": "40-14"},
                ],
            },
        )
        result = format_power_ranking(event)
        assert "[Power Rankings Update]" in result
        assert "Source: espn.com" in result
        assert "1. Celtics (42-12)" in result
        assert "2. Thunder (40-14)" in result

    def test_without_record(self):
        event = PowerRankingEvent(
            rankings={"nba.com": [{"rank": 1, "team": "Lakers"}]},
        )
        result = format_power_ranking(event)
        assert "1. Lakers" in result
        assert "()" not in result

    def test_top_10_limit(self):
        rankings = [
            {"rank": i, "team": f"Team{i}", "record": f"{15 - i}-{i}"}
            for i in range(1, 15)
        ]
        event = PowerRankingEvent(rankings={"espn.com": rankings})
        result = format_power_ranking(event)
        assert "10. Team10" in result
        assert "11. Team11" not in result

    def test_empty(self):
        event = PowerRankingEvent(rankings={})
        result = format_power_ranking(event)
        assert "[Power Rankings Update]" in result


class TestFormatExpertPrediction:
    def test_with_full_prediction(self):
        event = ExpertPredictionEvent(
            predictions=[
                {
                    "source": "ESPN",
                    "expert": "Stephen A.",
                    "prediction": "Lakers win by 10",
                    "confidence": "High",
                },
            ],
        )
        result = format_expert_prediction(event)
        assert "[Expert Predictions]" in result
        assert "ESPN" in result
        assert "Stephen A." in result
        assert "Lakers win by 10" in result
        assert "Confidence: High" in result

    def test_without_expert_or_confidence(self):
        event = ExpertPredictionEvent(
            predictions=[
                {"source": "CBS Sports", "prediction": "Celtics favored"},
            ],
        )
        result = format_expert_prediction(event)
        assert "CBS Sports" in result
        assert "Celtics favored" in result
        assert "Confidence" not in result

    def test_empty(self):
        event = ExpertPredictionEvent(predictions=[])
        result = format_expert_prediction(event)
        assert "[Expert Predictions]" in result


class TestWebsearchEventFormattersRegistry:
    def test_contains_all_three_keys(self):
        assert "injury_report" in WEBSEARCH_EVENT_FORMATTERS
        assert "power_ranking" in WEBSEARCH_EVENT_FORMATTERS
        assert "expert_prediction" in WEBSEARCH_EVENT_FORMATTERS
        assert len(WEBSEARCH_EVENT_FORMATTERS) == 3

    def test_functions_are_callable(self):
        for fn in WEBSEARCH_EVENT_FORMATTERS.values():
            assert callable(fn)
