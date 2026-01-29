"""Web Search game insight event types.

These are WebSearchInsightEvent subclasses that provide supplementary intelligence
to agents. Each event type handles a specific kind of web search query
and its LLM-processed results.

Each subclass owns its full lifecycle via ``from_web_search()``:
    build query from GameContext → call search API → call LLM → return typed event.

Hierarchy:
    PreGameInsightEvent
    └── WebSearchInsightEvent (query + summary)
        ├── InjuryReportEvent (+ injured_players)
        ├── PowerRankingEvent (+ rankings)
        └── ExpertPredictionEvent (+ predictions)
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any, ClassVar, Literal, Self

from pydantic import Field

from dojozero.data._models import (
    WebSearchInsightEvent,
    register_event,
)
from dojozero.data._utils import (
    call_dashscope_model,
    extract_json_from_dashscope_response,
    initialize_dashscope,
)
from dojozero.data.websearch._api import WebSearchAPI
from dojozero.data.websearch._context import GameContext

logger = logging.getLogger(__name__)

# Pre-compiled regex patterns for response parsing
_SUMMARY_PATTERN = re.compile(
    r"SUMMARY:\s*(.*?)(?=STRUCTURED_DATA:|$)", re.DOTALL | re.IGNORECASE
)
_STRUCTURED_DATA_HEADER_PATTERN = re.compile(r"STRUCTURED_DATA:", re.IGNORECASE)
_STRUCTURED_DATA_JSON_PATTERN = re.compile(
    r"STRUCTURED_DATA:\s*(\{.*\})", re.DOTALL | re.IGNORECASE
)


def _filter_teams(
    injured_players: dict[str, list[str]],
    home_team: str,
    away_team: str,
) -> dict[str, list[str]]:
    """Filter injured_players dict to only teams matching the game context.

    Uses case-insensitive substring matching to handle variations like
    "Grizzlies" vs "Memphis Grizzlies".
    """
    relevant = [t.lower() for t in (home_team, away_team) if t]
    if not relevant:
        return injured_players

    def _matches(team_key: str) -> bool:
        key_lower = team_key.lower()
        for r in relevant:
            if r in key_lower or key_lower in r:
                return True
        return False

    return {k: v for k, v in injured_players.items() if _matches(k)}


class WebSearchIntent(str, Enum):
    """Web search query intent types.

    These intents specify the type of processing expected for a query,
    allowing explicit routing to specific processors.

    Note: These are routing hints for processors, separate from EventTypes
    which are used for event type identification in the event bus.
    """

    INJURY_REPORT = "injury_report"
    POWER_RANKING = "power_ranking"
    EXPERT_PREDICTION = "expert_prediction"


class WebSearchEventMixin:
    """Mixin providing search → LLM → typed-event lifecycle for websearch events.

    Subclasses must implement:
    - ``default_search_template`` (ClassVar[str]) — query template with placeholders
    - ``_build_llm_prompt(context_text)`` — returns the LLM prompt string
    - ``_parse_llm_response(response, query, context)`` — returns a typed event instance or None
    """

    default_search_template: ClassVar[str]

    @classmethod
    async def from_web_search(
        cls,
        api: WebSearchAPI,
        context: GameContext,
        model: str = "qwen-turbo",
    ) -> Self | None:
        """Full lifecycle: build query → search API → LLM → typed event.

        Args:
            api: WebSearchAPI instance for executing searches.
            context: GameContext with team/date info for template rendering.
            model: Dashscope model to use (default: "qwen-turbo").

        Returns:
            Typed event instance, or None if no results or processing failed.
        """
        # Ensure Dashscope is initialized
        initialize_dashscope()

        # 1. Build query from template + context
        query = context.render_template(cls.default_search_template)
        logger.info("WebSearch query for %s: '%s'", cls.__name__, query)

        # 2. Call search API
        data = await api.fetch(
            "search",
            {
                "query": query,
                "search_depth": "advanced",
                "max_results": 5,
                "include_raw_content": True,
            },
        )
        results = data.get("results", [])
        if not results:
            logger.warning("No search results for %s query: '%s'", cls.__name__, query)
            return None

        # 3. Build LLM prompt from results
        result_texts = cls._format_search_results(results)
        if not result_texts:
            return None
        prompt = cls._build_llm_prompt("\n\n".join(result_texts))

        # 4. Call Dashscope LLM
        try:
            response = await call_dashscope_model(prompt, model=model)
        except Exception as e:
            logger.error(
                "Dashscope call failed for %s: %s", cls.__name__, e, exc_info=True
            )
            return None

        # 5. Parse into typed event
        event = cls._parse_llm_response(response, query, context)

        # 6. Populate game_id, sport, and source from context so the event
        #    participates in the DataHub lifecycle gate.
        #    All concrete subclasses are Pydantic models (via WebSearchInsightEvent)
        #    with game_id/sport/source fields from SportEvent / PreGameInsightEvent.
        if event is not None and hasattr(event, "model_copy"):
            overrides: dict[str, Any] = {}
            if context.game_id and not getattr(event, "game_id", ""):
                overrides["game_id"] = context.game_id
            if context.sport and not getattr(event, "sport", ""):
                overrides["sport"] = context.sport
            if not getattr(event, "source", ""):
                overrides["source"] = "websearch"
            if overrides:
                event = event.model_copy(update=overrides)  # type: ignore[union-attr]

        return event

    @classmethod
    def _format_search_results(cls, results: list[dict[str, Any]]) -> list[str]:
        """Format raw API results into text blocks for LLM prompt."""
        texts = []
        for r in results:
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            url = r.get("url", "")
            if title or snippet:
                parts: list[str] = []
                if url:
                    parts.append(f"Source: {url}")
                parts.append(f"Title: {title}")
                parts.append(f"Content: {snippet}")
                texts.append("\n".join(parts))
        return texts

    @classmethod
    def _build_llm_prompt(cls, context: str) -> str:
        """Build the LLM prompt from formatted search results.

        Args:
            context: Concatenated search result text blocks.

        Returns:
            Prompt string for Dashscope.
        """
        raise NotImplementedError(f"{cls.__name__} must implement _build_llm_prompt()")

    @classmethod
    def _parse_llm_response(
        cls, response: dict[str, Any], query: str, context: GameContext
    ) -> Self | None:
        """Parse Dashscope LLM response into a typed event.

        Args:
            response: Dashscope API response dict.
            query: Original search query string.
            context: GameContext with team/date info for filtering.

        Returns:
            Typed event instance or None if parsing failed.
        """
        raise NotImplementedError(
            f"{cls.__name__} must implement _parse_llm_response()"
        )


@register_event
class InjuryReportEvent(WebSearchEventMixin, WebSearchInsightEvent):
    """Injury report from web search + LLM processing.

    Contains both human-readable summary (inherited from WebSearchInsightEvent)
    and structured data (team -> list of injured players).
    """

    event_type: Literal["event.injury_report"] = "event.injury_report"
    default_search_template: ClassVar[str] = (
        "{sport} injury updates for {teams} on {date}"
    )

    injured_players: dict[str, list[str]] = Field(default_factory=dict)

    @classmethod
    def _build_llm_prompt(cls, context: str) -> str:
        return f"""Based on the following web search results about injuries, provide:
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

