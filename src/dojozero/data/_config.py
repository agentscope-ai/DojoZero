"""Shared configuration models for trial params.

These Pydantic models are used in trial builder params (YAML) to configure
data infrastructure. They are distinct from actor configs (TypedDicts in
_streams.py) which are used for actor instantiation.

Hierarchy:
- Trial params YAML -> HubConfig, TrialDataStreamConfig (Pydantic, validated at build time)
- Trial builder -> converts to DataStreamSpec with DataHubDataStreamConfig (TypedDict)
- Actor.from_dict() -> receives DataHubDataStreamConfig
"""

from typing import Any

from pydantic import BaseModel, Field


class HubConfig(BaseModel):
    """Configuration for DataHub persistence in trial params.

    Used by trial builders to configure event persistence to JSONL files.
    This is for the `hub:` field in trial params YAML.

    Note: Persistence is always enabled. The persistence_file field is required.
    """

    persistence_file: str = Field(
        ..., description="Path to JSONL file for event persistence"
    )


class TrialDataStreamConfig(BaseModel):
    """Configuration for a data stream in trial params.

    Used in the `data_streams:` list in trial params YAML to define
    which event types each stream should subscribe to.

    Note: This is distinct from DataHubDataStreamConfig in _streams.py
    which is the actor config TypedDict used at instantiation time.
    """

    id: str = Field(..., description="Unique identifier for this data stream")
    event_type: str = Field(
        ..., description="Event type this stream subscribes to (e.g., 'play_by_play')"
    )
    initializer: dict[str, Any] | None = Field(
        default=None,
        description="Optional initializer configuration (e.g., search_queries for websearch)",
    )


# Backwards compatibility alias
DataStreamConfig = TrialDataStreamConfig

__all__ = [
    "DataStreamConfig",  # Alias for backwards compatibility
    "HubConfig",
    "TrialDataStreamConfig",
]
