"""Dashboard Server package for DojoZero.

This package provides the Dashboard Server functionality including:
- REST API for trial management
- Trial scheduling and game monitoring
- Game discovery from NBA/ESPN APIs

Moved from core module to keep server-related code separate from core abstractions.
"""

from ._game_discovery import (
    GameInfo,
    NBAGameFetcher,
    NFLGameFetcher,
    TeamInfo,
    VenueInfo,
)
from ._scheduler import (
    FileSchedulerStore,
    ScheduledTrial,
    ScheduledTrialPhase,
    ScheduleManager,
    SchedulerStore,
    TrialSource,
    TrialSourceConfig,
    TrialSourceStore,
)
from ._gateway_routing import (
    GatewayRouter,
    create_gateway_router,
)
from ._server import (
    DashboardServerState,
    create_dashboard_app,
    get_server_state,
    run_dashboard_server,
)
from ._trial_manager import (
    QueuedTrial,
    QueuedTrialPhase,
    TrialManager,
)
from ._types import (
    BacktestMetadata,
    BroadcastDataDict,
    ErrorResponse,
    GameMetadata,
    HealthResponse,
    HubMetadata,
    InitialTrialSourceDict,
    OddsDataDict,
    ScheduledGameMetadata,
    ScheduledMetadata,
    ScheduledTrialResponse,
    TeamDataDict,
    TrialInfoResponse,
    TrialSourceConfigDict,
    TrialSourceResponse,
    VenueDataDict,
)

__all__ = [
    # Server
    "create_dashboard_app",
    "run_dashboard_server",
    "DashboardServerState",
    "get_server_state",
    # Gateway Routing
    "GatewayRouter",
    "create_gateway_router",
    # Trial Manager
    "TrialManager",
    "QueuedTrial",
    "QueuedTrialPhase",
    # Scheduler
    "ScheduleManager",
    "ScheduledTrial",
    "ScheduledTrialPhase",
    "SchedulerStore",
    "FileSchedulerStore",
    # Trial Sources
    "TrialSource",
    "TrialSourceConfig",
    "TrialSourceStore",
    # Game Discovery
    "GameInfo",
    "TeamInfo",
    "VenueInfo",
    "NBAGameFetcher",
    "NFLGameFetcher",
    # TypedDicts for metadata
    "BacktestMetadata",
    "GameMetadata",
    "HubMetadata",
    "ScheduledGameMetadata",
    "ScheduledMetadata",
    # TypedDicts for API responses
    "ErrorResponse",
    "HealthResponse",
    "ScheduledTrialResponse",
    "TrialInfoResponse",
    "TrialSourceResponse",
    # TypedDicts for configuration
    "InitialTrialSourceDict",
    "TrialSourceConfigDict",
    # TypedDicts for game discovery
    "BroadcastDataDict",
    "OddsDataDict",
    "TeamDataDict",
    "VenueDataDict",
]
