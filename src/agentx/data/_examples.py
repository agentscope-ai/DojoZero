"""Examples demonstrating DataFact vs DataEvent usage and differences.

This module shows:
1. How DataFacts and DataEvents differ
2. When to use each
3. How to convert between them

Key Relationship:
- Fact = Snapshot (current state at a point in time)
- Event = Update (change/delta that contributes to building the snapshot)
- Multiple Events → Aggregate → Fact
- Fact = Initial State + Sum of All Events
"""

from datetime import datetime, timezone

from agentx.data import (
    GameScoreFact,
    OddsChangeEvent,
    OddsFact,
    PlayByPlayEvent,
    ScoreboardSnapshotEvent,
    TeamStatsEvent,
)
from agentx.processor import (
    StatelessOddsAggregator,
    StatelessScoreAggregator,
    create_score_aggregator,
    create_stateless_odds_aggregator,
    create_stateless_score_aggregator,
)


def explain_fact_event_relationship() -> None:
    """Explain the fundamental relationship between Facts and Events."""
    print("=" * 70)
    print("FACT vs EVENT RELATIONSHIP")
    print("=" * 70)
    print()
    print("FACT = SNAPSHOT (Current State)")
    print("  - Represents 'what is' at a point in time")
    print("  - Example: 'Current score is 45-42'")
    print("  - Used for: Pull queries, current state lookups")
    print()
    print("EVENT = UPDATE (Change/Delta)")
    print("  - Represents 'what changed'")
    print("  - Example: 'Score changed: +3 points (42 → 45)'")
    print("  - Used for: Push streams, change tracking")
    print()
    print("RELATIONSHIP:")
    print("  Events are the building blocks that contribute to Facts")
    print("  Fact = Initial State + Sum of All Events")
    print()
    print("  Example:")
    print("    Event 1: +2 points → Score: 2-0")
    print("    Event 2: +3 points → Score: 2-3")
    print("    Event 3: +2 points → Score: 4-3")
    print("    ...")
    print("    Fact (snapshot): Current Score = 45-42")
    print()
    print("  The aggregator maintains the Fact by processing Events:")
    print("    - Each Event updates the Fact")
    print("    - Fact represents the current state after all Events")
    print()
    print("=" * 70)
    print()


def example_fact_vs_event() -> None:
    """Demonstrate the key differences between Facts and Events."""
    
    # ===== DATA FACT (Pull-based, Current State) =====
    # Facts represent "what is" - current state snapshots
    current_score = GameScoreFact(
        game_id="game_123",
        home_team_id="LAL",
        away_team_id="BOS",
        home_score=45,
        away_score=42,
        period=2,
        period_time="10:23",
        game_status="live",
        timestamp=datetime.now(timezone.utc),
    )
    
    print("=== DataFact (Current State) ===")
    print(f"Fact Type: {current_score.fact_type}")
    print(f"Current Score: {current_score.home_score}-{current_score.away_score}")
    print(f"Access Pattern: Pull/Query")
    print(f"Use Case: 'What is the current score?'")
    print()
    
    # ===== DATA EVENT (Push-based, Change) =====
    # Events represent "what changed" - deltas/changes
    score_change = PlayByPlayEvent(
        game_id="game_123",
        period=2,
        period_time="10:20",
        game_time=datetime.now(timezone.utc),
        play_type="shot",
        team_id="LAL",
        player_id="lebron_23",
        points=3,  # 3-point shot made
        home_score=45,  # Score AFTER this play
        away_score=42,
        description="LeBron James makes 3-pointer",
        shot_type="3PT Field Goal",
        shot_distance=25.0,
        is_made=True,
        timestamp=datetime.now(timezone.utc),
    )
    
    print("=== DataEvent (Change/Delta) ===")
    print(f"Event Type: {score_change.event_type}")
    print(f"Change: +{score_change.points} points")
    print(f"Score After: {score_change.home_score}-{score_change.away_score}")
    print(f"Change Magnitude: {score_change.get_change_magnitude()}")
    print(f"Access Pattern: Push/Stream")
    print(f"Use Case: 'Score changed by X points'")
    print()
    
    # ===== KEY DIFFERENCES =====
    print("=== Key Differences ===")
    print("DataFact:")
    print("  - Represents current state")
    print("  - No 'before' state")
    print("  - Optimized for queries")
    print("  - Synchronous/blocking")
    print("  - Cached/materialized")
    print()
    print("DataEvent:")
    print("  - Represents change/delta")
    print("  - Includes change metadata")
    print("  - Optimized for streaming")
    print("  - Asynchronous/push")
    print("  - Source of truth")


