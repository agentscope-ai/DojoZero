"""Agent group implementation using AgentScope's MsgHub for communication."""

import logging
from pathlib import Path
from typing import Any, Mapping, Sequence, TypedDict

from agentscope.agent import ReActAgent
from agentscope.message import Msg
from agentscope.pipeline import MsgHub

from dojozero.core import ActorContext, Agent, AgentBase, Operator, StreamEvent

from ._agent import BettingAgent

LOGGER = logging.getLogger(__name__)


class _ActorIdConfig(TypedDict):
    actor_id: str


class BettingAgentGroupConfig(_ActorIdConfig, total=False):
    """Configuration for BettingAgentGroup.

    Args:
        actor_id: Unique identifier for the group as an actor
        config_paths: List of paths to agent YAML config files
        max_rounds: Maximum discussion rounds per event (default: 1)
    """

    config_paths: list[str]
    max_rounds: int


class BettingAgentGroup(AgentBase, Agent[BettingAgentGroupConfig]):
    """A group of BettingAgents that can communicate via MsgHub.

    Inherits from AgentBase to implement the Actor protocol. The group
    acts as a single agent that coordinates multiple internal BettingAgent
    instances, allowing them to see each other's replies but not internal
    thinking or tool execution.

    Uses composition to wrap BettingAgent instances and delegates MsgHub
    communication to their internal ReActAgent instances.
    """

    def __init__(
        self,
        actor_id: str,
        trial_id: str,
        agents: list[BettingAgent],
        max_rounds: int = 1,
    ) -> None:
        """Initialize agent group.

        Args:
            actor_id: Unique identifier for the group
            trial_id: The trial ID for the group
            agents: List of BettingAgent instances to include in the group
            max_rounds: Maximum discussion rounds per event
        """
        super().__init__(actor_id, trial_id)
        self._agents = agents
        self._max_rounds = max_rounds
        self._event_count = 0

    @classmethod
    def from_dict(
        cls,
        config: BettingAgentGroupConfig,
        context: ActorContext,
    ) -> "BettingAgentGroup":
        """Create agent group from configuration.

        Args:
            config: Group configuration with actor_id, config_paths, and max_rounds
            context: Runtime context with trial_id
        """
        actor_id = config["actor_id"]
        config_paths = config.get("config_paths", [])
        max_rounds = config.get("max_rounds", 1)

        agents: list[BettingAgent] = []
        for path in config_paths:
            # Use config file stem as agent actor_id
            agent_actor_id = Path(path).stem
            agent = BettingAgent.from_yaml(
                path, actor_id=agent_actor_id, trial_id=context.trial_id
            )
            agents.append(agent)
            LOGGER.info("[%s] added to group '%s'", agent.name, actor_id)

        return cls(
            actor_id=actor_id,
            trial_id=context.trial_id,
            agents=agents,
            max_rounds=max_rounds,
        )

    @classmethod
    def from_config_paths(
        cls,
        actor_id: str,
        trial_id: str,
        config_paths: Sequence[str | Path],
        max_rounds: int = 1,
    ) -> "BettingAgentGroup":
        """Create agent group from config file paths.

        Convenience constructor for creating groups from YAML config files.

        Args:
            actor_id: Unique identifier for the group
            trial_id: The trial ID for the group
            config_paths: List of paths to agent YAML config files
            max_rounds: Maximum discussion rounds per event
        """
        agents: list[BettingAgent] = []
        for path in config_paths:
            # Use config file stem as agent actor_id
            agent_actor_id = Path(path).stem
            agent = BettingAgent.from_yaml(
                path, actor_id=agent_actor_id, trial_id=trial_id
            )
            agents.append(agent)
            LOGGER.info("[%s] added to group '%s'", agent.name, actor_id)

        return cls(
            actor_id=actor_id,
            trial_id=trial_id,
            agents=agents,
            max_rounds=max_rounds,
        )

    @property
    def _react_agents(self) -> list[ReActAgent]:
        """Return list of internal ReActAgent instances for MsgHub communication."""
        return [agent._react_agent for agent in self._agents]

    async def register_operators(self, operators: Sequence[Operator]) -> None:
        """Register operators for all agents in the group."""
        # Also register in base class registry
        for op in operators:
            self._operator_registry[op.actor_id] = op
        # Register operators with each contained agent
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
        LOGGER.info(
            "agent group '%s' starting with %d agents",
            self.actor_id,
            len(self._agents),
        )
        for agent in self._agents:
            await agent.start()

    async def stop(self) -> None:
        """Stop all agents in the group."""
        LOGGER.info(
            "agent group '%s' stopping after %d events",
            self.actor_id,
            self._event_count,
        )
        for agent in self._agents:
            await agent.stop()

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        """Process stream event with all agents in the group.

        All agents discuss the event using MsgHub. Each agent can see
        other agents' replies but not their internal reasoning.

        Args:
            event: The stream event to process
        """
        await self._handle_stream_event_with_rounds(event, self._max_rounds)

    async def _handle_stream_event_with_rounds(
        self,
        event: StreamEvent[Any],
        max_rounds: int,
    ) -> list[Msg]:
        """Process stream event with configurable discussion rounds.

        Args:
            event: The stream event to process
            max_rounds: Maximum discussion rounds (each agent speaks once per round)

        Returns:
            List of all messages generated during discussion
        """
        LOGGER.info(
            "group '%s' processing event seq=%s from stream '%s'",
            self.actor_id,
            event.sequence,
            event.stream_id,
        )

        self._event_count += 1

        # Track all messages for return
        all_messages: list[Msg] = []

        # Create announcement from event
        announcement = Msg(
            name="system",
            content=f"New market data: {event.payload}",
            role="user",
        )

        # Use MsgHub for agent communication with internal ReActAgent instances
        async with MsgHub(
            participants=self._react_agents,
            announcement=announcement,
        ):
            for round_num in range(max_rounds):
                LOGGER.info("--- Round %d/%d ---", round_num + 1, max_rounds)

                for agent in self._agents:
                    # Call internal ReActAgent - MsgHub broadcasts to others
                    response = await agent._react_agent()
                    if response:
                        all_messages.append(response)
                    agent._event_count += 1

        return all_messages

    async def save_state(self) -> Mapping[str, Any]:
        """Save state of the group and all contained agents."""
        agent_states = {}
        for agent in self._agents:
            agent_states[agent.actor_id] = await agent.save_state()

        return {
            "event_count": self._event_count,
            "agent_states": agent_states,
        }

    async def load_state(self, state: Mapping[str, Any]) -> None:
        """Load state of the group and all contained agents."""
        self._event_count = int(state.get("event_count", 0))
        agent_states = state.get("agent_states", {})

        for agent in self._agents:
            if agent.actor_id in agent_states:
                await agent.load_state(agent_states[agent.actor_id])

        LOGGER.info(
            "agent group '%s' restored: events=%d, agents=%d",
            self.actor_id,
            self._event_count,
            len(self._agents),
        )

    @property
    def event_count(self) -> int:
        """Return total events processed by the group."""
        return self._event_count
