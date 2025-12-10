"""Web Search-specific processors."""

from __future__ import annotations

import json
import re
from typing import Any, cast

from agentx.data._models import DataEvent
from agentx.data._processors import DataProcessor
from agentx.data._utils import (
    call_dashscope_model,
    extract_json_from_dashscope_response,
    initialize_dashscope,
)
from agentx.data.websearch._events import (
    ExpertPredictionEvent,
    InjurySummaryEvent,
    PowerRankingEvent,
    RawWebSearchEvent,
    WebSearchIntent,
)


class InjurySummaryProcessor(DataProcessor):
    """Processor that uses Dashscope to summarize injury information from web search results.
    
    This processor extracts and summarizes injury information from raw web search results.
    """
    
    intended_intent = WebSearchIntent.INJURY_SUMMARY  # Intent this processor handles
    
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "qwen-turbo",
    ):
        """Initialize injury summary processor.
        
        Args:
            api_key: Dashscope API key (defaults to DASHSCOPE_API_KEY env var)
            model: Dashscope model to use for summarization
        """
        # Initialize Dashscope (will raise if not available or key missing)
        self.api_key = initialize_dashscope(api_key)
        self.model = model
    
    def should_process(self, event: DataEvent) -> bool:
        """Check if this processor should handle the event.
        
        Only processes raw web search events with injury-related queries.
        If event has intent, uses intent-based routing (via base class); otherwise uses keyword matching.
        
        Args:
            event: Event to check
            
        Returns:
            True if event is injury-related raw web search, False otherwise
        """
        # Only process raw web search events
        if event.event_type != "raw_web_search":
            return False
        
        # Use base class intent checking first
        event_intent = getattr(event, "intent", None)
        if event_intent is not None:
            return super().should_process(event)
        
        # Fallback to keyword-based filtering when no intent
        query = getattr(event, "query", "")
        if not query:
            return False
        
        query_lower = query.lower()
        return "injury" in query_lower or "injured" in query_lower
    
    async def process(self, event: DataEvent) -> DataEvent | None:
        """Process raw web search event and generate injury summary.
        
        Args:
            event: Raw web search event
            
        Returns:
            Injury summary event or None
        """
        # should_process() already ensures this is a raw_web_search event
        raw_event = cast(RawWebSearchEvent, event)  # type: ignore[arg-type]
        
        # Extract text from search results
        result_texts = []
        for result in raw_event.results:
            title = result.get("title", "")
            snippet = result.get("snippet", "")
            if title or snippet:
                result_texts.append(f"Title: {title}\nContent: {snippet}")
        
        if not result_texts:
            return None
        
        # Combine results into context
        context = "\n\n".join(result_texts)
        
        # Create prompt for injury summarization (hybrid: summary + structured data)
        prompt = f"""Based on the following web search results about injuries, provide:
1. A concise human-readable summary
2. A structured JSON object mapping teams to lists of injured players

Search Results:
{context}

Please provide your response in the following format:

SUMMARY:
[Provide a concise summary focusing on who is injured, type of injury, status/severity, and timeline/return date if mentioned]

STRUCTURED_DATA:
{{
  "team1": ["player1", "player2"],
  "team2": ["player3", "player4"]
}}

If a team or player name is not clearly mentioned, use empty lists or omit the team.
Only include players who are confirmed to be injured/out."""
        
        # Call Dashscope API
        try:
            response = await call_dashscope_model(
                prompt=prompt,
                model=self.model,
            )
            
            # Extract summary and structured data from response
            summary = ""
            injured_players: dict[str, list[str]] = {}
            
            if response.get("status_code") == 200:
                full_text = response.get("output", {}).get("text", "").strip()
                if not full_text:
                    # Fallback: try alternative response structure
                    full_text = str(response.get("output", "")).strip()
                
                # Parse the response: extract SUMMARY and STRUCTURED_DATA sections
                summary_match = re.search(r"SUMMARY:\s*(.*?)(?=STRUCTURED_DATA:|$)", full_text, re.DOTALL | re.IGNORECASE)
                if summary_match:
                    summary = summary_match.group(1).strip()
                else:
                    # Fallback: use everything before STRUCTURED_DATA as summary
                    structured_match = re.search(r"STRUCTURED_DATA:", full_text, re.IGNORECASE)
                    if structured_match:
                        summary = full_text[:structured_match.start()].strip()
                    else:
                        summary = full_text
                
                # Extract structured JSON data using utility function
                # First extract the STRUCTURED_DATA section, then use utility to parse
                structured_match = re.search(r"STRUCTURED_DATA:\s*(\{.*\})", full_text, re.DOTALL | re.IGNORECASE)
                if structured_match:
                    json_str = structured_match.group(1).strip()
                    # Create a mock response structure for the utility function
                    mock_response = {
                        "status_code": 200,
                        "output": {"text": json_str}
                    }
                    extracted = extract_json_from_dashscope_response(
                        mock_response, expected_type=dict
                    )
                    if extracted and isinstance(extracted, dict):
                        # Ensure all values are lists
                        injured_players = {
                            k: v if isinstance(v, list) else [v] if isinstance(v, str) else []
                            for k, v in extracted.items()
                        }
            else:
                error_msg = response.get("message", "Unknown error")
                summary = f"Error generating summary: {error_msg}"
            
        except Exception as e:
            summary = f"Error calling Dashscope API: {str(e)}"
            injured_players = {}
        
        # Create injury summary event
        event_class: type[InjurySummaryEvent] = InjurySummaryEvent  # type: ignore[assignment]
        return event_class(
            timestamp=raw_event.timestamp,
            query=raw_event.query,
            summary=summary,
            injured_players=injured_players,
        )


