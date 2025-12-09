"""Web Search-specific processors."""

from __future__ import annotations

import json
import re
from typing import Any, Sequence, cast

from agentx.data._models import DataEvent
from agentx.data._processors import DataProcessor
from agentx.data._utils import call_dashscope_model, initialize_dashscope
from agentx.data.websearch._events import InjurySummaryEvent, RawWebSearchEvent


class InjurySummaryProcessor(DataProcessor):
    """Processor that uses Dashscope to summarize injury information from web search results.
    
    This processor extracts and summarizes injury information from raw web search results.
    """
    
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "qwen-turbo",
        max_tokens: int = 500,
    ):
        """Initialize injury summary processor.
        
        Args:
            api_key: Dashscope API key (defaults to DASHSCOPE_API_KEY env var)
            model: Dashscope model to use for summarization
            max_tokens: Maximum tokens for the summary
        """
        # Initialize Dashscope (will raise if not available or key missing)
        self.api_key = initialize_dashscope(api_key)
        self.model = model
        self.max_tokens = max_tokens
    
    def should_process(self, event: DataEvent) -> bool:
        """Check if this processor should handle the event.
        
        Only processes raw web search events with injury-related queries.
        
        Args:
            event: Event to check
            
        Returns:
            True if event is injury-related raw web search, False otherwise
        """
        # Only process raw web search events
        if event.event_type != "raw_web_search":
            return False
        
        # Check if query is injury-related
        query = getattr(event, "query", "")
        if not query:
            return False
        
        query_lower = query.lower()
        return "injury" in query_lower or "injured" in query_lower
    
    async def process(self, events: Sequence[DataEvent]) -> DataEvent | None:
        """Process raw web search events and generate injury summary.
        
        Args:
            events: Sequence of raw web search events
            
        Returns:
            Injury summary event or None
        """
        if not events:
            return None
        
        # Get the latest raw event by checking event_type
        raw_event: RawWebSearchEvent | None = None  # type: ignore[valid-type]
        for event in events:
            if event.event_type == "raw_web_search":
                raw_event = cast(RawWebSearchEvent, event)  # type: ignore[arg-type]
                break
        
        if not raw_event:
            return None
        
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
                max_tokens=self.max_tokens,
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
                
                # Extract structured JSON data
                structured_match = re.search(r"STRUCTURED_DATA:\s*(\{.*\})", full_text, re.DOTALL | re.IGNORECASE)
                if structured_match:
                    try:
                        json_str = structured_match.group(1).strip()
                        injured_players = json.loads(json_str)
                        # Validate structure: should be dict[str, list[str]]
                        if not isinstance(injured_players, dict):
                            injured_players = {}
                        else:
                            # Ensure all values are lists
                            injured_players = {
                                k: v if isinstance(v, list) else [v] if isinstance(v, str) else []
                                for k, v in injured_players.items()
                            }
                    except (json.JSONDecodeError, AttributeError):
                        # JSON parsing failed, leave as empty dict
                        injured_players = {}
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
            source_results_count=len(raw_event.results),
        )

