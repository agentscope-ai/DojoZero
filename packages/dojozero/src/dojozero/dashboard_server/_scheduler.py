"""Schedule Manager for DojoZero Dashboard Server.

Handles automatic scheduling of trials based on registered trial sources:
- Trial sources define scenario templates for NBA/NFL games
- Server periodically syncs with external APIs to discover upcoming games
- Automatically schedules trials based on registered sources
- Monitors game status and stops trials when games complete
- File-based persistence for crash recovery
"""

import asyncio
import copy
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ValidationError

from dojozero.core._registry import (
    TrialBuilderNotFoundError,
    get_trial_builder_definition,
)
from dojozero.data.espn import (
    STATUS_CANCELLED,
    STATUS_FINAL,
    STATUS_POSTPONED,
)
from dojozero.utils import utc_to_us_date, us_game_day_today

from ._game_discovery import GameInfo, NBAGameFetcher, NCAAGameFetcher, NFLGameFetcher
from ._trial_manager import TrialManager
from ._types import GameMetadata, ScheduledGameMetadata

if TYPE_CHECKING:
    from ._cluster import PeerRegistry

LOGGER = logging.getLogger("dojozero.scheduler")


# =============================================================================
# Trial Source Models
# =============================================================================


class TrialSourceConfig(BaseModel):
    """Configuration for a trial source.

    Attributes:
        scenario_name: Name of the trial builder (e.g., "nba", "nfl")
        scenario_config: Base configuration passed to the trial builder.
            Game-specific fields (espn_game_id, hub.persistence_file) are
            added automatically when scheduling individual games.
        pre_start_hours: Hours before game start to launch the trial
        check_interval_seconds: Interval for checking game status
        auto_stop_on_completion: Whether to stop trial when game finishes
        data_dir: Base directory for persistence files. If set, files are
            created at {data_dir}/{game_date}/{game_id}.jsonl
        sync_interval_seconds: How often to sync with ESPN API for new games
        max_daily_games: Maximum number of games to schedule per day
            for this source. 0 means unlimited (default).
    """

    scenario_name: str
    scenario_config: dict[str, Any] = {}
    pre_start_hours: float = 2.0
    check_interval_seconds: float = 60.0
    auto_stop_on_completion: bool = True
    data_dir: str | None = None
    sync_interval_seconds: float = (
        300.0  # How often to sync with external APIs (5 min default)
    )
    max_daily_games: int = 0  # 0 = unlimited


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
    game_id: str  # ESPN game ID (used for both NBA and NFL)
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
            "game_id": self.game_id,
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
            game_id=data.get("game_id") or data.get("event_id", ""),
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


