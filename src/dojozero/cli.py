"""DojoZero CLI for running trials today and hosting a FastAPI server soon."""

import argparse
import asyncio
import importlib
import logging
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from typing import Any, Awaitable, Callable, Iterable, Mapping, MutableMapping, Sequence
from uuid import uuid4

import yaml
from pydantic import ValidationError

from dojozero.core import (
    RuntimeContext,
    TrialOrchestrator,
    OrchestratorError,
    FileSystemOrchestratorStore,
    LocalActorRuntimeProvider,
    TrialBuilderDefinition,
    TrialSpec,
    TrialStatus,
    get_trial_builder_definition,
    list_trial_builders,
)
from dojozero.data import DataHub
from dojozero.data.espn import get_espn_game_url
from dojozero.data.polymarket import PolymarketAPI
from dojozero.utils import utc_iso_to_local
from dojozero.core import TrialBuilderNotFoundError as _TrialBuilderNotFoundError
from dojozero.dashboard_server import InitialTrialSourceDict

try:  # Optional Ray dependency
    from dojozero.ray_runtime import RayActorRuntimeProvider
except ImportError:  # pragma: no cover - ray is optional
    RayActorRuntimeProvider = None  # type: ignore[assignment]

DEFAULT_IMPORTS: tuple[str, ...] = (
    "dojozero.nba",
    "dojozero.nfl",
)
DEFAULT_STORE_DIRECTORY: str = "./dojozero-store"
DEFAULT_RUNTIME_PROVIDER: str = "local"

RUN_USAGE_EXAMPLES = dedent(
    """
     Examples:
        1. Create a new trial
            dojo0 run --params sample_trial.yaml --trial-id sample-trial

        2. Resume a trial from the latest checkpoint
            dojo0 run --trial-id sample-trial --resume-latest

        3. Use a custom store directory and Ray runtime
            dojo0 run --store-directory ./my-store --runtime-provider ray --params sample_trial.yaml
     """
).strip()

LOGGER = logging.getLogger("dojozero.cli")


