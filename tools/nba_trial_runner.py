#!/usr/bin/env python3
"""NBA Trial Runner

Orchestrates betting trials for NBA games:
- Checks NBA API for daily games
- Sets up separate trial/config for each game
- Starts trial 2 hours before game time
- Runs agents that analyze data and place bets
- Runs until game concludes
- Persists all events to event files (for backtesting)

When using --server flag, trials are submitted to a Dashboard Server which handles:
- SLS trace export (via --otlp-endpoint on server)
- OSS backup (via --oss-backup on server)

Usage:
    # Local mode (no SLS/OSS integration)
    python nba_trial_runner.py run --data-dir outputs

    # Server mode (with SLS/OSS via Dashboard Server)
    # First start: dojo0 serve --otlp-endpoint https://... --trace-backend sls
    python nba_trial_runner.py run --data-dir outputs --server http://localhost:8000
"""

import argparse
import asyncio
import hashlib
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from dateutil import parser

# Add parent directory to path to import dojozero modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dojozero.data.nba._utils import get_game_info_by_id, get_games_for_date

logger = logging.getLogger(__name__)


class GameTrialManager:
    """Manages trial lifecycle for a single NBA game."""

    def __init__(
        self,
        game: dict[str, Any],
        base_config: Path,
        pre_start_hours: float = 2.0,
        check_interval_seconds: float = 60.0,
        data_dir: Path | None = None,
        game_date: str | None = None,
        log_level: str = "INFO",
        server: str | None = None,
    ):
        """Initialize game trial manager.

        Args:
            game: Game dictionary from NBA API
            base_config: Path to base config template
            pre_start_hours: Hours before game to start trial (default: 2.0)
            check_interval_seconds: Interval to check game status (default: 60.0)
            data_dir: If provided, use {data_dir}/{date}/{game_id}.yaml and {data_dir}/{date}/{game_id}.jsonl
                      If None, use defaults: configs/ and outputs/
            game_date: Date string (YYYY-MM-DD) for date-organized structure
            log_level: Logging level for subprocess (default: INFO)
            server: Dashboard Server URL (e.g., http://localhost:8000). If provided,
                    trials are submitted to the server which handles SLS/OSS.
        """
        self.game = game
        self.game_id = str(game.get("gameId", ""))
        self.base_config = base_config
        self.pre_start_hours = pre_start_hours
        self.check_interval_seconds = check_interval_seconds
        self.data_dir = data_dir
        self.game_date = game_date
        self.log_level = log_level
        self.server = server

        # Parse game time
        self.game_time_utc: datetime | None = None
        if game.get("gameTimeUTC"):
            try:
                self.game_time_utc = parser.parse(game["gameTimeUTC"])
                if self.game_time_utc.tzinfo is None:
                    self.game_time_utc = self.game_time_utc.replace(tzinfo=timezone.utc)
            except Exception as e:
                logger.warning("Could not parse game time for %s: %s", self.game_id, e)

        # Trial state
        self.trial_id: str | None = None
        self.config_file: Path | None = None
        self.events_file: Path | None = None
        self.log_file: Path | None = None
        self.process: subprocess.Popen | None = None
        self._log_file_handle = None  # Store file handle for proper cleanup
        self.started = False
        self.completed = False
        self._logger: logging.Logger | None = None
        self._server_confirmed_running = (
            False  # Track if server confirmed trial is running
        )
        self._subprocess_handled = False  # Track if subprocess exit was already handled
        self._initial_game_status: int | None = (
            None  # Track game status at monitoring start
        )
        self._grace_period_seconds: float = (
            300.0  # 5 min grace period for already-finished games
        )

    async def check_server_trial_status(self) -> str | None:
        """Check if trial is running on the server.

        Returns:
            Trial phase ('running', 'stopped', etc.) or None if not found/error
        """
        if not self.server or not self.trial_id:
            return None

        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.server.rstrip('/')}/api/trials/{self.trial_id}/status"
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("phase")
                return None
        except Exception as e:
            self.log(logging.DEBUG, "Failed to check server status: %s", e)
            return None

    def generate_config_file(self) -> Path:
        """Generate config file for this game.

        Returns:
            Path to generated config file
        """
        # Load base config
        with open(self.base_config, "r") as f:
            config = yaml.safe_load(f)

        # Update espn_game_id
        config["scenario"]["config"]["espn_game_id"] = self.game_id

        # Determine file paths
        if self.data_dir:
            # Data dir structure: {data_dir}/{date}/{game_id}.yaml, {game_id}.jsonl, {game_id}.log
            if not self.game_date:
                # If data_dir is provided but no game_date, use today's date
                self.game_date = datetime.now().strftime("%Y-%m-%d")
            date_dir = self.data_dir / self.game_date
            config_file = date_dir / f"{self.game_id}.yaml"
            events_file = date_dir / f"{self.game_id}.jsonl"
            log_file = date_dir / f"{self.game_id}.log"
        else:
            # Flat structure: use default directories
            project_root = Path(__file__).parent.parent
            configs_dir = project_root / "configs"
            outputs_dir = project_root / "outputs"
            config_file = configs_dir / f"nba-moneyline_{self.game_id}.yaml"
            events_file = outputs_dir / f"nba_betting_events_{self.game_id}.jsonl"
            log_file = outputs_dir / f"{self.game_id}.log"

        # Update persistence file in config
        if "hub" not in config["scenario"]["config"]:
            config["scenario"]["config"]["hub"] = {}
        config["scenario"]["config"]["hub"]["persistence_file"] = str(events_file)

        # Create directory structure
        config_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.parent.mkdir(parents=True, exist_ok=True)

        # Save config file
        with open(config_file, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        self.config_file = config_file
        self.events_file = events_file
        self.log_file = log_file

        # Generate unique trial ID with hash postfix to avoid conflicts
        # Hash includes game_id, date, and timestamp for uniqueness
        timestamp = datetime.now(timezone.utc).isoformat()
        hash_input = f"{self.game_id}-{self.game_date or 'unknown'}-{timestamp}"
        hash_suffix = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
        self.trial_id = f"nba-game-{self.game_id}-{hash_suffix}"

        # Set up file logger for this game
        self._setup_file_logger()

        logger.info(
            "Generated config for game %s: %s (events: %s, log: %s)",
            self.game_id,
            config_file,
            events_file,
            log_file,
        )

        return config_file

    def _setup_file_logger(self) -> None:
        """Set up file logger for this game."""
        if not self.log_file:
            return

        # Create logger for this game
        logger = logging.getLogger(f"game_{self.game_id}")
        logger.setLevel(logging.DEBUG)

        # Remove existing handlers to avoid duplicates
        logger.handlers.clear()

        # File handler
        file_handler = logging.FileHandler(self.log_file, mode="a", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        # Don't propagate to root logger (avoid duplicate logs)
        logger.propagate = False

        self._logger = logger

    def log(self, level: int, message: str, *args: Any) -> None:
        """Log a message to both console and file logger.

        Args:
            level: Logging level (logging.DEBUG, INFO, WARNING, ERROR)
            message: Log message (can contain format specifiers)
            *args: Arguments for message formatting
        """
        # Log to console (via root logger)
        logger.log(level, message, *args)

        # Log to file logger if available
        if self._logger:
            self._logger.log(level, message, *args)

    def calculate_start_time(self) -> datetime | None:
        """Calculate when to start the trial (2 hours before game).

        Returns:
            Start time in UTC, or None if game time unavailable
        """
        if not self.game_time_utc:
            return None

        start_time = self.game_time_utc - timedelta(hours=self.pre_start_hours)
        return start_time

    async def wait_until_start_time(self) -> bool:
        """Wait until it's time to start the trial.

        Returns:
            True if start time reached, False if game already started/finished
        """
        start_time = self.calculate_start_time()
        if not start_time:
            logger.warning("Cannot determine start time for game %s", self.game_id)
            return False

        now = datetime.now(timezone.utc)

        # If start time already passed, start immediately
        if start_time <= now:
            logger.info(
                "Start time already passed for game %s (was %s, now %s)",
                self.game_id,
                start_time,
                now,
            )
            return True

        # Wait until start time
        wait_seconds = (start_time - now).total_seconds()
        self.log(
            logging.INFO,
            "Scheduled trial for game %s to start in %.1f seconds (at %s)",
            self.game_id,
            wait_seconds,
            start_time,
        )

        await asyncio.sleep(wait_seconds)
        return True

    async def start_trial(self) -> bool:
        """Start the trial process.

        Returns:
            True if started successfully, False otherwise
        """
        if self.started:
            logger.warning("Trial already started for game %s", self.game_id)
            return False

        if not self.config_file:
            self.generate_config_file()

        # Build dojo0 run command
        # Note: --log-level is a top-level argument, must come before "run"
        cmd = [
            sys.executable,
            "-m",
            "dojozero.cli",
            "--log-level",
            self.log_level,
            "run",
            "--params",
            str(self.config_file),
            "--trial-id",
            self.trial_id,
        ]

        # If server is specified, submit to Dashboard Server
        # Server handles SLS trace export and OSS backup
        if self.server:
            cmd.extend(["--server", self.server])

        self.log(
            logging.INFO, "Starting trial for game %s: %s", self.game_id, " ".join(cmd)
        )

        try:
            # Start process (non-blocking)
            # Redirect stdout/stderr to log file if available, otherwise to PIPE
            if self.log_file:
                # Open log file in append mode with unbuffered writes
                # This ensures immediate writes and proper isolation per process
                self._log_file_handle = open(
                    self.log_file,
                    "a",
                    encoding="utf-8",
                    buffering=1,  # Line buffered for immediate writes
                )

                # Set environment variable for subprocess (future enhancement)
                env = os.environ.copy()
                env["DOJOZERO_LOG_FILE"] = str(self.log_file)

                self.process = subprocess.Popen(
                    cmd,
                    stdout=self._log_file_handle,
                    stderr=subprocess.STDOUT,  # Merge stderr into stdout
                    # Note: Python's logging.basicConfig() writes to stderr by default.
                    # By merging stderr into stdout, all logger.info() calls in the
                    # subprocess will be captured and written to the log file.
                    text=True,
                    env=env,  # Pass environment with log file path
                    # Ensure process writes are isolated
                    bufsize=1,  # Line buffered for immediate writes
                )
                # File handle will be closed when process terminates
            else:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            self.started = True

            self.log(
                logging.INFO,
                "✓ Trial started for game %s (PID: %d, trial_id: %s)",
                self.game_id,
                self.process.pid,
                self.trial_id,
            )

            return True
        except Exception as e:
            self.log(
                logging.ERROR, "Failed to start trial for game %s: %s", self.game_id, e
            )
            return False

    async def _get_current_game_status(self) -> int | None:
        """Get current game status from NBA API.

        Returns:
            Game status (1=scheduled, 2=in progress, 3=finished) or None if error
        """
        try:
            check_date = self.game_date or datetime.now().strftime("%Y-%m-%d")
            games = get_games_for_date(check_date, print_games=False)
            current_game = next(
                (g for g in games if str(g.get("gameId")) == self.game_id), None
            )
            if current_game:
                return current_game.get("gameStatus")
            return None
        except Exception as e:
            self.log(
                logging.WARNING,
                "Error getting game status for %s: %s",
                self.game_id,
                e,
            )
            return None

    async def monitor_trial(self) -> None:
        """Monitor trial process and game status until game concludes."""
        if not self.started or not self.process:
            logger.warning(
                "Cannot monitor trial for game %s (not started)", self.game_id
            )
            return

        self.log(
            logging.INFO,
            "Monitoring trial for game %s until game concludes",
            self.game_id,
        )

        # Wait for initial data collection before checking game status.
        # This ensures at least one polling cycle completes.
        initial_wait_seconds = 90
        self.log(
            logging.INFO,
            "Waiting %d seconds for initial data collection before monitoring game status",
            initial_wait_seconds,
        )
        await asyncio.sleep(initial_wait_seconds)

        # Record initial game status to detect already-finished games
        monitoring_start_time = datetime.now(timezone.utc)
        self._initial_game_status = await self._get_current_game_status()
        if self._initial_game_status == 3:
            self.log(
                logging.INFO,
                "Game %s was already finished at monitoring start. "
                "Will allow %.0f second grace period for data processing.",
                self.game_id,
                self._grace_period_seconds,
            )

        while True:
            # Check if process is still running (only handle exit once)
            if self.process.poll() is not None and not self._subprocess_handled:
                # Process ended - mark as handled
                self._subprocess_handled = True
                return_code = self.process.returncode
                stdout, stderr = self.process.communicate()

                if return_code == 0:
                    # In server mode, CLI exits after submission - trial runs on server
                    # Continue monitoring game status until game concludes
                    if self.server:
                        if not self._server_confirmed_running:
                            server_phase = await self.check_server_trial_status()
                            if server_phase == "running":
                                self._server_confirmed_running = True
                                self.log(
                                    logging.INFO,
                                    "✓ Trial submitted and running on server for game %s",
                                    self.game_id,
                                )
                                # Continue monitoring game status - don't break
                            elif server_phase in ("stopped", "failed"):
                                self.log(
                                    logging.INFO,
                                    "Trial completed on server for game %s (phase: %s)",
                                    self.game_id,
                                    server_phase,
                                )
                                self.completed = True
                                break
                            else:
                                # Unknown state, continue monitoring
                                self.log(
                                    logging.WARNING,
                                    "Trial status unknown for game %s (phase: %s), "
                                    "continuing to monitor",
                                    self.game_id,
                                    server_phase,
                                )
                        # If already confirmed running, continue monitoring
                    else:
                        # Local mode - process completed means trial completed
                        self.log(
                            logging.INFO,
                            "✓ Trial process completed for game %s (trial_id: %s)",
                            self.game_id,
                            self.trial_id,
                        )
                        break
                else:
                    # Subprocess failed - but check if trial is running on server
                    # This can happen when the CLI times out waiting for server response
                    # but the server actually launched the trial successfully
                    if self.server and not self._server_confirmed_running:
                        server_phase = await self.check_server_trial_status()
                        if server_phase == "running":
                            self._server_confirmed_running = True
                            self.log(
                                logging.INFO,
                                "✓ Trial is running on server for game %s "
                                "(CLI subprocess timed out but server launched trial)",
                                self.game_id,
                            )
                            # Continue monitoring - don't break
                        else:
                            self.log(
                                logging.ERROR,
                                "✗ Trial process failed for game %s "
                                "(trial_id: %s, return_code: %d, server_phase: %s)",
                                self.game_id,
                                self.trial_id,
                                return_code,
                                server_phase,
                            )
                            if stderr:
                                self.log(
                                    logging.ERROR, "Stderr: %s", stderr[:500]
                                )  # First 500 chars
                            break
                    else:
                        self.log(
                            logging.ERROR,
                            "✗ Trial process failed for game %s (trial_id: %s, return_code: %d)",
                            self.game_id,
                            self.trial_id,
                            return_code,
                        )
                        if stderr:
                            self.log(
                                logging.ERROR, "Stderr: %s", stderr[:500]
                            )  # First 500 chars
                        break

            # Check game status
            # Use the game's actual date, not today's date
            game_status = await self._get_current_game_status()
            if game_status == 3:
                # Game is finished - check if we should stop
                if self._initial_game_status == 3:
                    # Game was already finished at monitoring start
                    # Apply grace period before stopping
                    elapsed = (
                        datetime.now(timezone.utc) - monitoring_start_time
                    ).total_seconds()
                    if elapsed >= self._grace_period_seconds:
                        self.log(
                            logging.INFO,
                            "Game %s was already finished; grace period (%.0fs) elapsed. "
                            "Stopping trial (trial_id: %s)",
                            self.game_id,
                            self._grace_period_seconds,
                            self.trial_id,
                        )
                        await self.stop_trial()
                        self.completed = True
                        break
                    else:
                        remaining = self._grace_period_seconds - elapsed
                        self.log(
                            logging.DEBUG,
                            "Game %s already finished; %.0fs remaining in grace period",
                            self.game_id,
                            remaining,
                        )
                else:
                    # Game transitioned to finished during monitoring - stop immediately
                    self.log(
                        logging.INFO,
                        "Game %s has finished, stopping trial (trial_id: %s)",
                        self.game_id,
                        self.trial_id,
                    )
                    await self.stop_trial()
                    self.completed = True
                    break

            # Wait before next check
            await asyncio.sleep(self.check_interval_seconds)

    async def stop_trial(self) -> None:
        """Stop the trial process.

        In server mode, calls the server's stop endpoint to stop the trial.
        In local mode, terminates the subprocess.
        """
        # In server mode, call the server's stop endpoint
        if self.server and self.trial_id:
            await self._stop_server_trial()

        # Also handle local subprocess if it exists
        if self.process:
            self.log(
                logging.INFO,
                "Stopping local process for game %s (PID: %d)",
                self.game_id,
                self.process.pid,
            )

            try:
                # Send SIGTERM
                self.process.terminate()

                # Wait up to 10 seconds for graceful shutdown
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    # Force kill if not responding
                    self.log(
                        logging.WARNING, "Trial process did not terminate, forcing kill"
                    )
                    self.process.kill()
                    self.process.wait()
            except Exception as e:
                self.log(
                    logging.WARNING,
                    "Error stopping local process for game %s: %s",
                    self.game_id,
                    e,
                )

        # Close log file handle if it exists
        if self._log_file_handle:
            try:
                self._log_file_handle.flush()  # Ensure all data is written
                self._log_file_handle.close()
            except Exception as e:
                self.log(
                    logging.WARNING,
                    "Error closing log file for game %s: %s",
                    self.game_id,
                    e,
                )
            finally:
                self._log_file_handle = None

        self.log(logging.INFO, "✓ Trial stopped for game %s", self.game_id)

    async def _stop_server_trial(self) -> bool:
        """Stop the trial on the server.

        Returns:
            True if stopped successfully, False otherwise
        """
        if not self.server or not self.trial_id:
            return False

        try:
            import httpx

            self.log(
                logging.INFO,
                "Sending stop request to server for trial %s",
                self.trial_id,
            )
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.server.rstrip('/')}/api/trials/{self.trial_id}/stop"
                )
                if response.status_code == 200:
                    data = response.json()
                    self.log(
                        logging.INFO,
                        "✓ Server trial stopped: %s (phase: %s)",
                        self.trial_id,
                        data.get("phase"),
                    )
                    return True
                elif response.status_code == 404:
                    self.log(
                        logging.WARNING,
                        "Trial %s not found on server (may have already completed)",
                        self.trial_id,
                    )
                    return False
                else:
                    self.log(
                        logging.WARNING,
                        "Failed to stop server trial %s: %s",
                        self.trial_id,
                        response.text,
                    )
                    return False
        except Exception as e:
            self.log(
                logging.ERROR,
                "Error stopping server trial %s: %s",
                self.trial_id,
                e,
            )
            return False

    def log_status(self) -> None:
        """Log crucial status information."""
        status_parts = [
            f"Game: {self.game_id}",
            f"Trial ID: {self.trial_id}",
            f"Started: {self.started}",
            f"Completed: {self.completed}",
        ]

        if self.config_file:
            status_parts.append(f"Config: {self.config_file}")
        if self.events_file:
            status_parts.append(f"Events: {self.events_file}")
            # Check if events file exists and get size
            if self.events_file.exists():
                size_kb = self.events_file.stat().st_size / 1024
                status_parts.append(f"Events size: {size_kb:.1f} KB")

        if self.log_file:
            status_parts.append(f"Log: {self.log_file}")
            # Check if log file exists and get size
            if self.log_file.exists():
                size_kb = self.log_file.stat().st_size / 1024
                status_parts.append(f"Log size: {size_kb:.1f} KB")

        if self.game_time_utc:
            status_parts.append(f"Game time: {self.game_time_utc}")

        status_msg = "STATUS: " + " | ".join(status_parts)
        logger.info(status_msg)
        if self._logger:
            self._logger.info(status_msg)


