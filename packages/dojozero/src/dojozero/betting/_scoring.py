"""Scoring strategies for the ScoringSys prediction framework.

Provides a pluggable strategy pattern for computing prediction scores
based on correctness and timing. Two concrete strategies:

- QuarterPoolStrategy: discrete quarter-based prize pool splitting
- ContinuousDecayStrategy: exponential time-decay weighting
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from pydantic import BaseModel, Field

from ._models import Prediction, PredictionOutcome, ScoringSystem


# =============================================================================
# Scoring Configuration
# =============================================================================


class ScoringConfig(BaseModel):
    """Configuration for a ScoringSys scoring strategy."""

    scoring_system: ScoringSystem
    max_predictions: Optional[int] = Field(
        default=None,
        description="Max predictions per agent per event (None = unlimited)",
    )
    quarter_pools: list[int] = Field(
        default_factory=lambda: [4000, 3000, 2000, 1000],
        description="Prize pool per quarter (index 0 = Q1). Used by QuarterPool strategy.",
    )
    base_score: Decimal = Field(
        default=Decimal("1000"),
        description="Base score for a correct prediction (used by ContinuousDecay).",
    )
    decay_lambda: float = Field(
        default=3.0,
        description="Decay rate lambda for continuous decay: weight = exp(-lambda * elapsed_ratio).",
    )

    @property
    def is_fixed_entry(self) -> bool:
        return self.scoring_system in (ScoringSystem.SYS1, ScoringSystem.SYS2)


# =============================================================================
# Abstract Strategy
# =============================================================================


class ScoringStrategy(ABC):
    """Base class for scoring strategies."""

    def __init__(self, config: ScoringConfig) -> None:
        self.config = config

    @abstractmethod
    def settle_predictions(
        self,
        predictions: list[Prediction],
        winner: str,
    ) -> list[Prediction]:
        """Score all predictions for a settled event.

        Args:
            predictions: All predictions for the event.
            winner: Game result — "home" or "away".

        Returns:
            The same prediction list with is_correct and score filled in.
        """
        ...


# =============================================================================
# Quarter Pool Strategy
# =============================================================================


def _outcome_matches_winner(selection: PredictionOutcome, winner: str) -> bool:
    """Check if a prediction outcome matches the game winner."""
    if winner == "home":
        return selection == PredictionOutcome.HOME_WIN
    elif winner == "away":
        return selection == PredictionOutcome.AWAY_WIN
    else:
        return selection == PredictionOutcome.EVEN


class QuarterPoolStrategy(ScoringStrategy):
    """Score by splitting a per-quarter prize pool among correct predictions.

    Each quarter has a decreasing pool (e.g. [4000, 3000, 2000, 1000]).
    Correct predictions in quarter q share quarter_pools[q] equally.
    """

    def settle_predictions(
        self,
        predictions: list[Prediction],
        winner: str,
    ) -> list[Prediction]:
        pools = self.config.quarter_pools

        # Mark correctness
        for p in predictions:
            p.is_correct = _outcome_matches_winner(p.selection, winner)

        # Group correct predictions by quarter
        correct_by_q: dict[int, list[Prediction]] = defaultdict(list)
        for p in predictions:
            if p.is_correct:
                correct_by_q[p.quarter].append(p)

        # Distribute pool
        for q, correct_preds in correct_by_q.items():
            pool_idx = max(0, q - 1)  # quarter 1 -> index 0
            pool = pools[pool_idx] if pool_idx < len(pools) else pools[-1]
            share = Decimal(str(pool)) / Decimal(str(len(correct_preds)))
            share = share.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            for p in correct_preds:
                p.score = share

        # Incorrect predictions get 0
        for p in predictions:
            if not p.is_correct:
                p.score = Decimal("0")

        return predictions


# =============================================================================
# Continuous Decay Strategy
# =============================================================================


class ContinuousDecayStrategy(ScoringStrategy):
    """Score = base_score * exp(-lambda * elapsed_ratio) for correct predictions.

    elapsed_ratio is the fraction of game time elapsed when the prediction
    was submitted (0.0 at game start, 1.0 at game end). Predictions made
    before game start have elapsed_ratio = 0.0 and receive full weight.
    """

    def settle_predictions(
        self,
        predictions: list[Prediction],
        winner: str,
    ) -> list[Prediction]:
        base = self.config.base_score
        lam = self.config.decay_lambda

        for p in predictions:
            p.is_correct = _outcome_matches_winner(p.selection, winner)
            if p.is_correct:
                weight = Decimal(str(math.exp(-lam * p.elapsed_ratio)))
                p.score = (base * weight).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            else:
                p.score = Decimal("0")

        return predictions


# =============================================================================
# Factory
# =============================================================================

_STRATEGY_MAP: dict[ScoringSystem, type[ScoringStrategy]] = {
    ScoringSystem.SYS1: QuarterPoolStrategy,
    ScoringSystem.SYS2: ContinuousDecayStrategy,
    ScoringSystem.SYS3: QuarterPoolStrategy,
    ScoringSystem.SYS4: ContinuousDecayStrategy,
}


def create_scoring_strategy(config: ScoringConfig) -> ScoringStrategy:
    """Instantiate the correct ScoringStrategy for the given config."""
    cls = _STRATEGY_MAP.get(config.scoring_system)
    if cls is None:
        raise ValueError(f"Unknown scoring system: {config.scoring_system}")
    return cls(config)


__all__ = [
    "ScoringConfig",
    "ScoringStrategy",
    "QuarterPoolStrategy",
    "ContinuousDecayStrategy",
    "create_scoring_strategy",
]
