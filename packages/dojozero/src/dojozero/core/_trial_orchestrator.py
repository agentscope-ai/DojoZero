"""Trial orchestration utilities for the DojoZero proof-of-concept.

This module implements the in-memory control plane for trial orchestration.
It is responsible for instantiating actors from serializable configurations,
wiring their dependencies, and managing their lifecycle (start, stop,
checkpoint, resume).
"""

import asyncio
import inspect
import logging
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Generic,
    Mapping,
    Protocol,
    Sequence,
    Tuple,
    Type,
    TypeVar,
    cast,
)
from uuid import uuid4

from ._actors import (
    Actor,
    ActorState,
    Agent,
    DataStream,
    Operator,
)
from ._runtime import (
    ActorHandler,
    ActorRuntimeProvider,
    LocalActorRuntimeProvider,
)
from ._metadata import BaseTrialMetadata, MetadataT
from ._types import RuntimeContext, JSONDict, StreamEvent


LOGGER = logging.getLogger("dojozero.orchestrator")


def _is_operator_like(candidate: Any) -> bool:
    return hasattr(candidate, "handle_stream_event") and hasattr(candidate, "actor_id")


def _is_agent_like(candidate: Any) -> bool:
    return _is_operator_like(candidate) and hasattr(candidate, "operators")


def _is_data_stream_like(candidate: Any) -> bool:
    return hasattr(candidate, "consumers") and hasattr(candidate, "actor_id")


ConfigSpecT = TypeVar("ConfigSpecT")


class OrchestratorError(RuntimeError):
    """Base class for orchestrator specific failures."""


class TrialNotFoundError(OrchestratorError):
    """Raised when a requested trial ID is unknown to the dashboard."""


class TrialExistsError(OrchestratorError):
    """Raised when a trial ID collision occurs."""


class CheckpointNotFoundError(OrchestratorError):
    """Raised when a referenced checkpoint does not exist."""


class ActorLifecycleError(OrchestratorError):
    """Raised when one or more actors fail to start or stop properly."""


class ActorRole(str, Enum):
    """Categorization used to determine start/stop ordering."""

    DATA_STREAM = "data_stream"
    OPERATOR = "operator"
    AGENT = "agent"
    SOCIAL_BOARD = "social_board"


class ActorPhase(str, Enum):
    """Lifecycle states for individual actors."""

    INITIALIZED = "initialized"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class TrialPhase(str, Enum):
    """Lifecycle states for complete trials."""

    INITIALIZED = "initialized"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(slots=True)
class ActorSpec(Generic[ConfigSpecT]):
    """Declarative configuration required to create an actor."""

    actor_id: str
    actor_cls: Type[Actor[ConfigSpecT]]
    config: ConfigSpecT
    resume_state: ActorState | None = None
    trial_id: str | None = None

    def __post_init__(self) -> None:
        if not self.actor_id:
            raise ValueError("actor_id cannot be empty")
        # Create shallow copies to avoid accidental external mutation.
        if isinstance(self.config, Mapping):
            self.config = cast(ConfigSpecT, dict(self.config))
        if self.resume_state is not None:
            self.resume_state = dict(self.resume_state)


@dataclass
class OperatorSpec(ActorSpec[ConfigSpecT]):
    """Specialized :class:`ActorSpec` for operator actors."""

    agent_ids: Sequence[str] = field(default_factory=tuple)
    data_stream_ids: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.agent_ids = tuple(self.agent_ids)
        self.data_stream_ids = tuple(self.data_stream_ids)


@dataclass
class AgentSpec(ActorSpec[ConfigSpecT]):
    """Specialized :class:`ActorSpec` for agent actors."""

    operator_ids: Sequence[str] = field(default_factory=tuple)
    data_stream_ids: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.operator_ids = tuple(self.operator_ids)
        self.data_stream_ids = tuple(self.data_stream_ids)


@dataclass
class DataStreamSpec(ActorSpec[ConfigSpecT]):
    """Specialized :class:`ActorSpec` for data stream actors."""

    consumer_ids: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.consumer_ids = tuple(self.consumer_ids)


@dataclass(slots=True)
class TrialSpec(Generic[MetadataT]):
    """High-level description of a trial with typed metadata.

    The metadata type parameter allows type-safe access to trial-specific
    configuration. For example, TrialSpec[BettingTrialMetadata] ensures
    that metadata fields like espn_game_id are properly typed.

    Attributes:
        trial_id: Unique identifier for the trial
        metadata: Domain-specific metadata (e.g., BettingTrialMetadata)
        data_streams: Specifications for data stream actors
        operators: Specifications for operator actors
        agents: Specifications for agent actors
        social_board: Optional shared social board for multi-agent communication (as OperatorSpec)
        resume_from_checkpoint_id: Optional checkpoint ID to resume from
        resume_from_latest: Whether to resume from the latest checkpoint
        builder_name: Name of the trial builder (for looking up context_builder)
    """

    trial_id: str
    metadata: MetadataT
    data_streams: Sequence[DataStreamSpec[Any]] = ()
    operators: Sequence[OperatorSpec[Any]] = ()
    agents: Sequence[AgentSpec[Any]] = ()
    social_board: OperatorSpec[Any] | None = field(
        default=None,
        compare=False,
        repr=False,
    )
    resume_from_checkpoint_id: str | None = None
    resume_from_latest: bool = False
    builder_name: str | None = None

    def __post_init__(self) -> None:
        if not self.trial_id:
            raise ValueError("trial_id cannot be empty")
        self.data_streams = tuple(self.data_streams)
        self.operators = tuple(self.operators)
        self.agents = tuple(self.agents)


@dataclass(slots=True)
class ActorRuntime(Generic[ConfigSpecT]):
    """Runtime bookkeeping for a single actor instance."""

    spec: ActorSpec[ConfigSpecT]
    handler: ActorHandler
    role: ActorRole
    phase: ActorPhase = ActorPhase.INITIALIZED
    last_error: Exception | None = None

    @property
    def actor_id(self) -> str:
        return self.handler.actor_id

    @property
    def instance(self) -> Actor[ConfigSpecT]:
        return cast(Actor[ConfigSpecT], self.handler.instance)


@dataclass(slots=True)
class TrialRuntime:
    """Runtime bookkeeping for a trial."""

    spec: TrialSpec
    actors: Dict[str, ActorRuntime[Any]]
    record: "TrialRecord"
    phase: TrialPhase = TrialPhase.INITIALIZED
    last_error: Exception | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _context: RuntimeContext | None = field(
        default=None, repr=False
    )  # Runtime context (stores, hubs, etc.)


@dataclass(slots=True)
class ActorStatus:
    """Serializable snapshot of an actor's lifecycle state."""

    actor_id: str
    role: ActorRole
    phase: ActorPhase
    last_error: str | None


@dataclass(slots=True)
class TrialStatus:
    """Serializable snapshot of a trial's lifecycle state."""

    trial_id: str
    phase: TrialPhase
    actors: Tuple[ActorStatus, ...]
    metadata: JSONDict
    last_error: str | None


@dataclass(slots=True)
class TrialCheckpoint:
    """Checkpoint payload that can be fed back into :class:`TrialSpec`."""

    trial_id: str
    actor_states: Mapping[str, ActorState]
    checkpoint_id: str | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        self.actor_states = {
            actor_id: dict(state) for actor_id, state in self.actor_states.items()
        }


@dataclass(slots=True)
class CheckpointSummary:
    """Lightweight metadata describing a stored checkpoint."""

    checkpoint_id: str
    trial_id: str
    created_at: datetime


