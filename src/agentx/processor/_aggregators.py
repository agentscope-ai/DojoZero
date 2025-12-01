"""Aggregation logic to convert DataEvents into DataFacts.

This module provides both stateless (batch) and stateful (streaming) aggregators
that derive facts from events. These are used by Data Stores and Data Processors.
"""

from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Generic, Sequence, TypeVar

from agentx.data._events import (
    DataEvent,
    GameStatusEvent,
    OddsChangeEvent,
    PlayByPlayEvent,
    ScoreboardSnapshotEvent,
    TeamStatsEvent,
)
from agentx.data._facts import (
    DataFact,
    GameScoreFact,
    GameStatusFact,
    OddsFact,
    TeamStatsFact,
)

EventT = TypeVar("EventT", bound=DataEvent)
FactT = TypeVar("FactT", bound=DataFact)


class StatelessAggregator(Generic[EventT, FactT]):
    """Stateless aggregator for batch processing.
    
    Processes a batch of events and returns aggregated facts.
    Used in pull/operator scenarios where we query a batch of events.
    """

    def aggregate(self, events: Sequence[EventT]) -> list[FactT]:
        """Aggregate a batch of events into facts.
        
        Args:
            events: List of events to aggregate
            
        Returns:
            List of aggregated facts
        """
        raise NotImplementedError


class StatefulAggregator(Generic[EventT, FactT]):
    """Stateful aggregator for streaming processing.
    
    Maintains running state and updates facts as events arrive.
    Used in streaming scenarios where events arrive incrementally.
    """

    def __init__(self) -> None:
        """Initialize the aggregator state."""
        self._state: dict[str, FactT] = {}

    def update(self, event: EventT) -> FactT | None:
        """Update aggregator state with a new event.
        
        Args:
            event: New event to process
            
        Returns:
            Updated fact if state changed, None otherwise
        """
        raise NotImplementedError

    def get_current(self, key: str) -> FactT | None:
        """Get current fact for a given key.
        
        Args:
            key: Key to look up (e.g., game_id, market_id)
            
        Returns:
            Current fact or None if not found
        """
        return self._state.get(key)

    def get_all(self) -> dict[str, FactT]:
        """Get all current facts.
        
        Returns:
            Dictionary of all current facts keyed by their identifier
        """
        return self._state.copy()


