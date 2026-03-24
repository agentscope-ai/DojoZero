"""Agent configuration and utilities for DojoZero."""

from ._config import (
    load_agent_config,
    load_persona_config,
    load_llm_file_config,
    AgentConfig,
    SingleModelAgentConfig,
    LLMConfig,
    PersonaConfig,
    LLMFileConfig,
    create_model,
    create_formatter,
    expand_agent_config,
)
from ._toolkit import create_toolkit, tool
from ._social_board import (
    HotTopicsEvent,
    SocialBoard,
    SocialMessage,
    create_social_board_tools,
    format_hot_topics_for_llm,
    SocialBoardActor,
    SocialBoardConfig,
)
from ._trial_utils import (
    get_expanded_agent_ids,
    build_operator_to_agents_map,
    build_agent_specs,
    load_agent_configs_cached,
)

__all__ = [
    "load_agent_config",
    "load_persona_config",
    "load_llm_file_config",
    "AgentConfig",
    "SingleModelAgentConfig",
    "LLMConfig",
    "PersonaConfig",
    "LLMFileConfig",
    "create_toolkit",
    "create_model",
    "create_formatter",
    "expand_agent_config",
    "tool",
    # Social board (multi-agent communication)
    "HotTopicsEvent",
    "SocialBoard",
    "SocialMessage",
    "create_social_board_tools",
    "format_hot_topics_for_llm",
    "SocialBoardActor",
    "SocialBoardConfig",
    # Trial utilities
    "get_expanded_agent_ids",
    "build_operator_to_agents_map",
    "build_agent_specs",
    "load_agent_configs_cached",
]
