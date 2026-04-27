"""System prompts for different scoring systems in the ScoringSys prediction framework.

Each scoring system has its own set of rules and strategies that agents should understand.
This module provides system prompts that educate agents about the specific rules
they should follow under each scoring system.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Default pool sizes (must match ScoringConfig / trial YAML)
_DEFAULT_QUARTER_POOLS: list[int] = [4000, 3000, 2000, 1000]


def _pools_pretty(quarter_pools: list[int]) -> str:
    return ", ".join(f"Q{i + 1}: {p}" for i, p in enumerate(quarter_pools))


def _line_max_predictions_fixed(max_predictions: Optional[int]) -> str:
    if max_predictions is None:
        return (
            "- The broker enforces a per-event prediction cap; use `get_my_predictions` to see "
            "how many you have already submitted. If you hit the cap, further `submit_prediction` "
            "calls return an error."
        )
    return (
        f"- You may submit **at most {max_predictions}** discrete prediction(s) per event. "
        "Each `submit_prediction` call counts against this cap."
    )


def _prompt_scoring_sys1(
    max_predictions: Optional[int], quarter_pools: list[int]
) -> str:
    pools = _pools_pretty(quarter_pools)
    return f"""You are participating in a prediction contest with the following rules:

QUARTER-POOL SCORING SYSTEM (fixed entry, multiple shots allowed up to the cap):
{_line_max_predictions_fixed(max_predictions)}
- You may predict **before the game and during play**; each correct prediction is scored in the
  **quarter pool for the period when the prediction is recorded** (see below).
- If your prediction is correct, you **share that quarter's pool** with every other **correct**
  prediction (including from other agents) that was recorded in the **same** quarter. Your score is
  that pool's size divided by the number of correct predictions in that quarter.
- Per-quarter total prize pools (points available to split in that quarter): {pools}
- Later regulation quarters have smaller pools; overtime maps to the last pool tier.
- The fewer agents who are correct in a given quarter, the larger each correct share.
- You can spread submissions across different quarters to compete for a later, smaller pool, or
  use multiple entries in the same quarter (each entry shares that quarter's pool separately—more
  correct entries from you also split the same pool with each other).
- The `submit_prediction` tool has an optional `quarter` argument. If you pass `0` (the default)
  or omit it: **before tip-off** you are in the pre-game bucket (same as Q1 for scoring); **after
  the game is live, the broker automatically sets the quarter from the current game period** so
  your submission is placed in the correct pool. You may also pass an explicit `quarter` (1-4
  for regulation) if you need to override.

STRATEGY:
- Compare pool sizes (Q1 largest) against how clear the outcome is later in the game.
- If many agents pick the same side in a quarter, each correct share shrinks; contrarian correct
  picks in a thin crowd earn more.
- Track remaining submissions with `get_my_predictions` and stay under the cap.
"""


def _prompt_scoring_sys2(
    max_predictions: Optional[int],
    base_score: str,
    decay_lambda: float,
) -> str:
    import math as _math
    ex_25 = round(float(base_score) * _math.exp(-decay_lambda * 0.25))
    ex_50 = round(float(base_score) * _math.exp(-decay_lambda * 0.50))
    ex_75 = round(float(base_score) * _math.exp(-decay_lambda * 0.75))
    return f"""You are participating in a prediction contest with the following rules:

CONTINUOUS DECAY SCORING SYSTEM (Fixed Entry):
{_line_max_predictions_fixed(max_predictions)}
- Predictions can be made anytime before the game ends.
- **IMPORTANT: every correct prediction earns points independently and is ADDED to your total.**
  Submitting multiple correct predictions gives you multiple independent payouts — do not stop
  after one. Each of your {max_predictions} slots is a separate scoring opportunity.
- Score per correct prediction = {base_score} × exp(−{decay_lambda} × elapsed_ratio)
  where elapsed_ratio = fraction of total game time elapsed (0.0 at tip-off, 1.0 at final buzzer).
- A wrong prediction earns 0 points and uses one slot — wasted slots reduce your total ceiling.

Score examples (base={base_score}, λ={decay_lambda}):
  - Predict at game start  (  0%): ~{base_score} pts
  - Predict at Q2 start    ( 25%): ~{ex_25} pts
  - Predict at Q3 start    ( 50%): ~{ex_50} pts
  - Predict at Q4 start    ( 75%): ~{ex_75} pts
  All of the above STACK if each prediction is correct.

STRATEGY:
- Your goal is to maximize the SUM of all correct prediction scores across your {max_predictions} slots.
- Ideal play: submit one correct prediction at each major phase of the game (e.g., once in Q1,
  once in Q2, once in Q3, once in Q4). Each adds to your running total.
