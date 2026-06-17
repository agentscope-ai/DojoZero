"""Window-pool scoring for the prediction contest.

A single rule applies: each game is divided into five fixed windows
(pre-game and Q1-Q4). Each agent may submit at most one prediction per
window. After the game settles, every correct prediction in window ``w``
shares ``window_pools[w]`` evenly with every other correct prediction
recorded in the same window.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_DOWN

from ._models import Prediction, PredictionOutcome


DEFAULT_WINDOW_POOLS: list[int] = [5000, 4000, 3000, 2000, 500]
NUM_WINDOWS: int = 5


def _outcome_matches_winner(selection: PredictionOutcome, winner: str) -> bool:
    """Check if a prediction outcome matches the game winner."""
    if winner == "home":
        return selection == PredictionOutcome.HOME_WIN
    if winner == "away":
        return selection == PredictionOutcome.AWAY_WIN
    return selection == PredictionOutcome.EVEN


def settle_window_predictions(
    predictions: list[Prediction],
    winner: str,
    window_pools: list[int],
) -> list[Prediction]:
    """Score all predictions for a settled event.

    Args:
        predictions: All predictions for the event.
        winner: Game result -- "home", "away", or anything else for "even".
        window_pools: Prize pool per window (index 0 = pre-game, 1-4 = the
            periods of play). The broker may fold unreached windows' pools into
            the last reached window before calling this, so the full pool stays
            winnable; this function just splits whatever pools it is given.

    Returns:
        New :class:`Prediction` instances with ``is_correct`` and ``score``
        filled in (same order as the input). Each correct prediction in
        window ``w`` receives an equal share of ``window_pools[w]``;
        incorrect predictions earn 0. Input objects are not mutated, so the
        function stays correct if :class:`Prediction` is ever made frozen.
    """
    correctness = {
        p.prediction_id: _outcome_matches_winner(p.selection, winner)
        for p in predictions
    }

    scores: dict[str, Decimal] = {}
    correct_by_window: dict[int, list[Prediction]] = defaultdict(list)
    for p in predictions:
        if correctness[p.prediction_id]:
            correct_by_window[p.window].append(p)
        else:
            scores[p.prediction_id] = Decimal("0")

    for window, correct_preds in correct_by_window.items():
        idx = max(0, min(window, len(window_pools) - 1))
        pool = Decimal(str(window_pools[idx]))
        # Round individual shares DOWN to two decimals so we never distribute
        # more than the pool. Any leftover from the rounding goes to the first
        # winner so the full pool is still paid out.
        share = (pool / Decimal(str(len(correct_preds)))).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
        remainder = pool - share * len(correct_preds)
        for i, p in enumerate(correct_preds):
            scores[p.prediction_id] = share + remainder if i == 0 else share

    return [
        p.model_copy(
            update={
                "is_correct": correctness[p.prediction_id],
                "score": scores[p.prediction_id],
            }
        )
        for p in predictions
    ]


__all__ = [
    "DEFAULT_WINDOW_POOLS",
    "NUM_WINDOWS",
    "settle_window_predictions",
]
