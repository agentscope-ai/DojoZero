#!/usr/bin/env python3
"""NFL Trial Runner

Orchestrates betting trials for NFL games:
- Checks ESPN API for games on a given date or week
- Sets up separate trial/config for each game
- Starts trial before game kickoff time
- Runs agents that analyze data and place bets
- Runs until game concludes
- Persists all events to event files (for backtesting)
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
sys.path.insert(0, str(Path(__file__).parent.parent))

from dojozero.data.nfl._api import NFLExternalAPI
from dojozero.utils.oss import OSSClient

logger = logging.getLogger(__name__)


async def get_nfl_games_for_date(
    game_date: datetime | str,
    print_games: bool = False,
) -> list[dict[str, Any]]:
    """Get NFL games for a specific date using ESPN API.

    Args:
        game_date: Date as datetime object or string in 'YYYY-MM-DD' format
        print_games: Whether to print game information (default: False)

    Returns:
        list[dict]: List of game dictionaries with standardized format:
        {
            'eventId': str,
            'gameStatus': int,  # 1=scheduled, 2=in_progress, 3=finished
            'gameStatusText': str,
            'gameTimeUTC': str,
            'gameTimeLTZ': datetime,
            'shortName': str,  # e.g., "KC @ BUF"
            'homeTeam': {
                'teamId': str,
                'teamName': str,
                'abbreviation': str,
                'score': int,
            },
            'awayTeam': {...},
            'venue': str,
            'broadcast': str,
            'odds': {...} or None,
        }
    """
    # Parse the requested date
    if isinstance(game_date, datetime):
        requested_date = game_date.date()
    elif isinstance(game_date, str):
        try:
            parsed_date = parser.parse(game_date).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            requested_date = parsed_date.date()
        except Exception:
            if print_games:
                print(f"Error: Could not parse date: {game_date}")
            return []
    else:
        if print_games:
            print(f"Error: Invalid date type: {type(game_date)}")
        return []

    date_str = requested_date.strftime("%Y%m%d")

    api = NFLExternalAPI()
    try:
        # Fetch scoreboard for the date
        data = await api.fetch("scoreboard", {"dates": date_str})
        scoreboard = data.get("scoreboard", {})
        events = scoreboard.get("events", [])

        games: list[dict[str, Any]] = []
        for event in events:
            event_id = event.get("id", "")
            short_name = event.get("shortName", "")

            # Get competition details
            competitions = event.get("competitions", [])
            if not competitions:
                continue

            comp = competitions[0]
            status = comp.get("status", {})
            status_type = status.get("type", {})
            status_id = status_type.get("id", "1")
            status_desc = status_type.get("description", "Scheduled")

            # Parse game time
            game_time_utc_str = comp.get("date", "")
            game_time_utc = None
            game_time_ltz = None
            if game_time_utc_str:
                try:
                    game_time_utc = parser.parse(game_time_utc_str)
                    if game_time_utc.tzinfo is None:
                        game_time_utc = game_time_utc.replace(tzinfo=timezone.utc)
                    game_time_ltz = game_time_utc.astimezone(tz=None)
                except Exception:
                    pass

            # Get competitors
            competitors = comp.get("competitors", [])
            home_team = {}
            away_team = {}
            for c in competitors:
                team = c.get("team", {})
                team_data = {
                    "teamId": team.get("id", ""),
                    "teamName": team.get("displayName", ""),
                    "abbreviation": team.get("abbreviation", ""),
                    "score": int(c.get("score", "0") or "0"),
                }
                if c.get("homeAway") == "home":
                    home_team = team_data
                else:
                    away_team = team_data

            # Get venue
            venue = comp.get("venue", {}).get("fullName", "")

            # Get broadcast
            broadcasts = comp.get("broadcasts", [])
            broadcast = ""
            if broadcasts:
                names = broadcasts[0].get("names", [])
                broadcast = ", ".join(names) if names else ""

            # Get odds
            odds_list = comp.get("odds", [])
            odds = None
            if odds_list:
                o = odds_list[0]
                odds = {
                    "provider": o.get("provider", {}).get("name", ""),
                    "spread": o.get("spread", 0),
                    "overUnder": o.get("overUnder", 0),
                    "homeMoneyLine": o.get("homeTeamOdds", {}).get("moneyLine", 0),
                    "awayMoneyLine": o.get("awayTeamOdds", {}).get("moneyLine", 0),
                }

            game = {
                "eventId": event_id,
                "gameStatus": int(status_id),
                "gameStatusText": status_desc,
                "gameTimeUTC": game_time_utc_str,
                "gameTimeLTZ": game_time_ltz,
                "shortName": short_name,
                "homeTeam": home_team,
                "awayTeam": away_team,
                "venue": venue,
                "broadcast": broadcast,
                "odds": odds,
            }
            games.append(game)

        if print_games:
            print(f"Date: {requested_date.strftime('%Y-%m-%d')}")
            print(f"Found {len(games)} game(s)\n")
            for game in games:
                time_str = (
                    game["gameTimeLTZ"].strftime("%Y-%m-%d %H:%M:%S %Z")
                    if game.get("gameTimeLTZ")
                    else "N/A"
                )
                score = f"{game['awayTeam'].get('score', 0)} - {game['homeTeam'].get('score', 0)}"
                odds_str = ""
                if game.get("odds"):
                    odds_str = f" | Spread: {game['odds']['spread']:+.1f}, O/U: {game['odds']['overUnder']}"
                print(
                    f"{game['eventId']}: {game['shortName']} @ {time_str} [{game['gameStatusText']}] {score}{odds_str}"
                )

        return games

    except Exception as e:
        logger.error("Error fetching NFL games for date %s: %s", game_date, e)
        if print_games:
            print(f"Error fetching games: {e}")
        return []
    finally:
        await api.close()


async def get_nfl_games_for_week(
    week: int,
    season_type: int = 2,
    print_games: bool = False,
) -> list[dict[str, Any]]:
    """Get NFL games for a specific week using ESPN API.

    Args:
        week: Week number (1-18 for regular season)
        season_type: 1=preseason, 2=regular, 3=postseason (default: 2)
        print_games: Whether to print game information (default: False)

    Returns:
        list[dict]: List of game dictionaries (same format as get_nfl_games_for_date)
    """
    api = NFLExternalAPI()
    try:
        data = await api.fetch("scoreboard", {"week": week, "seasontype": season_type})
        scoreboard = data.get("scoreboard", {})
        events = scoreboard.get("events", [])

        games: list[dict[str, Any]] = []
        for event in events:
            event_id = event.get("id", "")
            short_name = event.get("shortName", "")

            competitions = event.get("competitions", [])
            if not competitions:
                continue

            comp = competitions[0]
            status = comp.get("status", {})
            status_type = status.get("type", {})
            status_id = status_type.get("id", "1")
            status_desc = status_type.get("description", "Scheduled")

            game_time_utc_str = comp.get("date", "")
            game_time_utc = None
            game_time_ltz = None
            if game_time_utc_str:
                try:
                    game_time_utc = parser.parse(game_time_utc_str)
                    if game_time_utc.tzinfo is None:
                        game_time_utc = game_time_utc.replace(tzinfo=timezone.utc)
                    game_time_ltz = game_time_utc.astimezone(tz=None)
                except Exception:
                    pass

            competitors = comp.get("competitors", [])
            home_team = {}
            away_team = {}
            for c in competitors:
                team = c.get("team", {})
                team_data = {
                    "teamId": team.get("id", ""),
                    "teamName": team.get("displayName", ""),
                    "abbreviation": team.get("abbreviation", ""),
                    "score": int(c.get("score", "0") or "0"),
                }
                if c.get("homeAway") == "home":
                    home_team = team_data
                else:
                    away_team = team_data

            venue = comp.get("venue", {}).get("fullName", "")

            broadcasts = comp.get("broadcasts", [])
            broadcast = ""
            if broadcasts:
                names = broadcasts[0].get("names", [])
                broadcast = ", ".join(names) if names else ""

            odds_list = comp.get("odds", [])
            odds = None
            if odds_list:
                o = odds_list[0]
                odds = {
                    "provider": o.get("provider", {}).get("name", ""),
                    "spread": o.get("spread", 0),
                    "overUnder": o.get("overUnder", 0),
                    "homeMoneyLine": o.get("homeTeamOdds", {}).get("moneyLine", 0),
                    "awayMoneyLine": o.get("awayTeamOdds", {}).get("moneyLine", 0),
                }

            game = {
                "eventId": event_id,
                "gameStatus": int(status_id),
                "gameStatusText": status_desc,
                "gameTimeUTC": game_time_utc_str,
                "gameTimeLTZ": game_time_ltz,
                "shortName": short_name,
                "homeTeam": home_team,
                "awayTeam": away_team,
                "venue": venue,
                "broadcast": broadcast,
                "odds": odds,
            }
            games.append(game)

        if print_games:
            season_name = {1: "Preseason", 2: "Regular", 3: "Postseason"}.get(
                season_type, "Unknown"
            )
            print(f"Week {week} ({season_name} Season)")
            print(f"Found {len(games)} game(s)\n")
            for game in games:
                time_str = (
                    game["gameTimeLTZ"].strftime("%Y-%m-%d %H:%M:%S %Z")
                    if game.get("gameTimeLTZ")
                    else "N/A"
                )
                score = f"{game['awayTeam'].get('score', 0)} - {game['homeTeam'].get('score', 0)}"
                odds_str = ""
                if game.get("odds"):
                    odds_str = f" | Spread: {game['odds']['spread']:+.1f}, O/U: {game['odds']['overUnder']}"
                print(
                    f"{game['eventId']}: {game['shortName']} @ {time_str} [{game['gameStatusText']}] {score}{odds_str}"
                )

        return games

    except Exception as e:
        logger.error("Error fetching NFL games for week %d: %s", week, e)
        if print_games:
            print(f"Error fetching games: {e}")
        return []
    finally:
        await api.close()


class NFLGameTrialManager:
    """Manages trial lifecycle for a single NFL game."""

    def __init__(
        self,
        game: dict[str, Any],
        base_config: Path,
        pre_start_hours: float = 1.0,
        check_interval_seconds: float = 60.0,
        data_dir: Path | None = None,
        game_date: str | None = None,
        log_level: str = "INFO",
        oss_upload: bool = False,
        oss_bucket: str | None = None,
        oss_prefix: str | None = None,
        server: str | None = None,
    ):
        """Initialize NFL game trial manager.

        Args:
            game: Game dictionary from ESPN API
            base_config: Path to base config template
            pre_start_hours: Hours before game to start trial (default: 1.0)
            check_interval_seconds: Interval to check game status (default: 60.0)
            data_dir: If provided, use {data_dir}/{date}/{event_id}.yaml
            game_date: Date string (YYYY-MM-DD) for date-organized structure
            log_level: Logging level for subprocess (default: INFO)
            oss_upload: Whether to upload files to OSS after trial completion
            oss_bucket: Override OSS bucket name (default: from env)
            oss_prefix: Override OSS prefix (default: from env)
            server: Dashboard Server URL for SLS/OSS integration
        """
        self.game = game
        self.event_id = str(game.get("eventId", ""))
        self.base_config = base_config
        self.pre_start_hours = pre_start_hours
        self.check_interval_seconds = check_interval_seconds
        self.data_dir = data_dir
        self.game_date = game_date
        self.log_level = log_level
        self.oss_upload = oss_upload
        self.oss_bucket = oss_bucket
        self.oss_prefix = oss_prefix
        self.server = server

        # Parse game time
        self.game_time_utc: datetime | None = game.get("gameTimeLTZ")
        if self.game_time_utc and self.game_time_utc.tzinfo is None:
            self.game_time_utc = self.game_time_utc.replace(tzinfo=timezone.utc)

        # Trial state
        self.trial_id: str | None = None
        self.config_file: Path | None = None
        self.events_file: Path | None = None
        self.log_file: Path | None = None
        self.process: subprocess.Popen | None = None
        self._log_file_handle = None
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

        # Update event_id in config
        config["scenario"]["config"]["event_id"] = self.event_id

        # Determine file paths
        if self.data_dir:
            if not self.game_date:
                self.game_date = datetime.now().strftime("%Y-%m-%d")
            date_dir = self.data_dir / self.game_date
            config_file = date_dir / f"{self.event_id}.yaml"
            events_file = date_dir / f"{self.event_id}.jsonl"
            log_file = date_dir / f"{self.event_id}.log"
        else:
            project_root = Path(__file__).parent.parent
            configs_dir = project_root / "configs"
            outputs_dir = project_root / "outputs"
            config_file = configs_dir / f"nfl-game_{self.event_id}.yaml"
            events_file = outputs_dir / f"nfl_events_{self.event_id}.jsonl"
            log_file = outputs_dir / f"nfl_{self.event_id}.log"

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

        # Generate unique trial ID
        timestamp = datetime.now(timezone.utc).isoformat()
        hash_input = f"{self.event_id}-{self.game_date or 'unknown'}-{timestamp}"
        hash_suffix = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
        self.trial_id = f"nfl-game-{self.event_id}-{hash_suffix}"

        # Set up file logger
        self._setup_file_logger()

        logger.info(
            "Generated config for game %s: %s (events: %s, log: %s)",
            self.event_id,
            config_file,
            events_file,
            log_file,
        )

        return config_file

    def _setup_file_logger(self) -> None:
        """Set up file logger for this game."""
        if not self.log_file:
            return

        game_logger = logging.getLogger(f"nfl_game_{self.event_id}")
        game_logger.setLevel(logging.DEBUG)
        game_logger.handlers.clear()

        file_handler = logging.FileHandler(self.log_file, mode="a", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)
        game_logger.addHandler(file_handler)
        game_logger.propagate = False

        self._logger = game_logger

    def log(self, level: int, message: str, *args: Any) -> None:
        """Log a message to both console and file logger."""
        logger.log(level, message, *args)
        if self._logger:
            self._logger.log(level, message, *args)

    def calculate_start_time(self) -> datetime | None:
        """Calculate when to start the trial (before kickoff).

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
            logger.warning("Cannot determine start time for game %s", self.event_id)
            return False

        now = datetime.now(timezone.utc)

        if start_time <= now:
            logger.info(
                "Start time already passed for game %s (was %s, now %s)",
                self.event_id,
                start_time,
                now,
            )
            return True

        wait_seconds = (start_time - now).total_seconds()
        self.log(
            logging.INFO,
            "Scheduled trial for game %s to start in %.1f seconds (at %s)",
            self.event_id,
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
            logger.warning("Trial already started for game %s", self.event_id)
            return False

        if not self.config_file:
            self.generate_config_file()

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

        if self.server:
            cmd.extend(["--server", self.server])

        self.log(
            logging.INFO, "Starting trial for game %s: %s", self.event_id, " ".join(cmd)
        )

        try:
            if self.log_file:
                self._log_file_handle = open(
                    self.log_file,
                    "a",
                    encoding="utf-8",
                    buffering=1,
                )

                env = os.environ.copy()
                env["DOJOZERO_LOG_FILE"] = str(self.log_file)

                self.process = subprocess.Popen(
                    cmd,
                    stdout=self._log_file_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env,
                    bufsize=1,
                )
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
                "Trial started for game %s (PID: %d, trial_id: %s)",
                self.event_id,
                self.process.pid,
                self.trial_id,
            )

            return True
        except Exception as e:
            self.log(
                logging.ERROR, "Failed to start trial for game %s: %s", self.event_id, e
            )
            return False

    async def monitor_trial(self) -> None:
        """Monitor trial process and game status until game concludes."""
        if not self.started or not self.process:
            logger.warning(
                "Cannot monitor trial for game %s (not started)", self.event_id
            )
            return

        self.log(
            logging.INFO,
            "Monitoring trial for game %s until game concludes",
            self.event_id,
        )

        # Wait for initial data collection before checking game status.
        # This ensures at least one polling cycle completes (scoreboard=60s, summary=30s, plays=10s).
        # Use 90 seconds to ensure all endpoints have been polled at least once.
        initial_wait_seconds = 90
        self.log(
            logging.INFO,
            "Waiting %d seconds for initial data collection before monitoring game status",
            initial_wait_seconds,
        )
        await asyncio.sleep(initial_wait_seconds)

        while True:
            # Check if process is still running
            if self.process.poll() is not None:
                return_code = self.process.returncode
                stdout, stderr = self.process.communicate()

                if return_code == 0:
                    self.log(
                        logging.INFO,
                        "Trial process completed for game %s (trial_id: %s)",
                        self.event_id,
                        self.trial_id,
                    )
                else:
                    self.log(
                        logging.ERROR,
                        "Trial process failed for game %s (trial_id: %s, return_code: %d)",
                        self.event_id,
                        self.trial_id,
                        return_code,
                    )
                    if stderr:
                        self.log(logging.ERROR, "Stderr: %s", stderr[:500])

                break

            # Check game status via ESPN API
            # Use the game's actual date, not today's date
            try:
                check_date = self.game_date or datetime.now().strftime("%Y-%m-%d")
                games = await get_nfl_games_for_date(check_date, print_games=False)
                current_game = next(
                    (g for g in games if str(g.get("eventId")) == self.event_id), None
                )

                if current_game:
                    game_status = current_game.get("gameStatus", 0)
                    # Status 3 = Finished
                    if game_status == 3:
                        self.log(
                            logging.INFO,
                            "Game %s has finished, stopping trial (trial_id: %s)",
                            self.event_id,
                            self.trial_id,
                        )
                        self.stop_trial()
                        self.completed = True
                        break
            except Exception as e:
                self.log(
                    logging.WARNING,
                    "Error checking game status for %s: %s",
                    self.event_id,
                    e,
                )

            await asyncio.sleep(self.check_interval_seconds)

    def stop_trial(self) -> None:
        """Stop the trial process."""
        if not self.process:
            return

        self.log(
            logging.INFO,
            "Stopping trial for game %s (PID: %d)",
            self.event_id,
            self.process.pid,
        )

        try:
            self.process.terminate()

            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.log(
                    logging.WARNING, "Trial process did not terminate, forcing kill"
                )
                self.process.kill()
                self.process.wait()

            if self._log_file_handle:
                try:
                    self._log_file_handle.flush()
                    self._log_file_handle.close()
                except Exception as e:
                    self.log(
                        logging.WARNING,
                        "Error closing log file for game %s: %s",
                        self.event_id,
                        e,
                    )
                finally:
                    self._log_file_handle = None

            self.log(logging.INFO, "Trial stopped for game %s", self.event_id)
        except Exception as e:
            self.log(
                logging.ERROR, "Error stopping trial for game %s: %s", self.event_id, e
            )

    def log_status(self) -> None:
        """Log crucial status information."""
        status_parts = [
            f"Game: {self.event_id}",
            f"Matchup: {self.game.get('shortName', 'Unknown')}",
            f"Trial ID: {self.trial_id}",
            f"Started: {self.started}",
            f"Completed: {self.completed}",
        ]

        if self.config_file:
            status_parts.append(f"Config: {self.config_file}")
        if self.events_file:
            status_parts.append(f"Events: {self.events_file}")
            if self.events_file.exists():
                size_kb = self.events_file.stat().st_size / 1024
                status_parts.append(f"Events size: {size_kb:.1f} KB")

        if self.log_file:
            status_parts.append(f"Log: {self.log_file}")
            if self.log_file.exists():
                size_kb = self.log_file.stat().st_size / 1024
                status_parts.append(f"Log size: {size_kb:.1f} KB")

        if self.game_time_utc:
            status_parts.append(f"Kickoff: {self.game_time_utc}")

        status_msg = "STATUS: " + " | ".join(status_parts)
        logger.info(status_msg)
        if self._logger:
            self._logger.info(status_msg)

    def upload_to_oss(self) -> list[str]:
        """Upload trial files to OSS.

        Returns:
            List of uploaded OSS keys
        """
        if not self.oss_upload:
            return []

        uploaded_keys: list[str] = []

        try:
            client = OSSClient.from_env(
                bucket_name=self.oss_bucket,
                prefix=self.oss_prefix,
            )

            # Determine OSS key prefix (mirror local structure)
            if self.game_date:
                oss_key_prefix = f"nfl/{self.game_date}"
            else:
                oss_key_prefix = "nfl"

            # Upload config file
            if self.config_file and self.config_file.exists():
                oss_key = f"{oss_key_prefix}/{self.event_id}.yaml"
                key = client.upload_file(self.config_file, oss_key)
                uploaded_keys.append(key)
                self.log(logging.INFO, "Uploaded config to OSS: %s", key)

            # Upload events file (JSONL)
            if self.events_file and self.events_file.exists():
                oss_key = f"{oss_key_prefix}/{self.event_id}.jsonl"
                key = client.upload_file(self.events_file, oss_key)
                uploaded_keys.append(key)
                self.log(logging.INFO, "Uploaded events to OSS: %s", key)

            # Upload log file
            if self.log_file and self.log_file.exists():
                oss_key = f"{oss_key_prefix}/{self.event_id}.log"
                key = client.upload_file(self.log_file, oss_key)
                uploaded_keys.append(key)
                self.log(logging.INFO, "Uploaded log to OSS: %s", key)

            self.log(
                logging.INFO,
                "Successfully uploaded %d files to OSS for game %s",
                len(uploaded_keys),
                self.event_id,
            )

        except Exception as e:
            self.log(
                logging.ERROR,
                "Failed to upload files to OSS for game %s: %s",
                self.event_id,
                e,
            )

        return uploaded_keys


