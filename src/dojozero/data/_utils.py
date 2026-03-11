"""Utility functions for data processing, including LLM integrations."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

# Try to load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# Try to import Dashscope
try:
    import dashscope
    from dashscope import Generation

    DASHSCOPE_AVAILABLE = True
except ImportError:
    DASHSCOPE_AVAILABLE = False
    dashscope = None  # type: ignore[assignment]
    Generation = None  # type: ignore[assignment, misc]

# Logger for Dashscope API calls
logger = logging.getLogger(__name__)


def initialize_dashscope(api_key: str | None = None) -> str:
    """Initialize Dashscope SDK with API key.

    Args:
        api_key: Dashscope API key (defaults to DOJOZERO_DASHSCOPE_API_KEY env var)

    Returns:
        The API key that was used

    Raises:
        ImportError: If Dashscope SDK is not installed
        ValueError: If API key is not provided and not found in environment
    """
    if not DASHSCOPE_AVAILABLE:
        raise ImportError(
            "Dashscope SDK not installed. Install with: pip install dashscope"
        )

    resolved_api_key = api_key or os.getenv("DOJOZERO_DASHSCOPE_API_KEY")
    if not resolved_api_key:
        raise ValueError(
            "DOJOZERO_DASHSCOPE_API_KEY not provided and not found in environment variables. "
            "Please set it in .env file or pass as parameter."
        )

    dashscope.api_key = resolved_api_key  # type: ignore[attr-defined]
    return resolved_api_key


async def call_dashscope_model(
    prompt: str,
    model: str = "qwen-turbo",
    max_retries: int = 3,
    retry_delay_base: float = 1.0,
    **kwargs: Any,
) -> dict[str, Any]:
    """Call Dashscope Generation API asynchronously with retry logic.

    Args:
        prompt: The prompt to send to the model
        model: Model name to use (default: "qwen-turbo")
        max_retries: Maximum number of retry attempts (default: 3)
        retry_delay_base: Base delay in seconds for exponential backoff (default: 1.0)
        **kwargs: Additional parameters to pass to Generation.call

    Returns:
        Response dictionary from Dashscope API

    Raises:
        ImportError: If Dashscope SDK is not installed
        RuntimeError: If Dashscope is not initialized (api_key not set)
        Exception: If all retry attempts fail
    """
    if not DASHSCOPE_AVAILABLE:
        raise ImportError(
            "Dashscope SDK not installed. Install with: pip install dashscope"
        )

    if not dashscope.api_key:  # type: ignore[attr-defined]
        raise RuntimeError(
            "Dashscope not initialized. Call initialize_dashscope() first."
        )

    def _call() -> dict[str, Any]:
        """Synchronous wrapper for Dashscope API call."""
        response = Generation.call(  # type: ignore[attr-defined]
            model=model,
            prompt=prompt,
            **kwargs,
        )
        return response  # type: ignore[no-any-return]

    # Retry logic with exponential backoff
    last_exception: Exception | None = None
    prompt_preview = prompt[:100] + "..." if len(prompt) > 100 else prompt

    for attempt in range(max_retries):
        try:
            if attempt == 0:
                logger.debug(
                    "Calling Dashscope API: model=%s, prompt_preview=%s",
                    model,
                    prompt_preview,
                )
            else:
                logger.info(
                    "Retrying Dashscope API call (attempt %d/%d): model=%s",
                    attempt + 1,
                    max_retries,
                    model,
                )

            # Run in thread pool since Dashscope SDK is synchronous
            response = await asyncio.to_thread(_call)

            # Log response status
            status_code = response.get("status_code", 0)
            if status_code == 200:
                output_length = len(str(response.get("output", {}).get("text", "")))
                logger.debug(
                    "Dashscope API call succeeded: model=%s, status=%d, output_length=%d",
                    model,
                    status_code,
                    output_length,
                )
                return response
            else:
                error_msg = response.get("message", "Unknown error")
                logger.warning(
                    "Dashscope API returned error: model=%s, status=%d, message=%s",
                    model,
                    status_code,
                    error_msg,
                )
                # Treat non-200 status as retryable error
                last_exception = RuntimeError(
                    f"API returned status {status_code}: {error_msg}"
                )

        except Exception as e:
            last_exception = e
            logger.warning(
                "Dashscope API call failed (attempt %d/%d): model=%s, error=%s",
                attempt + 1,
                max_retries,
                model,
                str(e),
            )

        # If not the last attempt, wait before retrying
        if attempt < max_retries - 1:
            delay = retry_delay_base * (2**attempt)  # Exponential backoff: 1s, 2s, 4s
            logger.debug("Waiting %.1f seconds before retry...", delay)
            await asyncio.sleep(delay)

    # All retries failed
    logger.error(
        "Dashscope API call failed after %d attempts: model=%s, error=%s",
        max_retries,
        model,
        str(last_exception) if last_exception else "Unknown error",
    )
    raise last_exception or RuntimeError("Dashscope API call failed after all retries")


def extract_json_from_dashscope_response(
    response: dict[str, Any], expected_type: type[dict] | type[list] = dict
) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Extract and parse JSON from Dashscope API response.

    Handles:
    - Markdown code blocks (```json ... ```)
    - Plain JSON text
    - Incomplete JSON (with repair logic)

    Args:
        response: Dashscope API response dictionary
        expected_type: Expected JSON type (dict or list). Defaults to dict.

    Returns:
        Parsed JSON object (dict or list) or None if extraction/parsing fails
    """
    if response.get("status_code") != 200:
        return None

    # Extract text from response structure: response["output"]["text"]
    output = response.get("output", {})
    if isinstance(output, dict):
        full_text = output.get("text", "").strip()
    else:
        full_text = str(output).strip()

    if not full_text:
        return None

    # Try to extract JSON from markdown code blocks first
    json_str = None

    # Pattern depends on expected type
    if expected_type is list:
        # Look for JSON array in markdown
        code_block_match = re.search(
            r"```(?:json)?\s*(\[.*?\])\s*```", full_text, re.DOTALL
        )
    else:
        # Look for JSON object in markdown
        code_block_match = re.search(
            r"```(?:json)?\s*(\{.*?\})\s*```", full_text, re.DOTALL
        )

    if code_block_match:
        json_str = code_block_match.group(1).strip()
    else:
        # Find JSON with balanced braces/brackets
        if expected_type is list:
            bracket_start = full_text.find("[")
            if bracket_start != -1:
                json_str = _extract_balanced_json(full_text, bracket_start, "[", "]")
        else:
            brace_start = full_text.find("{")
            if brace_start != -1:
                json_str = _extract_balanced_json(full_text, brace_start, "{", "}")

    if not json_str:
        return None

    # Try parsing
    try:
        parsed = json.loads(json_str)
        # Validate type
        if isinstance(parsed, expected_type):
            return parsed
        logger.debug(
            "Parsed JSON type mismatch: expected=%s, got=%s",
            expected_type.__name__,
            type(parsed).__name__,
        )
        return None
    except Exception as e:
        logger.debug("Could not parse JSON from Dashscope response: %s", e)
        return None


