"""Window-pool scoring for the prediction contest.

A single rule applies: each game is divided into five fixed windows
(pre-game and Q1-Q4). Each agent may submit at most one prediction per
window. After the game settles, every correct prediction in window ``w``
shares ``window_pools[w]`` evenly with every other correct prediction
recorded in the same window.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

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
        window_pools: Prize pool per window (index 0 = pre-game, 1-4 = Q1-Q4).

    Returns:
        The same prediction list with ``is_correct`` and ``score`` filled in.
        Each correct prediction in window ``w`` receives an equal share of
        ``window_pools[w]``; incorrect predictions receive 0.
    """
    for p in predictions:
        p.is_correct = _outcome_matches_winner(p.selection, winner)

    correct_by_window: dict[int, list[Prediction]] = defaultdict(list)
    for p in predictions:
        if p.is_correct:
            correct_by_window[p.window].append(p)

    for window, correct_preds in correct_by_window.items():
        idx = max(0, min(window, len(window_pools) - 1))
        pool = window_pools[idx]
        share = Decimal(str(pool)) / Decimal(str(len(correct_preds)))
        share = share.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        for p in correct_preds:
            p.score = share

    for p in predictions:
        if not p.is_correct:
            p.score = Decimal("0")

    return predictions


__all__ = [
    "DEFAULT_WINDOW_POOLS",
    "NUM_WINDOWS",
    "settle_window_predictions",
]
