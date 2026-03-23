"""NCAA data store implementation.

NCAA basketball uses the same ESPN data format as NBA, so we reuse the
NBAStore implementation and remap event types to NCAA-specific classes.
"""

import logging
from typing import Any, Sequence

from dojozero.data._models import DataEvent, PollProfile
from dojozero.data._stores import ExternalAPI
from dojozero.data.nba._store import NBAStore
from dojozero.data.ncaa._api import NCAAExternalAPI
from dojozero.data.ncaa._events import (
    NCAAGamePlayerStats,
    NCAAGameUpdateEvent,
    NCAAPlayEvent,
    NCAAPlayerStats,
    NCAATeamGameStats,
)
from dojozero.data.ncaa._state_tracker import GameStateTracker

logger = logging.getLogger(__name__)


class NCAAStore(NBAStore):
    """NCAA data store for polling ESPN NCAA API and emitting events.

    Inherits all parsing and polling logic from NBAStore since NCAA basketball
    uses the same ESPN API structure. Overrides sport_type and remaps NBA event
    types to NCAA event types in the output.
    """

    sport_type: str = "ncaa"

    # NCAA uses same poll profiles as NBA (basketball game structure is identical)
    _POLL_PROFILES: dict[PollProfile, dict[str, float]] = {
        PollProfile.PRE_GAME: {"boxscore": 120.0, "play_by_play": 60.0},
        PollProfile.IN_GAME: {"boxscore": 30.0, "play_by_play": 10.0},
        PollProfile.LATE_GAME: {"boxscore": 15.0, "play_by_play": 5.0},
    }

    def __init__(
        self,
        store_id: str = "ncaa_store",
        api: ExternalAPI | None = None,
        poll_intervals: dict[str, float] | None = None,
        event_emitter: Any = None,
    ):
        super().__init__(
            store_id=store_id,
            api=api or NCAAExternalAPI(),
            poll_intervals=poll_intervals,
            event_emitter=event_emitter,
        )
        # Override state tracker with NCAA-specific one
        self._state = GameStateTracker()

    def _parse_api_response(
        self,
        data: dict[str, Any],
    ) -> Sequence[DataEvent]:
        """Parse API response and remap NBA events to NCAA events.

        Delegates to the parent NBAStore parser, then converts any NBA-specific
        events to their NCAA equivalents.
        """
        events = list(super()._parse_api_response(data))

        remapped: list[DataEvent] = []
        for event in events:
            remapped.append(_remap_event(event))

        return remapped


def _remap_event(event: DataEvent) -> DataEvent:
    """Remap an NBA event to its NCAA equivalent.

    NBA events are converted to NCAA events with the same data.
    Non-NBA events (lifecycle, odds) pass through unchanged.
    """
    from dojozero.data.nba._events import NBAGameUpdateEvent as _NBAUpdate
    from dojozero.data.nba._events import NBAPlayEvent as _NBAPlay

    if isinstance(event, _NBAUpdate):
        return NCAAGameUpdateEvent(
            timestamp=event.timestamp,
            game_id=event.game_id,
            sport="ncaa",
            period=event.period,
            game_clock=event.game_clock,
            game_time_utc=event.game_time_utc,
            home_score=event.home_score,
            away_score=event.away_score,
            home_team_stats=NCAATeamGameStats(
                team_id=event.home_team_stats.team_id,
                team_name=event.home_team_stats.team_name,
                team_city=event.home_team_stats.team_city,
                team_tricode=event.home_team_stats.team_tricode,
                score=event.home_team_stats.score,
            ),
            away_team_stats=NCAATeamGameStats(
                team_id=event.away_team_stats.team_id,
                team_name=event.away_team_stats.team_name,
                team_city=event.away_team_stats.team_city,
                team_tricode=event.away_team_stats.team_tricode,
                score=event.away_team_stats.score,
            ),
            player_stats=NCAAGamePlayerStats(
                home=[
                    NCAAPlayerStats(
                        player_id=p.player_id,
                        name=p.name,
                        position=p.position,
                        statistics=p.statistics,
                    )
                    for p in event.player_stats.home
                ],
                away=[
                    NCAAPlayerStats(
                        player_id=p.player_id,
                        name=p.name,
                        position=p.position,
                        statistics=p.statistics,
                    )
                    for p in event.player_stats.away
                ],
            ),
        )

    if isinstance(event, _NBAPlay):
        return NCAAPlayEvent(
            timestamp=event.timestamp,
            game_id=event.game_id,
            sport="ncaa",
            period=event.period,
            clock=event.clock,
            description=event.description,
            team_tricode=event.team_tricode,
            home_score=event.home_score,
            away_score=event.away_score,
            action_type=event.action_type,
            player_name=event.player_name,
            player_id=event.player_id,
            event_id=event.event_id,
            action_number=event.action_number,
        )

    # Lifecycle events (GameInitialize, GameStart, GameResult) and OddsUpdate
    # pass through unchanged — they are sport-agnostic
    return event


__all__ = ["NCAAStore"]
