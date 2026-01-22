"""Shared utilities for agent config processing in trials.

This module provides common functions for building agent specs from
configuration dicts, supporting both inline configs and YAML file expansion.
"""

import logging
from typing import Any, TypeVar

from dojozero.agents._config import (
    LLMConfig,
    load_agent_config,
    expand_agent_config,
)
from dojozero.core import AgentSpec

logger = logging.getLogger(__name__)

# Type variable for agent config types (e.g., BettingAgentConfig)
TAgentConfig = TypeVar("TAgentConfig", bound=dict[str, Any])


def get_expanded_agent_ids(agent_dict: dict[str, Any]) -> list[str]:
    """Get the list of agent IDs that will be created from an agent config.

    If agent_config_path is specified, returns expanded IDs (one per model).
    Otherwise, returns the single agent ID.

    Args:
        agent_dict: Agent configuration dict with id, optional agent_config_path

    Returns:
        List of agent IDs (expanded if using YAML config with multiple models)
    """
    agent_id = agent_dict.get("id")
    if not agent_id:
        return []

    agent_config_path = agent_dict.get("agent_config_path")
    if agent_config_path:
        # Load YAML and get all model names to create expanded IDs
        yaml_config = load_agent_config(agent_config_path)
        expanded_ids = []
        for llm_config in yaml_config["llm"]:
            model_name = llm_config.get("model_name", "unknown")
            expanded_ids.append(f"{agent_id}-{model_name}")
        return expanded_ids
    else:
        return [str(agent_id)]


def build_operator_to_agents_map(
    agents: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Build a mapping from operator IDs to agent IDs.

    For agents with agent_config_path, expands to include all model-specific agent IDs.

    Args:
        agents: List of agent configuration dicts

    Returns:
        Dict mapping operator_id to list of agent IDs that reference it
    """
    operator_to_agents: dict[str, list[str]] = {}

    for agent_dict in agents:
        agent_id = agent_dict.get("id")
        if not agent_id:
            continue

        operator_ids = agent_dict.get("operators", [])
        expanded_ids = get_expanded_agent_ids(agent_dict)

        for op_id in operator_ids:
            if op_id not in operator_to_agents:
                operator_to_agents[op_id] = []
            operator_to_agents[op_id].extend(expanded_ids)

    return operator_to_agents


def build_agent_specs(
    agents: list[dict[str, Any]],
    agent_cls: type[Any],
    allowed_class_names: set[str] | None = None,
) -> list[AgentSpec[Any]]:
    """Build AgentSpec instances from agent configuration dicts.

    Supports two modes:
    1. YAML config path: Loads config and expands into multiple agents (one per model)
    2. Inline config: Creates a single agent with specified config

    Args:
        agents: List of agent configuration dicts
        agent_cls: The agent class to use (e.g., BettingAgent)
        allowed_class_names: Set of allowed class names (default: {"BettingAgent"})

    Returns:
        List of AgentSpec instances

    Raises:
        ValueError: If agent config is invalid or uses unsupported class
    """
    if allowed_class_names is None:
        allowed_class_names = {"BettingAgent"}

    agent_specs: list[AgentSpec[Any]] = []

    for agent_dict in agents:
        agent_id = agent_dict.get("id")
        if not agent_id:
            raise ValueError("Agent config missing required 'id' field")

        agent_class_name = agent_dict.get("class")
        if agent_class_name not in allowed_class_names:
            raise ValueError(
                f"Invalid agent class '{agent_class_name}' for agent '{agent_id}'. "
                f"Allowed classes: {sorted(allowed_class_names)}"
            )

        operator_ids = agent_dict.get("operators", [])
        data_stream_ids = agent_dict.get("data_streams", [])

        agent_config_path = agent_dict.get("agent_config_path")

        if agent_config_path:
            # Load YAML and expand into multiple agents (one per model)
            yaml_config = load_agent_config(agent_config_path)
            expanded_configs = expand_agent_config(yaml_config)

            for single_config in expanded_configs:
                # Create unique actor_id using model name
                model_name = single_config["llm"].get("model_name", "unknown")
                expanded_actor_id = f"{agent_id}-{model_name}"

                agent_config: dict[str, Any] = {
                    "actor_id": expanded_actor_id,
                    "name": single_config["name"],
                    "sys_prompt": single_config["sys_prompt"],
                    "llm": single_config["llm"],
                }

                agent_spec = AgentSpec(
                    actor_id=expanded_actor_id,
                    actor_cls=agent_cls,
                    config=agent_config,
                    operator_ids=tuple(operator_ids) if operator_ids else (),
                    data_stream_ids=tuple(data_stream_ids),
                )
                agent_specs.append(agent_spec)
                logger.info(
                    "Expanded agent '%s' with model '%s' -> '%s'",
                    agent_id,
                    model_name,
                    expanded_actor_id,
                )
        else:
            # Inline config mode (no expansion)
            agent_config = {
                "actor_id": agent_id,
            }
            # Copy optional config fields
            if agent_dict.get("name"):
                agent_config["name"] = agent_dict["name"]
            if agent_dict.get("sys_prompt"):
                agent_config["sys_prompt"] = agent_dict["sys_prompt"]

            # Build LLM config if model_type or model_name are specified
            if agent_dict.get("model_type") or agent_dict.get("model_name"):
                llm_config: LLMConfig = {}
                if agent_dict.get("model_type"):
                    llm_config["model_type"] = agent_dict["model_type"]
                if agent_dict.get("model_name"):
                    llm_config["model_name"] = agent_dict["model_name"]
                agent_config["llm"] = llm_config

            agent_spec = AgentSpec(
                actor_id=agent_id,
                actor_cls=agent_cls,
                config=agent_config,
                operator_ids=tuple(operator_ids) if operator_ids else (),
                data_stream_ids=tuple(data_stream_ids),
            )
            agent_specs.append(agent_spec)

    return agent_specs
