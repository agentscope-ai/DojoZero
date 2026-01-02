"""Demo: Using Tavily Python SDK for web search.

This demo shows how to use the Tavily Python SDK directly,
as an alternative to the MCP integration.
"""

import asyncio
import os

# Try to load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, skip .env loading

try:
    from tavily import TavilyClient
except ImportError:
    print("Tavily SDK not installed. Install with: pip install tavily-python")
    exit(1)

from agentx.data.websearch import WebSearchAPI


def demo_tavily_api():
    """Basic Tavily search demo."""
    print("=" * 70)
    print("TAVILY PYTHON SDK DEMO")
    print("=" * 70)
    print()

    # Get API key from environment
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        print("Error: TAVILY_API_KEY not found in environment variables.")
        print("Please set it in your .env file or export it.")
        return

    print("Step 1: Initialize Tavily Client")
    print("-" * 70)
    tavily_client = TavilyClient(api_key=api_key)
    print(f"  Initialized Tavily client with API key: {api_key[:10]}...")
    print()

    # Basic search
    print("Step 2: Basic Search")
    print("-" * 70)
    query = "Who is Leo Messi?"
    print(f"  Query: {query}")
    response = tavily_client.search(query)
    print(f"  Response type: {type(response)}")
    print(
        f"  Response keys: {list(response.keys()) if isinstance(response, dict) else 'N/A'}"
    )
    print()

    if isinstance(response, dict):
        print("Step 3: Search Results")
        print("-" * 70)
        results = response.get("results", [])
        print(f"  Total results: {len(results)}")
        print()

        for i, result in enumerate(results[:3], 1):  # Show first 3
            print(f"  Result {i}:")
            print(f"    Title: {result.get('title', 'N/A')}")
            print(f"    URL: {result.get('url', 'N/A')}")
            print(f"    Content: {result.get('content', 'N/A')[:100]}...")
            if "score" in result:
                print(f"    Score: {result.get('score', 'N/A')}")
            print()

        # Show other response fields
        if "answer" in response:
            print(f"  Answer: {response.get('answer', 'N/A')}")
            print()

        if "query" in response:
            print(f"  Query: {response.get('query', 'N/A')}")
            print()
    else:
        print(f"  Full response: {response}")
        print()


async def demo_websearch_integration():
    """Demonstrate websearch integration with Tavily SDK."""
    print("=" * 70)
    print("TAVILY SDK SEARCH DEMO")
    print("=" * 70)
    print()

    # Test WebSearchAPI Integraiton
    print("Testing WebSearchAPI:")
    print("-" * 70)
    api_tavily = WebSearchAPI(
        use_tavily=True,
    )

    try:
        result = await api_tavily.fetch(
            "search", {"query": "Lakers vs Celtics injury odds", "max_results": 5}
        )
        print(f"  Results: {result['total_results']}")
        if result["results"]:
            print(f"  First result: {result['results'][0].get('title', 'N/A')}")
            print(f"  URL: {result['results'][0].get('url', 'N/A')}")
            if len(result["results"]) > 1:
                print(f"  Second result: {result['results'][1].get('title', 'N/A')}")
    except Exception as e:
        print(f"  Error: {e}")
    print()

    # Test another query with WebSearchAPI
    print("Testing WebSearchAPI with different query:")
    print("-" * 70)
    try:
        result = await api_tavily.fetch(
            "search", {"query": "NBA injury updates", "max_results": 3}
        )
        print(f"  Results: {result['total_results']}")
        for i, res in enumerate(result["results"][:3], 1):
            print(f"  {i}. {res.get('title', 'N/A')}")
            print(f"     {res.get('url', 'N/A')}")
    except Exception as e:
        print(f"  Error: {e}")
    print()

    # Test advanced search with parameters
    print("Testing Tavily with advanced parameters:")
    print("-" * 70)
    try:
        result = await api_tavily.fetch(
            "search",
            {
                "query": "Polymarket prediction markets",
                "max_results": 3,
                "search_depth": "advanced",
                "topic": "general",
                "include_answer": True,
            },
        )
        print(f"  Results: {result['total_results']}")
        for i, res in enumerate(result["results"][:3], 1):
            print(f"  {i}. {res.get('title', 'N/A')}")
            print(f"     url: {res.get('url', 'N/A')}")
            print(f"     snippet: {res.get('snippet', 'N/A')}")
    except Exception as e:
        print(f"  Error: {e}")
    print()


if __name__ == "__main__":
    print("\n")
    demo_tavily_api()
    print("\n")
    asyncio.run(demo_websearch_integration())
    print("\n")