async def run_trials_for_date(
    game_date: datetime | str,
    base_config: Path,
    pre_start_hours: float = 1.0,
    check_interval_seconds: float = 60.0,
    data_dir: Path | None = None,
    log_level: str = "INFO",
    oss_upload: bool = False,
    oss_bucket: str | None = None,
    oss_prefix: str | None = None,
    server: str | None = None,
) -> list[NFLGameTrialManager]:
    """Run trials for all NFL games on a given date.

    Args:
        game_date: Date to run trials for
        base_config: Path to base config template
        pre_start_hours: Hours before game to start trial
        check_interval_seconds: Interval to check game status
        data_dir: If provided, organize files by date
        log_level: Logging level
        oss_upload: Whether to upload files to OSS after trial completion
        oss_bucket: Override OSS bucket name
        oss_prefix: Override OSS prefix
        server: Dashboard Server URL for SLS/OSS integration

    Returns:
        List of NFLGameTrialManager instances
    """
    if isinstance(game_date, datetime):
        date_str = game_date.strftime("%Y-%m-%d")
    else:
        date_str = game_date

    logger.info("Fetching NFL games for date: %s", game_date)
    games = await get_nfl_games_for_date(game_date, print_games=True)

    if not games:
        logger.info("No NFL games found for date %s", game_date)
        return []

    managers: list[NFLGameTrialManager] = []
    for game in games:
        event_id = str(game.get("eventId", ""))
        if not event_id:
            logger.warning("Skipping game without eventId: %s", game)
            continue

        manager = NFLGameTrialManager(
            game=game,
            base_config=base_config,
            pre_start_hours=pre_start_hours,
            check_interval_seconds=check_interval_seconds,
            data_dir=data_dir,
            game_date=date_str if data_dir else None,
            log_level=log_level,
            oss_upload=oss_upload,
            oss_bucket=oss_bucket,
            oss_prefix=oss_prefix,
            server=server,
        )
        managers.append(manager)

        manager.generate_config_file()
        manager.log_status()

    return managers


