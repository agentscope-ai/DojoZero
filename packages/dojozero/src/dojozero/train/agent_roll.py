"""DojoZero Betting Agent Training Script.

This script sets up the training loop for the betting agent using AgentJet Swarm.

Prerequisites:
    1. Start Swarm server: ajet-swarm start
    2. (Optional) Start overwatch: ajet-swarm overwatch

Usage:
    cd /mnt/data/dengjiaji.djj/DojoZero
    python -m dojozero.train.agent_roll

Environment Variables:
    SWARM_URL: Swarm server URL (default: http://localhost:10086)
    DATA_DIR: Directory containing game JSONL files
    OSS_ENDPOINT, OSS_ID, OSS_KEY: OSS configuration (loaded from .env)
"""

import os
import logging
from datetime import datetime
from pathlib import Path

# Load .env file from workspace root
from dotenv import load_dotenv
env_path = Path("/mnt/data/dengjiaji.djj/.env")
if env_path.exists():
    load_dotenv(env_path)
    print(f"Loaded environment from {env_path}")

from ajet.copilot.job import AgentJetJob
from ajet.tuner_lib.experimental.swarm_client import SwarmClient, run_episodes_until_all_complete
from ajet.schema.task import Task

from packages.dojozero.src.dojozero.train.data_loader import DojoDataLoader, GameTask
from packages.dojozero.src.dojozero.train.agent_run import run_agent_and_compute_reward

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
# Silence noisy broker internals in rollout logs.
logging.getLogger("dojozero.betting._broker").disabled = True
logger = logging.getLogger(__name__)


# ==================== Configuration ====================

# Local configurations (client-side)
LOCAL_GRPO_N = 16  # GRPO group size (number of rollouts per task)
LOCAL_NUM_EPOCH = 4  # Number of training epochs
LOCAL_MAX_PARALLEL = 8  # Maximum parallel episodes

# Remote configurations (server-side)
REMOTE_SWARM_URL = os.environ.get("SWARM_URL", "http://localhost:10086")
REMOTE_BATCH_SIZE = 8  # Adjusted for 4x H20 GPUs
REMOTE_ALLOCATE_GPU_PER_NODE = 4  # 4x H20 GPUs
REMOTE_BASE_YAML_CONFIG = "/mnt/data/dengjiaji.djj/AgentJet/ajet/default_config/ajet_ts_default.yaml"

# Model configuration
# MODEL_NAME = "Qwen3-8B"
MODEL_NAME = "Qwen2.5-7B-Instruct"
REMOTE_TRAIN_MODEL = f"/mnt/data/shared/qwen/{MODEL_NAME}"

# Data configuration
DATA_DIR = os.environ.get("DATA_DIR", "/mnt/data/dengjiaji.djj/dojo_data")

# Training configuration
EVENT_FILTER_MODE = "sampled"  # "full", "scoring", "sampled"
EVENT_SAMPLE_RATE = 50  # For "sampled" mode: keep every Nth event
INITIAL_BALANCE = "1000.00"


EXPERIMENT_NAME = f"dojo-nba-126-trials-{datetime.now().strftime('%Y%m%d')}-{MODEL_NAME}"
PROJECT_NAME = "dojo-betting"

# ==================== Helper Functions ====================

def game_task_to_ajet_task(game_task: GameTask) -> Task:
    """Convert a DojoZero GameTask to an AgentJet Task.

    Args:
        game_task: DojoZero game task

    Returns:
        AgentJet Task object
    """
    return Task(
        task_id=game_task.task_id,
        metadata={
            "game_file": game_task.game_file,
            "game_id": game_task.game_id,
            **game_task.metadata,
        },
    )


# ==================== Main Training Function ====================

