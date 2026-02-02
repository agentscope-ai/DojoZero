"""Replay WebSocket endpoint for frontend testing.

This module provides a WebSocket endpoint that replays recorded snapshot data,
allowing frontend developers to test and debug without needing a live trial.

Usage:
    Connect to: /ws/test/replay
    
    Control commands (send as JSON):
        {"command": "speed", "value": 2}     - Set playback speed (0.5x to 10x)
        {"command": "pause"}                  - Pause playback
        {"command": "resume"}                 - Resume playback  
        {"command": "reset"}                  - Reset to beginning
        {"command": "skip", "value": 10}      - Skip forward N events
        {"command": "seek", "value": 50}      - Jump to event index N
        {"command": "status"}                 - Get current playback status

    Server messages:
        {"type": "snapshot", ...}             - Initial batch of events
        {"type": "span", ...}                 - Single event during playback
        {"type": "replay_status", ...}        - Playback status update
        {"type": "trial_ended", ...}          - End of replay data
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, NamedTuple

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel

LOGGER = logging.getLogger("dojozero.arena_server.replay")

# Default snapshot data file path (relative to this module)
DEFAULT_SNAPSHOT_PATH = Path(__file__).parent / "snapshot_data.json"


class ReplayStatusMessage(BaseModel):
    """Playback status message sent to clients."""

    type: str = "replay_status"
    current_index: int
    total_items: int
    is_paused: bool
    speed: float
    progress_percent: float
    timestamp: str


@dataclass
class ReplayController:
    """Controls the replay of recorded snapshot data.
    
    Manages playback state including position, speed, and pause status.
    Provides methods for controlling playback via WebSocket commands.
    """

    items: list[dict[str, Any]] = field(default_factory=list)
    current_index: int = 0
    speed: float = 1.0  # Playback speed multiplier
    is_paused: bool = False
    base_interval: float = 0.5  # Base interval between events in seconds
    
    # Number of initial items to send as snapshot (non-play events typically)
    snapshot_size: int = 10

    @classmethod
    def from_file(cls, path: Path | str) -> "ReplayController":
        """Load replay data from a JSON file."""
        path = Path(path)
        if not path.exists():
            LOGGER.warning("Snapshot file not found: %s", path)
            return cls(items=[])
        
        try:
            with open(path) as f:
                data = json.load(f)
            items = data.get("items", [])
            LOGGER.info("Loaded %d items from %s", len(items), path)
            return cls(items=items)
        except Exception as e:
            LOGGER.error("Failed to load snapshot file: %s", e)
            return cls(items=[])

    def reset(self) -> None:
        """Reset playback to the beginning."""
        self.current_index = 0
        self.is_paused = False
        LOGGER.debug("Replay reset to beginning")

    def pause(self) -> None:
        """Pause playback."""
        self.is_paused = True
        LOGGER.debug("Replay paused at index %d", self.current_index)

    def resume(self) -> None:
        """Resume playback."""
        self.is_paused = False
        LOGGER.debug("Replay resumed from index %d", self.current_index)

    def set_speed(self, speed: float) -> None:
        """Set playback speed (clamped to 0.1x - 10x)."""
        self.speed = max(0.1, min(10.0, speed))
        LOGGER.debug("Replay speed set to %.1fx", self.speed)

    def skip(self, count: int) -> None:
        """Skip forward by count events."""
        self.current_index = min(
            self.current_index + count, 
            len(self.items) - 1
        )
        LOGGER.debug("Skipped to index %d", self.current_index)

    def seek(self, index: int) -> None:
        """Jump to a specific event index."""
        self.current_index = max(0, min(index, len(self.items) - 1))
        LOGGER.debug("Seeked to index %d", self.current_index)

    def get_snapshot_items(self) -> list[dict[str, Any]]:
        """Get the initial snapshot items to send on connection."""
        # Send first N items as snapshot, or fewer if not enough items
        count = min(self.snapshot_size, len(self.items))
        self.current_index = count
        return self.items[:count]

    def get_next_item(self) -> dict[str, Any] | None:
        """Get the next item to send, or None if at end."""
        if self.current_index >= len(self.items):
            return None
        item = self.items[self.current_index]
        self.current_index += 1
        return item

    def get_effective_interval(self) -> float:
        """Get the actual interval between events based on speed."""
        return self.base_interval / self.speed

    def is_complete(self) -> bool:
        """Check if all items have been sent."""
        return self.current_index >= len(self.items)

    def get_status(self) -> ReplayStatusMessage:
        """Get current playback status."""
        total = len(self.items)
        progress = (self.current_index / total * 100) if total > 0 else 0
        return ReplayStatusMessage(
            current_index=self.current_index,
            total_items=total,
            is_paused=self.is_paused,
            speed=self.speed,
            progress_percent=round(progress, 1),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )


def _make_span_message(
    item: dict[str, Any],
    trial_id: str = "test-replay",
) -> dict[str, Any]:
    """Convert a snapshot item to a WebSocket span message."""
    return {
        "type": "span",
        "trial_id": trial_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "category": item.get("category", ""),
        "data": item.get("data", {}),
    }


def _make_snapshot_message(
    items: list[dict[str, Any]],
    trial_id: str = "test-replay",
) -> dict[str, Any]:
    """Convert items to a WebSocket snapshot message."""
    return {
        "type": "snapshot",
        "trial_id": trial_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {"items": items},
    }


def _make_trial_ended_message(trial_id: str = "test-replay") -> dict[str, Any]:
    """Create a trial ended message."""
    return {
        "type": "trial_ended",
        "trial_id": trial_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


class CommandResult(NamedTuple):
    """Result of handling a replay control command."""

    status: ReplayStatusMessage | None
    snapshot_items: list[dict[str, Any]] | None  # Items to send as snapshot


async def _handle_command(
    controller: ReplayController,
    command_data: dict[str, Any],
) -> CommandResult:
    """Handle a control command from the client.

    For seek/skip/reset commands, returns snapshot_items that should be sent
    to the client so they have the full state up to the current position.

    Returns:
        CommandResult with status and optional snapshot items.
    """
    command = command_data.get("command", "")
    value = command_data.get("value")
    snapshot_items: list[dict[str, Any]] | None = None

    if command == "pause":
        controller.pause()
    elif command == "resume":
        controller.resume()
    elif command == "reset":
        controller.reset()
        # Reset needs to re-send initial snapshot
        snapshot_items = controller.get_snapshot_items()
    elif command == "speed" and value is not None:
        controller.set_speed(float(value))
    elif command == "skip" and value is not None:
        old_index = controller.current_index
        controller.skip(int(value))
        # Send skipped events as snapshot
        snapshot_items = controller.items[old_index : controller.current_index]
    elif command == "seek" and value is not None:
        target_index = max(0, min(int(value), len(controller.items) - 1))
        # Send all events from 0 to target_index as snapshot
        snapshot_items = controller.items[:target_index]
        controller.current_index = target_index
        LOGGER.debug("Seek to %d, sending %d items as snapshot", target_index, len(snapshot_items))
    elif command == "status":
        pass  # Just return status
    else:
        LOGGER.warning("Unknown command: %s", command)
        return CommandResult(status=None, snapshot_items=None)

    return CommandResult(
        status=controller.get_status(),
        snapshot_items=snapshot_items,
    )


def create_replay_websocket_handler(
    snapshot_path: Path | str | None = None,
) -> Callable[[WebSocket], Coroutine[Any, Any, None]]:
    """Create a WebSocket handler for replay functionality.
    
    Args:
        snapshot_path: Path to snapshot JSON file. Defaults to bundled snapshot_data.json.
    
    Returns:
        An async function that handles WebSocket connections for replay.
    """
    path = Path(snapshot_path) if snapshot_path else DEFAULT_SNAPSHOT_PATH

    async def handler(websocket: WebSocket) -> None:
        """Handle a replay WebSocket connection."""
        await websocket.accept()
        LOGGER.info("Replay WebSocket connection accepted")

        # Create a fresh controller for each connection
        controller = ReplayController.from_file(path)
        
        if not controller.items:
            await websocket.send_json({
                "type": "error",
                "message": "No replay data available",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await websocket.close()
            return

        trial_id = "test-replay"

        try:
            # Send initial snapshot
            snapshot_items = controller.get_snapshot_items()
            snapshot_msg = _make_snapshot_message(snapshot_items, trial_id)
            await websocket.send_json(snapshot_msg)
            LOGGER.info("Sent snapshot with %d items", len(snapshot_items))

            # Send initial status
            status = controller.get_status()
            await websocket.send_json(status.model_dump())

            # Main playback loop
            while True:
                try:
                    # Check for client commands (non-blocking with timeout)
                    msg_text = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=controller.get_effective_interval(),
                    )

                    # Parse and handle command
                    try:
                        command_data = json.loads(msg_text)
                        result = await _handle_command(controller, command_data)

                        # If snapshot items need to be sent (seek/skip/reset)
                        if result.snapshot_items:
                            snapshot_msg = _make_snapshot_message(
                                result.snapshot_items, trial_id
                            )
                            await websocket.send_json(snapshot_msg)
                            LOGGER.info(
                                "Sent snapshot with %d items after command",
                                len(result.snapshot_items),
                            )

                        # Send status update
                        if result.status:
                            await websocket.send_json(result.status.model_dump())
                    except json.JSONDecodeError:
                        LOGGER.warning("Invalid JSON command: %s", msg_text)

                except asyncio.TimeoutError:
                    # No command received, continue playback if not paused
                    if controller.is_paused:
                        continue

                    if controller.is_complete():
                        # Send trial ended message
                        ended_msg = _make_trial_ended_message(trial_id)
                        await websocket.send_json(ended_msg)
                        LOGGER.info("Replay completed, sent trial_ended")
                        
                        # Pause at end, client can reset to replay
                        controller.pause()
                        status = controller.get_status()
                        await websocket.send_json(status.model_dump())
                        continue

                    # Send next item
                    item = controller.get_next_item()
                    if item:
                        span_msg = _make_span_message(item, trial_id)
                        await websocket.send_json(span_msg)

                        # Send status update every 50 items
                        if controller.current_index % 50 == 0:
                            status = controller.get_status()
                            await websocket.send_json(status.model_dump())

        except WebSocketDisconnect:
            LOGGER.info("Replay WebSocket disconnected")
        except Exception as e:
            LOGGER.error("Replay WebSocket error: %s", e)
        finally:
            LOGGER.debug("Replay handler cleanup complete")

    return handler


__all__ = [
    "CommandResult",
    "ReplayController",
    "ReplayStatusMessage",
    "create_replay_websocket_handler",
    "DEFAULT_SNAPSHOT_PATH",
]