class PlayByPlayToScoreAggregator(StatefulAggregator[DataEvent, GameScoreFact]):
    """Stateful aggregator that maintains current game scores from score-related events.
    
    Handles both:
    - Incremental updates: PlayByPlayEvent (individual plays)
    - Snapshot updates: ScoreboardSnapshotEvent (full scoreboard state)
    
    This is used by Data Stores to maintain current GameScoreFact.
    """

    def __init__(self) -> None:
        """Initialize aggregator with empty state."""
        super().__init__()
        # Track game metadata (team IDs, etc.) - in real implementation, 
        # this would come from game metadata store
        self._game_metadata: dict[str, dict[str, str]] = {}

    def update(self, event: DataEvent) -> GameScoreFact | None:
        """Update score fact from a score-related event.
        
        Handles both incremental (PlayByPlayEvent) and snapshot (ScoreboardSnapshotEvent) updates.
        
        Args:
            event: Score-related event (PlayByPlayEvent or ScoreboardSnapshotEvent)
            
        Returns:
            Updated GameScoreFact
        """
        # Handle snapshot update (full state refresh)
        if isinstance(event, ScoreboardSnapshotEvent):
            fact = GameScoreFact(
                game_id=event.game_id,
                home_team_id=event.home_team_id,
                away_team_id=event.away_team_id,
                home_score=event.home_score,
                away_score=event.away_score,
                period=event.period,
                period_time=event.period_time,
                game_status=event.game_status,
                timestamp=event.timestamp,
            )
            # Update metadata cache
            self._game_metadata[event.game_id] = {
                "home_team_id": event.home_team_id,
                "away_team_id": event.away_team_id,
            }
            self._state[event.game_id] = fact
            return fact
        
        # Handle incremental update (PlayByPlayEvent)
        if isinstance(event, PlayByPlayEvent):
            # Get or initialize game metadata
            if event.game_id not in self._game_metadata:
                # In real implementation, this would query game metadata
                # For now, we'll use placeholder values
                self._game_metadata[event.game_id] = {
                    "home_team_id": "HOME",  # Would come from game metadata
                    "away_team_id": "AWAY",
                }
            
            metadata = self._game_metadata[event.game_id]
            
            # Create/update score fact from event
            fact = GameScoreFact(
                game_id=event.game_id,
                home_team_id=metadata["home_team_id"],
                away_team_id=metadata["away_team_id"],
                home_score=event.home_score,
                away_score=event.away_score,
                period=event.period,
                period_time=event.period_time,
                game_status="live",  # Would be determined from GameStatusEvent
                timestamp=event.timestamp,
            )
            
            # Update state
            self._state[event.game_id] = fact
            return fact
        
        # Unknown event type
        return None

    def batch_aggregate(self, events: Sequence[DataEvent]) -> GameScoreFact | None:
        """Stateless batch aggregation: process all events and return final score.
        
        Handles both incremental and snapshot events. If snapshot events are present,
        uses the latest snapshot; otherwise processes incremental events.
        
        Args:
            events: Sequence of score-related events (should be ordered by timestamp)
            
        Returns:
            Final GameScoreFact after processing all events, or None if empty
        """
        if not events:
            return None
        
        # Check for snapshot events (they represent full state)
        snapshot_events = [e for e in events if isinstance(e, ScoreboardSnapshotEvent)]
        if snapshot_events:
            # Use the latest snapshot (most recent full state)
            latest_snapshot = snapshot_events[-1]
            return GameScoreFact(
                game_id=latest_snapshot.game_id,
                home_team_id=latest_snapshot.home_team_id,
                away_team_id=latest_snapshot.away_team_id,
                home_score=latest_snapshot.home_score,
                away_score=latest_snapshot.away_score,
                period=latest_snapshot.period,
                period_time=latest_snapshot.period_time,
                game_status=latest_snapshot.game_status,
                timestamp=latest_snapshot.timestamp,
            )
        
        # Process incremental events (PlayByPlayEvent)
        incremental_events = [e for e in events if isinstance(e, PlayByPlayEvent)]
        if not incremental_events:
            return None
        
        # Use the latest incremental event to get current state
        latest_event = incremental_events[-1]
        
        # Get game metadata (would come from game metadata store)
        game_id = latest_event.game_id
        if game_id not in self._game_metadata:
            self._game_metadata[game_id] = {
                "home_team_id": "HOME",
                "away_team_id": "AWAY",
            }
        
        metadata = self._game_metadata[game_id]
        
        return GameScoreFact(
            game_id=game_id,
            home_team_id=metadata["home_team_id"],
            away_team_id=metadata["away_team_id"],
            home_score=latest_event.home_score,
            away_score=latest_event.away_score,
            period=latest_event.period,
            period_time=latest_event.period_time,
            game_status="live",
            timestamp=latest_event.timestamp,
        )


class OddsChangeToOddsAggregator(StatefulAggregator[OddsChangeEvent, OddsFact]):
    """Stateful aggregator that maintains current odds from odds change events.
    
    This is used by Data Stores to maintain current OddsFact from OddsChangeEvents.
    """

    def update(self, event: OddsChangeEvent) -> OddsFact | None:
        """Update odds fact from an odds change event.
        
        Args:
            event: Odds change event
            
        Returns:
            Updated OddsFact
        """
        # Create fact from latest event
        fact = OddsFact(
            market_id=event.market_id,
            market_question=event.market_question,
            game_id=event.game_id,
            outcome=event.outcome,
            current_odds=event.current_odds,
            volume_24h=event.volume_24h or 0.0,
            liquidity=event.liquidity or 0.0,
            timestamp=event.timestamp,
        )
        
        # Key by market_id + outcome for unique identification
        key = f"{event.market_id}:{event.outcome}"
        self._state[key] = fact
        return fact

    def batch_aggregate(self, events: list[OddsChangeEvent]) -> dict[str, OddsFact]:
        """Stateless batch aggregation: process all events and return final odds.
        
        Args:
            events: List of odds change events (should be ordered by timestamp)
            
        Returns:
            Dictionary of final OddsFacts keyed by market_id:outcome
        """
        result: dict[str, OddsFact] = {}
        
        # Process events in order, keeping latest for each market+outcome
        for event in events:
            key = f"{event.market_id}:{event.outcome}"
            result[key] = OddsFact(
                market_id=event.market_id,
                market_question=event.market_question,
                game_id=event.game_id,
                outcome=event.outcome,
                current_odds=event.current_odds,
                volume_24h=event.volume_24h or 0.0,
                liquidity=event.liquidity or 0.0,
                timestamp=event.timestamp,
            )
        
        return result


