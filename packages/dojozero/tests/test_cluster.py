"""Tests for the multi-server cluster module."""

import asyncio
from pathlib import Path

import pytest

from dojozero.dashboard_server._cluster import (
    ClusterConfig,
    FileLeaderElector,
    StaticPeerRegistry,
    create_cluster,
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
