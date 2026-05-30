"""Unit tests for the window-pool scoring math."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from dojozero.betting._models import Prediction, PredictionOutcome
from dojozero.betting._scoring import (
    DEFAULT_WINDOW_POOLS,
    NUM_WINDOWS,
    settle_window_predictions,
)


def _pred(
    *,
    prediction_id: str,
    agent_id: str,
    selection: PredictionOutcome,
    window: int,
) -> Prediction:
    return Prediction(
        prediction_id=prediction_id,
        agent_id=agent_id,
        event_id="evt-1",
        selection=selection,
        submit_time=datetime.now(timezone.utc),
        window=window,
        elapsed_ratio=0.0,
    )


def test_no_predictions_returns_empty() -> None:
    assert settle_window_predictions([], "home", DEFAULT_WINDOW_POOLS) == []


def test_single_winner_gets_full_pool() -> None:
    p = _pred(
        prediction_id="p1",
        agent_id="a1",
        selection=PredictionOutcome.HOME_WIN,
        window=0,
    )

    settled = settle_window_predictions([p], "home", DEFAULT_WINDOW_POOLS)

    assert len(settled) == 1
    assert settled[0].is_correct is True
    assert settled[0].score == Decimal("5000.00")


def test_all_correct_split_pool_evenly() -> None:
    preds = [
        _pred(
            prediction_id=f"p{i}",
            agent_id=f"a{i}",
            selection=PredictionOutcome.HOME_WIN,
            window=1,
        )
        for i in range(4)
    ]

    settled = settle_window_predictions(preds, "home", DEFAULT_WINDOW_POOLS)

    # Window 1 pool = 4000 / 4 = 1000.00 each, no remainder.
    assert {p.score for p in settled} == {Decimal("1000.00")}
    assert sum((p.score or Decimal("0")) for p in settled) == Decimal("4000")


def test_rounding_remainder_goes_to_first_winner() -> None:
    # 5000 / 3 = 1666.6666...; ROUND_DOWN → 1666.66 each.
    # Distributed naively: 3 * 1666.66 = 4999.98. Remainder 0.02 → first.
    preds = [
        _pred(
            prediction_id=f"p{i}",
            agent_id=f"a{i}",
            selection=PredictionOutcome.HOME_WIN,
            window=0,
        )
        for i in range(3)
    ]

    settled = settle_window_predictions(preds, "home", DEFAULT_WINDOW_POOLS)

    scores = [p.score for p in settled]
    # First-index gets share + remainder; rest get share.
    assert scores[0] == Decimal("1666.68")
    assert scores[1] == Decimal("1666.66")
    assert scores[2] == Decimal("1666.66")
    # Total equals the pool exactly.
    assert sum((s or Decimal("0")) for s in scores) == Decimal("5000")


def test_all_incorrect_score_zero() -> None:
    preds = [
        _pred(
            prediction_id=f"p{i}",
            agent_id=f"a{i}",
            selection=PredictionOutcome.AWAY_WIN,
            window=0,
        )
        for i in range(3)
    ]

    settled = settle_window_predictions(preds, "home", DEFAULT_WINDOW_POOLS)

    assert all(p.is_correct is False for p in settled)
    assert all(p.score == Decimal("0") for p in settled)


def test_mixed_correct_and_incorrect_per_window() -> None:
    # Two correct in window 2 split that pool; one incorrect earns 0.
    preds = [
        _pred(
            prediction_id="p1",
            agent_id="a1",
            selection=PredictionOutcome.HOME_WIN,
            window=2,
        ),
        _pred(
            prediction_id="p2",
            agent_id="a2",
            selection=PredictionOutcome.HOME_WIN,
            window=2,
        ),
        _pred(
            prediction_id="p3",
            agent_id="a3",
            selection=PredictionOutcome.AWAY_WIN,
            window=2,
        ),
    ]

    settled = settle_window_predictions(preds, "home", DEFAULT_WINDOW_POOLS)

    by_id = {p.prediction_id: p for p in settled}
    # Window 2 pool = 3000 / 2 = 1500.00 each.
    assert by_id["p1"].score == Decimal("1500.00")
    assert by_id["p2"].score == Decimal("1500.00")
    assert by_id["p3"].score == Decimal("0")


def test_even_winner_pays_even_selection() -> None:
    preds = [
        _pred(
            prediction_id="p1",
            agent_id="a1",
            selection=PredictionOutcome.EVEN,
            window=3,
        ),
        _pred(
            prediction_id="p2",
            agent_id="a2",
            selection=PredictionOutcome.HOME_WIN,
            window=3,
        ),
    ]

    settled = settle_window_predictions(preds, "even", DEFAULT_WINDOW_POOLS)

    by_id = {p.prediction_id: p for p in settled}
    # Window 3 pool = 2000; sole correct winner.
    assert by_id["p1"].is_correct is True
    assert by_id["p1"].score == Decimal("2000.00")
    assert by_id["p2"].is_correct is False
    assert by_id["p2"].score == Decimal("0")


def test_short_window_pools_clamps_pool_lookup() -> None:
    """If ``window_pools`` has fewer entries than the prediction's window,
    settlement clamps to the last available pool rather than IndexErroring.
    """
    short_pools = [10, 20]  # only windows 0 and 1 defined
    p = _pred(
        prediction_id="p1",
        agent_id="a1",
        selection=PredictionOutcome.HOME_WIN,
        window=4,  # max allowed by the Prediction model
    )

    settled = settle_window_predictions([p], "home", short_pools)

    # Pool[1] = 20 is the last entry; sole winner takes the full pool.
    assert settled[0].score == Decimal("20.00")


def test_settlement_does_not_mutate_inputs() -> None:
    p = _pred(
        prediction_id="p1",
        agent_id="a1",
        selection=PredictionOutcome.HOME_WIN,
        window=0,
    )

    settled = settle_window_predictions([p], "home", DEFAULT_WINDOW_POOLS)

    # Input prediction is unchanged; settled ones are new instances.
    assert p.is_correct is None
    assert p.score is None
    assert settled[0] is not p
    assert settled[0].prediction_id == p.prediction_id


def test_num_windows_constant_matches_default_pools() -> None:
    assert NUM_WINDOWS == len(DEFAULT_WINDOW_POOLS) == 5


@pytest.mark.parametrize(
    "winner,selection,expected",
    [
        ("home", PredictionOutcome.HOME_WIN, True),
        ("home", PredictionOutcome.AWAY_WIN, False),
        ("away", PredictionOutcome.AWAY_WIN, True),
        ("away", PredictionOutcome.HOME_WIN, False),
        ("even", PredictionOutcome.EVEN, True),
        ("even", PredictionOutcome.HOME_WIN, False),
    ],
)
def test_correctness_matrix(
    winner: str, selection: PredictionOutcome, expected: bool
) -> None:
    p = _pred(prediction_id="p1", agent_id="a1", selection=selection, window=0)
    settled = settle_window_predictions([p], winner, DEFAULT_WINDOW_POOLS)
    assert settled[0].is_correct is expected