async def run_trials_for_week(
    week: int,
    base_config: Path,
    season_type: int = 2,
    pre_start_hours: float = 1.0,
    check_interval_seconds: float = 60.0,
    data_dir: Path | None = None,
    log_level: str = "INFO",
    oss_upload: bool = False,
    oss_bucket: str | None = None,
    oss_prefix: str | None = None,
    server: str | None = None,
) -> list[NFLGameTrialManager]:
    """Run trials for all NFL games in a given week.

    Args:
        week: Week number
        base_config: Path to base config template
        season_type: 1=preseason, 2=regular, 3=postseason
        pre_start_hours: Hours before game to start trial
        check_interval_seconds: Interval to check game status
        data_dir: If provided, organize files by date
        log_level: Logging level
        oss_upload: Whether to upload files to OSS after trial completion
        oss_bucket: Override OSS bucket name
        oss_prefix: Override OSS prefix
        server: Dashboard Server URL for SLS/OSS integration

    Returns:
        List of NFLGameTrialManager instances
    """
    logger.info("Fetching NFL games for week %d (season type %d)", week, season_type)
    games = await get_nfl_games_for_week(week, season_type, print_games=True)

    if not games:
        logger.info("No NFL games found for week %d", week)
        return []

    managers: list[NFLGameTrialManager] = []
    for game in games:
        event_id = str(game.get("eventId", ""))
        if not event_id:
            logger.warning("Skipping game without eventId: %s", game)
            continue

        # Determine date from game time
        game_time = game.get("gameTimeLTZ")
        game_date = game_time.strftime("%Y-%m-%d") if game_time else None

        manager = NFLGameTrialManager(
            game=game,
            base_config=base_config,
            pre_start_hours=pre_start_hours,
            check_interval_seconds=check_interval_seconds,
            data_dir=data_dir,
            game_date=game_date if data_dir else None,
            log_level=log_level,
            oss_upload=oss_upload,
            oss_bucket=oss_bucket,
            oss_prefix=oss_prefix,
            server=server,
        )
        managers.append(manager)

        manager.generate_config_file()
        manager.log_status()

    return managers


