"""Example: Google Search → LLM Processing → SearchResultFact for Operators.

This demonstrates the complete flow:
1. Initiate Google search with proper terms
2. Process using LLM-based processor
3. Generate SearchResultFact that can be consumed by operators
"""

from datetime import datetime, timezone

from agentx.data import (
    GoogleSearchResultEvent,
    SearchResultFact,
)
from agentx.processor import (
    LLMSearchResultProcessor,
    create_llm_search_processor,
)


def example_google_search_flow() -> None:
    """Demonstrate the complete Google search → LLM → Fact flow."""
    
    print("=" * 70)
    print("GOOGLE SEARCH → LLM PROCESSING → SEARCHRESULTFACT")
    print("=" * 70)
    print()
    
    # ===== Step 1: Initiate Google Search =====
    print("Step 1: Initiate Google Search")
    print("-" * 70)
    
    # Simulate Google Search API call
    search_query = "Lakers vs Celtics game tonight LeBron James injury status"
    game_id = "game_123"
    
    # In real implementation, this would call Google Search API
    # results = google_search_api.search(search_query)
    mock_search_results = [
        {
            "title": "LeBron James Injury Update: Lakers Star Questionable for Celtics Game",
            "snippet": "LeBron James is listed as questionable for tonight's game against the Celtics due to ankle soreness...",
            "link": "https://espn.com/nba/lakers",
            "source": "ESPN",
            "displayLink": "espn.com",
        },
        {
            "title": "Lakers vs Celtics Preview: Key Matchup Analysis",
            "snippet": "The Lakers face the Celtics tonight in a crucial matchup. Both teams are coming off wins...",
            "link": "https://theathletic.com/nba",
            "source": "The Athletic",
            "displayLink": "theathletic.com",
        },
        {
            "title": "NBA Betting Odds: Lakers vs Celtics Line Movement",
            "snippet": "Betting lines have shifted in favor of Celtics after LeBron injury news...",
            "link": "https://sportsbook.com",
            "source": "Sportsbook",
            "displayLink": "sportsbook.com",
        },
    ]
    
    # Create GoogleSearchResultEvent (raw search results)
    search_event = GoogleSearchResultEvent(
        query=search_query,
        search_id=f"search_{datetime.now().timestamp()}",
        results=mock_search_results,
        total_results=len(mock_search_results),
        search_time=0.45,
        game_id=game_id,
        team_ids=["LAL", "BOS"],
        player_ids=["lebron_23"],
        timestamp=datetime.now(timezone.utc),
    )
    
    print(f"Query: {search_query}")
    print(f"Results Found: {search_event.total_results}")
    print(f"Search Time: {search_event.search_time}s")
    print(f"Event Type: {search_event.event_type}")
    print(f"Update Type: {search_event.update_type} (snapshot)")
    print()
    
    # ===== Step 2: Process Using LLM-Based Processor =====
    print("Step 2: Process Using LLM-Based Processor")
    print("-" * 70)
    
    # Create LLM processor (in real implementation, would pass actual LLM client)
    # llm_client = OpenAI() or Anthropic() etc.
    mock_llm_client = None  # Placeholder
    processor = create_llm_search_processor(mock_llm_client)
    
    print("Processing search results with LLM...")
    print("  - Extracting key insights")
    print("  - Analyzing sentiment")
    print("  - Identifying betting-relevant information")
    print()
    
    # Process event → fact
    search_fact = processor.process(search_event)
    
    if not search_fact:
        print("ERROR: Processing failed")
        return
    
    # ===== Step 3: SearchResultFact for Operator Consumption =====
    print("Step 3: SearchResultFact (Ready for Operator Consumption)")
    print("-" * 70)
    
    print(f"Fact Type: {search_fact.fact_type}")
    print(f"Query: {search_fact.query}")
    print()
    print("Summary:")
    print(f"  {search_fact.summary}")
    print()
    print("Key Findings:")
    for finding in search_fact.key_findings:
        print(f"  • {finding}")
    print()
    print("Sentiment Analysis:")
    print(f"  Sentiment: {search_fact.sentiment}")
    print(f"  Score: {search_fact.sentiment_score:.2f}")
    print(f"  Relevance: {search_fact.relevance_score:.2f}")
    print()
    print("Betting Insights:")
    for insight in search_fact.betting_insights:
        print(f"  • {insight}")
    print(f"  Confidence: {search_fact.confidence:.2f}")
    print()
    print("Top Sources:")
    for source in search_fact.top_sources:
        print(f"  • {source}")
    print()
    
    # ===== Step 4: Operator Consumption =====
    print("Step 4: Operator Consumes SearchResultFact")
    print("-" * 70)
    
    print("Operator can now:")
    print("  ✅ Access processed insights via fact.summary")
    print("  ✅ Use key_findings for decision-making")
    print("  ✅ Check sentiment_score for market sentiment")
    print("  ✅ Use betting_insights for betting decisions")
    print("  ✅ Evaluate confidence to weight the insights")
    print()
    
    # Example operator usage
    def betting_operator_decision(fact: SearchResultFact) -> str:
        """Example: Operator uses fact to make betting decision."""
        if fact.sentiment_score < -0.3:
            return "Consider betting against (negative sentiment)"
        elif fact.sentiment_score > 0.3 and fact.confidence > 0.7:
            return "Consider betting for (positive sentiment, high confidence)"
        else:
            return "Neutral - wait for more information"
    
    decision = betting_operator_decision(search_fact)
    print(f"Operator Decision: {decision}")
    print()


def example_operator_pull_pattern() -> None:
    """Show how operators pull SearchResultFact."""
    
    print("=" * 70)
    print("OPERATOR PULL PATTERN")
    print("=" * 70)
    print()
    
    print("Flow:")
    print("1. Agent requests operator to search for information")
    print("2. Operator initiates Google search")
    print("3. Operator processes results with LLM processor")
    print("4. Operator returns SearchResultFact to agent")
    print()
    
    # Simulate operator pull
    class BettingOperator:
        def __init__(self, llm_processor: LLMSearchResultProcessor):
            self.llm_processor = llm_processor
        
        def search_and_process(self, query: str, game_id: str | None = None) -> SearchResultFact | None:
            """Operator method: search and return processed fact."""
            # 1. Search (would call Google API)
            search_event = GoogleSearchResultEvent(
                query=query,
                search_id=f"search_{datetime.now().timestamp()}",
                results=[],  # Would be actual results
                total_results=0,
                search_time=0.0,
                game_id=game_id,
                timestamp=datetime.now(timezone.utc),
            )
            
            # 2. Process with LLM
            fact = self.llm_processor.process(search_event)
            
            # 3. Return fact
            return fact
    
    print("Example Usage:")
    print("  operator = BettingOperator(llm_processor)")
    print("  fact = operator.search_and_process('Lakers injury news', game_id='game_123')")
    print("  # Agent receives SearchResultFact with processed insights")
    print()


if __name__ == "__main__":
    example_google_search_flow()
    print("\n")
    example_operator_pull_pattern()

