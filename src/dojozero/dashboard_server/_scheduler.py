"""Schedule Manager for DojoZero Dashboard Server.

Handles automatic scheduling of trials based on registered trial sources:
- Trial sources define scenario templates for NBA/NFL games
- Server periodically syncs with external APIs to discover upcoming games
- Automatically schedules trials based on registered sources
- Monitors game status and stops trials when games complete
- File-based persistence for crash recovery
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from dojozero.core._registry import (
    TrialBuilderNotFoundError,
    get_trial_builder_definition,
)

from ._game_discovery import GameInfo, NBAGameFetcher, NFLGameFetcher
from ._trial_manager import TrialManager

LOGGER = logging.getLogger("dojozero.scheduler")


# =============================================================================
# Trial Source Models
# =============================================================================


class TrialSourceConfig(BaseModel):
    """Configuration for a trial source."""

    scenario_name: str
    scenario_config: dict[str, Any] = {}
    pre_start_hours: float = 2.0
    check_interval_seconds: float = 60.0
    auto_stop_on_completion: bool = True
    data_dir: str | None = None
    sync_interval_seconds: float = (
        300.0  # How often to sync with external APIs (5 min default)
    )


@dataclass
class TrialSource:
    """A registered trial source for automatic scheduling.

    Trial sources define how to schedule trials for a sport type.
    The server periodically syncs with ESPN API scoreboard to discover
    upcoming games and automatically schedules trials.
    """

    source_id: str
    sport_type: str  # "nba" or "nfl"
    config: TrialSourceConfig
    enabled: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_sync_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "source_id": self.source_id,
            "sport_type": self.sport_type,
            "config": self.config.model_dump(),
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
            "last_sync_at": self.last_sync_at.isoformat()
            if self.last_sync_at
            else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrialSource":
        """Create from dictionary."""
        from dateutil import parser

        config_data = data.get("config", {})
        config = TrialSourceConfig(**config_data)

        last_sync_at = None
        if data.get("last_sync_at"):
            last_sync_at = parser.parse(data["last_sync_at"])

        return cls(
            source_id=data["source_id"],
            sport_type=data["sport_type"],
            config=config,
            enabled=data.get("enabled", True),
            created_at=parser.parse(data["created_at"])
            if data.get("created_at")
            else datetime.now(timezone.utc),
            last_sync_at=last_sync_at,
        )


class TrialSourceStore(Protocol):
    """Protocol for trial source persistence."""

    def save_sources(self, sources: list[TrialSource]) -> None:
        """Save all trial sources."""
        ...

    def load_sources(self) -> list[TrialSource]:
        """Load all trial sources."""
        ...


class ScheduledTrialPhase(str, Enum):
    """Phase of a scheduled trial."""

    WAITING = "waiting"  # Waiting for scheduled start time
    LAUNCHING = "launching"  # Currently launching the trial
    RUNNING = "running"  # Trial is running
    MONITORING = "monitoring"  # Monitoring game status
    COMPLETED = "completed"  # Trial completed (game finished)
    FAILED = "failed"  # Trial failed
    CANCELLED = "cancelled"  # Cancelled by user


@dataclass
class ScheduledTrial:
    """A scheduled trial waiting to be launched."""

    schedule_id: str
    scenario_name: str
    scenario_config: dict[str, Any]
    sport_type: str  # "nba" or "nfl"
    event_id: str  # ESPN event ID (used for both NBA and NFL)
    event_time: datetime  # When the game starts
    scheduled_start_time: datetime  # When to launch trial
    pre_start_hours: float
    check_interval_seconds: float
    auto_stop_on_completion: bool
    phase: ScheduledTrialPhase = ScheduledTrialPhase.WAITING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    launched_trial_id: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    game_date: str | None = None  # For status polling
    # Grace period handling for already-finished games
    monitoring_started_at: datetime | None = None  # When monitoring began
    initial_game_status: int | None = None  # Game status when monitoring started

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "schedule_id": self.schedule_id,
            "scenario_name": self.scenario_name,
            "scenario_config": self.scenario_config,
            "sport_type": self.sport_type,
            "event_id": self.event_id,
            "event_time": self.event_time.isoformat(),
            "scheduled_start_time": self.scheduled_start_time.isoformat(),
            "pre_start_hours": self.pre_start_hours,
            "check_interval_seconds": self.check_interval_seconds,
            "auto_stop_on_completion": self.auto_stop_on_completion,
            "phase": self.phase.value,
            "created_at": self.created_at.isoformat(),
            "launched_trial_id": self.launched_trial_id,
            "error": self.error,
            "metadata": self.metadata,
            "game_date": self.game_date,
            "monitoring_started_at": self.monitoring_started_at.isoformat()
            if self.monitoring_started_at
            else None,
            "initial_game_status": self.initial_game_status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduledTrial":
        """Create from dictionary."""
        from dateutil import parser

        monitoring_started_at = None
        if data.get("monitoring_started_at"):
            monitoring_started_at = parser.parse(data["monitoring_started_at"])

        return cls(
            schedule_id=data["schedule_id"],
            scenario_name=data["scenario_name"],
            scenario_config=data.get("scenario_config", {}),
            sport_type=data["sport_type"],
            event_id=data["event_id"],
            event_time=parser.parse(data["event_time"]),
            scheduled_start_time=parser.parse(data["scheduled_start_time"]),
            pre_start_hours=data.get("pre_start_hours", 2.0),
            check_interval_seconds=data.get("check_interval_seconds", 60.0),
            auto_stop_on_completion=data.get("auto_stop_on_completion", True),
            phase=ScheduledTrialPhase(data.get("phase", "waiting")),
            created_at=parser.parse(data["created_at"])
            if data.get("created_at")
            else datetime.now(timezone.utc),
            launched_trial_id=data.get("launched_trial_id"),
            error=data.get("error"),
            metadata=data.get("metadata", {}),
            game_date=data.get("game_date"),
            monitoring_started_at=monitoring_started_at,
            initial_game_status=data.get("initial_game_status"),
        )


class SchedulerStore(Protocol):
    """Protocol for schedule persistence."""

    def save(self, schedules: list[ScheduledTrial]) -> None:
        """Save all schedules."""
        ...

    def load(self) -> list[ScheduledTrial]:
        """Load all schedules."""
        ...

    def save_sources(self, sources: list[TrialSource]) -> None:
        """Save all trial sources."""
        ...

    def load_sources(self) -> list[TrialSource]:
        """Load all trial sources."""
        ...


class FileSchedulerStore:
    """File-based scheduler store using JSON.

    Stores both scheduled trials and trial sources in separate files
    within the specified directory.
    """

    def __init__(self, store_dir: Path):
        """Initialize with store directory path.

        Args:
            store_dir: Path to the directory for scheduler persistence.
                       Creates schedules.json and trial_sources.json in this directory.
        """
        self._store_dir = store_dir
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._store_dir / "schedules.json"
        self._sources_path = self._store_dir / "trial_sources.json"

    def save(self, schedules: list[ScheduledTrial]) -> None:
        """Save all schedules to file."""
        data = [s.to_dict() for s in schedules]
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        LOGGER.debug("Saved %d schedules to %s", len(schedules), self._path)

    def load(self) -> list[ScheduledTrial]:
        """Load all schedules from file."""
        if not self._path.exists():
            return []

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            schedules = [ScheduledTrial.from_dict(d) for d in data]
            LOGGER.info("Loaded %d schedules from %s", len(schedules), self._path)
            return schedules
        except Exception as e:
            LOGGER.error("Error loading schedules from %s: %s", self._path, e)
            return []

    def save_sources(self, sources: list[TrialSource]) -> None:
        """Save all trial sources to file."""
        data = [s.to_dict() for s in sources]
        with open(self._sources_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        LOGGER.debug("Saved %d trial sources to %s", len(sources), self._sources_path)

    def load_sources(self) -> list[TrialSource]:
        """Load all trial sources from file."""
        if not self._sources_path.exists():
            return []

        try:
            with open(self._sources_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            sources = [TrialSource.from_dict(d) for d in data]
            LOGGER.info(
                "Loaded %d trial sources from %s", len(sources), self._sources_path
            )
            return sources
        except Exception as e:
            LOGGER.error(
                "Error loading trial sources from %s: %s", self._sources_path, e
            )
            return []


class ScheduleManager:
    """Manages scheduled trials based on registered trial sources.

    Features:
    - Registers trial sources that define scenario templates for sports
    - Periodically syncs with external APIs to discover upcoming games
    - Automatically schedules trials for discovered games
    - Waits until scheduled start time to launch trials
    - Monitors game status and stops trials when games complete
    - Persists schedules and sources to file for crash recovery
    - Limits concurrent trial launches to prevent server overload
    - Grace period handling for already-finished games
    """

    def __init__(
        self,
        trial_manager: TrialManager,
        store: SchedulerStore | None = None,
        sync_interval_seconds: float = 300.0,  # 5 minutes default
        max_concurrent_launches: int = 10,  # Max trials to launch concurrently
        grace_period_seconds: float = 300.0,  # 5 min grace period for finished games
    ):
        """Initialize the ScheduleManager.

        Args:
            trial_manager: TrialManager instance for launching trials
            store: Optional persistence store. If None, schedules are in-memory only.
            sync_interval_seconds: How often to sync trial sources with external APIs
            max_concurrent_launches: Maximum number of trials to launch concurrently.
                This prevents overwhelming the server when multiple games have
                similar start times. Default: 10
            grace_period_seconds: Grace period in seconds for games that are already
                finished when monitoring starts. This allows time for final data
                collection before stopping the trial. Default: 300 (5 minutes)
        """
        self._trial_manager = trial_manager
        self._store = store
        self._sync_interval = sync_interval_seconds
        self._max_concurrent_launches = max_concurrent_launches
        self._grace_period_seconds = grace_period_seconds

        # All scheduled trials by ID
        self._schedules: dict[str, ScheduledTrial] = {}

        # All trial sources by ID
        self._sources: dict[str, TrialSource] = {}

        # Track which event IDs have been scheduled for each source
        # Key: (source_id, event_id), Value: schedule_id
        self._scheduled_events: dict[tuple[str, str], str] = {}

        # Semaphore to limit concurrent trial launches
        self._launch_semaphore = asyncio.Semaphore(max_concurrent_launches)

        # Background tasks
        self._scheduler_task: asyncio.Task[None] | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._sync_task: asyncio.Task[None] | None = None
        self._shutdown_event = asyncio.Event()

        # Game fetchers
        self._nba_fetcher = NBAGameFetcher()
        self._nfl_fetcher = NFLGameFetcher()

    async def start(self) -> None:
        """Start the schedule manager."""
        self._shutdown_event.clear()

        # Load persisted schedules and sources
        if self._store:
            # Load trial sources
            loaded_sources = self._store.load_sources()
            for source in loaded_sources:
                self._sources[source.source_id] = source

            # Load scheduled trials
            loaded = self._store.load()
            for s in loaded:
                # Skip completed/cancelled schedules
                if s.phase not in (
                    ScheduledTrialPhase.COMPLETED,
                    ScheduledTrialPhase.CANCELLED,
                    ScheduledTrialPhase.FAILED,
                ):
                    self._schedules[s.schedule_id] = s
                    # Track scheduled events
                    source_id = s.metadata.get("source_id")
                    if source_id:
                        self._scheduled_events[(source_id, s.event_id)] = s.schedule_id

        # Start background tasks
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        self._sync_task = asyncio.create_task(self._sync_loop())

        LOGGER.info(
            "ScheduleManager started (loaded %d sources, %d active schedules)",
            len(self._sources),
            len(self._schedules),
        )

    async def stop(self) -> None:
        """Stop the schedule manager."""
        LOGGER.info("ScheduleManager stopping...")
        self._shutdown_event.set()

        # Cancel background tasks
        for task, name in [
            (self._scheduler_task, "scheduler"),
            (self._monitor_task, "monitor"),
            (self._sync_task, "sync"),
        ]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                LOGGER.debug("Cancelled %s task", name)

        self._scheduler_task = None
        self._monitor_task = None
        self._sync_task = None

        # Save state
        self._persist()
        self._persist_sources()

        LOGGER.info("ScheduleManager stopped")

    async def schedule_trial(
        self,
        scenario_name: str,
        scenario_config: dict[str, Any],
        sport_type: str,
        event_id: str,
        event_time: datetime,
        pre_start_hours: float = 2.0,
        check_interval_seconds: float = 60.0,
        auto_stop_on_completion: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Schedule a single trial.

        Args:
            scenario_name: Name of the trial builder
            scenario_config: Configuration for the scenario
            sport_type: "nba" or "nfl"
            event_id: Game ID or ESPN event ID
            event_time: When the game starts (UTC)
            pre_start_hours: Hours before game to start trial
            check_interval_seconds: Interval to check game status
            auto_stop_on_completion: Whether to stop trial when game finishes
            metadata: Optional metadata

        Returns:
            Schedule ID
        """
        # Calculate scheduled start time
        scheduled_start_time = event_time - timedelta(hours=pre_start_hours)

        # Generate schedule ID
        hash_input = f"{sport_type}-{event_id}-{datetime.now(timezone.utc).isoformat()}"
        hash_suffix = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
        schedule_id = f"sched-{sport_type}-{event_id}-{hash_suffix}"

        # Extract game_date from event_time
        game_date = event_time.strftime("%Y-%m-%d")

        scheduled = ScheduledTrial(
            schedule_id=schedule_id,
            scenario_name=scenario_name,
            scenario_config=scenario_config,
            sport_type=sport_type,
            event_id=event_id,
            event_time=event_time,
            scheduled_start_time=scheduled_start_time,
            pre_start_hours=pre_start_hours,
            check_interval_seconds=check_interval_seconds,
            auto_stop_on_completion=auto_stop_on_completion,
            metadata=metadata or {},
            game_date=game_date,
        )

        self._schedules[schedule_id] = scheduled
        self._persist()

        LOGGER.info(
            "Scheduled trial '%s' for %s game %s (start: %s)",
            schedule_id,
            sport_type,
            event_id,
            scheduled_start_time,
        )

        return schedule_id

    async def schedule_batch(
        self,
        sport_type: str,
        date: str | None = None,
        week: int | None = None,
        scenario_name: str = "",
        scenario_config: dict[str, Any] | None = None,
        pre_start_hours: float = 2.0,
        check_interval_seconds: float = 60.0,
        auto_stop_on_completion: bool = True,
        data_dir: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> list[ScheduledTrial]:
        """Schedule trials for all games on a date or week.

        Args:
            sport_type: "nba" or "nfl"
            date: Date in YYYY-MM-DD format (required for NBA, optional for NFL)
            week: NFL week number (optional, NFL only)
            scenario_name: Name of the trial builder
            scenario_config: Base configuration for the scenario
            pre_start_hours: Hours before game to start trial
            check_interval_seconds: Interval to check game status
            auto_stop_on_completion: Whether to stop trial when game finishes
            data_dir: Optional data directory for output files
            metadata: Optional metadata

        Returns:
            List of ScheduledTrial objects created
        """
        # Fetch games
        games: list[GameInfo] = []
        if sport_type == "nba":
            if not date:
                date = datetime.now().strftime("%Y-%m-%d")
            games = await self._nba_fetcher.fetch_games_for_date(date)
        elif sport_type == "nfl":
            if week is not None:
                games = await self._nfl_fetcher.fetch_games_for_week(week)
            elif date:
                games = await self._nfl_fetcher.fetch_games_for_date(date)
            else:
                games = await self._nfl_fetcher.fetch_games_for_date(None)

        if not games:
            LOGGER.info("No games found for batch scheduling")
            return []

        # Schedule each game
        scheduled_trials: list[ScheduledTrial] = []
        for game in games:
            if game.game_time_utc is None:
                LOGGER.warning("Skipping game %s without game time", game.event_id)
                continue

            # Build config for this game
            config = dict(scenario_config or {})

            # Add game-specific config (both NBA and NFL use event_id with ESPN)
            config["event_id"] = game.event_id

            # Add data_dir to hub config if specified
            if data_dir:
                game_date = game.game_time_utc.strftime("%Y-%m-%d")
                if "hub" not in config:
                    config["hub"] = {}
                persistence_file = f"{data_dir}/{game_date}/{game.event_id}.jsonl"
                config["hub"]["persistence_file"] = persistence_file
                config["hub"]["enable_persistence"] = True

            # Add game info to metadata
            game_metadata = dict(metadata or {})
            game_metadata["game_short_name"] = game.short_name
            game_metadata["home_team"] = game.home_team.name
            game_metadata["away_team"] = game.away_team.name

            try:
                schedule_id = await self.schedule_trial(
                    scenario_name=scenario_name,
                    scenario_config=config,
                    sport_type=sport_type,
                    event_id=game.event_id,
                    event_time=game.game_time_utc,
                    pre_start_hours=pre_start_hours,
                    check_interval_seconds=check_interval_seconds,
                    auto_stop_on_completion=auto_stop_on_completion,
                    metadata=game_metadata,
                )
                scheduled = self._schedules.get(schedule_id)
                if scheduled:
                    scheduled_trials.append(scheduled)
            except Exception as e:
                LOGGER.error("Failed to schedule game %s: %s", game.event_id, e)

        LOGGER.info(
            "Batch scheduled %d trials for %s",
            len(scheduled_trials),
            sport_type,
        )

        return scheduled_trials

    async def cancel_scheduled(self, schedule_id: str) -> bool:
        """Cancel a scheduled trial.

        Args:
            schedule_id: Schedule identifier

        Returns:
            True if cancelled, False if not found or already completed
        """
        scheduled = self._schedules.get(schedule_id)
        if scheduled is None:
            return False

        if scheduled.phase in (
            ScheduledTrialPhase.COMPLETED,
            ScheduledTrialPhase.FAILED,
        ):
            return False

        # If trial was launched, cancel it via trial manager
        if scheduled.launched_trial_id:
            await self._trial_manager.cancel(scheduled.launched_trial_id)

        scheduled.phase = ScheduledTrialPhase.CANCELLED
        self._persist()

        LOGGER.info("Cancelled schedule: %s", schedule_id)
        return True

    def get_scheduled(self, schedule_id: str) -> ScheduledTrial | None:
        """Get a scheduled trial by ID."""
        return self._schedules.get(schedule_id)

    def list_scheduled(self) -> list[ScheduledTrial]:
        """List all scheduled trials."""
        return list(self._schedules.values())

    async def clear_all_scheduled(self) -> int:
        """Cancel and remove all scheduled trials.

        Returns:
            Number of scheduled trials cleared
        """
        count = 0
        for schedule_id in list(self._schedules.keys()):
            scheduled = self._schedules[schedule_id]

            # Cancel running trials
            if scheduled.launched_trial_id:
                try:
                    await self._trial_manager.cancel(scheduled.launched_trial_id)
                except Exception as e:
                    LOGGER.warning(
                        "Failed to cancel trial %s: %s", scheduled.launched_trial_id, e
                    )

            # Remove from tracking
            source_id = scheduled.metadata.get("source_id")
            if source_id:
                self._scheduled_events.pop((source_id, scheduled.event_id), None)

            del self._schedules[schedule_id]
            count += 1

        self._persist()
        LOGGER.info("Cleared %d scheduled trials", count)
        return count

    # -------------------------------------------------------------------------
    # Trial Source Management
    # -------------------------------------------------------------------------

    def register_source(
        self,
        source_id: str,
        sport_type: str,
        config: TrialSourceConfig,
    ) -> TrialSource:
        """Register a new trial source.

        Args:
            source_id: Unique identifier for this source
            sport_type: "nba" or "nfl"
            config: Trial source configuration

        Returns:
            The created TrialSource
        """
        if source_id in self._sources:
            raise ValueError(f"Trial source '{source_id}' already exists")

        if sport_type not in ("nba", "nfl"):
            raise ValueError(f"Invalid sport_type: {sport_type}")

        source = TrialSource(
            source_id=source_id,
            sport_type=sport_type,
            config=config,
            enabled=True,
        )

        self._sources[source_id] = source
        self._persist_sources()

        LOGGER.info("Registered trial source '%s' for %s", source_id, sport_type)
        return source

    def unregister_source(self, source_id: str) -> bool:
        """Unregister a trial source.

        Args:
            source_id: Source identifier

        Returns:
            True if removed, False if not found
        """
        if source_id not in self._sources:
            return False

        del self._sources[source_id]
        self._persist_sources()

        LOGGER.info("Unregistered trial source '%s'", source_id)
        return True

    def get_source(self, source_id: str) -> TrialSource | None:
        """Get a trial source by ID."""
        return self._sources.get(source_id)

    def list_sources(self) -> list[TrialSource]:
        """List all registered trial sources."""
        return list(self._sources.values())

    def set_source_enabled(self, source_id: str, enabled: bool) -> bool:
        """Enable or disable a trial source.

        Args:
            source_id: Source identifier
            enabled: Whether to enable or disable

        Returns:
            True if updated, False if not found
        """
        source = self._sources.get(source_id)
        if source is None:
            return False

        source.enabled = enabled
        self._persist_sources()

        LOGGER.info(
            "Trial source '%s' %s", source_id, "enabled" if enabled else "disabled"
        )
        return True

    async def sync_source(self, source_id: str) -> list[ScheduledTrial]:
        """Manually trigger sync for a specific trial source.

        Args:
            source_id: Source identifier

        Returns:
            List of newly scheduled trials
        """
        source = self._sources.get(source_id)
        if source is None:
            raise ValueError(f"Trial source '{source_id}' not found")

        return await self._sync_source(source)

    async def _sync_source(self, source: TrialSource) -> list[ScheduledTrial]:
        """Sync a trial source with external API and schedule new trials.

        Args:
            source: The trial source to sync

        Returns:
            List of newly scheduled trials
        """
        if not source.enabled:
            return []

        config = source.config
        games: list[GameInfo] = []

        try:
            if source.sport_type == "nba":
                # Fetch NBA games from ESPN scoreboard
                games = await self._nba_fetcher.fetch_games_for_date(None)

            elif source.sport_type == "nfl":
                # Fetch NFL games from ESPN scoreboard
                games = await self._nfl_fetcher.fetch_games_for_date(None)

        except Exception as e:
            LOGGER.error("Error fetching games for source %s: %s", source.source_id, e)
            return []

        # Schedule trials for new games
        scheduled_trials: list[ScheduledTrial] = []
        now = datetime.now(timezone.utc)

        for game in games:
            # Skip games without time or already past
            if game.game_time_utc is None:
                continue

            # Skip games that have already started
            if game.game_time_utc <= now:
                continue

            # Skip if already scheduled for this source
            if (source.source_id, game.event_id) in self._scheduled_events:
                continue

            # Build config for this game
            game_config = dict(config.scenario_config)

            # Add game-specific config (both NBA and NFL use event_id with ESPN)
            game_config["event_id"] = game.event_id

            # Add data_dir to hub config if specified
            if config.data_dir:
                game_date = game.game_time_utc.strftime("%Y-%m-%d")
                if "hub" not in game_config:
                    game_config["hub"] = {}
                persistence_file = (
                    f"{config.data_dir}/{game_date}/{game.event_id}.jsonl"
                )
                game_config["hub"]["persistence_file"] = persistence_file
                game_config["hub"]["enable_persistence"] = True

            # Metadata for the trial
            metadata = {
                "source_id": source.source_id,
                "game_short_name": game.short_name,
                "home_team": game.home_team.name,
                "away_team": game.away_team.name,
            }

            try:
                schedule_id = await self.schedule_trial(
                    scenario_name=config.scenario_name,
                    scenario_config=game_config,
                    sport_type=source.sport_type,
                    event_id=game.event_id,
                    event_time=game.game_time_utc,
                    pre_start_hours=config.pre_start_hours,
                    check_interval_seconds=config.check_interval_seconds,
                    auto_stop_on_completion=config.auto_stop_on_completion,
                    metadata=metadata,
                )

                # Track this event as scheduled for this source
                self._scheduled_events[(source.source_id, game.event_id)] = schedule_id

                scheduled = self._schedules.get(schedule_id)
                if scheduled:
                    scheduled_trials.append(scheduled)

            except Exception as e:
                LOGGER.error(
                    "Failed to schedule game %s for source %s: %s",
                    game.event_id,
                    source.source_id,
                    e,
                )

        # Update last sync time
        source.last_sync_at = datetime.now(timezone.utc)
        self._persist_sources()

        if scheduled_trials:
            LOGGER.info(
                "Synced source '%s': scheduled %d new trials",
                source.source_id,
                len(scheduled_trials),
            )

        return scheduled_trials

    async def _sync_loop(self) -> None:
        """Background loop that syncs trial sources with external APIs.

        Each source has its own sync_interval_seconds in config. The loop checks
        each source and syncs it if enough time has passed since last_sync_at.
        """
        # Initial sync on startup (after a small delay)
        await asyncio.sleep(5.0)

        # Do initial sync for all sources
        for source in list(self._sources.values()):
            if source.enabled:
                try:
                    await self._sync_source(source)
                except Exception as e:
                    LOGGER.error("Initial sync error for %s: %s", source.source_id, e)

        # Periodic check loop - check every 30 seconds which sources need syncing
        check_interval = 30.0

        while not self._shutdown_event.is_set():
            try:
                now = datetime.now(timezone.utc)

                for source in list(self._sources.values()):
                    if not source.enabled:
                        continue

                    # Get source-specific sync interval (default 5 min)
                    sync_interval = source.config.sync_interval_seconds

                    # Check if it's time to sync this source
                    if source.last_sync_at is None:
                        # Never synced, sync now
                        needs_sync = True
                    else:
                        elapsed = (now - source.last_sync_at).total_seconds()
                        needs_sync = elapsed >= sync_interval

                    if needs_sync:
                        try:
                            await self._sync_source(source)
                        except Exception as e:
                            LOGGER.error(
                                "Sync error for source %s: %s", source.source_id, e
                            )

                # Wait before next check
                await asyncio.sleep(check_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                LOGGER.error("Sync loop error: %s", e, exc_info=True)
                await asyncio.sleep(check_interval)

    def _persist(self) -> None:
        """Persist schedules to store."""
        if self._store:
            self._store.save(list(self._schedules.values()))

    def _persist_sources(self) -> None:
        """Persist trial sources to store."""
        if self._store:
            self._store.save_sources(list(self._sources.values()))

    async def _scheduler_loop(self) -> None:
        """Background loop that checks for trials to launch.

        Uses a semaphore to limit concurrent launches, preventing server overload
        when multiple games have similar start times.
        """
        while not self._shutdown_event.is_set():
            try:
                now = datetime.now(timezone.utc)

                # Collect trials that are ready to launch
                ready_to_launch: list[ScheduledTrial] = []
                for scheduled in list(self._schedules.values()):
                    if scheduled.phase != ScheduledTrialPhase.WAITING:
                        continue

                    # Check if it's time to start
                    if scheduled.scheduled_start_time <= now:
                        ready_to_launch.append(scheduled)

                # Launch trials concurrently with semaphore limiting
                if ready_to_launch:
                    LOGGER.info(
                        "Found %d trials ready to launch (max concurrent: %d)",
                        len(ready_to_launch),
                        self._max_concurrent_launches,
                    )

                    # Create launch tasks with semaphore protection
                    async def launch_with_semaphore(s: ScheduledTrial) -> None:
                        async with self._launch_semaphore:
                            # Add small stagger delay to avoid thundering herd
                            await asyncio.sleep(1.0)
                            await self._launch_scheduled(s)

                    # Launch all ready trials concurrently (semaphore limits actual concurrency)
                    tasks = [
                        asyncio.create_task(launch_with_semaphore(s))
                        for s in ready_to_launch
                    ]
                    await asyncio.gather(*tasks, return_exceptions=True)

                # Check every 10 seconds
                await asyncio.sleep(10.0)

            except asyncio.CancelledError:
                break
            except Exception as e:
                LOGGER.error("Scheduler loop error: %s", e, exc_info=True)
                await asyncio.sleep(10.0)

    async def _monitor_loop(self) -> None:
        """Background loop that monitors running trials for game completion.

        Implements grace period handling for games that are already finished when
        monitoring starts. This allows time for final data collection before
        stopping the trial.
        """
        while not self._shutdown_event.is_set():
            try:
                now = datetime.now(timezone.utc)

                for scheduled in list(self._schedules.values()):
                    if scheduled.phase not in (
                        ScheduledTrialPhase.RUNNING,
                        ScheduledTrialPhase.MONITORING,
                    ):
                        continue

                    if not scheduled.auto_stop_on_completion:
                        continue

                    # Check game status
                    game_status = await self._get_game_status(scheduled)

                    # Initialize monitoring state on first check
                    if scheduled.monitoring_started_at is None:
                        scheduled.monitoring_started_at = now
                        scheduled.initial_game_status = game_status
                        scheduled.phase = ScheduledTrialPhase.MONITORING
                        self._persist()

                        if game_status == 3:
                            LOGGER.info(
                                "Game %s (schedule %s) was already finished at "
                                "monitoring start. Will allow %.0f second grace "
                                "period for data processing.",
                                scheduled.event_id,
                                scheduled.schedule_id,
                                self._grace_period_seconds,
                            )

                    if game_status == 3:  # Finished
                        # Check if game was already finished when monitoring started
                        if scheduled.initial_game_status == 3:
                            # Apply grace period before stopping
                            elapsed = (
                                now - scheduled.monitoring_started_at
                            ).total_seconds()

                            if elapsed >= self._grace_period_seconds:
                                LOGGER.info(
                                    "Game %s (schedule %s) was already finished; "
                                    "grace period (%.0fs) elapsed. Stopping trial.",
                                    scheduled.event_id,
                                    scheduled.schedule_id,
                                    self._grace_period_seconds,
                                )
                                await self._stop_trial(scheduled)
                            else:
                                remaining = self._grace_period_seconds - elapsed
                                LOGGER.debug(
                                    "Game %s already finished; %.0fs remaining "
                                    "in grace period",
                                    scheduled.event_id,
                                    remaining,
                                )
                        else:
                            # Game transitioned to finished during monitoring
                            # Stop immediately
                            LOGGER.info(
                                "Game %s finished for schedule %s, stopping trial",
                                scheduled.event_id,
                                scheduled.schedule_id,
                            )
                            await self._stop_trial(scheduled)

                # Check every 30 seconds
                await asyncio.sleep(30.0)

            except asyncio.CancelledError:
                break
            except Exception as e:
                LOGGER.error("Monitor loop error: %s", e, exc_info=True)
                await asyncio.sleep(30.0)

    async def _launch_scheduled(self, scheduled: ScheduledTrial) -> None:
        """Launch a scheduled trial."""
        # Check if shutdown is in progress - skip launching
        if self._shutdown_event.is_set():
            LOGGER.debug(
                "Skipping launch of %s - scheduler is shutting down",
                scheduled.schedule_id,
            )
            return

        scheduled.phase = ScheduledTrialPhase.LAUNCHING

        try:
            # Get builder definition
            try:
                definition = get_trial_builder_definition(scheduled.scenario_name)
            except TrialBuilderNotFoundError as e:
                scheduled.phase = ScheduledTrialPhase.FAILED
                scheduled.error = str(e)
                self._persist()
                return

            # Generate trial ID
            hash_input = f"{scheduled.sport_type}-{scheduled.event_id}-{datetime.now(timezone.utc).isoformat()}"
            hash_suffix = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
            trial_id = f"{scheduled.sport_type}-game-{scheduled.event_id}-{hash_suffix}"

            # Build trial spec - uses build_async which handles both sync and async builders
            try:
                spec = await definition.build_async(trial_id, scheduled.scenario_config)
            except ValidationError as e:
                scheduled.phase = ScheduledTrialPhase.FAILED
                scheduled.error = f"Invalid config: {e}"
                self._persist()
                return

            # Add metadata
            spec.metadata.update(scheduled.metadata)
            spec.metadata["schedule_id"] = scheduled.schedule_id
            spec.metadata["sport_type"] = scheduled.sport_type
            spec.metadata["event_id"] = scheduled.event_id

            # Submit to trial manager
            await self._trial_manager.submit(spec)

            scheduled.launched_trial_id = trial_id
            scheduled.phase = ScheduledTrialPhase.RUNNING
            self._persist()

            LOGGER.info(
                "Launched trial '%s' for schedule '%s'",
                trial_id,
                scheduled.schedule_id,
            )

        except Exception as e:
            scheduled.phase = ScheduledTrialPhase.FAILED
            scheduled.error = str(e)
            self._persist()
            LOGGER.error(
                "Failed to launch scheduled trial %s: %s",
                scheduled.schedule_id,
                e,
                exc_info=True,
            )

    async def _get_game_status(self, scheduled: ScheduledTrial) -> int | None:
        """Get current game status for a scheduled trial."""
        try:
            if scheduled.sport_type == "nba":
                return await self._nba_fetcher.get_game_status(
                    scheduled.event_id,
                    scheduled.game_date,
                )
            elif scheduled.sport_type == "nfl":
                return await self._nfl_fetcher.get_game_status(
                    scheduled.event_id,
                    scheduled.game_date,
                )
        except Exception as e:
            LOGGER.warning(
                "Error getting game status for %s: %s",
                scheduled.schedule_id,
                e,
            )
        return None

    async def _stop_trial(self, scheduled: ScheduledTrial) -> None:
        """Stop a running trial."""
        if not scheduled.launched_trial_id:
            return

        try:
            # Stop via trial manager (which handles orchestrator stop internally)
            await self._trial_manager.cancel(scheduled.launched_trial_id)

            scheduled.phase = ScheduledTrialPhase.COMPLETED
            self._persist()

            LOGGER.info(
                "Stopped trial '%s' for schedule '%s'",
                scheduled.launched_trial_id,
                scheduled.schedule_id,
            )

        except Exception as e:
            LOGGER.error(
                "Error stopping trial for schedule %s: %s",
                scheduled.schedule_id,
                e,
            )


__all__ = [
    "FileSchedulerStore",
    "ScheduledTrial",
    "ScheduledTrialPhase",
    "ScheduleManager",
    "SchedulerStore",
    "TrialSource",
    "TrialSourceConfig",
    "TrialSourceStore",
]