class RedisSchedulerStore:
    """Redis-backed scheduler store using hashes.

    Uses two Redis hashes:
    - ``dojozero:schedules`` — ``schedule_id → JSON(ScheduledTrial)``
    - ``dojozero:trial_sources`` — ``source_id → JSON(TrialSource)``
    """

    SCHEDULES_KEY = "dojozero:schedules"
    SOURCES_KEY = "dojozero:trial_sources"

    def __init__(self, redis_url: str) -> None:
        import redis as sync_redis

        self._redis: Any = sync_redis.from_url(redis_url)

    def save(self, schedules: list[ScheduledTrial]) -> None:
        pipe = self._redis.pipeline()
        pipe.delete(self.SCHEDULES_KEY)
        for s in schedules:
            pipe.hset(
                self.SCHEDULES_KEY, s.schedule_id, json.dumps(s.to_dict(), default=str)
            )
        pipe.execute()
        LOGGER.debug("Saved %d schedules to Redis", len(schedules))

    def load(self) -> list[ScheduledTrial]:
        try:
            raw: dict[bytes, bytes] = self._redis.hgetall(self.SCHEDULES_KEY)
            schedules = []
            for _key, val in raw.items():
                data = json.loads(val)
                schedules.append(ScheduledTrial.from_dict(data))
            LOGGER.info("Loaded %d schedules from Redis", len(schedules))
            return schedules
        except Exception as e:
            LOGGER.error("Error loading schedules from Redis: %s", e)
            return []

    def save_sources(self, sources: list[TrialSource]) -> None:
        pipe = self._redis.pipeline()
        pipe.delete(self.SOURCES_KEY)
        for s in sources:
            pipe.hset(
                self.SOURCES_KEY, s.source_id, json.dumps(s.to_dict(), default=str)
            )
        pipe.execute()
        LOGGER.debug("Saved %d trial sources to Redis", len(sources))

    def load_sources(self) -> list[TrialSource]:
        try:
            raw: dict[bytes, bytes] = self._redis.hgetall(self.SOURCES_KEY)
            sources = []
            for _key, val in raw.items():
                data = json.loads(val)
                sources.append(TrialSource.from_dict(data))
            LOGGER.info("Loaded %d trial sources from Redis", len(sources))
            return sources
        except Exception as e:
            LOGGER.error("Error loading trial sources from Redis: %s", e)
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
        grace_period_seconds: float = 60.0,  # Safety net (self-stop is primary at 10s)
        peer_registry: "PeerRegistry | None" = None,
        server_id: str | None = None,
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
            peer_registry: Optional peer registry for multi-server trial assignment.
            server_id: This server's identifier (for cluster mode).
        """
        self._trial_manager = trial_manager
        self._store = store
        self._sync_interval = sync_interval_seconds
        self._max_concurrent_launches = max_concurrent_launches
        self._grace_period_seconds = grace_period_seconds
        self._peer_registry: PeerRegistry | None = peer_registry
        self._server_id = server_id

        # All scheduled trials by ID
        self._schedules: dict[str, ScheduledTrial] = {}

        # All trial sources by ID
        self._sources: dict[str, TrialSource] = {}

        # NOTE: _scheduled_events is derived from _schedules on access via the
        # @property below.  No separate dict to keep in sync.

        # Semaphore to limit concurrent trial launches
        self._launch_semaphore = asyncio.Semaphore(max_concurrent_launches)

        # Background tasks
        self._scheduler_task: asyncio.Task[None] | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._sync_task: asyncio.Task[None] | None = None
        self._shutdown_event = asyncio.Event()

        # Game IDs confirmed finished by internal pipeline (GameResultEvent)
        self._confirmed_finished: set[str] = set()

        # Timestamp of first ESPN FINAL status seen (for safety timeout)
        self._espn_final_first_seen: dict[str, datetime] = {}

        # Safety timeout: force-stop if pipeline hasn't confirmed after this many seconds
        self._safety_timeout_seconds = 300.0  # 5 minutes

        # Game fetchers
        self._nba_fetcher = NBAGameFetcher()
        self._ncaa_fetcher = NCAAGameFetcher()
        self._nfl_fetcher = NFLGameFetcher()

        # Shared HTTP session for remote submissions (created in start())
        self._http_session: Any = None  # aiohttp.ClientSession

    @property
    def _scheduled_events(self) -> dict[tuple[str, str], str]:
        """Derive scheduled events from _schedules (single source of truth).

        Returns a dict of ``(source_id, game_id) → schedule_id`` for all
        schedules that are still active (not completed/cancelled/failed).
        """
        result: dict[tuple[str, str], str] = {}
        for s in self._schedules.values():
            if s.phase in (
                ScheduledTrialPhase.COMPLETED,
                ScheduledTrialPhase.CANCELLED,
                ScheduledTrialPhase.FAILED,
            ):
                continue
            source_id = s.metadata.get("source_id")
            if source_id:
                result[(source_id, s.game_id)] = s.schedule_id
        return result

    async def start(self) -> None:
        """Start the schedule manager."""
        self._shutdown_event.clear()

        # Load persisted schedules and sources
        if self._store:
            # Load trial sources
            loaded_sources = self._store.load_sources()
            for source in loaded_sources:
                self._sources[source.source_id] = source

            # Load all scheduled trials (including finished ones for history)
            loaded = self._store.load()
            for s in loaded:
                self._schedules[s.schedule_id] = s

            # Reset orphaned schedules stuck in launching with no trial ID.
            # This is always safe: the trial was never created.  Schedules in
            # running/monitoring with a launched_trial_id may be executing on
            # a peer, so we leave those alone — the monitor loop will detect
            # if the game has finished or the trial is truly lost.
            orphaned = 0
            for s in self._schedules.values():
                if (
                    s.phase == ScheduledTrialPhase.LAUNCHING
                    and s.launched_trial_id is None
                ):
                    LOGGER.warning(
                        "Resetting stuck schedule '%s' (phase=launching, "
                        "no trial ID) to waiting",
                        s.schedule_id,
                    )
                    s.phase = ScheduledTrialPhase.WAITING
                    orphaned += 1
            if orphaned:
                LOGGER.info("Reset %d stuck schedules to waiting", orphaned)
                self._persist()

        # Create shared HTTP session for remote submissions
        import aiohttp

        self._http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
        )

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

        # Close shared HTTP session
        if self._http_session is not None:
            await self._http_session.close()
            self._http_session = None

        # Save state
        self._persist()
        self._persist_sources()

        LOGGER.info("ScheduleManager stopped")

    def _generate_schedule_id(self, sport_type: str, game_id: str) -> str:
        """Generate a unique schedule ID.

        Args:
            sport_type: Sport type (e.g., "nba", "nfl")
            game_id: Game ID

        Returns:
            Unique schedule ID
        """
        return f"sched-{sport_type}-{game_id}"

    async def schedule_trial(
        self,
        scenario_name: str,
        scenario_config: dict[str, Any],
        sport_type: str,
        game_id: str,
        event_time: datetime,
        pre_start_hours: float = 2.0,
        check_interval_seconds: float = 60.0,
        auto_stop_on_completion: bool = True,
        metadata: dict[str, Any] | None = None,
        schedule_id: str | None = None,
    ) -> str:
        """Schedule a single trial.

        Args:
            scenario_name: Name of the trial builder
            scenario_config: Configuration for the scenario
            sport_type: "nba" or "nfl"
            game_id: ESPN game ID
            event_time: When the game starts (UTC)
            pre_start_hours: Hours before game to start trial
            check_interval_seconds: Interval to check game status
            auto_stop_on_completion: Whether to stop trial when game finishes
            metadata: Optional metadata
            schedule_id: Optional schedule ID (generated if not provided)

        Returns:
            Schedule ID
        """
        # Calculate scheduled start time
        scheduled_start_time = event_time - timedelta(hours=pre_start_hours)

        # Generate schedule ID if not provided
        if schedule_id is None:
            schedule_id = self._generate_schedule_id(sport_type, game_id)

        # Extract game_date from event_time in US Eastern time
        game_date = utc_to_us_date(event_time)

        scheduled = ScheduledTrial(
            schedule_id=schedule_id,
            scenario_name=scenario_name,
            scenario_config=scenario_config,
            sport_type=sport_type,
            game_id=game_id,
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
            game_id,
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
            # None → fetcher uses us_game_day_today() (US Eastern game day)
            games = await self._nba_fetcher.fetch_games_for_date(date)
        elif sport_type == "ncaa":
            games = await self._ncaa_fetcher.fetch_games_for_date(date)
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
                LOGGER.warning("Skipping game %s without game time", game.game_id)
                continue

            # Build config for this game
            config = copy.deepcopy(scenario_config or {})

            # Add game-specific config (both NBA and NFL use espn_game_id)
            config["espn_game_id"] = game.game_id

            # Prepare hub config
            game_date = utc_to_us_date(game.game_time_utc)
            if "hub" not in config:
                config["hub"] = {}

            # Generate schedule_id early so we can use it in persistence_file
            schedule_id = self._generate_schedule_id(sport_type, game.game_id)

            # Set persistence_file with unique schedule_id to avoid conflicts
            if data_dir:
                persistence_file = f"{data_dir}/{game_date}/{schedule_id}.jsonl"
                config["hub"]["persistence_file"] = persistence_file

            # Add game info to metadata using typed structure
            game_metadata: dict[str, Any] = dict(metadata) if metadata else {}
            # Use typed keys for game metadata
            typed_game_meta: GameMetadata = {
                "game_short_name": game.short_name,
                "home_team": game.home_team.name,
                "away_team": game.away_team.name,
                "home_tricode": game.home_team.tricode,
                "away_tricode": game.away_team.tricode,
                "game_date": game_date,
            }
            game_metadata.update(typed_game_meta)

            try:
                schedule_id = await self.schedule_trial(
                    scenario_name=scenario_name,
                    scenario_config=config,
                    sport_type=sport_type,
                    game_id=game.game_id,
                    event_time=game.game_time_utc,
                    pre_start_hours=pre_start_hours,
                    check_interval_seconds=check_interval_seconds,
                    auto_stop_on_completion=auto_stop_on_completion,
                    metadata=game_metadata,
                    schedule_id=schedule_id,
                )
                scheduled = self._schedules.get(schedule_id)
                if scheduled:
                    scheduled_trials.append(scheduled)
            except Exception as e:
                LOGGER.error("Failed to schedule game %s: %s", game.game_id, e)

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

    def mark_game_finished(self, game_id: str) -> None:
        """Mark a game as confirmed finished by the internal pipeline.

        Called via DataHub on_game_result callback after GameResultEvent
        has been dispatched (broker has already settled bets).
        """
        LOGGER.info("Game %s confirmed finished by internal pipeline", game_id)
        self._confirmed_finished.add(game_id)

    def get_scheduled(self, schedule_id: str) -> ScheduledTrial | None:
        """Get a scheduled trial by ID."""
        return self._schedules.get(schedule_id)

    def list_scheduled(self, include_finished: bool = False) -> list[ScheduledTrial]:
        """List scheduled trials.

        Args:
            include_finished: If True, include completed/cancelled/failed trials.
                             If False (default), only return active trials.

        Returns:
            List of scheduled trials
        """
        if include_finished:
            return list(self._schedules.values())

        # Filter out finished trials
        active_phases = {
            ScheduledTrialPhase.WAITING,
            ScheduledTrialPhase.LAUNCHING,
            ScheduledTrialPhase.RUNNING,
            ScheduledTrialPhase.MONITORING,
        }
        return [s for s in self._schedules.values() if s.phase in active_phases]

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

        if sport_type not in ("nba", "nfl", "ncaa"):
            raise ValueError(f"Invalid sport_type: {sport_type}")

        source = TrialSource(
            source_id=source_id,
            sport_type=sport_type,
            config=config,
            enabled=True,
        )

        self._sources[source_id] = source
        self._persist_sources()

        # Log config summary for visibility
        agents = config.scenario_config.get("agents", [])
        agent_personas = [a.get("persona", a.get("id", "?")) for a in agents]
        llm_paths = {a.get("llm_config_path", "inline") for a in agents}
        max_games_str = (
            str(config.max_daily_games) if config.max_daily_games > 0 else "unlimited"
        )
        LOGGER.info(
            "Registered trial source '%s' for %s: "
            "max_games=%s, personas=%s, llm_configs=%s",
            source_id,
            sport_type,
            max_games_str,
            agent_personas,
            sorted(llm_paths),
        )
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

    def list_source_ids(self) -> list[str]:
        """Return all registered source IDs."""
        return list(self._sources.keys())

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

            elif source.sport_type == "ncaa":
                # Fetch NCAA basketball games from ESPN scoreboard
                games = await self._ncaa_fetcher.fetch_games_for_date(None)

            elif source.sport_type == "nfl":
                # Fetch NFL games from ESPN scoreboard
                games = await self._nfl_fetcher.fetch_games_for_date(None)

        except Exception as e:
            LOGGER.error("Error fetching games for source %s: %s", source.source_id, e)
            return []

        # Check max_daily_games limit (count all games scheduled today for this source)
        max_games = config.max_daily_games
        if max_games > 0:
            # Align with game_date / ESPN slate: US Eastern calendar day, not UTC
            today = us_game_day_today()
            daily_count = sum(
                1
                for sid, gid in self._scheduled_events
                if sid == source.source_id
                and self._schedules.get(self._scheduled_events[(sid, gid)])
                and self._schedules[self._scheduled_events[(sid, gid)]].game_date
                == today
            )
            remaining_slots = max_games - daily_count
            if remaining_slots <= 0:
                LOGGER.info(
                    "Source '%s': max_daily_games=%d reached "
                    "(%d today), skipping new games",
                    source.source_id,
                    max_games,
                    daily_count,
                )
                return []
        else:
            remaining_slots = len(games)  # unlimited

        # Schedule trials for new games
        scheduled_trials: list[ScheduledTrial] = []

        for game in games:
            # Skip games without time
            if game.game_time_utc is None:
                continue

            # Skip games that have already finished (status 3 = finished)
            # Allow scheduling for: scheduled (1), in-progress (2)
            if game.status == 3:
                continue

            # Skip postponed or cancelled games
            status_text_lower = game.status_text.lower()
            if "postponed" in status_text_lower:
                LOGGER.debug(
                    "Skipping postponed game %s (%s): %s",
                    game.game_id,
                    game.short_name,
                    game.status_text,
                )
                continue
            if "canceled" in status_text_lower or "cancelled" in status_text_lower:
                LOGGER.debug(
                    "Skipping cancelled game %s (%s): %s",
                    game.game_id,
                    game.short_name,
                    game.status_text,
                )
                continue

            # Skip if already scheduled for this source
            if (source.source_id, game.game_id) in self._scheduled_events:
                continue

            # Enforce max_daily_games limit (before claiming, to avoid orphaned claims)
            if max_games > 0 and remaining_slots <= 0:
                LOGGER.info(
                    "Source '%s': skipping game %s (%s) — max_daily_games=%d reached",
                    source.source_id,
                    game.game_id,
                    game.short_name,
                    max_games,
                )
                continue

            # Cluster-wide game dedup: claim this game atomically
            if self._peer_registry is not None and self._server_id is not None:
                try:
                    claimed = await self._peer_registry.claim_game(
                        source.sport_type, game.game_id, self._server_id
                    )
                    if not claimed:
                        LOGGER.info(
                            "Game %s (%s) already claimed by another server, skipping",
                            game.game_id,
                            game.short_name,
                        )
                        continue
                except Exception as e:
                    LOGGER.warning("Failed to claim game %s: %s", game.game_id, e)
                    # Fail-closed: don't schedule if claim check fails
                    continue

            # Build config for this game (deep copy to avoid shared nested dicts)
            game_config = copy.deepcopy(config.scenario_config)

            # Add game-specific config (both NBA and NFL use espn_game_id)
            game_config["espn_game_id"] = game.game_id

            # Convert game time to US Eastern date for consistent date handling
            game_date = utc_to_us_date(game.game_time_utc)

            # Prepare hub config
            if "hub" not in game_config:
                game_config["hub"] = {}

            # Generate schedule_id early so we can use it in persistence_file
            schedule_id = self._generate_schedule_id(source.sport_type, game.game_id)

            # Set persistence_file with unique schedule_id to avoid conflicts
            if config.data_dir:
                persistence_file = f"{config.data_dir}/{game_date}/{schedule_id}.jsonl"
                game_config["hub"]["persistence_file"] = persistence_file

            # Metadata for the trial using typed structure
            trial_metadata: ScheduledGameMetadata = {
                "source_id": source.source_id,
                "game_short_name": game.short_name,
                "home_team": game.home_team.name,
                "away_team": game.away_team.name,
                "home_tricode": game.home_team.tricode,
                "away_tricode": game.away_team.tricode,
                "game_date": game_date,
            }

            try:
                schedule_id = await self.schedule_trial(
                    scenario_name=config.scenario_name,
                    scenario_config=game_config,
                    sport_type=source.sport_type,
                    game_id=game.game_id,
                    event_time=game.game_time_utc,
                    pre_start_hours=config.pre_start_hours,
                    check_interval_seconds=config.check_interval_seconds,
                    auto_stop_on_completion=config.auto_stop_on_completion,
                    metadata=dict(trial_metadata),
                    schedule_id=schedule_id,
                )

                remaining_slots -= 1

                scheduled = self._schedules.get(schedule_id)
                if scheduled:
                    scheduled_trials.append(scheduled)

            except Exception as e:
                LOGGER.error(
                    "Failed to schedule game %s for source %s: %s",
                    game.game_id,
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
        # Initial delay: longer in cluster mode so peers can register heartbeats
        initial_delay = 30.0 if self._peer_registry is not None else 5.0
        await asyncio.sleep(initial_delay)

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

        Also handles abnormal game states (postponed, cancelled) by stopping
        trials immediately with appropriate logging.
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

                    # Check game status with text for better logging
                    status_info = await self._get_game_status_info(scheduled)
                    if status_info is None:
                        continue

                    game_status, status_text = status_info

                    # Initialize monitoring state on first check
                    if scheduled.monitoring_started_at is None:
                        scheduled.monitoring_started_at = now
                        scheduled.initial_game_status = game_status
                        scheduled.phase = ScheduledTrialPhase.MONITORING
                        self._persist()

                        if game_status == STATUS_FINAL:
                            LOGGER.info(
                                "Game %s (schedule %s) was already finished at "
                                "monitoring start. Will allow %.0f second grace "
                                "period for data processing.",
                                scheduled.game_id,
                                scheduled.schedule_id,
                                self._grace_period_seconds,
                            )

                    # Handle postponed or cancelled games - stop immediately
                    if game_status in (STATUS_POSTPONED, STATUS_CANCELLED):
                        state_str = (
                            "POSTPONED"
                            if game_status == STATUS_POSTPONED
                            else "CANCELLED"
                        )
                        error_str = (
                            "postponed"
                            if game_status == STATUS_POSTPONED
                            else "cancelled"
                        )
                        LOGGER.warning(
                            "Game %s (schedule %s) has been %s (%s). "
                            "Stopping trial immediately.",
                            scheduled.game_id,
                            scheduled.schedule_id,
                            state_str,
                            status_text,
                        )
                        scheduled.error = f"Game {error_str}: {status_text}"
                        await self._stop_trial(scheduled)
                        continue

                    if game_status == STATUS_FINAL:
                        # Check if game was already finished when monitoring started
                        if scheduled.initial_game_status == STATUS_FINAL:
                            # Apply grace period before stopping
                            elapsed = (
                                now - scheduled.monitoring_started_at
                            ).total_seconds()

                            if elapsed >= self._grace_period_seconds:
                                LOGGER.info(
                                    "Game %s (schedule %s) was already finished; "
                                    "grace period (%.0fs) elapsed. Stopping trial.",
                                    scheduled.game_id,
                                    scheduled.schedule_id,
                                    self._grace_period_seconds,
                                )
                                await self._stop_trial(scheduled)
                            else:
                                remaining = self._grace_period_seconds - elapsed
                                LOGGER.debug(
                                    "Game %s already finished; %.0fs remaining "
                                    "in grace period",
                                    scheduled.game_id,
                                    remaining,
                                )
                        elif scheduled.game_id in self._confirmed_finished:
                            # Pipeline already processed GameResultEvent
                            LOGGER.info(
                                "Game %s confirmed by pipeline for schedule %s, "
                                "stopping trial",
                                scheduled.game_id,
                                scheduled.schedule_id,
                            )
                            await self._stop_trial(scheduled)
                        else:
                            # ESPN says FINAL but pipeline hasn't confirmed
                            if scheduled.game_id not in self._espn_final_first_seen:
                                self._espn_final_first_seen[scheduled.game_id] = now
                                LOGGER.warning(
                                    "Game %s reported FINAL by ESPN but not "
                                    "confirmed by internal pipeline (schedule %s). "
                                    "Waiting for pipeline confirmation or %.0fs "
                                    "safety timeout.",
                                    scheduled.game_id,
                                    scheduled.schedule_id,
                                    self._safety_timeout_seconds,
                                )
                            else:
                                elapsed = (
                                    now - self._espn_final_first_seen[scheduled.game_id]
                                ).total_seconds()
                                if elapsed >= self._safety_timeout_seconds:
                                    LOGGER.warning(
                                        "Safety timeout (%.0fs) expired for "
                                        "game %s (schedule %s). Force-stopping "
                                        "trial without pipeline confirmation.",
                                        elapsed,
                                        scheduled.game_id,
                                        scheduled.schedule_id,
                                    )
                                    await self._stop_trial(scheduled)
                                else:
                                    LOGGER.debug(
                                        "Game %s: ESPN FINAL, waiting for "
                                        "pipeline (%.0fs / %.0fs)",
                                        scheduled.game_id,
                                        elapsed,
                                        self._safety_timeout_seconds,
                                    )

                # Check for orphaned schedules whose owning peer is dead
                await self._recover_orphaned_schedules()

                # Check every 30 seconds
                await asyncio.sleep(30.0)

            except asyncio.CancelledError:
                break
            except Exception as e:
                LOGGER.error("Monitor loop error: %s", e, exc_info=True)
                await asyncio.sleep(30.0)

    # Dead-peer threshold: peer must be missing for this long before we
    # consider its schedules orphaned.  Much longer than PEER_TTL (30s) to
    # tolerate brief restarts and network blips.
    _DEAD_PEER_THRESHOLD = 300.0  # 5 minutes

    async def _recover_orphaned_schedules(self) -> None:
        """Recover schedules whose owning peer has been dead for a long time.

        For running/monitoring schedules with a ``launched_trial_id``, check
        whether the peer that owns the trial is still alive.  If the peer has
        been unresponsive for longer than ``_DEAD_PEER_THRESHOLD``:

        - Game already finished → mark schedule ``completed``
        - Game not started yet  → reset to ``waiting`` (safe to re-launch)
        - Game in progress      → mark ``failed`` (partial data, don't dupe)

        Two independent signals are required before acting:
        1. Redis heartbeat stale (>5 min)
        2. HTTP health check to peer's last known URL fails
        """
        if self._peer_registry is None or self._server_id is None:
            return

        for scheduled in list(self._schedules.values()):
            if scheduled.phase not in (
                ScheduledTrialPhase.RUNNING,
                ScheduledTrialPhase.MONITORING,
            ):
                continue
            if not scheduled.launched_trial_id:
                continue

            # Look up the owning peer
            owner = await self._peer_registry.get_peer_for_trial(
                scheduled.launched_trial_id
            )

            # Determine if this schedule is orphaned
            is_orphaned = False
            owner_label = "unknown"

            if owner is None:
                # No owner recorded (trial_owners flushed or legacy trial).
                # Check if the trial is running locally — if so, skip.
                local_status = self._trial_manager.get_status(
                    scheduled.launched_trial_id
                )
                if local_status is not None:
                    continue  # running locally, monitor loop handles it
                is_orphaned = True
                LOGGER.warning(
                    "Schedule '%s' trial '%s' has no owner and is not "
                    "running locally — treating as orphaned",
                    scheduled.schedule_id,
                    scheduled.launched_trial_id,
                )
            elif owner.server_id == self._server_id:
                # We own it — local monitor loop handles this
                continue
            else:
                owner_label = owner.server_id
                # Signal 1: Redis heartbeat staleness
                # staleness=None means the peer entry was already pruned
                # from Redis — treat that as definitely dead.
                staleness = await self._peer_registry.get_peer_staleness(
                    owner.server_id
                )
                if staleness is not None and staleness <= self._DEAD_PEER_THRESHOLD:
                    continue  # peer is alive or recently seen

                # Signal 2: HTTP health check to the peer's last known URL
                if owner.server_url:
                    try:
                        health_url = f"{owner.server_url.rstrip('/')}/api/trials"
                        async with self._http_session.get(
                            health_url, timeout=5
                        ) as resp:
                            if resp.status < 500:
                                continue  # peer is reachable
                    except Exception:
                        pass  # unreachable — confirms dead
                is_orphaned = True

            if not is_orphaned:
                continue

            # Schedule is orphaned. Decide action based on game state.
            LOGGER.warning(
                "Recovering orphaned schedule '%s' (trial=%s, owner=%s)",
                scheduled.schedule_id,
                scheduled.launched_trial_id,
                owner_label,
            )

            status_info = await self._get_game_status_info(scheduled)
            game_status = status_info[0] if status_info else None

            if game_status == STATUS_FINAL:
                scheduled.phase = ScheduledTrialPhase.COMPLETED
                scheduled.error = (
                    f"Owner peer '{owner_label}' died; game already finished"
                )
                LOGGER.info(
                    "Schedule '%s': game finished, marked completed "
                    "(partial data from dead peer)",
                    scheduled.schedule_id,
                )
            elif game_status is not None and game_status < 2:
                # Game hasn't started — safe to retry
                scheduled.phase = ScheduledTrialPhase.WAITING
                scheduled.launched_trial_id = None
                scheduled.monitoring_started_at = None
                LOGGER.info(
                    "Schedule '%s': game not started, reset to waiting",
                    scheduled.schedule_id,
                )
            else:
                # Game in progress or unknown — mark failed to avoid dupes
                scheduled.phase = ScheduledTrialPhase.FAILED
                scheduled.error = (
                    f"Owner peer '{owner_label}' died mid-game; "
                    f"marked failed to avoid duplicate traces"
                )
                LOGGER.warning(
                    "Schedule '%s': game in progress on dead peer, "
                    "marked failed (avoiding duplicate)",
                    scheduled.schedule_id,
                )

            self._persist()

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

        # No game claim re-check here. The schedule is already committed
        # in the store — whoever is leader should launch it. Claims are only
        # for dedup at scheduling time in the sync loop.

        try:
            # Get builder definition
            try:
                definition = get_trial_builder_definition(scheduled.scenario_name)
            except TrialBuilderNotFoundError as e:
                scheduled.phase = ScheduledTrialPhase.FAILED
                scheduled.error = str(e)
                self._persist()
                return

            # Generate trial ID with hash suffix so each attempt is unique in
            # SLS traces and the orchestrator store.  The schedule_id (without
            # hash) remains the dedup key.
            import hashlib
            import time as _time

            hash_input = f"{scheduled.game_id}-{_time.time()}"
            hash_suffix = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
            trial_id = f"{scheduled.sport_type}-game-{scheduled.game_id}-{hash_suffix}"

            # Build trial spec - uses build_async which handles both sync and async builders
            try:
                spec = await definition.build_async(trial_id, scheduled.scenario_config)
            except ValidationError as e:
                scheduled.phase = ScheduledTrialPhase.FAILED
                scheduled.error = f"Invalid config: {e}"
                self._persist()
                return

            # Note: spec.metadata is a frozen dataclass (e.g., BettingTrialMetadata)
            # with sport_type and espn_game_id already populated by the builder.
            # No need to add schedule_id/game_id - they're tracked in ScheduledTrial.

            # Pick target server if peer registry available
            target_peer = None
            if self._peer_registry is not None:
                try:
                    peers = await self._peer_registry.get_peers()
                    if peers:
                        # Pick peer with fewest active trials
                        target_peer = min(peers, key=lambda p: p.active_trials)
                except Exception as e:
                    LOGGER.warning("Failed to query peers for scheduling: %s", e)

            # Submit to target server
            if (
                target_peer is not None
                and self._server_id is not None
                and target_peer.server_id != self._server_id
            ):
                # Remote submission via HTTP
                remote_url = f"{target_peer.server_url.rstrip('/')}/api/trials"
                payload = {
                    "trial_id": trial_id,
                    "scenario": {
                        "name": scheduled.scenario_name,
                        "config": scheduled.scenario_config,
                    },
                }
                try:
                    async with self._http_session.post(
                        remote_url,
                        json=payload,
                        headers={
                            "X-Dojozero-Forwarded": self._server_id or "scheduler"
                        },
                    ) as resp:
                        if resp.status not in (200, 201):
                            body = await resp.text()
                            raise RuntimeError(
                                f"Remote submission failed ({resp.status}): {body}"
                            )
                    LOGGER.info(
                        "Submitted trial '%s' to remote peer '%s'",
                        trial_id,
                        target_peer.server_id,
                    )
                except Exception as e:
                    LOGGER.warning(
                        "Remote submission to '%s' failed, falling back to local: %s",
                        target_peer.server_id,
                        e,
                    )
                    await self._trial_manager.submit(spec)
            else:
                # Local submission
                await self._trial_manager.submit(spec)

            # Register trial ownership in peer registry
            if self._peer_registry is not None:
                owner = (
                    target_peer.server_id
                    if target_peer is not None
                    and self._server_id is not None
                    and target_peer.server_id != self._server_id
                    else self._server_id or ""
                )
                try:
                    await self._peer_registry.register_trial(trial_id, owner)
                except Exception as e:
                    LOGGER.debug("Failed to register trial ownership: %s", e)

            scheduled.launched_trial_id = trial_id
            scheduled.phase = ScheduledTrialPhase.RUNNING
            self._persist()

            # Register game result callback on trial's DataHub (local trials only)
            if (
                target_peer is None
                or self._server_id is None
                or target_peer.server_id == self._server_id
            ):
                self._register_game_result_callback(scheduled)

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
        result = await self._get_game_status_info(scheduled)
        return result[0] if result else None

    async def _get_game_status_info(
        self, scheduled: ScheduledTrial
    ) -> tuple[int, str] | None:
        """Get current game status and status text for a scheduled trial.

        Returns:
            Tuple of (status_code, status_text) or None if not found.
            Status codes: 1=scheduled, 2=in_progress, 3=finished, 4=postponed, 5=cancelled
        """
        try:
            if scheduled.sport_type == "nba":
                return await self._nba_fetcher.get_game_status_info(
                    scheduled.game_id,
                    scheduled.game_date,
                )
            elif scheduled.sport_type == "ncaa":
                return await self._ncaa_fetcher.get_game_status_info(
                    scheduled.game_id,
                    scheduled.game_date,
                )
            elif scheduled.sport_type == "nfl":
                return await self._nfl_fetcher.get_game_status_info(
                    scheduled.game_id,
                    scheduled.game_date,
                )
            else:
                LOGGER.warning(
                    "Unknown sport type '%s' for schedule %s",
                    scheduled.sport_type,
                    scheduled.schedule_id,
                )
        except Exception as e:
            LOGGER.error(
                "Failed to get game status for %s (event %s, sport %s): %s",
                scheduled.schedule_id,
                scheduled.game_id,
                scheduled.sport_type,
                e,
                exc_info=True,
            )
        return None

    def _register_game_result_callback(self, scheduled: ScheduledTrial) -> None:
        """Register a DataHub callback to detect game completion from pipeline."""
        trial_id = scheduled.launched_trial_id
        if not trial_id:
            return

        game_id = scheduled.game_id

        async def on_game_result(result_game_id: str) -> None:
            if result_game_id == game_id:
                self.mark_game_finished(game_id)

        if not self._trial_manager.register_on_game_result(trial_id, on_game_result):
            LOGGER.warning(
                "Failed to register game_result callback for game %s (trial %s). "
                "Falling back to ESPN-only detection.",
                game_id,
                trial_id,
            )

    async def _stop_trial(self, scheduled: ScheduledTrial) -> None:
        """Stop a running trial (game completed normally)."""
        if not scheduled.launched_trial_id:
            return

        try:
            # Stop via trial manager - use complete_trial() not cancel() since game completed
            await self._trial_manager.complete_trial(scheduled.launched_trial_id)

            scheduled.phase = ScheduledTrialPhase.COMPLETED
            self._persist()

            # Clean up tracking state
            self._espn_final_first_seen.pop(scheduled.game_id, None)
            self._confirmed_finished.discard(scheduled.game_id)

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
