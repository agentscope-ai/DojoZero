#!/usr/bin/env python3
"""NBA Game Collector Driver

Orchestrates data collection for NBA games:
- Checks NBA API for daily games
- Sets up separate trial/config for each game
- Starts trial 2 hours before game time
- Uses proper naming (config and replay files with game IDs)
- Runs until game concludes
- Logs crucial trial start/end/saved status
"""

import argparse
import asyncio
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from dateutil import parser

# Add parent directory to path to import agentx modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from demos.nba_api_demo import get_games_for_date
from agentx.data.nba._utils import get_game_info_by_id

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
        """
        self.game = game
        self.game_id = str(game.get("gameId", ""))
        self.base_config = base_config
        self.pre_start_hours = pre_start_hours
        self.check_interval_seconds = check_interval_seconds
        self.data_dir = data_dir
        self.game_date = game_date
        self.log_level = log_level

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
        self.replay_file: Path | None = None
        self.log_file: Path | None = None
        self.process: subprocess.Popen | None = None
        self._log_file_handle = None  # Store file handle for proper cleanup
        self.started = False
        self.completed = False
        self._logger: logging.Logger | None = None

    def generate_config_file(self) -> Path:
        """Generate config file for this game.

        Returns:
            Path to generated config file
        """
        # Load base config
        with open(self.base_config, "r") as f:
            config = yaml.safe_load(f)

        # Update game_id
        config["scenario"]["config"]["game_id"] = self.game_id

        # Determine file paths
        if self.data_dir:
            # Data dir structure: {data_dir}/{date}/{game_id}.yaml, {game_id}.jsonl, {game_id}.log
            if not self.game_date:
                # If data_dir is provided but no game_date, use today's date
                from datetime import datetime
                self.game_date = datetime.now().strftime("%Y-%m-%d")
            date_dir = self.data_dir / self.game_date
            config_file = date_dir / f"{self.game_id}.yaml"
            replay_file = date_dir / f"{self.game_id}.jsonl"
            log_file = date_dir / f"{self.game_id}.log"
        else:
            # Flat structure: use default directories
            project_root = Path(__file__).parent.parent
            configs_dir = project_root / "configs"
            outputs_dir = project_root / "outputs"
            config_file = configs_dir / f"nba-pregame-betting_{self.game_id}.yaml"
            replay_file = outputs_dir / f"nba_betting_events_{self.game_id}.jsonl"
            log_file = outputs_dir / f"{self.game_id}.log"

        # Update persistence file in config
        if "hub" not in config["scenario"]["config"]:
            config["scenario"]["config"]["hub"] = {}
        config["scenario"]["config"]["hub"]["persistence_file"] = str(replay_file)
        config["scenario"]["config"]["hub"]["enable_persistence"] = True

        # Create directory structure
        config_file.parent.mkdir(parents=True, exist_ok=True)
        replay_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.parent.mkdir(parents=True, exist_ok=True)

        # Save config file
        with open(config_file, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        self.config_file = config_file
        self.replay_file = replay_file
        self.log_file = log_file
        self.trial_id = f"nba-game-{self.game_id}"

        # Set up file logger for this game
        self._setup_file_logger()

        logger.info(
            "Generated config for game %s: %s (replay: %s, log: %s)",
            self.game_id,
            config_file,
            replay_file,
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

        # Build agentx run command
        # Note: --log-level is a top-level argument, must come before "run"
        cmd = [
            sys.executable,
            "-m",
            "agentx.cli",
            "--log-level",
            self.log_level,
            "run",
            "--params",
            str(self.config_file),
            "--trial-id",
            self.trial_id,
        ]

        self.log(logging.INFO, "Starting trial for game %s: %s", self.game_id, " ".join(cmd))

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
                env["AGENTX_LOG_FILE"] = str(self.log_file)
                
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
            self.log(logging.ERROR, "Failed to start trial for game %s: %s", self.game_id, e)
            return False

    async def monitor_trial(self) -> None:
        """Monitor trial process and game status until game concludes."""
        if not self.started or not self.process:
            logger.warning("Cannot monitor trial for game %s (not started)", self.game_id)
            return

        self.log(logging.INFO, "Monitoring trial for game %s until game concludes", self.game_id)

        while True:
            # Check if process is still running
            if self.process.poll() is not None:
                # Process ended
                return_code = self.process.returncode
                stdout, stderr = self.process.communicate()

                if return_code == 0:
                    self.log(
                        logging.INFO,
                        "✓ Trial process completed for game %s (trial_id: %s)",
                        self.game_id,
                        self.trial_id,
                    )
                else:
                    self.log(
                        logging.ERROR,
                        "✗ Trial process failed for game %s (trial_id: %s, return_code: %d)",
                        self.game_id,
                        self.trial_id,
                        return_code,
                    )
                    if stderr:
                        self.log(logging.ERROR, "Stderr: %s", stderr[:500])  # First 500 chars

                break

            # Check game status
            try:
                # Fetch current game status
                games = get_games_for_date(datetime.now(), print_games=False)
                current_game = next(
                    (g for g in games if str(g.get("gameId")) == self.game_id), None
                )

                if current_game:
                    game_status = current_game.get("gameStatus", 0)
                    # Status 3 = Finished
                    if game_status == 3:
                        self.log(
                            logging.INFO,
                            "Game %s has finished, stopping trial (trial_id: %s)",
                            self.game_id,
                            self.trial_id,
                        )
                        self.stop_trial()
                        self.completed = True
                        break
            except Exception as e:
                self.log(logging.WARNING, "Error checking game status for %s: %s", self.game_id, e)

            # Wait before next check
            await asyncio.sleep(self.check_interval_seconds)

    def stop_trial(self) -> None:
        """Stop the trial process."""
        if not self.process:
            return

        self.log(logging.INFO, "Stopping trial for game %s (PID: %d)", self.game_id, self.process.pid)

        try:
            # Send SIGTERM
            self.process.terminate()

            # Wait up to 10 seconds for graceful shutdown
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Force kill if not responding
                self.log(logging.WARNING, "Trial process did not terminate, forcing kill")
                self.process.kill()
                self.process.wait()

            # Close log file handle if it exists
            if self._log_file_handle:
                try:
                    self._log_file_handle.flush()  # Ensure all data is written
                    self._log_file_handle.close()
                except Exception as e:
                    self.log(logging.WARNING, "Error closing log file for game %s: %s", self.game_id, e)
                finally:
                    self._log_file_handle = None

            self.log(logging.INFO, "✓ Trial stopped for game %s", self.game_id)
        except Exception as e:
            self.log(logging.ERROR, "Error stopping trial for game %s: %s", self.game_id, e)

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
        if self.replay_file:
            status_parts.append(f"Replay: {self.replay_file}")
            # Check if replay file exists and get size
            if self.replay_file.exists():
                size_kb = self.replay_file.stat().st_size / 1024
                status_parts.append(f"Replay size: {size_kb:.1f} KB")

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


async def collect_game_for_id(
    game_id: str,
    base_config: Path,
    pre_start_hours: float = 2.0,
    check_interval_seconds: float = 60.0,
    data_dir: Path | None = None,
    log_level: str = "INFO",
) -> list[GameTrialManager]:
    """Collect data for a specific game by ID.

    Searches across recent dates to find the game, then extracts full game data.
    If the game is not found, returns an empty list.

    Args:
        game_id: Game ID to collect data for
        base_config: Path to base config template
        pre_start_hours: Hours before game to start trial
        check_interval_seconds: Interval to check game status
        data_dir: If provided, use {data_dir}/{date}/{game_id}.yaml and {data_dir}/{date}/{game_id}.jsonl
                  If None, use defaults: configs/ and outputs/

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
    game_date_str = game_info.get('game_date')
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
        logger.error("Game ID %s not found in games for date %s", game_id, game_date_str)
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
    )
    manager.generate_config_file()
    manager.log_status()

    return [manager]