class GameStatusEventToFactAggregator(StatefulAggregator[GameStatusEvent, GameStatusFact]):
    """Stateful aggregator that maintains current game status from status events."""

    def update(self, event: GameStatusEvent) -> GameStatusFact | None:
        """Update game status fact from a status event.
        
        Args:
            event: Game status event
            
        Returns:
            Updated GameStatusFact
        """
        fact = GameStatusFact(
            game_id=event.game_id,
            status=event.status,
            scheduled_start=event.scheduled_start,
            actual_start=event.actual_start,
            ended_at=event.ended_at,
            home_score=event.home_score,
            away_score=event.away_score,
            venue=event.venue,
            timestamp=event.timestamp,
        )
        
        self._state[event.game_id] = fact
        return fact


# Factory functions for common aggregators

def create_score_aggregator() -> PlayByPlayToScoreAggregator:
    """Create a score aggregator for maintaining game scores."""
    return PlayByPlayToScoreAggregator()


def create_odds_aggregator() -> OddsChangeToOddsAggregator:
    """Create an odds aggregator for maintaining current odds."""
    return OddsChangeToOddsAggregator()


def create_game_status_aggregator() -> GameStatusEventToFactAggregator:
    """Create a game status aggregator."""
    return GameStatusEventToFactAggregator()


# Registry of aggregators by event type
AGGREGATOR_REGISTRY: dict[str, Callable[[], Any]] = {
    "play_by_play": create_score_aggregator,
    "odds_change": create_odds_aggregator,
    "game_status": create_game_status_aggregator,
}


def get_aggregator(event_type: str) -> StatefulAggregator[DataEvent, DataFact] | None:
    """Get an aggregator for a given event type.
    
    Args:
        event_type: Type of event (e.g., "play_by_play", "odds_change")
        
    Returns:
        Aggregator instance or None if not found
    """
    factory = AGGREGATOR_REGISTRY.get(event_type)
    if factory:
        return factory()
    return None


# Stateless Aggregator Implementations

class StatelessScoreAggregator(StatelessAggregator[DataEvent, GameScoreFact]):
    """Stateless aggregator that processes batches of score-related events.
    
    Used in pull/operator scenarios where you query a batch of events
    and need the final aggregated score without maintaining state.
    
    Example:
        # Operator queries all play-by-play events for a game
        events = store.query_events("play_by_play", game_id="game_123")
        aggregator = StatelessScoreAggregator()
        facts = aggregator.aggregate(events)  # Returns list of GameScoreFact
    """
    
    def aggregate(self, events: Sequence[DataEvent]) -> list[GameScoreFact]:
        """Aggregate a batch of score-related events into facts.
        
        Groups events by game_id and returns the latest fact for each game.
        Handles both incremental (PlayByPlayEvent) and snapshot (ScoreboardSnapshotEvent) events.
        
        Args:
            events: Sequence of score-related events
            
        Returns:
            List of GameScoreFact (one per game_id)
        """
        # Group events by game_id
        game_events: dict[str, list[DataEvent]] = {}
        for event in events:
            if event.game_id:
                game_events.setdefault(event.game_id, []).append(event)
        
        facts: list[GameScoreFact] = []
        
        for game_id, game_event_list in game_events.items():
            # Sort by timestamp
            sorted_events = sorted(game_event_list, key=lambda e: e.timestamp)
            
            # Check for snapshot events (they represent full state)
            snapshot_events = [e for e in sorted_events if isinstance(e, ScoreboardSnapshotEvent)]
            if snapshot_events:
                # Use the latest snapshot
                latest_snapshot = snapshot_events[-1]
                facts.append(GameScoreFact(
                    game_id=latest_snapshot.game_id,
                    home_team_id=latest_snapshot.home_team_id,
                    away_team_id=latest_snapshot.away_team_id,
                    home_score=latest_snapshot.home_score,
                    away_score=latest_snapshot.away_score,
                    period=latest_snapshot.period,
                    period_time=latest_snapshot.period_time,
                    game_status=latest_snapshot.game_status,
                    timestamp=latest_snapshot.timestamp,
                ))
                continue
            
            # Process incremental events (PlayByPlayEvent)
            incremental_events = [e for e in sorted_events if isinstance(e, PlayByPlayEvent)]
            if incremental_events:
                latest_event = incremental_events[-1]
                # In real implementation, team IDs would come from game metadata
                facts.append(GameScoreFact(
                    game_id=latest_event.game_id,
                    home_team_id="HOME",  # Would come from game metadata
                    away_team_id="AWAY",
                    home_score=latest_event.home_score,
                    away_score=latest_event.away_score,
                    period=latest_event.period,
                    period_time=latest_event.period_time,
                    game_status="live",
                    timestamp=latest_event.timestamp,
                ))
        
        return facts