def example_odds_fact_vs_event() -> None:
    """Show how OddsFact and OddsChangeEvent differ."""
    
    # Fact: Current state
    current_odds = OddsFact(
        market_id="market_456",
        market_question="Will Lakers win?",
        game_id="game_123",
        outcome="Yes",
        current_odds=1.85,
        volume_24h=100000.0,
        liquidity=50000.0,
        timestamp=datetime.now(timezone.utc),
    )
    
    # Event: Change
    odds_change = OddsChangeEvent(
        market_id="market_456",
        market_question="Will Lakers win?",
        game_id="game_123",
        outcome="Yes",
        previous_odds=1.80,  # Before
        current_odds=1.85,    # After
        odds_change=0.05,      # Delta
        odds_change_percent=2.78,  # Percentage change
        volume_24h=100000.0,
        liquidity=50000.0,
        timestamp=datetime.now(timezone.utc),
    )
    
    print("=== OddsFact vs OddsChangeEvent ===")
    print(f"Fact (Current): {current_odds.current_odds}")
    print(f"Event (Change): {odds_change.previous_odds} -> {odds_change.current_odds}")
    print(f"Change Magnitude: {odds_change.get_change_magnitude()}%")
    print()
    print("Fact: 'What are the current odds?' (pull query)")
    print("Event: 'Odds changed from X to Y' (push stream)")


def example_deriving_fact_from_events() -> None:
    """Show how facts can be derived from events."""
    
    # Simulate receiving play-by-play events
    events = [
        PlayByPlayEvent(
            game_id="game_123",
            period=1,
            period_time="12:00",
            game_time=datetime.now(timezone.utc),
            play_type="shot",
            team_id="LAL",
            player_id="lebron_23",
            points=2,
            home_score=2,
            away_score=0,
            description="LeBron makes 2-pointer",
            timestamp=datetime.now(timezone.utc),
        ),
        PlayByPlayEvent(
            game_id="game_123",
            period=1,
            period_time="11:30",
            game_time=datetime.now(timezone.utc),
            play_type="shot",
            team_id="BOS",
            player_id="tatum_0",
            points=3,
            home_score=2,
            away_score=3,
            description="Tatum makes 3-pointer",
            timestamp=datetime.now(timezone.utc),
        ),
    ]
    
    # Derive current score fact from latest event
    latest_event = events[-1]
    derived_fact = GameScoreFact(
        game_id=latest_event.game_id,
        home_team_id="LAL",
        away_team_id="BOS",
        home_score=latest_event.home_score,
        away_score=latest_event.away_score,
        period=latest_event.period,
        period_time=latest_event.period_time,
        game_status="live",
        timestamp=latest_event.timestamp,
    )
    
    print("=== Deriving Fact from Events ===")
    print(f"Received {len(events)} events")
    print(f"Derived Fact: {derived_fact.home_score}-{derived_fact.away_score}")
    print("This is how Data Stores maintain facts from events!")


