"""Toolkit creation helpers."""

import functools
import inspect
from typing import Any, Callable

from agentscope.message import TextBlock
from agentscope.tool import Toolkit, ToolResponse


def tool(func: Callable) -> Callable:
    """Decorator that wraps a function to return ToolResponse."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> ToolResponse:
        result = await func(*args, **kwargs)
        return ToolResponse(content=[TextBlock(type="text", text=str(result))])

    # Copy signature for agentscope introspection
    wrapper.__signature__ = inspect.signature(func)  # type: ignore
    return wrapper


def create_toolkit(tools: list[Callable]) -> Toolkit:
    """Create a Toolkit from a list of tool functions.

    Example:
        toolkit = create_toolkit(broker.agent_tools(agent_id))
    """
    toolkit = Toolkit()
    for t in tools:
        toolkit.register_tool_function(t)
    return toolkit
