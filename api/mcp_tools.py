"""Tool registry and dispatch for the parsival MCP server (IFC-PV-001).

Tools register with the :func:`tool` decorator, which records the callable,
its JSON Schema and its description in module-level dicts.  ``dispatch``
invokes a tool by keyword expansion, so **the schema's property names are the
wire contract** (CON-PV-008): renaming a parameter breaks callers even when
the tool name is unchanged.  ``tests/test_mcp.py::test_schema_matches_signature``
pins that invariant, because a test that calls the Python function directly
cannot see the mismatch.

This module must never import ``app`` — that would create an import cycle,
since ``app`` mounts the router that imports this module.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import mcp_sql

TOOL_REGISTRY: dict[str, Callable[..., Any]] = {}
TOOL_SCHEMAS: dict[str, dict] = {}
TOOL_DESCRIPTIONS: dict[str, str] = {}

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def tool(name: str, description: str, schema: dict) -> Callable:
    """Register a function as an MCP tool.

    Args:
        name: Wire name, ``noun.verb`` by convention.
        description: One line shown to the calling agent in ``tools/list``.
        schema: JSON Schema for the arguments object.

    Returns:
        The undecorated function, so it stays directly callable in tests.
    """

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        TOOL_REGISTRY[name] = fn
        TOOL_SCHEMAS[name] = schema
        TOOL_DESCRIPTIONS[name] = description
        return fn

    return deco


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

    Args:
        name: Registered tool name.
        arguments: Keyword arguments from the MCP ``tools/call`` params.

    Returns:
        Whatever the tool returns; must be JSON-serialisable.
    """
    return TOOL_REGISTRY[name](**arguments)


# ── schema.describe ───────────────────────────────────────────────────────────


@tool(
    "schema.describe",
    "List database tables, or describe one table's columns and indexes.",
    {
        "type": "object",
        "properties": {
            "table": {"type": "string", "description": "Table to describe; omit to list all."}
        },
        "required": [],
    },
)
def schema_describe(table: str | None = None) -> dict:
    """Return table names, or one table's columns and indexes.

    Args:
        table: Table to describe.  When None, returns the table list.

    Returns:
        ``{"tables": [...]}`` or ``{"table", "columns", "indexes"}``.

    Raises:
        ValueError: If ``table`` is not an existing table.  The name is checked
            against ``sqlite_master`` before it is interpolated, because PRAGMA
            does not accept bound parameters.
    """
    c = mcp_sql.ro_conn()
    names = [
        r["name"]
        for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    ]
    if table is None:
        return {"tables": names}

    if table not in names or not _IDENT_RE.match(table):
        raise ValueError(f"unknown table: {table}")

    columns = [
        {"name": r["name"], "type": r["type"], "notnull": bool(r["notnull"]), "pk": bool(r["pk"])}
        for r in c.execute(f"PRAGMA table_info({table})").fetchall()
    ]
    indexes = [r["name"] for r in c.execute(f"PRAGMA index_list({table})").fetchall()]
    return {"table": table, "columns": columns, "indexes": indexes}
