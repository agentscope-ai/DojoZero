"""Cluster primitives for multi-server DojoZero deployments.

Provides leader election and peer discovery so that multiple Dashboard Server
instances can cooperate: one leader runs the ScheduleManager while all
instances accept trial submissions and serve gateway requests.

Redis is the only supported back-end. ``--cluster-redis-url`` enables cluster
mode; standalone mode (no ``ClusterConfig``) needs no Redis.

When no ``ClusterConfig`` is provided the server runs in standalone mode and
these primitives are not used.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import time
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class ClusterConfig(BaseModel):
    """Cluster configuration for multi-server deployments."""

    server_id: str = ""
    server_url: str = ""
    redis_url: str = ""


class PeerInfo(BaseModel):
    """Information about a cluster peer."""

    server_id: str
    server_url: str
    active_trials: int = 0
    last_heartbeat: float = 0.0


# ---------------------------------------------------------------------------
# Leader Election Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class LeaderElector(Protocol):
    """Protocol for leader election implementations."""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def is_leader(self) -> bool: ...
    async def wait_for_leader(self) -> str:
        """Wait until a leader is known and return its server_id."""
        ...


class RedisLeaderElector:
    """Leader election using Redis ``SET NX EX`` with periodic renewal."""

    LOCK_KEY = "dojozero:leader"
    TTL_SECONDS = 30
    RENEW_INTERVAL = 5

    # Lua script for atomic check-and-renew: only extend TTL if we still own
    # the key.  This prevents the race where GET succeeds but the key expires
    # before the subsequent EXPIRE call.
    _RENEW_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""

    def __init__(self, server_id: str, redis_url: str) -> None:
        self._server_id = server_id
        self._redis_url = redis_url
        self._is_leader = False
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._redis: Any = None  # noqa: F821
        self._renew_script: Any = None

    async def start(self) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(self._redis_url)
        self._renew_script = self._redis.register_script(self._RENEW_LUA)
        self._stop_event.clear()
        self._task = asyncio.create_task(self._election_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Release leadership if held
        if self._is_leader and self._redis is not None:
            try:
                current = await self._redis.get(self.LOCK_KEY)
                if current and current.decode() == self._server_id:
                    await self._redis.delete(self.LOCK_KEY)
            except Exception:
                pass
        if self._redis is not None:
            await self._redis.aclose()
        self._is_leader = False

    def is_leader(self) -> bool:
        return self._is_leader

    async def wait_for_leader(self) -> str:
        while not self._stop_event.is_set():
            if self._is_leader:
                return self._server_id
            # Check who the current leader is
            if self._redis is not None:
                current = await self._redis.get(self.LOCK_KEY)
                if current:
                    return current.decode()
            await asyncio.sleep(1)
        return self._server_id

    async def _election_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self._is_leader:
                    # Atomically check ownership and extend TTL
                    renewed = await self._renew_script(
                        keys=[self.LOCK_KEY],
                        args=[self._server_id, self.TTL_SECONDS],
                    )
                    if not renewed:
                        self._is_leader = False
                        logger.info(
                            "Server %s lost leadership (key changed)",
                            self._server_id,
                        )
                else:
                    # Try to acquire
                    acquired = await self._redis.set(
                        self.LOCK_KEY,
                        self._server_id,
                        nx=True,
                        ex=self.TTL_SECONDS,
                    )
                    if acquired:
                        self._is_leader = True
                        logger.info(
                            "Server %s became leader via Redis", self._server_id
                        )
            except Exception as e:
                logger.warning("Leader election error: %s", e)
            await asyncio.sleep(self.RENEW_INTERVAL)


# ---------------------------------------------------------------------------
# Peer Registry Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class PeerRegistry(Protocol):
    """Protocol for peer discovery implementations."""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def get_peers(self) -> list[PeerInfo]: ...
    async def get_peer_for_trial(self, trial_id: str) -> PeerInfo | None: ...
    async def register_trial(self, trial_id: str, server_id: str) -> None: ...
    async def update_active_trials(self, server_id: str, count: int) -> None: ...
    async def claim_game(
        self, sport_type: str, game_id: str, server_id: str
    ) -> bool: ...
    async def is_game_claimed(self, sport_type: str, game_id: str) -> bool: ...
    async def is_peer_alive(self, server_id: str) -> bool:
        """Check if a peer has a recent heartbeat."""
        ...

    async def get_peer_staleness(self, server_id: str) -> float | None:
        """Seconds since last heartbeat, or None if peer is unknown."""
        ...


class RedisPeerRegistry:
    """Peer registry backed by Redis with TTL-based heartbeats."""

    PEERS_KEY = "dojozero:peers"
    TRIALS_KEY = "dojozero:trial_owners"
    GAME_CLAIMS_KEY = "dojozero:game_claims"
    HEARTBEAT_INTERVAL = 10
    PEER_TTL = 30

    def __init__(self, server_id: str, server_url: str, redis_url: str) -> None:
        self._server_id = server_id
        self._server_url = server_url
        self._redis_url = redis_url
        self._redis: Any = None  # noqa: F821
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._active_trials = 0

    async def start(self) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(self._redis_url)
        self._stop_event.clear()
        # Register ourselves immediately
        await self._heartbeat()
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info("Redis peer registry started for %s", self._server_id)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Deregister
        if self._redis is not None:
            try:
                await self._redis.hdel(self.PEERS_KEY, self._server_id)
            except Exception:
                pass
            await self._redis.aclose()

    async def get_peers(self) -> list[PeerInfo]:
        import json

        peers: list[PeerInfo] = []
        now = time.time()
        all_peers = await self._redis.hgetall(self.PEERS_KEY)
        for _sid, data in all_peers.items():
            sid = _sid.decode() if isinstance(_sid, bytes) else _sid
            try:
                info = json.loads(data)
                if now - info.get("last_heartbeat", 0) > self.PEER_TTL:
                    continue  # stale peer
                peers.append(
                    PeerInfo(
                        server_id=sid,
                        server_url=info["server_url"],
                        active_trials=info.get("active_trials", 0),
                        last_heartbeat=info.get("last_heartbeat", 0),
                    )
                )
            except Exception:
                continue
        return peers

    async def get_peer_for_trial(self, trial_id: str) -> PeerInfo | None:
        owner_data = await self._redis.hget(self.TRIALS_KEY, trial_id)
        if owner_data is None:
            return None
        import json

        try:
            info = json.loads(owner_data)
            return PeerInfo(
                server_id=info["server_id"],
                server_url=info["server_url"],
            )
        except Exception:
            return None

    async def register_trial(self, trial_id: str, server_id: str) -> None:
        import json

        # Look up the server URL from peers hash
        peer_data = await self._redis.hget(self.PEERS_KEY, server_id)
        if peer_data:
            info = json.loads(peer_data)
            server_url = info["server_url"]
        elif server_id == self._server_id:
            server_url = self._server_url
        else:
            server_url = ""

        await self._redis.hset(
            self.TRIALS_KEY,
            trial_id,
            json.dumps({"server_id": server_id, "server_url": server_url}),
        )

    async def update_active_trials(self, server_id: str, count: int) -> None:
        if server_id == self._server_id:
            self._active_trials = count
            await self._heartbeat()

    async def is_peer_alive(self, server_id: str) -> bool:
        """Check if a peer has heartbeated within PEER_TTL."""
        staleness = await self.get_peer_staleness(server_id)
        return staleness is not None and staleness <= self.PEER_TTL

    async def get_peer_staleness(self, server_id: str) -> float | None:
        """Seconds since last heartbeat, or None if peer is unknown."""
        import json

        raw = await self._redis.hget(self.PEERS_KEY, server_id)
        if raw is None:
            return None
        try:
            info = json.loads(raw)
            return time.time() - info.get("last_heartbeat", 0)
        except Exception:
            return None

    async def _heartbeat(self) -> None:
        import json

        await self._redis.hset(
            self.PEERS_KEY,
            self._server_id,
            json.dumps(
                {
                    "server_url": self._server_url,
                    "active_trials": self._active_trials,
                    "last_heartbeat": time.time(),
                }
            ),
        )

    async def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._heartbeat()
                await self._prune_stale_peers()
            except Exception as e:
                logger.warning("Peer heartbeat error: %s", e)
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)

    async def _prune_stale_peers(self) -> None:
        """Remove peers whose heartbeat has long expired from Redis.

        Uses 2× PEER_TTL so a temporarily unavailable peer has time to
        recover before its entry is deleted.  ``get_peers()`` already
        excludes stale peers from scheduling decisions at 1× PEER_TTL.
        """
        import json

        prune_threshold = self.PEER_TTL * 2
        now = time.time()
        all_peers = await self._redis.hgetall(self.PEERS_KEY)
        stale = []
        for _sid, data in all_peers.items():
            sid = _sid.decode() if isinstance(_sid, bytes) else _sid
            if sid == self._server_id:
                continue
            try:
                info = json.loads(data)
                if now - info.get("last_heartbeat", 0) > prune_threshold:
                    stale.append(sid)
            except Exception:
                stale.append(sid)
        if stale:
            await self._redis.hdel(self.PEERS_KEY, *stale)
            logger.info("Pruned %d stale peer(s): %s", len(stale), stale)

    async def claim_game(self, sport_type: str, game_id: str, server_id: str) -> bool:
        """Atomically claim a game for scheduling. Returns True if claimed."""
        field_key = f"{sport_type}:{game_id}"
        # HSETNX: set only if the field does not exist (atomic)
        was_set = await self._redis.hsetnx(self.GAME_CLAIMS_KEY, field_key, server_id)
        if was_set:
            return True
        # Field already exists — check if we already own it
        existing = await self._redis.hget(self.GAME_CLAIMS_KEY, field_key)
        if existing is not None:
            owner = existing.decode() if isinstance(existing, bytes) else existing
            return owner == server_id
        return False

    async def is_game_claimed(self, sport_type: str, game_id: str) -> bool:
        field_key = f"{sport_type}:{game_id}"
        return bool(await self._redis.hexists(self.GAME_CLAIMS_KEY, field_key))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_cluster(
    config: ClusterConfig,
) -> tuple[LeaderElector, PeerRegistry]:
    """Create leader elector and peer registry from configuration.

    Requires ``redis_url`` to be set.

    Args:
        config: Cluster configuration.

    Returns:
        Tuple of (LeaderElector, PeerRegistry).

    Raises:
        ValueError: If ``redis_url`` is not set.
    """
    if not config.redis_url:
        raise ValueError("redis_url is required for cluster mode")

    server_id = config.server_id or platform.node()
    server_url = config.server_url

    elector: LeaderElector = RedisLeaderElector(server_id, config.redis_url)
    registry: PeerRegistry = RedisPeerRegistry(server_id, server_url, config.redis_url)

    return elector, registry


__all__ = [
    "ClusterConfig",
    "LeaderElector",
    "PeerInfo",
    "PeerRegistry",
    "RedisLeaderElector",
    "RedisPeerRegistry",
    "create_cluster",
]