class DojoZeroCLIError(RuntimeError):
    """Raised for CLI usage errors that should exit with status 1."""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dojo0",
        description="Run DojoZero trials in standalone mode.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (default: INFO).",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Launch or resume a trial",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=RUN_USAGE_EXAMPLES,
    )
    run_parser.add_argument(
        "--params",
        type=Path,
        help="Path to the trial-builder params YAML (required when creating a new trial).",
    )
    run_parser.add_argument(
        "--trial-id",
        help="Override the trial id (defaults to a random UUID).",
    )
    run_parser.add_argument(
        "--checkpoint-id",
        help="Resume from a specific checkpoint id.",
    )
    run_parser.add_argument(
        "--resume-latest",
        action="store_true",
        help="Resume from the latest checkpoint for the trial when no spec is provided.",
    )
    run_parser.add_argument(
        "--server",
        help="Submit trial to a running Dashboard Server (e.g., http://localhost:8000). "
        "If not provided, runs the trial locally.",
    )
    run_parser.add_argument(
        "--trace-backend",
        dest="trace_backend",
        choices=["jaeger", "sls"],
        help="Trace backend type for local run. Use 'jaeger' for local development, "
        "'sls' for Alibaba Cloud Simple Log Service (uses env vars). "
        "Ignored when --server is specified.",
    )
    run_parser.add_argument(
        "--trace-ingest-endpoint",
        dest="trace_ingest_endpoint",
        default="http://localhost:4318",
        help="OTLP endpoint for Jaeger trace ingestion (default: http://localhost:4318). "
        "Only used when --trace-backend=jaeger.",
    )
    run_parser.add_argument(
        "--service-name",
        dest="service_name",
        default="dojozero",
        help="Service name for trace export (default: dojozero).",
    )
    run_parser.add_argument(
        "--store-directory",
        dest="store_directory",
        type=Path,
        default=None,
        help=f"Directory for filesystem store (default: {DEFAULT_STORE_DIRECTORY}).",
    )
    run_parser.add_argument(
        "--runtime-provider",
        dest="runtime_provider",
        choices=["local", "ray"],
        default=DEFAULT_RUNTIME_PROVIDER,
        help=f"Runtime provider (default: {DEFAULT_RUNTIME_PROVIDER}).",
    )
    run_parser.add_argument(
        "--ray-config",
        dest="ray_config",
        type=Path,
        help="Path to Ray runtime configuration YAML file (only used with --runtime-provider ray).",
    )
    run_parser.add_argument(
        "--disable-gateway",
        dest="disable_gateway",
        action="store_true",
        help="Disable HTTP gateway for external agents (gateway is enabled by default).",
    )
    run_parser.add_argument(
        "--gateway-port",
        dest="gateway_port",
        type=int,
        default=8080,
        help="Port for the HTTP gateway (default: 8080).",
    )
    run_parser.add_argument(
        "--gateway-host",
        dest="gateway_host",
        default="127.0.0.1",
        help="Host for the HTTP gateway (default: 127.0.0.1).",
    )

    subparsers.add_parser(
        "list-builders",
        help="List all registered trial builders",
    )

    get_parser = subparsers.add_parser(
        "get-builder",
        help="Show schema information for a trial builder",
    )
    get_parser.add_argument("name", help="Registered builder name")
    get_parser.add_argument(
        "--create-example-params",
        nargs="?",
        const="",
        metavar="PATH",
        help="Write an example YAML spec to PATH (defaults to <name>_example.yaml)",
    )

    backtest_parser = subparsers.add_parser(
        "backtest",
        help="Run backtesting from historical event files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Run backtesting from JSONL event files.\n\n"
        "Supports multiple files via glob patterns:\n"
        "  Local files:  outputs/2025-01-*/*.jsonl\n\n"
        "Files are processed sequentially in sorted order.",
    )
    backtest_parser.add_argument(
        "--events",
        type=str,
        nargs="+",
        required=True,
        dest="event_files",
        help="Path(s) to JSONL event file(s). Supports glob patterns (e.g., 'outputs/*/*.jsonl'). "
        "Multiple patterns can be specified.",
    )
    backtest_parser.add_argument(
        "--params",
        type=Path,
        required=True,
        help="Path to the trial-builder params YAML (required for agent/stream setup)",
    )
    backtest_parser.add_argument(
        "--trial-id",
        help="Override the trial id (defaults to a random UUID)",
    )
    backtest_parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        dest="backtest_speed",
        help="Backtest speed multiplier (e.g., 2.0 for 2x speed, 0.5 for half speed). Default: 1.0 (real-time)",
    )
    backtest_parser.add_argument(
        "--max-sleep",
        type=float,
        default=20.0,
        dest="backtest_max_sleep",
        help="Maximum sleep time in seconds between events (caps long delays). Default: 20.0 seconds",
    )
    backtest_parser.add_argument(
        "--emit-traces",
        action="store_true",
        default=False,
        dest="emit_traces",
        help="Emit data events to the trace backend (SLS/Jaeger) with rebased timestamps "
        "so replay trials are visible in Arena UI. Requires --trace-backend.",
    )
    backtest_parser.add_argument(
        "--server",
        help="Submit backtest to a running Dashboard Server (e.g., http://localhost:8000). "
        "The server must have access to the event file at the same path.",
    )
    backtest_parser.add_argument(
        "--trace-backend",
        dest="trace_backend",
        choices=["jaeger", "sls"],
        help="Trace backend type for local backtest. Use 'jaeger' for local development, "
        "'sls' for Alibaba Cloud Simple Log Service (uses env vars). "
        "Ignored when --server is specified.",
    )
    backtest_parser.add_argument(
        "--trace-ingest-endpoint",
        dest="trace_ingest_endpoint",
        default="http://localhost:4318",
        help="OTLP endpoint for Jaeger trace ingestion (default: http://localhost:4318). "
        "Only used when --trace-backend=jaeger.",
    )
    backtest_parser.add_argument(
        "--service-name",
        dest="service_name",
        default="dojozero",
        help="Service name for trace export (default: dojozero).",
    )
    backtest_parser.add_argument(
        "--store-directory",
        dest="store_directory",
        type=Path,
        default=None,
        help=f"Directory for filesystem store (default: {DEFAULT_STORE_DIRECTORY}).",
    )
    backtest_parser.add_argument(
        "--runtime-provider",
        dest="runtime_provider",
        choices=["local", "ray"],
        default=DEFAULT_RUNTIME_PROVIDER,
        help=f"Runtime provider (default: {DEFAULT_RUNTIME_PROVIDER}).",
    )
    backtest_parser.add_argument(
        "--ray-config",
        dest="ray_config",
        type=Path,
        help="Path to Ray runtime configuration YAML file (only used with --runtime-provider ray).",
    )

    # Dashboard Server command
    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the Dashboard Server for trial management",
        description="Launch the Dashboard Server for running trials and exposing trace APIs.",
    )
    serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address to bind to (default: 127.0.0.1).",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to listen on (default: 8000).",
    )
    serve_parser.add_argument(
        "--trace-backend",
        dest="trace_backend",
        choices=["jaeger", "sls"],
        help="Trace backend type. Use 'jaeger' for local development, "
        "'sls' for Alibaba Cloud Simple Log Service (uses env vars).",
    )
    serve_parser.add_argument(
        "--service-name",
        dest="service_name",
        default="dojozero",
        help="Service name for trace export (default: dojozero). Use to isolate multiple dashboard or arena servers.",
    )
    serve_parser.add_argument(
        "--trace-ingest-endpoint",
        dest="trace_ingest_endpoint",
        default="http://localhost:4318",
        help="OTLP endpoint for Jaeger trace ingestion (default: http://localhost:4318). "
        "Only used when --trace-backend=jaeger.",
    )
    serve_parser.add_argument(
        "--trial-source",
        dest="trial_sources",
        action="append",
        default=[],
        help="Path or glob pattern for trial source YAML files (repeatable). "
        "Enables automatic scheduling for the specified sports/scenarios. "
        "Requires filesystem store to be configured.",
    )
    serve_parser.add_argument(
        "--no-auto-resume",
        dest="no_auto_resume",
        action="store_true",
        help="Disable automatic resuming of interrupted trials from previous shutdown.",
    )
    serve_parser.add_argument(
        "--stale-threshold-hours",
        dest="stale_threshold_hours",
        type=float,
        default=24.0,
        help="Skip resuming trials with checkpoints older than this (hours). Default: 24.0",
    )
    serve_parser.add_argument(
        "--store-directory",
        dest="store_directory",
        type=Path,
        default=None,
        help=f"Directory for filesystem store (default: {DEFAULT_STORE_DIRECTORY}).",
    )
    serve_parser.add_argument(
        "--runtime-provider",
        dest="runtime_provider",
        choices=["local", "ray"],
        default=DEFAULT_RUNTIME_PROVIDER,
        help=f"Runtime provider (default: {DEFAULT_RUNTIME_PROVIDER}).",
    )
    serve_parser.add_argument(
        "--ray-config",
        dest="ray_config",
        type=Path,
        help="Path to Ray runtime configuration YAML file (only used with --runtime-provider ray).",
    )
    serve_parser.add_argument(
        "--disable-gateway",
        dest="disable_gateway",
        action="store_true",
        help="Disable HTTP gateway for external agents (gateway is enabled by default).",
    )

    # Arena Server command
    arena_parser = subparsers.add_parser(
        "arena",
        help="Start the Arena Server for WebSocket streaming",
        description="Launch the Arena Server for real-time span streaming to browsers.",
    )
    arena_parser.add_argument(
        "--config",
        dest="config_file",
        type=Path,
        help="Path to YAML configuration file. CLI args override config file values.",
    )
    arena_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address to bind to (default: 127.0.0.1).",
    )
    arena_parser.add_argument(
        "--port",
        type=int,
        default=3001,
        help="Port to listen on (default: 3001).",
    )
    arena_parser.add_argument(
        "--trace-backend",
        dest="trace_backend",
        choices=["jaeger", "sls"],
        required=True,
        help="Trace backend type. Use 'jaeger' for local development, "
        "'sls' for Alibaba Cloud Simple Log Service (uses env vars).",
    )
    arena_parser.add_argument(
        "--service-name",
        dest="service_name",
        default="dojozero",
        help="Service name for trace queries (default: dojozero). Use to isolate multiple arena servers.",
    )
    arena_parser.add_argument(
        "--trace-query-endpoint",
        dest="trace_query_endpoint",
        default="http://localhost:16686",
        help="Jaeger Query API endpoint (default: http://localhost:16686). "
        "Only used when --trace-backend=jaeger.",
    )
    arena_parser.add_argument(
        "--static-dir",
        dest="static_dir",
        type=Path,
        default=None,
        help="Path to built static assets to serve (optional).",
    )
    arena_parser.add_argument(
        "--redis-url",
        dest="redis_url",
        default=None,
        help="Redis URL for fast startup (e.g., redis://host:6379/0). "
        "Can also be set via DOJOZERO_REDIS_URL env var.",
    )

    # Sync Service command
    sync_parser = subparsers.add_parser(
        "sync-service",
        help="Start the SLS to Redis sync service",
        description="Launch the Sync Service that continuously syncs data from SLS to Redis. "
        "Arena Server can then use Redis for fast startup.",
    )
    sync_parser.add_argument(
        "--redis-url",
        dest="redis_url",
        default=None,
        help="Redis URL (e.g., redis://host:6379/0). Required if DOJOZERO_REDIS_URL not set.",
    )
    sync_parser.add_argument(
        "--sync-interval",
        dest="sync_interval",
        type=float,
        default=5.0,
        help="Sync interval in seconds (default: 5.0).",
    )
    sync_parser.add_argument(
        "--lookback-days",
        dest="lookback_days",
        type=int,
        default=90,
        help="Lookback period in days for trial data (default: 90).",
    )
    sync_parser.add_argument(
        "--service-name",
        dest="service_name",
        default="dojozero",
        help="Service name for SLS queries (default: dojozero).",
    )

    # List trials command
    list_trials_parser = subparsers.add_parser(
        "list-trials",
        help="List trials from a running Dashboard Server",
        description="Fetch and display all trials (scheduled and running) from a Dashboard Server. "
        "Use --running-only to show only running trials, or --scheduled-only for scheduled trials.",
    )
    list_trials_parser.add_argument(
        "--server",
        default="http://localhost:8000",
        help="Dashboard Server URL (default: http://localhost:8000).",
    )
    list_trials_parser.add_argument(
        "--scheduled",
        "--scheduled-only",
        action="store_true",
        dest="scheduled_only",
        help="List only auto-scheduled trials (from trial sources).",
    )
    list_trials_parser.add_argument(
        "--running-only",
        action="store_true",
        help="List only running/queued trials.",
    )
    list_trials_parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Output as raw JSON instead of pretty-printed table.",
    )
    list_trials_parser.add_argument(
        "--show-links",
        action="store_true",
        help="Show ESPN and Polymarket links for each game.",
    )
    list_trials_parser.add_argument(
        "--include-finished",
        action="store_true",
        help="Include completed/cancelled/failed scheduled trials.",
    )

    # List trial sources command
    list_sources_parser = subparsers.add_parser(
        "list-sources",
        help="List trial sources from a running Dashboard Server",
        description="Fetch and display registered trial sources from a Dashboard Server.",
    )
    list_sources_parser.add_argument(
        "--server",
        default="http://localhost:8000",
        help="Dashboard Server URL (default: http://localhost:8000).",
    )
    list_sources_parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Output as raw JSON instead of pretty-printed table.",
    )

    # Remove trial source command
    remove_source_parser = subparsers.add_parser(
        "remove-source",
        help="Remove a trial source from a running Dashboard Server",
        description="Unregister a trial source from the Dashboard Server.",
    )
    remove_source_parser.add_argument(
        "source_id",
        help="The source ID to remove.",
    )
    remove_source_parser.add_argument(
        "--server",
        default="http://localhost:8000",
        help="Dashboard Server URL (default: http://localhost:8000).",
    )

    # Clear scheduled trials command
    clear_schedules_parser = subparsers.add_parser(
        "clear-schedules",
        help="Clear all scheduled trials from a running Dashboard Server",
        description="Cancel and remove all scheduled trials from the Dashboard Server.",
    )
    clear_schedules_parser.add_argument(
        "--server",
        default="http://localhost:8000",
        help="Dashboard Server URL (default: http://localhost:8000).",
    )
    clear_schedules_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt.",
    )

    # Agent management commands
    agents_parser = subparsers.add_parser(
        "agents",
        help="Manage agent API keys for authentication",
        description="Manage agent API keys stored in agent_keys.yaml.",
    )
    agents_subparsers = agents_parser.add_subparsers(
        dest="agents_command", required=True
    )

    # agents add
    agents_add_parser = agents_subparsers.add_parser(
        "add",
        help="Register a new agent and generate API key",
    )
    agents_add_parser.add_argument(
        "--id",
        required=True,
        help="Unique agent identifier (used for cross-trial aggregation).",
    )
    agents_add_parser.add_argument(
        "--name",
        help="Human-readable display name for the agent.",
    )
    agents_add_parser.add_argument(
        "--persona",
        help="Persona tag for frontend display (e.g., 'degen', 'whale', 'shark').",
    )
    agents_add_parser.add_argument(
        "--model",
        help="Model identifier (e.g., 'gpt-4', 'claude-3').",
    )
    agents_add_parser.add_argument(
        "--model-display-name",
        help="Human-readable model name for frontend (e.g., 'GPT-4 Turbo').",
    )
    agents_add_parser.add_argument(
        "--cdn-url",
        help="Avatar image URL for frontend display.",
    )
    agents_add_parser.add_argument(
        "--store",
        default=DEFAULT_STORE_DIRECTORY,
        help=f"Store directory containing agent_keys.yaml (default: {DEFAULT_STORE_DIRECTORY}).",
    )

    # agents list
    agents_list_parser = agents_subparsers.add_parser(
        "list",
        help="List all registered agents",
    )
    agents_list_parser.add_argument(
        "--store",
        default=DEFAULT_STORE_DIRECTORY,
        help=f"Store directory containing agent_keys.yaml (default: {DEFAULT_STORE_DIRECTORY}).",
    )
    agents_list_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON.",
    )

    # agents remove
    agents_remove_parser = agents_subparsers.add_parser(
        "remove",
        help="Remove an agent and revoke its API key",
    )
    agents_remove_parser.add_argument(
        "agent_id",
        help="Agent ID to remove.",
    )
    agents_remove_parser.add_argument(
        "--store",
        default=DEFAULT_STORE_DIRECTORY,
        help=f"Store directory containing agent_keys.yaml (default: {DEFAULT_STORE_DIRECTORY}).",
    )
    agents_remove_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt.",
    )

    return parser


def _import_modules(modules: Iterable[str]) -> None:
    for name in modules:
        if not name:
            continue
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on environment
            raise DojoZeroCLIError(f"failed to import module '{name}': {exc}") from exc


def _load_yaml_mapping(path: Path, *, label: str) -> MutableMapping[str, Any]:
    if not path.exists():
        raise DojoZeroCLIError(f"{label} file '{path}' does not exist")
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, MutableMapping):
        raise DojoZeroCLIError(f"{label} file must contain a mapping at the top level")
    return payload


