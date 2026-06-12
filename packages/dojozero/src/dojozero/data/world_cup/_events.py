"""World Cup (soccer) event types.

Two tiers of the unified event hierarchy:
- Atomic (Tier 1): WorldCupPlayEvent — single play-by-play action
- Snapshot (Tier 3): WorldCupGameUpdateEvent — full match state w/ curated stats

Lifecycle events (GameInitializeEvent, GameStartEvent, GameResultEvent)
and OddsUpdateEvent live in `dojozero.data._models`.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from dojozero.data._models import (
    BaseGameUpdateEvent,
    BasePlayEvent,
    register_event,
)


# =============================================================================
# Curated soccer stat models (Pydantic, frozen)
# These are nested inside Pydantic events, so BaseModel keeps validation and
# serialization consistent with the event payloads while remaining immutable.
# =============================================================================


class SoccerTeamMatchStats(BaseModel):
    """Team-level match statistics for a single soccer match.

    Curated subset of ESPN soccer boxscore stats. Field names map to ESPN
    statistic ``name`` keys; see ``WorldCupStore._parse_team_stats`` for the
    mapping from raw ESPN to this model.
    """

    model_config = ConfigDict(frozen=True)

    team_id: str = ""
    team_name: str = ""
    team_tricode: str = ""  # FIFA 3-letter code, e.g. "ARG"
    score: int = 0

    # Possession & passing
    possession_pct: float = 0.0
    total_passes: int = 0
    accurate_passes: int = 0
    pass_pct: float = 0.0

    # Attack
    total_shots: int = 0
    shots_on_target: int = 0
    blocked_shots: int = 0
    corners: int = 0
    offsides: int = 0

    # Defense
    total_tackles: int = 0
    effective_tackles: int = 0
    interceptions: int = 0
    saves: int = 0

    # Discipline
    fouls_committed: int = 0
    yellow_cards: int = 0
    red_cards: int = 0


class SoccerPlayerMatchStats(BaseModel):
    """Player-level match statistics for a single soccer match.

    Curated subset of ESPN soccer player stats. Goals/assists/cards are
    promoted to explicit fields; appearances/positions tracked separately.
    """

    model_config = ConfigDict(frozen=True)

    player_id: str = ""
    name: str = ""
    jersey: str = ""
    position: str = ""  # e.g. "F", "M", "D", "G"
    starter: bool = False
    subbed_in: bool = False
    subbed_out: bool = False

    # Output stats
    goals: int = 0
    assists: int = 0
    total_shots: int = 0
    shots_on_target: int = 0

    # Discipline
    fouls_committed: int = 0
    fouls_suffered: int = 0
    yellow_cards: int = 0
    red_cards: int = 0

    # Goalkeeper-relevant (zero for outfield)
    saves: int = 0
    goals_conceded: int = 0


class SoccerGamePlayerStats(BaseModel):
    """Container for home and away player stats in a single match."""

    model_config = ConfigDict(frozen=True)

    home: list[SoccerPlayerMatchStats] = Field(default_factory=list)
    away: list[SoccerPlayerMatchStats] = Field(default_factory=list)


# =============================================================================
# Tier 1: Atomic — WorldCupPlayEvent
# =============================================================================


@register_event
class WorldCupPlayEvent(BasePlayEvent):
    """Soccer play-by-play event.

    Extends ``BasePlayEvent`` with soccer-specific fields. ``action_type``
    holds the human-readable ESPN play type text (e.g., "Goal",
    "Yellow Card", "Substitution", "Kickoff").
    """

    event_type: Literal["event.world_cup_play"] = "event.world_cup_play"

    action_type: str = ""  # ESPN play `type.text`
    action_type_id: str = ""  # ESPN play `type.id` (stable across translations)
    player_id: str = ""
    player_name: str = ""

    def get_dedup_key(self) -> str | None:
        """Return dedup key for soccer plays: {game_id}_play_{play_id}."""
        if self.game_id and self.play_id:
            return f"{self.game_id}_play_{self.play_id}"
        return None


# =============================================================================
# Tier 3: Snapshot — WorldCupGameUpdateEvent
# =============================================================================


@register_event
class WorldCupGameUpdateEvent(BaseGameUpdateEvent):
    """Soccer match snapshot with curated team and player stats."""

    event_type: Literal["event.world_cup_game_update"] = "event.world_cup_game_update"

    home_team_stats: SoccerTeamMatchStats = Field(default_factory=SoccerTeamMatchStats)
    away_team_stats: SoccerTeamMatchStats = Field(default_factory=SoccerTeamMatchStats)
    player_stats: SoccerGamePlayerStats = Field(default_factory=SoccerGamePlayerStats)


__all__ = [
    "SoccerGamePlayerStats",
    "SoccerPlayerMatchStats",
    "SoccerTeamMatchStats",
    "WorldCupGameUpdateEvent",
    "WorldCupPlayEvent",
]
