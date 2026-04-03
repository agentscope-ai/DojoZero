"""Tests for the dashboard_server scheduler module."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dojozero.dashboard_server._scheduler import (
    FileSchedulerStore,
    ScheduledTrial,
    ScheduledTrialPhase,
    ScheduleManager,
    TrialSource,
    TrialSourceConfig,
)


class TestScheduledTrial:
    """Tests for ScheduledTrial dataclass."""

    def test_to_dict(self):
        """Test serialization to dictionary."""
        now = datetime.now(timezone.utc)
        event_time = now + timedelta(hours=2)
        start_time = event_time - timedelta(hours=2)

        trial = ScheduledTrial(
            schedule_id="sched-nba-001-abc123",
            scenario_name="nba-moneyline",
            scenario_config={"game_id": "001"},
            sport_type="nba",
            game_id="001",
            event_time=event_time,
            scheduled_start_time=start_time,
            pre_start_hours=2.0,
            check_interval_seconds=60.0,
            auto_stop_on_completion=True,
            phase=ScheduledTrialPhase.WAITING,
            created_at=now,
        )

        d = trial.to_dict()

        assert d["schedule_id"] == "sched-nba-001-abc123"
        assert d["scenario_name"] == "nba-moneyline"
        assert d["sport_type"] == "nba"
        assert d["game_id"] == "001"
        assert d["phase"] == "waiting"
        assert d["pre_start_hours"] == 2.0
        assert d["check_interval_seconds"] == 60.0

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        now = datetime.now(timezone.utc)
        event_time = now + timedelta(hours=2)
        start_time = event_time - timedelta(hours=2)

        d = {
            "schedule_id": "sched-nfl-001-xyz789",
            "scenario_name": "nfl-moneyline",
            "scenario_config": {"event_id": "401772976"},
            "sport_type": "nfl",
            "game_id": "401772976",
            "event_time": event_time.isoformat(),
            "scheduled_start_time": start_time.isoformat(),
            "pre_start_hours": 1.0,
            "check_interval_seconds": 30.0,
            "auto_stop_on_completion": True,
            "phase": "running",
            "created_at": now.isoformat(),
            "launched_trial_id": "nfl-game-401772976-abc",
        }

        trial = ScheduledTrial.from_dict(d)

        assert trial.schedule_id == "sched-nfl-001-xyz789"
        assert trial.scenario_name == "nfl-moneyline"
        assert trial.sport_type == "nfl"
        assert trial.phase == ScheduledTrialPhase.RUNNING
        assert trial.launched_trial_id == "nfl-game-401772976-abc"


class TestFileSchedulerStore:
    """Tests for FileSchedulerStore persistence."""

    def test_save_and_load(self):
        """Test saving and loading schedules."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "schedules.json"
            store = FileSchedulerStore(path)

            now = datetime.now(timezone.utc)
            event_time = now + timedelta(hours=2)
            start_time = event_time - timedelta(hours=2)

            trial = ScheduledTrial(
                schedule_id="sched-test-001",
                scenario_name="test-scenario",
                scenario_config={"key": "value"},
                sport_type="nba",
                game_id="001",
                event_time=event_time,
                scheduled_start_time=start_time,
                pre_start_hours=2.0,
                check_interval_seconds=60.0,
                auto_stop_on_completion=True,
                created_at=now,
            )

            # Save
            store.save([trial])
            assert path.exists()

            # Load
            loaded = store.load()
            assert len(loaded) == 1
            assert loaded[0].schedule_id == "sched-test-001"
            assert loaded[0].scenario_name == "test-scenario"

    def test_load_empty_file(self):
        """Test loading from non-existent file returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "nonexistent.json"
            store = FileSchedulerStore(path)

            loaded = store.load()
            assert loaded == []

    def test_creates_parent_directories(self):
        """Test that parent directories are created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "nested" / "path" / "schedules.json"
            store = FileSchedulerStore(path)

            now = datetime.now(timezone.utc)
            trial = ScheduledTrial(
                schedule_id="test",
                scenario_name="test",
                scenario_config={},
                sport_type="nba",
                game_id="001",
                event_time=now,
                scheduled_start_time=now,
                pre_start_hours=2.0,
                check_interval_seconds=60.0,
                auto_stop_on_completion=True,
                created_at=now,
            )

            store.save([trial])
            assert path.exists()


