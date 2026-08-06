"""Tool registry and dispatch for the parsival MCP server (IFC-PV-001)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

TOOL_REGISTRY: dict[str, Callable[..., Any]] = {}
TOOL_SCHEMAS: dict[str, dict] = {}
TOOL_DESCRIPTIONS: dict[str, str] = {}


def tool_specs() -> list[dict]:
    """Return the MCP ``tools/list`` payload for every registered tool."""
    return [
        {
            "name": name,
            "description": TOOL_DESCRIPTIONS[name],
            "inputSchema": TOOL_SCHEMAS[name],
        }
        for name in TOOL_REGISTRY
    ]


def dispatch(name: str, arguments: dict) -> Any:
    """Invoke a registered tool by keyword expansion.

    Argument names are therefore part of the wire contract (CON-PV-008).

    Args:
        name: Registered tool name.
        arguments: Keyword arguments from the MCP ``tools/call`` params.

    Returns:
        Whatever the tool returns; must be JSON-serialisable.
    """
    return TOOL_REGISTRY[name](**arguments)
