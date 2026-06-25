import json

import pytest

from dojozero.arena_server._cache import LandingPageCache
from dojozero.arena_server._utils import _compute_stats
from dojozero.core._tracing import SpanData


def _prediction_final_stats_span() -> SpanData:
    return SpanData(
        trace_id="world-cup-trial",
        span_id="final-stats",
        operation_name="broker.final_stats",
        start_time=1_000_000,
        duration=0,
        tags={
            "broker.contest_kind": "window_pool_prediction",
            "broker.bets_count": 0,
            "broker.prediction_statistics": json.dumps(
                {
                    "agent-a": {
                        "total_predictions": 2,
                        "correct_predictions": 1,
                        "accuracy": 0.5,
                        "total_score": "12.50",
                    },
                    "agent-b": {
                        "total_predictions": 1,
                        "correct_predictions": 1,
                        "accuracy": 1.0,
                        "total_score": "2.75",
                    },
                }
            ),
            "broker.window_pools": json.dumps([5000, 4000, 3000, 2000, 500]),
        },
    )


def _zero_bet_betting_final_stats_span() -> SpanData:
    """A classic_betting trial where no agent placed a bet (no stats/bets)."""
    return SpanData(
        trace_id="betting-trial",
        span_id="betting-final",
        operation_name="broker.final_stats",
        start_time=1_000_000,
        duration=0,
        tags={
            "broker.contest_kind": "classic_betting",
            "broker.bets_count": 0,
        },
    )


@pytest.mark.asyncio
async def test_zero_bet_betting_trial_counts_as_betting_mode() -> None:
    # A zero-bet betting trial aggregated with a prediction trial must yield
    # mode="mixed" — the betting contest is detected by contest_kind, not by
    # bet activity (which is zero).
    cache = LandingPageCache()
    cache.set_trial_info(
        "betting-trial",
        {"phase": "completed", "metadata": {"sport_type": "WORLD_CUP"}},
    )
    cache.set_trial_info(
        "prediction-trial",
        {"phase": "completed", "metadata": {"sport_type": "WORLD_CUP"}},
    )

    stats = await _compute_stats(
        object(),  # type: ignore[arg-type]
        ["betting-trial", "prediction-trial"],
        cache,
        {
            "betting-trial": [_zero_bet_betting_final_stats_span()],
            "prediction-trial": [_prediction_final_stats_span()],
        },
    )

    assert stats.mode == "mixed"


@pytest.mark.asyncio
async def test_world_cup_stats_use_prediction_totals() -> None:
    cache = LandingPageCache()
    cache.set_trial_info(
        "world-cup-trial",
        {"phase": "completed", "metadata": {"sport_type": "WORLD_CUP"}},
    )

    stats = await _compute_stats(
        object(),  # type: ignore[arg-type]
        ["world-cup-trial"],
        cache,
        {"world-cup-trial": [_prediction_final_stats_span()]},
    )

    assert stats.games_played == 1
    assert stats.wagered_today == 0
    # bet_counts is betting-only; the 3 predictions surface via prediction_count.
    assert stats.bet_counts == 0
    assert stats.prediction_count == 3
    assert stats.prediction_points == pytest.approx(15.25)
