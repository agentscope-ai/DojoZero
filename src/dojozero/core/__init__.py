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
from ._dashboard import (
    ActorLifecycleError,
    ActorPhase,
    ActorRole,
    ActorSpec,
    AgentSpec,
    ActorStatus,
    CheckpointNotFoundError,
    CheckpointSummary,
    DataStreamSpec,
    Dashboard,
    DashboardError,
    DashboardStore,
    InMemoryDashboardStore,
    OperatorSpec,
    TrialCheckpoint,
    TrialExistsError,
    TrialNotFoundError,
    TrialPhase,
    TrialRecord,
    TrialSpec,
    TrialStatus,
)
from ._filesystem_dashboard_store import FileSystemDashboardStore
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
from ._dashboard_server import (
    DashboardServerState,
    create_dashboard_app,
    run_dashboard_server,
)
from ._arena_server import (
    ArenaServerState,
    SpanBroadcaster,
    WSMessageType,
    create_arena_app,
    run_arena_server,
)
from ._tracing import (
    JaegerTraceReader,
    SpanData,
    TraceReader,
    convert_agent_message_to_span,
    convert_checkpoint_event_to_span,
    create_span_from_event,
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
    # Dashboard
    "CheckpointNotFoundError",
    "CheckpointSummary",
    "Dashboard",
    "DashboardError",
    "DashboardStore",
    "FileSystemDashboardStore",
    "InMemoryDashboardStore",
    "TrialBuilderDefinition",
    "TrialBuilderFn",
    "TrialBuilderNotFoundError",
    "TrialBuilderRegistryError",
    "TrialCheckpoint",
    "TrialExistsError",
    "TrialNotFoundError",
    "TrialPhase",
    "TrialRecord",
    "TrialSpec",
    "TrialStatus",
    # Dashboard Server
    "DashboardServerState",
    "create_dashboard_app",
    "run_dashboard_server",
    # Arena Server
    "ArenaServerState",
    "SpanBroadcaster",
    "WSMessageType",
    "create_arena_app",
    "run_arena_server",
    # Trace Store
    "JaegerTraceReader",
    "SpanData",
    "TraceReader",
    "convert_agent_message_to_span",
    "convert_checkpoint_event_to_span",
    "create_span_from_event",
    "load_spans_from_checkpoint",
    # Runtime
    "LocalActorHandler",
    "LocalActorRuntimeProvider",
    # Registry
    "ParamModelT",
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
]
