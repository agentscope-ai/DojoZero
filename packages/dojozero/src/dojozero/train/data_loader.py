"""Data loading and task management for training.

This module handles:
1. Loading game data from JSONL files
2. Creating training/validation/test task lists
3. Managing data splits
"""

import glob
import hashlib
import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class GameTask:
    """A training task representing a single game.

    Attributes:
        task_id: Unique identifier for the task
        game_file: Path to the JSONL file containing game events
        game_id: ESPN game ID extracted from the file
        metadata: Additional metadata about the game
    """

    task_id: str
    game_file: str
    game_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


class DojoDataLoader:
    """Load game data from dojo_data directory and create task lists.

    Supports:
    - Listing all available game files
    - Loading events from a specific game
    - Creating train/val/test splits
    - Creating task objects for AgentJet
    """

    def __init__(
        self,
        data_dir: str = "/mnt/data/dengjiaji.djj/dojo_data",
        train_ratio: float = 0.8,
        val_ratio: float = 0.1,
        test_ratio: float = 0.1,
        seed: int = 42,
    ):
        """Initialize the data loader.

        Args:
            data_dir: Directory containing game JSONL files
            train_ratio: Fraction of data for training
            val_ratio: Fraction of data for validation
            test_ratio: Fraction of data for testing
            seed: Random seed for reproducible splits
        """
        self.data_dir = Path(data_dir)
        self.train_ratio = train_ratio
        self.val_ratio = val_ratio
        self.test_ratio = test_ratio
        self.seed = seed

        # Validate ratios
        total = train_ratio + val_ratio + test_ratio
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"Ratios must sum to 1.0, got {total}")

    def list_game_files(self) -> list[str]:
        """List all JSONL files in the data directory.

        Returns:
            Sorted list of absolute paths to game files
        """
        pattern = str(self.data_dir / "*.jsonl")
        files = sorted(glob.glob(pattern))
        return files

    def _extract_game_id(self, file_path: str) -> str:
        """Extract game ID from filename.

        Supports formats:
        - 401809839.jsonl
        - nba_betting_events-401810837.jsonl

        Args:
            file_path: Path to the JSONL file

        Returns:
            Extracted game ID
        """
        filename = Path(file_path).stem
        # Try to extract numeric ID
        parts = filename.split("-")
        for part in reversed(parts):
            # Check if part is numeric (game ID)
            if part.isdigit():
                return part
        # Fallback: use whole filename as ID
        return filename

    def load_events(self, game_file: str) -> list[dict[str, Any]]:
        """Load all events from a game file.

        Args:
            game_file: Path to the JSONL file

        Returns:
            List of event dictionaries
        """
        events = []
        with open(game_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def _get_game_metadata(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Extract metadata from game events.

        Args:
            events: List of event dictionaries

        Returns:
            Metadata dictionary with game info
        """
        metadata: dict[str, Any] = {}

        for e in events:
            event_type = e.get("event_type", "")

            if event_type == "event.game_initialize":
                metadata["game_id"] = e.get("game_id", "")
                metadata["sport"] = e.get("sport", "")
                metadata["game_time"] = e.get("game_time", "")
                home = e.get("home_team", {})
                away = e.get("away_team", {})
                metadata["home_team"] = home.get("name", "")
                metadata["away_team"] = away.get("name", "")
                metadata["home_tricode"] = home.get("tricode", "")
                metadata["away_tricode"] = away.get("tricode", "")

            elif event_type == "event.game_result":
                metadata["winner"] = e.get("winner", "")
                metadata["home_score"] = e.get("home_score", 0)
                metadata["away_score"] = e.get("away_score", 0)

        return metadata

    def create_task(self, game_file: str) -> GameTask:
        """Create a task object from a game file.

        Args:
            game_file: Path to the JSONL file

        Returns:
            GameTask object
        """
        game_id = self._extract_game_id(game_file)
        task_id = f"game_{game_id}"

        # Load events to extract metadata
        events = self.load_events(game_file)
        metadata = self._get_game_metadata(events)
        metadata["game_file"] = game_file
        metadata["num_events"] = len(events)

        return GameTask(
            task_id=task_id,
            game_file=game_file,
            game_id=game_id,
            metadata=metadata,
        )

    def create_task_list(
        self,
        split: str = "train",
        shuffle: bool = True,
    ) -> list[GameTask]:
        """Create a list of tasks for the specified split.

        Args:
            split: One of "train", "val", "test", or "all"
            shuffle: Whether to shuffle the tasks

        Returns:
            List of GameTask objects
        """
        game_files = self.list_game_files()
        if not game_files:
            logger.warning("No game files found in %s", self.data_dir)
            return []

        # Create deterministic split based on file hash
        rng = random.Random(self.seed)
        shuffled_files = game_files.copy()
        rng.shuffle(shuffled_files)

        n_total = len(shuffled_files)
        n_train = int(n_total * self.train_ratio)
        n_val = int(n_total * self.val_ratio)

        if split == "train":
            selected_files = shuffled_files[:n_train]
        elif split == "val":
            selected_files = shuffled_files[n_train : n_train + n_val]
        elif split == "test":
            selected_files = shuffled_files[n_train + n_val :]
        elif split == "all":
            selected_files = shuffled_files
        else:
            raise ValueError(f"Unknown split: {split}")

        # Optionally shuffle the selected files
        if shuffle:
            rng = random.Random()  # Use fresh random state
            rng.shuffle(selected_files)

        # Create tasks
        tasks = []
        for game_file in selected_files:
            try:
                task = self.create_task(game_file)
                tasks.append(task)
            except Exception as e:
                logger.warning("Failed to create task for %s: %s", game_file, e)

        logger.info(
            "Created %d tasks for split '%s' from %d total files",
            len(tasks),
            split,
            n_total,
        )
        return tasks

    def export_task_list(
        self,
        output_file: str,
        split: str = "train",
    ) -> None:
        """Export task list to a JSONL file for AgentJet.

        Args:
            output_file: Path to output JSONL file
            split: Data split to export
        """
        tasks = self.create_task_list(split=split, shuffle=False)

        with open(output_file, "w", encoding="utf-8") as f:
            for task in tasks:
                task_dict = {
                    "task_id": task.task_id,
                    "game_file": task.game_file,
                    "game_id": task.game_id,
                    "metadata": task.metadata,
                }
                f.write(json.dumps(task_dict) + "\n")

        logger.info("Exported %d tasks to %s", len(tasks), output_file)
