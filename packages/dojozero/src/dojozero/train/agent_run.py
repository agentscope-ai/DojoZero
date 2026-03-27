"""Agent execution entry point for Swarm.

This module provides the entry point function called by the Swarm worker
to execute a single training episode.
"""

import asyncio
import logging

from ajet.schema.task import Task, WorkflowOutput

from packages.dojozero.src.dojozero.train.episode_runner import EpisodeRunner

logger = logging.getLogger(__name__)


def run_agent_and_compute_reward(
    task: Task,
    base_url: str,
    api_key: str,
    event_filter_mode: str = "scoring",
    event_sample_rate: int = 5,
    initial_balance: str = "1000.00",
) -> WorkflowOutput:
    """Execute a single episode and compute reward.

    This is the main entry point called by the Swarm worker for each episode.
    It runs the EpisodeRunner synchronously using asyncio.run().

    Args:
        task: AgentJet Task object containing game_file in metadata
        base_url: AgentJet API base URL for model inference
        api_key: AgentJet API key
        event_filter_mode: Event compression mode (full, scoring, sampled)
        event_sample_rate: For "sampled" mode, keep every Nth event
        initial_balance: Starting balance for betting

    Returns:
        WorkflowOutput with reward and metrics
    """
    # Extract game file from task metadata
    game_file = task.metadata.get("game_file")
    if not game_file:
        logger.error("Task missing game_file in metadata: %s", task.task_id)
        return WorkflowOutput(
            reward=0.0,
            is_success=False,
            metadata={"error": "Missing game_file in task metadata"},
        )

    try:
        # Create episode runner
        runner = EpisodeRunner(
            game_file=game_file,
            base_url=base_url,
            api_key=api_key,
            event_filter_mode=event_filter_mode,
            event_sample_rate=event_sample_rate,
            initial_balance=initial_balance,
        )

        # Run episode (sync wrapper around async)
        reward, metadata = asyncio.run(runner.run())

        # Build output (ensure all metrics are JSON-serializable floats/ints)
        stats = metadata["stats"]
        return WorkflowOutput(
            reward=float(reward),
            is_success=reward > 0,
            metadata=metadata,
            log_metrics={
                "roi": float(reward),
                "total_bets": int(stats["total_bets"]),
                "wins": int(stats["wins"]),
                "losses": int(stats["losses"]),
                "win_rate": float(stats["win_rate"]),
                "net_profit": float(stats["net_profit"]),
                "total_wagered": float(stats["total_wagered"]),
            },
        )

    except Exception as e:
        logger.exception("Episode failed for task %s: %s", task.task_id, e)
        return WorkflowOutput(
            reward=0.0,
            is_success=False,
            metadata={"error": str(e), "game_file": game_file},
        )


async def run_agent_and_compute_reward_async(
    task: Task,
    base_url: str,
    api_key: str,
    event_filter_mode: str = "scoring",
    event_sample_rate: int = 5,
    initial_balance: str = "1000.00",
) -> WorkflowOutput:
    """Async version of run_agent_and_compute_reward.

    Use this when calling from an async context to avoid nested event loops.

    Args:
        task: AgentJet Task object containing game_file in metadata
        base_url: AgentJet API base URL for model inference
        api_key: AgentJet API key
        event_filter_mode: Event compression mode
        event_sample_rate: For "sampled" mode, keep every Nth event
        initial_balance: Starting balance for betting

    Returns:
        WorkflowOutput with reward and metrics
    """
    game_file = task.metadata.get("game_file")
    if not game_file:
        logger.error("Task missing game_file in metadata: %s", task.task_id)
        return WorkflowOutput(
            reward=0.0,
            is_success=False,
            metadata={"error": "Missing game_file in task metadata"},
        )

    try:
        runner = EpisodeRunner(
            game_file=game_file,
            base_url=base_url,
            api_key=api_key,
            event_filter_mode=event_filter_mode,
            event_sample_rate=event_sample_rate,
            initial_balance=initial_balance,
        )

        reward, metadata = await runner.run()

        stats = metadata["stats"]
        return WorkflowOutput(
            reward=float(reward),
            is_success=reward > 0,
            metadata=metadata,
            log_metrics={
                "roi": float(reward),
                "total_bets": int(stats["total_bets"]),
                "wins": int(stats["wins"]),
                "losses": int(stats["losses"]),
                "win_rate": float(stats["win_rate"]),
                "net_profit": float(stats["net_profit"]),
                "total_wagered": float(stats["total_wagered"]),
            },
        )

    except Exception as e:
        logger.exception("Episode failed for task %s: %s", task.task_id, e)
        return WorkflowOutput(
            reward=0.0,
            is_success=False,
            metadata={"error": str(e), "game_file": game_file},
        )


def create_task_from_game_file(game_file: str, task_id: str | None = None) -> Task:
    """Create a Task object from a game file path.

    Convenience function for testing or manual execution.

    Args:
        game_file: Path to the JSONL game file
        task_id: Optional task ID (defaults to filename)

    Returns:
        Task object ready for execution
    """
    from pathlib import Path

    if task_id is None:
        task_id = f"game_{Path(game_file).stem}"

    return Task(
        task_id=task_id,
        metadata={"game_file": game_file},
    )
