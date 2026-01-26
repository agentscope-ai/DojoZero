"""NFL-specific trial metadata types.

Extends BaseBettingTrialMetadata with NFL-specific fields.
"""

from typing import NotRequired

from dojozero.betting import BaseBettingTrialMetadata


class NFLTrialMetadata(BaseBettingTrialMetadata):
    """NFL-specific trial metadata.

    Inherits all fields from BaseBettingTrialMetadata and adds
    NFL-specific configuration options.

    Additional Fields:
        nfl_poll_intervals: Custom poll intervals for NFL data fetching
            Default: {"scoreboard": 60.0, "summary": 30.0, "plays": 10.0}
        polymarket_poll_intervals: Custom poll intervals for Polymarket
            Default: {"odds": 300.0}
    """

    nfl_poll_intervals: NotRequired[dict[str, float]]
    polymarket_poll_intervals: NotRequired[dict[str, float]]


__all__ = ["NFLTrialMetadata"]
