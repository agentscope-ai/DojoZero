"""Public exports for the :mod:`dojozero.core` package."""

from ._actors import (
    Agent,
    Actor,
    ActorConfig,
    ActorState,
    DataStream,
    Operator,
)
from ._base import ActorBase, AgentBase, DataStreamBase, OperatorBase
from ._metadata import (
    BaseTrialMetadata,
    MetadataT,
)
from ._trial_orchestrator import (
    ActorLifecycleError,
    ActorPhase,
    ActorRole,
    ActorSpec,
    AgentSpec,
    ActorStatus,
    CheckpointNotFoundError,
    CheckpointSummary,
    DataStreamSpec,
    InMemoryOrchestratorStore,
    OperatorSpec,
    OrchestratorError,
    OrchestratorStore,
    TrialCheckpoint,
    TrialExistsError,
    TrialNotFoundError,
    TrialOrchestrator,
    TrialPhase,
    TrialRecord,
    TrialSpec,
    TrialStatus,
)
from ._filesystem_orchestrator_store import FileSystemOrchestratorStore

from ._runtime import (
    ActorHandler,
    ActorRuntimeProvider,
    LocalActorHandler,
    LocalActorRuntimeProvider,
)
from ._registry import (
    ParamModelT,
    TrialBuilderDefinition,
    TrialBuilderNotFoundError,
    TrialBuilderRegistryError,
    TrialBuilderFn,
    get_trial_builder_definition,
    list_trial_builders,
    register_trial_builder,
    unregister_trial_builder,
)
from ._models import (
    AgentAction,
    AgentInfo,
    LeaderboardEntry,
    BettingResultSpan,
    SpanModel,
    TrialLifecycleSpan,
    deserialize_span,
    serialize_span_for_ws,
)
from ._tracing import (
    JaegerTraceReader,
    SpanData,
    TraceReader,
    convert_agent_message_to_span,
    convert_checkpoint_event_to_span,
    create_span_from_event,
    deserialize_event_from_span,
    load_spans_from_checkpoint,
)
from ._types import (
    RuntimeContext,
    JSONDict,
    JSONPrimitive,
    JSONValue,
    QueryResult,
    StreamEvent,
)

__all__ = [
    # Actors
    "Actor",
    "ActorBase",
    "ActorConfig",
    "ActorHandler",
    "ActorLifecycleError",
    "ActorPhase",
    "ActorRole",
    "ActorRuntimeProvider",
    "ActorSpec",
    "ActorState",
    "ActorStatus",
    "Agent",
    "AgentBase",
    "AgentSpec",
    "DataStream",
    "DataStreamBase",
    "DataStreamSpec",
    "Operator",
    "OperatorBase",
    "OperatorSpec",
    # Orchestrator
    "CheckpointNotFoundError",
    "CheckpointSummary",
    "FileSystemOrchestratorStore",
    "InMemoryOrchestratorStore",
    "OrchestratorError",
    "OrchestratorStore",
    "TrialOrchestrator",
    "TrialCheckpoint",
    "TrialExistsError",
    "TrialNotFoundError",
    "TrialPhase",
    "TrialRecord",
    "TrialSpec",
    "TrialStatus",
    # API Models
    "AgentAction",
    "AgentInfo",
    "LeaderboardEntry",
    # Span Models
    "BettingResultSpan",
    "SpanModel",
    "TrialLifecycleSpan",
    "deserialize_span",
    "serialize_span_for_ws",
    # Trace Store
    "JaegerTraceReader",
    "SpanData",
    "TraceReader",
    "convert_agent_message_to_span",
    "convert_checkpoint_event_to_span",
    "create_span_from_event",
    "deserialize_event_from_span",
    "load_spans_from_checkpoint",
    # Runtime
    "LocalActorHandler",
    "LocalActorRuntimeProvider",
    # Registry
    "ParamModelT",
    "TrialBuilderDefinition",
    "TrialBuilderFn",
    "TrialBuilderNotFoundError",
    "TrialBuilderRegistryError",
    "get_trial_builder_definition",
    "list_trial_builders",
    "register_trial_builder",
    "unregister_trial_builder",
    # Types
    "RuntimeContext",
    "JSONDict",
    "JSONPrimitive",
    "JSONValue",
    "QueryResult",
    "StreamEvent",
    # Metadata
    "BaseTrialMetadata",
    "MetadataT",
]
