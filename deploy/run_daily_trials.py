#!/usr/bin/env python3
"""Daily runner script for Betting Trials (NBA/NFL).

Wrapper script that runs trials for games (agents analyze data and place bets).

For SLS trace export and OSS backup, start a Dashboard Server first:
    dojo0 serve --otlp-endpoint https://... --trace-backend sls --oss-backup

Then run this script with --server flag:
    python run_daily_trials.py configs/nba-moneyline.yaml --server http://localhost:8000

Usage:
    python run_daily_trials.py configs/nba-moneyline.yaml
    python run_daily_trials.py configs/nfl-moneyline.yaml --date 2025-01-20
    python run_daily_trials.py configs/nba-moneyline.yaml --server http://localhost:8000
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

# Resolve paths
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent


def load_env_file() -> None:
    """Load environment variables from .env file."""
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        print(f"ERROR: .env file not found at {env_file}", file=sys.stderr)
        print(
            "Please create .env file with required API keys (see .env.template)",
            file=sys.stderr,
        )
        sys.exit(1)

    # Simple .env parser (or use python-dotenv if available)
    try:
        from dotenv import load_dotenv

        load_dotenv(env_file)
    except ImportError:
        # Manual parsing if python-dotenv not available
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())


def detect_trial_type(config_path: Path) -> tuple[str, Path, Path]:
    """Detect trial type from config and return (type, runner_path, data_dir)."""
    with open(config_path) as f:
        config = yaml.safe_load(f)

    scenario_name = config.get("scenario", {}).get("name", "")

    if scenario_name.startswith("nba"):
        return (
            "NBA",
            PROJECT_ROOT / "tools" / "nba_trial_runner.py",
            PROJECT_ROOT / "data" / "nba-betting",
        )
    elif scenario_name.startswith("nfl"):
        return (
            "NFL",
            PROJECT_ROOT / "tools" / "nfl_trial_runner.py",
            PROJECT_ROOT / "data" / "nfl",
        )
    else:
        print(
            f"ERROR: Unknown scenario type in config: {scenario_name}", file=sys.stderr
        )
        print("Expected scenario name starting with 'nba' or 'nfl'", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run daily betting trials (NBA/NFL)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s configs/nba-moneyline.yaml
  %(prog)s configs/nfl-moneyline.yaml --date 2025-01-20
  %(prog)s configs/nba-moneyline.yaml --server http://localhost:8000
        """,
    )
    parser.add_argument("config", type=Path, help="Path to trial config YAML file")
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="Date to run trials for (YYYY-MM-DD, default: today)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Override data directory (default: auto-detected from config)",
    )
    parser.add_argument(
        "--server",
        type=str,
        default=None,
        help="Dashboard Server URL (e.g., http://localhost:8000). "
        "When specified, trials are submitted to the server which handles "
        "SLS trace export and OSS backup.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=24 * 60 * 60,
        help="Timeout in seconds (default: 86400 = 24 hours)",
    )

    args = parser.parse_args()

    # Resolve config path
    config_path = args.config
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path

    if not config_path.exists():
        print(f"ERROR: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    # Load environment
    load_env_file()

    # Detect trial type
    trial_type, runner_path, default_data_dir = detect_trial_type(config_path)
    data_dir = args.data_dir or default_data_dir

    if not runner_path.exists():
        print(f"ERROR: Trial runner not found: {runner_path}", file=sys.stderr)
        sys.exit(1)

    # Ensure data directory exists
    data_dir.mkdir(parents=True, exist_ok=True)

    # Log startup
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] Starting {trial_type} Betting Trials for date: {args.date}")
    print(f"[{timestamp}] Using config: {config_path}")

    # Build command
    cmd = [
        sys.executable,
        str(runner_path),
        "run",
        "--config",
        str(config_path),
        "--data-dir",
        str(data_dir),
        "--date",
        args.date,
        "--log-level",
        args.log_level,
    ]

    if args.server:
        cmd.extend(["--server", args.server])
        print(f"[{timestamp}] Using Dashboard Server: {args.server}")

    # Run trials
    try:
        result = subprocess.run(cmd, timeout=args.timeout)
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"[{timestamp}] ERROR: Trials timed out after {args.timeout} seconds",
            file=sys.stderr,
        )
        sys.exit(1)

    # Summary
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if exit_code == 0:
        date_dir = data_dir / args.date
        if date_dir.exists():
            game_count = len(list(date_dir.glob("*.jsonl")))
            print(f"[{timestamp}] Trials completed: {game_count} game(s)")
        else:
            print(f"[{timestamp}] Trials completed: No games found for {args.date}")
    else:
        print(
            f"[{timestamp}] ERROR: Trials failed with exit code: {exit_code}",
            file=sys.stderr,
        )
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