def _gather_imports(payload: Mapping[str, Any] | None) -> Sequence[str]:
    if not payload:
        return ()
    imports = payload.get("imports")
    if imports is None:
        return ()
    if isinstance(imports, str):
        return (imports,)
    if isinstance(imports, Sequence):
        normalized: list[str] = []
        for item in imports:
            if not isinstance(item, str):
                raise DojoZeroCLIError("entries in 'imports' must be strings")
            normalized.append(item)
        return tuple(normalized)
    raise DojoZeroCLIError("'imports' must be a string or sequence of strings")


async def _prepare_trial_spec(trial_id: str, payload: Mapping[str, Any]) -> TrialSpec:
    if not trial_id:
        raise DojoZeroCLIError("trial_id must be provided")
    scenario = payload.get("scenario")
    used_legacy_key = False
    if scenario is None and "environment" in payload:
        scenario = payload["environment"]
        used_legacy_key = True
    if not isinstance(scenario, Mapping):
        raise DojoZeroCLIError("spec.scenario must be a mapping")
    if used_legacy_key:
        LOGGER.warning(
            "spec.environment is deprecated; rename the params key to spec.scenario"
        )

    module_name = scenario.get("module")
    if module_name is not None:
        if not isinstance(module_name, str):
            raise DojoZeroCLIError("spec.scenario.module must be a string")
        _import_modules([module_name])

    builder_name = scenario.get("name")
    if not isinstance(builder_name, str) or not builder_name:
        raise DojoZeroCLIError("spec.scenario.name must be a non-empty string")

    builder_config = scenario.get("config") or {}
    if not isinstance(builder_config, Mapping):
        raise DojoZeroCLIError("spec.scenario.config must be a mapping when provided")

    try:
        definition = get_trial_builder_definition(builder_name)
    except _TrialBuilderNotFoundError as exc:
        raise DojoZeroCLIError(str(exc)) from exc
    try:
        # Use build_async to support both sync and async trial builders
        spec = await definition.build_async(trial_id, builder_config)
    except ValidationError as exc:
        raise DojoZeroCLIError(
            f"invalid config for builder '{builder_name}': {exc}"
        ) from exc

    metadata = payload.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, Mapping):
            raise DojoZeroCLIError("spec.metadata must be a mapping when provided")
        # Update metadata fields from user-provided values
        for key, value in metadata.items():
            if hasattr(spec.metadata, key):
                setattr(spec.metadata, key, value)

    resume_cfg = payload.get("resume")
    if resume_cfg is not None:
        if not isinstance(resume_cfg, Mapping):
            raise DojoZeroCLIError("spec.resume must be a mapping when provided")
        checkpoint_id = resume_cfg.get("checkpoint_id")
        latest_flag = resume_cfg.get("latest")
        if checkpoint_id is not None:
            if not isinstance(checkpoint_id, str) or not checkpoint_id:
                raise DojoZeroCLIError(
                    "spec.resume.checkpoint_id must be a non-empty string"
                )
            spec.resume_from_checkpoint_id = checkpoint_id
            spec.resume_from_latest = False
        latest = bool(latest_flag)
        if latest and checkpoint_id is None:
            spec.resume_from_latest = True
        if checkpoint_id is None and not latest:
            raise DojoZeroCLIError(
                "spec.resume must specify checkpoint_id or set latest: true"
            )

    return spec


def _create_store(
    store_directory: Path | None,
) -> FileSystemOrchestratorStore:
    """Create a filesystem store from the store directory.

    Args:
        store_directory: Directory for the store. If None, uses DEFAULT_STORE_DIRECTORY.

    Returns:
        FileSystemOrchestratorStore instance.
    """
    base_path = store_directory if store_directory else Path(DEFAULT_STORE_DIRECTORY)
    return FileSystemOrchestratorStore(base_path)


def _create_runtime_provider(
    runtime_provider: str,
    ray_config_path: Path | None = None,
):
    """Create a runtime provider from CLI options.

    Args:
        runtime_provider: Either "local" or "ray".
        ray_config_path: Optional path to Ray configuration YAML file.

    Returns:
        ActorRuntimeProvider instance.
    """
    if runtime_provider == "local":
        return LocalActorRuntimeProvider()
    if runtime_provider == "ray":
        if RayActorRuntimeProvider is None:
            raise DojoZeroCLIError(
                "ray runtime requested but ray is not installed. "
                "Please install ray dependencies with 'pip install dojozero[ray]'"
            )
        # Load ray config from file if provided
        init_kwargs: dict[str, Any] = {}
        auto_init = True
        if ray_config_path is not None:
            ray_cfg = _load_yaml_mapping(ray_config_path, label="ray-config")
            init_kwargs = dict(ray_cfg.get("init_kwargs") or {})
            auto_init = bool(ray_cfg.get("auto_init", True))
        return RayActorRuntimeProvider(auto_init=auto_init, init_kwargs=init_kwargs)
    raise DojoZeroCLIError(f"unsupported runtime provider '{runtime_provider}'")


def _setup_otel_exporter(
    trace_backend: str | None,
    trace_ingest_endpoint: str | None,
    service_name: str = "dojozero",
) -> Any:
    """Set up OTel exporter based on trace backend configuration.

    Args:
        trace_backend: Backend type ("sls" or "jaeger") or None to disable
        trace_ingest_endpoint: OTLP endpoint for jaeger backend
        service_name: Service name for trace attribution

    Returns:
        Configured OTelSpanExporter instance, or None if disabled
    """
    if not trace_backend:
        LOGGER.info("No trace backend configured - traces will not be exported")
        return None

    from dojozero.core._tracing import (
        OTelSpanExporter,
        SLSLogExporter,
        get_sls_exporter_headers,
        set_otel_exporter,
        set_sls_log_exporter,
    )

    if trace_backend == "sls":
        import os

        # Construct SLS OTLP endpoint from environment variables
        sls_project = os.environ.get("DOJOZERO_SLS_PROJECT", "")
        sls_endpoint = os.environ.get("DOJOZERO_SLS_ENDPOINT", "")
        if not sls_project or not sls_endpoint:
            raise DojoZeroCLIError(
                "SLS trace backend requires DOJOZERO_SLS_PROJECT and "
                "DOJOZERO_SLS_ENDPOINT environment variables"
            )
        otlp_endpoint = f"https://{sls_project}.{sls_endpoint}"
        headers = get_sls_exporter_headers()
        otel_exporter = OTelSpanExporter(
            otlp_endpoint, service_name=service_name, headers=headers
        )
        otel_exporter.start()
        set_otel_exporter(otel_exporter)
        LOGGER.info(
            "OTel exporter configured: %s (backend: sls, service_name: %s)",
            otlp_endpoint,
            service_name,
        )

        # Also initialize SLS Log exporter for flat field indexing
        sls_logstore = os.environ.get("DOJOZERO_SLS_LOGSTORE", "")
        if sls_logstore:
            sls_log_exporter = SLSLogExporter(
                project=sls_project,
                endpoint=sls_endpoint,
                logstore=sls_logstore,
                service_name=service_name,
            )
            sls_log_exporter.start()
            set_sls_log_exporter(sls_log_exporter)
            LOGGER.info(
                "SLS Log exporter configured: %s/%s (flat fields)",
                sls_project,
                sls_logstore,
            )
        else:
            LOGGER.warning(
                "DOJOZERO_SLS_LOGSTORE not set - spans will only be exported via OTLP. "
                "Set DOJOZERO_SLS_LOGSTORE for flat field indexing and better querying."
            )

        return otel_exporter
    elif trace_backend == "jaeger":
        otlp_endpoint = trace_ingest_endpoint or "http://localhost:4318"
        otel_exporter = OTelSpanExporter(
            otlp_endpoint, service_name=service_name, headers=None
        )
        otel_exporter.start()
        set_otel_exporter(otel_exporter)
        LOGGER.info(
            "OTel exporter configured: %s (backend: jaeger, service_name: %s)",
            otlp_endpoint,
            service_name,
        )
        return otel_exporter
    else:
        raise DojoZeroCLIError(f"Unsupported trace backend: {trace_backend}")


def _shutdown_otel_exporter(otel_exporter: Any) -> None:
    """Shutdown OTel and SLS exporters and clear global references.

    Args:
        otel_exporter: The OTelSpanExporter instance to shutdown
    """
    if otel_exporter is None:
        return

    from dojozero.core._tracing import (
        get_sls_log_exporter,
        set_otel_exporter,
        set_sls_log_exporter,
    )

    otel_exporter.shutdown()
    set_otel_exporter(None)
    LOGGER.info("OTel exporter shutdown complete")

    # Shutdown SLS log exporter if configured
    sls_log_exporter = get_sls_log_exporter()
    if sls_log_exporter is not None:
        sls_log_exporter.shutdown()
        set_sls_log_exporter(None)
        LOGGER.info("SLS Log exporter shutdown complete")


def _default_example_filename(builder_name: str) -> str:
    return f"{builder_name.replace('.', '_')}_example.yaml"


def _generate_example_spec(
    builder_name: str, definition: TrialBuilderDefinition
) -> MutableMapping[str, Any]:
    return {
        "scenario": {
            "name": builder_name,
            "config": definition.example_dict(),
        },
    }


def _write_yaml_file(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)


