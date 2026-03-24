"""Built-in betting strategies for dojozero-agent.

Strategies implement the Strategy protocol and are loaded by module path.
Custom strategies should define a `Strategy` class with a `decide` method.

Example usage:
    dojozero-agent start <trial-id> --strategy dojozero_client._strategy.conservative

Built-in strategies:
    - conservative: Only bet on large edges (>10%)
    - momentum: Follow recent odds trends
    - manual: No auto-betting, just notifications
"""

from __future__ import annotations

from typing import Any


class ConservativeStrategy:
    """Conservative betting strategy - only bet on large edges.

    Configuration:
        min_edge: Minimum probability edge required (default: 0.10)
        bet_size: Fixed bet amount (default: 50)
        prefer_home: Prefer home team when edge exists (default: True)
    """

    def __init__(self, config: dict[str, Any]):
        self.min_edge = config.get("min_edge", 0.10)
        self.bet_size = config.get("bet_size", 50)
        self.prefer_home = config.get("prefer_home", True)

    def decide(
        self, event: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Bet only when there's a significant edge."""
        if "odds" not in event.get("type", ""):
            return None

        payload = event.get("payload", {})
        home_prob = payload.get("homeProbability", payload.get("home_probability", 0.5))
        away_prob = payload.get("awayProbability", payload.get("away_probability", 0.5))

        # Check if balance allows betting
        balance = state.get("balance", 0)
        if balance < self.bet_size:
            return None

        # Bet on home if probability > 50% + min_edge
        if self.prefer_home and home_prob > 0.5 + self.min_edge:
            return {
                "market": "moneyline",
                "selection": "home",
                "amount": self.bet_size,
            }

        # Bet on away if probability > 50% + min_edge
        if away_prob > 0.5 + self.min_edge:
            return {
                "market": "moneyline",
                "selection": "away",
                "amount": self.bet_size,
            }

        return None


class MomentumStrategy:
    """Momentum betting strategy - follow recent odds trends.

    Bets in the direction of odds movement when momentum is strong.

    Configuration:
        momentum_threshold: Minimum odds change to trigger bet (default: 0.05)
        bet_size: Fixed bet amount (default: 50)
        lookback: Number of recent odds to consider (default: 3)
    """

    def __init__(self, config: dict[str, Any]):
        self.momentum_threshold = config.get("momentum_threshold", 0.05)
        self.bet_size = config.get("bet_size", 50)
        self._odds_history: list[float] = []
        self._max_history = config.get("lookback", 3)

    def decide(
        self, event: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Bet with momentum when odds are trending."""
        if "odds" not in event.get("type", ""):
            return None

        payload = event.get("payload", {})
        home_prob = payload.get("homeProbability", payload.get("home_probability", 0.5))

        # Track odds history
        self._odds_history.append(home_prob)
        if len(self._odds_history) > self._max_history:
            self._odds_history.pop(0)

        # Need at least 2 data points
        if len(self._odds_history) < 2:
            return None

        # Check if balance allows betting
        balance = state.get("balance", 0)
        if balance < self.bet_size:
            return None

        # Calculate momentum (change from oldest to newest)
        momentum = self._odds_history[-1] - self._odds_history[0]

        # Bet with momentum if strong enough
        if momentum > self.momentum_threshold:
            return {
                "market": "moneyline",
                "selection": "home",
                "amount": self.bet_size,
            }
        elif momentum < -self.momentum_threshold:
            return {
                "market": "moneyline",
                "selection": "away",
                "amount": self.bet_size,
            }

        return None


class ManualStrategy:
    """Manual strategy - no auto-betting.

    Use this when you want to receive events and notifications
    but make betting decisions manually.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        del config  # unused

    def decide(
        self, event: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Never auto-bet."""
        del event, state  # unused
        return None


# Aliases for convenience
conservative = ConservativeStrategy
momentum = MomentumStrategy
manual = ManualStrategy

# Default strategy alias
Strategy = ManualStrategy

__all__ = [
    "ConservativeStrategy",
    "MomentumStrategy",
    "ManualStrategy",
    "Strategy",
    "conservative",
    "momentum",
    "manual",
]
