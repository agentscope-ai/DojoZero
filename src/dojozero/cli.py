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
    Dashboard,
    DashboardError,
    FileSystemDashboardStore,
    InMemoryDashboardStore,
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

DEFAULT_IMPORTS: tuple[str, ...] = ("dojozero.samples", "dojozero.nba_moneyline")
DEFAULT_CLI_CONFIG: Mapping[str, Any] = {
    "store": {
        "kind": "filesystem",
        "root": "./dojozero-store",
    },
    "runtime": {
        "kind": "local",
    },
    "imports": ["dojozero.samples", "dojozero.nba_moneyline"],
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

    replay_parser = subparsers.add_parser(
        "replay",
        help="Replay events from a file for backtesting",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    replay_parser.add_argument(
        "--replay-file",
        type=Path,
        required=True,
        help="Path to JSONL replay file containing events",
    )
    replay_parser.add_argument(
        "--params",
        type=Path,
        required=True,
        help="Path to the trial-builder params YAML (required for agent/stream setup)",
    )
    replay_parser.add_argument(
        "--trial-id",
        help="Override the trial id (defaults to a random UUID)",
    )
    replay_parser.add_argument(
        "--replay-speed-up",
        type=float,
        default=1.0,
        dest="replay_speed_up",
        help="Replay speed multiplier (e.g., 2.0 for 2x speed, 0.5 for half speed). Default: 1.0 (real-time)",
    )
    replay_parser.add_argument(
        "--replay-max-sleep",
        type=float,
        default=20.0,
        dest="replay_max_sleep",
        help="Maximum sleep time in seconds between events (caps long delays). Default: 20.0 seconds",
    )
    replay_parser.add_argument(
        "--server",
        help="Submit replay to a running Dashboard Server (e.g., http://localhost:8000). "
        "The server must have access to the replay file at the same path.",
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
        "--otlp-endpoint",
        dest="otlp_endpoint",
        help="OTLP endpoint URL for external trace storage. "
        "If not provided, enables built-in Trace Query API.",
    )

    # Frontend Server command
    frontend_parser = subparsers.add_parser(
        "frontend",
        help="Start the Frontend Server for WebSocket streaming",
        description="Launch the Frontend Server for real-time span streaming to browsers.",
    )
    frontend_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address to bind to (default: 127.0.0.1).",
    )
    frontend_parser.add_argument(
        "--port",
        type=int,
        default=3001,
        help="Port to listen on (default: 3001).",
    )
    frontend_parser.add_argument(
        "--trace-store",
        dest="trace_store",
        required=True,
        help="URL to Trace Store (Dashboard or Jaeger). "
        "Example: http://localhost:8000 (Dashboard) or http://localhost:16686 (Jaeger).",
    )
    frontend_parser.add_argument(
        "--static-dir",
        dest="static_dir",
        type=Path,
        help="Path to built frontend assets to serve (optional).",
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
) -> InMemoryDashboardStore | FileSystemDashboardStore:
    store_cfg = payload.get("store") or {}
    if not isinstance(store_cfg, Mapping):
        raise DojoZeroCLIError("config.store must be a mapping when provided")
    kind = str(store_cfg.get("kind", "memory")).lower()
    if kind == "memory":
        return InMemoryDashboardStore()
    if kind == "filesystem":
        root = store_cfg.get("root")
        base_path = (
            Path(str(root)) if root is not None else Path.cwd() / "dojozero-store"
        )
        return FileSystemDashboardStore(base_path)
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
    dashboard: Dashboard,
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
            checkpoint = await dashboard.checkpoint_trial(trial_id)
        except DashboardError as exc:
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
            await dashboard.stop_trial(trial_id)
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

    # Submit to server
    base_url = server_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        LOGGER.info("Submitting trial to server: %s", base_url)
        response = await client.post(
            f"{base_url}/api/trials",
            json=request_payload,
        )

        if response.status_code == 201:
            result = response.json()
            LOGGER.info(
                "Trial '%s' submitted successfully (phase: %s)",
                result.get("id"),
                result.get("phase"),
            )
            return 0
        else:
            error = response.json().get("error", response.text)
            raise DojoZeroCLIError(f"Failed to submit trial: {error}")


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
    dashboard = Dashboard(store=store, runtime_provider=runtime_provider)

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

    if spec is not None:
        await _run_trial_and_monitor(
            dashboard=dashboard,
            trial_id=trial_id,
            start_fn=lambda: dashboard.launch_trial(spec),
        )
    else:
        resume_checkpoint = checkpoint_id if checkpoint_id else None
        await _run_trial_and_monitor(
            dashboard=dashboard,
            trial_id=trial_id,
            start_fn=lambda: dashboard.resume_trial(trial_id, resume_checkpoint),
        )
    return 0


async def _submit_replay_to_server(
    server_url: str,
    params_payload: MutableMapping[str, Any],
    trial_id: str | None,
    replay_file: Path,
    speed_up: float,
    max_sleep: float,
) -> int:
    """Submit a replay trial to a remote Dashboard Server."""
    import httpx

    request_payload: dict[str, Any] = {
        "params": dict(params_payload),
        "replay": {
            "file": str(replay_file.absolute()),
            "speed_up": speed_up,
            "max_sleep": max_sleep,
        },
    }
    if trial_id:
        request_payload["trial_id"] = trial_id

    base_url = server_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        LOGGER.info("Submitting replay trial to server: %s", base_url)
        response = await client.post(
            f"{base_url}/api/trials",
            json=request_payload,
        )

        if response.status_code in (200, 201, 202):
            result = response.json()
            LOGGER.info(
                "Replay trial '%s' submitted successfully (phase: %s)",
                result.get("id"),
                result.get("phase"),
            )
            return 0
        else:
            error = response.json().get("error", response.text)
            raise DojoZeroCLIError(f"Failed to submit replay trial: {error}")


async def _replay_command(args: argparse.Namespace) -> int:
    """Handle replay command."""
    from uuid import uuid4

    params_payload = _load_yaml_mapping(args.params, label="params")
    trial_id = args.trial_id or uuid4().hex
    replay_file = args.replay_file
    speed_up = args.replay_speed_up
    max_sleep = args.replay_max_sleep

    if not replay_file.exists():
        raise DojoZeroCLIError(f"Replay file not found: {replay_file}")

    if speed_up <= 0:
        raise DojoZeroCLIError(f"Replay speed-up must be positive, got: {speed_up}")

    if max_sleep <= 0:
        raise DojoZeroCLIError(f"Replay max-sleep must be positive, got: {max_sleep}")

    # Check if submitting to a remote server
    server_url = getattr(args, "server", None)
    if server_url:
        return await _submit_replay_to_server(
            server_url=server_url,
            params_payload=params_payload,
            trial_id=args.trial_id,
            replay_file=replay_file,
            speed_up=speed_up,
            max_sleep=max_sleep,
        )

    # Local execution mode
    config_payload = _load_cli_config(args.setting)

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
    dashboard = Dashboard(store=store, runtime_provider=runtime_provider)

    # Prepare trial spec from params
    spec = _prepare_trial_spec(trial_id, params_payload)

    # Extract builder_name from scenario (it's not in metadata by default)
    scenario = params_payload.get("scenario", {})
    builder_name = scenario.get("name") if isinstance(scenario, dict) else None
    if not builder_name:
        raise DojoZeroCLIError("Could not determine builder name from params")

    # Add replay metadata
    spec.metadata["replay_file"] = str(replay_file)
    spec.metadata["replay_mode"] = True
    spec.metadata["replay_speed_up"] = speed_up
    spec.metadata["replay_max_sleep"] = max_sleep
    spec.metadata["builder_name"] = builder_name  # Ensure it's in metadata

    # Extract hub_id from spec (from stream configs)
    hub_id = None
    for stream_spec in spec.data_streams:
        config = stream_spec.config
        if config.get("hub_id"):
            hub_id = config["hub_id"]
            break

    if not hub_id:
        # Fallback: use default hub_id from params or metadata
        hub_id_raw = spec.metadata.get("hub_id", "data_hub")
        hub_id = str(hub_id_raw) if hub_id_raw else "data_hub"

    # Ensure hub_id is a string
    hub_id = str(hub_id)

    # Create DataHub in replay mode (disable persistence, will load events from file)
    hub = DataHub(
        hub_id=hub_id,
        persistence_file=None,  # Not persisting during replay
        enable_persistence=False,
    )

    # Create ReplayCoordinator with speed control
    from dojozero.data import ReplayCoordinator

    coordinator = ReplayCoordinator(data_hub=hub, replay_file=replay_file)
    coordinator.set_speed(speed_up=speed_up, max_sleep=max_sleep)

    # Set up progress callback
    def progress_callback(current: int, total: int) -> None:
        if current % max(1, min(100, total // 10)) == 0:
            progress_pct = (current / total) * 100
            LOGGER.info(
                "Replay progress: %d/%d events (%.1f%%)", current, total, progress_pct
            )

    coordinator.set_progress_callback(progress_callback)

    # Load events into hub
    LOGGER.info("Loading events from replay file: %s", replay_file)
    await coordinator.start_replay()

    # Create a replay-specific context builder that returns our replay hub
    # We'll temporarily override the context builder in the trial builder registry
    from dojozero.core._registry import get_trial_builder_definition

    try:
        builder_def = get_trial_builder_definition(builder_name)
        original_context_builder = builder_def.context_builder

        # Create replay context builder
        def replay_context_builder(spec: TrialSpec) -> dict[str, Any]:
            """Replay-specific context builder that provides replay hub, no stores."""
            return {
                "data_hubs": {hub_id: hub},
                "stores": {},  # No stores in replay mode
            }

        # Temporarily override context builder
        builder_def.context_builder = replay_context_builder

        # Launch trial (will use our replay context)
        LOGGER.info("Launching trial '%s' in replay mode", trial_id)
        await dashboard.launch_trial(spec)

        # Restore original context builder
        builder_def.context_builder = original_context_builder
    except Exception as e:
        LOGGER.error("Failed to set up replay context: %s", e)
        raise DojoZeroCLIError(f"Failed to set up replay: {e}") from e

    # Start replay with speed control and progress tracking
    LOGGER.info(
        "Starting replay at %.1fx speed (max sleep: %.1fs)", speed_up, max_sleep
    )
    await coordinator.replay_all()

    # Stop trial
    LOGGER.info("Stopping trial '%s'", trial_id)
    await dashboard.stop_trial(trial_id)
    coordinator.stop_replay()

    LOGGER.info("Replay complete for trial '%s'", trial_id)
    return 0


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


async def _serve_command(args: argparse.Namespace) -> int:
    """Handle serve command - start Dashboard Server."""
    from dojozero.core._dashboard_server import run_dashboard_server

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
    dashboard = Dashboard(store=store, runtime_provider=runtime_provider)

    host = args.host
    port = args.port
    otlp_endpoint = getattr(args, "otlp_endpoint", None)

    LOGGER.info("Starting Dashboard Server at http://%s:%d", host, port)
    LOGGER.info("Trial API: http://%s:%d/api/trials", host, port)
    if otlp_endpoint:
        LOGGER.info("Traces will be sent to OTLP endpoint: %s", otlp_endpoint)
    else:
        LOGGER.info("Trials API: http://%s:%d/api/trials", host, port)

    await run_dashboard_server(
        dashboard=dashboard,
        host=host,
        port=port,
        otlp_endpoint=otlp_endpoint,
    )
    return 0


async def _frontend_command(args: argparse.Namespace) -> int:
    """Handle frontend command - start Frontend Server."""
    from dojozero.core._frontend_server import run_frontend_server

    host = args.host
    port = args.port
    trace_store = args.trace_store
    static_dir = getattr(args, "static_dir", None)

    LOGGER.info("Starting Frontend Server at http://%s:%d", host, port)
    LOGGER.info("Trace Store: %s", trace_store)
    LOGGER.info("WebSocket: ws://%s:%d/ws/trials/{trial_id}/stream", host, port)
    if static_dir:
        LOGGER.info("Static files: %s", static_dir)

    await run_frontend_server(
        trace_store_url=trace_store,
        host=host,
        port=port,
        static_dir=static_dir,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)

    try:
        if args.command == "run":
            return asyncio.run(_run_command(args))
        if args.command == "replay":
            return asyncio.run(_replay_command(args))
        if args.command == "list-builders":
            return _list_builders_command(args)
        if args.command == "get-builder":
            return _get_builder_command(args)
        if args.command == "serve":
            return asyncio.run(_serve_command(args))
        if args.command == "frontend":
            return asyncio.run(_frontend_command(args))
        raise DojoZeroCLIError(f"unknown command '{args.command}'")
    except DojoZeroCLIError as exc:
        LOGGER.error(str(exc))
        return 1
    except KeyboardInterrupt:
        LOGGER.error("interrupted")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
