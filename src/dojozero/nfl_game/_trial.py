"""Trial builder for NFL game data collection scenario."""

import logging
from typing import Any

from pydantic import BaseModel, Field

from dojozero.core import (
    RuntimeContext,
    DataStreamSpec,
    register_trial_builder,
    TrialSpec,
)
from dojozero.data._factory import build_runtime_context

# Import factories to ensure they are registered
import dojozero.data.nfl._factory  # noqa: F401

logger = logging.getLogger(__name__)


# Mapping from synthetic event types to actual NFL event types
NFL_SYNTHETIC_EVENT_TYPE_MAP: dict[str, list[str]] = {
    "nfl_game_status_change": [
        "nfl_game_start",
        "nfl_game_result",
        "nfl_game_initialize",
    ],
}


class NFLHubConfig(BaseModel):
    """Hub configuration for NFL trials."""

    persistence_file: str = Field(default="outputs/nfl_events.jsonl")
    enable_persistence: bool = Field(default=True)


class NFLDataStreamConfig(BaseModel):
    """Data stream configuration for NFL trials."""

    id: str
    event_type: str


class NFLGameTrialParams(BaseModel):
    """Trial parameters for NFL game data collection.

    This trial builder creates a data collection setup for NFL games
    using the ESPN API. Odds come from ESPN's sportsbook data (DraftKings, etc.).
    """

    # NFL game configuration
    event_id: str = Field(..., description="ESPN event ID (e.g., '401671827')")

    # Hub configuration
    hub: NFLHubConfig | None = Field(default=None)
    hub_id: str = Field(default="nfl_game_hub")
    persistence_file: str | None = Field(default=None)
    enable_persistence: bool | None = Field(default=None)

    # Data streams configuration
    data_streams: list[NFLDataStreamConfig] | None = Field(
        default=None,
        description="List of data streams to create. If not provided, defaults to all NFL event types.",
    )

    # Polling configuration (optional overrides)
    scoreboard_poll_interval: float | None = Field(
        default=None, description="Scoreboard polling interval in seconds (default: 60)"
    )
    summary_poll_interval: float | None = Field(
        default=None, description="Summary polling interval in seconds (default: 30)"
    )
    plays_poll_interval: float | None = Field(
        default=None,
        description="Play-by-play polling interval in seconds (default: 10)",
    )


def _build_trial_spec(
    trial_id: str,
    params: NFLGameTrialParams,
) -> TrialSpec:
    """Return a TrialSpec for NFL game data collection."""

    # Extract hub configuration
    if params.hub:
        hub_id = params.hub_id
        persistence_file = params.hub.persistence_file
        enable_persistence = params.hub.enable_persistence
    else:
        hub_id = params.hub_id
        persistence_file = params.persistence_file or "outputs/nfl_events.jsonl"
        enable_persistence = (
            params.enable_persistence if params.enable_persistence is not None else True
        )

    # Determine data streams
    if params.data_streams:
        stream_configs = params.data_streams
    else:
        # Default: all NFL event types (odds from ESPN sportsbook data)
        stream_configs = [
            NFLDataStreamConfig(
                id="nfl_game_initialize_stream", event_type="nfl_game_initialize"
            ),
            NFLDataStreamConfig(
                id="nfl_game_start_stream", event_type="nfl_game_start"
            ),
            NFLDataStreamConfig(
                id="nfl_game_result_stream", event_type="nfl_game_result"
            ),
            NFLDataStreamConfig(
                id="nfl_game_update_stream", event_type="nfl_game_update"
            ),
            NFLDataStreamConfig(id="nfl_play_stream", event_type="nfl_play"),
            NFLDataStreamConfig(id="nfl_drive_stream", event_type="nfl_drive"),
            # ESPN sportsbook odds (DraftKings, FanDuel, etc.)
            NFLDataStreamConfig(
                id="nfl_odds_update_stream", event_type="nfl_odds_update"
            ),
        ]

    # Build poll intervals override if any provided
    nfl_poll_intervals: dict[str, float] | None = None
    if any(
        [
            params.scoreboard_poll_interval,
            params.summary_poll_interval,
            params.plays_poll_interval,
        ]
    ):
        nfl_poll_intervals = {}
        if params.scoreboard_poll_interval:
            nfl_poll_intervals["scoreboard"] = params.scoreboard_poll_interval
        if params.summary_poll_interval:
            nfl_poll_intervals["summary"] = params.summary_poll_interval
        if params.plays_poll_interval:
            nfl_poll_intervals["plays"] = params.plays_poll_interval

    # Create stream specs
    # Note: For NFL, we use a simple pass-through DataStream that subscribes to hub events
    # A full implementation would have a custom NFL DataStream class
    stream_specs = []
    for stream_config in stream_configs:
        # Determine actual event types (handle synthetic types)
        if stream_config.event_type in NFL_SYNTHETIC_EVENT_TYPE_MAP:
            actual_event_types = NFL_SYNTHETIC_EVENT_TYPE_MAP[stream_config.event_type]
        else:
            actual_event_types = [stream_config.event_type]

        # Use the generic DataHubDataStream for now
        # A full implementation would use a custom NFLDataStream
        from dojozero.data._streams import DataHubDataStream, DataHubDataStreamConfig

        stream_spec_config: DataHubDataStreamConfig = {
            "actor_id": stream_config.id,
            "hub_id": hub_id,
            "event_types": actual_event_types,
        }

        stream_spec = DataStreamSpec(
            actor_id=stream_config.id,
            actor_cls=DataHubDataStream,
            config=stream_spec_config,
        )
        stream_specs.append(stream_spec)

    # Build metadata
    metadata: dict[str, Any] = {
        "sample": "nfl-game",
        "event_id": params.event_id,
        "hub_id": hub_id,
        "persistence_file": persistence_file,
        "enable_persistence": enable_persistence,
        # Store types to create (NFL only - odds come from ESPN)
        "store_types": ["nfl"],
    }

    # Add poll intervals if overridden
    if nfl_poll_intervals:
        metadata["nfl_poll_intervals"] = nfl_poll_intervals

    return TrialSpec(
        trial_id=trial_id,
        data_streams=tuple(stream_specs),
        operators=(),  # No operators for basic data collection
        agents=(),  # No agents for basic data collection
        metadata=metadata,
    )