IMPORTANT: ONLY include players from the teams mentioned in the search query above. Do NOT include players from other teams.
Only include players who are confirmed to be injured/out."""

    @classmethod
    def _parse_llm_response(
        cls, response: dict[str, Any], query: str, context: GameContext
    ) -> InjuryReportEvent | None:
        summary = ""
        injured_players: dict[str, list[str]] = {}

        if response.get("status_code") == 200:
            full_text = response.get("output", {}).get("text", "").strip()
            if not full_text:
                full_text = str(response.get("output", "")).strip()

            # Parse SUMMARY section
            summary_match = _SUMMARY_PATTERN.search(full_text)
            if summary_match:
                summary = summary_match.group(1).strip()
            else:
                structured_match = _STRUCTURED_DATA_HEADER_PATTERN.search(full_text)
                if structured_match:
                    summary = full_text[: structured_match.start()].strip()
                else:
                    summary = full_text

            # Parse STRUCTURED_DATA section
            structured_match = _STRUCTURED_DATA_JSON_PATTERN.search(full_text)
            if structured_match:
                json_str = structured_match.group(1).strip()
                mock_response = {"status_code": 200, "output": {"text": json_str}}
                extracted = extract_json_from_dashscope_response(
                    mock_response, expected_type=dict
                )
                if extracted and isinstance(extracted, dict):
                    injured_players = {
                        k: (
                            v
                            if isinstance(v, list)
                            else [v]
                            if isinstance(v, str)
                            else []
                        )
                        for k, v in extracted.items()
                    }
        else:
            error_msg = response.get("message", "Unknown error")
            logger.warning(
                "Dashscope API returned non-200 for injury report: status=%d, message=%s",
                response.get("status_code", 0),
                error_msg,
            )
            summary = f"Error generating summary: {error_msg}"

        # Filter to only teams in the game context
        if injured_players and (context.home_team or context.away_team):
            injured_players = _filter_teams(
                injured_players, context.home_team, context.away_team
            )

        return cls(
            query=query,
            summary=summary,
            injured_players=injured_players,
        )


@register_event
class PowerRankingEvent(WebSearchEventMixin, WebSearchInsightEvent):
    """Power rankings from web search + LLM processing.

    Contains rankings from multiple sources with structured data.
    Format: {"nba.com": [{"rank": 1, "team": "Lakers", ...}], "espn.com": [...]}
    """

    event_type: Literal["event.power_ranking"] = "event.power_ranking"
    default_search_template: ClassVar[str] = "{sport} power rankings"

    rankings: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)

    @classmethod
    def _build_llm_prompt(cls, context: str) -> str:
        return f"""Extract NBA power rankings from these search results. Return JSON only.

