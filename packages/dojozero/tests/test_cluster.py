"""Tests for the multi-server cluster module."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
    RedisLeaderElector,
    create_cluster,
)
from dojozero.dashboard_server._trial_manager import (
    TrialManager,
)


# ---------------------------------------------------------------------------
# RedisLeaderElector (with mocked Redis)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_leader_atomic_renewal() -> None:
    """Lua-based renewal should atomically check ownership and extend TTL."""
    elector = RedisLeaderElector(server_id="server-1", redis_url="redis://fake")

    # Mock the Redis client and Lua script
    mock_redis = AsyncMock()
    mock_script = AsyncMock()

    elector._redis = mock_redis
    elector._renew_script = mock_script
    elector._is_leader = True

    # Test the renewal path directly
    elector._is_leader = True
    mock_script.return_value = 1

    # Run one iteration of election loop
    elector._stop_event = asyncio.Event()

    async def run_one_iteration():
        """Run exactly one iteration of the election loop."""
        try:
            if elector._is_leader:
                renewed = await elector._renew_script(
                    keys=[elector.LOCK_KEY],
                    args=[elector._server_id, elector.TTL_SECONDS],
                )
                if not renewed:
                    elector._is_leader = False
        except Exception:
            pass

    # Successful renewal — stays leader
    mock_script.return_value = 1
    await run_one_iteration()
    assert elector._is_leader is True
    mock_script.assert_awaited_with(
        keys=[RedisLeaderElector.LOCK_KEY],
        args=["server-1", 30],
    )

    # Failed renewal (someone else owns the key) — loses leadership
    mock_script.return_value = 0
    await run_one_iteration()
    assert elector._is_leader is False


@pytest.mark.asyncio
async def test_redis_leader_ttl_is_30() -> None:
    """TTL should be 30 seconds (6 renewal chances at 5s interval)."""
    assert RedisLeaderElector.TTL_SECONDS == 30
    assert RedisLeaderElector.RENEW_INTERVAL == 5


@pytest.mark.asyncio
async def test_redis_leader_registers_lua_script() -> None:
    """start() should register the Lua renewal script."""
    elector = RedisLeaderElector(server_id="server-1", redis_url="redis://fake")

    mock_redis = MagicMock()
    mock_script = MagicMock()
    mock_redis.register_script.return_value = mock_script

    with patch("redis.asyncio.from_url", return_value=mock_redis):
        # Don't actually start the loop
        with patch.object(asyncio, "create_task"):
            await elector.start()

    mock_redis.register_script.assert_called_once()
    assert elector._renew_script is mock_script

    # Cleanup
    elector._stop_event.set()
    elector._is_leader = False
    elector._redis = None


# ---------------------------------------------------------------------------
# create_cluster factory
# ---------------------------------------------------------------------------


def test_create_cluster_redis_only() -> None:
    """create_cluster should always create Redis-backed instances."""
    config = ClusterConfig(
        server_id="test-server",
        server_url="http://localhost:8000",
        redis_url="redis://localhost:6379/0",
    )
    elector, registry = create_cluster(config)
    assert isinstance(elector, RedisLeaderElector)
    from dojozero.dashboard_server._cluster import RedisPeerRegistry

    assert isinstance(registry, RedisPeerRegistry)


def test_create_cluster_requires_redis_url() -> None:
    """create_cluster should raise if redis_url is empty."""
    config = ClusterConfig(
        server_id="test",
        server_url="http://localhost:8000",
        redis_url="",
    )
    with pytest.raises(ValueError, match="redis_url is required"):
        create_cluster(config)


def test_create_cluster_defaults_server_id() -> None:
    """server_id defaults to hostname when empty."""
    import platform

    config = ClusterConfig(
        server_id="",
        server_url="http://localhost:8000",
        redis_url="redis://localhost:6379/0",
    )
    elector, _registry = create_cluster(config)
    assert isinstance(elector, RedisLeaderElector)
    assert elector._server_id == platform.node()


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
    registry = AsyncMock()
    registry.update_active_trials = AsyncMock()
    registry.start = AsyncMock()

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

    registry.update_active_trials.assert_awaited_once_with("self", 2)


# ---------------------------------------------------------------------------
# Gateway reverse-proxy loop prevention
# ---------------------------------------------------------------------------


def test_gateway_router_no_proxy_when_forwarded() -> None:
    """Requests with X-Dojozero-Forwarded should not be re-proxied."""
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from dojozero.dashboard_server._gateway_routing import (
        GatewayRouter,
        create_gateway_router,
    )

    gw_router = GatewayRouter()

    # Create a mock peer registry that would return a peer
    mock_registry = AsyncMock()
    mock_peer = MagicMock()
    mock_peer.server_url = "http://localhost:8001"
    mock_registry.get_peer_for_trial = AsyncMock(return_value=mock_peer)

    app = FastAPI()
    router = create_gateway_router(gw_router, peer_registry=mock_registry)
    app.include_router(router)
    client = TestClient(app)

    # With forwarding header: should return 404 immediately, no proxy attempt
    resp = client.get(
        "/api/trials/nonexistent-trial/agents",
        headers={"X-Dojozero-Forwarded": "1"},
    )
    assert resp.status_code == 404
    # The peer registry should NOT have been consulted
    mock_registry.get_peer_for_trial.assert_not_called()


def test_gateway_router_proxies_without_forwarded_header() -> None:
    """Requests without X-Dojozero-Forwarded should attempt peer lookup."""
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from dojozero.dashboard_server._gateway_routing import (
        GatewayRouter,
        create_gateway_router,
    )

    gw_router = GatewayRouter()

    # Create a mock peer registry that returns None (no owner found)
    mock_registry = AsyncMock()
    mock_registry.get_peer_for_trial = AsyncMock(return_value=None)

    app = FastAPI()
    router = create_gateway_router(gw_router, peer_registry=mock_registry)
    app.include_router(router)
    client = TestClient(app)

    resp = client.get("/api/trials/nonexistent-trial/agents")
    assert resp.status_code == 404
    # The peer registry SHOULD have been consulted
    mock_registry.get_peer_for_trial.assert_called_once_with("nonexistent-trial")