async def run_trial_for_game(
    game_id: str,
    base_config: Path,
    pre_start_hours: float = 2.0,
    check_interval_seconds: float = 60.0,
    data_dir: Path | None = None,
    log_level: str = "INFO",
    server: str | None = None,
) -> list[GameTrialManager]:
    """Run trial for a specific game by ID.

    Searches across recent dates to find the game, then sets up and runs the trial.
    If the game is not found, returns an empty list.

    Args:
        game_id: Game ID to run trial for
        base_config: Path to base config template
        pre_start_hours: Hours before game to start trial
        check_interval_seconds: Interval to check game status
        data_dir: If provided, use {data_dir}/{date}/{game_id}.yaml and {data_dir}/{date}/{game_id}.jsonl
                  If None, use defaults: configs/ and outputs/
        log_level: Logging level for subprocess
        server: Dashboard Server URL for SLS/OSS integration

    Returns:
        List with single GameTrialManager instance, or empty list if game not found
    """
    # First, find which date the game is on
    logger.info("Searching for game ID: %s", game_id)
    game_info = get_game_info_by_id(game_id)

    if not game_info:
        logger.error("Game ID %s not found in recent dates", game_id)
        return []

    # Extract the date from game_info
    game_date_str = game_info.get_game_date_us()
    if not game_date_str:
        logger.error("Game found but missing date information")
        return []

    logger.info("Found game %s on date %s", game_id, game_date_str)

    # Fetch full game data for that date
    games = get_games_for_date(game_date_str, print_games=False)

    if not games:
        logger.error("No games found for date %s", game_date_str)
        return []

    # Extract the specific game
    game = next((g for g in games if str(g.get("gameId", "")) == game_id), None)
    if not game:
        logger.error(
            "Game ID %s not found in games for date %s", game_id, game_date_str
        )
        return []

    # Create manager for this game
    manager = GameTrialManager(
        game=game,
        base_config=base_config,
        pre_start_hours=pre_start_hours,
        check_interval_seconds=check_interval_seconds,
        data_dir=data_dir,
        game_date=game_date_str if data_dir else None,
        log_level=log_level,
        server=server,
    )
    manager.generate_config_file()
    manager.log_status()

    return [manager]


