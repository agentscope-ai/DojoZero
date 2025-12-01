"""Data processors for transforming events into facts using LLMs and other methods.

This module provides processors that transform raw events (like GoogleSearchResultEvent)
into processed facts (like SearchResultFact) using LLM-based processing.
"""

from datetime import datetime
from typing import Any, Protocol

from agentx.data._events import DataEvent, GoogleSearchResultEvent
from agentx.data._facts import DataFact, SearchResultFact


class DataProcessor(Protocol):
    """Protocol for data processors that transform events into facts."""
    
    def process(self, event: DataEvent) -> DataFact | None:
        """Process an event and return a fact.
        
        Args:
            event: Event to process
            
        Returns:
            Processed fact or None if processing failed
        """
        ...


class LLMSearchResultProcessor:
    """LLM-based processor that transforms GoogleSearchResultEvent into SearchResultFact.
    
    This processor:
    1. Takes raw Google search results
    2. Uses an LLM to extract insights, sentiment, and key findings
    3. Generates a SearchResultFact that can be consumed by operators
    
    Example:
        processor = LLMSearchResultProcessor(llm_client)
        event = GoogleSearchResultEvent(...)
        fact = processor.process(event)  # Returns SearchResultFact
    """
    
    def __init__(self, llm_client: Any) -> None:
        """Initialize processor with LLM client.
        
        Args:
            llm_client: LLM client (e.g., OpenAI, Anthropic, etc.)
        """
        self.llm_client = llm_client
    
    def process(self, event: GoogleSearchResultEvent) -> SearchResultFact | None:
        """Process Google search results using LLM.
        
        Args:
            event: GoogleSearchResultEvent with raw search results
            
        Returns:
            SearchResultFact with processed insights
        """
        # Extract text from search results
        search_texts = self._extract_texts_from_results(event.results)
        
        # Use LLM to process search results
        llm_prompt = self._build_llm_prompt(event.query, search_texts, event.game_id)
        llm_response = self._call_llm(llm_prompt)
        
        # Parse LLM response into structured data
        processed_data = self._parse_llm_response(llm_response)
        
        # Extract top sources
        top_sources = self._extract_top_sources(event.results)
        
        # Build SearchResultFact
        return SearchResultFact(
            query=event.query,
            search_id=event.search_id,
            summary=processed_data.get("summary", ""),
            key_findings=processed_data.get("key_findings", []),
            relevance_score=processed_data.get("relevance_score", 0.0),
            sentiment=processed_data.get("sentiment", "neutral"),
            sentiment_score=processed_data.get("sentiment_score", 0.0),
            game_id=event.game_id,
            team_ids=event.team_ids,
            player_ids=event.player_ids,
            top_results_count=len(event.results),
            top_sources=top_sources,
            betting_insights=processed_data.get("betting_insights", []),
            confidence=processed_data.get("confidence", 0.0),
            processed_at=datetime.now(),
            timestamp=event.timestamp,
        )
    
    def _extract_texts_from_results(self, results: list[dict[str, Any]]) -> list[str]:
        """Extract text content from search results.
        
        Args:
            results: Raw search results from Google API
            
        Returns:
            List of text snippets from results
        """
        texts = []
        for result in results:
            # Extract title, snippet, etc.
            title = result.get("title", "")
            snippet = result.get("snippet", "")
            texts.append(f"{title}\n{snippet}")
        return texts
    
    def _build_llm_prompt(
        self, 
        query: str, 
        search_texts: list[str], 
        game_id: str | None
    ) -> str:
        """Build LLM prompt for processing search results.
        
        Args:
            query: Original search query
            search_texts: Extracted text from search results
            game_id: Related game ID if applicable
            
        Returns:
            Formatted LLM prompt
        """
        texts_combined = "\n\n---\n\n".join(search_texts[:10])  # Top 10 results
        
        prompt = f"""You are analyzing Google search results for NBA betting purposes.

Search Query: {query}
Game ID: {game_id if game_id else "N/A"}

Search Results:
{texts_combined}

Please analyze these search results and provide:
1. A concise summary (2-3 sentences)
2. Key findings (3-5 bullet points)
3. Overall relevance score (0.0-1.0)
4. Sentiment (positive/negative/neutral) and sentiment score (-1.0 to 1.0)
5. Betting-relevant insights (2-3 insights)
6. Confidence in insights (0.0-1.0)

Format your response as JSON:
{{
    "summary": "...",
    "key_findings": ["...", "..."],
    "relevance_score": 0.85,
    "sentiment": "positive",
    "sentiment_score": 0.6,
    "betting_insights": ["...", "..."],
    "confidence": 0.75
}}
"""
        return prompt
    
    def _call_llm(self, prompt: str) -> str:
        """Call LLM with prompt.
        
        Args:
            prompt: LLM prompt
            
        Returns:
            LLM response text
        """
        # This is a placeholder - in real implementation, would call actual LLM
        # Example: response = self.llm_client.complete(prompt)
        # For now, return a mock response
        return """{
    "summary": "Recent news suggests Lakers are performing well with LeBron James leading the team. Multiple sources indicate strong team chemistry and recent wins.",
    "key_findings": [
        "Lakers won last 3 games",
        "LeBron James averaging 28 points per game",
        "Team chemistry improving",
        "Upcoming game against Celtics"
    ],
    "relevance_score": 0.85,
    "sentiment": "positive",
    "sentiment_score": 0.6,
    "betting_insights": [
        "Lakers showing strong momentum",
        "Consider betting on Lakers for next game"
    ],
    "confidence": 0.75
}"""
    
    def _parse_llm_response(self, response: str) -> dict[str, Any]:
        """Parse LLM JSON response.
        
        Args:
            response: LLM response text (should be JSON)
            
        Returns:
            Parsed data dictionary
        """
        import json
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            # Fallback if LLM doesn't return valid JSON
            return {
                "summary": response[:200],
                "key_findings": [],
                "relevance_score": 0.5,
                "sentiment": "neutral",
                "sentiment_score": 0.0,
                "betting_insights": [],
                "confidence": 0.5,
            }
    
    def _extract_top_sources(self, results: list[dict[str, Any]]) -> list[str]:
        """Extract top sources from search results.
        
        Args:
            results: Raw search results
            
        Returns:
            List of top source names
        """
        sources = []
        for result in results[:5]:  # Top 5 results
            source = result.get("source", result.get("displayLink", ""))
            if source:
                sources.append(source)
        return sources


# Factory function
def create_llm_search_processor(llm_client: Any) -> LLMSearchResultProcessor:
    """Create an LLM-based search result processor.
    
    Args:
        llm_client: LLM client instance
        
    Returns:
        LLMSearchResultProcessor instance
    """
    return LLMSearchResultProcessor(llm_client)