def example_stateful_aggregation() -> None:
    """Show how stateful aggregators maintain facts from streaming events."""
    from agentx.data import (
        OddsChangeEvent,
        PlayByPlayEvent,
    )
    from agentx.processor import (
        create_odds_aggregator,
        create_score_aggregator,
    )
    
    # ===== Stateful Aggregation (Streaming) =====
    # Used by Data Stores to maintain current facts as events arrive
    
    print("=== Stateful Aggregation (Streaming) ===")
    
    # Score aggregator maintains current score
    score_agg = create_score_aggregator()
    
    events = [
        PlayByPlayEvent(
            game_id="game_123",
            period=1,
            period_time="12:00",
            game_time=datetime.now(timezone.utc),
            play_type="shot",
            team_id="LAL",
            player_id="lebron_23",
            points=2,
            home_score=2,
            away_score=0,
            description="LeBron makes 2-pointer",
            timestamp=datetime.now(timezone.utc),
        ),
        PlayByPlayEvent(
            game_id="game_123",
            period=1,
            period_time="11:30",
            game_time=datetime.now(timezone.utc),
            play_type="shot",
            team_id="BOS",
            player_id="tatum_0",
            points=3,
            home_score=2,
            away_score=3,
            description="Tatum makes 3-pointer",
            timestamp=datetime.now(timezone.utc),
        ),
    ]
    
    # Process events one by one (as they arrive in stream)
    for event in events:
        fact = score_agg.update(event)
        if fact:
            print(f"Event: {event.description}")
            print(f"Updated Fact: {fact.home_score}-{fact.away_score}")
            print()
    
    # Get current fact
    current_fact = score_agg.get_current("game_123")
    if current_fact:
        print(f"Current Score Fact: {current_fact.home_score}-{current_fact.away_score}")
        print()
    
    # ===== Odds Aggregation =====
    odds_agg = create_odds_aggregator()
    
    odds_events = [
        OddsChangeEvent(
            market_id="market_456",
            market_question="Will Lakers win?",
            game_id="game_123",
            outcome="Yes",
            previous_odds=1.80,
            current_odds=1.85,
            odds_change=0.05,
            odds_change_percent=2.78,
            timestamp=datetime.now(timezone.utc),
        ),
        OddsChangeEvent(
            market_id="market_456",
            market_question="Will Lakers win?",
            game_id="game_123",
            outcome="Yes",
            previous_odds=1.85,
            current_odds=1.92,
            odds_change=0.07,
            odds_change_percent=3.78,
            timestamp=datetime.now(timezone.utc),
        ),
    ]
    
    for event in odds_events:
        fact = odds_agg.update(event)
        if fact:
            key = f"{event.market_id}:{event.outcome}"
            print(f"Odds changed: {event.previous_odds} -> {event.current_odds}")
            print(f"Current Fact: {fact.current_odds}")
            print()


def example_stateless_aggregation() -> None:
    """Show how stateless aggregators process batches of events."""
    from agentx.data import OddsChangeEvent, PlayByPlayEvent
    from agentx.processor import create_odds_aggregator, create_score_aggregator
    
    print("=== Stateless Aggregation (Batch) ===")
    
    # Batch of events (e.g., from a query)
    events = [
        PlayByPlayEvent(
            game_id="game_123",
            period=1,
            period_time="12:00",
            game_time=datetime.now(timezone.utc),
            play_type="shot",
            team_id="LAL",
            player_id="lebron_23",
            points=2,
            home_score=2,
            away_score=0,
            description="LeBron makes 2-pointer",
            timestamp=datetime.now(timezone.utc),
        ),
        PlayByPlayEvent(
            game_id="game_123",
            period=1,
            period_time="11:30",
            game_time=datetime.now(timezone.utc),
            play_type="shot",
            team_id="BOS",
            player_id="tatum_0",
            points=3,
            home_score=2,
            away_score=3,
            description="Tatum makes 3-pointer",
            timestamp=datetime.now(timezone.utc),
        ),
    ]
    
    # Process batch and get final fact
    score_agg = create_score_aggregator()
    final_fact = score_agg.batch_aggregate(events)
    
    if final_fact:
        print(f"Processed {len(events)} events")
        print(f"Final Score Fact: {final_fact.home_score}-{final_fact.away_score}")
        print("This is used in pull/operator scenarios for batch queries")


