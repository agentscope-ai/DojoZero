"""DojoZero CLI for running trials today and hosting a FastAPI server soon."""

import argparse
import asyncio
import importlib
import logging
import signal
import sys
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
    InMemoryOrchestratorStore,
    LocalActorRuntimeProvider,
    TrialBuilderDefinition,
    TrialSpec,
    TrialStatus,
    get_trial_builder_definition,
    list_trial_builders,
)
from dojozero.data import DataHub
from dojozero.core import TrialBuilderNotFoundError as _TrialBuilderNotFoundError

try:  # Optional Ray dependency
    from dojozero.ray_runtime import RayActorRuntimeProvider
except ImportError:  # pragma: no cover - ray is optional
    RayActorRuntimeProvider = None  # type: ignore[assignment]

DEFAULT_IMPORTS: tuple[str, ...] = (
    "dojozero.samples",
    "dojozero.nba_moneyline",
    "dojozero.nfl_moneyline",
)
DEFAULT_CLI_CONFIG: Mapping[str, Any] = {
    "store": {
        "kind": "filesystem",
        "root": "./dojozero-store",
    },
    "runtime": {
        "kind": "local",
    },
    "imports": ["dojozero.samples", "dojozero.nba_moneyline", "dojozero.nfl_moneyline"],
}