class TestScheduleManager:
    """Tests for ScheduleManager."""

    @pytest.fixture
    def mock_trial_manager(self):
        """Create a mock TrialManager."""
        manager = MagicMock()
        manager.submit = AsyncMock(return_value="trial-123")
        manager.cancel = AsyncMock(return_value=True)
        manager.dashboard = MagicMock()
        manager.dashboard.stop_trial = AsyncMock()
        return manager

    @pytest.mark.asyncio
    async def test_schedule_trial(self, mock_trial_manager):
        """Test scheduling a single trial."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "schedules.json"
            store = FileSchedulerStore(path)
            scheduler = ScheduleManager(
                trial_manager=mock_trial_manager,
                store=store,
            )

            event_time = datetime.now(timezone.utc) + timedelta(hours=3)

            schedule_id = await scheduler.schedule_trial(
                scenario_name="nba-moneyline",
                scenario_config={"game_id": "001"},
                sport_type="nba",
                game_id="001",
                event_time=event_time,
                pre_start_hours=2.0,
                check_interval_seconds=60.0,
            )

            assert schedule_id == "sched-nba-001"

            # Check it was persisted
            loaded = store.load()
            assert len(loaded) == 1
            assert loaded[0].schedule_id == schedule_id

    @pytest.mark.asyncio
    async def test_cancel_scheduled(self, mock_trial_manager):
        """Test cancelling a scheduled trial."""
        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
        )

        event_time = datetime.now(timezone.utc) + timedelta(hours=3)

        schedule_id = await scheduler.schedule_trial(
            scenario_name="nba-moneyline",
            scenario_config={},
            sport_type="nba",
            game_id="001",
            event_time=event_time,
        )

        # Cancel
        result = await scheduler.cancel_scheduled(schedule_id)
        assert result is True

        # Verify cancelled
        scheduled = scheduler.get_scheduled(schedule_id)
        assert scheduled is not None
        assert scheduled.phase == ScheduledTrialPhase.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self, mock_trial_manager):
        """Test cancelling a non-existent schedule returns False."""
        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
        )

        result = await scheduler.cancel_scheduled("nonexistent-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_list_scheduled(self, mock_trial_manager):
        """Test listing all scheduled trials."""
        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
        )

        now = datetime.now(timezone.utc)

        # Schedule multiple
        await scheduler.schedule_trial(
            scenario_name="nba-moneyline",
            scenario_config={},
            sport_type="nba",
            game_id="001",
            event_time=now + timedelta(hours=3),
        )
        await scheduler.schedule_trial(
            scenario_name="nba-moneyline",
            scenario_config={},
            sport_type="nba",
            game_id="002",
            event_time=now + timedelta(hours=4),
        )

        schedules = scheduler.list_scheduled()
        assert len(schedules) == 2

    @pytest.mark.asyncio
    async def test_deterministic_schedule_ids_for_same_game(self, mock_trial_manager):
        """Test that the same game always produces the same schedule_id (deterministic)."""
        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
        )

        now = datetime.now(timezone.utc)
        event_time = now + timedelta(hours=3)
        game_id = "401810525"
        sport_type = "nba"

        schedule_id_1 = await scheduler.schedule_trial(
            scenario_name="nba-moneyline",
            scenario_config={"hub": {}},
            sport_type=sport_type,
            game_id=game_id,
            event_time=event_time,
        )

        # Same game produces same schedule_id (deterministic for cluster dedup)
        assert schedule_id_1 == f"sched-{sport_type}-{game_id}"

        # Different games produce different IDs
        schedule_id_other = await scheduler.schedule_trial(
            scenario_name="nba-moneyline",
            scenario_config={"hub": {}},
            sport_type=sport_type,
            game_id="401810526",
            event_time=event_time,
        )
        assert schedule_id_1 != schedule_id_other

    @pytest.mark.asyncio
    async def test_schedule_id_generation_is_deterministic(self, mock_trial_manager):
        """Test that _generate_schedule_id is deterministic for same game."""
        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
        )

        sport_type = "nba"
        game_id = "401810525"

        # Generate multiple schedule IDs for the same game — all should be identical
        schedule_ids = set()
        for _ in range(5):
            schedule_id = scheduler._generate_schedule_id(sport_type, game_id)
            schedule_ids.add(schedule_id)

        assert len(schedule_ids) == 1

        # Should have the correct format
        schedule_id = schedule_ids.pop()
        assert schedule_id == f"sched-{sport_type}-{game_id}"

    @pytest.mark.asyncio
    async def test_persistence_file_with_schedule_id(self, mock_trial_manager):
        """Test that persistence files use deterministic schedule_id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            scheduler = ScheduleManager(
                trial_manager=mock_trial_manager,
                store=None,
            )

            now = datetime.now(timezone.utc)
            event_time = now + timedelta(hours=3)
            game_id = "401810525"
            sport_type = "nba"

            schedule_id = scheduler._generate_schedule_id(sport_type, game_id)
            game_date = "2026-01-27"
            persistence_file = f"{tmpdir}/{game_date}/{schedule_id}.jsonl"

            config = {"hub": {"persistence_file": persistence_file}}

            returned_id = await scheduler.schedule_trial(
                scenario_name="nba-moneyline",
                scenario_config=config,
                sport_type=sport_type,
                game_id=game_id,
                event_time=event_time,
                schedule_id=schedule_id,
            )

            assert returned_id == schedule_id

            trial = scheduler.get_scheduled(schedule_id)
            assert trial is not None

            pf = trial.scenario_config.get("hub", {}).get("persistence_file")
            assert pf == persistence_file
            assert schedule_id in pf

            # Different game produces different persistence path
            schedule_id_2 = scheduler._generate_schedule_id(sport_type, "401810526")
            assert schedule_id != schedule_id_2

    @pytest.mark.asyncio
    async def test_start_loads_persisted(self, mock_trial_manager):
        """Test that start() loads persisted schedules."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "schedules.json"
            store = FileSchedulerStore(path)

            # Pre-populate store
            now = datetime.now(timezone.utc)
            event_time = now + timedelta(hours=3)
            trial = ScheduledTrial(
                schedule_id="persisted-001",
                scenario_name="test",
                scenario_config={},
                sport_type="nba",
                game_id="001",
                event_time=event_time,
                scheduled_start_time=event_time - timedelta(hours=2),
                pre_start_hours=2.0,
                check_interval_seconds=60.0,
                auto_stop_on_completion=True,
                phase=ScheduledTrialPhase.WAITING,
                created_at=now,
            )
            store.save([trial])

            # Create new scheduler and start
            scheduler = ScheduleManager(
                trial_manager=mock_trial_manager,
                store=store,
            )
            await scheduler.start()

            # Should have loaded the persisted schedule
            assert len(scheduler.list_scheduled()) == 1
            assert scheduler.get_scheduled("persisted-001") is not None

            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_completed_schedules_not_loaded(self, mock_trial_manager):
        """Test that completed schedules are not loaded on start."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "schedules.json"
            store = FileSchedulerStore(path)

            # Pre-populate with completed schedule
            now = datetime.now(timezone.utc)
            trial = ScheduledTrial(
                schedule_id="completed-001",
                scenario_name="test",
                scenario_config={},
                sport_type="nba",
                game_id="001",
                event_time=now,
                scheduled_start_time=now,
                pre_start_hours=2.0,
                check_interval_seconds=60.0,
                auto_stop_on_completion=True,
                phase=ScheduledTrialPhase.COMPLETED,  # Already completed
                created_at=now,
            )
            store.save([trial])

            scheduler = ScheduleManager(
                trial_manager=mock_trial_manager,
                store=store,
            )
            await scheduler.start()

            # Should NOT have loaded the completed schedule
            assert len(scheduler.list_scheduled()) == 0

            await scheduler.stop()


