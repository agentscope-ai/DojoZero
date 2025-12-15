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
        api_key: Dashscope API key (defaults to DASHSCOPE_API_KEY env var)
        
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
    
    resolved_api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
    if not resolved_api_key:
        raise ValueError(
            "DASHSCOPE_API_KEY not provided and not found in environment variables. "
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
                last_exception = RuntimeError(f"API returned status {status_code}: {error_msg}")
                
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
            delay = retry_delay_base * (2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
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
    response: dict[str, Any],
    expected_type: type[dict] | type[list] = dict
) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Extract and parse JSON from Dashscope API response.
    
    Handles:
    - Markdown code blocks (```json ... ```)
    - Plain JSON text
    - Incomplete JSON (with repair logic)
    
    Args:
        response: Dashscope API response dictionary
        expected_type: Expected JSON type (dict or list). Defaults to dict.
        repair_incomplete: Whether to attempt repairing incomplete JSON. Defaults to True.
        
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
    if expected_type == list:
        # Look for JSON array in markdown
        code_block_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", full_text, re.DOTALL)
    else:
        # Look for JSON object in markdown
        code_block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", full_text, re.DOTALL)
    
    if code_block_match:
        json_str = code_block_match.group(1).strip()
    else:
        # Find JSON with balanced braces/brackets
        if expected_type == list:
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


def _extract_balanced_json(text: str, start: int, open_char: str, close_char: str) -> str | None:
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
        return text[start:end + 1].strip()
    elif count > 0:
        # Incomplete JSON - don't try to close it here, let repair function handle it
        # Just return what we have so repair can work on it
        return text[start:].strip()
    
    return None
