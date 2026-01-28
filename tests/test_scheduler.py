"""Tests for the dashboard_server scheduler module."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
            "event_id": "401772976",
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

            assert schedule_id.startswith("sched-nba-001-")

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
    async def test_unique_persistence_files_for_same_game(self, mock_trial_manager):
        """Test that multiple trials for the same game get unique persistence files."""
        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
        )

        now = datetime.now(timezone.utc)
        event_time = now + timedelta(hours=3)
        event_id = "401810525"
        sport_type = "nba"

        # Schedule two trials for the same game
        schedule_id_1 = await scheduler.schedule_trial(
            scenario_name="nba-moneyline",
            scenario_config={"hub": {}},
            sport_type=sport_type,
            event_id=event_id,
            event_time=event_time,
        )

        # Add a small delay to ensure different timestamps
        import asyncio

        await asyncio.sleep(0.01)

        schedule_id_2 = await scheduler.schedule_trial(
            scenario_name="nba-moneyline",
            scenario_config={"hub": {}},
            sport_type=sport_type,
            event_id=event_id,
            event_time=event_time,
        )

        # Verify schedule IDs are different
        assert schedule_id_1 != schedule_id_2
        assert schedule_id_1.startswith(f"sched-{sport_type}-{event_id}-")
        assert schedule_id_2.startswith(f"sched-{sport_type}-{event_id}-")

        # Retrieve the scheduled trials
        trial_1 = scheduler.get_scheduled(schedule_id_1)
        trial_2 = scheduler.get_scheduled(schedule_id_2)

        assert trial_1 is not None
        assert trial_2 is not None

        # Extract persistence files from configs if they exist
        persistence_file_1 = trial_1.scenario_config.get("hub", {}).get(
            "persistence_file"
        )
        persistence_file_2 = trial_2.scenario_config.get("hub", {}).get(
            "persistence_file"
        )

        # If persistence files are set, they should be different
        if persistence_file_1 and persistence_file_2:
            assert persistence_file_1 != persistence_file_2
            # Each should contain the unique schedule_id
            assert schedule_id_1 in persistence_file_1
            assert schedule_id_2 in persistence_file_2

    @pytest.mark.asyncio
    async def test_schedule_id_generation_is_unique(self, mock_trial_manager):
        """Test that _generate_schedule_id creates unique IDs for same game."""
        scheduler = ScheduleManager(
            trial_manager=mock_trial_manager,
            store=None,
        )

        sport_type = "nba"
        event_id = "401810525"

        # Generate multiple schedule IDs for the same game
        schedule_ids = set()
        for _ in range(5):
            schedule_id = scheduler._generate_schedule_id(sport_type, event_id)
            schedule_ids.add(schedule_id)
            # Small delay to ensure different timestamps
            import asyncio

            await asyncio.sleep(0.001)

        # All IDs should be unique
        assert len(schedule_ids) == 5

        # All should have the correct format
        for schedule_id in schedule_ids:
            assert schedule_id.startswith(f"sched-{sport_type}-{event_id}-")
            # Should have 8-char hash suffix
            hash_part = schedule_id.split("-")[-1]
            assert len(hash_part) == 8

    @pytest.mark.asyncio
    async def test_persistence_file_with_schedule_id(self, mock_trial_manager):
        """Test that scheduling with data_dir creates unique persistence files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            scheduler = ScheduleManager(
                trial_manager=mock_trial_manager,
                store=None,
            )

            now = datetime.now(timezone.utc)
            event_time = now + timedelta(hours=3)
            event_id = "401810525"
            sport_type = "nba"

            # Schedule two trials for the same game with data_dir
            # First, pre-generate schedule IDs and set persistence_file
            schedule_id_1 = scheduler._generate_schedule_id(sport_type, event_id)
            game_date = "2026-01-27"
            persistence_file_1 = f"{tmpdir}/{game_date}/{schedule_id_1}.jsonl"

            import asyncio

            await asyncio.sleep(0.01)

            schedule_id_2 = scheduler._generate_schedule_id(sport_type, event_id)
            persistence_file_2 = f"{tmpdir}/{game_date}/{schedule_id_2}.jsonl"

            # Schedule trials with pre-determined persistence files
            config_1 = {"hub": {"persistence_file": persistence_file_1}}
            config_2 = {"hub": {"persistence_file": persistence_file_2}}

            returned_id_1 = await scheduler.schedule_trial(
                scenario_name="nba-moneyline",
                scenario_config=config_1,
                sport_type=sport_type,
                event_id=event_id,
                event_time=event_time,
                schedule_id=schedule_id_1,
            )

            returned_id_2 = await scheduler.schedule_trial(
                scenario_name="nba-moneyline",
                scenario_config=config_2,
                sport_type=sport_type,
                event_id=event_id,
                event_time=event_time,
                schedule_id=schedule_id_2,
            )

            # Verify schedule IDs are different
            assert returned_id_1 != returned_id_2
            assert returned_id_1 == schedule_id_1
            assert returned_id_2 == schedule_id_2

            # Retrieve the scheduled trials
            trial_1 = scheduler.get_scheduled(schedule_id_1)
            trial_2 = scheduler.get_scheduled(schedule_id_2)

            assert trial_1 is not None
            assert trial_2 is not None

            # Verify persistence files are different and contain schedule_id
            pf_1 = trial_1.scenario_config.get("hub", {}).get("persistence_file")
            pf_2 = trial_2.scenario_config.get("hub", {}).get("persistence_file")

            assert pf_1 == persistence_file_1
            assert pf_2 == persistence_file_2
            assert pf_1 != pf_2
            assert schedule_id_1 in pf_1
            assert schedule_id_2 in pf_2

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

        # Defaults: max_concurrent_launches=10, grace_period_seconds=300.0
        assert scheduler._max_concurrent_launches == 10
        assert scheduler._grace_period_seconds == 300.0

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
            "event_id": "001",
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
            "event_id": "001",
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