async def run_trials_for_date(
    game_date: datetime | str,
    base_config: Path,
    pre_start_hours: float = 2.0,
    check_interval_seconds: float = 60.0,
    data_dir: Path | None = None,
    log_level: str = "INFO",
    server: str | None = None,
) -> list[GameTrialManager]:
    """Run trials for all games on a given date.

    Args:
        game_date: Date to run trials for
        base_config: Path to base config template
        pre_start_hours: Hours before game to start trial
        check_interval_seconds: Interval to check game status
        data_dir: If provided, use {data_dir}/{date}/{game_id}.yaml and {data_dir}/{date}/{game_id}.jsonl
                  If None, use defaults: configs/ and outputs/
        log_level: Logging level for subprocess
        server: Dashboard Server URL for SLS/OSS integration

    Returns:
        List of GameTrialManager instances
    """
    # Parse date string
    if isinstance(game_date, datetime):
        date_str = game_date.strftime("%Y-%m-%d")
    else:
        date_str = game_date

    # Get games for date
    logger.info("Fetching games for date: %s", game_date)
    games = get_games_for_date(game_date, print_games=True)

    if not games:
        logger.info("No games found for date %s", game_date)
        return []

    # Create managers for each game
    managers: list[GameTrialManager] = []
    for game in games:
        game_id_str = str(game.get("gameId", ""))
        if not game_id_str:
            logger.warning("Skipping game without gameId: %s", game)
            continue

        manager = GameTrialManager(
            game=game,
            base_config=base_config,
            pre_start_hours=pre_start_hours,
            check_interval_seconds=check_interval_seconds,
            data_dir=data_dir,
            game_date=(
                date_str if data_dir else None
            ),  # Pass date_str when data_dir is set
            log_level=log_level,
            server=server,
        )
        managers.append(manager)

        # Generate config file immediately
        manager.generate_config_file()
        manager.log_status()

    return managers