class PowerRankingProcessor(DataProcessor):
    """Processor that extracts power rankings from web search results.
    
    Extracts structured power rankings from NBA.com, ESPN, and other sources.
    If team names are mentioned in the query, filters results to only those teams.
    """
    
    intended_intent = WebSearchIntent.POWER_RANKING  # Intent this processor handles
    
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "qwen-turbo",
    ):
        """Initialize power ranking processor.
        
        Args:
            api_key: Dashscope API key (defaults to DASHSCOPE_API_KEY env var)
            model: Dashscope model to use for extraction
        """
        # Initialize Dashscope (will raise if not available or key missing)
        self.api_key = initialize_dashscope(api_key)
        self.model = model
    
    def should_process(self, event: DataEvent) -> bool:
        """Check if this processor should handle the event.
        
        Only processes raw web search events with power ranking-related queries.
        If event has intent, uses intent-based routing (via base class); otherwise uses keyword matching.
        
        Args:
            event: Event to check
            
        Returns:
            True if event is power ranking-related raw web search, False otherwise
        """
        # Only process raw web search events
        if event.event_type != "raw_web_search":
            return False
        
        # Use base class intent checking first
        event_intent = getattr(event, "intent", None)
        if event_intent is not None:
            return super().should_process(event)
        
        # Fallback to keyword-based filtering when no intent
        query = getattr(event, "query", "")
        if not query:
            return False
        
        query_lower = query.lower()
        return "power ranking" in query_lower or "power rankings" in query_lower
    
    async def process(self, event: DataEvent) -> DataEvent | None:
        """Process raw web search event and extract power rankings.
        
        Args:
            event: Raw web search event
            
        Returns:
            Power ranking event or None
        """
        # should_process() already ensures this is a raw_web_search event
        raw_event = cast(RawWebSearchEvent, event)  # type: ignore[arg-type]
        
        # Extract text from search results
        result_texts = []
        for result in raw_event.results:
            title = result.get("title", "")
            snippet = result.get("snippet", "")
            url = result.get("url", "")
            if title or snippet:
                # Include URL to identify source
                result_texts.append(f"Source: {url}\nTitle: {title}\nContent: {snippet}")
        
        if not result_texts:
            return None
        
        # Combine results into context
        context = "\n\n".join(result_texts)
        
        # Create prompt for power ranking extraction (concise to save tokens)
        prompt = f"""Extract NBA power rankings from these search results. Return JSON only.

Results:
{context}

Format:
{{
  "nba.com": [{{"rank": 1, "team": "Lakers", "record": "15-5", "notes": "..."}}],
  "espn.com": [{{"rank": 1, "team": "Lakers", "record": "15-5", "notes": "..."}}]
}}

Rules:
- Each team appears ONCE per source
- Extract full ranking
- Use URL domain as key (e.g., "nba.com")
- Skip sources without clear rankings"""
        
        # Call Dashscope API
        try:
            response = await call_dashscope_model(
                prompt=prompt,
                model=self.model,
            )

            # Extract rankings from response using utility function
            extracted = extract_json_from_dashscope_response(
                response, expected_type=dict
            )
            
            rankings: dict[str, list[dict[str, Any]]] = {}
            if extracted and isinstance(extracted, dict):
                # Clean and validate rankings: remove duplicates, ensure proper format
                cleaned_rankings: dict[str, list[dict[str, Any]]] = {}
                for source, teams in extracted.items():
                    if not isinstance(teams, list):
                        continue
                    
                    # Filter out invalid entries and remove duplicates
                    seen_teams: set[str] = set()
                    valid_teams = []
                    for team_data in teams:
                        if not isinstance(team_data, dict):
                            continue
                        
                        team_name = team_data.get("team", "").strip()
                        if not team_name:
                            continue
                        
                        # Skip if we've seen this team already (duplicate)
                        if team_name.lower() in seen_teams:
                            continue
                        
                        seen_teams.add(team_name.lower())
                        valid_teams.append(team_data)
                    
                    # Only include if we have at least 1 team (relaxed threshold)
                    if len(valid_teams) >= 1:
                        cleaned_rankings[source] = valid_teams
                
                rankings = cleaned_rankings
                if rankings:
                    print(f"DEBUG: Successfully extracted rankings from {len(rankings)} sources")
            
        except Exception as e:
            print(f"DEBUG: Error extracting rankings: {e}")
            rankings = {}
        
        # Create power ranking event
        event_class: type[PowerRankingEvent] = PowerRankingEvent  # type: ignore[assignment]
        return event_class(
            timestamp=raw_event.timestamp,
            query=raw_event.query,
            rankings=rankings,
        )


