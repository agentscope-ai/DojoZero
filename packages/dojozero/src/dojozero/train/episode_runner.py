"""Episode runner for training.

This module runs a single training episode using DojoZero's existing
BettingAgent and BrokerOperator, replacing the LLM model with AgentJet's API.
"""

import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from dojozero.betting import BettingAgent, BrokerOperator
from dojozero.betting._models import Statistics
from dojozero.core import StreamEvent
from dojozero.data import deserialize_data_event
from dojozero.nba._formatters import format_event

from packages.dojozero.src.dojozero.train.model_adapter import create_agentjet_model, create_agentjet_formatter
from packages.dojozero.src.dojozero.train.event_filter import EventFilter, EventFilterMode
from packages.dojozero.src.dojozero.train.reward import calculate_reward

logger = logging.getLogger(__name__)

# Default persona path
DEFAULT_PERSONA_PATH = Path(__file__).parent.parent.parent.parent / "agents" / "personas" / "basic.yaml"


def _load_default_prompt() -> str:
    """Load the default system prompt from basic.yaml."""
    if DEFAULT_PERSONA_PATH.exists():
        with open(DEFAULT_PERSONA_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data.get("sys_prompt", "")
    return "You are a sports betting agent."


def _dict_to_stream_event(event_dict: dict[str, Any], stream_id: str = "training") -> StreamEvent[Any]:
    """Convert an event dictionary to a StreamEvent.

    Args:
        event_dict: Raw event dictionary from JSONL
        stream_id: Stream ID for the event

    Returns:
        StreamEvent wrapping the deserialized DataEvent
    """
    data_event = deserialize_data_event(event_dict)
    if data_event is None:
        raise ValueError(f"Failed to deserialize event: {event_dict.get('event_type')}")
    return StreamEvent(stream_id=stream_id, payload=data_event)


def _sanitize_decimals(obj: Any) -> Any:
    """Recursively convert Decimal values to float in nested dicts/lists."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            obj[k] = _sanitize_decimals(v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            obj[i] = _sanitize_decimals(v)
    elif isinstance(obj, Decimal):
        return float(obj)
    return obj


class EpisodeRunner:
    """Run a single training episode.

    Reuses DojoZero's BettingAgent and BrokerOperator while replacing
    the LLM model with AgentJet's OpenAI-compatible API.

    Flow:
    1. Load events from JSONL file
    2. Filter events for agent and broker
    3. Create BrokerOperator and BettingAgent with AgentJet model
    4. Register broker with agent (injects tools)
    5. Send broker events (initialize game and odds)
    6. Send agent events (game flow)
    7. Send game result to broker (settlement)
    8. Calculate reward from broker statistics
    """

    def __init__(
        self,
        game_file: str,
        base_url: str,
        api_key: str,
        initial_balance: str = "1000.00",
        event_filter_mode: str | EventFilterMode = EventFilterMode.SCORING,
        event_sample_rate: int = 5,
        sys_prompt: str | None = None,
        model_name: str = "agentjet-model",
    ):
        """Initialize the episode runner.

        Args:
            game_file: Path to JSONL file with game events
            base_url: AgentJet API base URL
            api_key: AgentJet API key
            initial_balance: Initial betting balance
            event_filter_mode: How to filter/compress events
            event_sample_rate: For "sampled" mode, keep every Nth event
            sys_prompt: System prompt for agent (uses default if None)
            model_name: Model name for API calls
        """
        self.game_file = game_file
        self.base_url = base_url
        self.api_key = api_key
        self.initial_balance = initial_balance
        self.model_name = model_name

        # Event filtering
        if isinstance(event_filter_mode, str):
            event_filter_mode = EventFilterMode(event_filter_mode)
        self.event_filter = EventFilter(mode=event_filter_mode, sample_rate=event_sample_rate)

        # System prompt
        self.sys_prompt = sys_prompt or _load_default_prompt()

        # Will be set during run()
        self.broker: BrokerOperator | None = None
        self.agent: BettingAgent | None = None

    def _load_events(self) -> list[dict[str, Any]]:
        """Load all events from the game file."""
        events = []
        with open(self.game_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def _create_broker(self) -> BrokerOperator:
        """Create and configure the BrokerOperator."""
        config = {
            "actor_id": "broker",
            "initial_balance": self.initial_balance,
        }
        return BrokerOperator(config=config, trial_id="training")

    def _create_agent(self) -> BettingAgent:
        """Create BettingAgent with AgentJet model."""
        model = create_agentjet_model(
            base_url=self.base_url,
            api_key=self.api_key,
            model_name=self.model_name,
        )
        formatter = create_agentjet_formatter()

        return BettingAgent(
            actor_id="betting_agent",
            trial_id="training",
            name="BettingAgent",
            sys_prompt=self.sys_prompt,
            model=model,
            formatter=formatter,
            event_formatter=format_event,
        )

    async def run(self) -> tuple[float, dict[str, Any]]:
        """Run the episode and return reward and metadata.

        Returns:
            Tuple of (reward, metadata)
            - reward: ROI-based reward from betting outcomes
            - metadata: Dictionary with stats, game info, etc.
        """
        # Load and filter events
        all_events = self._load_events()
        agent_events = self.event_filter.filter_for_agent(all_events)
        broker_events = self.event_filter.filter_for_broker(all_events)
        # Safety guard: settlement must happen only once at the end.
        broker_events = [
            e for e in broker_events if e.get("event_type") != "event.game_result"
        ]
        final_odds = self.event_filter.extract_final_odds(all_events)
        game_result = self.event_filter.extract_game_result(all_events)
        agent_event_ids = {id(e) for e in agent_events}
        broker_event_ids = {id(e) for e in broker_events}
        result_event_ids = {id(game_result)} if game_result is not None else set()

        logger.info(
            "Episode starting: %s - %d total events, %d agent events, %d broker events",
            self.game_file,
            len(all_events),
            len(agent_events),
            len(broker_events),
        )

        # Create components
        self.broker = self._create_broker()
        self.agent = self._create_agent()

        # Register agent with broker first (creates trading account state)
        await self.broker.register_agents([self.agent])

        # Register broker with agent (injects tools)
        await self.agent.register_operators([self.broker])

        # Start components
        await self.broker.start()
        await self.agent.start()

        try:
            # Replay events in original order so broker and agent stay time-consistent.
            # This mirrors a real stream where both consumers observe the same timeline.
            for event_dict in all_events:
                should_send_to_broker = (
                    id(event_dict) in broker_event_ids or id(event_dict) in result_event_ids
                )
                should_send_to_agent = id(event_dict) in agent_event_ids

                if should_send_to_broker:
                    try:
                        broker_event = _dict_to_stream_event(
                            event_dict, stream_id="broker_feed"
                        )
                        await self.broker.handle_stream_event(broker_event)
                    except Exception as e:
                        logger.warning("Failed to send broker event: %s", e)

                if should_send_to_agent:
                    try:
                        agent_event = _dict_to_stream_event(
                            event_dict, stream_id="agent_feed"
                        )
                        await self.agent.handle_stream_event(agent_event)
                    except Exception as e:
                        logger.warning("Failed to send agent event: %s", e)

            # Get statistics and calculate reward
            stats = await self.broker.get_statistics(self.agent.actor_id)
            reward = calculate_reward(
                stats=stats,
                final_odds=final_odds,
                game_result=game_result,
            )

            # Prepare metadata (convert Decimal → float for JSON serialization)
            stats_dict = stats.model_dump()
            _sanitize_decimals(stats_dict)
            metadata = {
                "game_file": self.game_file,
                "stats": stats_dict,
                "total_events": len(all_events),
                "agent_events": len(agent_events),
                "broker_events": len(broker_events),
                "event_filter_mode": self.event_filter.mode.value,
            }

            if game_result:
                metadata["winner"] = game_result.get("winner", "")
                metadata["home_score"] = game_result.get("home_score", 0)
                metadata["away_score"] = game_result.get("away_score", 0)

            logger.info(
                "Episode completed: reward=%.4f, total_bets=%d, roi=%.2f%%",
                reward,
                stats.total_bets,
                stats.roi,
            )

            return reward, metadata

        finally:
            # Clean up
            await self.agent.stop()
            await self.broker.stop()


async def run_episode(
    game_file: str,
    base_url: str,
    api_key: str,
    event_filter_mode: str = "scoring",
    event_sample_rate: int = 5,
    initial_balance: str = "1000.00",
) -> tuple[float, dict[str, Any]]:
    """Convenience function to run a single episode.

    Args:
        game_file: Path to JSONL file with game events
        base_url: AgentJet API base URL
        api_key: AgentJet API key
        event_filter_mode: How to filter/compress events
        event_sample_rate: For "sampled" mode, keep every Nth event
        initial_balance: Initial betting balance

    Returns:
        Tuple of (reward, metadata)
    """
    runner = EpisodeRunner(
        game_file=game_file,
        base_url=base_url,
        api_key=api_key,
        event_filter_mode=event_filter_mode,
        event_sample_rate=event_sample_rate,
        initial_balance=initial_balance,
    )
    return await runner.run()