async def run_trial_for_event(
    event_id: str,
    base_config: Path,
    pre_start_hours: float = 1.0,
    check_interval_seconds: float = 60.0,
    data_dir: Path | None = None,
    log_level: str = "INFO",
    oss_upload: bool = False,
    oss_bucket: str | None = None,
    oss_prefix: str | None = None,
    server: str | None = None,
) -> list[NFLGameTrialManager]:
    """Run trial for a specific NFL game by event ID.

    Args:
        event_id: ESPN event ID
        base_config: Path to base config template
        pre_start_hours: Hours before game to start trial
        check_interval_seconds: Interval to check game status
        data_dir: If provided, organize files by date
        log_level: Logging level
        oss_upload: Whether to upload files to OSS after trial completion
        oss_bucket: Override OSS bucket name
        oss_prefix: Override OSS prefix
        server: Dashboard Server URL for SLS/OSS integration

    Returns:
        List with single NFLGameTrialManager, or empty list if not found
    """
    logger.info("Searching for NFL game with event ID: %s", event_id)

    # Search in current week's games first
    api = NFLExternalAPI()
    try:
        data = await api.fetch("scoreboard")
        scoreboard = data.get("scoreboard", {})
        events = scoreboard.get("events", [])

        target_event = None
        for event in events:
            if event.get("id") == event_id:
                target_event = event
                break

        if not target_event:
            logger.error("Event ID %s not found in current scoreboard", event_id)
            return []

        # Parse the event into our format
        comp = target_event.get("competitions", [{}])[0]
        status = comp.get("status", {}).get("type", {})

        game_time_str = comp.get("date", "")
        game_time_ltz = None
        if game_time_str:
            try:
                game_time = parser.parse(game_time_str)
                if game_time.tzinfo is None:
                    game_time = game_time.replace(tzinfo=timezone.utc)
                game_time_ltz = game_time.astimezone(tz=None)
            except Exception:
                pass

        competitors = comp.get("competitors", [])
        home_team = {}
        away_team = {}
        for c in competitors:
            team = c.get("team", {})
            team_data = {
                "teamId": team.get("id", ""),
                "teamName": team.get("displayName", ""),
                "abbreviation": team.get("abbreviation", ""),
                "score": int(c.get("score", "0") or "0"),
            }
            if c.get("homeAway") == "home":
                home_team = team_data
            else:
                away_team = team_data

        game = {
            "eventId": event_id,
            "gameStatus": int(status.get("id", "1")),
            "gameStatusText": status.get("description", "Scheduled"),
            "gameTimeUTC": game_time_str,
            "gameTimeLTZ": game_time_ltz,
            "shortName": target_event.get("shortName", ""),
            "homeTeam": home_team,
            "awayTeam": away_team,
        }

        game_date = game_time_ltz.strftime("%Y-%m-%d") if game_time_ltz else None

        manager = NFLGameTrialManager(
            game=game,
            base_config=base_config,
            pre_start_hours=pre_start_hours,
            check_interval_seconds=check_interval_seconds,
            data_dir=data_dir,
            game_date=game_date if data_dir else None,
            log_level=log_level,
            oss_upload=oss_upload,
            oss_bucket=oss_bucket,
            oss_prefix=oss_prefix,
            server=server,
        )
        manager.generate_config_file()
        manager.log_status()

        return [manager]

    except Exception as e:
        logger.error("Error fetching game %s: %s", event_id, e)
        return []
    finally:
        await api.close()


