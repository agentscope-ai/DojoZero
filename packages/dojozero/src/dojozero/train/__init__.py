"""DojoZero RL Training module for AgentJet integration.

This module provides components to train betting agents using GRPO
with the AgentJet framework while reusing DojoZero's existing
BettingAgent and BrokerOperator infrastructure.

Components:
- model_adapter: Adapts AgentJet's OpenAI-compatible API to agentscope model
- event_filter: Filters and routes events to agent/broker
- data_loader: Loads game data and creates training task lists
- reward: Calculates reward based on betting outcomes
- episode_runner: Runs a single training episode
- agent_run: Entry point for Swarm worker
- agent_roll: Training loop with SwarmClient
"""

from packages.dojozero.src.dojozero.train.event_filter import EventFilter, EventFilterMode
from packages.dojozero.src.dojozero.train.data_loader import DojoDataLoader, GameTask
from packages.dojozero.src.dojozero.train.reward import calculate_reward
from packages.dojozero.src.dojozero.train.model_adapter import (
    AgentJetModelWrapper,
    create_agentjet_model,
    create_agentjet_formatter,
)
from packages.dojozero.src.dojozero.train.episode_runner import EpisodeRunner, run_episode
from packages.dojozero.src.dojozero.train.agent_run import (
    run_agent_and_compute_reward,
    run_agent_and_compute_reward_async,
    create_task_from_game_file,
)

__all__ = [
    # Event filtering
    "EventFilter",
    "EventFilterMode",
    # Data loading
    "DojoDataLoader",
    "GameTask",
    # Reward calculation
    "calculate_reward",
    # Model adapter
    "AgentJetModelWrapper",
    "create_agentjet_model",
    "create_agentjet_formatter",
    # Episode execution
    "EpisodeRunner",
    "run_episode",
    # Swarm entry points
    "run_agent_and_compute_reward",
    "run_agent_and_compute_reward_async",
    "create_task_from_game_file",
]
