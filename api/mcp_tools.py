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

import config
import db
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


# ── sql.query ─────────────────────────────────────────────────────────────────


@tool(
    "sql.query",
    "Run a read-only SELECT against the parsival database. Writes are refused.",
    {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "A single SELECT or WITH statement."},
            "limit": {
                "type": "integer",
                "description": f"Max rows (default {mcp_sql.DEFAULT_LIMIT}, max {mcp_sql.MAX_LIMIT}).",
            },
        },
        "required": ["sql"],
    },
)
def sql_query(sql: str, limit: int = mcp_sql.DEFAULT_LIMIT) -> dict:
    """Execute a read-only query.

    Args:
        sql: A single SELECT or WITH statement.
        limit: Maximum rows to return.

    Returns:
        ``{"columns", "rows", "row_count", "truncated"}``.
    """
    return mcp_sql.run_query(sql, limit)


# ── projects.list / cards.list ────────────────────────────────────────────────


@tool(
    "projects.list",
    "List every configured project with keywords, parent, shifts and card count.",
    {"type": "object", "properties": {}, "required": []},
)
def projects_list() -> dict:
    """Return the configured projects enriched with live board data.

    Returns:
        ``{"projects": [...]}``; each entry adds ``shifts`` and ``card_count``
        to the stored configuration.
    """
    with db.lock:
        shifts = db.list_project_shifts()
        counts = {
            r["project"]: r["n"]
            for r in db.conn()
            .execute("SELECT project, COUNT(*) AS n FROM lookahead_cards GROUP BY project")
            .fetchall()
        }

    by_project: dict[str, list] = {}
    for s in shifts:
        by_project.setdefault(s["project_tag"], []).append(s)

    out = []
    for p in config.PROJECTS:
        name = p.get("name", "")
        out.append(
            {
                "name": name,
                "parent": p.get("parent", ""),
                "description": p.get("description", ""),
                "keywords": p.get("keywords", []),
                "learned_keywords": p.get("learned_keywords", []),
                "channels": p.get("channels", []),
                "shifts": by_project.get(name, []),
                "card_count": counts.get(name, 0),
            }
        )
    return {"projects": out}


@tool(
    "cards.list",
    "List look-ahead cards over an arbitrary date range, with optional filters.",
    {
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "Project tag to filter by."},
            "start": {"type": "string", "description": "Range start, YYYY-MM-DD."},
            "end": {"type": "string", "description": "Range end, YYYY-MM-DD."},
            "status": {
                "type": "string",
                "description": "One of planned, in_progress, done, blocked.",
            },
        },
        "required": [],
    },
)
def cards_list(
    project: str | None = None,
    start: str | None = None,
    end: str | None = None,
    status: str | None = None,
) -> dict:
    """Return cards overlapping a date range.

    Unlike the board, which renders a fixed 14-day window, this accepts any
    range — the reason a multi-year contract schedule is usable at all.

    Args:
        project: Project tag to filter by.
        start: Range start, YYYY-MM-DD.
        end: Range end, YYYY-MM-DD.
        status: Card status to filter by.

    Returns:
        ``{"cards": [...], "count": int}``.
    """
    with db.lock:
        cards = db.list_lookahead_cards(project=project, start_date=start, end_date=end)
    if status:
        cards = [c for c in cards if c.get("status") == status]
    return {"cards": cards, "count": len(cards)}


@tool(
    "situations.list",
    "List situations with score and priority; dismissed ones are excluded by default.",
    {
        "type": "object",
        "properties": {
            "include_dismissed": {"type": "boolean", "description": "Include dismissed."},
            "limit": {"type": "integer", "description": "Max situations (default 50)."},
        },
        "required": [],
    },
)
def situations_list(include_dismissed: bool = False, limit: int = 50) -> dict:
    """Return situations ordered by score, highest first.

    Args:
        include_dismissed: Whether to include dismissed situations.
        limit: Maximum number to return.

    Returns:
        ``{"situations": [...], "count": int}``.
    """
    with db.lock:
        sits = db.get_all_situations(include_dismissed=include_dismissed)
    sits.sort(key=lambda s: s.get("score") or 0, reverse=True)
    capped = sits[: max(1, int(limit))]
    return {"situations": capped, "count": len(capped)}


@tool(
    "situations.get",
    "Fetch one situation with its contributing items and open actions.",
    {
        "type": "object",
        "properties": {
            "situation_id": {"type": "string", "description": "The situation's id."}
        },
        "required": ["situation_id"],
    },
)
def situations_get(situation_id: str) -> dict:
    """Return one situation together with the items that formed it.

    The contributing items are the evidence: a situation without them cannot
    be re-analysed by a caller that did not form it.

    Args:
        situation_id: The situation's id.

    Returns:
        The situation dict plus ``items`` and ``item_count``.

    Raises:
        ValueError: If no such situation exists.
    """
    with db.lock:
        sit = db.get_situation(situation_id)
        if not sit:
            raise ValueError(f"situation not found: {situation_id}")
        items = [db.get_item(i) for i in sit.get("item_ids", [])]

    resolved = [i for i in items if i]
    out = dict(sit)
    out["items"] = resolved
    out["item_count"] = len(resolved)
    return out