async def run_trials(
    managers: list[NFLGameTrialManager],
    max_concurrent_starts: int = 10,
) -> None:
    """Run trials for all game managers.

    Args:
        managers: List of NFLGameTrialManager instances
        max_concurrent_starts: Maximum number of trials to start concurrently
    """
    tasks = []
    start_semaphore = asyncio.Semaphore(max_concurrent_starts)

    for manager in managers:

        async def run_game(manager: NFLGameTrialManager) -> None:
            try:
                should_start = await manager.wait_until_start_time()
                if not should_start:
                    logger.warning("Skipping start for game %s", manager.event_id)
                    return

                async with start_semaphore:
                    await asyncio.sleep(1.0)
                    started = await manager.start_trial()
                    if not started:
                        logger.error(
                            "Failed to start trial for game %s", manager.event_id
                        )
                        return

                await manager.monitor_trial()

                # Upload to OSS if enabled
                if manager.oss_upload:
                    manager.upload_to_oss()

                manager.log_status()

            except Exception as e:
                logger.error("Error in trial for game %s: %s", manager.event_id, e)
                manager.log_status()

        tasks.append(asyncio.create_task(run_game(manager)))

    await asyncio.gather(*tasks)

    logger.info("=" * 80)
    logger.info("FINAL STATUS FOR ALL GAMES:")
    for manager in managers:
        manager.log_status()
    logger.info("=" * 80)


