"""NBA Pre-Game Betting scenario demonstrating DataHub integration with AgentX.

This sample shows how to integrate the DataHub/DataStore system with the AgentX
trial framework. It demonstrates:

1. DataHub setup with WebSearchStore and processors
2. DataHubDataStream that bridges DataHub events to StreamEvent system
3. Agents that consume StreamEvent[DataEvent] from DataHub
4. Trial builder that wires everything together

Usage:
    Create a params file (e.g., nba_pregame.yaml):
    
    ```yaml
    scenario:
      name: samples.nba-pregame-betting
      config:
        hub_id: nba_pregame_hub
        persistence_file: outputs/nba_pregame_events.jsonl
        stream_ids:
          - raw_web_search
          - injury_summary
          - power_ranking
          - expert_prediction
        agent_ids:
          - betting_agent
    ```
    
    Then run:
    ```bash
    agentx run --params nba_pregame.yaml --trial-id nba-trial
    ```

How metadata flows to queries and processors:

1. **Trial Builder** (`_build_trial_spec`):
   - Calls `get_game_info_by_id(game_id)` to get team tricodes, names, and date
   - Stores this metadata in the operator config

2. **DataHubDataStream** (for `raw_web_search` stream):
   - On `start()`, triggers initial web searches to bootstrap the event chain
   - Follows the `bounded_random` pattern: streams drive the chain by generating events
   - Generates queries with team info embedded:
     - "NBA injury updates for Lakers vs Spurs on 2025-01-15"
     - "NBA power rankings"
     - "NBA expert predictions for Lakers vs Spurs on 2025-01-15"
   - Calls `store.search(query, intent=...)` to trigger searches
   - This drives the entire event chain: searches → raw events → processors → processed events → agents

3. **Event Counter Operator** (`EventCounterOperator`):
   - Tracks total number of events processed across all agents
   - Similar to `CounterOperator` in `bounded_random` sample
   - Agents call `operator.count()` for each event they process

4. **Agent** (`NBAPreGameBettingAgent`):
   - Receives processed events via streams
   - Processes events and can trigger additional searches via operator if needed

5. **WebSearchStore** (`store.search()`):
   - Executes the query via Tavily API
   - Creates `RawWebSearchEvent` with the query string (which contains team info)
   - Emits raw event to DataHub

6. **Processors** (InjurySummaryProcessor, PowerRankingProcessor, etc.):
   - Receive `RawWebSearchEvent` with `query` attribute
   - Can extract team info from query string if needed
   - Process results and emit processed events (InjurySummaryEvent, etc.)
   - Events flow back through DataHub and are forwarded by DataHubDataStream to agents
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, TypedDict, cast

from pydantic import BaseModel, Field

from agentx.core import (
    Agent,
    AgentBase,
    AgentSpec,
    DataStream,
    DataStreamBase,
    DataStreamSpec,
    Operator,
    OperatorBase,
    OperatorSpec,
    register_trial_builder,
    StreamEvent,
    TrialSpec,
)
from agentx.data import DataHub, WebSearchAPI, WebSearchStore
from agentx.data._models import DataEvent
from agentx.data.nba._utils import get_game_info_by_id
from agentx.data.websearch._events import WebSearchIntent
from agentx.data.websearch._processors import (
    ExpertPredictionProcessor,
    InjurySummaryProcessor,
    PowerRankingProcessor,
)

LOGGER = logging.getLogger("agentx.samples.nba_pregame_betting")


class _ActorIdConfig(TypedDict):
    actor_id: str


class DataHubDataStreamConfig(_ActorIdConfig, total=False):
    hub_id: str
    persistence_file: str
    stream_id: str  # Which stream_id to subscribe to in DataHub
    event_types: list[str]  # Which event_types to subscribe to (alternative to stream_id)
    websearch_store_id: str  # Store ID for triggering searches (only for raw_web_search stream)
    home_team_tricode: str  # Team metadata for generating queries
    away_team_tricode: str
    home_team_name: str
    away_team_name: str
    game_date: str


class NBAPreGameBettingAgentConfig(_ActorIdConfig, total=False):
    operator_id: str  # ID of the event counter operator to use


class EventCounterOperatorConfig(_ActorIdConfig):
    """Configuration for event counter operator."""
    pass


class NBAPreGameBettingTrialParams(BaseModel):
    """Trial parameters for NBA pre-game betting scenario."""

    # NBA game configuration
    game_id: str = Field(..., description="NBA.com game ID (e.g., '0022500290')")

    # DataHub configuration
    hub_id: str = Field(default="nba_pregame_hub")
    persistence_file: str = Field(default="outputs/nba_pregame_events.jsonl")
    enable_persistence: bool = Field(default=True)

    # Store configuration
    websearch_store_id: str = Field(default="websearch_store")
    poll_interval_seconds: float = Field(default=30.0)

    # Stream configuration
    stream_ids: list[str] = Field(
        default_factory=lambda: [
            "raw_web_search",
            "injury_summary",
            "power_ranking",
            "expert_prediction",
        ]
    )

    # Agent configuration
    agent_ids: list[str] = Field(default_factory=lambda: ["betting_agent"])

    # Search queries (optional, for triggering searches)
    # If not provided, will be auto-generated based on game_id
    search_queries: list[dict[str, Any]] = Field(default_factory=list)


class DataHubDataStream(
    DataStreamBase, DataStream[DataHubDataStreamConfig]
):
    """DataStream that bridges DataHub events to StreamEvent system.

    Subscribes to DataHub events and converts them to StreamEvent[DataEvent]
    for consumption by agents.
    """

    def __init__(
        self,
        *,
        actor_id: str,
        hub: DataHub | None = None,
        stream_id: str | None = None,
        event_types: list[str] | None = None,
        store: WebSearchStore | None = None,
        home_team_tricode: str | None = None,
        away_team_tricode: str | None = None,
        home_team_name: str | None = None,
        away_team_name: str | None = None,
        game_date: str | None = None,
    ) -> None:
        super().__init__(actor_id)
        self._hub = hub
        self._stream_id = stream_id
        self._event_types = event_types or []
        self._store = store
        self._home_team_tricode = home_team_tricode
        self._away_team_tricode = away_team_tricode
        self._home_team_name = home_team_name
        self._away_team_name = away_team_name
        self._game_date = game_date
        self._sequence = 0
        self._event_callback: Any | None = None
        self._received_events: list[DataEvent] = []
        self._searches_triggered = False

    @classmethod
    def from_dict(
        cls,
        config: DataHubDataStreamConfig,
    ) -> "DataHubDataStream":
        # Get hub from registry (set by trial builder)
        hub: DataHub | None = None
        store: WebSearchStore | None = None
        
        hub_registry: dict[str, DataHub] = getattr(cls, "_hub_registry", {})
        store_registry: dict[str, WebSearchStore] = getattr(cls, "_store_registry", {})
        
        if hub_registry:
            hub_id = config.get("hub_id", "default_hub")
            hub = hub_registry.get(hub_id)
        
        if store_registry:
            store_id = config.get("websearch_store_id")
            if store_id:
                store = store_registry.get(store_id)

        if hub is None:
            # Fallback: create new hub (shouldn't happen in normal flow)
            hub_id = config.get("hub_id", "default_hub")
            persistence_file = config.get("persistence_file", "outputs/events.jsonl")
            hub = DataHub(hub_id=hub_id, persistence_file=persistence_file)

        return cls(
            actor_id=config["actor_id"],
            hub=hub,
            stream_id=config.get("stream_id"),
            event_types=config.get("event_types", []),
            store=store,
            home_team_tricode=config.get("home_team_tricode"),
            away_team_tricode=config.get("away_team_tricode"),
            home_team_name=config.get("home_team_name"),
            away_team_name=config.get("away_team_name"),
            game_date=config.get("game_date"),
        )

    async def start(self) -> None:
        """Protocol hook: subscribe to DataHub events and trigger searches if needed.
        
        For raw_web_search stream, this triggers the initial searches to drive the chain,
        following the pattern from bounded_random where streams generate events on start.
        """
        if self._hub is None:
            raise RuntimeError(f"stream '{self.actor_id}' has no DataHub instance")

        LOGGER.info(
            "stream '%s' starting: stream_id=%s event_types=%s",
            self.actor_id,
            self._stream_id,
            self._event_types,
        )

        # Trigger searches for raw_web_search stream (drives the whole chain)
        if self._stream_id == "raw_web_search" and self._store and not self._searches_triggered:
            await self._trigger_initial_searches()
            self._searches_triggered = True

        # Subscribe to DataHub events using callback mechanism
        def event_callback(event: DataEvent) -> None:
            # Check if this event matches our subscription
            should_forward = False
            if self._stream_id and event.event_type == self._stream_id:
                should_forward = True
            elif self._event_types and event.event_type in self._event_types:
                should_forward = True

            if should_forward:
                self._received_events.append(event)
                # Schedule async publish
                asyncio.create_task(self._publish_event(event))

        self._event_callback = event_callback

        # Determine event types to subscribe to
        subscribe_event_types = self._event_types.copy()
        if self._stream_id and self._stream_id not in subscribe_event_types:
            subscribe_event_types.append(self._stream_id)

        # Subscribe to DataHub using subscribe_agent mechanism
        # We use the stream's actor_id as the agent_id for subscription
        for event_type in subscribe_event_types:
            self._hub.subscribe_agent(
                agent_id=self.actor_id,
                event_types=[event_type],
                callback=event_callback,
            )

    async def _trigger_initial_searches(self) -> None:
        """Trigger initial web searches to bootstrap the event chain."""
        if self._store is None:
            return
        
        # Build team context for queries
        teams_str = ""
        if self._home_team_name and self._away_team_name:
            teams_str = f"{self._away_team_name} vs {self._home_team_name}"
        elif self._home_team_tricode and self._away_team_tricode:
            teams_str = f"{self._away_team_tricode} @ {self._home_team_tricode}"
        
        date_str = ""
        if self._game_date:
            date_str = f" on {self._game_date}"
        
        # Generate queries with team info embedded
        queries = []
        
        # Injury report query
        if teams_str:
            injury_query = f"NBA injury updates for {teams_str}{date_str}"
            queries.append((injury_query, WebSearchIntent.INJURY_SUMMARY))
        else:
            queries.append(("NBA injury updates", WebSearchIntent.INJURY_SUMMARY))
        
        # Power ranking query
        queries.append(("NBA power rankings", WebSearchIntent.POWER_RANKING))
        
        # Expert prediction query
        if teams_str:
            prediction_query = f"NBA expert predictions for {teams_str}{date_str}"
            queries.append((prediction_query, WebSearchIntent.EXPERT_PREDICTION))
        else:
            queries.append(("NBA expert predictions", WebSearchIntent.EXPERT_PREDICTION))
        
        # Execute searches
        LOGGER.info("stream '%s' triggering initial searches to bootstrap event chain", self.actor_id)
        for query, intent in queries:
            try:
                LOGGER.info("stream '%s' searching: '%s' (intent: %s)", self.actor_id, query, intent)
                await self._store.search(query, intent=intent)
            except Exception as e:
                LOGGER.error(
                    "stream '%s' failed to search '%s': %s",
                    self.actor_id,
                    query,
                    e,
                    exc_info=True,
                )

    async def _publish_event(self, event: DataEvent) -> None:
        """Publish a DataEvent as a StreamEvent."""
        self._sequence += 1
        stream_event = StreamEvent(
            stream_id=self.actor_id,
            payload=event,
            sequence=self._sequence,
            emitted_at=event.timestamp,
        )
        await self._publish(stream_event)

    async def stop(self) -> None:
        """Protocol hook: unsubscribe from DataHub."""
        LOGGER.info(
            "stream '%s' stopping after %d events",
            self.actor_id,
            len(self._received_events),
        )
        # Unsubscribe from DataHub
        if self._hub:
            self._hub.unsubscribe_agent(self.actor_id)

    async def save_state(self) -> Mapping[str, Any]:
        """Protocol hook: dashboard snapshot for checkpoints."""
        return {
            "sequence": self._sequence,
            "received_events": len(self._received_events),
            "searches_triggered": self._searches_triggered,
        }

    async def load_state(self, state: Mapping[str, Any]) -> None:
        """Protocol hook: dashboard restores a checkpoint before resuming."""
        self._sequence = int(state.get("sequence", 0))
        self._searches_triggered = bool(state.get("searches_triggered", False))
        LOGGER.info(
            "stream '%s' restored: sequence=%d searches_triggered=%s",
            self.actor_id,
            self._sequence,
            self._searches_triggered,
        )

    @property
    def hub(self) -> DataHub:
        """Get the underlying DataHub instance."""
        if self._hub is None:
            raise RuntimeError(f"stream '{self.actor_id}' has no DataHub instance")
        return self._hub


class EventCounterOperator(OperatorBase, Operator[EventCounterOperatorConfig]):
    """Operator that counts events processed by agents.
    
    Similar to CounterOperator in bounded_random, this operator maintains
    a shared count of events that agents can increment.
    """

    def __init__(self, actor_id: str) -> None:
        super().__init__(actor_id)
        self._count = 0
        self._lock = asyncio.Lock()

    @classmethod
    def from_dict(
        cls,
        config: EventCounterOperatorConfig,
    ) -> "EventCounterOperator":
        return cls(actor_id=str(config["actor_id"]))

    async def start(self) -> None:
        """Protocol hook: dashboard calls this before traffic is routed."""
        LOGGER.info("operator '%s' starting", self.actor_id)

    async def stop(self) -> None:
        """Protocol hook: dashboard calls this during shutdown."""
        LOGGER.info("operator '%s' stopping at count=%d", self.actor_id, self._count)

    async def handle_stream_event(
        self, event: StreamEvent[Any]
    ) -> None:  # pragma: no cover - not used
        """Protocol hook: dashboard forwards stream payloads here when routed."""
        del event

    async def save_state(self) -> Mapping[str, Any]:
        """Protocol hook: dashboard snapshot for checkpoints."""
        async with self._lock:
            return {"count": self._count}

    async def load_state(self, state: Mapping[str, Any]) -> None:
        """Protocol hook: dashboard restores operator state on resume."""
        async with self._lock:
            self._count = int(state.get("count", 0))
            LOGGER.info(
                "operator '%s' restored to count=%d", self.actor_id, self._count
            )

    async def count(self) -> int:
        """RPC method: increment and return the event count.
        
        Called by agents for each event they process.
        
        Returns:
            The new count after incrementing
        """
        async with self._lock:
            self._count += 1
            LOGGER.info(
                "operator '%s' incremented count to %d", self.actor_id, self._count
            )
            return self._count

    @property
    def value(self) -> int:
        """Get the current count value."""
        return self._count


class _EventCounterOperatorLike(Operator[EventCounterOperatorConfig], Protocol):
    """Protocol for event counter operator - only the RPC surface is required."""
    async def count(self) -> int: ...


class NBAPreGameBettingAgent(AgentBase, Agent[NBAPreGameBettingAgentConfig]):
    """Agent that processes NBA pre-game betting data from DataHub streams.
    
    This agent processes events from DataHub streams and uses a counter operator
    to track the total number of events processed across all agents.
    """

    def __init__(self, actor_id: str, operator_id: str | None = None) -> None:
        super().__init__(actor_id)
        self._operator_id = operator_id
        self._operator: _EventCounterOperatorLike | None = None
        self._events_processed = 0
        self._received_events: list[DataEvent] = []
        self._observed_counts: list[int] = []

    @classmethod
    def from_dict(
        cls,
        config: NBAPreGameBettingAgentConfig,
    ) -> "NBAPreGameBettingAgent":
        return cls(
            actor_id=str(config["actor_id"]),
            operator_id=config.get("operator_id"),
        )

    def register_operators(self, operators: Sequence[Operator]) -> None:
        """Register operators that the agent can reach."""
        super().register_operators(operators)
        
        # Find and register the counter operator if available
        if self._operator_id and operators:
            for operator in operators:
                if operator.actor_id == self._operator_id:
                    if not hasattr(operator, "count"):
                        raise TypeError(
                            f"operator '{operator.actor_id}' must expose a 'count' coroutine"
                        )
                    self._operator = cast(_EventCounterOperatorLike, operator)
                    LOGGER.info(
                        "agent '%s' registered operator '%s'",
                        self.actor_id,
                        self._operator_id,
                    )
                    break

    async def start(self) -> None:
        """Protocol hook: dashboard calls this before events are dispatched."""
        LOGGER.info("agent '%s' starting", self.actor_id)

    async def stop(self) -> None:
        """Protocol hook: dashboard calls this when the trial is stopping."""
        LOGGER.info(
            "agent '%s' stopping after %d events",
            self.actor_id,
            self._events_processed,
        )

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        """Protocol hook: dashboard forwards each stream event to subscribed agents."""
        data_event: DataEvent = event.payload
        self._received_events.append(data_event)
        self._events_processed += 1

        # Increment shared counter via operator (like CounterAgent in bounded_random)
        operator_count = None
        if self._operator:
            try:
                operator_count = await self._operator.count()
                self._observed_counts.append(operator_count)
            except Exception as e:
                LOGGER.warning(
                    "agent '%s' failed to increment counter: %s",
                    self.actor_id,
                    e,
                )

        event_type = data_event.event_type
        LOGGER.info(
            "agent '%s' handled event seq=%s type=%s operator_count=%s",
            self.actor_id,
            event.sequence,
            event_type,
            operator_count,
        )

        # Process different event types
        if event_type == "raw_web_search":
            # Type checker workaround
            web_event = cast(Any, data_event)
            query = getattr(web_event, "query", "")
            results = getattr(web_event, "results", [])
            LOGGER.info(
                "  Raw search: query='%s' results=%d",
                query,
                len(results) if isinstance(results, list) else 0,
            )
        elif event_type == "injury_summary":
            injury_event = cast(Any, data_event)
            injured_players = getattr(injury_event, "injured_players", {})
            teams = list(injured_players.keys()) if injured_players else []
            total_players = (
                sum(len(p) for p in injured_players.values())
                if injured_players
                else 0
            )
            LOGGER.info(
                "  Injury summary: %d teams, %d players",
                len(teams),
                total_players,
            )
        elif event_type == "power_ranking":
            ranking_event = cast(Any, data_event)
            rankings = getattr(ranking_event, "rankings", {})
            sources = list(rankings.keys()) if rankings else []
            total_teams = (
                sum(len(teams) for teams in rankings.values()) if rankings else 0
            )
            LOGGER.info(
                "  Power rankings: %d sources, %d teams",
                len(sources),
                total_teams,
            )
        elif event_type == "expert_prediction":
            prediction_event = cast(Any, data_event)
            predictions = getattr(prediction_event, "predictions", [])
            LOGGER.info("  Expert predictions: %d predictions", len(predictions))

    async def save_state(self) -> Mapping[str, Any]:
        """Protocol hook: dashboard snapshot for checkpoints."""
        return {
            "events_processed": self._events_processed,
            "received_events_count": len(self._received_events),
            "observed_counts": list(self._observed_counts),
        }

    async def load_state(self, state: Mapping[str, Any]) -> None:
        """Protocol hook: dashboard restores agent state before resuming."""
        self._events_processed = int(state.get("events_processed", 0))
        self._observed_counts = [
            int(value) for value in state.get("observed_counts", [])
        ]
        LOGGER.info(
            "agent '%s' restored: events=%d observed_counts=%d",
            self.actor_id,
            self._events_processed,
            len(self._observed_counts),
        )

    @property
    def events_processed(self) -> int:
        return self._events_processed


def _build_trial_spec(
    trial_id: str,
    params: NBAPreGameBettingTrialParams,
) -> TrialSpec:
    """Return a :class:`TrialSpec` that wires DataHub, streams, and agents together."""

    # Get game information from game_id to extract team tricodes
    game_info = get_game_info_by_id(params.game_id)
    home_team_tricode: str | None = None
    away_team_tricode: str | None = None
    home_team_name: str | None = None
    away_team_name: str | None = None
    game_date: str | None = None

    if game_info:
        home_team_tricode = game_info.get("home_team_tricode")
        away_team_tricode = game_info.get("away_team_tricode")
        home_team_name = game_info.get("home_team")
        away_team_name = game_info.get("away_team")
        game_date = game_info.get("game_date")
        LOGGER.info(
            "Found game info: %s @ %s on %s",
            f"{away_team_tricode} @ {home_team_tricode}",
            f"{away_team_name} @ {home_team_name}",
            game_date,
        )
    else:
        LOGGER.error(
            "Could not find game info for game_id=%s. Exiting.",
            params.game_id,
        )
        raise ValueError(f"Could not find game info for game_id={params.game_id}.")

    # Create DataHub instance
    hub = DataHub(
        hub_id=params.hub_id,
        persistence_file=params.persistence_file,
        enable_persistence=params.enable_persistence,
    )

    # Setup WebSearchStore
    api = WebSearchAPI()
    store = WebSearchStore(
        store_id=params.websearch_store_id,
        api=api
    )

    # Register processors
    store.register_stream("injury_summary", InjurySummaryProcessor(), ["raw_web_search"])
    store.register_stream("power_ranking", PowerRankingProcessor(), ["raw_web_search"])
    store.register_stream(
        "expert_prediction", ExpertPredictionProcessor(), ["raw_web_search"]
    )

    # Connect store to DataHub
    hub.connect_store(store)

    # Create stream specs - one per stream_id
    stream_specs = []
    for stream_id in params.stream_ids:
        # Determine event_types for this stream
        if stream_id == "raw_web_search":
            event_types = ["raw_web_search"]
        else:
            # Processed streams produce events with matching event_type
            event_types = [stream_id]

        stream_config: DataHubDataStreamConfig = {
            "actor_id": f"{stream_id}_stream",
            "hub_id": params.hub_id,
            "persistence_file": params.persistence_file,
            "stream_id": stream_id,
            "event_types": event_types,
        }

        # Store hub and store references in registry so from_dict can access them
        # This is a workaround - in production, hub would be in runtime context
        if not hasattr(DataHubDataStream, "_hub_registry"):
            setattr(DataHubDataStream, "_hub_registry", {})
        if not hasattr(DataHubDataStream, "_store_registry"):
            setattr(DataHubDataStream, "_store_registry", {})
        
        stream_hub_registry: dict[str, DataHub] = getattr(DataHubDataStream, "_hub_registry", {})
        stream_store_registry: dict[str, WebSearchStore] = getattr(DataHubDataStream, "_store_registry", {})
        stream_hub_registry[params.hub_id] = hub
        stream_store_registry[params.websearch_store_id] = store

        # Add team metadata and store reference for raw_web_search stream
        if stream_id == "raw_web_search" and home_team_tricode and away_team_tricode:
            stream_config["websearch_store_id"] = params.websearch_store_id
            stream_config["home_team_tricode"] = home_team_tricode
            stream_config["away_team_tricode"] = away_team_tricode
            if home_team_name:
                stream_config["home_team_name"] = home_team_name
            if away_team_name:
                stream_config["away_team_name"] = away_team_name
            if game_date:
                stream_config["game_date"] = game_date

        stream_spec = DataStreamSpec(
            actor_id=f"{stream_id}_stream",
            actor_cls=DataHubDataStream,
            config=stream_config,
            consumer_ids=tuple(params.agent_ids),
        )
        stream_specs.append(stream_spec)

    # Create counter operator for tracking events
    operator_specs = []
    operator_config: EventCounterOperatorConfig = {"actor_id": "event_counter"}
    operator_spec = OperatorSpec(
        actor_id="event_counter",
        actor_cls=EventCounterOperator,
        config=operator_config,
    )
    operator_specs.append(operator_spec)
    LOGGER.info("Created event counter operator")

    # Create agent specs
    agent_specs = []
    for agent_id in params.agent_ids:
        agent_config: NBAPreGameBettingAgentConfig = {
            "actor_id": agent_id,
            "operator_id": "event_counter",
        }
        agent_spec = AgentSpec(
            actor_id=agent_id,
            actor_cls=NBAPreGameBettingAgent,
            config=agent_config,
            operator_ids=("event_counter",),
        )
        agent_specs.append(agent_spec)

    # Build metadata with game information
    metadata: dict[str, Any] = {
        "sample": "nba-pregame-betting",
        "game_id": params.game_id,
        "hub_id": params.hub_id,
        "stream_ids": params.stream_ids,
    }
    
    # Add team information if available
    if home_team_tricode and away_team_tricode:
        metadata["home_team_tricode"] = home_team_tricode
        metadata["away_team_tricode"] = away_team_tricode
        if home_team_name:
            metadata["home_team"] = home_team_name
        if away_team_name:
            metadata["away_team"] = away_team_name
        if game_date:
            metadata["game_date"] = game_date

    return TrialSpec(
        trial_id=trial_id,
        data_streams=tuple(stream_specs),
        operators=tuple(operator_specs),
        agents=tuple(agent_specs),
        metadata=metadata,
    )


register_trial_builder(
    "samples.nba-pregame-betting",
    NBAPreGameBettingTrialParams,
    _build_trial_spec,
    description="NBA pre-game betting scenario with web search data processing",
    example_params=NBAPreGameBettingTrialParams(
        game_id="0022501205",  # Example NBA game ID
        hub_id="nba_pregame_hub",
        persistence_file="outputs/nba_pregame_events.jsonl",
        stream_ids=["raw_web_search", "injury_summary", "power_ranking"],
        agent_ids=["betting_agent"],
    ),
)


__all__ = [
    "DataHubDataStream",
    "NBAPreGameBettingAgent",
    "EventCounterOperator",
    "NBAPreGameBettingTrialParams",
    "DataHubDataStreamConfig",
    "NBAPreGameBettingAgentConfig",
    "EventCounterOperatorConfig",
]
