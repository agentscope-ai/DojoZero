"""Reward calculation for training.

This module calculates rewards based on betting outcomes.
Phase 1: Simple ROI-based reward from broker statistics.
Phase 2: Could include CLV (Closing Line Value) using final odds.
"""

from decimal import Decimal
from typing import Any

from dojozero.betting._models import Statistics


def calculate_reward(
    stats: Statistics,
    final_odds: dict[str, Any] | None = None,
    game_result: dict[str, Any] | None = None,
) -> float:
    """Calculate reward from betting statistics.

    Phase 1 implementation uses ROI directly from broker statistics.
    The broker already computes ROI based on the odds at bet placement time.

    Args:
        stats: Broker statistics for the agent (from broker.get_statistics)
        final_odds: Last odds_update event (for Phase 2 CLV calculation)
        game_result: Game result event (for verification)

    Returns:
        Reward value (ROI for Phase 1)
    """
    # No bets placed - neutral reward
    if stats.total_bets == 0 or stats.total_wagered == 0:
        return 0.0

    # Phase 1: Use ROI directly
    # ROI = (net_profit / total_wagered) * 100
    # Note: stats.roi is already in percentage form
    return float(stats.roi)


def calculate_reward_with_clv(
    stats: Statistics,
    final_odds: dict[str, Any],
    agent_bets: list[dict[str, Any]],
) -> tuple[float, dict[str, float]]:
    """Calculate reward with Closing Line Value bonus.

    CLV measures how well the agent timed their bets relative to
    the closing (final) odds. Positive CLV indicates edge.

    Args:
        stats: Broker statistics for the agent
        final_odds: Last odds_update event
        agent_bets: List of bets placed by the agent

    Returns:
        Tuple of (total_reward, breakdown_dict)
    """
    # Base reward is ROI
    roi = float(stats.roi) if stats.total_wagered > 0 else 0.0

    # Calculate CLV bonus
    clv_bonus = _calculate_clv(agent_bets, final_odds)

    # Combined reward (weights can be tuned)
    total_reward = roi + 0.1 * clv_bonus  # Small CLV bonus

    breakdown = {
        "roi": roi,
        "clv": clv_bonus,
        "total": total_reward,
    }

    return total_reward, breakdown


def _calculate_clv(
    agent_bets: list[dict[str, Any]],
    final_odds: dict[str, Any],
) -> float:
    """Calculate Closing Line Value for the agent's bets.

    CLV = (bet_probability - closing_probability) / closing_probability

    Positive CLV means the agent got better odds than closing.

    Args:
        agent_bets: List of bets with probability at placement
        final_odds: Final odds_update event

    Returns:
        Average CLV across all bets (in percentage)
    """
    if not agent_bets or not final_odds:
        return 0.0

    odds_data = final_odds.get("odds", {})
    moneyline = odds_data.get("moneyline", {})

    closing_probs = {
        "home": moneyline.get("home_probability", 0.5),
        "away": moneyline.get("away_probability", 0.5),
    }

    clv_values = []
    for bet in agent_bets:
        selection = bet.get("selection", "").lower()
        bet_prob = bet.get("probability", 0.5)

        if selection in closing_probs:
            closing_prob = closing_probs[selection]
            if closing_prob > 0:
                # CLV: how much better was our probability vs closing
                clv = (bet_prob - closing_prob) / closing_prob * 100
                clv_values.append(clv)

    if not clv_values:
        return 0.0

    return sum(clv_values) / len(clv_values)


def normalize_reward(
    reward: float,
    min_reward: float = -100.0,
    max_reward: float = 100.0,
    target_range: tuple[float, float] = (-1.0, 1.0),
) -> float:
    """Normalize reward to a target range.

    Useful for stabilizing RL training with bounded rewards.

    Args:
        reward: Raw reward value
        min_reward: Expected minimum reward
        max_reward: Expected maximum reward
        target_range: Target (min, max) range for normalized reward

    Returns:
        Normalized reward
    """
    # Clip to expected range
    clipped = max(min_reward, min(max_reward, reward))

    # Scale to target range
    t_min, t_max = target_range
    normalized = t_min + (clipped - min_reward) / (max_reward - min_reward) * (
        t_max - t_min
    )

    return normalized


def create_sparse_reward(
    stats: Statistics,
    game_result: dict[str, Any] | None = None,
) -> float:
    """Create a sparse reward based on outcome only.

    Alternative reward function that only gives reward at episode end.

    Args:
        stats: Broker statistics
        game_result: Game result event

    Returns:
        +1 for positive profit, -1 for loss, 0 for no bets
    """
    if stats.total_bets == 0:
        return 0.0

    if stats.net_profit > 0:
        return 1.0
    elif stats.net_profit < 0:
        return -1.0
    else:
        return 0.0