class StatelessOddsAggregator(StatelessAggregator[OddsChangeEvent, OddsFact]):
    """Stateless aggregator that processes batches of odds change events.
    
    Used in pull/operator scenarios where you query a batch of odds events
    and need the current odds for each market without maintaining state.
    
    Example:
        # Operator queries all odds changes for a market
        events = store.query_events("odds_change", market_id="market_456")
        aggregator = StatelessOddsAggregator()
        facts = aggregator.aggregate(events)  # Returns list of OddsFact
    """
    
    def aggregate(self, events: Sequence[OddsChangeEvent]) -> list[OddsFact]:
        """Aggregate a batch of odds change events into facts.
        
        Groups events by market_id + outcome and returns the latest fact for each.
        
        Args:
            events: Sequence of odds change events
            
        Returns:
            List of OddsFact (one per market_id:outcome combination)
        """
        # Group events by market_id:outcome
        market_events: dict[str, list[OddsChangeEvent]] = {}
        for event in events:
            key = f"{event.market_id}:{event.outcome}"
            market_events.setdefault(key, []).append(event)
        
        facts: list[OddsFact] = []
        
        for key, event_list in market_events.items():
            # Sort by timestamp and use the latest event
            sorted_events = sorted(event_list, key=lambda e: e.timestamp)
            latest_event = sorted_events[-1]
            
            facts.append(OddsFact(
                market_id=latest_event.market_id,
                market_question=latest_event.market_question,
                game_id=latest_event.game_id,
                outcome=latest_event.outcome,
                current_odds=latest_event.current_odds,
                volume_24h=latest_event.volume_24h or 0.0,
                liquidity=latest_event.liquidity or 0.0,
                timestamp=latest_event.timestamp,
            ))
        
        return facts


class StatelessTeamStatsAggregator(StatelessAggregator[TeamStatsEvent, TeamStatsFact]):
    """Stateless aggregator that processes batches of team stats events.
    
    Used in pull/operator scenarios where you query a batch of team stats events
    and need the current stats without maintaining state.
    
    Example:
        # Operator queries all team stats for a game
        events = store.query_events("team_stats", game_id="game_123", team_id="LAL")
        aggregator = StatelessTeamStatsAggregator()
        facts = aggregator.aggregate(events)  # Returns list of TeamStatsFact
    """
    
    def aggregate(self, events: Sequence[TeamStatsEvent]) -> list[TeamStatsFact]:
        """Aggregate a batch of team stats events into facts.
        
        Groups events by team_id + game_id + period and returns the latest fact for each.
        
        Args:
            events: Sequence of team stats events
            
        Returns:
            List of TeamStatsFact (one per team_id:game_id:period combination)
        """
        # Group events by team_id:game_id:period
        stats_events: dict[str, list[TeamStatsEvent]] = {}
        for event in events:
            period_key = str(event.period) if event.period else "all"
            key = f"{event.team_id}:{event.game_id}:{period_key}"
            stats_events.setdefault(key, []).append(event)
        
        facts: list[TeamStatsFact] = []
        
        for key, event_list in stats_events.items():
            # Sort by timestamp and use the latest event
            sorted_events = sorted(event_list, key=lambda e: e.timestamp)
            latest_event = sorted_events[-1]
            
            facts.append(TeamStatsFact(
                team_id=latest_event.team_id,
                game_id=latest_event.game_id,
                period=latest_event.period,
                points=latest_event.points,
                rebounds=latest_event.rebounds,
                assists=latest_event.assists,
                steals=latest_event.steals,
                blocks=latest_event.blocks,
                turnovers=latest_event.turnovers,
                fouls=latest_event.fouls,
                fg_percentage=latest_event.fg_percentage,
                three_pt_percentage=latest_event.three_pt_percentage,
                ft_percentage=latest_event.ft_percentage,
                pace=latest_event.pace,
                offensive_rating=latest_event.offensive_rating,
                defensive_rating=latest_event.defensive_rating,
                timestamp=latest_event.timestamp,
            ))
        
        return facts


# Factory functions for stateless aggregators

def create_stateless_score_aggregator() -> StatelessScoreAggregator:
    """Create a stateless score aggregator for batch processing."""
    return StatelessScoreAggregator()


def create_stateless_odds_aggregator() -> StatelessOddsAggregator:
    """Create a stateless odds aggregator for batch processing."""
    return StatelessOddsAggregator()


def create_stateless_team_stats_aggregator() -> StatelessTeamStatsAggregator:
    """Create a stateless team stats aggregator for batch processing."""
    return StatelessTeamStatsAggregator()