async def _start_gateway_server(
    orchestrator: TrialOrchestrator,
    trial_id: str,
    host: str,
    port: int,
) -> asyncio.Task[None]:
    """Start the gateway server for a running trial.

    Args:
        orchestrator: The trial orchestrator
        trial_id: The trial ID
        host: Gateway host address
        port: Gateway port

    Returns:
        The asyncio task running the gateway server
    """
    import uvicorn

    from dojozero.gateway import create_gateway_app
    from dojozero.betting import BrokerOperator

    # TODO: Refactor to use public API instead of accessing private members (_trials, _context).
    # Add orchestrator.get_trial_context(trial_id) method to expose DataHub and BrokerOperator.
    runtime = orchestrator._trials.get(trial_id)
    if runtime is None:
        raise DojoZeroCLIError(f"Trial '{trial_id}' not found in orchestrator")

    context = runtime._context
    if context is None:
        raise DojoZeroCLIError(f"Trial '{trial_id}' has no runtime context")

    # Get DataHub from context
    if not context.data_hubs:
        raise DojoZeroCLIError(f"Trial '{trial_id}' has no DataHubs")

    # Get the first DataHub (most trials have one)
    hub_id = next(iter(context.data_hubs.keys()))
    data_hub = context.data_hubs[hub_id]

    # Find BrokerOperator from running actors
    broker: BrokerOperator | None = None
    for actor_runtime in runtime.actors.values():
        actor = actor_runtime.instance
        if isinstance(actor, BrokerOperator):
            broker = actor
            break

    if broker is None:
        raise DojoZeroCLIError(
            f"Trial '{trial_id}' has no BrokerOperator. "
            "Gateway requires a trial with betting functionality."
        )

    # Get metadata for the gateway
    metadata: dict[str, Any] = {}
    if hasattr(runtime.spec.metadata, "__dict__"):
        metadata = {
            k: v
            for k, v in vars(runtime.spec.metadata).items()
            if not k.startswith("_")
        }
    elif hasattr(runtime.spec.metadata, "model_dump"):
        metadata = runtime.spec.metadata.model_dump()

    # Create gateway app
    app = create_gateway_app(
        trial_id=trial_id,
        data_hub=data_hub,
        broker=broker,
        metadata=metadata,
    )

    # Create uvicorn config
    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    LOGGER.info("Starting Gateway server at http://%s:%d", host, port)
    LOGGER.info("  Registration: POST http://%s:%d/agents", host, port)
    LOGGER.info("  Events stream: GET http://%s:%d/events/stream", host, port)
    LOGGER.info("  Place bet: POST http://%s:%d/bets", host, port)

    # Run server as a background task
    async def run_server() -> None:
        try:
            await server.serve()
        except asyncio.CancelledError:
            LOGGER.info("Gateway server shutting down")
            await server.shutdown()

    task = asyncio.create_task(run_server())
    return task