def _build_nfl_runtime_context(spec: TrialSpec) -> RuntimeContext:
    """Build runtime context for NFL game trial.

    Uses the generic build_runtime_context with registered store factories.

    Args:
        spec: Trial specification

    Returns:
        RuntimeContext with trial_id, data_hubs, stores, and startup callback
    """
    metadata = dict(spec.metadata)  # Convert to regular dict for type compatibility

    # Get hub configuration from metadata
    hub_id_raw = metadata.get("hub_id", "nfl_game_hub")
    hub_id = str(hub_id_raw) if hub_id_raw else "nfl_game_hub"

    persistence_file_raw = metadata.get("persistence_file")
    persistence_file = str(persistence_file_raw) if persistence_file_raw else None

    enable_persistence_raw = metadata.get("enable_persistence", True)
    enable_persistence = (
        bool(enable_persistence_raw) if enable_persistence_raw is not None else True
    )

    # Get store types from metadata (NFL only - odds come from ESPN)
    store_types_raw = metadata.get("store_types", ["nfl"])
    if isinstance(store_types_raw, list):
        store_types = [str(s) for s in store_types_raw]
    else:
        store_types = ["nfl"]

    # Build and return RuntimeContext directly
    return build_runtime_context(
        trial_id=spec.trial_id,
        hub_id=hub_id,
        persistence_file=persistence_file,
        enable_persistence=enable_persistence,
        metadata=metadata,
        store_types=store_types,
    )


register_trial_builder(
    "nfl-game",
    NFLGameTrialParams,
    _build_trial_spec,
    description="NFL game data collection scenario using ESPN API",
    context_builder=_build_nfl_runtime_context,
    example_params={
        "event_id": "401671827",
        "hub": {
            "persistence_file": "outputs/nfl_events.jsonl",
            "enable_persistence": True,
        },
        "data_streams": [
            {"id": "nfl_game_initialize_stream", "event_type": "nfl_game_initialize"},
            {"id": "nfl_game_start_stream", "event_type": "nfl_game_start"},
            {"id": "nfl_game_result_stream", "event_type": "nfl_game_result"},
            {"id": "nfl_game_update_stream", "event_type": "nfl_game_update"},
            {"id": "nfl_play_stream", "event_type": "nfl_play"},
            {"id": "nfl_drive_stream", "event_type": "nfl_drive"},
            # ESPN sportsbook odds (DraftKings, FanDuel, etc.)
            {"id": "nfl_odds_update_stream", "event_type": "nfl_odds_update"},
        ],
    },
)
