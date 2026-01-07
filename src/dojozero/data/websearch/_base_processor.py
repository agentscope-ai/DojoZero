"""Base processor for Dashscope-powered web search processors."""

from __future__ import annotations

import logging
from typing import Any

from dojozero.data._models import DataEvent, EventTypes
from dojozero.data._processors import DataProcessor
from dojozero.data._utils import call_dashscope_model, initialize_dashscope
from dojozero.data.websearch._events import WebSearchIntent

logger = logging.getLogger(__name__)


class BaseDashscopeProcessor(DataProcessor):
    """Base processor for Dashscope-powered web search processors.

    Provides common functionality for processors that use Dashscope LLM
    to process web search results:
    - Dashscope initialization (api_key, model)
    - Intent-based should_process() with keyword fallback
    - Safe Dashscope API calling with error handling

    Subclasses should:
    - Set `intended_intent` class attribute (optional)
    - Set `fallback_keywords` class attribute (for non-intent routing)
    - Implement `process()` method with specific processing logic
    """

    # Subclasses should override these class attributes
    intended_intent: WebSearchIntent | None = None
    fallback_keywords: list[str] = []

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "qwen-turbo",
    ):
        """Initialize Dashscope processor.

        Args:
            api_key: Dashscope API key (defaults to DOJOZERO_DASHSCOPE_API_KEY env var)
            model: Dashscope model to use (default: "qwen-turbo")
        """
        # Initialize Dashscope (will raise if not available or key missing)
        self.api_key = initialize_dashscope(api_key)
        self.model = model

    def should_process(self, event: DataEvent) -> bool:
        """Check if this processor should handle the event.

        Uses a two-stage approach:
        1. Intent-based routing (if event has intent attribute)
        2. Keyword-based routing (fallback for backward compatibility)

        Args:
            event: Event to check

        Returns:
            True if processor should handle this event, False otherwise
        """
        # Only process raw web search events
        if event.event_type != EventTypes.RAW_WEB_SEARCH.value:
            return False

        # Stage 1: Intent-based routing (preferred)
        event_intent = getattr(event, "intent", None)
        if event_intent is not None:
            # Use base class intent checking (compares event intent with self.intended_intent)
            return super().should_process(event)

        # Stage 2: Keyword-based routing (fallback)
        query = getattr(event, "query", "")
        if not query:
            return False

        query_lower = query.lower()
        return any(kw in query_lower for kw in self.fallback_keywords)

    async def _call_dashscope_safe(
        self,
        prompt: str,
    ) -> dict[str, Any]:
        """Call Dashscope API with unified error handling.

        Args:
            prompt: Prompt to send to Dashscope

        Returns:
            Dashscope response dict with status_code and output/message
        """
        try:
            response = await call_dashscope_model(
                prompt=prompt,
                model=self.model,
            )
            return response
        except Exception as e:
            logger.error(
                "Dashscope call failed in %s: %s",
                self.__class__.__name__,
                e,
                exc_info=True,
            )
            return {"status_code": 500, "message": str(e)}

    async def process(self, event: DataEvent) -> DataEvent | None:
        """Process event and return result.

        Subclasses must implement this method.

        Args:
            event: Event to process

        Returns:
            Processed event or None
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement process() method"
        )