def main():
    """Main training loop for DojoZero betting agent."""

    print("=" * 60)
    print("DojoZero Betting Agent Training")
    print("=" * 60)

    # Load training data
    print(f"\nLoading training data from {DATA_DIR}...")
    data_loader = DojoDataLoader(data_dir=DATA_DIR)
    game_tasks = data_loader.create_task_list(split="train")

    if not game_tasks:
        print("Error: No training data found.")
        print(f"Please ensure JSONL files exist in {DATA_DIR}")
        return

    print(f"Found {len(game_tasks)} training games")

    # Convert to AgentJet tasks
    tasks = [game_task_to_ajet_task(gt) for gt in game_tasks]

    # Initialize swarm client
    print(f"\nConnecting to swarm server at {REMOTE_SWARM_URL}...")
    swarm_worker = SwarmClient(REMOTE_SWARM_URL)

    # Configure and start training engine
    print("Configuring training engine...")
    yaml_job = AgentJetJob(
        base_yaml_config=REMOTE_BASE_YAML_CONFIG,
        algorithm="grpo",
        project_name=PROJECT_NAME,
        experiment_name=EXPERIMENT_NAME,
        n_gpu=REMOTE_ALLOCATE_GPU_PER_NODE,
        model=REMOTE_TRAIN_MODEL,
        batch_size=REMOTE_BATCH_SIZE,
        num_repeat=LOCAL_GRPO_N,
    )

    # Disable CPU offloading — H20 GPUs have 97GB VRAM each, plenty for a 7B model.
    # Offloading to CPU caused OOM: 4 workers × ~45GB each ≈ 180GB
    # yaml_job.config.ajet.trainer_common.fsdp_config.param_offload = False
    # yaml_job.config.ajet.trainer_common.fsdp_config.optimizer_offload = False

    swarm_worker.auto_sync_train_config_and_start_engine(yaml_job)
    print("Training engine started!")

    # Define rollout function
    def rollout(task: Task) -> float | None:
        """Execute a single episode rollout.

        Args:
            task: The task to execute

        Returns:
            Reward value or None if failed
        """
        try:
            # Begin episode - get API credentials from swarm
            episode_uuid, api_baseurl_key = swarm_worker.begin_episode()

            # Execute agent
            workflow_output = run_agent_and_compute_reward(
                task=task,
                base_url=api_baseurl_key.base_url,
                api_key=api_baseurl_key.api_key,
                event_filter_mode=EVENT_FILTER_MODE,
                event_sample_rate=EVENT_SAMPLE_RATE,
                initial_balance=INITIAL_BALANCE,
            )

            # Report output back to swarm server
            swarm_worker.end_episode(task, episode_uuid, workflow_output)

            # Print rollout statistics
            swarm_worker.print_rollout_stat()

            reward = workflow_output.reward
            if isinstance(reward, list):
                return reward[0] if reward else 0.0
            return reward if reward is not None else 0.0

        except Exception as e:
            logger.exception("Episode failed: %s", e)
            return None

    # Training loop
    print("\nStarting training loop...")
    print(f"Configuration:")
    print(f"  - GRPO N: {LOCAL_GRPO_N}")
    print(f"  - Batch Size: {REMOTE_BATCH_SIZE}")
    print(f"  - Max Epochs: {LOCAL_NUM_EPOCH}")
    print(f"  - Model: {REMOTE_TRAIN_MODEL}")
    print(f"  - GPUs: {REMOTE_ALLOCATE_GPU_PER_NODE}")
    print(f"  - Event Filter: {EVENT_FILTER_MODE} (sample_rate={EVENT_SAMPLE_RATE})")
    print(f"  - Initial Balance: {INITIAL_BALANCE}")
    print("=" * 60)

    next_batch = []
    total_episodes = 0
    total_rewards = 0.0
    successful_episodes = 0

    try:
        for epoch in range(LOCAL_NUM_EPOCH):
            print(f"\n{'='*20} Epoch {epoch + 1}/{LOCAL_NUM_EPOCH} {'='*20}")

            # Iterate through tasks
            for task in tasks:
                # Rollout GRPO_N times for this task
                for _ in range(LOCAL_GRPO_N):
                    next_batch.append(task)

                    # Execute batch when ready
                    if len(next_batch) >= (REMOTE_BATCH_SIZE * LOCAL_GRPO_N):
                        print(f"\nExecuting batch of {len(next_batch)} episodes...")

                        episode_results = run_episodes_until_all_complete(
                            next_batch,
                            func=rollout,
                            max_workers=LOCAL_MAX_PARALLEL,
                            auto_retry=True,
                        )

                        total_episodes += len(next_batch)

                        # Compute statistics
                        batch_successful = sum(1 for r in episode_results if r is not None)
                        batch_rewards = [r for r in episode_results if r is not None]
                        batch_avg_reward = sum(batch_rewards) / max(len(batch_rewards), 1)

                        successful_episodes += batch_successful
                        total_rewards += sum(batch_rewards)

                        print(f"Batch complete:")
                        print(f"  - Total episodes: {total_episodes}")
                        print(f"  - Successful: {batch_successful}/{len(next_batch)}")
                        print(f"  - Batch avg reward (ROI): {batch_avg_reward:.2f}%")
                        print(f"  - Overall avg reward: {total_rewards/max(successful_episodes,1):.2f}%")

                        next_batch.clear()

            # End of epoch summary
            if successful_episodes > 0:
                epoch_avg = total_rewards / successful_episodes
                print(f"\nEpoch {epoch + 1} summary:")
                print(f"  - Total episodes: {total_episodes}")
                print(f"  - Success rate: {successful_episodes/total_episodes*100:.1f}%")
                print(f"  - Average ROI: {epoch_avg:.2f}%")

    except KeyboardInterrupt:
        print("\n\nTraining interrupted by user")
    except Exception as e:
        logger.exception("Training failed with error: %s", e)
    finally:
        # Execute remaining episodes if any
        if next_batch:
            print("\nExecuting remaining episodes...")
            run_episodes_until_all_complete(next_batch, func=rollout, max_workers=LOCAL_MAX_PARALLEL, auto_retry=True)

        print("\n" + "=" * 60)
        print("Training complete!")
        print(f"Total episodes executed: {total_episodes}")
        if successful_episodes > 0:
            print(f"Overall average ROI: {total_rewards/successful_episodes:.2f}%")
        print("=" * 60)


if __name__ == "__main__":
    main()