@dataclass
class GatewayConfig:
    """Configuration for the HTTP gateway."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8080


async def _run_trial_and_monitor(
    *,
    orchestrator: TrialOrchestrator,
    trial_id: str,
    start_fn: Callable[[], Awaitable["TrialStatus"]],
    gateway_config: GatewayConfig | None = None,
) -> None:
    status = await start_fn()
    LOGGER.info("trial '%s' is %s", trial_id, status.phase.value)

    # Start gateway if enabled
    gateway_task: asyncio.Task[None] | None = None
    if gateway_config and gateway_config.enabled:
        try:
            gateway_task = await _start_gateway_server(
                orchestrator=orchestrator,
                trial_id=trial_id,
                host=gateway_config.host,
                port=gateway_config.port,
            )
        except Exception as e:
            LOGGER.error("Failed to start gateway: %s", e)
            LOGGER.warning("Trial will continue without gateway")

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    shutting_down = {"started": False}

    async def _capture_checkpoint() -> None:
        try:
            checkpoint = await orchestrator.checkpoint_trial(trial_id)
        except OrchestratorError as exc:
            LOGGER.warning("skipping checkpoint for trial '%s': %s", trial_id, exc)
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.warning(
                "unexpected error checkpointing trial '%s': %s",
                trial_id,
                exc,
            )
        else:
            LOGGER.info(
                "checkpoint '%s' created for trial '%s'",
                checkpoint.checkpoint_id,
                trial_id,
            )

    async def _graceful_shutdown() -> None:
        try:
            # Stop gateway first if running
            if gateway_task is not None and not gateway_task.done():
                LOGGER.info("stopping gateway server")
                gateway_task.cancel()
                try:
                    await gateway_task
                except asyncio.CancelledError:
                    pass
                LOGGER.info("gateway server stopped")

            await _capture_checkpoint()
            LOGGER.info("stopping trial '%s'", trial_id)
            await orchestrator.stop_trial(trial_id)
            LOGGER.info("trial '%s' stopped", trial_id)
        finally:
            stop_event.set()

    def _handle_signal(signame: str) -> None:
        if not shutting_down["started"]:
            shutting_down["started"] = True
            loop.create_task(_graceful_shutdown())
            LOGGER.info("received %s, initiating graceful shutdown", signame)
        else:
            LOGGER.error("received %s during shutdown; aborting", signame)
            raise KeyboardInterrupt(f"forced shutdown via {signame}")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig.name)
        except NotImplementedError:  # pragma: no cover - Windows fallback
            signal.signal(sig, lambda *_: _handle_signal(sig.name))

    try:
        await stop_event.wait()
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.remove_signal_handler(sig)
            except Exception:  # pragma: no cover - best effort
                pass


def _configure_logging(level: str) -> None:
    """Configure logging with timestamps while scoping the CLI log level."""
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    formatter = logging.Formatter(
        fmt=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Keep the root logger quiet (WARNING+) so third-party modules are unaffected.
    logging.basicConfig(
        level=logging.WARNING,
        format=fmt,
        datefmt=formatter.datefmt,
    )

    dojo_logger = logging.getLogger("dojozero")
    dojo_logger.handlers.clear()
    dojo_logger.propagate = False
    dojo_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    dojo_logger.addHandler(handler)

    # Suppress noisy third-party library logs
    # httpx logs all HTTP requests at INFO level, which is too verbose
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def _submit_to_server(
    server_url: str,
    params_payload: Mapping[str, Any],
    trial_id: str | None,
    checkpoint_id: str | None,
    resume_latest: bool,
) -> int:
    """Submit a trial to a running Dashboard Server."""
    import httpx

    scenario = params_payload.get("scenario")
    if scenario is None and "environment" in params_payload:
        scenario = params_payload["environment"]
    if not isinstance(scenario, Mapping):
        raise DojoZeroCLIError("spec.scenario must be a mapping")

    builder_name = scenario.get("name")
    if not isinstance(builder_name, str) or not builder_name:
        raise DojoZeroCLIError("spec.scenario.name must be a non-empty string")

    # Build request payload
    request_payload: dict[str, Any] = {
        "scenario": {
            "name": builder_name,
            "module": scenario.get("module"),
            "config": scenario.get("config", {}),
        },
        "metadata": params_payload.get("metadata", {}),
    }

    if trial_id:
        request_payload["trial_id"] = trial_id

    # Handle resume configuration
    if checkpoint_id or resume_latest:
        request_payload["resume"] = {
            "checkpoint_id": checkpoint_id,
            "latest": resume_latest,
        }

    # Submit to server - server returns immediately (trial is queued)
    base_url = server_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        LOGGER.info("Submitting trial to server: %s", base_url)
        response = await client.post(
            f"{base_url}/api/trials",
            json=request_payload,
        )

        # 201 Created (old behavior) or 202 Accepted (new queued behavior)
        if response.status_code in (201, 202):
            result = response.json()
            trial_id = result.get("id")
            phase = result.get("phase")
            message = result.get("message", "")
            queue_info = ""
            if "queue_position" in result:
                queue_info = f" (queue: {result['queue_position']}, running: {result['running_count']})"
            LOGGER.info(
                "Trial '%s' submitted successfully (phase: %s)%s %s",
                trial_id,
                phase,
                queue_info,
                message,
            )
            return 0
        else:
            # Try to parse JSON error, fall back to text
            try:
                error = response.json().get("error", response.text)
            except Exception:
                error = response.text or f"HTTP {response.status_code}"
            raise DojoZeroCLIError(
                f"Failed to submit trial (HTTP {response.status_code}): {error}"
            )


async def _run_command(args: argparse.Namespace) -> int:
    params_payload = (
        _load_yaml_mapping(args.params, label="params") if args.params else None
    )

    # Check if submitting to a remote server
    server_url = getattr(args, "server", None)
    if server_url:
        if params_payload is None:
            raise DojoZeroCLIError("--params is required when submitting to a server")
        return await _submit_to_server(
            server_url=server_url,
            params_payload=params_payload,
            trial_id=args.trial_id,
            checkpoint_id=args.checkpoint_id,
            resume_latest=bool(args.resume_latest),
        )

    # Local execution mode

    # Initialize OTLP exporter if trace backend is configured
    otel_exporter = _setup_otel_exporter(
        trace_backend=getattr(args, "trace_backend", None),
        trace_ingest_endpoint=getattr(args, "trace_ingest_endpoint", None),
        service_name=getattr(args, "service_name", "dojozero"),
    )

    # Imports: default imports + imports from params file
    params_imports = _gather_imports(params_payload)
    modules_to_import: list[str] = list(DEFAULT_IMPORTS)
    modules_to_import.extend(params_imports)
    _import_modules(modules_to_import)

    store = _create_store(args.store_directory)
    runtime_provider = _create_runtime_provider(args.runtime_provider, args.ray_config)
    orchestrator = TrialOrchestrator(store=store, runtime_provider=runtime_provider)

    checkpoint_id = args.checkpoint_id
    resume_latest = bool(args.resume_latest)
    trial_id = args.trial_id or uuid4().hex

    spec: TrialSpec | None = None
    if params_payload is not None:
        spec = await _prepare_trial_spec(trial_id, params_payload)
        if checkpoint_id:
            spec.resume_from_checkpoint_id = checkpoint_id
        elif resume_latest:
            spec.resume_from_latest = True
    else:
        if checkpoint_id is None and not resume_latest:
            raise DojoZeroCLIError(
                "--params is required unless --checkpoint-id or --resume-latest is provided"
            )
        if args.trial_id is None:
            raise DojoZeroCLIError(
                "--trial-id is required when resuming without a params file"
            )

    # Create gateway config (enabled by default, can be disabled with --disable-gateway)
    gateway_config: GatewayConfig | None = None
    if not getattr(args, "disable_gateway", False):
        gateway_config = GatewayConfig(
            enabled=True,
            host=getattr(args, "gateway_host", "127.0.0.1"),
            port=getattr(args, "gateway_port", 8080),
        )
        LOGGER.info(
            "Gateway enabled at http://%s:%d", gateway_config.host, gateway_config.port
        )

    try:
        if spec is not None:
            await _run_trial_and_monitor(
                orchestrator=orchestrator,
                trial_id=trial_id,
                start_fn=lambda: orchestrator.launch_trial(spec),
                gateway_config=gateway_config,
            )
        else:
            resume_checkpoint = checkpoint_id if checkpoint_id else None
            await _run_trial_and_monitor(
                orchestrator=orchestrator,
                trial_id=trial_id,
                start_fn=lambda: orchestrator.resume_trial(trial_id, resume_checkpoint),
                gateway_config=gateway_config,
            )
    finally:
        # Clean up exporters if configured
        _shutdown_otel_exporter(otel_exporter)

    return 0


def _resolve_event_files(
    patterns: list[str], temp_dir: Path | None = None
) -> list[Path]:
    """Resolve event file patterns to actual file paths.

    Supports:
    - Local file paths: outputs/game.jsonl
    - Local glob patterns: outputs/*/*.jsonl, outputs/2025-01-*/*.jsonl

    Args:
        patterns: List of file patterns
        temp_dir: Unused (kept for API compatibility)

    Returns:
        List of resolved local file paths (sorted)

    Raises:
        DojoZeroCLIError: If no files match
    """
    import glob

    resolved_files: list[Path] = []

    for pattern in patterns:
        # Local file or glob pattern
        if "*" in pattern or "?" in pattern:
            # Glob pattern
            matched = glob.glob(pattern, recursive=True)
            if not matched:
                LOGGER.warning("No local files match pattern: %s", pattern)
                continue
            LOGGER.info("Found %d local files matching %s", len(matched), pattern)
            for match in matched:
                path = Path(match)
                if path.is_file():
                    resolved_files.append(path)
        else:
            # Single file
            path = Path(pattern)
            if not path.exists():
                raise DojoZeroCLIError(f"Event file not found: {pattern}")
            if not path.is_file():
                raise DojoZeroCLIError(f"Not a file: {pattern}")
            resolved_files.append(path)

    if not resolved_files:
        raise DojoZeroCLIError(f"No event files found matching patterns: {patterns}")

    # Sort for deterministic order
    return sorted(resolved_files)


async def _backtest_single_file(
    event_file: Path,
    trial_id: str,
    params_payload: MutableMapping[str, Any],
    builder_name: str,
    speed: float,
    max_sleep: float,
    orchestrator: "TrialOrchestrator",
    emit_traces: bool = False,
) -> None:
    """Run backtest for a single file.

    Args:
        event_file: Path to the event file
        trial_id: Trial ID to use
        params_payload: Trial params
        builder_name: Name of the trial builder
        speed: Backtest speed multiplier
        max_sleep: Maximum sleep between events
        orchestrator: TrialOrchestrator instance
        emit_traces: Emit data events to trace backend with rebased timestamps
    """
    # Prepare trial spec from params
    spec = await _prepare_trial_spec(trial_id, params_payload)

    # Convert metadata to backtest-specific type with required backtest fields
    from dataclasses import asdict

    from dojozero.betting import BacktestBettingTrialMetadata

    # Create BacktestBettingTrialMetadata from existing metadata
    metadata_dict = asdict(spec.metadata)
    spec.metadata = BacktestBettingTrialMetadata(
        **metadata_dict,
        backtest_mode=True,
        backtest_file=str(event_file),
        backtest_speed=speed,
        backtest_max_sleep=max_sleep,
    )
    spec.builder_name = builder_name

    # Extract hub_id from spec (from stream configs)
    hub_id = None
    for stream_spec in spec.data_streams:
        config = stream_spec.config
        if config.get("hub_id"):
            hub_id = config["hub_id"]
            break

    if not hub_id:
        hub_id_raw = spec.metadata.get("hub_id", "data_hub")
        hub_id = str(hub_id_raw) if hub_id_raw else "data_hub"
    hub_id = str(hub_id)

    # Create DataHub in backtest mode (uses event_file path for consistency)
    hub = DataHub(
        hub_id=hub_id,
        persistence_file=str(event_file),
    )

    if emit_traces:
        hub.enable_backtest_traces(trial_id=trial_id)

    # Create BacktestCoordinator
    from dojozero.data import BacktestCoordinator

    coordinator = BacktestCoordinator(data_hub=hub, backtest_file=event_file)
    coordinator.set_speed(speed_up=speed, max_sleep=max_sleep)

    # Progress callback
    def progress_callback(current: int, total: int) -> None:
        if current % max(1, min(100, total // 10)) == 0:
            progress_pct = (current / total) * 100
            LOGGER.info(
                "[%s] Backtest progress: %d/%d events (%.1f%%)",
                event_file.name,
                current,
                total,
                progress_pct,
            )

    coordinator.set_progress_callback(progress_callback)

    # Load events
    LOGGER.info("Loading events from file: %s", event_file)
    await coordinator.start()

    # Set up backtest context
    from dojozero.core._registry import get_trial_builder_definition

    builder_def = get_trial_builder_definition(builder_name)
    original_context_builder = builder_def.context_builder

    def backtest_context_builder(spec: TrialSpec) -> RuntimeContext:
        return RuntimeContext(
            trial_id=spec.trial_id,
            data_hubs={hub_id: hub},
            stores={},
        )

    try:
        builder_def.context_builder = backtest_context_builder

        LOGGER.info("Launching trial '%s' in backtest mode", trial_id)
        await orchestrator.launch_trial(spec)

        builder_def.context_builder = original_context_builder

        LOGGER.info(
            "Starting backtest at %.1fx speed (max sleep: %.1fs)", speed, max_sleep
        )
        await coordinator.run_all()

        LOGGER.info("Stopping trial '%s'", trial_id)
        await orchestrator.stop_trial(trial_id)
        coordinator.stop()
    finally:
        builder_def.context_builder = original_context_builder


async def _submit_backtest_to_server(
    server_url: str,
    params_payload: MutableMapping[str, Any],
    trial_id: str | None,
    event_file: Path,
    speed: float,
    max_sleep: float,
) -> int:
    """Submit a backtest trial to a remote Dashboard Server."""
    import httpx

    request_payload: dict[str, Any] = {
        "params": dict(params_payload),
        "backtest": {
            "file": str(event_file.absolute()),
            "speed": speed,
            "max_sleep": max_sleep,
        },
    }
    if trial_id:
        request_payload["trial_id"] = trial_id

    base_url = server_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        LOGGER.info("Submitting backtest trial to server: %s", base_url)
        response = await client.post(
            f"{base_url}/api/trials",
            json=request_payload,
        )

        if response.status_code in (200, 201, 202):
            result = response.json()
            queue_info = ""
            if "queue_position" in result:
                queue_info = f" (queue: {result['queue_position']}, running: {result['running_count']})"
            LOGGER.info(
                "Backtest trial '%s' submitted successfully (phase: %s)%s",
                result.get("id"),
                result.get("phase"),
                queue_info,
            )
            return 0
        else:
            try:
                error = response.json().get("error", response.text)
            except Exception:
                error = response.text or f"HTTP {response.status_code}"
            raise DojoZeroCLIError(f"Failed to submit backtest trial: {error}")


async def _backtest_command(args: argparse.Namespace) -> int:
    """Handle backtest command."""
    from uuid import uuid4

    params_payload = _load_yaml_mapping(args.params, label="params")
    speed = args.backtest_speed
    max_sleep = args.backtest_max_sleep
    emit_traces = args.emit_traces

    if speed <= 0:
        raise DojoZeroCLIError(f"Backtest speed must be positive, got: {speed}")

    if max_sleep <= 0:
        raise DojoZeroCLIError(f"Backtest max-sleep must be positive, got: {max_sleep}")

    # Resolve event files (supports glob patterns and OSS URLs)
    event_files = _resolve_event_files(args.event_files)
    LOGGER.info("Resolved %d event file(s) to process", len(event_files))

    # Check if submitting to a remote server
    server_url = getattr(args, "server", None)
    if server_url:
        if len(event_files) > 1:
            raise DojoZeroCLIError(
                "Server mode does not support multiple event files. "
                "Please submit files one at a time or run locally."
            )
        return await _submit_backtest_to_server(
            server_url=server_url,
            params_payload=params_payload,
            trial_id=args.trial_id,
            event_file=event_files[0],
            speed=speed,
            max_sleep=max_sleep,
        )

    # Local execution mode

    # Initialize OTLP exporter if trace backend is configured
    otel_exporter = _setup_otel_exporter(
        trace_backend=getattr(args, "trace_backend", None),
        trace_ingest_endpoint=getattr(args, "trace_ingest_endpoint", None),
        service_name=getattr(args, "service_name", "dojozero"),
    )

    # Imports: default imports + imports from params file
    params_imports = _gather_imports(params_payload)
    modules_to_import: list[str] = list(DEFAULT_IMPORTS)
    modules_to_import.extend(params_imports)
    _import_modules(modules_to_import)

    store = _create_store(args.store_directory)
    runtime_provider = _create_runtime_provider(args.runtime_provider, args.ray_config)
    orchestrator = TrialOrchestrator(store=store, runtime_provider=runtime_provider)

    # Extract builder_name from scenario
    scenario = params_payload.get("scenario", {})
    builder_name = scenario.get("name") if isinstance(scenario, dict) else None
    if not builder_name:
        raise DojoZeroCLIError("Could not determine builder name from params")

    # Process each event file sequentially
    total_files = len(event_files)
    completed = 0
    failed = 0

    try:
        for i, event_file in enumerate(event_files, 1):
            # Generate unique trial_id for each file (unless single file with user-provided id)
            if args.trial_id and total_files == 1:
                trial_id = args.trial_id
            else:
                # Use file stem as part of trial_id for traceability
                file_stem = event_file.stem
                trial_id = f"{file_stem}-{uuid4().hex[:8]}"

            LOGGER.info(
                "=" * 60 + "\nProcessing file %d/%d: %s (trial_id: %s)\n" + "=" * 60,
                i,
                total_files,
                event_file.name,
                trial_id,
            )

            try:
                await _backtest_single_file(
                    event_file=event_file,
                    trial_id=trial_id,
                    params_payload=params_payload,
                    builder_name=builder_name,
                    speed=speed,
                    max_sleep=max_sleep,
                    orchestrator=orchestrator,
                    emit_traces=emit_traces,
                )
                completed += 1
                LOGGER.info("Completed backtest for %s", event_file.name)
            except Exception as e:
                failed += 1
                LOGGER.error("Failed to backtest %s: %s", event_file.name, e)
                # Continue with next file instead of aborting
                continue

    finally:
        # Clean up exporters if configured
        _shutdown_otel_exporter(otel_exporter)

    # Summary
    LOGGER.info(
        "=" * 60 + "\nBacktest complete: %d/%d files succeeded, %d failed\n" + "=" * 60,
        completed,
        total_files,
        failed,
    )

    return 0 if failed == 0 else 1


def _list_builders_command(_: argparse.Namespace) -> int:
    _import_modules(DEFAULT_IMPORTS)

    builders = list_trial_builders()
    for name in builders:
        definition = get_trial_builder_definition(name)
        description = f" - {definition.description}" if definition.description else ""
        print(f"{name}{description}")
    return 0


def _get_builder_command(args: argparse.Namespace) -> int:
    _import_modules(DEFAULT_IMPORTS)

    try:
        definition = get_trial_builder_definition(args.name)
    except _TrialBuilderNotFoundError as exc:
        raise DojoZeroCLIError(str(exc)) from exc

    print(f"Builder: {args.name}")
    if definition.description:
        print(f"Description: {definition.description}")
    schema_dict = definition.schema()
    schema_yaml = yaml.safe_dump(schema_dict, sort_keys=False)
    print("Schema:\n" + schema_yaml)

    example_path_arg = args.create_example_params
    if example_path_arg is not None:
        default_name = _default_example_filename(args.name)
        path = Path(example_path_arg or default_name)
        payload = _generate_example_spec(args.name, definition)
        _write_yaml_file(path, payload)
        print(f"Example YAML written to {path}")
    else:
        print("Tip: rerun with --create-example-params [path] to emit a starter YAML")
    return 0


def _load_trial_source_from_yaml(path: Path) -> InitialTrialSourceDict:
    """Load a trial source configuration from a YAML file.

    Args:
        path: Path to the YAML file

    Returns:
        InitialTrialSourceDict with trial source configuration

    Raises:
        DojoZeroCLIError: If file doesn't exist or is invalid
    """
    if not path.exists():
        raise DojoZeroCLIError(f"Trial source file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise DojoZeroCLIError(f"Invalid YAML in trial source file {path}: {e}")

    if not isinstance(data, dict):
        raise DojoZeroCLIError(
            f"Trial source file {path} must contain a mapping at the top level"
        )

    # Validate required fields
    required_fields = ["source_id", "sport_type", "config"]
    for field in required_fields:
        if field not in data:
            raise DojoZeroCLIError(
                f"Trial source file {path} missing required field: {field}"
            )

    # Cast to typed dict after validation
    return InitialTrialSourceDict(
        source_id=data["source_id"],
        sport_type=data["sport_type"],
        config=data["config"],
    )


async def _serve_command(args: argparse.Namespace) -> int:
    """Handle serve command - start Dashboard Server."""
    from dojozero.dashboard_server import run_dashboard_server

    # Imports: default imports and CLI --import-module flags
    # Import default modules; trial source imports are handled dynamically when sources are loaded
    _import_modules(DEFAULT_IMPORTS)

    store = _create_store(args.store_directory)
    runtime_provider = _create_runtime_provider(args.runtime_provider, args.ray_config)
    orchestrator = TrialOrchestrator(store=store, runtime_provider=runtime_provider)

    host = args.host
    port = args.port
    trace_backend = getattr(args, "trace_backend", None)
    trace_ingest_endpoint = getattr(args, "trace_ingest_endpoint", None)
    service_name = getattr(args, "service_name", "dojozero")
    trial_source_files: list[str] = getattr(args, "trial_sources", []) or []
    auto_resume = not getattr(args, "no_auto_resume", False)
    stale_threshold_hours = getattr(args, "stale_threshold_hours", 24.0)

    # Determine store path from args
    store_path = (
        args.store_directory if args.store_directory else Path(DEFAULT_STORE_DIRECTORY)
    )

    # Create scheduler store (uses store root directory for persistence)
    from dojozero.dashboard_server._scheduler import FileSchedulerStore

    scheduler_store = FileSchedulerStore(store_path)

    # Expand glob patterns and load trial source configurations
    import glob as glob_module

    initial_trial_sources: list[InitialTrialSourceDict] = []
    for source_pattern in trial_source_files:
        # Expand glob pattern
        matched_files = sorted(glob_module.glob(str(source_pattern)))
        if not matched_files:
            LOGGER.error("Trial source file not found: %s", source_pattern)
            raise DojoZeroCLIError(f"Trial source file not found: {source_pattern}")

        for source_file in matched_files:
            source_path = Path(source_file)
            source_data = _load_trial_source_from_yaml(source_path)
            initial_trial_sources.append(source_data)
            LOGGER.info(
                "Loaded trial source from %s: %s",
                source_path,
                source_data.get("source_id"),
            )

    LOGGER.info("Starting Dashboard Server at http://%s:%d", host, port)
    LOGGER.info("Trial API: http://%s:%d/api/trials", host, port)
    LOGGER.info("Trial Source API: http://%s:%d/api/trial-sources", host, port)
    LOGGER.info("Scheduled Trials API: http://%s:%d/api/scheduled-trials", host, port)
    LOGGER.info("Game Discovery API: http://%s:%d/api/games/{nba,nfl}", host, port)
    LOGGER.info("Store path: %s", store_path)
    if initial_trial_sources:
        LOGGER.info("Initial trial sources: %d", len(initial_trial_sources))

    if trace_backend == "sls":
        LOGGER.info("Trace backend: SLS (using env vars for configuration)")
    elif trace_backend == "jaeger":
        LOGGER.info("Trace backend: Jaeger (endpoint: %s)", trace_ingest_endpoint)
    else:
        LOGGER.info("No trace backend configured - traces will not be exported")

    enable_gateway = not getattr(args, "disable_gateway", False)
    if enable_gateway:
        LOGGER.info(
            "Gateway API enabled at http://%s:%d/api/trials/{trial_id}/", host, port
        )

    # Load agent authenticator (local YAML keys + GitHub PAT)
    from dojozero.gateway import (
        CompositeAuthenticator,
        GitHubAgentAuthenticator,
        LocalAgentAuthenticator,
    )

    local_auth = None
    agent_keys_path = Path(store_path) / "agent_keys.yaml"
    if agent_keys_path.exists():
        local_auth = LocalAgentAuthenticator(config_path=agent_keys_path)
        LOGGER.info(
            "Local agent keys loaded (%d keys from %s)",
            len(local_auth._keys),
            agent_keys_path,
        )

    authenticator = CompositeAuthenticator(
        local=local_auth,
        github=GitHubAgentAuthenticator(),
    )
    LOGGER.info(
        "Agent authentication enabled (local=%s, github=enabled)",
        "enabled" if local_auth else "disabled",
    )

    await run_dashboard_server(
        orchestrator=orchestrator,
        scheduler_store=scheduler_store,
        host=host,
        port=port,
        trace_backend=trace_backend,
        trace_ingest_endpoint=trace_ingest_endpoint,
        service_name=service_name,
        initial_trial_sources=initial_trial_sources if initial_trial_sources else None,
        auto_resume=auto_resume,
        stale_threshold_hours=stale_threshold_hours,
        enable_gateway=enable_gateway,
        authenticator=authenticator,
    )
    return 0


def _print_trial_links(
    metadata: Mapping[str, Any],
    event_id: str,
    sport_type: str,
    event_time_str: str = "",
) -> None:
    """Print ESPN and Polymarket links for a trial.

    Args:
        metadata: Trial metadata dict containing GameMetadata keys
            (home_tricode, away_tricode, game_date, game_short_name)
        event_id: ESPN event ID
        sport_type: Sport type (e.g., "nba", "nfl")
        event_time_str: Optional event time string for date fallback
    """
    home_tricode = str(metadata.get("home_tricode", ""))
    away_tricode = str(metadata.get("away_tricode", ""))
    game_date = str(metadata.get("game_date", ""))

    # Fallback: extract tricodes from game_short_name (e.g., "LAL @ BOS")
    if not (home_tricode and away_tricode):
        short_name = metadata.get("game_short_name", "")
        if " @ " in short_name:
            parts = short_name.split(" @ ")
            if len(parts) == 2:
                away_tricode = parts[0].strip()
                home_tricode = parts[1].strip()

    # Fallback: extract game_date from event_time
    if not game_date and event_time_str:
        game_date = event_time_str[:10]  # Extract YYYY-MM-DD

    # ESPN link
    if event_id:
        print(f"    ESPN: {get_espn_game_url(event_id, sport_type)}")

    # Polymarket link
    if home_tricode and away_tricode and game_date:
        polymarket_url = PolymarketAPI.get_event_url(
            away_tricode, home_tricode, game_date, sport_type
        )
        print(f"    Polymarket: {polymarket_url}")


async def _list_trials_command(args: argparse.Namespace) -> int:
    """Handle list-trials command - list trials from Dashboard Server.

    By default shows both scheduled and running trials with user-friendly info.
    """
    import json

    import httpx

    server = args.server.rstrip("/")
    scheduled_only = getattr(args, "scheduled_only", False)
    running_only = getattr(args, "running_only", False)
    output_json = args.output_json
    show_links = getattr(args, "show_links", False)
    include_finished = getattr(args, "include_finished", False)

    # Fetch data from both endpoints by default
    scheduled_trials: list[dict] = []
    running_trials: list[dict] = []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Fetch scheduled trials unless --running-only
            if not running_only:
                try:
                    params = {}
                    if include_finished:
                        params["include_finished"] = "true"
                    resp = await client.get(
                        f"{server}/api/scheduled-trials", params=params
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    scheduled_trials = data.get("scheduled_trials", [])
                except httpx.HTTPStatusError:
                    # Scheduling may not be enabled, ignore
                    pass

            # Fetch running trials unless --scheduled-only
            if not scheduled_only:
                resp = await client.get(f"{server}/api/trials")
                resp.raise_for_status()
                data = resp.json()
                running_trials = (
                    data if isinstance(data, list) else data.get("trials", [])
                )

    except httpx.HTTPStatusError as e:
        body = e.response.text
        raise DojoZeroCLIError(
            f"Server returned error {e.response.status_code}: {body}"
        )
    except httpx.RequestError as e:
        error_detail = str(e) or type(e).__name__
        raise DojoZeroCLIError(
            f"Failed to connect to server at {server}: {error_detail}"
        )

    if output_json:
        combined = {
            "scheduled_trials": scheduled_trials,
            "running_trials": running_trials,
        }
        if scheduled_only:
            combined = {"scheduled_trials": scheduled_trials}
        elif running_only:
            combined = {"running_trials": running_trials}
        print(json.dumps(combined, indent=2))
        return 0

    # Pretty print with user-friendly info
    print("=" * 110)

    # Split scheduled trials into active and finished
    scheduled_finished_phases = {"completed", "cancelled", "failed"}
    active_scheduled = [
        t
        for t in scheduled_trials
        if t.get("phase", "") not in scheduled_finished_phases
    ]
    finished_scheduled = [
        t for t in scheduled_trials if t.get("phase", "") in scheduled_finished_phases
    ]

    # Build set of trial IDs that are finished according to scheduled trials
    # (these should not appear in active running trials)
    finished_scheduled_trial_ids = {
        t.get("launched_trial_id")
        for t in finished_scheduled
        if t.get("launched_trial_id")
    }

    # Split running trials into active and finished
    running_active_phases = {"pending", "starting", "running"}
    running_finished_phases = {"stopped", "completed", "failed", "cancelled"}
    active_running = [
        t
        for t in running_trials
        if t.get("phase", "") in running_active_phases
        and t.get("id", t.get("trial_id", "")) not in finished_scheduled_trial_ids
    ]
    finished_running = [
        t for t in running_trials if t.get("phase", "") in running_finished_phases
    ]

    # Helper to get game name from metadata
    def _get_game_name(metadata: dict, fallback: str = "") -> str:
        if metadata.get("game_short_name"):
            return metadata["game_short_name"]
        elif metadata.get("home_tricode") and metadata.get("away_tricode"):
            return f"{metadata['away_tricode']} @ {metadata['home_tricode']}"
        elif metadata.get("home_team") and metadata.get("away_team"):
            return f"{metadata['away_team']} @ {metadata['home_team']}"
        return fallback

    # === TABLE 1: Scheduled Trials (active only) ===
    if active_scheduled and not running_only:
        print(f"\n📅 Scheduled Trials ({len(active_scheduled)}):")
        print("-" * 100)
        print(
            f"  {'Status':<12} {'Game':<30} {'Game Time':<14} {'Trial Start':<14} {'ESPN Game ID':<12}"
        )
        print("  " + "-" * 96)

        for trial in active_scheduled:
            phase = trial.get("phase", "")
            metadata = trial.get("metadata", {})
            game_name = _get_game_name(metadata, trial.get("scenario_name", ""))
            if len(game_name) > 28:
                game_name = game_name[:25] + "..."
            event_time = utc_iso_to_local(trial.get("event_time", ""))
            start_time = utc_iso_to_local(trial.get("scheduled_start_time", ""))
            espn_game_id = (
                metadata.get("espn_game_id", "") or trial.get("event_id", "")
            )[:12]

            # Use text-only status for consistent column width
            print(
                f"  {phase:<12} {game_name:<30} {event_time:<14} {start_time:<14} {espn_game_id:<12}"
            )

            if show_links:
                _print_trial_links(
                    metadata=metadata,
                    event_id=trial.get("event_id", ""),
                    sport_type=trial.get("sport_type", "nba"),
                    event_time_str=trial.get("event_time", ""),
                )
            if trial.get("error"):
                print(f"    └─ Error: {trial['error'][:80]}")

    # === TABLE 2: Finished Trials (from both scheduled and running) ===
    # Combine finished scheduled trials with finished running trials
    all_finished: list[dict] = []
    for trial in finished_scheduled:
        all_finished.append(
            {
                "source": "scheduled",
                "phase": trial.get("phase", ""),
                "game_name": _get_game_name(
                    trial.get("metadata", {}), trial.get("scenario_name", "")
                ),
                "espn_game_id": trial.get("metadata", {}).get("espn_game_id", "")
                or trial.get("event_id", ""),
                "trial_id": trial.get("launched_trial_id", ""),
                "error": trial.get("error"),
            }
        )
    for trial in finished_running:
        all_finished.append(
            {
                "source": "running",
                "phase": trial.get("phase", ""),
                "game_name": _get_game_name(trial.get("metadata", {})),
                "espn_game_id": trial.get("metadata", {}).get("espn_game_id", "")
                or trial.get("metadata", {}).get("event_id", ""),
                "trial_id": trial.get("id", trial.get("trial_id", "")),
                "error": trial.get("error"),
            }
        )

    if all_finished and include_finished:
        print(f"\n📋 Finished Trials ({len(all_finished)}):")
        print("-" * 110)
        print(f"  {'Status':<12} {'Game':<30} {'ESPN Game ID':<14} {'Trial ID':<45}")
        print("  " + "-" * 106)

        for item in all_finished:
            phase = item["phase"]
            game_name = (
                item["game_name"][:28] + "..."
                if len(item["game_name"]) > 28
                else item["game_name"]
            )
            espn_game_id = item["espn_game_id"][:12] if item["espn_game_id"] else ""
            trial_id = item["trial_id"]
            if len(trial_id) > 43:
                trial_id = trial_id[:40] + "..."

            # Use text-only status for consistent column width
            print(f"  {phase:<12} {game_name:<30} {espn_game_id:<14} {trial_id:<45}")

            if item.get("error"):
                print(f"    └─ Error: {item['error'][:80]}")

    # === TABLE 3: Running Trials (active only, no status column) ===
    if active_running and not scheduled_only:
        print(f"\n🏃 Running Trials ({len(active_running)}):")
        print("-" * 100)
        print(f"  {'Game':<30} {'ESPN Game ID':<14} {'Trial ID':<50}")
        print("  " + "-" * 96)

        for trial in active_running:
            trial_id = trial.get("id", trial.get("trial_id", ""))
            metadata = trial.get("metadata", {})
            game_name = _get_game_name(metadata)
            espn_game_id = metadata.get("espn_game_id", "") or metadata.get(
                "event_id", ""
            )

            if len(trial_id) > 48:
                trial_id = trial_id[:45] + "..."
            if len(game_name) > 28:
                game_name = game_name[:25] + "..."
            if len(espn_game_id) > 12:
                espn_game_id = espn_game_id[:12]

            print(f"  {game_name:<30} {espn_game_id:<14} {trial_id:<50}")

    # Summary
    active_count = len(active_scheduled) + len(active_running)
    if active_count == 0 and (not include_finished or len(all_finished) == 0):
        print("\n  No trials found.")

    return 0


async def _list_sources_command(args: argparse.Namespace) -> int:
    """Handle list-sources command - list trial sources from Dashboard Server."""
    import json

    import httpx

    server = args.server.rstrip("/")
    output_json = args.output_json
    url = f"{server}/api/trial-sources"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        body = e.response.text
        raise DojoZeroCLIError(
            f"Server returned error {e.response.status_code}: {body}"
        )
    except httpx.RequestError as e:
        # httpx exceptions can have empty str() representations
        error_detail = str(e) or type(e).__name__
        raise DojoZeroCLIError(
            f"Failed to connect to server at {server}: {error_detail}"
        )

    if output_json:
        print(json.dumps(data, indent=2))
        return 0

    # Pretty print
    sources = data.get("sources", [])
    count = data.get("count", len(sources))
    print(f"Trial Sources ({count}):")
    print("-" * 100)
    if not sources:
        print("  No trial sources registered.")
        print("  Tip: Use --trial-source flag with 'dojo0 serve' to register sources.")
    else:
        # Header
        print(
            f"  {'Source ID':<30} {'Sport':<6} {'Scenario':<25} {'Enabled':<8} {'Last Sync':<20}"
        )
        print("  " + "-" * 87)
        for source in sources:
            source_id = source.get("source_id", "")[:28]
            sport = source.get("sport_type", "")
            config = source.get("config", {})
            scenario = config.get("scenario_name", "")[:23]
            enabled = "Yes" if source.get("enabled", False) else "No"
            last_sync = source.get("last_sync_at", "")
            if last_sync:
                last_sync = utc_iso_to_local(last_sync, "%Y-%m-%d %H:%M")
            else:
                last_sync = "Never"
            print(
                f"  {source_id:<30} {sport:<6} {scenario:<25} {enabled:<8} {last_sync:<20}"
            )

    return 0


async def _remove_source_command(args: argparse.Namespace) -> int:
    """Handle remove-source command - remove a trial source from Dashboard Server."""
    import httpx

    server = args.server.rstrip("/")
    source_id = args.source_id
    url = f"{server}/api/trial-sources/{source_id}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.delete(url)
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        body = e.response.text
        raise DojoZeroCLIError(
            f"Server returned error {e.response.status_code}: {body}"
        )
    except httpx.RequestError as e:
        # httpx exceptions can have empty str() representations
        error_detail = str(e) or type(e).__name__
        raise DojoZeroCLIError(
            f"Failed to connect to server at {server}: {error_detail}"
        )

    print(f"Removed trial source: {source_id}")
    return 0


async def _clear_schedules_command(args: argparse.Namespace) -> int:
    """Handle clear-schedules command - clear all scheduled trials."""
    import httpx

    server = args.server.rstrip("/")

    # Confirm if not using --yes flag
    if not args.yes:
        print("This will cancel and remove ALL scheduled trials.")
        confirm = input("Are you sure? (y/N): ")
        if confirm.lower() not in ("y", "yes"):
            print("Cancelled.")
            return 0

    url = f"{server}/api/scheduled-trials"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(url)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        body = e.response.text
        raise DojoZeroCLIError(
            f"Server returned error {e.response.status_code}: {body}"
        )
    except httpx.RequestError as e:
        # httpx exceptions can have empty str() representations
        error_detail = str(e) or type(e).__name__
        raise DojoZeroCLIError(
            f"Failed to connect to server at {server}: {error_detail}"
        )

    count = data.get("cleared_count", 0)
    print(f"Cleared {count} scheduled trial(s).")
    return 0


async def _arena_command(args: argparse.Namespace) -> int:
    """Handle arena command - start Arena Server."""
    from dojozero.arena_server import run_arena_server
    from dojozero.arena_server._config import ArenaServerConfig

    # Load config from YAML file if provided (for cache/query settings only)
    config = None
    config_file = getattr(args, "config_file", None)
    if config_file:
        try:
            config = ArenaServerConfig.from_yaml(config_file)
            LOGGER.info("Loaded config from: %s", config_file)
        except FileNotFoundError:
            raise DojoZeroCLIError(f"Config file not found: {config_file}")
        except Exception as e:
            raise DojoZeroCLIError(f"Failed to load config file: {e}")

    # Get CLI args (all have defaults now)
    host = args.host
    port = args.port
    trace_backend = args.trace_backend
    trace_query_endpoint = args.trace_query_endpoint
    static_dir = getattr(args, "static_dir", None)
    service_name = args.service_name
    redis_url = getattr(args, "redis_url", None)

    LOGGER.info("Starting Arena Server at http://%s:%d", host, port)
    if trace_backend == "sls":
        LOGGER.info("Trace backend: SLS (using env vars for configuration)")
    else:
        LOGGER.info("Trace backend: Jaeger (endpoint: %s)", trace_query_endpoint)
    LOGGER.info("WebSocket: ws://%s:%d/ws/trials/{trial_id}/stream", host, port)
    if static_dir:
        LOGGER.info("Static files: %s", static_dir)
    if redis_url or os.getenv("DOJOZERO_REDIS_URL"):
        LOGGER.info("Redis: enabled (fast startup)")

    await run_arena_server(
        config=config,
        host=host,
        port=port,
        trace_backend=trace_backend,
        trace_query_endpoint=trace_query_endpoint,
        static_dir=static_dir,
        service_name=service_name,
        redis_url=redis_url,
    )
    return 0


async def _sync_service_command(args: argparse.Namespace) -> int:
    """Handle sync-service command - start SLS to Redis sync service."""
    from dojozero.sync_service import SyncService
    from dojozero.sync_service._redis_client import RedisClient
    from dojozero.arena_server._cache import CacheConfig
    from dojozero.core._tracing import create_trace_reader

    # Get Redis URL (required)
    redis_url = args.redis_url or os.getenv("DOJOZERO_REDIS_URL")
    if not redis_url:
        raise DojoZeroCLIError(
            "Redis URL is required. Provide via --redis-url or DOJOZERO_REDIS_URL env var."
        )

    # Create trace reader (always SLS for sync service)
    trace_reader = create_trace_reader(
        backend="sls",
        service_name=args.service_name,
    )

    # Create Redis client
    redis_client = RedisClient(redis_url=redis_url)

    # Create config
    config = CacheConfig(
        refresh_interval=args.sync_interval,
        trials_lookback_days=args.lookback_days,
    )

    # Create and start sync service
    service = SyncService(
        trace_reader=trace_reader,
        redis_client=redis_client,
        config=config,
    )

    safe_url = redis_url.split("@")[-1] if "@" in redis_url else redis_url
    LOGGER.info("Starting Sync Service")
    LOGGER.info("Redis URL: %s", safe_url)
    LOGGER.info("Sync interval: %s seconds", args.sync_interval)
    LOGGER.info("Lookback days: %s", args.lookback_days)

    await service.start()
    return 0


def _agents_command(args: argparse.Namespace) -> int:
    """Handle agents command - manage agent API keys."""
    import json as json_module
    from pathlib import Path

    from dojozero.gateway._auth import AgentKeyManager

    store_dir = Path(args.store)
    keys_file = store_dir / "agent_keys.yaml"
    key_manager = AgentKeyManager(keys_file)

    if args.agents_command == "add":
        agent_id = args.id
        display_name = args.name
        persona = args.persona
        model = args.model
        model_display_name = getattr(args, "model_display_name", None)
        cdn_url = getattr(args, "cdn_url", None)

        # Check if agent_id already exists
        existing = key_manager.find_by_agent_id(agent_id)
        if existing:
            LOGGER.error(
                "Agent '%s' already exists with key: %s...%s",
                agent_id,
                existing.api_key[:12],
                existing.api_key[-4:],
            )
            return 1

        # Generate new API key and add
        api_key = AgentKeyManager.generate_api_key()
        key_manager.add(
            api_key,
            agent_id,
            display_name=display_name,
            persona=persona,
            model=model,
            model_display_name=model_display_name,
            cdn_url=cdn_url,
        )

        print(f"Created agent '{agent_id}'")
        if display_name:
            print(f"  Display name: {display_name}")
        if persona:
            print(f"  Persona: {persona}")
        if model:
            print(f"  Model: {model}")
        if model_display_name:
            print(f"  Model display name: {model_display_name}")
        if cdn_url:
            print(f"  CDN URL: {cdn_url}")
        print(f"  API key: {api_key}")
        print(f"\nStored in: {keys_file}")
        return 0

    elif args.agents_command == "list":
        entries = key_manager.list_all()

        if not entries:
            print("No agents registered.")
            print("\nUse 'dojo agents add --id <agent_id>' to register an agent.")
            return 0

        if args.json:
            # JSON output (without exposing full keys)
            output = []
            for entry in entries:
                item = {
                    "agentId": entry.identity.agent_id,
                    "displayName": entry.identity.display_name,
                    "keyPrefix": entry.api_key[:12] + "...",
                }
                # Include optional fields only if set
                if entry.identity.persona:
                    item["persona"] = entry.identity.persona
                if entry.identity.model:
                    item["model"] = entry.identity.model
                if entry.identity.model_display_name:
                    item["modelDisplayName"] = entry.identity.model_display_name
                if entry.identity.cdn_url:
                    item["cdnUrl"] = entry.identity.cdn_url
                output.append(item)
            print(json_module.dumps(output, indent=2))
        else:
            # Table output
            print(f"{'AGENT_ID':<20} {'DISPLAY_NAME':<25} {'KEY_PREFIX':<20}")
            print("-" * 65)
            for entry in entries:
                agent_id = entry.identity.agent_id
                display_name = entry.identity.display_name or "-"
                key_prefix = entry.api_key[:12] + "..." + entry.api_key[-4:]
                print(f"{agent_id:<20} {display_name:<25} {key_prefix:<20}")
            print(f"\nTotal: {len(entries)} agent(s)")
            print(f"Keys file: {keys_file}")
        return 0

    elif args.agents_command == "remove":
        agent_id = args.agent_id

        # Find the agent
        entry = key_manager.find_by_agent_id(agent_id)
        if entry is None:
            LOGGER.error("Agent '%s' not found.", agent_id)
            return 1

        # Confirm
        if not args.yes:
            confirm = input(
                f"Remove agent '{agent_id}'? This will revoke its API key. [y/N] "
            )
            if confirm.lower() != "y":
                print("Cancelled.")
                return 0

        key_manager.remove(entry.api_key)
        print(f"Removed agent '{agent_id}' and revoked API key.")
        return 0

    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)

    try:
        if args.command == "run":
            return asyncio.run(_run_command(args))
        if args.command == "backtest":
            return asyncio.run(_backtest_command(args))
        if args.command == "list-builders":
            return _list_builders_command(args)
        if args.command == "get-builder":
            return _get_builder_command(args)
        if args.command == "serve":
            return asyncio.run(_serve_command(args))
        if args.command == "arena":
            return asyncio.run(_arena_command(args))
        if args.command == "sync-service":
            return asyncio.run(_sync_service_command(args))
        if args.command == "list-trials":
            return asyncio.run(_list_trials_command(args))
        if args.command == "list-sources":
            return asyncio.run(_list_sources_command(args))
        if args.command == "remove-source":
            return asyncio.run(_remove_source_command(args))
        if args.command == "clear-schedules":
            return asyncio.run(_clear_schedules_command(args))
        if args.command == "agents":
            return _agents_command(args)
        raise DojoZeroCLIError(f"unknown command '{args.command}'")
    except DojoZeroCLIError as exc:
        LOGGER.error(str(exc))
        return 1
    except KeyboardInterrupt:
        LOGGER.error("interrupted")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
