"""Cluster primitives for multi-server DojoZero deployments.

Provides leader election and peer discovery so that multiple Dashboard Server
instances can cooperate: one leader runs the ScheduleManager while all
instances accept trial submissions and serve gateway requests.

Two back-ends are supported for each primitive:

* **file** (dev): ``fcntl.flock`` on a shared filesystem path.
* **redis** (prod): ``SET NX EX`` for leader election, hash-based peer registry.

When no ``ClusterConfig`` is provided the server runs in standalone mode and
these primitives are not used.
"""

from __future__ import annotations

import asyncio
import fcntl
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
    leader_election: str = "file"  # "file" | "redis"
    discovery: str = "static"  # "static" | "redis"
    peers: list[str] = []  # static peer URLs (dev)
    redis_url: str | None = None  # for redis mode (prod)
    shared_store_path: str | None = None  # NFS path for file lock


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


class FileLeaderElector:
    """Leader election using ``fcntl.flock`` on a shared filesystem.

    Suitable for development clusters sharing an NFS / local directory.
    """

    def __init__(self, server_id: str, lock_path: str) -> None:
        self._server_id = server_id
        self._lock_path = lock_path
        self._is_leader = False
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._fd: Any | None = None

    async def start(self) -> None:
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
        self._release_lock()

    def is_leader(self) -> bool:
        return self._is_leader

    async def wait_for_leader(self) -> str:
        # In file-based election, we can only know about ourselves.
        while not self._is_leader and not self._stop_event.is_set():
            await asyncio.sleep(0.5)
        return self._server_id

    # -- internals --

    async def _election_loop(self) -> None:
        while not self._stop_event.is_set():
            acquired = await asyncio.get_event_loop().run_in_executor(
                None, self._try_acquire_lock
            )
            if acquired and not self._is_leader:
                self._is_leader = True
                logger.info("This server (%s) became leader", self._server_id)
            elif not acquired and self._is_leader:
                self._is_leader = False
                logger.info("This server (%s) lost leadership", self._server_id)
            await asyncio.sleep(5)

    def _try_acquire_lock(self) -> bool:
        try:
            if self._fd is None:
                self._fd = open(self._lock_path, "w")  # noqa: SIM115
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, IOError):
            return False

    def _release_lock(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                self._fd.close()
            except Exception:
                pass
            self._fd = None
        self._is_leader = False


class RedisLeaderElector:
    """Leader election using Redis ``SET NX EX`` with periodic renewal."""

    LOCK_KEY = "dojozero:leader"
    TTL_SECONDS = 15
    RENEW_INTERVAL = 5

    def __init__(self, server_id: str, redis_url: str) -> None:
        self._server_id = server_id
        self._redis_url = redis_url
        self._is_leader = False
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._redis: Any = None  # noqa: F821

    async def start(self) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(self._redis_url)
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
                    # Renew our lease
                    current = await self._redis.get(self.LOCK_KEY)
                    if current and current.decode() == self._server_id:
                        await self._redis.expire(self.LOCK_KEY, self.TTL_SECONDS)
                    else:
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


class StaticPeerRegistry:
    """Peer registry from a static list of URLs (dev/testing).

    In static mode we only know peer URLs, not their server IDs.
    We use ``server_url`` as the canonical key everywhere so that
    ``register_trial``, ``update_active_trials``, and ``get_peers``
    are all consistent.  A ``_url_for_id`` helper maps a server_id
    (like ``"server-1"``) to the corresponding URL when needed.
    """

    def __init__(self, server_id: str, server_url: str, peer_urls: list[str]) -> None:
        self._server_id = server_id
        self._server_url = server_url
        self._peer_urls = peer_urls
        self._trial_owners: dict[str, str] = {}  # trial_id -> server_url
        self._active_counts: dict[str, int] = {}  # server_url -> count
        self._game_claims: dict[str, str] = {}  # "{sport}:{game_id}" -> server_id

    async def start(self) -> None:
        logger.info(
            "Static peer registry started with %d peer(s)", len(self._peer_urls)
        )

    async def stop(self) -> None:
        pass

    def _url_for_id(self, server_id: str) -> str:
        """Resolve a server_id to a URL.

        If *server_id* matches our own id we return our URL.
        If it looks like a URL already (contains ``://``) return as-is.
        Otherwise fall back to returning it unchanged.
        """
        if server_id == self._server_id:
            return self._server_url
        # Already a URL (the common case for static peers)
        return server_id

    async def get_peers(self) -> list[PeerInfo]:
        peers = [
            PeerInfo(
                server_id=self._server_id,
                server_url=self._server_url,
                active_trials=self._active_counts.get(self._server_url, 0),
                last_heartbeat=time.time(),
            )
        ]
        for url in self._peer_urls:
            if url != self._server_url:
                peers.append(
                    PeerInfo(
                        server_id=url,
                        server_url=url,
                        active_trials=self._active_counts.get(url, 0),
                        last_heartbeat=time.time(),
                    )
                )
        return peers

    async def get_peer_for_trial(self, trial_id: str) -> PeerInfo | None:
        owner_url = self._trial_owners.get(trial_id)
        if owner_url is None:
            return None
        owner_id = self._server_id if owner_url == self._server_url else owner_url
        return PeerInfo(
            server_id=owner_id,
            server_url=owner_url,
            active_trials=self._active_counts.get(owner_url, 0),
        )

    async def register_trial(self, trial_id: str, server_id: str) -> None:
        self._trial_owners[trial_id] = self._url_for_id(server_id)

    async def update_active_trials(self, server_id: str, count: int) -> None:
        self._active_counts[self._url_for_id(server_id)] = count

    async def claim_game(
        self, sport_type: str, game_id: str, server_id: str
    ) -> bool:
        key = f"{sport_type}:{game_id}"
        existing = self._game_claims.get(key)
        if existing is None or existing == server_id:
            self._game_claims[key] = server_id
            return True
        return False

    async def is_game_claimed(self, sport_type: str, game_id: str) -> bool:
        return f"{sport_type}:{game_id}" in self._game_claims


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
            except Exception as e:
                logger.warning("Peer heartbeat error: %s", e)
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)

    async def claim_game(
        self, sport_type: str, game_id: str, server_id: str
    ) -> bool:
        """Atomically claim a game for scheduling. Returns True if claimed."""
        field_key = f"{sport_type}:{game_id}"
        # HSETNX: set only if the field does not exist (atomic)
        was_set = await self._redis.hsetnx(
            self.GAME_CLAIMS_KEY, field_key, server_id
        )
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

    Args:
        config: Cluster configuration.

    Returns:
        Tuple of (LeaderElector, PeerRegistry).
    """
    server_id = config.server_id or platform.node()
    server_url = config.server_url

    # Leader election
    if config.leader_election == "redis":
        if not config.redis_url:
            raise ValueError("redis_url required for redis leader election")
        elector: LeaderElector = RedisLeaderElector(server_id, config.redis_url)
    else:
        lock_path = config.shared_store_path or "/tmp/dojozero_leader.lock"
        elector = FileLeaderElector(server_id, lock_path)

    # Peer discovery
    if config.discovery == "redis":
        if not config.redis_url:
            raise ValueError("redis_url required for redis discovery")
        registry: PeerRegistry = RedisPeerRegistry(
            server_id, server_url, config.redis_url
        )
    else:
        registry = StaticPeerRegistry(server_id, server_url, config.peers)

    return elector, registry


__all__ = [
    "ClusterConfig",
    "FileLeaderElector",
    "LeaderElector",
    "PeerInfo",
    "PeerRegistry",
    "RedisLeaderElector",
    "RedisPeerRegistry",
    "StaticPeerRegistry",
    "create_cluster",
]
