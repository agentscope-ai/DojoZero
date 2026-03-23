"""NCAA pre-game betting DataStream with web search event class lifecycle.

Reuses the NBA DataStream since NCAA basketball uses the same data structure.
Only the sport_type context differs (ESPN sport/league parameters).
"""

from dojozero.nba._datastream import (
    NBAPreGameBettingDataHubDataStream,
    NBAPreGameBettingDataHubDataStreamConfig,
)

# NCAA reuses the NBA DataStream and config unchanged.
# The sport_type is set via RuntimeContext, which routes ESPN API calls
# to the correct sport/league.
NCAAPreGameBettingDataHubDataStream = NBAPreGameBettingDataHubDataStream
NCAAPreGameBettingDataHubDataStreamConfig = NBAPreGameBettingDataHubDataStreamConfig

__all__ = [
    "NCAAPreGameBettingDataHubDataStream",
    "NCAAPreGameBettingDataHubDataStreamConfig",
]