class TestTrialSource:
    """Tests for TrialSource dataclass."""

    def test_to_dict(self):
        """Test serialization to dictionary."""
        now = datetime.now(timezone.utc)

        config = TrialSourceConfig(
            scenario_name="nba-moneyline",
            scenario_config={"key": "value"},
            pre_start_hours=2.0,
            check_interval_seconds=60.0,
            auto_stop_on_completion=True,
            data_dir="outputs",
        )

        source = TrialSource(
            source_id="nba-source-1",
            sport_type="nba",
            config=config,
            enabled=True,
            created_at=now,
        )

        d = source.to_dict()

        assert d["source_id"] == "nba-source-1"
        assert d["sport_type"] == "nba"
        assert d["enabled"] is True
        assert d["config"]["scenario_name"] == "nba-moneyline"
        assert d["config"]["pre_start_hours"] == 2.0

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        now = datetime.now(timezone.utc)

        d = {
            "source_id": "nfl-source-1",
            "sport_type": "nfl",
            "config": {
                "scenario_name": "nfl-moneyline",
                "scenario_config": {"event_id": "401772976"},
                "pre_start_hours": 1.0,
                "check_interval_seconds": 30.0,
                "auto_stop_on_completion": True,
                "data_dir": "outputs",
            },
            "enabled": True,
            "created_at": now.isoformat(),
            "last_sync_at": None,
        }

        source = TrialSource.from_dict(d)

        assert source.source_id == "nfl-source-1"
        assert source.sport_type == "nfl"
        assert source.config.scenario_name == "nfl-moneyline"