class ExpertPredictionProcessor(DataProcessor):
    """Processor that extracts expert predictions from web search results.
    
    Extracts expert predictions and analysis from NBA.com, ESPN, and other credible sources.
    """
    
    intended_intent = WebSearchIntent.EXPERT_PREDICTION  # Intent this processor handles
    
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "qwen-turbo",
    ):
        """Initialize expert prediction processor.
        
        Args:
            api_key: Dashscope API key (defaults to DASHSCOPE_API_KEY env var)
            model: Dashscope model to use for extraction
        """
        # Initialize Dashscope (will raise if not available or key missing)
        self.api_key = initialize_dashscope(api_key)
        self.model = model
    
    def should_process(self, event: DataEvent) -> bool:
        """Check if this processor should handle the event.
        
        Only processes raw web search events with prediction/expert-related queries.
        If event has intent, uses intent-based routing (via base class); otherwise uses keyword matching.
        
        Args:
            event: Event to check
            
        Returns:
            True if event is prediction-related raw web search, False otherwise
        """
        # Only process raw web search events
        if event.event_type != "raw_web_search":
            return False
        
        # Use base class intent checking first
        event_intent = getattr(event, "intent", None)
        if event_intent is not None:
            return super().should_process(event)
        
        # Fallback to keyword-based filtering when no intent
        query = getattr(event, "query", "")
        if not query:
            return False
        
        query_lower = query.lower()
        prediction_keywords = [
            "expert predictions", "expert prediction", "expert pick", "expert picks",
            "prediction", "predictions", "expert", "pick", "picks",
            "forecast", "analysis", "preview"
        ]
        return any(keyword in query_lower for keyword in prediction_keywords)
    
    async def process(self, event: DataEvent) -> DataEvent | None:
        """Process raw web search event and extract expert predictions.
        
        Args:
            event: Raw web search event
            
        Returns:
            Expert prediction event or None
        """
        # should_process() already ensures this is a raw_web_search event
        raw_event = cast(RawWebSearchEvent, event)  # type: ignore[arg-type]
        
        # Extract text from search results
        result_texts = []
        for result in raw_event.results:
            title = result.get("title", "")
            snippet = result.get("snippet", "")
            url = result.get("url", "")
            if title or snippet:
                # Include URL to identify source
                result_texts.append(f"Source: {url}\nTitle: {title}\nContent: {snippet}")
        
        if not result_texts:
            return None
        
        # Combine results into context
        context = "\n\n".join(result_texts)
        
        # Create prompt for expert prediction extraction
        prompt = f"""Based on the following web search results about NBA expert predictions, extract structured prediction data.

Search Results:
{context}

Please extract expert predictions from each source (NBA.com, ESPN, etc.) and provide in the following JSON format:

[
  {{
    "source": "nba.com",
    "expert": "Expert Name (if mentioned)",
    "prediction": "Main prediction text",
    "reasoning": "Expert's reasoning/analysis",
    "confidence": "High/Medium/Low (if mentioned)"
  }},
  {{
    "source": "espn.com",
    "expert": "Expert Name",
    "prediction": "Main prediction text",
    "reasoning": "Expert's reasoning/analysis",
    "confidence": "High/Medium/Low"
  }}
]

Extract predictions from all credible sources mentioned. If expert name is not mentioned, use "Anonymous" or omit the field.
Focus on game predictions, matchup analysis, and expert picks."""
        
        # Call Dashscope API
        try:
            response = await call_dashscope_model(
                prompt=prompt,
                model=self.model
            )
            
            # Extract predictions from response using utility function
            extracted = extract_json_from_dashscope_response(
                response, expected_type=list
            )
            
            predictions: list[dict[str, Any]] = []
            if extracted and isinstance(extracted, list):
                # Ensure all items are dicts
                predictions = [
                    p if isinstance(p, dict) else {}
                    for p in extracted
                ]
            
        except Exception as e:
            print(f"DEBUG: Error extracting predictions: {e}")
            predictions = []
        
        # Create expert prediction event
        event_class: type[ExpertPredictionEvent] = ExpertPredictionEvent  # type: ignore[assignment]
        return event_class(
            timestamp=raw_event.timestamp,
            query=raw_event.query,
            predictions=predictions,
        )