async def run_trials(
    managers: list[GameTrialManager],
    max_concurrent_starts: int = 10,
) -> None:
    """Run trials for all game managers.

    Args:
        managers: List of GameTrialManager instances
        max_concurrent_starts: Maximum number of trials to start concurrently (default: 10).
            This prevents overwhelming the server when multiple games have
            similar start times.
    """
    tasks = []
    # Semaphore to limit concurrent trial starts
    start_semaphore = asyncio.Semaphore(max_concurrent_starts)

    for manager in managers:
        # Create task for each game
        async def run_game(manager: GameTrialManager) -> None:
            try:
                # Wait until start time
                should_start = await manager.wait_until_start_time()
                if not should_start:
                    logger.warning("Skipping start for game %s", manager.game_id)
                    return

                # Use semaphore to limit concurrent trial starts
                async with start_semaphore:
                    # Add small delay to stagger starts
                    await asyncio.sleep(1.0)
                    # Start trial
                    started = await manager.start_trial()
                    if not started:
                        logger.error(
                            "Failed to start trial for game %s", manager.game_id
                        )
                        return

                # Monitor until game concludes
                await manager.monitor_trial()

                # Log final status
                # Note: SLS trace export and OSS backup are handled by Dashboard Server
                manager.log_status()

            except Exception as e:
                logger.error("Error in trial for game %s: %s", manager.game_id, e)
                manager.log_status()

        tasks.append(asyncio.create_task(run_game(manager)))

    # Wait for all tasks
    await asyncio.gather(*tasks)

    # Log final status for all games
    logger.info("=" * 80)
    logger.info("FINAL STATUS FOR ALL GAMES:")
    for manager in managers:
        manager.log_status()
    logger.info("=" * 80)