@dataclass(slots=True)
class TrialRecord:
    """Persisted representation of a trial configuration and last known status."""

    spec: TrialSpec
    last_status: TrialStatus | None = None
    owner_server_id: str | None = None

    @property
    def trial_id(self) -> str:
        return self.spec.trial_id


class OrchestratorStore(Protocol):
    """Storage abstraction used by :class:`TrialOrchestrator` for persistence."""

    def list_trial_records(self) -> Sequence[TrialRecord]: ...

    def get_trial_record(self, trial_id: str) -> TrialRecord | None: ...

    def upsert_trial_record(self, record: TrialRecord) -> None: ...

    def delete_trial_record(self, trial_id: str) -> None: ...

    def save_checkpoint(self, checkpoint: TrialCheckpoint) -> TrialCheckpoint: ...

    def load_checkpoint(self, checkpoint_id: str) -> TrialCheckpoint: ...

    def list_checkpoints(self, trial_id: str) -> Sequence[CheckpointSummary]: ...

    def save_trial_results(self, trial_id: str, results: Dict[str, Any]) -> None: ...

    def get_trial_results(self, trial_id: str) -> Dict[str, Any] | None: ...


class InMemoryOrchestratorStore(OrchestratorStore):
    """Simple :class:`OrchestratorStore` implementation backed by local dictionaries."""

    def __init__(self) -> None:
        self._records: Dict[str, TrialRecord] = {}
        self._checkpoints: Dict[str, TrialCheckpoint] = {}
        self._checkpoint_index: Dict[str, list[str]] = {}
        self._results: Dict[str, Dict[str, Any]] = {}

    def list_trial_records(self) -> Sequence[TrialRecord]:
        return tuple(
            TrialRecord(spec=record.spec, last_status=record.last_status)
            for record in self._records.values()
        )

    def get_trial_record(self, trial_id: str) -> TrialRecord | None:
        record = self._records.get(trial_id)
        if record is None:
            return None
        return TrialRecord(spec=record.spec, last_status=record.last_status)

    def upsert_trial_record(self, record: TrialRecord) -> None:
        self._records[record.trial_id] = TrialRecord(
            spec=record.spec, last_status=record.last_status
        )

    def delete_trial_record(self, trial_id: str) -> None:
        self._records.pop(trial_id, None)
        for checkpoint_id in self._checkpoint_index.pop(trial_id, []):
            self._checkpoints.pop(checkpoint_id, None)

    def save_checkpoint(self, checkpoint: TrialCheckpoint) -> TrialCheckpoint:
        checkpoint_id = checkpoint.checkpoint_id or uuid4().hex
        created_at = checkpoint.created_at or datetime.now(timezone.utc)
        payload = TrialCheckpoint(
            trial_id=checkpoint.trial_id,
            actor_states={
                actor_id: dict(state)
                for actor_id, state in checkpoint.actor_states.items()
            },
            checkpoint_id=checkpoint_id,
            created_at=created_at,
        )
        self._checkpoints[checkpoint_id] = payload
        self._checkpoint_index.setdefault(checkpoint.trial_id, []).append(checkpoint_id)
        return payload

    def load_checkpoint(self, checkpoint_id: str) -> TrialCheckpoint:
        try:
            return self._checkpoints[checkpoint_id]
        except KeyError as exc:  # pragma: no cover - defensive guard
            raise CheckpointNotFoundError(
                f"checkpoint '{checkpoint_id}' does not exist"
            ) from exc

    def list_checkpoints(self, trial_id: str) -> Sequence[CheckpointSummary]:
        checkpoint_ids = self._checkpoint_index.get(trial_id, [])
        return tuple(
            CheckpointSummary(
                checkpoint_id=checkpoint_id,
                trial_id=trial_id,
                created_at=self._checkpoints[checkpoint_id].created_at
                or datetime.now(timezone.utc),
            )
            for checkpoint_id in checkpoint_ids
        )

    def save_trial_results(self, trial_id: str, results: Dict[str, Any]) -> None:
        self._results[trial_id] = results

    def get_trial_results(self, trial_id: str) -> Dict[str, Any] | None:
        return self._results.get(trial_id)


