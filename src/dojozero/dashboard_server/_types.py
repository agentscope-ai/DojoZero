"""Type definitions for the Dashboard Server.

This module provides TypedDicts for strongly-typed dictionary structures
used throughout the dashboard server, including:
- Trial metadata (game info, backtest config, scheduling info)
- API response shapes
- Configuration dictionaries for trial sources

Using TypedDicts improves type safety over dict[str, Any] by:
- Providing IDE autocomplete for dictionary keys
- Catching typos and missing keys at type-check time
- Documenting expected dictionary shapes
"""

from typing import Any, TypedDict


# =============================================================================
# Trial Metadata TypedDicts
# =============================================================================


class GameMetadata(TypedDict, total=False):
    """Core game information metadata.

    These fields describe the game itself (teams, date, etc.) and are
    populated when scheduling trials for specific games.
    """

    game_short_name: str  # e.g., "LAL @ BOS"
    home_team: str  # Full team name, e.g., "Boston Celtics"
    away_team: str  # Full team name, e.g., "Los Angeles Lakers"
    home_tricode: str  # Team abbreviation, e.g., "BOS"
    away_tricode: str  # Team abbreviation, e.g., "LAL"
    game_date: str  # YYYY-MM-DD format


class ScheduledGameMetadata(GameMetadata, total=False):
    """Extended metadata for trials scheduled from a trial source.

    Inherits all fields from GameMetadata and adds scheduling-related
    fields that link the trial back to its source and event.
    """

    source_id: str  # Trial source that scheduled this trial
    event_id: str  # ESPN event ID
    sport_type: str  # "nba" or "nfl"


class BacktestMetadata(TypedDict, total=False):
    """Metadata for backtest/replay trials.

    These fields are populated when running a trial in backtest mode
    from a persisted event file.
    """

    backtest_file: str  # Path to the event JSONL file
    backtest_mode: bool  # True if in backtest mode
    backtest_speed: float  # Playback speed multiplier
    backtest_max_sleep: float  # Maximum sleep between events
    builder_name: str  # Trial builder used (e.g., "nba", "nfl")


class ScheduledMetadata(TypedDict, total=False):
    """Metadata added when a trial is launched from a schedule.

    These fields link the running trial back to its schedule entry.
    """

    schedule_id: str  # ScheduledTrial.schedule_id
    sport_type: str  # "nba" or "nfl"
    event_id: str  # ESPN event ID


class HubMetadata(TypedDict, total=False):
    """Metadata for hub configuration passed to context builders.

    These fields are extracted from trial metadata to configure
    the DataHub and stores.
    """

    hub_id: str
    persistence_file: str
    event_types: list[str]
    store_types: list[str]


# =============================================================================
# API Response TypedDicts
# =============================================================================


class TrialInfoResponse(TypedDict, total=False):
    """Response shape for trial info in list/detail endpoints."""

    id: str
    phase: str
    metadata: dict[str, Any]
    error: str | None
    source: str  # "queue" or "dashboard"
    agents: list[dict[str, Any]]  # Only when source="dashboard"
    queue_position: int  # Only in submit response
    running_count: int  # Only in submit response
    message: str  # Status message


class ErrorResponse(TypedDict):
    """Response shape for error responses."""

    error: str


class HealthResponse(TypedDict):
    """Response shape for health check endpoint."""

    status: str
    trial_manager: dict[str, int]
    scheduling_enabled: bool


class ScheduledTrialResponse(TypedDict, total=False):
    """Response shape for scheduled trial list/detail."""

    count: int
    scheduled_trials: list[dict[str, Any]]


class TrialSourceResponse(TypedDict, total=False):
    """Response shape for trial source list/detail."""

    count: int
    sources: list[dict[str, Any]]


# =============================================================================
# Trial Source Configuration TypedDicts
# =============================================================================


class TrialSourceConfigDict(TypedDict, total=False):
    """Dictionary shape for trial source configuration.

    Used when loading trial source config from initial_trial_sources or storage.

    Note: scenario_config is kept as dict[str, Any] because:
    1. It's loaded from YAML and modified dynamically (adding espn_game_id, etc.)
    2. It's validated by Pydantic models (NBATrialParams, NFLTrialParams) when
       the trial is built, so type safety is enforced at that point.
    3. The structure varies by scenario (NBA vs NFL have different fields).
    """

    scenario_name: str
    scenario_config: dict[str, Any]
    pre_start_hours: float
    check_interval_seconds: float
    auto_stop_on_completion: bool
    data_dir: str | None
    sync_interval_seconds: float


class InitialTrialSourceDict(TypedDict):
    """Dictionary shape for initial trial sources passed to server."""

    source_id: str
    sport_type: str
    config: TrialSourceConfigDict


# =============================================================================
# Game Discovery TypedDicts
# =============================================================================


class TeamDataDict(TypedDict, total=False):
    """Dictionary shape for team data parsed from ESPN API."""

    teamId: str
    displayName: str
    teamTricode: str
    score: str | int
    teamCity: str
    shortDisplayName: str
    color: str
    alternateColor: str
    logo: str
    record: str


class VenueDataDict(TypedDict, total=False):
    """Dictionary shape for venue data parsed from ESPN API."""

    venueId: str
    name: str
    city: str
    state: str
    indoor: bool


class BroadcastDataDict(TypedDict):
    """Dictionary shape for broadcast data parsed from ESPN API."""

    market: str
    names: list[str]


class OddsDataDict(TypedDict, total=False):
    """Dictionary shape for odds data parsed from ESPN API."""

    provider: str
    spread: float
    overUnder: float
    homeMoneyLine: int
    awayMoneyLine: int


__all__ = [
    # Metadata
    "BacktestMetadata",
    "GameMetadata",
    "HubMetadata",
    "ScheduledGameMetadata",
    "ScheduledMetadata",
    # API Responses
    "ErrorResponse",
    "HealthResponse",
    "ScheduledTrialResponse",
    "TrialInfoResponse",
    "TrialSourceResponse",
    # Trial Source Config
    "InitialTrialSourceDict",
    "TrialSourceConfigDict",
    # Game Discovery
    "BroadcastDataDict",
    "OddsDataDict",
    "TeamDataDict",
    "VenueDataDict",
]
