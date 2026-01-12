"""NFL game data collection scenario.

This module provides a trial builder for collecting NFL game data
using the ESPN API and the generic store factory infrastructure.

Usage:
    # Generate example config
    uv run dojozero get-builder nfl-game --example

    # Run a trial
    uv run dojozero run --params configs/nfl-game.yaml
"""

from dojozero.nfl_game._trial import (
    NFLGameTrialParams,
    NFLHubConfig,
    NFLDataStreamConfig,
)

__all__ = [
    "NFLGameTrialParams",
    "NFLHubConfig",
    "NFLDataStreamConfig",
]
