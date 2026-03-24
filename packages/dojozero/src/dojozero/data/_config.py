"""Shared configuration models for trial params.

These Pydantic models are used in trial builder params (YAML) to configure
data infrastructure. They are distinct from actor configs (TypedDicts in
_streams.py) which are used for actor instantiation.

Hierarchy:
- Trial params YAML -> HubConfig, TrialDataStreamConfig (Pydantic, validated at build time)
- Trial builder -> converts to DataStreamSpec with DataHubDataStreamConfig (TypedDict)
- Actor.from_dict() -> receives DataHubDataStreamConfig
"""

from pydantic import BaseModel, Field


class HubConfig(BaseModel):
    """Configuration for DataHub persistence in trial params.

    Used by trial builders to configure event persistence to JSONL files.
    This is for the ``hub:`` field in trial params YAML.

    Note: Persistence is always enabled. The persistence_file is optional here
    because for auto-scheduled trials, it gets populated dynamically by the
    scheduler based on data_dir. Trial builders should validate that it's set
    before building the trial spec.
    """

    persistence_file: str | None = Field(
        default=None,
        description=(
            "Path to JSONL file for event persistence. Required for live trials. "
            "For auto-scheduled trials, this is populated by the scheduler."
        ),
    )


class TrialDataStreamConfig(BaseModel):
    """Configuration for a data stream in trial params.

    Used in the ``data_streams:`` list in trial params YAML to define
    which event types each stream should subscribe to.

    YAML example::

        data_streams:
          - id: pre_game_insights_stream
            event_types:
              - injury_report
              - power_ranking
              - expert_prediction

    Note: This is distinct from DataHubDataStreamConfig in _streams.py
    which is the actor config TypedDict used at instantiation time.
    """

    id: str = Field(..., description="Unique identifier for this data stream")
    event_type: str = Field(
        default="",
        description="Single event type (legacy). Prefer event_types list.",
    )
    event_types: list[str] = Field(
        default_factory=list,
        description="Event type suffixes this stream subscribes to (e.g., 'injury_report', 'nba_game_update').",
    )


__all__ = [
    "HubConfig",
    "TrialDataStreamConfig",
]