RUN_USAGE_EXAMPLES = dedent(
    """
     Examples:
        1. Create a new trial
            dojo0 run --params sample_trial.yaml --trial-id sample-trial

        2. Resume a trial from the latest checkpoint
            dojo0 run --trial-id sample-trial --resume-latest

        3. Use a custom dashboard configuration (also works with `dojo0 serve`)
            dojo0 --setting ./settings/prod.yaml run --params sample_trial.yaml --trial-id sample-trial
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
        "--import-module",
        action="append",
        dest="import_modules",
        default=[],
        help="Python module to import before running the sub-command (repeatable).",
    )
    parser.add_argument(
        "--no-default-imports",
        action="store_true",
        help="Skip importing built-in helper modules (e.g. samples).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (default: INFO).",
    )
    parser.add_argument(
        "--setting",
        type=Path,
        help=(
            "Path to the dashboard settings YAML (store/runtime/imports) used by DojoZero."
        ),
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
        "Supports multiple files via glob patterns and OSS URLs:\n"
        "  Local files:  outputs/2025-01-*/*.jsonl\n"
        "  OSS files:    oss://bucket/prefix/*.jsonl\n\n"
        "Files are processed sequentially in sorted order.",
    )
    backtest_parser.add_argument(
        "--events",
        type=str,
        nargs="+",
        required=True,
        dest="event_files",
        help="Path(s) to JSONL event file(s). Supports glob patterns (e.g., 'outputs/*/*.jsonl') "
        "and OSS URLs (e.g., 'oss://bucket/prefix/*.jsonl'). Multiple patterns can be specified.",
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
        "--oss-backup",
        dest="oss_backup",
        action="store_true",
        help="Enable OSS backup for trial data (events JSONL). "
        "Requires DOJOZERO_OSS_BUCKET and DOJOZERO_OSS_ENDPOINT env vars.",
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

    # Arena Server command
    arena_parser = subparsers.add_parser(
        "arena",
        help="Start the Arena Server for WebSocket streaming",
        description="Launch the Arena Server for real-time span streaming to browsers.",
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
        help="Path to built static assets to serve (optional).",
    )

    # List trials command
    list_trials_parser = subparsers.add_parser(
        "list-trials",
        help="List trials from a running Dashboard Server",
        description="Fetch and display trials from a Dashboard Server. "
        "Use --scheduled to list auto-scheduled trials from trial sources.",
    )
    list_trials_parser.add_argument(
        "--server",
        default="http://localhost:8000",
        help="Dashboard Server URL (default: http://localhost:8000).",
    )
    list_trials_parser.add_argument(
        "--scheduled",
        action="store_true",
        help="List auto-scheduled trials (from trial sources) instead of running/queued trials.",
    )
    list_trials_parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Output as raw JSON instead of pretty-printed table.",
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


def _load_cli_config(path: Path | None) -> Mapping[str, Any]:
    base = {
        "store": dict(DEFAULT_CLI_CONFIG["store"]),
        "runtime": dict(DEFAULT_CLI_CONFIG["runtime"]),
        "imports": list(DEFAULT_CLI_CONFIG["imports"]),
    }

    payload: Mapping[str, Any] | None = None
    if path is not None:
        payload = _load_yaml_mapping(path, label="config")
    if payload is None:
        return base

    store_cfg = payload.get("store")
    if isinstance(store_cfg, Mapping):
        base["store"].update(store_cfg)

    runtime_cfg = payload.get("runtime")
    if isinstance(runtime_cfg, Mapping):
        base["runtime"].update(runtime_cfg)

    imports_cfg = payload.get("imports")
    if imports_cfg is not None:
        if isinstance(imports_cfg, str):
            base["imports"] = [imports_cfg]
        elif isinstance(imports_cfg, Sequence):
            normalized: list[str] = []
            for item in imports_cfg:
                if not isinstance(item, str):
                    raise DojoZeroCLIError("entries in config.imports must be strings")
                normalized.append(item)
            base["imports"] = normalized
        else:
            raise DojoZeroCLIError(
                "config.imports must be a string or sequence of strings"
            )

    for key, value in payload.items():
        if key not in base:
            base[key] = value
    return base


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


def _prepare_trial_spec(trial_id: str, payload: Mapping[str, Any]) -> TrialSpec:
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
        spec = definition.build(trial_id, builder_config)
    except ValidationError as exc:
        raise DojoZeroCLIError(
            f"invalid config for builder '{builder_name}': {exc}"
        ) from exc

    metadata = payload.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, Mapping):
            raise DojoZeroCLIError("spec.metadata must be a mapping when provided")
        spec.metadata.update(metadata)

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
    payload: Mapping[str, Any],
) -> InMemoryOrchestratorStore | FileSystemOrchestratorStore:
    store_cfg = payload.get("store") or {}
    if not isinstance(store_cfg, Mapping):
        raise DojoZeroCLIError("config.store must be a mapping when provided")
    kind = str(store_cfg.get("kind", "memory")).lower()
    if kind == "memory":
        return InMemoryOrchestratorStore()
    if kind == "filesystem":
        root = store_cfg.get("root")
        base_path = (
            Path(str(root)) if root is not None else Path.cwd() / "dojozero-store"
        )
        return FileSystemOrchestratorStore(base_path)
    raise DojoZeroCLIError(f"unsupported store.kind '{kind}'")


def _create_runtime_provider(payload: Mapping[str, Any]):
    runtime_cfg = payload.get("runtime") or {}
    if not isinstance(runtime_cfg, Mapping):
        raise DojoZeroCLIError("config.runtime must be a mapping when provided")
    kind = str(runtime_cfg.get("kind", "local")).lower()
    if kind == "local":
        return LocalActorRuntimeProvider()
    if kind == "ray":
        if RayActorRuntimeProvider is None:
            raise DojoZeroCLIError(
                "ray runtime requested but ray is not installed. "
                "Please install ray dependencies with 'pip install dojozero[ray]'"
            )
        init_kwargs = runtime_cfg.get("init_kwargs") or {}
        if not isinstance(init_kwargs, Mapping):
            raise DojoZeroCLIError(
                "config.runtime.init_kwargs must be a mapping when provided"
            )
        auto_init = bool(runtime_cfg.get("auto_init", True))
        return RayActorRuntimeProvider(auto_init=auto_init, init_kwargs=init_kwargs)
    raise DojoZeroCLIError(f"unsupported runtime.kind '{kind}'")


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


async def _run_trial_and_monitor(
    *,
    orchestrator: TrialOrchestrator,
    trial_id: str,
    start_fn: Callable[[], Awaitable["TrialStatus"]],
) -> None:
    status = await start_fn()
    LOGGER.info("trial '%s' is %s", trial_id, status.phase.value)
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
    config_payload = _load_cli_config(args.setting)
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
    trace_backend = getattr(args, "trace_backend", None)
    trace_ingest_endpoint = getattr(args, "trace_ingest_endpoint", None)
    service_name = getattr(args, "service_name", "dojozero")
    otel_exporter = None

    if trace_backend:
        from dojozero.core._tracing import (
            OTelSpanExporter,
            get_sls_exporter_headers,
            set_otel_exporter,
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
            set_otel_exporter(otel_exporter)
            LOGGER.info(
                "OTel exporter configured: %s (backend: sls, service_name: %s)",
                otlp_endpoint,
                service_name,
            )
        elif trace_backend == "jaeger":
            otlp_endpoint = trace_ingest_endpoint or "http://localhost:4318"
            otel_exporter = OTelSpanExporter(
                otlp_endpoint, service_name=service_name, headers=None
            )
            set_otel_exporter(otel_exporter)
            LOGGER.info(
                "OTel exporter configured: %s (backend: jaeger, service_name: %s)",
                otlp_endpoint,
                service_name,
            )
    else:
        LOGGER.info("No trace backend configured - traces will not be exported")

    config_imports = _gather_imports(config_payload)
    params_imports = _gather_imports(params_payload)
    requested_imports = list(args.import_modules or [])
    modules_to_import: list[str] = []
    if not args.no_default_imports:
        modules_to_import.extend(DEFAULT_IMPORTS)
    modules_to_import.extend(config_imports)
    modules_to_import.extend(params_imports)
    modules_to_import.extend(requested_imports)
    _import_modules(modules_to_import)

    store = _create_store(config_payload)
    runtime_provider = _create_runtime_provider(config_payload)
    orchestrator = TrialOrchestrator(store=store, runtime_provider=runtime_provider)

    checkpoint_id = args.checkpoint_id
    resume_latest = bool(args.resume_latest)
    trial_id = args.trial_id or uuid4().hex

    spec: TrialSpec | None = None
    if params_payload is not None:
        spec = _prepare_trial_spec(trial_id, params_payload)
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

    try:
        if spec is not None:
            await _run_trial_and_monitor(
                orchestrator=orchestrator,
                trial_id=trial_id,
                start_fn=lambda: orchestrator.launch_trial(spec),
            )
        else:
            resume_checkpoint = checkpoint_id if checkpoint_id else None
            await _run_trial_and_monitor(
                orchestrator=orchestrator,
                trial_id=trial_id,
                start_fn=lambda: orchestrator.resume_trial(trial_id, resume_checkpoint),
            )
    finally:
        # Clean up OTLP exporter if configured
        if otel_exporter is not None:
            from dojozero.core._tracing import set_otel_exporter

            otel_exporter.shutdown()
            set_otel_exporter(None)
            LOGGER.info("OTel exporter shutdown complete")

    return 0


def _resolve_event_files(
    patterns: list[str], temp_dir: Path | None = None
) -> list[Path]:
    """Resolve event file patterns to actual file paths.

    Supports:
    - Local file paths: outputs/game.jsonl
    - Local glob patterns: outputs/*/*.jsonl, outputs/2025-01-*/*.jsonl
    - OSS URLs: oss://bucket/prefix/file.jsonl
    - OSS glob patterns: oss://bucket/prefix/*.jsonl

    Args:
        patterns: List of file patterns or OSS URLs
        temp_dir: Temporary directory for downloading OSS files (created if None)

    Returns:
        List of resolved local file paths (sorted)

    Raises:
        DojoZeroCLIError: If no files match or OSS access fails
    """
    import glob
    import tempfile

    resolved_files: list[Path] = []
    oss_temp_dir = temp_dir

    # Check if any OSS patterns exist and initialize client once before the loop
    oss_patterns = [p for p in patterns if p.startswith("oss://")]
    oss_client = None
    if oss_patterns:
        # Extract bucket name from the first OSS pattern for client initialization
        first_oss_url = oss_patterns[0][6:]  # Remove "oss://"
        parts = first_oss_url.split("/", 1)
        if len(parts) < 2:
            raise DojoZeroCLIError(f"Invalid OSS URL format: {oss_patterns[0]}")
        bucket_name = parts[0]

        try:
            from dojozero.utils.oss import OSSClient

            oss_client = OSSClient.from_env(bucket_name=bucket_name)
        except ImportError:
            raise DojoZeroCLIError(
                "OSS support requires oss2 package. Install with: pip install oss2"
            )
        except ValueError as e:
            raise DojoZeroCLIError(f"OSS configuration error: {e}")

    for pattern in patterns:
        if pattern.startswith("oss://"):
            # Parse OSS URL: oss://bucket/prefix/path/*.jsonl
            # Format: oss://bucket/key or oss://bucket/prefix/*.jsonl
            url_path = pattern[6:]  # Remove "oss://"
            parts = url_path.split("/", 1)
            if len(parts) < 2:
                raise DojoZeroCLIError(f"Invalid OSS URL format: {pattern}")

            bucket_name = parts[0]
            oss_key_pattern = parts[1]

            assert oss_client is not None  # Initialized above

            # Create temp directory for OSS downloads if not provided
            if oss_temp_dir is None:
                oss_temp_dir = Path(tempfile.mkdtemp(prefix="dojozero_backtest_"))
                LOGGER.info("Created temp directory for OSS files: %s", oss_temp_dir)

            # Check if pattern contains glob characters
            if "*" in oss_key_pattern or "?" in oss_key_pattern:
                # List files matching the pattern
                # Extract the prefix (non-glob part) for efficient listing
                prefix_parts = []
                for part in oss_key_pattern.split("/"):
                    if "*" in part or "?" in part:
                        break
                    prefix_parts.append(part)
                oss_prefix = "/".join(prefix_parts)

                matching_keys = oss_client.list_files(oss_prefix, oss_key_pattern)
                if not matching_keys:
                    LOGGER.warning("No OSS files match pattern: %s", pattern)
                    continue

                LOGGER.info(
                    "Found %d OSS files matching %s", len(matching_keys), pattern
                )

                # Download each matching file, preserving directory structure
                for oss_key in matching_keys:
                    local_path = oss_temp_dir / oss_key
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    oss_client.download_file(oss_key, local_path)
                    resolved_files.append(local_path)
            else:
                # Single file - download directly, preserving directory structure
                local_path = oss_temp_dir / oss_key_pattern
                local_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    oss_client.download_file(oss_key_pattern, local_path)
                    resolved_files.append(local_path)
                except Exception as e:
                    raise DojoZeroCLIError(
                        f"Failed to download OSS file {pattern}: {e}"
                    )
        else:
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
    """
    # Prepare trial spec from params
    spec = _prepare_trial_spec(trial_id, params_payload)

    # Add backtest metadata
    spec.metadata["backtest_file"] = str(event_file)
    spec.metadata["backtest_mode"] = True
    spec.metadata["backtest_speed"] = speed
    spec.metadata["backtest_max_sleep"] = max_sleep
    spec.metadata["builder_name"] = builder_name

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

    # Create DataHub in backtest mode
    hub = DataHub(
        hub_id=hub_id,
        persistence_file=None,
        enable_persistence=False,
    )

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
    config_payload = _load_cli_config(args.setting)

    # Initialize OTLP exporter if trace backend is configured
    trace_backend = getattr(args, "trace_backend", None)
    trace_ingest_endpoint = getattr(args, "trace_ingest_endpoint", None)
    service_name = getattr(args, "service_name", "dojozero")
    otel_exporter = None

    if trace_backend:
        from dojozero.core._tracing import (
            OTelSpanExporter,
            get_sls_exporter_headers,
            set_otel_exporter,
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
            set_otel_exporter(otel_exporter)
            LOGGER.info(
                "OTel exporter configured: %s (backend: sls, service_name: %s)",
                otlp_endpoint,
                service_name,
            )
        elif trace_backend == "jaeger":
            otlp_endpoint = trace_ingest_endpoint or "http://localhost:4318"
            otel_exporter = OTelSpanExporter(
                otlp_endpoint, service_name=service_name, headers=None
            )
            set_otel_exporter(otel_exporter)
            LOGGER.info(
                "OTel exporter configured: %s (backend: jaeger, service_name: %s)",
                otlp_endpoint,
                service_name,
            )
    else:
        LOGGER.info("No trace backend configured - traces will not be exported")

    config_imports = _gather_imports(config_payload)
    params_imports = _gather_imports(params_payload)
    requested_imports = list(args.import_modules or [])
    modules_to_import: list[str] = []
    if not args.no_default_imports:
        modules_to_import.extend(DEFAULT_IMPORTS)
    modules_to_import.extend(config_imports)
    modules_to_import.extend(params_imports)
    modules_to_import.extend(requested_imports)
    _import_modules(modules_to_import)

    store = _create_store(config_payload)
    runtime_provider = _create_runtime_provider(config_payload)
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
                )
                completed += 1
                LOGGER.info("Completed backtest for %s", event_file.name)
            except Exception as e:
                failed += 1
                LOGGER.error("Failed to backtest %s: %s", event_file.name, e)
                # Continue with next file instead of aborting
                continue

    finally:
        # Clean up OTLP exporter if configured
        if otel_exporter is not None:
            from dojozero.core._tracing import set_otel_exporter

            otel_exporter.shutdown()
            set_otel_exporter(None)
            LOGGER.info("OTel exporter shutdown complete")

    # Summary
    LOGGER.info(
        "=" * 60 + "\nBacktest complete: %d/%d files succeeded, %d failed\n" + "=" * 60,
        completed,
        total_files,
        failed,
    )

    return 0 if failed == 0 else 1


def _list_builders_command(args: argparse.Namespace) -> int:
    modules_to_import: list[str] = []
    if not args.no_default_imports:
        modules_to_import.extend(DEFAULT_IMPORTS)
    modules_to_import.extend(args.import_modules or [])
    _import_modules(modules_to_import)

    builders = list_trial_builders()
    for name in builders:
        definition = get_trial_builder_definition(name)
        description = f" - {definition.description}" if definition.description else ""
        print(f"{name}{description}")
    return 0


def _get_builder_command(args: argparse.Namespace) -> int:
    modules_to_import: list[str] = []
    if not args.no_default_imports:
        modules_to_import.extend(DEFAULT_IMPORTS)
    modules_to_import.extend(args.import_modules or [])
    _import_modules(modules_to_import)

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


def _load_trial_source_from_yaml(path: Path) -> dict[str, Any]:
    """Load a trial source configuration from a YAML file.

    Args:
        path: Path to the YAML file

    Returns:
        Dictionary with trial source configuration

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

    return data


async def _serve_command(args: argparse.Namespace) -> int:
    """Handle serve command - start Dashboard Server."""
    from dojozero.dashboard_server import run_dashboard_server

    config_payload = _load_cli_config(args.setting)

    config_imports = _gather_imports(config_payload)
    requested_imports = list(args.import_modules or [])
    modules_to_import: list[str] = []
    if not args.no_default_imports:
        modules_to_import.extend(DEFAULT_IMPORTS)
    modules_to_import.extend(config_imports)
    modules_to_import.extend(requested_imports)
    _import_modules(modules_to_import)

    store = _create_store(config_payload)
    runtime_provider = _create_runtime_provider(config_payload)
    orchestrator = TrialOrchestrator(store=store, runtime_provider=runtime_provider)

    host = args.host
    port = args.port
    trace_backend = getattr(args, "trace_backend", None)
    trace_ingest_endpoint = getattr(args, "trace_ingest_endpoint", None)
    oss_backup = getattr(args, "oss_backup", False)
    service_name = getattr(args, "service_name", "dojozero")
    trial_source_files: list[str] = getattr(args, "trial_sources", []) or []
    auto_resume = not getattr(args, "no_auto_resume", False)
    stale_threshold_hours = getattr(args, "stale_threshold_hours", 24.0)

    # Create scheduler store (uses store root directory for persistence)
    from dojozero.dashboard_server._scheduler import FileSchedulerStore

    store_cfg = config_payload.get("store") or {}
    store_root = store_cfg.get("root")
    store_path = (
        Path(str(store_root))
        if store_root is not None
        else Path.cwd() / "dojozero-store"
    )
    scheduler_store = FileSchedulerStore(store_path)

    # Expand glob patterns and load trial source configurations
    import glob as glob_module

    initial_trial_sources: list[dict[str, Any]] = []
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
        if oss_backup:
            LOGGER.info("OSS backup enabled for trial data")
    elif trace_backend == "jaeger":
        LOGGER.info("Trace backend: Jaeger (endpoint: %s)", trace_ingest_endpoint)
    else:
        LOGGER.info("No trace backend configured - traces will not be exported")

    await run_dashboard_server(
        orchestrator=orchestrator,
        scheduler_store=scheduler_store,
        host=host,
        port=port,
        trace_backend=trace_backend,
        trace_ingest_endpoint=trace_ingest_endpoint,
        oss_backup=oss_backup,
        service_name=service_name,
        initial_trial_sources=initial_trial_sources if initial_trial_sources else None,
        auto_resume=auto_resume,
        stale_threshold_hours=stale_threshold_hours,
    )
    return 0


async def _list_trials_command(args: argparse.Namespace) -> int:
    """Handle list-trials command - list trials from Dashboard Server."""
    import json
    import urllib.request
    import urllib.error

    server = args.server.rstrip("/")
    scheduled = args.scheduled
    output_json = args.output_json

    # Choose endpoint based on --scheduled flag
    if scheduled:
        url = f"{server}/api/scheduled-trials"
    else:
        url = f"{server}/api/trials"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise DojoZeroCLIError(f"Server returned error {e.code}: {body}")
    except urllib.error.URLError as e:
        raise DojoZeroCLIError(f"Failed to connect to server at {server}: {e}")

    if output_json:
        print(json.dumps(data, indent=2))
        return 0

    # Pretty print
    if scheduled:
        trials = data.get("scheduled_trials", [])
        count = data.get("count", len(trials))
        print(f"Scheduled Trials ({count}):")
        print("-" * 100)
        if not trials:
            print("  No scheduled trials found.")
        else:
            # Header
            print(
                f"  {'ID':<40} {'Phase':<12} {'Event':<15} {'Sport':<6} {'Start Time':<25}"
            )
            print("  " + "-" * 96)
            for trial in trials:
                schedule_id = trial.get("schedule_id", "")[:38]
                phase = trial.get("phase", "")
                event_id = trial.get("event_id", "")[:13]
                sport = trial.get("sport_type", "")
                # Convert UTC time to local timezone for display
                start_time_str = trial.get("scheduled_start_time", "")
                if start_time_str:
                    from datetime import datetime, timezone

                    try:
                        # Parse ISO format UTC time
                        utc_time = datetime.fromisoformat(
                            start_time_str.replace("Z", "+00:00")
                        )
                        if utc_time.tzinfo is None:
                            utc_time = utc_time.replace(tzinfo=timezone.utc)
                        # Convert to local timezone
                        local_time = utc_time.astimezone()
                        start_time = local_time.strftime("%Y-%m-%d %H:%M:%S %Z")
                    except (ValueError, TypeError):
                        start_time = start_time_str[:23]
                else:
                    start_time = ""
                print(
                    f"  {schedule_id:<40} {phase:<12} {event_id:<15} {sport:<6} {start_time:<25}"
                )
    else:
        # Regular trials - API returns a list directly
        trials = data if isinstance(data, list) else data.get("trials", [])
        count = len(trials)
        print(f"Trials ({count}):")
        print("-" * 100)
        if not trials:
            print("  No trials found.")
        else:
            # Header
            print(f"  {'Trial ID':<36} {'Phase':<12} {'Source':<12} {'Metadata':<30}")
            print("  " + "-" * 88)
            for trial in trials:
                trial_id = trial.get("id", trial.get("trial_id", ""))[:34]
                phase = trial.get("phase", "")
                source = trial.get("source", "")
                # Extract meaningful metadata
                metadata = trial.get("metadata", {})
                meta_str = ""
                if metadata.get("game_id"):
                    meta_str = f"game:{metadata['game_id']}"
                elif metadata.get("event_id"):
                    meta_str = f"event:{metadata['event_id']}"
                elif metadata:
                    # Show first key-value pair
                    for k, v in metadata.items():
                        meta_str = f"{k}:{v}"[:28]
                        break
                print(f"  {trial_id:<36} {phase:<12} {source:<12} {meta_str:<30}")

    return 0


async def _list_sources_command(args: argparse.Namespace) -> int:
    """Handle list-sources command - list trial sources from Dashboard Server."""
    import json
    import urllib.request
    import urllib.error

    server = args.server.rstrip("/")
    output_json = args.output_json
    url = f"{server}/api/trial-sources"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise DojoZeroCLIError(f"Server returned error {e.code}: {body}")
    except urllib.error.URLError as e:
        raise DojoZeroCLIError(f"Failed to connect to server at {server}: {e}")

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
            last_sync = source.get("last_sync_at", "Never")
            if last_sync and last_sync != "Never":
                last_sync = last_sync[:18]
            else:
                last_sync = "Never"
            print(
                f"  {source_id:<30} {sport:<6} {scenario:<25} {enabled:<8} {last_sync:<20}"
            )

    return 0


async def _remove_source_command(args: argparse.Namespace) -> int:
    """Handle remove-source command - remove a trial source from Dashboard Server."""
    import json
    import urllib.request
    import urllib.error

    server = args.server.rstrip("/")
    source_id = args.source_id
    url = f"{server}/api/trial-sources/{source_id}"

    try:
        request = urllib.request.Request(url, method="DELETE")
        with urllib.request.urlopen(request, timeout=10) as response:
            json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise DojoZeroCLIError(f"Server returned error {e.code}: {body}")
    except urllib.error.URLError as e:
        raise DojoZeroCLIError(f"Failed to connect to server at {server}: {e}")

    print(f"Removed trial source: {source_id}")
    return 0


async def _clear_schedules_command(args: argparse.Namespace) -> int:
    """Handle clear-schedules command - clear all scheduled trials."""
    import json
    import urllib.request
    import urllib.error

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
        request = urllib.request.Request(url, method="DELETE")
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise DojoZeroCLIError(f"Server returned error {e.code}: {body}")
    except urllib.error.URLError as e:
        raise DojoZeroCLIError(f"Failed to connect to server at {server}: {e}")

    count = data.get("cleared_count", 0)
    print(f"Cleared {count} scheduled trial(s).")
    return 0


async def _arena_command(args: argparse.Namespace) -> int:
    """Handle arena command - start Arena Server."""
    from dojozero.arena_server import run_arena_server

    host = args.host
    port = args.port
    trace_backend = args.trace_backend
    trace_query_endpoint = getattr(args, "trace_query_endpoint", None)
    static_dir = getattr(args, "static_dir", None)
    service_name = getattr(args, "service_name", "dojozero")

    LOGGER.info("Starting Arena Server at http://%s:%d", host, port)
    if trace_backend == "sls":
        LOGGER.info("Trace backend: SLS (using env vars for configuration)")
    else:
        LOGGER.info("Trace backend: Jaeger (endpoint: %s)", trace_query_endpoint)
    LOGGER.info("WebSocket: ws://%s:%d/ws/trials/{trial_id}/stream", host, port)
    if static_dir:
        LOGGER.info("Static files: %s", static_dir)

    await run_arena_server(
        host=host,
        port=port,
        trace_backend=trace_backend,
        trace_query_endpoint=trace_query_endpoint,
        static_dir=static_dir,
        service_name=service_name,
    )
    return 0


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
        if args.command == "list-trials":
            return asyncio.run(_list_trials_command(args))
        if args.command == "list-sources":
            return asyncio.run(_list_sources_command(args))
        if args.command == "remove-source":
            return asyncio.run(_remove_source_command(args))
        if args.command == "clear-schedules":
            return asyncio.run(_clear_schedules_command(args))
        raise DojoZeroCLIError(f"unknown command '{args.command}'")
    except DojoZeroCLIError as exc:
        LOGGER.error(str(exc))
        return 1
    except KeyboardInterrupt:
        LOGGER.error("interrupted")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
