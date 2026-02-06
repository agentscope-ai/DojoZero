"""Store Factory: Generic infrastructure for creating domain-specific data stores.

This module provides a registry-based factory pattern for creating DataStore instances.
Each domain (NBA, NFL, WebSearch, etc.) registers a factory that knows how to create
its specific store type with proper configuration.

Usage:
    # Register a factory (done in domain modules)
    @register_store_factory("nba")
    class NBAStoreFactory(StoreFactory):
        def create_store(self, store_id, metadata, hub):
            ...

    # Build actor context (in trial builders)
    context = build_runtime_context(
        trial_id=spec.trial_id,
        hub_id=hub_id,
        metadata=metadata,
        store_types=["nba", "websearch", "polymarket"],
    )
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable

from dojozero.betting._metadata import BettingTrialMetadata
from dojozero.core._types import RuntimeContext
from dojozero.data._hub import DataHub
from dojozero.data._stores import DataStore

logger = logging.getLogger(__name__)


class StoreFactory(ABC):
    """Abstract factory for creating domain-specific DataStore instances.

    Each domain (NBA, NFL, WebSearch, Polymarket) should implement this interface
    to create its specific store type with proper configuration.

    The metadata parameter is typed as BettingTrialMetadata, providing type-safe
    access to trial configuration fields.
    """

    @abstractmethod
    def create_store(
        self,
        store_id: str,
        metadata: BettingTrialMetadata,
        hub: DataHub,
    ) -> DataStore:
        """Create and configure a DataStore instance.

        Args:
            store_id: Unique identifier for the store
            metadata: Typed trial metadata containing domain-specific config
                     (e.g., espn_game_id, market_url, home_tricode)
            hub: DataHub to connect the store to

        Returns:
            Configured DataStore instance (already connected to hub)
        """
        ...


# Global registry of store factories
_STORE_FACTORIES: dict[str, StoreFactory] = {}


def register_store_factory(name: str) -> Any:
    """Decorator to register a store factory class.

    Args:
        name: Factory name (e.g., "nba", "nfl", "websearch")

    Returns:
        Decorator function

    Example:
        @register_store_factory("nba")
        class NBAStoreFactory(StoreFactory):
            def create_store(self, store_id, metadata, hub):
                ...
    """

    def decorator(cls: type[StoreFactory]) -> type[StoreFactory]:
        factory_instance = cls()
        _STORE_FACTORIES[name] = factory_instance
        logger.debug("Registered store factory: %s", name)
        return cls

    return decorator


def get_store_factory(name: str) -> StoreFactory | None:
    """Get a registered store factory by name.

    Args:
        name: Factory name

    Returns:
        StoreFactory instance or None if not found
    """
    return _STORE_FACTORIES.get(name)


def list_store_factories() -> list[str]:
    """List all registered store factory names.

    Returns:
        List of factory names
    """
    return list(_STORE_FACTORIES.keys())


def build_runtime_context(
    trial_id: str,
    hub_id: str,
    persistence_file: str,
    metadata: BettingTrialMetadata,
    store_types: list[str],
    sport_type: str = "",
) -> RuntimeContext:
    """Build RuntimeContext with DataHub and stores using registered factories.

    This is a generic context builder that creates a DataHub and populates it
    with stores based on the requested store types. Each store type must have
    a registered factory.

    Args:
        trial_id: Trial identifier for the context
        hub_id: Unique identifier for the DataHub
        persistence_file: Path to persistence file (required)
        metadata: Typed trial metadata passed to store factories
        store_types: List of store type names to create (e.g., ["nba", "websearch"])
        sport_type: Sport type identifier (e.g., "nba", "nfl")

    Returns:
        RuntimeContext with trial_id, data_hubs, stores, startup, and cleanup callbacks

    Raises:
        ValueError: If a requested store type has no registered factory
    """
    data_hubs: dict[str, DataHub] = {}
    stores: dict[str, DataStore] = {}

    # Create DataHub with trial_id for trace emission
    hub = DataHub(
        hub_id=hub_id,
        persistence_file=persistence_file,
        trial_id=trial_id,
    )
    data_hubs[hub_id] = hub

    # Create stores using registered factories
    for store_type in store_types:
        factory = get_store_factory(store_type)
        if factory is None:
            raise ValueError(
                f"No store factory registered for type '{store_type}'. "
                f"Available factories: {list_store_factories()}"
            )

        # Create store with standard naming: {type}_store
        store_id = f"{store_type}_store"
        try:
            store = factory.create_store(store_id, metadata, hub)
            stores[store_id] = store
            logger.debug("Created store: %s (type: %s)", store_id, store_type)
        except Exception as e:
            logger.error("Failed to create store '%s': %s", store_type, e)
            raise

    # Self-stop mechanism: request trial stop when game ends
    @dataclass
    class _StopState:
        """Mutable container for stop mechanism state."""

        requested: bool = False
        callback: Callable[[], None] | None = None

    stop_state = _StopState()

    def _request_stop() -> None:
        if stop_state.requested:
            return
        stop_state.requested = True
        logger.info(
            "Trial '%s' self-stop requested (GameResultEvent received)", trial_id
        )
        # Call the wrapped callback if orchestrator has set one
        if stop_state.callback is not None:
            stop_state.callback()

    def _set_stop_callback(callback: Callable[[], None]) -> None:
        """Allow orchestrator to inject its wrapped stop callback."""
        stop_state.callback = callback

    hub.subscribe_agent(
        agent_id=f"_trial_self_stop_{trial_id}",
        event_types=["event.game_result"],
        callback=lambda _event: _request_stop(),
    )

    # Create startup callback
    async def start_data_stores() -> None:
        """Start all DataHub stores (begin polling)."""
        await hub.start()

    # Create cleanup callback
    async def stop_data_stores() -> None:
        """Stop all DataHub stores (stop polling and close sessions)."""
        await hub.stop()

    return RuntimeContext(
        trial_id=trial_id,
        sport_type=sport_type,
        data_hubs=data_hubs,
        stores=stores,
        startup=start_data_stores,
        cleanup=stop_data_stores,
        request_stop=_request_stop,
        set_stop_callback=_set_stop_callback,
    )


__all__ = [
    "StoreFactory",
    "register_store_factory",
    "get_store_factory",
    "list_store_factories",
    "build_runtime_context",
]
