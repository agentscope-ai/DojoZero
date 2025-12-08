"""Web Search ExternalAPI implementation with MCP service integration."""

from typing import Any, Callable

from agentx.data._stores import ExternalAPI


class MCPSearchAdapter:
    """Adapter for MCP search services."""
    
    def __init__(
        self,
        mcp_search_fn: Callable | None = None,
        provider: str = "tavily",
    ):
        """Initialize MCP search adapter.
        
        Args:
            mcp_search_fn: Function to call MCP search tool
            provider: Search provider ("tavily" or "bailian")
        """
        self.mcp_search_fn = mcp_search_fn
        self.provider = provider
    
    async def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Perform web search using MCP service."""
        if not self.mcp_search_fn:
            raise ValueError("MCP search function not provided")
        
        search_params = {
            "query": query,
            "max_results": max_results,
            **kwargs,
        }
        
        import asyncio
        import inspect
        
        if inspect.iscoroutinefunction(self.mcp_search_fn):
            result = await self.mcp_search_fn(**search_params)
        else:
            result = await asyncio.to_thread(self.mcp_search_fn, **search_params)
        
        return self._normalize_results(result, query)
    
    def _normalize_results(self, mcp_result: dict[str, Any], query: str) -> dict[str, Any]:
        """Normalize MCP search results to standard format."""
        if self.provider == "tavily":
            results = mcp_result.get("results", [])
            normalized = []
            for item in results:
                normalized.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                })
            return {
                "query": query,
                "results": normalized,
                "total_results": len(normalized),
            }
        elif self.provider == "bailian":
            results = mcp_result.get("results", []) or mcp_result.get("data", [])
            normalized = []
            for item in results:
                normalized.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("snippet", "") or item.get("description", ""),
                })
            return {
                "query": query,
                "results": normalized,
                "total_results": len(normalized),
            }
        return {"query": query, "results": [], "total_results": 0}


class WebSearchAPI(ExternalAPI):
    """Web Search API implementation with MCP service support."""
    
    def __init__(
        self,
        api_key: str | None = None,
        use_mcp: bool = True,
        mcp_search_fn: Callable | None = None,
        mcp_provider: str = "tavily",
    ):
        """Initialize Web Search API."""
        self.api_key = api_key
        self.use_mcp = use_mcp
        
        if use_mcp:
            self.mcp_adapter = MCPSearchAdapter(
                mcp_search_fn=mcp_search_fn,
                provider=mcp_provider,
            )
        else:
            self.mcp_adapter = None
    
    async def fetch(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fetch search results."""
        if endpoint == "search":
            query = params.get("query", "") if params else ""
            
            if not query:
                return {"query": "", "results": [], "total_results": 0}
            
            if self.use_mcp and self.mcp_adapter:
                try:
                    max_results = params.get("max_results", 10) if params else 10
                    return await self.mcp_adapter.search(
                        query=query,
                        max_results=max_results,
                    )
                except Exception as e:
                    print(f"MCP search failed: {e}, falling back to simulated results")
            
            # Fallback: Simulated search results
            return {
                "query": query,
                "results": [
                    {"title": f"Result 1 for {query}", "url": "https://example.com/1", "snippet": "..."},
                    {"title": f"Result 2 for {query}", "url": "https://example.com/2", "snippet": "..."},
                ],
                "total_results": 2,
            }
        
        return {}
    
    async def place_bet(self, market_id: str, outcome: str, amount: float) -> dict[str, Any]:
        """Not applicable for search API."""
        raise NotImplementedError("Web Search API does not support betting")