class TestFileSchedulerStoreTrialSources:
    """Tests for FileSchedulerStore trial source persistence."""

    def test_save_and_load_sources(self):
        """Test saving and loading trial sources."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "schedules.json"
            store = FileSchedulerStore(path)

            now = datetime.now(timezone.utc)

            config = TrialSourceConfig(
                scenario_name="nba-moneyline",
                scenario_config={"key": "value"},
                pre_start_hours=2.0,
            )

            source = TrialSource(
                source_id="test-source",
                sport_type="nba",
                config=config,
                enabled=True,
                created_at=now,
            )

            # Save
            store.save_sources([source])

            # Load
            loaded = store.load_sources()
            assert len(loaded) == 1
            assert loaded[0].source_id == "test-source"
            assert loaded[0].config.scenario_name == "nba-moneyline"

    def test_load_empty_sources(self):
        """Test loading sources from non-existent file returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "nonexistent.json"
            store = FileSchedulerStore(path)

            loaded = store.load_sources()
            assert loaded == []


class TestScheduleManagerTrialSources:
    """Tests for ScheduleManager trial source functionality."""

    @pytest.fixture
    def mock_trial_manager(self):
        """Create a mock TrialManager."""
        manager = MagicMock()
        manager.submit = AsyncMock(return_value="trial-123")
        manager.cancel = AsyncMock(return_value=True)
        manager.dashboard = MagicMock()
        manager.dashboard.stop_trial = AsyncMock()
        return manager

    def test_register_source(self, mock_trial_manager):
        """Test registering a trial source."""
        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
        )

        config = TrialSourceConfig(
            scenario_name="nba-moneyline",
            scenario_config={},
            pre_start_hours=2.0,
        )

        source = scheduler.register_source(
            source_id="nba-source-1",
            sport_type="nba",
            config=config,
        )

        assert source.source_id == "nba-source-1"
        assert source.sport_type == "nba"
        assert source.enabled is True

        # Verify it's retrievable
        assert scheduler.get_source("nba-source-1") is not None

    def test_register_duplicate_source_raises(self, mock_trial_manager):
        """Test registering a duplicate source raises ValueError."""
        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
        )

        config = TrialSourceConfig(
            scenario_name="nba-moneyline",
            scenario_config={},
        )

        scheduler.register_source(
            source_id="nba-source-1",
            sport_type="nba",
            config=config,
        )

        with pytest.raises(ValueError, match="already exists"):
            scheduler.register_source(
                source_id="nba-source-1",
                sport_type="nba",
                config=config,
            )

    def test_unregister_source(self, mock_trial_manager):
        """Test unregistering a trial source."""
        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
        )

        config = TrialSourceConfig(scenario_name="nba-moneyline", scenario_config={})

        scheduler.register_source(
            source_id="nba-source-1",
            sport_type="nba",
            config=config,
        )

        result = scheduler.unregister_source("nba-source-1")
        assert result is True
        assert scheduler.get_source("nba-source-1") is None

    def test_unregister_nonexistent(self, mock_trial_manager):
        """Test unregistering a non-existent source returns False."""
        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
        )

        result = scheduler.unregister_source("nonexistent")
        assert result is False

    def test_list_sources(self, mock_trial_manager):
        """Test listing all trial sources."""
        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
        )

        config = TrialSourceConfig(scenario_name="test", scenario_config={})

        scheduler.register_source(
            source_id="source-1",
            sport_type="nba",
            config=config,
        )
        scheduler.register_source(
            source_id="source-2",
            sport_type="nfl",
            config=config,
        )

        sources = scheduler.list_sources()
        assert len(sources) == 2

    def test_set_source_enabled(self, mock_trial_manager):
        """Test enabling/disabling a trial source."""
        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
        )

        config = TrialSourceConfig(scenario_name="test", scenario_config={})

        scheduler.register_source(
            source_id="source-1",
            sport_type="nba",
            config=config,
        )

        # Disable
        result = scheduler.set_source_enabled("source-1", False)
        assert result is True
        source = scheduler.get_source("source-1")
        assert source is not None
        assert source.enabled is False

        # Enable
        result = scheduler.set_source_enabled("source-1", True)
        assert result is True
        source = scheduler.get_source("source-1")
        assert source is not None
        assert source.enabled is True

    @pytest.mark.asyncio
    async def test_start_loads_persisted_sources(self, mock_trial_manager):
        """Test that start() loads persisted trial sources."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "schedules.json"
            store = FileSchedulerStore(path)

            # Pre-populate store with a source
            config = TrialSourceConfig(
                scenario_name="nba-moneyline",
                scenario_config={},
            )
            source = TrialSource(
                source_id="persisted-source",
                sport_type="nba",
                config=config,
            )
            store.save_sources([source])

            # Create new scheduler and start
            scheduler = ScheduleManager(
                trial_manager=mock_trial_manager,
                store=store,
            )
            await scheduler.start()

            # Should have loaded the persisted source
            assert len(scheduler.list_sources()) == 1
            assert scheduler.get_source("persisted-source") is not None

            await scheduler.stop()


class TestScheduleManagerConcurrencyAndGracePeriod:
    """Tests for concurrent launch limiting and grace period handling."""

    @pytest.fixture
    def mock_trial_manager(self):
        """Create a mock TrialManager."""
        manager = MagicMock()
        manager.submit = AsyncMock(return_value="trial-123")
        manager.cancel = AsyncMock(return_value=True)
        manager.dashboard = MagicMock()
        manager.dashboard.stop_trial = AsyncMock()
        return manager

    def test_max_concurrent_launches_parameter(self, mock_trial_manager):
        """Test that max_concurrent_launches parameter is set correctly."""
        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
            max_concurrent_launches=5,
        )

        assert scheduler._max_concurrent_launches == 5
        # Check semaphore has correct limit
        assert scheduler._launch_semaphore._value == 5

    def test_grace_period_parameter(self, mock_trial_manager):
        """Test that grace_period_seconds parameter is set correctly."""
        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
            grace_period_seconds=600.0,  # 10 minutes
        )

        assert scheduler._grace_period_seconds == 600.0

    def test_default_parameters(self, mock_trial_manager):
        """Test default values for new parameters."""
        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
        )

        # Defaults: max_concurrent_launches=10, grace_period_seconds=60.0
        assert scheduler._max_concurrent_launches == 10
        assert scheduler._grace_period_seconds == 60.0

    def test_scheduled_trial_monitoring_fields_serialization(self):
        """Test that monitoring fields are serialized correctly."""
        now = datetime.now(timezone.utc)
        event_time = now + timedelta(hours=2)
        start_time = event_time - timedelta(hours=2)
        monitoring_start = now - timedelta(minutes=5)

        trial = ScheduledTrial(
            schedule_id="sched-nba-001-abc123",
            scenario_name="nba-moneyline",
            scenario_config={"game_id": "001"},
            sport_type="nba",
            game_id="001",
            event_time=event_time,
            scheduled_start_time=start_time,
            pre_start_hours=2.0,
            check_interval_seconds=60.0,
            auto_stop_on_completion=True,
            phase=ScheduledTrialPhase.MONITORING,
            created_at=now,
            monitoring_started_at=monitoring_start,
            initial_game_status=3,  # Already finished
        )

        d = trial.to_dict()

        assert d["monitoring_started_at"] == monitoring_start.isoformat()
        assert d["initial_game_status"] == 3

    def test_scheduled_trial_monitoring_fields_deserialization(self):
        """Test that monitoring fields are deserialized correctly."""
        now = datetime.now(timezone.utc)
        event_time = now + timedelta(hours=2)
        start_time = event_time - timedelta(hours=2)
        monitoring_start = now - timedelta(minutes=5)

        d = {
            "schedule_id": "sched-nba-001-abc123",
            "scenario_name": "nba-moneyline",
            "scenario_config": {"game_id": "001"},
            "sport_type": "nba",
            "game_id": "001",
            "event_time": event_time.isoformat(),
            "scheduled_start_time": start_time.isoformat(),
            "pre_start_hours": 2.0,
            "check_interval_seconds": 60.0,
            "auto_stop_on_completion": True,
            "phase": "monitoring",
            "created_at": now.isoformat(),
            "monitoring_started_at": monitoring_start.isoformat(),
            "initial_game_status": 3,
        }

        trial = ScheduledTrial.from_dict(d)

        assert trial.monitoring_started_at is not None
        assert abs((trial.monitoring_started_at - monitoring_start).total_seconds()) < 1
        assert trial.initial_game_status == 3

    def test_scheduled_trial_monitoring_fields_none_serialization(self):
        """Test that None monitoring fields are serialized correctly."""
        now = datetime.now(timezone.utc)
        event_time = now + timedelta(hours=2)
        start_time = event_time - timedelta(hours=2)

        trial = ScheduledTrial(
            schedule_id="sched-nba-001-abc123",
            scenario_name="nba-moneyline",
            scenario_config={"game_id": "001"},
            sport_type="nba",
            game_id="001",
            event_time=event_time,
            scheduled_start_time=start_time,
            pre_start_hours=2.0,
            check_interval_seconds=60.0,
            auto_stop_on_completion=True,
            phase=ScheduledTrialPhase.WAITING,
            created_at=now,
            # monitoring fields not set (None by default)
        )

        d = trial.to_dict()

        assert d["monitoring_started_at"] is None
        assert d["initial_game_status"] is None

    def test_scheduled_trial_monitoring_fields_none_deserialization(self):
        """Test that missing/None monitoring fields deserialize correctly."""
        now = datetime.now(timezone.utc)
        event_time = now + timedelta(hours=2)
        start_time = event_time - timedelta(hours=2)

        d = {
            "schedule_id": "sched-nba-001-abc123",
            "scenario_name": "nba-moneyline",
            "scenario_config": {"game_id": "001"},
            "sport_type": "nba",
            "game_id": "001",
            "event_time": event_time.isoformat(),
            "scheduled_start_time": start_time.isoformat(),
            "pre_start_hours": 2.0,
            "check_interval_seconds": 60.0,
            "auto_stop_on_completion": True,
            "phase": "waiting",
            "created_at": now.isoformat(),
            # monitoring fields not present
        }

        trial = ScheduledTrial.from_dict(d)

        assert trial.monitoring_started_at is None
        assert trial.initial_game_status is None


class TestClusterDedup:
    """Tests for cluster-wide game deduplication."""

    @pytest.fixture
    def mock_trial_manager(self):
        """Create a mock TrialManager."""
        manager = MagicMock()
        manager.submit = AsyncMock(return_value="trial-123")
        manager.cancel = AsyncMock(return_value=True)
        manager.dashboard = MagicMock()
        manager.dashboard.stop_trial = AsyncMock()
        return manager

    @pytest.fixture
    def mock_peer_registry(self):
        """Create a mock PeerRegistry with claim_game support."""
        registry = MagicMock()
        registry.claim_game = AsyncMock(return_value=True)
        registry.is_game_claimed = AsyncMock(return_value=False)
        registry.get_peers = AsyncMock(return_value=[])
        registry.register_trial = AsyncMock()
        return registry

    def test_generate_schedule_id_deterministic(self, mock_trial_manager):
        """Same sport_type+game_id always produces the same schedule_id."""
        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
        )

        id1 = scheduler._generate_schedule_id("nba", "401810490")
        id2 = scheduler._generate_schedule_id("nba", "401810490")
        assert id1 == id2

        # Different game_id produces different schedule_id
        id3 = scheduler._generate_schedule_id("nba", "401810491")
        assert id1 != id3

    def test_generate_schedule_id_cross_host_consistent(self, mock_trial_manager):
        """Two ScheduleManagers produce the same schedule_id for the same game."""
        scheduler_a = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
            server_id="server-a",
        )
        scheduler_b = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
            server_id="server-b",
        )

        id_a = scheduler_a._generate_schedule_id("nba", "401810490")
        id_b = scheduler_b._generate_schedule_id("nba", "401810490")
        assert id_a == id_b

    @pytest.mark.asyncio
    async def test_sync_source_skips_claimed_game(
        self, mock_trial_manager, mock_peer_registry
    ):
        """When claim_game returns False, the game is skipped."""
        mock_peer_registry.claim_game = AsyncMock(return_value=False)

        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
            peer_registry=mock_peer_registry,
            server_id="server-1",
        )

        config = TrialSourceConfig(
            scenario_name="nba-moneyline",
            scenario_config={},
        )
        source = scheduler.register_source(
            source_id="nba-source",
            sport_type="nba",
            config=config,
        )

        # Mock the game fetcher to return a game
        game = MagicMock()
        game.game_id = "401810490"
        game.game_time_utc = datetime.now(timezone.utc) + timedelta(hours=3)
        game.status = 1  # scheduled
        game.status_text = "Scheduled"
        game.short_name = "LAL @ BOS"
        game.home_team = MagicMock(name="Boston Celtics", tricode="BOS")
        game.away_team = MagicMock(name="Los Angeles Lakers", tricode="LAL")

        with patch.object(
            scheduler._nba_fetcher,
            "fetch_games_for_date",
            new_callable=AsyncMock,
            return_value=[game],
        ):
            result = await scheduler._sync_source(source)

        assert len(result) == 0
        mock_peer_registry.claim_game.assert_awaited_once_with(
            "nba", "401810490", "server-1"
        )

    @pytest.mark.asyncio
    async def test_sync_source_claims_game_before_scheduling(
        self, mock_trial_manager, mock_peer_registry
    ):
        """When claim_game returns True, the game is scheduled."""
        mock_peer_registry.claim_game = AsyncMock(return_value=True)

        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
            peer_registry=mock_peer_registry,
            server_id="server-1",
        )

        config = TrialSourceConfig(
            scenario_name="nba-moneyline",
            scenario_config={},
        )
        source = scheduler.register_source(
            source_id="nba-source",
            sport_type="nba",
            config=config,
        )

        game = MagicMock()
        game.game_id = "401810490"
        game.game_time_utc = datetime.now(timezone.utc) + timedelta(hours=3)
        game.status = 1
        game.status_text = "Scheduled"
        game.short_name = "LAL @ BOS"
        game.home_team = MagicMock(name="Boston Celtics", tricode="BOS")
        game.away_team = MagicMock(name="Los Angeles Lakers", tricode="LAL")

        with patch.object(
            scheduler._nba_fetcher,
            "fetch_games_for_date",
            new_callable=AsyncMock,
            return_value=[game],
        ):
            result = await scheduler._sync_source(source)

        assert len(result) == 1
        mock_peer_registry.claim_game.assert_awaited_once_with(
            "nba", "401810490", "server-1"
        )

    @pytest.mark.asyncio
    async def test_sync_source_fails_closed_on_claim_error(
        self, mock_trial_manager, mock_peer_registry
    ):
        """When claim_game raises, the game is skipped (fail-closed)."""
        mock_peer_registry.claim_game = AsyncMock(
            side_effect=ConnectionError("Redis unavailable")
        )

        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
            peer_registry=mock_peer_registry,
            server_id="server-1",
        )

        config = TrialSourceConfig(
            scenario_name="nba-moneyline",
            scenario_config={},
        )
        source = scheduler.register_source(
            source_id="nba-source",
            sport_type="nba",
            config=config,
        )

        game = MagicMock()
        game.game_id = "401810490"
        game.game_time_utc = datetime.now(timezone.utc) + timedelta(hours=3)
        game.status = 1
        game.status_text = "Scheduled"
        game.short_name = "LAL @ BOS"
        game.home_team = MagicMock(name="Boston Celtics", tricode="BOS")
        game.away_team = MagicMock(name="Los Angeles Lakers", tricode="LAL")

        with patch.object(
            scheduler._nba_fetcher,
            "fetch_games_for_date",
            new_callable=AsyncMock,
            return_value=[game],
        ):
            result = await scheduler._sync_source(source)

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_sync_source_no_claim_without_peer_registry(
        self, mock_trial_manager
    ):
        """Without a peer_registry, scheduling proceeds without claim checks."""
        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
            # No peer_registry
        )

        config = TrialSourceConfig(
            scenario_name="nba-moneyline",
            scenario_config={},
        )
        source = scheduler.register_source(
            source_id="nba-source",
            sport_type="nba",
            config=config,
        )

        game = MagicMock()
        game.game_id = "401810490"
        game.game_time_utc = datetime.now(timezone.utc) + timedelta(hours=3)
        game.status = 1
        game.status_text = "Scheduled"
        game.short_name = "LAL @ BOS"
        game.home_team = MagicMock(name="Boston Celtics", tricode="BOS")
        game.away_team = MagicMock(name="Los Angeles Lakers", tricode="LAL")

        with patch.object(
            scheduler._nba_fetcher,
            "fetch_games_for_date",
            new_callable=AsyncMock,
            return_value=[game],
        ):
            result = await scheduler._sync_source(source)

        # Should schedule without any claim check
        assert len(result) == 1