def example_incremental_vs_snapshot() -> None:
    """Demonstrate the difference between incremental and snapshot updates."""
    print("=== Incremental vs Snapshot Updates ===")
    print()
    
    # ===== Incremental Update (Delta) =====
    print("INCREMENTAL UPDATE (Delta):")
    print("  - Represents a single change")
    print("  - Example: Individual play-by-play event")
    print("  - Used for: Real-time streaming of individual plays")
    print()
    
    incremental_event = PlayByPlayEvent(
        game_id="game_123",
        period=2,
        period_time="10:20",
        game_time=datetime.now(timezone.utc),
        play_type="shot",
        team_id="LAL",
        player_id="lebron_23",
        points=3,  # This play scored 3 points
        home_score=45,  # Score AFTER this play
        away_score=42,
        description="LeBron makes 3-pointer",
        timestamp=datetime.now(timezone.utc),
    )
    
    print(f"  Event Type: {incremental_event.event_type}")
    print(f"  Update Type: {incremental_event.update_type}")
    print(f"  Change: +{incremental_event.points} points")
    print(f"  Score After: {incremental_event.home_score}-{incremental_event.away_score}")
    print()
    
    # ===== Snapshot Update (Full State) =====
    print("SNAPSHOT UPDATE (Full State):")
    print("  - Represents complete current state")
    print("  - Example: Full scoreboard from API pull")
    print("  - Used for: Periodic refreshes, initial state, catch-up")
    print()
    
    snapshot_event = ScoreboardSnapshotEvent(
        game_id="game_123",
        home_team_id="LAL",
        away_team_id="BOS",
        home_score=45,  # Current score (full state)
        away_score=42,
        period=2,
        period_time="10:20",
        game_status="live",
        timestamp=datetime.now(timezone.utc),
    )
    
    print(f"  Event Type: {snapshot_event.event_type}")
    print(f"  Update Type: {snapshot_event.update_type}")
    print(f"  Current Score: {snapshot_event.home_score}-{snapshot_event.away_score}")
    print(f"  Full State: Complete scoreboard snapshot")
    print()
    
    # ===== Aggregator Handling Both =====
    print("AGGREGATOR HANDLING:")
    print("  - Aggregator can handle both types")
    print("  - Incremental: Updates fact incrementally")
    print("  - Snapshot: Replaces fact with full state")
    print()
    
    agg = create_score_aggregator()
    
    # Process incremental events
    print("Processing incremental events...")
    for i in range(3):
        event = PlayByPlayEvent(
            game_id="game_123",
            period=2,
            period_time=f"10:{20-i}",
            game_time=datetime.now(timezone.utc),
            play_type="shot",
            team_id="LAL",
            points=2,
            home_score=40 + i * 2,
            away_score=42,
            description=f"Play {i+1}",
            timestamp=datetime.now(timezone.utc),
        )
        fact = agg.update(event)
        if fact:
            print(f"  After incremental event {i+1}: {fact.home_score}-{fact.away_score}")
    
    # Process snapshot event (replaces state)
    print("\nProcessing snapshot event...")
    snapshot = ScoreboardSnapshotEvent(
        game_id="game_123",
        home_team_id="LAL",
        away_team_id="BOS",
        home_score=50,  # New full state
        away_score=45,
        period=2,
        period_time="9:30",
        game_status="live",
        timestamp=datetime.now(timezone.utc),
    )
    fact = agg.update(snapshot)
    if fact:
        print(f"  After snapshot: {fact.home_score}-{fact.away_score}")
        print("  (Snapshot replaces incremental state)")


