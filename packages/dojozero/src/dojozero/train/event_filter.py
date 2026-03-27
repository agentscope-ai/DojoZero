"""Event filtering and routing for training.

This module handles:
1. Filtering events for agent (exclude odds_update, game_result)
2. Filtering events for broker (initialize game and odds)
3. Compressing high-frequency events (nba_play, nba_game_update)
4. Extracting final odds and game result for reward calculation
"""

from enum import Enum
from typing import Any


class EventFilterMode(Enum):
    """Event filtering modes for compressible events."""

    FULL = "full"  # Keep all events
    SCORING = "scoring"  # Only scoring plays for nba_play
    SAMPLED = "sampled"  # Sample every N events
    SUMMARY = "summary"  # Summarize events (Phase 2)


class EventFilter:
    """Filter and route events for training.

    Event routing:
    - Agent events: All except odds_update and game_result
    - Broker events: game_initialize, odds_update, game_start, nba_game_update
    - Result events: game_result (for reward calculation)
    - Final odds: Last odds_update event (for reward calculation)
    """

    # Events that only go to broker (agent cannot see directly)
    BROKER_ONLY_EVENTS = {"event.odds_update"}

    # Events used only for reward calculation
    RESULT_EVENTS = {"event.game_result"}

    # High-frequency events that can be compressed
    COMPRESSIBLE_EVENTS = {"event.nba_play", "event.nba_game_update"}

    # Events broker needs before agent reasoning (initialization and live state updates).
    # NOTE: game_result is intentionally excluded here and is sent only once at the
    # end of the episode by EpisodeRunner, after agent events are processed.
    BROKER_EVENTS = {
        "event.game_initialize",
        "event.odds_update",
        "event.game_start",
        "event.nba_game_update",
    }

    # Events that are always kept for agent (informational)
    AGENT_ALWAYS_KEEP = {
        "event.game_initialize",
        "event.pregame_stats",
        "event.injury_report",
        "event.expert_prediction",
        "event.game_start",
    }

    def __init__(
        self,
        mode: str | EventFilterMode = EventFilterMode.SCORING,
        sample_rate: int = 5,
    ):
        """Initialize the event filter.

        Args:
            mode: Compression mode for high-frequency events
            sample_rate: For SAMPLED mode, keep every Nth event
        """
        if isinstance(mode, str):
            mode = EventFilterMode(mode)
        self.mode = mode
        self.sample_rate = sample_rate

    def filter_for_agent(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter events to be sent to the agent.

        - Excludes odds_update (agent uses get_event tool instead)
        - Excludes game_result (used only for settlement)
        - Compresses nba_play/nba_game_update based on mode

        Args:
            events: Raw events loaded from JSONL

        Returns:
            Filtered list of events for the agent
        """
        agent_events = []
        play_count = 0
        update_count = 0

        for e in events:
            event_type = e.get("event_type", "")

            # Skip broker-only events
            if event_type in self.BROKER_ONLY_EVENTS:
                continue

            # Skip result events
            if event_type in self.RESULT_EVENTS:
                continue

            # Always keep informational events
            if event_type in self.AGENT_ALWAYS_KEEP:
                agent_events.append(e)
                continue

            # Handle compressible events
            if event_type in self.COMPRESSIBLE_EVENTS:
                if self.mode == EventFilterMode.FULL:
                    agent_events.append(e)
                elif self.mode == EventFilterMode.SCORING:
                    # For nba_play: only keep scoring plays
                    if event_type == "event.nba_play":
                        if e.get("is_scoring_play", False):
                            agent_events.append(e)
                    # For nba_game_update: keep quarter/half end updates
                    elif event_type == "event.nba_game_update":
                        # Keep updates at quarter boundaries
                        clock = e.get("game_clock", "")
                        if clock in ("0:00", "12:00", "end"):
                            agent_events.append(e)
                elif self.mode == EventFilterMode.SAMPLED:
                    if event_type == "event.nba_play":
                        play_count += 1
                        if play_count % self.sample_rate == 0:
                            agent_events.append(e)
                    else:  # nba_game_update
                        update_count += 1
                        if update_count % self.sample_rate == 0:
                            agent_events.append(e)
            else:
                # Unknown event type - keep it
                agent_events.append(e)

        return agent_events

    def filter_for_broker(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter events to be sent to the broker.

        Broker needs:
        - game_initialize: To set up the event
        - odds_update: To update probabilities
        - game_start: To transition event status
        - nba_game_update: To track game progress
        - game_result is excluded here and handled separately at episode end

        Args:
            events: Raw events loaded from JSONL

        Returns:
            Filtered list of events for the broker
        """
        return [e for e in events if e.get("event_type", "") in self.BROKER_EVENTS]

    def extract_final_odds(self, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Extract the final odds_update event for reward calculation.

        Args:
            events: Raw events loaded from JSONL

        Returns:
            The last odds_update event, or None if not found
        """
        final_odds = None
        for e in events:
            if e.get("event_type") == "event.odds_update":
                final_odds = e
        return final_odds

    def extract_game_result(self, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Extract the game_result event for settlement.

        Args:
            events: Raw events loaded from JSONL

        Returns:
            The game_result event, or None if not found
        """
        for e in events:
            if e.get("event_type") == "event.game_result":
                return e
        return None

    def get_event_stats(self, events: list[dict[str, Any]]) -> dict[str, int]:
        """Get statistics about event counts by type.

        Args:
            events: Raw events loaded from JSONL

        Returns:
            Dictionary mapping event types to counts
        """
        stats: dict[str, int] = {}
        for e in events:
            event_type = e.get("event_type", "unknown")
            stats[event_type] = stats.get(event_type, 0) + 1
        return stats