Results:
{context}

Format:
{{
  "nba.com": [{{"rank": 1, "team": "Lakers", "record": "15-5", "notes": "..."}}],
  "espn.com": [{{"rank": 1, "team": "Lakers", "record": "15-5", "notes": "..."}}]
}}

Rules:
- Each team appears ONCE per source
- Extract full ranking, all 30 teams should be included
- Use URL domain as key (e.g., "nba.com")
- Skip sources without clear rankings"""

    @classmethod
    def _parse_llm_response(
        cls, response: dict[str, Any], query: str, context: GameContext
    ) -> PowerRankingEvent | None:
        extracted = extract_json_from_dashscope_response(response, expected_type=dict)

        if not extracted:
            logger.warning(
                "Failed to extract power rankings: query=%s, status=%d",
                query,
                response.get("status_code", 0),
            )

        rankings: dict[str, list[dict[str, Any]]] = {}
        if extracted and isinstance(extracted, dict):
            cleaned_rankings: dict[str, list[dict[str, Any]]] = {}
            for source, teams in extracted.items():
                if not isinstance(teams, list):
                    continue

                seen_teams: set[str] = set()
                valid_teams = []
                for team_data in teams:
                    if not isinstance(team_data, dict):
                        continue

                    team_name = team_data.get("team", "").strip()
                    if not team_name:
                        continue

                    if team_name.lower() in seen_teams:
                        continue

                    seen_teams.add(team_name.lower())
                    valid_teams.append(team_data)

                if len(valid_teams) >= 1:
                    cleaned_rankings[source] = valid_teams

            rankings = cleaned_rankings

        return cls(
            query=query,
            rankings=rankings,
        )


@register_event
class ExpertPredictionEvent(WebSearchEventMixin, WebSearchInsightEvent):
    """Expert predictions from web search + LLM processing.

    Format: [{"source": "nba.com", "expert": "...", "prediction": "..."}]
    """

    event_type: Literal["event.expert_prediction"] = "event.expert_prediction"
    default_search_template: ClassVar[str] = "{sport} expert predictions for {teams}"

    predictions: list[dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def _build_llm_prompt(cls, context: str) -> str:
        return f"""Based on the following web search results about NBA expert predictions, extract structured prediction data.

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

    @classmethod
    def _parse_llm_response(
        cls, response: dict[str, Any], query: str, context: GameContext
    ) -> ExpertPredictionEvent | None:
        extracted = extract_json_from_dashscope_response(response, expected_type=list)

        if not extracted:
            logger.warning(
                "Failed to extract expert predictions: query=%s, status=%d",
                query,
                response.get("status_code", 0),
            )

        predictions: list[dict[str, Any]] = []
        if extracted and isinstance(extracted, list):
            predictions = [p if isinstance(p, dict) else {} for p in extracted]

        return cls(
            query=query,
            predictions=predictions,
        )


__all__ = [
    "ExpertPredictionEvent",
    "GameContext",
    "InjuryReportEvent",
    "PowerRankingEvent",
    "WebSearchEventMixin",
    "WebSearchInsightEvent",
    "WebSearchIntent",
]