class TrialOrchestrator:
    """In-memory controller responsible for coordinating actors.

    A dashboard instance can host multiple concurrent trials. Each trial defines its
    own set of DataStreams, Operators, and Agents and can be checkpointed or
    resumed independently. All lifecycle operations are asynchronous to integrate
    naturally with FastAPI or CLI frontends.
    """

    _START_ORDER: Tuple[ActorRole, ...] = (
        ActorRole.OPERATOR,
        ActorRole.AGENT,
        ActorRole.SOCIAL_BOARD,
        ActorRole.DATA_STREAM,
    )
    _STOP_ORDER: Tuple[ActorRole, ...] = (
        ActorRole.DATA_STREAM,
        ActorRole.SOCIAL_BOARD,
        ActorRole.AGENT,
        ActorRole.OPERATOR,
    )

    def __init__(
        self,
        *,
        store: OrchestratorStore | None = None,
        runtime_provider: ActorRuntimeProvider | None = None,
    ) -> None:
        self._store = store or InMemoryOrchestratorStore()
        self._runtime_provider = runtime_provider or LocalActorRuntimeProvider()
        self._catalog: Dict[str, TrialRecord] = {
            record.trial_id: TrialRecord(
                spec=record.spec, last_status=record.last_status
            )
            for record in self._store.list_trial_records()
        }
        self._trials: Dict[str, TrialRuntime] = {}
        self._lock = asyncio.Lock()
        LOGGER.debug(
            "TrialOrchestrator initialized with store=%s runtime_provider=%s",
            type(self._store).__name__,
            type(self._runtime_provider).__name__,
        )

    @property
    def store(self) -> OrchestratorStore:
        """Access the underlying OrchestratorStore."""
        return self._store

    async def launch_trial(
        self,
        spec: TrialSpec,
        owner_server_id: str | None = None,
    ) -> TrialStatus:
        """Instantiate and start every actor defined in *spec*.

        Args:
            spec: Trial specification.
            owner_server_id: Server that owns this trial (cluster mode).
        """

        LOGGER.info("launching trial '%s'", spec.trial_id)
        spec = self._apply_resume_from_spec(spec)
        normalized_spec = self._normalize_spec(spec)
        record = self._catalog.get(spec.trial_id)
        if record is None:
            record = TrialRecord(spec=normalized_spec, owner_server_id=owner_server_id)
        elif record.spec != normalized_spec:
            raise OrchestratorError(
                f"trial '{spec.trial_id}' already registered with a different configuration"
            )
        else:
            record.spec = (
                normalized_spec  # Ensure latest metadata (e.g., schedule tweaks)
            )
        runtime = await self._build_runtime(spec, record)
        async with self._lock:
            if spec.trial_id in self._trials:
                raise TrialExistsError(f"trial '{spec.trial_id}' already exists")
            self._trials[spec.trial_id] = runtime
            self._catalog[spec.trial_id] = record
            self._store.upsert_trial_record(record)
        await self._with_trial_lock(runtime, self._start_runtime)
        status = self._build_trial_status(runtime)
        self._persist_trial_status(runtime, status)

        # Emit trial.started span
        self._emit_trial_lifecycle_span(
            trial_id=spec.trial_id,
            phase="started",
            metadata=asdict(spec.metadata),
        )

        LOGGER.info(
            "trial '%s' launch complete (phase=%s)",
            spec.trial_id,
            status.phase.value,
        )
        return status

    async def stop_trial(self, trial_id: str) -> TrialStatus:
        """Gracefully stop all actors belonging to *trial_id*."""

        LOGGER.info("stopping trial '%s'", trial_id)
        runtime = self._require_runtime(trial_id)
        await self._with_trial_lock(runtime, self._stop_runtime)
        status = self._build_trial_status(runtime)
        self._persist_trial_status(runtime, status)

        # Emit trial.stopped span
        self._emit_trial_lifecycle_span(
            trial_id=trial_id,
            phase="stopped",
            metadata={"final_phase": status.phase.value},
        )

        LOGGER.info(
            "trial '%s' stopped (phase=%s)",
            trial_id,
            status.phase.value,
        )
        return status

    async def delete_trial(self, trial_id: str) -> None:
        """Forget a stopped trial and release its runtime bookkeeping."""

        LOGGER.info("deleting trial '%s'", trial_id)
        async with self._lock:
            runtime = self._trials.get(trial_id)
            if runtime is not None and runtime.phase not in {
                TrialPhase.STOPPED,
                TrialPhase.FAILED,
            }:
                raise OrchestratorError("trial must be stopped before deletion")
            self._trials.pop(trial_id, None)
            record = self._catalog.pop(trial_id, None)
            if record is None:
                raise TrialNotFoundError(f"trial '{trial_id}' does not exist")
            self._store.delete_trial_record(trial_id)
        LOGGER.info("trial '%s' deleted", trial_id)

    def list_trials(self) -> Tuple[TrialStatus, ...]:
        """Return statuses for every known trial."""

        statuses: list[TrialStatus] = []
        seen: set[str] = set()
        for trial_id, runtime in self._trials.items():
            status = self._build_trial_status(runtime)
            statuses.append(status)
            seen.add(trial_id)
        for trial_id, record in self._catalog.items():
            if trial_id in seen:
                continue
            statuses.append(self._status_from_record(record))
        result = tuple(sorted(statuses, key=lambda status: status.trial_id))
        LOGGER.info("listed %d trials", len(result))
        return result

    def get_trial_status(self, trial_id: str) -> TrialStatus:
        """Return the lifecycle status for *trial_id*."""

        runtime = self._trials.get(trial_id)
        if runtime is not None:
            status = self._build_trial_status(runtime)
            self._persist_trial_status(runtime, status)
            LOGGER.info(
                "status fetched for trial '%s' (phase=%s)",
                trial_id,
                status.phase.value,
            )
            return status
        record = self._catalog.get(trial_id)
        if record is not None:
            status = self._status_from_record(record)
            LOGGER.info(
                "status fetched for trial '%s' (phase=%s)",
                trial_id,
                status.phase.value,
            )
            return status
        record = self._store.get_trial_record(trial_id)
        if record is not None:
            self._catalog[trial_id] = record
            status = self._status_from_record(record)
            LOGGER.info(
                "status fetched for trial '%s' (phase=%s)",
                trial_id,
                status.phase.value,
            )
            return status
        raise TrialNotFoundError(f"trial '{trial_id}' does not exist")

    def get_actor(self, trial_id: str, actor_id: str) -> Actor:
        """Return the concrete actor instance for inspection or direct calls."""

        runtime = self._require_runtime(trial_id)
        try:
            LOGGER.debug(
                "returning actor '%s' for trial '%s'",
                actor_id,
                trial_id,
            )
            return runtime.actors[actor_id].instance
        except KeyError as exc:  # pragma: no cover - thin guard
            raise KeyError(
                f"actor '{actor_id}' is not part of trial '{trial_id}'"
            ) from exc

    async def checkpoint_trial(self, trial_id: str) -> TrialCheckpoint:
        """Capture a consistent checkpoint for *trial_id*."""

        LOGGER.info("checkpointing trial '%s'", trial_id)
        runtime = self._require_runtime(trial_id)
        if runtime.phase == TrialPhase.INITIALIZED:
            raise OrchestratorError("trial must be running before checkpointing")
        async with runtime.lock:
            actor_states = await asyncio.gather(
                *(
                    self._save_actor_state(actor_id, actor_runtime)
                    for actor_id, actor_runtime in runtime.actors.items()
                )
            )
        checkpoint = TrialCheckpoint(trial_id=trial_id, actor_states=dict(actor_states))
        saved = self._store.save_checkpoint(checkpoint)
        LOGGER.info(
            "checkpoint '%s' captured for trial '%s'",
            saved.checkpoint_id,
            trial_id,
        )
        return saved

    def list_checkpoints(self, trial_id: str) -> Tuple[CheckpointSummary, ...]:
        """Return available checkpoint metadata for *trial_id*."""

        summaries = tuple(self._store.list_checkpoints(trial_id))
        LOGGER.info(
            "listed %d checkpoints for trial '%s'",
            len(summaries),
            trial_id,
        )
        return summaries

    def load_checkpoint(self, checkpoint_id: str) -> TrialCheckpoint:
        """Load a stored checkpoint payload (includes actor states)."""

        checkpoint = self._store.load_checkpoint(checkpoint_id)
        LOGGER.info(
            "loaded checkpoint '%s' for trial '%s'",
            checkpoint_id,
            checkpoint.trial_id,
        )
        return checkpoint

    async def resume_trial(
        self, trial_id: str, checkpoint_id: str | None = None
    ) -> TrialStatus:
        """Restart a trial using its persisted configuration and optional checkpoint."""

        LOGGER.info(
            "resuming trial '%s'%s",
            trial_id,
            f" with checkpoint '{checkpoint_id}'" if checkpoint_id else "",
        )
        record = self._catalog.get(trial_id)
        if record is None:
            record = self._store.get_trial_record(trial_id)
            if record is None:
                raise TrialNotFoundError(f"trial '{trial_id}' does not exist")
            self._catalog[trial_id] = record
        checkpoint = self._resolve_resume_checkpoint(trial_id, checkpoint_id)
        if checkpoint is not None:
            LOGGER.info(
                "checkpoint '%s' loaded for trial '%s'",
                checkpoint.checkpoint_id,
                trial_id,
            )
        else:
            LOGGER.info("no checkpoint used for trial '%s'", trial_id)
        resume_spec = self._spec_with_resume_state(record.spec, checkpoint)
        status = await self.launch_trial(resume_spec)
        LOGGER.info(
            "trial '%s' resume complete (phase=%s)",
            trial_id,
            status.phase.value,
        )
        return status

    async def _save_actor_state(
        self, actor_id: str, actor_runtime: ActorRuntime[Any]
    ) -> Tuple[str, ActorState]:
        LOGGER.debug(
            "saving state for actor '%s' (role=%s)",
            actor_id,
            actor_runtime.role.value,
        )
        state = await actor_runtime.handler.save_state()
        return actor_id, state

    def has_trial(self, trial_id: str) -> bool:
        """Return ``True`` if the dashboard knows about *trial_id*."""

        result = trial_id in self._trials
        LOGGER.debug("has_trial('%s') -> %s", trial_id, result)
        return result

    async def _build_runtime(
        self, spec: TrialSpec, record: TrialRecord
    ) -> TrialRuntime:
        # Build runtime context with DataHub and Store instances
        # Extract from stream configs to recreate hub/store instances
        context = self._build_runtime_context(spec)

        # Note: _startup is NOT called here - it will be called after all actors
        # (especially DATA_STREAM actors) are started to ensure streams subscribe
        # before stores start polling

        registry: Dict[str, ActorRuntime[Any]] = {}
        agents: Dict[str, Agent] = {}
        operators: Dict[str, Operator] = {}
        data_streams: Dict[str, DataStream] = {}

        for actor_spec in spec.operators:
            actor_spec.trial_id = spec.trial_id
            runtime = await self._materialize_actor(
                actor_spec,
                ActorRole.OPERATOR,
                context=context,
            )
            self._register_actor_runtime(registry, runtime)
            operator_instance = runtime.instance
            if not (
                isinstance(operator_instance, Operator)
                or _is_operator_like(operator_instance)
            ):
                raise OrchestratorError(
                    f"actor '{runtime.actor_id}' registered as operator"
                    " does not implement the Operator protocol"
                )
            operators[runtime.actor_id] = cast(Operator, operator_instance)
            self._emit_actor_registration_span(
                spec.trial_id, runtime.actor_id, "operator", actor_spec.config
            )
            LOGGER.debug(
                "Materialized operator '%s' for trial '%s'",
                runtime.actor_id,
                spec.trial_id,
            )
        for actor_spec in spec.agents:
            actor_spec.trial_id = spec.trial_id
            runtime = await self._materialize_actor(
                actor_spec,
                ActorRole.AGENT,
                context=context,
            )
            self._register_actor_runtime(registry, runtime)
            agent_instance = runtime.instance
            if not (
                isinstance(agent_instance, Agent) or _is_agent_like(agent_instance)
            ):
                raise OrchestratorError(
                    f"actor '{runtime.actor_id}' registered as agent"
                    " does not implement the Agent protocol"
                )
            agents[runtime.actor_id] = cast(Agent, agent_instance)
            self._emit_actor_registration_span(
                spec.trial_id, runtime.actor_id, "agent", actor_spec.config
            )
        # Emit batch agent initialization span with all agent metadata
        self._emit_agents_initialize_span(spec.trial_id, spec)
        for actor_spec in spec.data_streams:
            actor_spec.trial_id = spec.trial_id
            runtime = await self._materialize_actor(
                actor_spec,
                ActorRole.DATA_STREAM,
                context=context,
            )
            self._register_actor_runtime(registry, runtime)
            stream_instance = runtime.instance
            if not (
                isinstance(stream_instance, DataStream)
                or _is_data_stream_like(stream_instance)
            ):
                raise OrchestratorError(
                    f"actor '{runtime.actor_id}' registered as data stream"
                    " does not implement the DataStream protocol"
                )
            data_streams[runtime.actor_id] = cast(DataStream, stream_instance)
            self._emit_actor_registration_span(
                spec.trial_id, runtime.actor_id, "datastream", actor_spec.config
            )
        if spec.social_board is not None:
            spec.social_board.trial_id = spec.trial_id
            runtime = await self._materialize_actor(
                spec.social_board,
                ActorRole.SOCIAL_BOARD,
                context=context,
            )
            self._register_actor_runtime(registry, runtime)
            self._emit_actor_registration_span(
                spec.trial_id,
                runtime.actor_id,
                "social_board",
                spec.social_board.config,
            )
        await self._wire_actor_dependencies(
            spec,
            agents=agents,
            operators=operators,
            data_streams=data_streams,
            actor_registry=registry,
        )
        # Store context in runtime so _start_runtime can access _startup function
        runtime = TrialRuntime(spec=spec, actors=registry, record=record)
        runtime._context = context
        return runtime

    async def _materialize_actor(
        self,
        spec: ActorSpec[Any],
        role: ActorRole,
        context: RuntimeContext,
    ) -> ActorRuntime[Any]:
        try:
            handler = await self._runtime_provider.create_handler(spec, context=context)
        except Exception as exc:  # pragma: no cover - defensive translation
            raise OrchestratorError(str(exc)) from exc
        return ActorRuntime(spec=spec, handler=handler, role=role)

    def _build_runtime_context(self, spec: TrialSpec) -> RuntimeContext:
        """Build runtime context using context builder from trial builder registry.

        If the trial builder provides a context_builder, use it. Otherwise,
        return minimal context with just trial_id for trials without data infrastructure.

        Args:
            spec: Trial specification

        Returns:
            RuntimeContext with trial_id and optionally data_hubs/stores
        """
        if spec.builder_name:
            try:
                from ._registry import get_trial_builder_definition

                builder_def = get_trial_builder_definition(spec.builder_name)
                if builder_def.context_builder:
                    return builder_def.context_builder(spec)
            except Exception:
                # Builder not found or no context builder - fall through to default
                pass

        # Default: minimal context with just trial_id
        return RuntimeContext(trial_id=spec.trial_id)

    def _register_actor_runtime(
        self, registry: Dict[str, ActorRuntime[Any]], runtime: ActorRuntime[Any]
    ) -> None:
        if runtime.actor_id in registry:
            raise OrchestratorError(f"duplicate actor id '{runtime.actor_id}' detected")
        registry[runtime.actor_id] = runtime

    async def _wire_actor_dependencies(
        self,
        spec: TrialSpec,
        *,
        agents: Mapping[str, Agent[Any]],
        operators: Mapping[str, Operator[Any]],
        data_streams: Mapping[str, DataStream[Any]],
        actor_registry: Mapping[str, ActorRuntime[Any]],
    ) -> None:
        await self._wire_operator_agents(spec.operators, operators, agents)
        await self._wire_agent_operators(spec.agents, agents, operators)
        # Agent-centric wiring: agents register themselves with streams
        await self._wire_agent_streams(spec.agents, agents, data_streams)
        # Operator-centric wiring: operators register themselves with streams
        await self._wire_operator_streams(spec.operators, operators, data_streams)
        # Legacy stream-centric wiring (for backward compatibility)
        await self._wire_stream_consumers(
            spec.data_streams,
            data_streams,
            agents,
            operators,
        )
        # Wire social board if present (multi-agent communication)
        if spec.social_board is not None:
            await self._wire_social_board_agents(
                [spec.social_board],
                agents,
                operators,
                actor_registry=actor_registry,
            )

    async def _wire_social_board_agents(
        self,
        social_board_specs: Sequence[OperatorSpec[Any]],
        agents: Mapping[str, Agent[Any]],
        operators: Mapping[str, Operator[Any]],
        *,
        actor_registry: Mapping[str, ActorRuntime[Any]],
    ) -> None:
        """Wire social boards to agents for multi-agent communication."""
        from dojozero.agents import create_social_board_tools

        for sb_spec in social_board_specs:
            # During _build_runtime the trial is not yet in self._trials; use the
            # local registry built for this trial.
            sb_runtime = actor_registry.get(sb_spec.actor_id)
            if sb_runtime is None:
                LOGGER.error(
                    "social board actor '%s' missing from actor registry",
                    sb_spec.actor_id,
                )
                continue
            sb_actor = sb_runtime.instance
            # `Actor` protocol doesn't define `social_board`, but
            # `SocialBoardActor` exposes it. Use `getattr` so Pyright doesn't
            # error on attribute access.
            social_board = getattr(sb_actor, "social_board", None)
            if social_board is None:
                LOGGER.warning(
                    "social board actor '%s' has no SocialBoard instance yet; skipping wiring",
                    sb_spec.actor_id,
                )
                continue

            # Determine which agents this social board connects to
            target_agents = agents
            if sb_spec.agent_ids:
                target_agents = {
                    aid: agent
                    for aid, agent in agents.items()
                    if aid in sb_spec.agent_ids
                }

            trial_id = sb_spec.trial_id
            if trial_id is None:
                LOGGER.error(
                    "social board spec '%s' missing trial_id; skipping hot topics wiring",
                    sb_spec.actor_id,
                )
                continue

            hot_topics_interval = 100
            hot_topics_trigger = self._build_hot_topics_trigger(
                social_board,
                target_agents,
                trial_id,
                system_summary_provider=self._get_system_summary_provider(operators),
            )

            # Create tools and register with each agent
            for agent_id, agent in target_agents.items():
                tools = create_social_board_tools(
                    social_board,
                    hot_topics_interval=hot_topics_interval,
                    hot_topics_trigger=hot_topics_trigger,
                )

                # Check if agent has register_social_board method
                if hasattr(agent, "register_social_board"):
                    # Pass the social board and tools as a tuple
                    payload = (social_board, hot_topics_interval, hot_topics_trigger)
                    await self._invoke_registration(
                        agent,
                        "register_social_board",
                        payload,
                        actor_id=agent_id,
                    )
                elif hasattr(agent, "register_tools"):
                    # Alternative: register tools directly
                    await self._invoke_registration(
                        agent,
                        "register_tools",
                        tools,
                        actor_id=agent_id,
                    )

    def _build_hot_topics_trigger(
        self,
        social_board: Any,
        agents: Mapping[str, Agent[Any]],
        trial_id: str,
        system_summary_provider: Callable[[], str | None] | None = None,
    ) -> Callable[[], Awaitable[None]]:
        """Build async callback that generates hot topics and pushes to all agents.

        The returned callback reads recent social board messages, calls the LLM
        to produce a short hot-topics list, then broadcasts a HotTopicsEvent
        to every agent via handle_stream_event. Used when message count hits
        a multiple of hot_topics_interval (e.g. every 20 posts).

        Args:
            social_board: Shared social board to read recent messages from.
            agents: Mapping of agent_id to Agent for broadcasting the event.
            trial_id: Trial ID to attach to the HotTopicsEvent.

        Returns:
            An async callable with no arguments that performs the generation
            and broadcast. Safe to call; logs and returns on LLM or API errors.
        """
        import re
        import time

        from dojozero.agents import HotTopicsEvent
        from dojozero.betting._config import HOT_TOPICS_PROMPT
        from dojozero.data._utils import call_dashscope_model, initialize_dashscope

        async def _trigger() -> None:
            try:
                initialize_dashscope()
            except (ImportError, ValueError) as e:
                LOGGER.warning("Hot topics skipped (Dashscope not available): %s", e)
                return
            # Use any agent to read; we want recent messages from all
            recent = social_board.read_messages("", limit=50, exclude_own=False)
            if not recent:
                LOGGER.debug("Hot topics skipped: no recent messages on social board")
                return
            recent_text = "\n".join(
                f"[{m.agent_id}]: {m.content}" for m in reversed(recent)
            )
            prompt = HOT_TOPICS_PROMPT.format(recent_messages=recent_text)
            try:
                response = await call_dashscope_model(prompt, model="qwen-turbo")
            except Exception as e:
                LOGGER.warning("Hot topics LLM call failed: %s", e)
                return
            text = (response.get("output") or {}).get("text", "") or ""
            # Parse numbered lines (1. topic, 2. topic, ...)
            topics: list[str] = []
            for line in text.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                m = re.match(r"^\d+[\.\)]\s*(.+)", line)
                if m:
                    topics.append(m.group(1).strip())
                else:
                    topics.append(line)
            if not topics:
                LOGGER.debug(
                    "Hot topics skipped: LLM returned no parseable topics (raw length=%d)",
                    len(text),
                )
                return
            # Optionally prepend a synthetic system summary topic (e.g., bet distribution)
            if system_summary_provider is not None:
                try:
                    system_line = system_summary_provider()
                except Exception as e:  # pragma: no cover - defensive logging
                    LOGGER.debug(
                        "Hot topics system summary provider failed: %s",
                        e,
                    )
                    system_line = None
                if system_line:
                    topics.insert(0, f"[System] {system_line}")
            LOGGER.info(
                "Hot topics generated and pushed to %d agents: %s",
                len(agents),
                "; ".join(topics[:]),
            )
            event = HotTopicsEvent(
                trial_id=trial_id,
                topics=tuple(topics[:10]),
                generated_at=time.time(),
            )
            stream_event = StreamEvent(
                stream_id="social_board",
                payload=event,
            )
            coros = [
                agent.handle_stream_event(stream_event) for agent in agents.values()
            ]
            results = await asyncio.gather(*coros, return_exceptions=True)
            for agent, result in zip(agents.values(), results):
                if isinstance(result, Exception):
                    LOGGER.warning(
                        "Failed to push hot topics to agent %s: %s",
                        getattr(agent, "actor_id", agent),
                        result,
                    )

        return _trigger

    def _get_system_summary_provider(
        self,
        operators: Mapping[str, Operator[Any]],
    ) -> Callable[[], str | None] | None:
        """Get system summary provider for hot topics if betting_broker is available."""
        _BROKER_OPERATOR_ID = "betting_broker"
        broker = operators.get(_BROKER_OPERATOR_ID)
        if broker is not None and callable(
            getattr(broker, "get_bet_distribution_summary", None)
        ):
            op = broker

            def _provider() -> str | None:
                try:
                    return op.get_bet_distribution_summary()  # type: ignore[no-any-return]
                except Exception as e:
                    LOGGER.debug(
                        "Hot topics system summary from %s failed: %s",
                        _BROKER_OPERATOR_ID,
                        e,
                    )
                    return None

            return _provider
        return None

    async def _wire_operator_agents(
        self,
        operator_specs: Sequence[OperatorSpec[Any]],
        operators: Mapping[str, Operator[Any]],
        agents: Mapping[str, Agent[Any]],
    ) -> None:
        for operator_spec in operator_specs:
            if not operator_spec.agent_ids:
                continue
            operator = operators.get(operator_spec.actor_id)
            if operator is None:  # pragma: no cover - defensive
                raise OrchestratorError(
                    f"operator '{operator_spec.actor_id}' missing from runtime registry"
                )
            dependencies: list[Agent[Any]] = []
            for agent_id in operator_spec.agent_ids:
                agent = agents.get(agent_id)
                if agent is None:
                    raise OrchestratorError(
                        f"operator '{operator_spec.actor_id}' requires agent '{agent_id}'"
                    )
                dependencies.append(agent)
            await self._invoke_registration(
                operator,
                "register_agents",
                tuple(dependencies),
                actor_id=operator_spec.actor_id,
            )

    async def _wire_agent_operators(
        self,
        agent_specs: Sequence[AgentSpec[Any]],
        agents: Mapping[str, Agent[Any]],
        operators: Mapping[str, Operator[Any]],
    ) -> None:
        for agent_spec in agent_specs:
            if not agent_spec.operator_ids:
                continue
            agent = agents.get(agent_spec.actor_id)
            if agent is None:  # pragma: no cover - defensive
                raise OrchestratorError(
                    f"agent '{agent_spec.actor_id}' missing from runtime registry"
                )
            dependencies: list[Operator[Any]] = []
            for operator_id in agent_spec.operator_ids:
                operator = operators.get(operator_id)
                if operator is None:
                    raise OrchestratorError(
                        f"agent '{agent_spec.actor_id}' requires operator '{operator_id}'"
                    )
                dependencies.append(operator)
            await self._invoke_registration(
                agent,
                "register_operators",
                tuple(dependencies),
                actor_id=agent_spec.actor_id,
            )

    async def _wire_agent_streams(
        self,
        agent_specs: Sequence[AgentSpec[Any]],
        agents: Mapping[str, Agent[Any]],
        data_streams: Mapping[str, DataStream[Any]],
    ) -> None:
        """Wire agents to data streams (agent-centric approach).

        Agents declare which streams they subscribe to via data_stream_ids.
        """
        for agent_spec in agent_specs:
            if not agent_spec.data_stream_ids:
                continue
            agent = agents.get(agent_spec.actor_id)
            if agent is None:  # pragma: no cover - defensive
                raise OrchestratorError(
                    f"agent '{agent_spec.actor_id}' missing from runtime registry"
                )
            dependencies: list[DataStream[Any]] = []
            for stream_id in agent_spec.data_stream_ids:
                stream = data_streams.get(stream_id)
                if stream is None:
                    raise OrchestratorError(
                        f"agent '{agent_spec.actor_id}' requires data stream '{stream_id}'"
                    )
                dependencies.append(stream)
            # Register agent as consumer of these streams
            for stream in dependencies:
                await self._invoke_registration(
                    stream,
                    "register_consumers",
                    (agent,),
                    actor_id=stream.actor_id,
                )

    async def _wire_operator_streams(
        self,
        operator_specs: Sequence[OperatorSpec[Any]],
        operators: Mapping[str, Operator[Any]],
        data_streams: Mapping[str, DataStream[Any]],
    ) -> None:
        """Wire operators to data streams (operator-centric approach).

        Operators declare which streams they subscribe to via data_stream_ids.
        """
        for operator_spec in operator_specs:
            if not operator_spec.data_stream_ids:
                continue
            operator = operators.get(operator_spec.actor_id)
            if operator is None:  # pragma: no cover - defensive
                raise OrchestratorError(
                    f"operator '{operator_spec.actor_id}' missing from runtime registry"
                )
            dependencies: list[DataStream[Any]] = []
            for stream_id in operator_spec.data_stream_ids:
                stream = data_streams.get(stream_id)
                if stream is None:
                    raise OrchestratorError(
                        f"operator '{operator_spec.actor_id}' requires data stream '{stream_id}'"
                    )
                dependencies.append(stream)
            # Register operator as consumer of these streams
            for stream in dependencies:
                await self._invoke_registration(
                    stream,
                    "register_consumers",
                    (operator,),
                    actor_id=stream.actor_id,
                )

    async def _wire_stream_consumers(
        self,
        stream_specs: Sequence[DataStreamSpec[Any]],
        data_streams: Mapping[str, DataStream[Any]],
        agents: Mapping[str, Agent[Any]],
        operators: Mapping[str, Operator[Any]],
    ) -> None:
        """Wire streams to consumers (stream-centric approach, legacy).

        Only used if consumer_ids are explicitly set on streams.
        Agent-centric wiring (via agent.data_stream_ids) takes precedence.
        """
        for stream_spec in stream_specs:
            if not stream_spec.consumer_ids:
                continue
            stream = data_streams.get(stream_spec.actor_id)
            if stream is None:  # pragma: no cover - defensive
                raise OrchestratorError(
                    f"data stream '{stream_spec.actor_id}' missing from runtime registry"
                )
            dependencies: list[Agent[Any] | Operator[Any]] = []
            for consumer_id in stream_spec.consumer_ids:
                consumer: Agent[Any] | Operator[Any] | None = agents.get(consumer_id)
                if consumer is None:
                    consumer = operators.get(consumer_id)
                if consumer is None:
                    raise OrchestratorError(
                        f"stream '{stream_spec.actor_id}' requires consumer '{consumer_id}'"
                    )
                dependencies.append(consumer)
            await self._invoke_registration(
                stream,
                "register_consumers",
                tuple(dependencies),
                actor_id=stream_spec.actor_id,
            )

    async def _invoke_registration(
        self,
        actor: Any,
        method_name: str,
        payload: Sequence[Any],
        *,
        actor_id: str,
    ) -> None:
        if not payload:
            return
        registrar = getattr(actor, method_name, None)
        if registrar is None:
            raise OrchestratorError(
                f"actor '{actor_id}' missing required '{method_name}' method"
            )
        result = registrar(payload)
        if inspect.isawaitable(result):
            await cast(Awaitable[Any], result)

    async def _with_trial_lock(
        self, runtime: TrialRuntime, fn: Callable[[TrialRuntime], Awaitable[None]]
    ) -> None:
        async with runtime.lock:
            await fn(runtime)

    async def _start_runtime(self, runtime: TrialRuntime) -> None:
        if runtime.phase not in {
            TrialPhase.INITIALIZED,
            TrialPhase.STOPPED,
            TrialPhase.FAILED,
        }:
            raise OrchestratorError(
                f"trial '{runtime.spec.trial_id}' cannot start from phase '{runtime.phase.value}'"
            )
        runtime.phase = TrialPhase.STARTING
        runtime.last_error = None
        try:
            # Start actors in order: OPERATOR, AGENT, DATA_STREAM
            for role in self._START_ORDER:
                await self._start_role(runtime, role)

            # After all actors (especially DATA_STREAM) are started and subscribed,
            # call startup function if provided by context builder (e.g., to start DataStore polling)
            # This ensures streams are subscribed before stores start emitting events
            context = runtime._context
            if context is not None and context.startup is not None:
                startup_fn = context.startup
                LOGGER.debug(
                    "Calling startup function after all actors started for trial '%s'",
                    runtime.spec.trial_id,
                )
                if asyncio.iscoroutinefunction(startup_fn):
                    await startup_fn()
                else:
                    startup_fn()
        except Exception as exc:  # pragma: no cover - error propagation
            runtime.phase = TrialPhase.FAILED
            runtime.last_error = exc
            await self._stop_started_actors(runtime)
            raise
        runtime.phase = TrialPhase.RUNNING

        # Wire self-stop: when context.request_stop fires (GameResultEvent),
        # schedule stop_trial() after a 10s delay for broker settlement
        context = runtime._context
        if context is not None and context.request_stop is not None:
            trial_id = runtime.spec.trial_id

            def _delayed_stop_callback() -> None:
                """Callback injected into factory's self-stop mechanism."""

                async def _delayed_stop() -> None:
                    await asyncio.sleep(10)
                    if runtime.phase == TrialPhase.RUNNING:
                        LOGGER.info(
                            "Executing self-stop for trial '%s' (checkpoint + stop)",
                            trial_id,
                        )
                        try:
                            # Checkpoint before stopping to preserve final state
                            LOGGER.info(
                                "Creating final checkpoint for trial '%s'", trial_id
                            )
                            await self.checkpoint_trial(trial_id)
                        except Exception as exc:
                            LOGGER.warning(
                                "Final checkpoint failed for trial '%s': %s",
                                trial_id,
                                exc,
                            )
                        try:
                            await self.stop_trial(trial_id)
                        except Exception as exc:
                            LOGGER.error(
                                "Self-stop failed for trial '%s': %s",
                                trial_id,
                                exc,
                            )
                    else:
                        LOGGER.info(
                            "Self-stop skipped for trial '%s' (phase=%s)",
                            trial_id,
                            runtime.phase.value,
                        )

                try:
                    asyncio.get_running_loop().create_task(_delayed_stop())
                except RuntimeError:
                    LOGGER.warning(
                        "No running event loop for self-stop of trial '%s'",
                        trial_id,
                    )

            # Inject the delayed stop callback into the factory's self-stop mechanism
            if context.set_stop_callback is not None:
                context.set_stop_callback(_delayed_stop_callback)

    async def _stop_runtime(self, runtime: TrialRuntime) -> None:
        if runtime.phase in {TrialPhase.STOPPED, TrialPhase.INITIALIZED}:
            return
        runtime.phase = TrialPhase.STOPPING
        errors: list[Exception] = []
        for role in self._STOP_ORDER:
            role_errors = await self._stop_role(runtime, role)
            errors.extend(role_errors)

        # Call cleanup function if provided by context builder (e.g., to stop DataStore polling)
        context = getattr(runtime, "_context", None)
        cleanup_fn = getattr(context, "cleanup", None) if context else None
        if cleanup_fn and callable(cleanup_fn):
            LOGGER.debug(
                "Calling cleanup function after stopping actors for trial '%s'",
                runtime.spec.trial_id,
            )
            try:
                if asyncio.iscoroutinefunction(cleanup_fn):
                    await cleanup_fn()
                else:
                    cleanup_fn()
            except Exception as exc:
                LOGGER.error(
                    "Error during cleanup for trial '%s': %s",
                    runtime.spec.trial_id,
                    exc,
                )
                errors.append(exc)

        if errors:
            runtime.phase = TrialPhase.FAILED
            runtime.last_error = errors[-1]
            raise ActorLifecycleError(
                f"failed to stop actors for trial '{runtime.spec.trial_id}'"
            ) from errors[-1]
        runtime.phase = TrialPhase.STOPPED

    async def _start_role(self, runtime: TrialRuntime, role: ActorRole) -> None:
        selected = [
            actor_rt for actor_rt in runtime.actors.values() if actor_rt.role is role
        ]
        if not selected:
            LOGGER.debug(
                "No actors with role %s found for trial '%s'",
                role.value,
                runtime.spec.trial_id,
            )
            return
        LOGGER.debug(
            "Starting %d actor(s) with role %s for trial '%s': %s",
            len(selected),
            role.value,
            runtime.spec.trial_id,
            [actor_rt.actor_id for actor_rt in selected],
        )
        for actor_rt in selected:
            actor_rt.phase = ActorPhase.STARTING
        results = await asyncio.gather(
            *(actor_rt.handler.start() for actor_rt in selected),
            return_exceptions=True,
        )
        failures: list[Tuple[ActorRuntime[Any], Exception]] = []
        for actor_rt, result in zip(selected, results):
            if isinstance(result, Exception):
                actor_rt.phase = ActorPhase.FAILED
                actor_rt.last_error = result
                failures.append((actor_rt, result))
                LOGGER.error(
                    "Failed to start actor '%s' (role=%s) in trial '%s': %s",
                    actor_rt.actor_id,
                    role.value,
                    runtime.spec.trial_id,
                    result,
                    exc_info=result,
                )
            else:
                actor_rt.phase = ActorPhase.RUNNING
                LOGGER.debug(
                    "Successfully started actor '%s' (role=%s) in trial '%s'",
                    actor_rt.actor_id,
                    role.value,
                    runtime.spec.trial_id,
                )
        if failures:
            actor_ids = ", ".join(actor.actor_id for actor, _ in failures)
            raise ActorLifecycleError(
                f"failed to start actors: {actor_ids}"
            ) from failures[0][1]

    async def _stop_role(
        self, runtime: TrialRuntime, role: ActorRole
    ) -> list[Exception]:
        selected = [
            actor_rt
            for actor_rt in runtime.actors.values()
            if actor_rt.role is role
            and actor_rt.phase in {ActorPhase.RUNNING, ActorPhase.STARTING}
        ]
        if not selected:
            return []
        for actor_rt in selected:
            actor_rt.phase = ActorPhase.STOPPING
        results = await asyncio.gather(
            *(actor_rt.handler.stop() for actor_rt in selected),
            return_exceptions=True,
        )
        errors: list[Exception] = []
        for actor_rt, result in zip(selected, results):
            if isinstance(result, Exception):
                actor_rt.phase = ActorPhase.FAILED
                actor_rt.last_error = result
                errors.append(result)
            else:
                actor_rt.phase = ActorPhase.STOPPED
        return errors

    async def _stop_started_actors(self, runtime: TrialRuntime) -> None:
        for role in self._STOP_ORDER:
            await self._stop_role(runtime, role)

        # Also call cleanup if startup function was called (to stop stores/sessions)
        context = getattr(runtime, "_context", None)
        cleanup_fn = getattr(context, "cleanup", None) if context else None
        if cleanup_fn and callable(cleanup_fn):
            LOGGER.debug(
                "Calling cleanup function after startup failure for trial '%s'",
                runtime.spec.trial_id,
            )
            try:
                if asyncio.iscoroutinefunction(cleanup_fn):
                    await cleanup_fn()
                else:
                    cleanup_fn()
            except Exception as exc:
                LOGGER.warning(
                    "Error during cleanup after startup failure for trial '%s': %s",
                    runtime.spec.trial_id,
                    exc,
                )

    def _build_trial_status(self, runtime: TrialRuntime) -> TrialStatus:
        actors = tuple(
            sorted(
                (
                    ActorStatus(
                        actor_id=actor_rt.actor_id,
                        role=actor_rt.role,
                        phase=actor_rt.phase,
                        last_error=(
                            str(actor_rt.last_error) if actor_rt.last_error else None
                        ),
                    )
                    for actor_rt in runtime.actors.values()
                ),
                key=lambda status: status.actor_id,
            )
        )
        return TrialStatus(
            trial_id=runtime.spec.trial_id,
            phase=runtime.phase,
            actors=actors,
            metadata=asdict(runtime.spec.metadata),
            last_error=str(runtime.last_error) if runtime.last_error else None,
        )

    def _persist_trial_status(self, runtime: TrialRuntime, status: TrialStatus) -> None:
        runtime.record.last_status = status
        self._catalog[runtime.spec.trial_id] = runtime.record
        self._store.upsert_trial_record(runtime.record)

    def _status_from_record(self, record: TrialRecord) -> TrialStatus:
        if record.last_status is not None:
            return record.last_status
        return TrialStatus(
            trial_id=record.trial_id,
            phase=TrialPhase.INITIALIZED,
            actors=tuple(),
            metadata=asdict(record.spec.metadata),
            last_error=None,
        )

    def _normalize_spec(self, spec: TrialSpec[MetadataT]) -> TrialSpec[MetadataT]:
        return TrialSpec(
            trial_id=spec.trial_id,
            metadata=spec.metadata,  # Immutable dataclass, no copy needed
            operators=tuple(
                replace(operator, resume_state=None) for operator in spec.operators
            ),
            agents=tuple(replace(agent, resume_state=None) for agent in spec.agents),
            data_streams=tuple(
                replace(stream, resume_state=None) for stream in spec.data_streams
            ),
            social_board=replace(spec.social_board, resume_state=None)
            if spec.social_board is not None
            else None,
            resume_from_checkpoint_id=None,
            resume_from_latest=False,
            builder_name=spec.builder_name,
        )

    def _spec_with_resume_state(
        self, spec: TrialSpec[MetadataT], checkpoint: TrialCheckpoint | None
    ) -> TrialSpec[MetadataT]:
        if checkpoint is None:
            return spec
        state_map = checkpoint.actor_states
        return TrialSpec(
            trial_id=spec.trial_id,
            metadata=spec.metadata,  # Immutable dataclass, no copy needed
            operators=tuple(
                replace(operator, resume_state=state_map.get(operator.actor_id))
                for operator in spec.operators
            ),
            agents=tuple(
                replace(agent, resume_state=state_map.get(agent.actor_id))
                for agent in spec.agents
            ),
            data_streams=tuple(
                replace(stream, resume_state=state_map.get(stream.actor_id))
                for stream in spec.data_streams
            ),
            social_board=replace(
                spec.social_board,
                resume_state=state_map.get(spec.social_board.actor_id),
            )
            if spec.social_board is not None
            else None,
            resume_from_checkpoint_id=None,
            resume_from_latest=False,
            builder_name=spec.builder_name,
        )

    def _resolve_resume_checkpoint(
        self, trial_id: str, checkpoint_id: str | None
    ) -> TrialCheckpoint | None:
        if checkpoint_id is not None:
            LOGGER.info(
                "resolving checkpoint '%s' for trial '%s'",
                checkpoint_id,
                trial_id,
            )
            return self._store.load_checkpoint(checkpoint_id)
        summaries = self._store.list_checkpoints(trial_id)
        if not summaries:
            LOGGER.info("no checkpoints available for trial '%s'", trial_id)
            return None
        latest = max(summaries, key=lambda summary: summary.created_at)
        LOGGER.info(
            "resolving latest checkpoint '%s' for trial '%s'",
            latest.checkpoint_id,
            trial_id,
        )
        return self._store.load_checkpoint(latest.checkpoint_id)

    def _apply_resume_from_spec(self, spec: TrialSpec) -> TrialSpec:
        checkpoint_id = spec.resume_from_checkpoint_id
        latest = spec.resume_from_latest and not checkpoint_id
        if not checkpoint_id and not latest:
            return spec
        if checkpoint_id:
            LOGGER.info(
                "trial '%s' requested resume from checkpoint '%s' via spec",
                spec.trial_id,
                checkpoint_id,
            )
        elif latest:
            LOGGER.info(
                "trial '%s' requested resume from latest checkpoint via spec",
                spec.trial_id,
            )
        checkpoint = self._resolve_resume_checkpoint(
            spec.trial_id,
            checkpoint_id if checkpoint_id else None,
        )
        if checkpoint is None:
            raise OrchestratorError(
                f"trial '{spec.trial_id}' requested latest checkpoint but none exist"
            )
        resumed_spec = self._spec_with_resume_state(spec, checkpoint)
        resumed_spec.resume_from_checkpoint_id = None
        resumed_spec.resume_from_latest = False
        return resumed_spec

    def _require_runtime(self, trial_id: str) -> TrialRuntime:
        try:
            return self._trials[trial_id]
        except KeyError as exc:
            raise TrialNotFoundError(f"trial '{trial_id}' does not exist") from exc

    def _emit_actor_registration_span(
        self,
        trial_id: str,
        actor_id: str,
        actor_type: str,
        config: Any,
    ) -> None:
        """Emit a registration span for an actor to the OTel exporter."""
        from ._tracing import emit_span, convert_actor_registration_to_span

        # Extract metadata from config
        metadata: dict[str, Any] = {}
        if isinstance(config, dict):
            metadata = {
                "name": config.get("name", config.get("actor_id", actor_id)),
                "model": config.get("model"),
                "model_provider": config.get("model_provider"),
                "system_prompt": config.get("system_prompt"),
                "tools": config.get("tools"),
                "source_type": config.get("source_type"),
            }
        else:
            metadata = {"name": actor_id}

        # Remove None values
        metadata = {k: v for k, v in metadata.items() if v is not None}

        span = convert_actor_registration_to_span(
            trial_id=trial_id,
            actor_id=actor_id,
            actor_type=actor_type,
            metadata=metadata,
        )
        emit_span(span)

    def _emit_trial_lifecycle_span(
        self,
        trial_id: str,
        phase: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Emit a trial lifecycle span (started/stopped) to the OTel exporter.

        Args:
            trial_id: The trial ID
            phase: Either "started" or "stopped"
            metadata: Optional metadata to include in the span
        """
        from ._tracing import emit_span, SpanData
        from uuid import uuid4
        import time

        now_us = int(time.time() * 1_000_000)
        span_id = uuid4().hex[:16]

        tags: dict[str, Any] = {
            "dojozero.trial.id": trial_id,
            "dojozero.trial.phase": phase,
        }
        if metadata:
            for key, value in metadata.items():
                if value is not None:
                    tags[f"trial.{key}"] = value

        span = SpanData(
            trace_id=trial_id,
            span_id=span_id,
            operation_name=f"trial.{phase}",
            start_time=now_us,
            duration=0,
            tags=tags,
        )
        emit_span(span)
        LOGGER.debug("Emitted trial.%s span for trial '%s'", phase, trial_id)

    def _emit_agents_initialize_span(
        self,
        trial_id: str,
        spec: TrialSpec,
    ) -> None:
        """Emit a batch agent initialization span with all agent metadata.

        This emits a single 'agent.agent_initialize' span containing an AgentList
        with all agent metadata (persona, model, system_prompt, cdn_url, etc.).
        """
        from ._tracing import emit_span, SpanData
        from dojozero.betting._models import AgentInfo, AgentList
        from uuid import uuid4
        import time
        import json

        # Build AgentInfo list from agent specs
        agent_infos: list[AgentInfo] = []

        for agent_spec in spec.agents:
            config = agent_spec.config or {}
            llm_config = config.get("llm", {})

            agent_info = AgentInfo(
                agent_id=agent_spec.actor_id,
                persona=config.get("persona", ""),
                model=llm_config.get("model_name", ""),
                model_display_name=config.get(
                    "model_display_name", llm_config.get("model_display_name", "")
                )
                or "",
                system_prompt=config.get("sys_prompt", ""),
                cdn_url=config.get("cdn_url", llm_config.get("cdn_url", "")) or "",
            )
            agent_infos.append(agent_info)

        agent_list = AgentList(agents=agent_infos)

        # Create span
        now_us = int(time.time() * 1_000_000)
        span_id = uuid4().hex[:16]

        # Serialize AgentList to tags
        tags: dict[str, Any] = {
            "agents": json.dumps([a.model_dump() for a in agent_list.agents]),
        }

        span = SpanData(
            trace_id=trial_id,
            span_id=span_id,
            operation_name="agent.agent_initialize",
            start_time=now_us,
            duration=0,
            tags=tags,
        )
        emit_span(span)
        LOGGER.debug(
            "Emitted agent.agent_initialize span for trial '%s' with %d agents",
            trial_id,
            len(agent_infos),
        )


__all__ = [
    "BaseTrialMetadata",
    "CheckpointNotFoundError",
    "CheckpointSummary",
    "ActorPhase",
    "ActorRole",
    "ActorSpec",
    "AgentSpec",
    "ActorStatus",
    "ActorLifecycleError",
    "MetadataT",
    "TrialOrchestrator",
    "OrchestratorError",
    "OrchestratorStore",
    "DataStreamSpec",
    "InMemoryOrchestratorStore",
    "OperatorSpec",
    "TrialCheckpoint",
    "TrialExistsError",
    "TrialNotFoundError",
    "TrialPhase",
    "TrialRecord",
    "TrialSpec",
    "TrialStatus",
]