def list_games_in_range(
    start_date: str | None = None,
    end_date: str | None = None,
) -> None:
    """List games in a date range.

    Args:
        start_date: Start date (YYYY-MM-DD). If None, defaults to today.
        end_date: End date (YYYY-MM-DD). If None, defaults to start_date (single day).
    """
    # Parse start date
    if start_date:
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError:
            logger.error(
                "Invalid start date format: %s (expected YYYY-MM-DD)", start_date
            )
            return
    else:
        start = datetime.now()

    # Parse end date
    if end_date:
        try:
            end = datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            logger.error("Invalid end date format: %s (expected YYYY-MM-DD)", end_date)
            return
    else:
        end = start

    # Ensure start <= end
    if start > end:
        start, end = end, start

    # Iterate through date range
    current = start
    total_games = 0

    print(f"\n{'=' * 80}")
    print(f"NBA Games from {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
    print(f"{'=' * 80}\n")

    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        games = get_games_for_date(date_str, print_games=False)

        if games:
            print(f"Date: {date_str} ({len(games)} game(s))")
            print("-" * 40)
            for game in games:
                game_id = game.get("gameId", "")
                away_team = game.get("awayTeam", {}).get("teamName", "Unknown")
                home_team = game.get("homeTeam", {}).get("teamName", "Unknown")
                away_tricode = game.get("awayTeam", {}).get("teamTricode", "???")
                home_tricode = game.get("homeTeam", {}).get("teamTricode", "???")
                status_text = game.get("gameStatusText", "Unknown")
                game_status = game.get("gameStatus", 0)

                # Format time
                time_str = "N/A"
                if game.get("gameTimeLTZ"):
                    time_str = game["gameTimeLTZ"].strftime("%H:%M %Z")

                # Format score if game has started
                score_str = ""
                if game_status in (2, 3):  # In progress or finished
                    away_score = game.get("awayTeam", {}).get("score", 0)
                    home_score = game.get("homeTeam", {}).get("score", 0)
                    score_str = f" | {away_score}-{home_score}"

                print(
                    f"  {game_id}: {away_tricode} @ {home_tricode} "
                    f"({away_team} vs {home_team}) | {time_str} | {status_text}{score_str}"
                )
                total_games += 1
            print()
        else:
            print(f"Date: {date_str} - No games scheduled\n")

        current += timedelta(days=1)

    print(f"{'=' * 80}")
    print(f"Total: {total_games} game(s)")
    print(f"{'=' * 80}\n")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="NBA Trial Runner - Orchestrates betting trials for NBA games"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # List games subcommand
    list_parser = subparsers.add_parser("list", help="List NBA games in a date range")
    list_parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Start date (YYYY-MM-DD). Default: today",
    )
    list_parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="End date (YYYY-MM-DD). Default: same as start date",
    )
    list_parser.add_argument(
        "--log-level",
        type=str,
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: WARNING)",
    )

    # Run trials subcommand
    run_parser = subparsers.add_parser("run", help="Run betting trials for NBA games")
    run_parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Date to run trials for (YYYY-MM-DD). Default: today",
    )
    run_parser.add_argument(
        "--game-id",
        type=str,
        default=None,
        help="Specific game ID to run trial for. If provided, only this game will be processed.",
    )
    run_parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).parent.parent / "trial_params" / "nba-moneyline.yaml",
        help="Path to trial config template (default: trial_params/nba-moneyline.yaml)",
    )
    run_parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Data directory for output: {data-dir}/{date}/{game_id}.yaml and {data-dir}/{date}/{game_id}.jsonl",
    )
    run_parser.add_argument(
        "--pre-start-hours",
        type=float,
        default=2.0,
        help="Hours before game to start trial (default: 2.0)",
    )
    run_parser.add_argument(
        "--check-interval",
        type=float,
        default=60.0,
        help="Interval in seconds to check game status (default: 60.0)",
    )
    run_parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    run_parser.add_argument(
        "--server",
        type=str,
        default=None,
        help="Dashboard Server URL (e.g., http://localhost:8000). "
        "When specified, trials are submitted to the server which handles "
        "SLS trace export and OSS backup.",
    )
    run_parser.add_argument(
        "--max-concurrent-starts",
        type=int,
        default=10,
        help="Maximum number of trials to start concurrently (default: 10). "
        "This prevents overwhelming the server with simultaneous submissions.",
    )

    args = parser.parse_args()

    # Handle no command
    if args.command is None:
        parser.print_help()
        return 0

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Handle list command
    if args.command == "list":
        list_games_in_range(
            start_date=args.start_date,
            end_date=args.end_date,
        )
        return 0

    # Handle run command
    if args.command == "run":
        # Validate config
        if not args.config.exists():
            logger.error("Config file not found: %s", args.config)
            return 1

        # Run trials
        try:
            # If game_id is provided, run trial for specific game
            if args.game_id:
                managers = asyncio.run(
                    run_trial_for_game(
                        game_id=args.game_id,
                        base_config=args.config,
                        pre_start_hours=args.pre_start_hours,
                        check_interval_seconds=args.check_interval,
                        data_dir=args.data_dir,
                        log_level=args.log_level,
                        server=args.server,
                    )
                )
            else:
                # Run trials for all games on date
                if args.date:
                    game_date = args.date
                else:
                    game_date = datetime.now()

                managers = asyncio.run(
                    run_trials_for_date(
                        game_date=game_date,
                        base_config=args.config,
                        pre_start_hours=args.pre_start_hours,
                        check_interval_seconds=args.check_interval,
                        data_dir=args.data_dir,
                        log_level=args.log_level,
                        server=args.server,
                    )
                )

            if not managers:
                logger.info("No games found for trials")
                return 0

            asyncio.run(
                run_trials(managers, max_concurrent_starts=args.max_concurrent_starts)
            )
            return 0

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
            return 130
        except Exception as e:
            logger.error("Fatal error: %s", e, exc_info=True)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
