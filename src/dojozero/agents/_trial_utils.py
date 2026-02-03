"""Shared utilities for agent config processing in trials.

This module provides common functions for building agent specs from
configuration dicts, supporting both inline configs and YAML file expansion.
"""

import logging
from typing import Any, TypeVar

from dojozero.agents._config import (
    AgentConfig,
    LLMConfig,
    load_agent_config,
    expand_agent_config,
)
from dojozero.core import AgentSpec

logger = logging.getLogger(__name__)

# Type variable for agent config types (e.g., BettingAgentConfig)
TAgentConfig = TypeVar("TAgentConfig", bound=dict[str, Any])


def load_agent_configs_cached(
    agents: list[dict[str, Any]],
) -> dict[tuple[str, str], AgentConfig]:
    """Load and cache agent configs from YAML files.

    Loads each unique (persona_config_path, llm_config_path) pair only once.

    Args:
        agents: List of agent configuration dicts

    Returns:
        Dict mapping (persona_config_path, llm_config_path) to loaded AgentConfig
    """
    config_cache: dict[tuple[str, str], AgentConfig] = {}

    for agent_dict in agents:
        persona_config_path = agent_dict.get("persona_config_path")
        llm_config_path = agent_dict.get("llm_config_path")

        if persona_config_path and llm_config_path:
            cache_key = (persona_config_path, llm_config_path)
            if cache_key not in config_cache:
                config_cache[cache_key] = load_agent_config(
                    persona_config_path,
                    llm_config_path,
                    name=agent_dict.get("persona", ""),
                )

    return config_cache


def get_expanded_agent_ids(
    agent_dict: dict[str, Any],
    config_cache: dict[tuple[str, str], AgentConfig] | None = None,
) -> list[str]:
    """Get the list of agent IDs that will be created from an agent config.

    If persona_config_path and llm_config_path are specified, returns expanded IDs.
    Otherwise, returns the single agent ID.

    Args:
        agent_dict: Agent configuration dict with id, optional config paths
        config_cache: Optional cache of loaded configs to avoid re-loading

    Returns:
        List of agent IDs (expanded if using YAML config with multiple models)
    """
    agent_id = agent_dict.get("id")
    if not agent_id:
        return []

    persona_config_path = agent_dict.get("persona_config_path")
    llm_config_path = agent_dict.get("llm_config_path")

    if persona_config_path and llm_config_path:
        cache_key = (persona_config_path, llm_config_path)
        # Use cached config if available, otherwise load
        if config_cache and cache_key in config_cache:
            yaml_config = config_cache[cache_key]
        else:
            yaml_config = load_agent_config(
                persona_config_path,
                llm_config_path,
                name=agent_dict.get("persona", ""),
            )

        expanded_ids = []
        for llm_config in yaml_config["llm"]:
            model_name = llm_config.get("model_name", "unknown")
            expanded_ids.append(f"{agent_id}-{model_name}")
        return expanded_ids
    else:
        return [str(agent_id)]


def build_operator_to_agents_map(
    agents: list[dict[str, Any]],
    config_cache: dict[tuple[str, str], AgentConfig] | None = None,
) -> dict[str, list[str]]:
    """Build a mapping from operator IDs to agent IDs.

    For agents with config paths, expands to include all model-specific agent IDs.

    Args:
        agents: List of agent configuration dicts
        config_cache: Optional cache of loaded configs to avoid re-loading

    Returns:
        Dict mapping operator_id to list of agent IDs that reference it
    """
    operator_to_agents: dict[str, list[str]] = {}

    for agent_dict in agents:
        agent_id = agent_dict.get("id")
        if not agent_id:
            continue

        operator_ids = agent_dict.get("operators", [])
        expanded_ids = get_expanded_agent_ids(agent_dict, config_cache)

        for op_id in operator_ids:
            if op_id not in operator_to_agents:
                operator_to_agents[op_id] = []
            operator_to_agents[op_id].extend(expanded_ids)

    return operator_to_agents


def build_agent_specs(
    agents: list[dict[str, Any]],
    agent_cls: type[Any],
    allowed_class_names: set[str] | None = None,
    config_cache: dict[tuple[str, str], AgentConfig] | None = None,
) -> list[AgentSpec[Any]]:
    """Build AgentSpec instances from agent configuration dicts.

    Supports two modes:
    1. YAML config paths: Loads config and expands into multiple agents (one per model)
    2. Inline config: Creates a single agent with specified config

    Args:
        agents: List of agent configuration dicts
        agent_cls: The agent class to use (e.g., BettingAgent)
        allowed_class_names: Set of allowed class names (default: {"BettingAgent"})
        config_cache: Optional cache of loaded configs to avoid re-loading

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

        persona_config_path = agent_dict.get("persona_config_path")
        llm_config_path = agent_dict.get("llm_config_path")

        if persona_config_path and llm_config_path:
            cache_key = (persona_config_path, llm_config_path)
            # Use cached config if available, otherwise load
            if config_cache and cache_key in config_cache:
                yaml_config = config_cache[cache_key]
            else:
                yaml_config = load_agent_config(
                    persona_config_path,
                    llm_config_path,
                    name=agent_dict.get("persona", ""),
                )

            expanded_configs = expand_agent_config(yaml_config)

            for single_config in expanded_configs:
                # Create unique actor_id using model name
                model_name = single_config["llm"].get("model_name", "unknown")
                expanded_actor_id = f"{agent_id}-{model_name}"

                # Extract persona from agent_dict (e.g., "degen" from persona field)
                persona = agent_dict.get("persona", "")

                agent_config: dict[str, Any] = {
                    "actor_id": expanded_actor_id,
                    "persona": persona,
                    "sys_prompt": single_config["sys_prompt"],
                    "llm": single_config["llm"],
                    # Display fields for agent registration
                    "model_display_name": single_config["llm"].get(
                        "model_display_name", ""
                    ),
                    "cdn_url": single_config["llm"].get("cdn_url", ""),
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
            inline_agent_config: dict[str, Any] = {
                "actor_id": agent_id,
            }
            # Copy optional config fields
            if agent_dict.get("persona"):
                inline_agent_config["persona"] = agent_dict["persona"]
            if agent_dict.get("sys_prompt"):
                inline_agent_config["sys_prompt"] = agent_dict["sys_prompt"]

            # Build LLM config if model_type or model_name are specified
            if agent_dict.get("model_type") or agent_dict.get("model_name"):
                inline_llm_config: LLMConfig = {}
                if agent_dict.get("model_type"):
                    inline_llm_config["model_type"] = agent_dict["model_type"]
                if agent_dict.get("model_name"):
                    inline_llm_config["model_name"] = agent_dict["model_name"]
                if agent_dict.get("model_display_name"):
                    inline_llm_config["model_display_name"] = agent_dict[
                        "model_display_name"
                    ]
                if agent_dict.get("cdn_url"):
                    inline_llm_config["cdn_url"] = agent_dict["cdn_url"]
                inline_agent_config["llm"] = inline_llm_config
                inline_agent_config["model_display_name"] = agent_dict.get(
                    "model_display_name", ""
                )
                inline_agent_config["cdn_url"] = agent_dict.get("cdn_url", "")

            agent_spec = AgentSpec(
                actor_id=agent_id,
                actor_cls=agent_cls,
                config=inline_agent_config,
                operator_ids=tuple(operator_ids) if operator_ids else (),
                data_stream_ids=tuple(data_stream_ids),
            )
            agent_specs.append(agent_spec)

    return agent_specs
