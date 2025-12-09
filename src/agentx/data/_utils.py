"""Utility functions for data processing, including LLM integrations."""

from __future__ import annotations

import asyncio
import json
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
    **kwargs: Any,
) -> dict[str, Any]:
    """Call Dashscope Generation API asynchronously.
    
    Args:
        prompt: The prompt to send to the model
        model: Model name to use (default: "qwen-turbo")
        **kwargs: Additional parameters to pass to Generation.call
        
    Returns:
        Response dictionary from Dashscope API
        
    Raises:
        ImportError: If Dashscope SDK is not installed
        RuntimeError: If Dashscope is not initialized (api_key not set)
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
    
    # Run in thread pool since Dashscope SDK is synchronous
    return await asyncio.to_thread(_call)


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
        return None
    except Exception as e:
        print(f"DEBUG: Could not parse JSON: {e}")
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
