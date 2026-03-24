"""Web Search ExternalAPI implementation with Tavily SDK integration."""

import os
from typing import Any, Callable

from dojozero.data._stores import ExternalAPI

# Try to load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, skip .env loading

# Try to import Tavily SDK
try:
    from tavily import TavilyClient

    TAVILY_AVAILABLE = True
except ImportError:
    TAVILY_AVAILABLE = False
    TavilyClient = None


class TavilySearchAdapter:
    """Adapter for Tavily Python SDK."""

    def __init__(self, api_key: str | None = None):
        """Initialize Tavily search adapter.

        Args:
            api_key: Tavily API key (defaults to DOJOZERO_TAVILY_API_KEY env var)
        """
        if not TAVILY_AVAILABLE:
            raise ImportError(
                "Tavily SDK not installed. Install with: pip install tavily-python"
            )

        self.api_key = api_key or os.getenv("DOJOZERO_TAVILY_API_KEY")
        if not self.api_key:
            raise ValueError(
                "DOJOZERO_TAVILY_API_KEY not provided and not found in environment variables. "
                "Please set it in .env file or pass as parameter."
            )

        if TavilyClient is None:
            raise ImportError("TavilyClient is not available")
        self.client = TavilyClient(api_key=self.api_key)

    async def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Perform web search using Tavily SDK.

        Args:
            query: Search query
            max_results: Maximum number of results
            **kwargs: Additional Tavily search parameters

        Returns:
            Search results in standardized format
        """
        import asyncio

        # Tavily SDK is synchronous, so we run it in a thread
        def _search():
            return self.client.search(
                query=query,
                max_results=max_results,
                **kwargs,
            )

        # Run synchronous Tavily call in thread pool
        result = await asyncio.to_thread(_search)

        # Normalize to our format
        return self._normalize_results(result, query)

    def _normalize_results(
        self, tavily_result: dict[str, Any], query: str
    ) -> dict[str, Any]:
        """Normalize Tavily SDK response to standard format.

        Args:
            tavily_result: Raw response from Tavily SDK
            query: Original search query

        Returns:
            Normalized result dictionary
        """
        results = tavily_result.get("results", [])
        normalized = []

        for item in results:
            # Prefer raw_content if available (more complete), otherwise use content
            content = item.get("raw_content") or item.get("content", "")
            normalized.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": content,
                }
            )

        return {
            "query": query,
            "results": normalized,
            "total_results": len(normalized),
        }


class WebSearchAPI(ExternalAPI):
    """Web Search API implementation with Tavily SDK support."""

    def __init__(
        self,
        api_key: str | None = None,
        use_tavily: bool = True,
        custom_search_fn: Callable | None = None,
    ):
        """Initialize Web Search API.

        Args:
            api_key: Tavily API key (defaults to DOJOZERO_TAVILY_API_KEY env var)
            use_tavily: Whether to use Tavily SDK (default: True)
            custom_search_fn: Custom search function (optional, for testing/mocking)
        """
        self.api_key = api_key
        self.use_tavily = use_tavily

        if use_tavily:
            try:
                self.tavily_adapter = TavilySearchAdapter(api_key=api_key)
            except (ImportError, ValueError) as e:
                print(f"Warning: Tavily SDK not available: {e}")
                self.tavily_adapter = None
        else:
            self.tavily_adapter = None

        self.custom_search_fn = custom_search_fn

    async def fetch(
        self, endpoint: str = "search", params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Fetch search results."""
        if endpoint == "search":
            query = params.get("query", "") if params else ""

            if not query:
                return {"query": "", "results": [], "total_results": 0}

            # Use custom search function if provided
            if self.custom_search_fn:
                import asyncio
                import inspect

                search_params = {
                    "query": query,
                    "max_results": params.get("max_results", 10) if params else 10,
                    **{k: v for k, v in (params or {}).items() if k != "query"},
                }

                if inspect.iscoroutinefunction(self.custom_search_fn):
                    result = await self.custom_search_fn(**search_params)
                else:
                    result = await asyncio.to_thread(
                        self.custom_search_fn, **search_params
                    )

                # Normalize result
                if isinstance(result, dict) and "results" in result:
                    return result
                if hasattr(result, "get"):
                    return {
                        "query": query,
                        "results": result.get("results", []),
                        "total_results": len(result.get("results", [])),
                    }
                else:
                    # Handle case where result is a list or other iterable
                    results_list = (
                        list(result)
                        if hasattr(result, "__iter__")
                        and not isinstance(result, (str, bytes))
                        else []
                    )
                    return {
                        "query": query,
                        "results": results_list,
                        "total_results": len(results_list),
                    }

            # Use Tavily SDK if available
            if self.use_tavily and self.tavily_adapter:
                max_results = params.get("max_results", 10) if params else 10
                # Extract Tavily-specific parameters
                tavily_params = {
                    k: v
                    for k, v in (params or {}).items()
                    if k
                    in [
                        "auto_parameters",
                        "topic",
                        "search_depth",
                        "chunks_per_source",
                        "include_answer",
                        "include_raw_content",
                        "include_images",
                        "include_image_descriptions",
                        "include_favicon",
                        "include_domains",
                        "exclude_domains",
                        "time_range",
                        "start_date",
                        "end_date",
                        "country",
                    ]
                }
                return await self.tavily_adapter.search(
                    query=query,
                    max_results=max_results,
                    **tavily_params,
                )

            # No Tavily adapter available
            raise ValueError(
                "Tavily SDK not available. "
                "Please install with: pip install tavily-python "
                "and set DOJOZERO_TAVILY_API_KEY in your .env file."
            )
        else:
            raise NotImplementedError(
                "Invalid endpoint. Please use 'search' endpoint. "
                "Use WebSearchAPI.fetch('search', {'query': 'your query', 'max_results': 10}) "
                "to search the web."
            )