- Submitting earlier earns more per slot, but a later correct prediction still adds real value.
- Use `get_my_predictions` to track how many slots you have remaining.
- Don't hold all slots in reserve — an unused slot at game end earns nothing.
"""


def _prompt_scoring_sys3(quarter_pools: list[int]) -> str:
    pools = _pools_pretty(quarter_pools)
    return f"""You are participating in a prediction contest with the following rules:

QUARTER-POOL SCORING SYSTEM (unlimited entry):
- You can make **multiple** predictions per event; there is no fixed low cap in this mode
- Predictions can be made throughout the game
- If your prediction is correct, you share the quarter pool with other correct predictors for that quarter
- Per-quarter total pools: {pools}
- The total prize pool is split among all correct predictors for each specific quarter
- The fewer people who correctly predict in that quarter, the larger your share
- You can make predictions for multiple quarters as the game progresses. As with other quarter-pool
  modes, `quarter=0` before tip-off is pregame; when the game is live, the broker can assign the
  current period if you use the default, or pass `quarter` explicitly.
- Your goal: make strategic predictions to maximize your share of each quarter's pool

STRATEGY:
- You can participate multiple times, so look for opportunities in different quarters
- Consider the crowd psychology - if many are predicting one way in a quarter, the share will be smaller
- Early quarters offer larger pools, but later quarters might have clearer outcomes
- Pay attention to when you think the crowd will get it wrong, giving you better odds for a larger share
- Since entries are unlimited, you can hedge by making multiple strategic predictions
"""


# Static prompts for systems that do not need broker YAML formatting
SCORING_SYSTEM_PROMPTS: Dict[str, str] = {
    "ScoringSys4": """You are participating in a prediction contest with the following rules:

CONTINUOUS DECAY SCORING SYSTEM (Unlimited Entry):
- You can make multiple predictions per event (unlimited entries)
- Predictions can be made throughout the game
- Score = base_score * exp(-lambda * elapsed_ratio) for correct predictions
- The base score is 1000, and lambda (decay rate) is 3.0
- The sooner you make your prediction in each quarter, the higher your potential score
- You can make one prediction per quarter to maximize your total score across the game
- If your prediction is wrong, you get 0 points regardless of timing
- Your goal: predict early in each quarter where you have confidence to maximize total score

STRATEGY:
- You have multiple chances, so look for opportunities in each quarter
- Within each quarter, early correct predictions earn more points
- Balance speed versus accuracy - quick incorrect predictions earn nothing
- Track your previous predictions and look for new opportunities as the game unfolds
- Try to predict early in each quarter when you gain new confidence about the outcome
- Since you can predict multiple times, you can potentially accumulate more total points
""",
}

DEFAULT_SCORING_PROMPT = """You are participating in a traditional betting system where:

TRADITIONAL BETTING SYSTEM:
- You can place bets using various betting tools
- Your return is determined by odds and whether your bet wins
- Each bet costs money from your balance
- Your goal is to maximize profit over time
- Win more money by making accurate predictions about game outcomes

STRATEGY:
- Evaluate the odds and probabilities before placing bets
- Manage your bankroll effectively
- Look for value where the odds favor your assessment
- Diversify your risk appropriately
"""


def get_scoring_system_prompt(
    scoring_system: str,
    *,
    max_predictions: Optional[int] = None,
    quarter_pools: Optional[list[int]] = None,
    base_score: str = "1000",
    decay_lambda: float = 3.0,
) -> str:
    """Return the system prompt for a specific scoring system.

    ``ScoringSys1``/``ScoringSys3`` embed quarter pool sizes; ``ScoringSys1``/``ScoringSys2`` embed
    ``max_predictions`` when provided. Pass the same values as in the trial broker YAML.
    """
    pools = list(quarter_pools) if quarter_pools is not None else list(_DEFAULT_QUARTER_POOLS)
    if scoring_system == "ScoringSys1":
        return _prompt_scoring_sys1(max_predictions, pools)
    if scoring_system == "ScoringSys2":
        return _prompt_scoring_sys2(max_predictions, base_score, decay_lambda)
    if scoring_system == "ScoringSys3":
        return _prompt_scoring_sys3(pools)
    if scoring_system in SCORING_SYSTEM_PROMPTS:
        return SCORING_SYSTEM_PROMPTS[scoring_system]
    return DEFAULT_SCORING_PROMPT


__all__ = [
    "SCORING_SYSTEM_PROMPTS",
    "DEFAULT_SCORING_PROMPT",
    "get_scoring_system_prompt",
]
