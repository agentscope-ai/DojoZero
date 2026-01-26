"""NBA-specific trial metadata types.

Extends BaseBettingTrialMetadata with NBA-specific fields.
"""

from typing import NotRequired

from dojozero.betting import BaseBettingTrialMetadata


class NBATrialMetadata(BaseBettingTrialMetadata):
    """NBA-specific trial metadata.

    Inherits all fields from BaseBettingTrialMetadata and adds
    NBA-specific configuration options.

    Additional Fields:
        nba_poll_intervals: Custom poll intervals for NBA data fetching
            Default: {"boxscore": 60.0, "play_by_play": 20.0}
        polymarket_poll_intervals: Custom poll intervals for Polymarket
            Default: {"odds": 300.0}
    """

    nba_poll_intervals: NotRequired[dict[str, float]]
    polymarket_poll_intervals: NotRequired[dict[str, float]]


__all__ = ["NBATrialMetadata"]
