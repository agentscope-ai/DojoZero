"""Arena Server configuration."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class CacheConfig(BaseModel):
    """Cache refresh and TTL configuration."""

    # Background refresh
    refresh_interval: float = Field(
        default=5.0, description="How often to refresh all caches (seconds)"
    )
    startup_timeout: float = Field(
        default=30.0, description="Max wait for initial cache population"
    )

    # Time range
    trials_lookback_days: int = Field(
        default=90, description="Days to look back for trials"
    )
    trials_limit: int = Field(default=500, description="Max trials to fetch per query")

    # TTL
    max_cache_ttl: float = Field(
        default=90 * 24 * 3600.0, description="Max TTL for cache entries (seconds)"
    )
    completed_trial_ttl: float = Field(
        default=90 * 24 * 3600.0, description="TTL for completed trial entries"
    )


class ReplayCacheConfig(BaseModel):
    """Replay cache configuration."""

    ttl: float = Field(
        default=90 * 24 * 3600.0, description="TTL for replay cache entries"
    )
    max_entries: int = Field(default=100, description="Max trials to cache for replay")
    core_categories: list[str] = Field(
        default_factory=lambda: ["play", "game_update"],
        description="Categories to track for replay progress",
    )


class QueryLimitsConfig(BaseModel):
    """Query limits configuration."""

    agent_actions_max_trials: int = Field(
        default=5, description="Max trials to process for agent actions"
    )
    agent_actions_limit: int = Field(
        default=20, description="Max agent actions to return"
    )
    leaderboard_limit: int = Field(
        default=20, description="Max leaderboard entries to return"
    )


class WebSocketConfig(BaseModel):
    """WebSocket configuration."""

    heartbeat_interval: float = Field(
        default=5.0, description="Heartbeat interval for WebSocket connections"
    )
    pause_buffer_size: int = Field(
        default=1000, description="Max items to buffer during pause"
    )


class ArenaServerConfig(BaseModel):
    """Arena Server configuration - loadable from YAML.

    Note: Server settings (host, port, static_dir) and trace backend settings
    (backend, query_endpoint, service_name) are configured via CLI arguments.
    """

    cache: CacheConfig = Field(default_factory=CacheConfig)
    replay_cache: ReplayCacheConfig = Field(default_factory=ReplayCacheConfig)
    query_limits: QueryLimitsConfig = Field(default_factory=QueryLimitsConfig)
    websocket: WebSocketConfig = Field(default_factory=WebSocketConfig)

    @classmethod
    def from_yaml(cls, path: Path | str) -> "ArenaServerConfig":
        """Load configuration from a YAML file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path) as f:
            data = yaml.safe_load(f)

        return cls.model_validate(data or {})

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArenaServerConfig":
        """Load configuration from a dictionary."""
        return cls.model_validate(data)

    def to_yaml(self, path: Path | str) -> None:
        """Save configuration to a YAML file."""
        path = Path(path)
        with open(path, "w") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False, sort_keys=False)


# Default configuration instance
DEFAULT_CONFIG = ArenaServerConfig()