def _extract_balanced_json(
    text: str, start: int, open_char: str, close_char: str
) -> str | None:
    """Extract JSON with balanced braces/brackets from text.

    Args:
        text: Text to search in
        start: Starting position (where open_char was found)
        open_char: Opening character ("{" or "[")
        close_char: Closing character ("}" or "]")

    Returns:
        Extracted JSON string or None
    """
    count = 0
    bracket_count = 0  # For nested arrays in objects
    end = -1
    in_string = False
    escape_next = False

    for i in range(start, len(text)):
        char = text[i]

        if escape_next:
            escape_next = False
            continue

        if char == "\\":
            escape_next = True
            continue

        if char == '"' and not escape_next:
            in_string = not in_string
            continue

        if not in_string:
            if char == open_char:
                count += 1
            elif char == close_char:
                count -= 1
                if count == 0:
                    end = i
                    break
            elif char == "[":
                bracket_count += 1
            elif char == "]":
                bracket_count -= 1

    if end != -1:
        return text[start : end + 1].strip()
    elif count > 0:
        # Incomplete JSON - don't try to close it here, let repair function handle it
        # Just return what we have so repair can work on it
        return text[start:].strip()

    return None


async def summarize_content(
    content: str,
    content_type: str = "content",
    max_length: int = 500,
    model: str = "qwen-turbo",
    game_context: dict | None = None,
) -> str | None:
    """Summarize content with relevance filtering. Returns None if irrelevant."""
    if not content or not content.strip():
        return None

    # Skip obvious junk
    junk = ["javascript is disabled", "please enable javascript", "supported browser"]
    if any(j in content.lower() for j in junk):
        return None

    # No game context and short content: return as-is
    if len(content) <= max_length and game_context is None:
        return content

    try:
        initialize_dashscope()

        context_hint = ""
        if game_context:
            home = game_context.get("home_team", "")
            away = game_context.get("away_team", "")
            date = game_context.get("game_date", "")
            # ---- NEW: optional fields, backward compatible ----
            prev_game = game_context.get(
                "prev_game", ""
            )  # e.g. "DEN lost to OKC 126-129 on 2026-03-10"
            spread = game_context.get("spread", "")  # e.g. "DEN -4.5"
            total = game_context.get("total", "")  # e.g. "O/U 218.5"

            extra_context = ""
            if prev_game:
                extra_context += f"\nPREVIOUS GAME: {prev_game}"
            if spread or total:
                extra_context += f"\nCURRENT LINE: {spread} | {total}"

            context_hint = f"""
TARGET GAME: {away} at {home} on {date}.{extra_context}
Team roster reference: remember which players belong to which team. Do not confuse them.

RELEVANCE FILTER (apply strictly):
**HIGHLY RELEVANT (ALWAYS KEEP):**
- Both "{home}" AND "{away}" mentioned together (strong matchup signal)
- Time keywords ("tonight", "today", "this evening") + either team name
- Betting signals: "model loves X", "betting on X tonight", betting analysis for TARGET game
- Game info: tip-off time, broadcast channel, game previews for TARGET game
- Injury reports explicitly for "tonight's game" or "tomorrow's game" when date matches {date}

**IRRELEVANT (DISCARD):**
- Content about {home} or {away} playing a DIFFERENT opponent (e.g., "{home} vs Spurs", "{away} vs Lakers") - UNLESS it contains injury/fatigue/rotation info affecting tonight
- Betting lines, ATS records, or spreads for OTHER matchups (even if they mention {home} or {away})
- Historical stats, past games, season records without tonight's context
- Purely promotional content, fan engagement polls, lifestyle/fashion posts, links without text, emoji-only posts, trivia questions

**CONDITIONALLY RELEVANT:**
- Results or stats from past games: ONLY if they directly indicate player's current status (e.g., injury sustained in that game) or show fatigue/workload affecting tonight
- Broadcast/schedule info: KEEP if it's for TARGET game, skip if it's generic or for other games

**CRITICAL RULE:** If a tweet mentions {home} or {away} but is clearly about a DIFFERENT opponent, it is IRRELEVANT — do not include it regardless of recency, unless it contains injury/fatigue/rotation info affecting tonight.

If ALL content is irrelevant, respond with exactly: IRRELEVANT"""

        prompt = f"""You are a sports betting research assistant. Analyze the following {content_type} and extract actionable intelligence for pre-game analysis.
{context_hint}

--- START CONTENT ---
{content}
--- END CONTENT ---

STEP-BY-STEP INSTRUCTIONS (follow strictly):

STEP 1 - EXTRACT: Read every item above. Extract the core fact from each one. Pay attention to:
  - **BETTING SIGNALS** (HIGHEST PRIORITY): Model picks, betting recommendations, "loves X tonight", "fade/follow", betting analysis mentions for TARGET game
  - **GAME INFO**: Tip-off time, broadcast channel, "tonight", "today", game previews/announcements for TARGET game
  - Injury status and official designations (preserve exact terms: "probable", "questionable", "out", "day-to-day")
  - Player availability, warmup reports, minutes restrictions
  - Coaching quotes hinting at game plan, rotation, or matchup strategy
  - Rest, load management, back-to-back fatigue signals
  - Recent performance that indicates form/momentum

STEP 2 - CONTEXT CHECK (CRITICAL): For each extracted fact, determine if it's about the TARGET game:
  **TARGET GAME indicators (KEEP these):**
  - Both team names ({home} AND {away}) appear together
  - Time keywords: "tonight", "today", "this evening" combined with either team name
  - Betting analysis explicitly mentioning "{home} vs {away}" or "{away} at {home}" for tonight
  - Game previews, tip-off times, broadcast info for tonight's game
  - Injury reports labeled "ahead of tomorrow's game" or "tonight's game" when date matches TARGET date
  
  **DIFFERENT GAME indicators (DISCARD unless injury/fatigue info):**
  - Mentions {home} or {away} but playing a DIFFERENT opponent (e.g., "{home} vs Spurs", "{away} vs Lakers")
  - Historical stats, past games, season records without tonight's context
  - Betting lines/ATS records for OTHER matchups
  - Player performance from OTHER games (unless it reveals injury/fatigue affecting tonight)
  
  **If DIFFERENT game but contains injury/fatigue/rotation info relevant to tonight:**
  - Keep it and prefix with "(vs OPPONENT)" to show it's from a different game
  - Example: "(vs OKC) Jokic played 38 minutes in emotional loss" - relevant if it shows fatigue

STEP 3 - DEDUPLICATE: If multiple items describe the same event, keep ONLY ONE.
  Dedup rules:
  - Same game with slightly different scores (e.g. 126-115 vs 123-115 for same matchup) = same event, keep the one with more detail
  - Same game recap posted multiple times = same event
  - A live update and a final score for the same game = keep only the final
  - A coach quote and a summary of that quote = keep the one with the actual quote
  - Multiple betting picks for same game = keep the most specific one (e.g., "model loves X" > generic betting hub)

STEP 4 - REWRITE with precision:
  - For BETTING items: Preserve exact wording of model picks and recommendations. Example: "Model loves Rockets against Nuggets tonight" NOT "Rockets are favored"
  - For INJURY items: KEEP official status terms exactly (probable, questionable, out, day-to-day) and specific body part. Example: "Murray listed probable with left ankle sprain" NOT "Murray might miss the game due to injury"
  - For GAME INFO: Include tip-off time and broadcast channel if mentioned
  - For all other categories: summarize in your own words, one concise sentence per fact.
  - If a fact is from a DIFFERENT game, prefix with the opponent. Example: "(vs OKC) Jokic played 38 minutes in emotional loss"

STEP 5 - CATEGORIZE AND SORT: Label each fact and sort by this priority order:
  1. BETTING - model picks, betting recommendations, "loves X tonight" signals for TARGET game
  2. GAME INFO - tip-off time, broadcast channel, game previews/announcements for TARGET game
  3. INJURY - injuries, official injury report designations, players ruled out, shutdowns
  4. LINEUP - confirmed starters, rotation changes, minutes restrictions, upgrades/downgrades
  5. RESULT - recent game scores relevant to momentum/fatigue (NOT the target game's schedule)
  6. PERFORMANCE - standout stat lines from recent games indicating current form
  7. STRATEGY - coaching decisions, rotation patterns, tactical notes, matchup quotes
  8. DRAFT - lottery implications, tank updates (only if relevant to team motivation)

OUTPUT FORMAT (plain text only - no markdown, no bold, no asterisks, no emojis, no hashtags):
Output ONLY the KEY POINTS and SIGNAL sections below. Nothing else - no preamble, no summary paragraph.

KEY POINTS:
- [CATEGORY] Concise summary sentence (YYYY-MM-DD)

SIGNAL:
Decision-making insights based ONLY on the KEY POINTS listed above. Do NOT introduce information not in KEY POINTS. Do NOT confuse different games or matchups. Include temporal context if relevant (e.g., "Recent fatigue from back-to-back suggests...", "Injury timeline indicates..."). If betting signals are present, highlight their implications.

EXAMPLE OUTPUT (for reference only, do not copy):
KEY POINTS:
- [BETTING] Model loves Rockets against Nuggets tonight (2026-03-11)
- [GAME INFO] Tip-off 9pm ET, broadcast on ESPN (2026-03-11)
- [INJURY] Watson out with right hamstring strain (2026-03-11)
- [INJURY] Murray listed probable with left ankle sprain (2026-03-11)
- [INJURY] Cameron Johnson listed probable with back spasms (2026-03-11)
- [LINEUP] Braun expected to absorb Watson's wing minutes (2026-03-11)
- [RESULT] (vs OKC) DEN lost 126-129 in a physical, emotional game; Jokic played 38 min (2026-03-10)
- [PERFORMANCE] (vs OKC) Jokic had 35/12/8 but looked fatigued late (2026-03-10)

SIGNAL:
Model favors Rockets tonight despite Nuggets being home favorites. Multiple injury concerns (Watson out, Murray/Cameron probable) combined with Jokic's 38-minute workload in yesterday's emotional loss suggest potential fatigue and rotation adjustments. The model's pick may reflect these depth and fatigue factors."""

        response = await call_dashscope_model(prompt, model=model)

        if response.get("status_code") == 200:
            summary = response.get("output", {}).get("text", "").strip()
            if not summary:
                return None
            # Check irrelevant
            if "IRRELEVANT" in summary.upper().split("\n")[0]:
                return None
            # Clean up any markdown artifacts the model might add despite instructions
            summary = summary.replace("**", "").replace("##", "").replace("# ", "")
            return summary

        return content
    except Exception as e:
        logger.warning("Failed to generate summary for %s: %s", content_type, e)
        return content