async def collect_games_for_date(
    game_date: datetime | str,
    base_config: Path,
    pre_start_hours: float = 2.0,
    check_interval_seconds: float = 60.0,
    data_dir: Path | None = None,
    log_level: str = "INFO",
) -> list[GameTrialManager]:
    """Collect data for all games on a given date.

    Args:
        game_date: Date to collect games for
        base_config: Path to base config template
        pre_start_hours: Hours before game to start trial
        check_interval_seconds: Interval to check game status
        data_dir: If provided, use {data_dir}/{date}/{game_id}.yaml and {data_dir}/{date}/{game_id}.jsonl
                  If None, use defaults: configs/ and outputs/

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
            game_date=date_str if data_dir else None,  # Pass date_str when data_dir is set
            log_level=log_level,
        )
        managers.append(manager)

        # Generate config file immediately
        manager.generate_config_file()
        manager.log_status()

    return managers


async def run_collection(
    managers: list[GameTrialManager],
) -> None:
    """Run collection for all game managers.

    Args:
        managers: List of GameTrialManager instances
    """
    tasks = []

    for manager in managers:
        # Create task for each game
        async def run_game(manager: GameTrialManager) -> None:
            try:
                # Wait until start time
                should_start = await manager.wait_until_start_time()
                if not should_start:
                    logger.warning("Skipping start for game %s", manager.game_id)
                    return

                # Start trial
                started = await manager.start_trial()
                if not started:
                    logger.error("Failed to start trial for game %s", manager.game_id)
                    return

                # Monitor until game concludes
                await manager.monitor_trial()

                # Log final status
                manager.log_status()

            except Exception as e:
                logger.error("Error in collection for game %s: %s", manager.game_id, e)
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


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="NBA Game Collector - Orchestrates data collection for NBA games"
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Date to collect games for (YYYY-MM-DD). Default: today",
    )
    parser.add_argument(
        "--game-id",
        type=str,
        default=None,
        help="Specific game ID to collect data for. If provided, only this game will be processed.",
    )
    parser.add_argument(
        "--base-config",
        type=Path,
        default=Path(__file__).parent.parent / "configs" / "nba-pregame-betting.yaml",
        help="Path to base config template (default: configs/nba-pregame-betting.yaml)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Data directory for date-organized structure: {data-dir}/{date}/{game_id}.yaml and {data-dir}/{date}/{game_id}.jsonl",
    )
    parser.add_argument(
        "--pre-start-hours",
        type=float,
        default=2.0,
        help="Hours before game to start trial (default: 2.0)",
    )
    parser.add_argument(
        "--check-interval",
        type=float,
        default=60.0,
        help="Interval in seconds to check game status (default: 60.0)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Validate base config
    if not args.base_config.exists():
        logger.error("Base config file not found: %s", args.base_config)
        return 1

    # Run collection
    try:
        # If game_id is provided, use collect_game_for_id (trumps date logic)
        if args.game_id:
            managers = asyncio.run(
                collect_game_for_id(
                    game_id=args.game_id,
                    base_config=args.base_config,
                    pre_start_hours=args.pre_start_hours,
                    check_interval_seconds=args.check_interval,
                    data_dir=args.data_dir,
                    log_level=args.log_level,
                )
            )
        else:
            # Determine date for date-based collection
            if args.date:
                game_date = args.date
            else:
                game_date = datetime.now()

            managers = asyncio.run(
                collect_games_for_date(
                    game_date=game_date,
                    base_config=args.base_config,
                    pre_start_hours=args.pre_start_hours,
                    check_interval_seconds=args.check_interval,
                    data_dir=args.data_dir,
                    log_level=args.log_level,
                )
            )

        if not managers:
            logger.info("No games to collect")
            return 0

        asyncio.run(run_collection(managers))
        return 0

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