async def list_games_in_range(
    start_date: str | None = None,
    end_date: str | None = None,
) -> None:
    """List NFL games in a date range.

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
    print(f"NFL Games from {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
    print(f"{'=' * 80}\n")

    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        games = await get_nfl_games_for_date(date_str, print_games=False)

        if games:
            print(f"Date: {date_str} ({len(games)} game(s))")
            print("-" * 40)
            for game in games:
                event_id = game.get("eventId", "")
                away_abbrev = game.get("awayTeam", {}).get("abbreviation", "???")
                home_abbrev = game.get("homeTeam", {}).get("abbreviation", "???")
                away_team = game.get("awayTeam", {}).get("teamName", "Unknown")
                home_team = game.get("homeTeam", {}).get("teamName", "Unknown")
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

                # Format odds if available
                odds_str = ""
                if game.get("odds"):
                    odds = game["odds"]
                    spread = odds.get("spread", 0)
                    over_under = odds.get("overUnder", 0)
                    if spread or over_under:
                        odds_str = f" | Spread: {spread:+.1f}, O/U: {over_under}"

                print(
                    f"  {event_id}: {away_abbrev} @ {home_abbrev} "
                    f"({away_team} vs {home_team}) | {time_str} | {status_text}{score_str}{odds_str}"
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
    arg_parser = argparse.ArgumentParser(
        description="NFL Trial Runner - Orchestrates betting trials for NFL games"
    )
    subparsers = arg_parser.add_subparsers(dest="command", help="Available commands")

    # List games subcommand
    list_parser = subparsers.add_parser("list", help="List NFL games in a date range")
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
        "--week",
        type=int,
        default=None,
        help="NFL week number to list games for (overrides date range)",
    )
    list_parser.add_argument(
        "--season-type",
        type=int,
        default=2,
        choices=[1, 2, 3],
        help="Season type: 1=preseason, 2=regular, 3=postseason (default: 2)",
    )
    list_parser.add_argument(
        "--log-level",
        type=str,
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: WARNING)",
    )

    # Run trials subcommand
    run_parser = subparsers.add_parser("run", help="Run betting trials for NFL games")
    run_parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Date to run trials for (YYYY-MM-DD). Default: today",
    )
    run_parser.add_argument(
        "--week",
        type=int,
        default=None,
        help="NFL week number to run trials for (1-18 for regular season)",
    )
    run_parser.add_argument(
        "--season-type",
        type=int,
        default=2,
        choices=[1, 2, 3],
        help="Season type: 1=preseason, 2=regular, 3=postseason (default: 2)",
    )
    run_parser.add_argument(
        "--game-id",
        "--event-id",
        type=str,
        default=None,
        dest="game_id",
        help="Specific ESPN event/game ID to run trial for",
    )
    run_parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).parent.parent / "trial_params" / "nfl-moneyline.yaml",
        help="Path to trial config template (default: trial_params/nfl-moneyline.yaml)",
    )
    run_parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Data directory for output",
    )
    run_parser.add_argument(
        "--pre-start-hours",
        type=float,
        default=1.0,
        help="Hours before kickoff to start trial (default: 1.0)",
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
        "--oss-upload",
        action="store_true",
        help="Upload files to OSS after trial completion",
    )
    run_parser.add_argument(
        "--oss-bucket",
        type=str,
        default=None,
        help="Override OSS bucket name (default: from DOJOZERO_OSS_BUCKET env var)",
    )
    run_parser.add_argument(
        "--oss-prefix",
        type=str,
        default=None,
        help="Override OSS prefix (default: from DOJOZERO_OSS_PREFIX env var)",
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

    args = arg_parser.parse_args()

    # Handle no command
    if args.command is None:
        arg_parser.print_help()
        return 0

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Handle list command
    if args.command == "list":
        if args.week:
            # List by week
            asyncio.run(
                get_nfl_games_for_week(args.week, args.season_type, print_games=True)
            )
        else:
            # List by date range
            asyncio.run(
                list_games_in_range(
                    start_date=args.start_date,
                    end_date=args.end_date,
                )
            )
        return 0

    # Handle run command
    if args.command == "run":
        # Validate config
        if not args.config.exists():
            logger.error("Config file not found: %s", args.config)
            logger.info(
                "Please create an NFL config template at %s or specify --config",
                args.config,
            )
            return 1

        try:
            if args.game_id:
                managers = asyncio.run(
                    run_trial_for_event(
                        event_id=args.game_id,
                        base_config=args.config,
                        pre_start_hours=args.pre_start_hours,
                        check_interval_seconds=args.check_interval,
                        data_dir=args.data_dir,
                        log_level=args.log_level,
                        oss_upload=args.oss_upload,
                        oss_bucket=args.oss_bucket,
                        oss_prefix=args.oss_prefix,
                        server=args.server,
                    )
                )
            elif args.week:
                managers = asyncio.run(
                    run_trials_for_week(
                        week=args.week,
                        base_config=args.config,
                        season_type=args.season_type,
                        pre_start_hours=args.pre_start_hours,
                        check_interval_seconds=args.check_interval,
                        data_dir=args.data_dir,
                        log_level=args.log_level,
                        oss_upload=args.oss_upload,
                        oss_bucket=args.oss_bucket,
                        oss_prefix=args.oss_prefix,
                        server=args.server,
                    )
                )
            else:
                game_date = args.date if args.date else datetime.now()
                managers = asyncio.run(
                    run_trials_for_date(
                        game_date=game_date,
                        base_config=args.config,
                        pre_start_hours=args.pre_start_hours,
                        check_interval_seconds=args.check_interval,
                        data_dir=args.data_dir,
                        log_level=args.log_level,
                        oss_upload=args.oss_upload,
                        oss_bucket=args.oss_bucket,
                        oss_prefix=args.oss_prefix,
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
