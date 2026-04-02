"""Tests for the multi-server cluster module."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from dojozero.core import (
    BaseTrialMetadata,
    FileSystemOrchestratorStore,
    TrialOrchestrator,
    TrialPhase,
    TrialRecord,
    TrialSpec,
    TrialStatus,
)
from dojozero.dashboard_server._cluster import (
    ClusterConfig,
    FileLeaderElector,
    StaticPeerRegistry,
    create_cluster,
)
from dojozero.dashboard_server._trial_manager import (
    TrialManager,
)


# ---------------------------------------------------------------------------
# FileLeaderElector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_leader_elector_acquires_leadership(tmp_path: Path) -> None:
    lock_file = str(tmp_path / "leader.lock")
    elector = FileLeaderElector(server_id="server-1", lock_path=lock_file)
    await elector.start()
    # Give the election loop a moment to acquire
    await asyncio.sleep(0.3)
    assert elector.is_leader()
    await elector.stop()
    assert not elector.is_leader()


@pytest.mark.asyncio
async def test_file_leader_elector_only_one_leader(tmp_path: Path) -> None:
    lock_file = str(tmp_path / "leader.lock")
    elector1 = FileLeaderElector(server_id="server-1", lock_path=lock_file)
    elector2 = FileLeaderElector(server_id="server-2", lock_path=lock_file)
    await elector1.start()
    await asyncio.sleep(0.3)
    assert elector1.is_leader()

    await elector2.start()
    await asyncio.sleep(0.3)
    # Only one can be leader at a time
    assert elector1.is_leader() != elector2.is_leader() or (
        elector1.is_leader() and not elector2.is_leader()
    )

    await elector1.stop()
    await elector2.stop()


@pytest.mark.asyncio
async def test_file_leader_elector_wait_for_leader(tmp_path: Path) -> None:
    lock_file = str(tmp_path / "leader.lock")
    elector = FileLeaderElector(server_id="server-1", lock_path=lock_file)
    await elector.start()
    leader_id = await asyncio.wait_for(elector.wait_for_leader(), timeout=5.0)
    assert leader_id == "server-1"
    await elector.stop()


# ---------------------------------------------------------------------------
# StaticPeerRegistry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_static_peer_registry_get_peers() -> None:
    registry = StaticPeerRegistry(
        server_id="self",
        server_url="http://localhost:8000",
        peer_urls=["http://localhost:8001", "http://localhost:8002"],
    )
    await registry.start()
    peers = await registry.get_peers()
    assert len(peers) == 3  # self + 2 peers
    urls = {p.server_url for p in peers}
    assert "http://localhost:8000" in urls
    assert "http://localhost:8001" in urls
    assert "http://localhost:8002" in urls
    await registry.stop()


@pytest.mark.asyncio
async def test_static_peer_registry_trial_ownership() -> None:
    registry = StaticPeerRegistry(
        server_id="self",
        server_url="http://localhost:8000",
        peer_urls=["http://localhost:8001"],
    )
    await registry.start()

    # No owner initially
    peer = await registry.get_peer_for_trial("trial-1")
    assert peer is None

    # Register trial ownership
    await registry.register_trial("trial-1", "self")
    peer = await registry.get_peer_for_trial("trial-1")
    assert peer is not None
    assert peer.server_url == "http://localhost:8000"

    # Register trial on remote peer
    await registry.register_trial("trial-2", "http://localhost:8001")
    peer = await registry.get_peer_for_trial("trial-2")
    assert peer is not None
    assert peer.server_url == "http://localhost:8001"

    await registry.stop()


@pytest.mark.asyncio
async def test_static_peer_registry_active_trials() -> None:
    registry = StaticPeerRegistry(
        server_id="self",
        server_url="http://localhost:8000",
        peer_urls=[],
    )
    await registry.start()
    await registry.update_active_trials("self", 5)
    peers = await registry.get_peers()
    assert peers[0].active_trials == 5
    await registry.stop()


@pytest.mark.asyncio
async def test_static_peer_deduplicates_self() -> None:
    """Self URL should not be duplicated even if listed in peer_urls."""
    registry = StaticPeerRegistry(
        server_id="self",
        server_url="http://localhost:8000",
        peer_urls=["http://localhost:8000"],  # same as self
    )
    await registry.start()
    peers = await registry.get_peers()
    assert len(peers) == 1
    await registry.stop()


# ---------------------------------------------------------------------------
# create_cluster factory
# ---------------------------------------------------------------------------


def test_create_cluster_file_static(tmp_path: Path) -> None:
    config = ClusterConfig(
        server_id="test-server",
        server_url="http://localhost:8000",
        leader_election="file",
        discovery="static",
        peers=["http://localhost:8001"],
        shared_store_path=str(tmp_path / "leader.lock"),
    )
    elector, registry = create_cluster(config)
    assert isinstance(elector, FileLeaderElector)
    assert isinstance(registry, StaticPeerRegistry)


def test_create_cluster_defaults_server_id() -> None:
    """server_id defaults to hostname when empty."""
    import platform

    config = ClusterConfig(
        server_id="",
        server_url="http://localhost:8000",
    )
    elector, _registry = create_cluster(config)
    # FileLeaderElector should have the hostname as server_id
    assert isinstance(elector, FileLeaderElector)
    assert elector._server_id == platform.node()


def test_create_cluster_redis_requires_url() -> None:
    config = ClusterConfig(
        server_id="test",
        server_url="http://localhost:8000",
        leader_election="redis",
    )
    with pytest.raises(ValueError, match="redis_url required"):
        create_cluster(config)


# ---------------------------------------------------------------------------
# TrialRecord.owner_server_id
# ---------------------------------------------------------------------------


def test_trial_record_owner_server_id() -> None:
    from dojozero.core import BaseTrialMetadata, TrialRecord, TrialSpec

    metadata = BaseTrialMetadata(
        hub_id="test_hub",
        persistence_file="/tmp/test.jsonl",
        store_types=(),
    )
    spec = TrialSpec(
        trial_id="test-trial",
        metadata=metadata,
        operators=(),
        agents=(),
        data_streams=(),
    )
    record = TrialRecord(spec=spec, owner_server_id="server-1")
    assert record.owner_server_id == "server-1"

    record_none = TrialRecord(spec=spec)
    assert record_none.owner_server_id is None


def test_trial_record_owner_persisted(tmp_path: Path) -> None:
    """owner_server_id round-trips through FileSystemOrchestratorStore."""
    from dojozero.core import (
        BaseTrialMetadata,
        FileSystemOrchestratorStore,
        TrialRecord,
        TrialSpec,
    )

    store = FileSystemOrchestratorStore(tmp_path)
    metadata = BaseTrialMetadata(
        hub_id="test_hub",
        persistence_file="/tmp/test.jsonl",
        store_types=(),
    )
    spec = TrialSpec(
        trial_id="persist-test",
        metadata=metadata,
        operators=(),
        agents=(),
        data_streams=(),
    )
    record = TrialRecord(spec=spec, owner_server_id="server-42")
    store.upsert_trial_record(record)

    loaded = store.get_trial_record("persist-test")
    assert loaded is not None
    assert loaded.owner_server_id == "server-42"


def test_trial_record_owner_none_persisted(tmp_path: Path) -> None:
    """Records without owner_server_id should still load correctly."""
    from dojozero.core import (
        BaseTrialMetadata,
        FileSystemOrchestratorStore,
        TrialRecord,
        TrialSpec,
    )

    store = FileSystemOrchestratorStore(tmp_path)
    metadata = BaseTrialMetadata(
        hub_id="test_hub",
        persistence_file="/tmp/test.jsonl",
        store_types=(),
    )
    spec = TrialSpec(
        trial_id="no-owner",
        metadata=metadata,
        operators=(),
        agents=(),
        data_streams=(),
    )
    record = TrialRecord(spec=spec)
    store.upsert_trial_record(record)

    loaded = store.get_trial_record("no-owner")
    assert loaded is not None
    assert loaded.owner_server_id is None


# ---------------------------------------------------------------------------
# Leader failover
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_leader_failover(tmp_path: Path) -> None:
    """When the leader stops, the other elector acquires leadership."""
    lock_file = str(tmp_path / "leader.lock")
    elector1 = FileLeaderElector(server_id="server-1", lock_path=lock_file)
    elector2 = FileLeaderElector(server_id="server-2", lock_path=lock_file)

    await elector1.start()
    await asyncio.sleep(0.3)
    assert elector1.is_leader()

    await elector2.start()
    await asyncio.sleep(0.3)
    assert not elector2.is_leader()

    # Leader stops — server-2 should take over
    await elector1.stop()
    # Wait for elector2's election loop to acquire (loops every 5s, but
    # we need at most one cycle)
    for _ in range(12):
        await asyncio.sleep(0.5)
        if elector2.is_leader():
            break
    assert elector2.is_leader()

    await elector2.stop()


# ---------------------------------------------------------------------------
# Owner-aware resume
# ---------------------------------------------------------------------------


def _make_spec(trial_id: str) -> TrialSpec:
    metadata = BaseTrialMetadata(
        hub_id="test_hub",
        persistence_file="/tmp/test.jsonl",
        store_types=(),
    )
    return TrialSpec(
        trial_id=trial_id,
        metadata=metadata,
        operators=(),
        agents=(),
        data_streams=(),
    )


@pytest.mark.asyncio
async def test_resume_skips_other_servers_trials(tmp_path: Path) -> None:
    """Trials owned by a different server should not be resumed."""
    store = FileSystemOrchestratorStore(tmp_path)
    spec = _make_spec("trial-other")

    # Create a trial record owned by a different server, with RUNNING status
    status = TrialStatus(
        trial_id="trial-other",
        phase=TrialPhase.RUNNING,
        actors=(),
        metadata={},
        last_error=None,
    )
    record = TrialRecord(spec=spec, last_status=status, owner_server_id="server-B")
    store.upsert_trial_record(record)

    # Save a checkpoint so it's resumable
    from dojozero.core._trial_orchestrator import TrialCheckpoint

    store.save_checkpoint(
        TrialCheckpoint(trial_id="trial-other", actor_states={}, checkpoint_id="cp1")
    )

    orchestrator = TrialOrchestrator(store=store)
    manager = TrialManager(
        orchestrator=orchestrator,
        auto_resume=True,
        server_id="server-A",
    )

    count = await manager._resume_interrupted_trials()
    assert count == 0  # Should skip — owned by server-B


@pytest.mark.asyncio
async def test_resume_own_trials(tmp_path: Path) -> None:
    """Trials owned by this server should be resumed."""
    store = FileSystemOrchestratorStore(tmp_path)
    spec = _make_spec("trial-mine")

    status = TrialStatus(
        trial_id="trial-mine",
        phase=TrialPhase.RUNNING,
        actors=(),
        metadata={},
        last_error=None,
    )
    record = TrialRecord(spec=spec, last_status=status, owner_server_id="server-A")
    store.upsert_trial_record(record)

    from dojozero.core._trial_orchestrator import TrialCheckpoint

    store.save_checkpoint(
        TrialCheckpoint(trial_id="trial-mine", actor_states={}, checkpoint_id="cp1")
    )

    orchestrator = TrialOrchestrator(store=store)
    # Mock resume_trial to avoid actually launching
    orchestrator.resume_trial = AsyncMock()

    manager = TrialManager(
        orchestrator=orchestrator,
        auto_resume=True,
        server_id="server-A",
    )

    count = await manager._resume_interrupted_trials()
    assert count == 1
    orchestrator.resume_trial.assert_called_once()


@pytest.mark.asyncio
async def test_resume_legacy_trial_no_owner(tmp_path: Path) -> None:
    """Trials with no owner_server_id (legacy) should be resumed by any server."""
    store = FileSystemOrchestratorStore(tmp_path)
    spec = _make_spec("trial-legacy")

    status = TrialStatus(
        trial_id="trial-legacy",
        phase=TrialPhase.RUNNING,
        actors=(),
        metadata={},
        last_error=None,
    )
    record = TrialRecord(spec=spec, last_status=status)  # no owner
    store.upsert_trial_record(record)

    from dojozero.core._trial_orchestrator import TrialCheckpoint

    store.save_checkpoint(
        TrialCheckpoint(trial_id="trial-legacy", actor_states={}, checkpoint_id="cp1")
    )

    orchestrator = TrialOrchestrator(store=store)
    orchestrator.resume_trial = AsyncMock()

    manager = TrialManager(
        orchestrator=orchestrator,
        auto_resume=True,
        server_id="server-A",
    )

    count = await manager._resume_interrupted_trials()
    assert count == 1


# ---------------------------------------------------------------------------
# Active trial count notification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_active_trials() -> None:
    """_notify_active_trials pushes count to peer registry."""
    registry = StaticPeerRegistry(
        server_id="self",
        server_url="http://localhost:8000",
        peer_urls=[],
    )
    await registry.start()

    orchestrator = MagicMock()
    manager = TrialManager(
        orchestrator=orchestrator,
        server_id="self",
        peer_registry=registry,
    )

    # Simulate running tasks
    manager._running_tasks["t1"] = MagicMock()
    manager._running_tasks["t2"] = MagicMock()
    manager._notify_active_trials()

    # Give the fire-and-forget coroutine a chance to run
    await asyncio.sleep(0.1)

    peers = await registry.get_peers()
    assert peers[0].active_trials == 2

    await registry.stop()
