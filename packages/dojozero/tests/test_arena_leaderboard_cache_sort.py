"""Regression tests for the cache-layer leaderboard sort.

The prediction-view sort remap (betting sort keys -> prediction_score) lives
solely in the serving layer (_endpoints.get_leaderboard). The cache-computation
path (_compute_leaderboard_from_spans) must stay neutral and sort strictly by the
requested field, otherwise a mixed/global board gets reordered by
prediction_score and pure-bettor agents are buried.
"""

from __future__ import annotations

import json

from dojozero.arena_server._utils import _compute_leaderboard_from_spans
from dojozero.core._tracing import SpanData


def _betting_final_stats_span(agent_id: str, net_profit: str) -> SpanData:
    return SpanData(
        trace_id="betting-trial",
        span_id="betting-final",
        operation_name="broker.final_stats",
        start_time=1_000_000,
        duration=0,
        tags={
            "broker.contest_kind": "classic_betting",
            "broker.statistics": json.dumps(
                {
                    agent_id: {
                        "total_bets": 5,
                        "total_wagered": "100.00",
                        "wins": 3,
                        "losses": 2,
                        "win_rate": 60.0,
                        "net_profit": net_profit,
                        "roi": 0.5,
                    }
                }
            ),
        },
    )


def _prediction_final_stats_span(agent_id: str, total_score: str) -> SpanData:
    return SpanData(
        trace_id="prediction-trial",
        span_id="prediction-final",
        operation_name="broker.final_stats",
        start_time=1_000_000,
        duration=0,
        tags={
            "broker.contest_kind": "window_pool_prediction",
            "broker.bets_count": 0,
            "broker.prediction_statistics": json.dumps(
                {
                    agent_id: {
                        "total_predictions": 4,
                        "correct_predictions": 3,
                        "accuracy": 0.75,
                        "total_score": total_score,
                    }
                }
            ),
            "broker.window_pools": json.dumps([5000, 4000, 3000, 2000, 500]),
        },
    )


def test_cache_leaderboard_does_not_remap_mixed_board_to_prediction_score() -> None:
    # Mixed global board: a pure-betting agent (high winnings, no predictions) and
    # a pure-prediction agent (high prediction score, zero winnings).
    spans_by_trial = {
        "betting-trial": [_betting_final_stats_span("bettor", "150.00")],
        "prediction-trial": [_prediction_final_stats_span("predictor", "20.00")],
    }

    result = _compute_leaderboard_from_spans(
        spans_by_trial,
        {},
        ["betting-trial", "prediction-trial"],
        sort_by="winnings",
        sort_order="desc",
    )

    ids = [e.agent.agent_id for e in result.leaderboard]
    assert set(ids) == {"bettor", "predictor"}
    # Regression: sorted strictly by winnings, NOT remapped to prediction_score
    # (which would put the predictor first and bury the bettor).
    assert ids[0] == "bettor"

    by_id = {e.agent.agent_id: e for e in result.leaderboard}
    assert by_id["bettor"].winnings == 150.0
    assert by_id["bettor"].prediction_score is None
    assert by_id["predictor"].winnings == 0
    assert by_id["predictor"].prediction_score == 20.0