def example_stateless_aggregators() -> None:
    """Demonstrate stateless aggregators for batch processing."""
    print("=== Stateless Aggregators (Batch Processing) ===")
    print()
    print("Stateless aggregators process batches of events without maintaining state.")
    print("Used in pull/operator scenarios for one-time batch queries.")
    print()
    
    # ===== Example 1: Batch Score Aggregation =====
    print("Example 1: Batch Score Aggregation")
    print("-" * 50)
    
    # Simulate querying all play-by-play events for a game
    batch_events = [
        PlayByPlayEvent(
            game_id="game_123",
            period=1,
            period_time="12:00",
            game_time=datetime.now(timezone.utc),
            play_type="shot",
            team_id="LAL",
            player_id="lebron_23",
            points=2,
            home_score=2,
            away_score=0,
            description="LeBron makes 2-pointer",
            timestamp=datetime.now(timezone.utc),
        ),
        PlayByPlayEvent(
            game_id="game_123",
            period=1,
            period_time="11:30",
            game_time=datetime.now(timezone.utc),
            play_type="shot",
            team_id="BOS",
            player_id="tatum_0",
            points=3,
            home_score=2,
            away_score=3,
            description="Tatum makes 3-pointer",
            timestamp=datetime.now(timezone.utc),
        ),
        PlayByPlayEvent(
            game_id="game_123",
            period=1,
            period_time="11:00",
            game_time=datetime.now(timezone.utc),
            play_type="shot",
            team_id="LAL",
            player_id="lebron_23",
            points=2,
            home_score=4,
            away_score=3,
            description="LeBron makes another 2-pointer",
            timestamp=datetime.now(timezone.utc),
        ),
    ]
    
    # Use stateless aggregator (no state maintained)
    stateless_agg = create_stateless_score_aggregator()
    facts = stateless_agg.aggregate(batch_events)
    
    print(f"Processed {len(batch_events)} events")
    print(f"Result: {len(facts)} fact(s)")
    if facts:
        fact = facts[0]
        print(f"Final Score: {fact.home_score}-{fact.away_score}")
        print(f"Period: {fact.period}, Time: {fact.period_time}")
    print()
    
    # ===== Example 2: Batch Odds Aggregation =====
    print("Example 2: Batch Odds Aggregation")
    print("-" * 50)
    
    # Simulate querying all odds changes for multiple markets
    odds_events = [
        OddsChangeEvent(
            market_id="market_456",
            market_question="Will Lakers win?",
            game_id="game_123",
            outcome="Yes",
            previous_odds=1.80,
            current_odds=1.85,
            odds_change=0.05,
            odds_change_percent=2.78,
            timestamp=datetime.now(timezone.utc),
        ),
        OddsChangeEvent(
            market_id="market_456",
            market_question="Will Lakers win?",
            game_id="game_123",
            outcome="Yes",
            previous_odds=1.85,
            current_odds=1.92,
            odds_change=0.07,
            odds_change_percent=3.78,
            timestamp=datetime.now(timezone.utc),
        ),
        OddsChangeEvent(
            market_id="market_789",
            market_question="Will Celtics win?",
            game_id="game_123",
            outcome="Yes",
            previous_odds=2.10,
            current_odds=2.05,
            odds_change=-0.05,
            odds_change_percent=-2.38,
            timestamp=datetime.now(timezone.utc),
        ),
    ]
    
    stateless_odds_agg = create_stateless_odds_aggregator()
    odds_facts = stateless_odds_agg.aggregate(odds_events)
    
    print(f"Processed {len(odds_events)} odds change events")
    print(f"Result: {len(odds_facts)} fact(s) (one per market:outcome)")
    for fact in odds_facts:
        print(f"  Market: {fact.market_question}")
        print(f"  Outcome: {fact.outcome}, Current Odds: {fact.current_odds}")
    print()
    
    # ===== Key Differences =====
    print("Key Differences: Stateless vs Stateful")
    print("-" * 50)
    print("Stateless Aggregator:")
    print("  ✅ No state maintained between calls")
    print("  ✅ Processes batch of events, returns facts")
    print("  ✅ Used for: Pull queries, one-time batch processing")
    print("  ✅ Thread-safe: No shared state")
    print()
    print("Stateful Aggregator:")
    print("  ✅ Maintains state between calls")
    print("  ✅ Updates state incrementally as events arrive")
    print("  ✅ Used for: Streaming, maintaining current state")
    print("  ⚠️  State management required")
    print()


if __name__ == "__main__":
    explain_fact_event_relationship()
    example_fact_vs_event()
    print("\n" + "=" * 50 + "\n")
    example_odds_fact_vs_event()
    print("\n" + "=" * 50 + "\n")
    example_deriving_fact_from_events()
    print("\n" + "=" * 50 + "\n")
    example_stateful_aggregation()
    print("\n" + "=" * 50 + "\n")
    example_stateless_aggregation()
    print("\n" + "=" * 50 + "\n")
    example_incremental_vs_snapshot()
    print("\n" + "=" * 50 + "\n")
    example_stateless_aggregators()

