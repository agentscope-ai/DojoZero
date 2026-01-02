"""Agent group implementation using AgentScope's MsgHub for communication."""

import logging
from pathlib import Path
from typing import Any, Sequence

from agentscope.message import Msg
from agentscope.pipeline import MsgHub

from agentx.core import Operator, StreamEvent

from .agent import BettingAgent

LOGGER = logging.getLogger("agentx.agents.group")


class BettingAgentGroup:
    """A group of BettingAgents that can communicate via MsgHub.

    Agents in the group can see each other's replies but not internal
    thinking or tool execution.
    """

    def __init__(
        self,
        config_paths: Sequence[str | Path],
    ) -> None:
        """Initialize agent group from config paths.

        Args:
            config_paths: List of paths to agent YAML config files
        """
        self._agents: list[BettingAgent] = []
        self._agent_colors: dict[str, str] = {}

        for i, path in enumerate(config_paths):
            agent = BettingAgent.from_yaml(path)
            self._agents.append(agent)
            LOGGER.info(
                "[%s] added to group",
                agent.name,
            )

    async def register_operators(self, operators: Sequence[Operator]) -> None:
        """Register operators for all agents in the group."""
        for agent in self._agents:
            await agent.register_operators(operators)

    @property
    def agents(self) -> list[BettingAgent]:
        """Return list of agents in the group."""
        return self._agents

    @property
    def agent_ids(self) -> list[str]:
        """Return list of agent IDs in the group."""
        return [a.actor_id for a in self._agents]

    def get_agent(self, agent_id: str) -> BettingAgent:
        """Get agent by ID."""
        for agent in self._agents:
            if agent.actor_id == agent_id:
                return agent
        raise KeyError(f"Agent '{agent_id}' not found in group")

    async def start(self) -> None:
        """Start all agents in the group."""
        LOGGER.info("Starting agent group with %d agents", len(self._agents))
        for agent in self._agents:
            await agent.start()

    async def stop(self) -> None:
        """Stop all agents in the group."""
        LOGGER.info("Stopping agent group")
        for agent in self._agents:
            await agent.stop()

    async def handle_stream_event(
        self,
        event: StreamEvent[Any],
        max_rounds: int = 1,
    ) -> list[Msg]:
        """Process stream event with all agents in the group.

        All agents discuss the event using MsgHub. Each agent can see
        other agents' replies but not their internal reasoning.

        Args:
            event: The stream event to process
            max_rounds: Maximum discussion rounds (each agent speaks once per round)

        Returns:
            List of all messages generated during discussion
        """
        LOGGER.info(
            "Group processing event seq=%s from stream '%s'",
            event.sequence,
            event.stream_id,
        )

        # Track all messages for return
        all_messages: list[Msg] = []

        # Create announcement from event
        announcement = Msg(
            name="system",
            content=f"New market data: {event.payload}",
            role="user",
        )

        # Use MsgHub for agent communication
        async with MsgHub(
            participants=self._agents,
            announcement=announcement,
        ):
            for round_num in range(max_rounds):
                LOGGER.info("--- Round %d/%d ---", round_num + 1, max_rounds)

                for agent in self._agents:
                    # Agent processes and replies - MsgHub broadcasts to others
                    response = await agent()
                    if response:
                        all_messages.append(response)
                    agent._event_count += 1

        return all_messages
