"""Read-only SQLite access for the parsival MCP server (PV-REQ-N-001).

A connection opened ``mode=ro`` is physically separate from ``db.py``'s shared
write connection.  Two consequences, both deliberate: a write attempted here
fails at the driver even if statement inspection is bypassed, and a long
analytical query cannot contend for the write lock (CON-PV-004, WAL is on).

Connections are cached per path because ``config.DB_PATH`` is set by the test
harness before import and differs from production.

THREADING: one shared connection per database path with ``check_same_thread=False``
is safe only when SQLite is compiled in serialized mode (``sqlite3.threadsafety == 3``).
FastAPI runs sync endpoints in a threadpool, so concurrent MCP requests from different
OS threads will call ``.execute()`` on the same connection. A non-serialized build would
require per-thread connections or a lock; the single-connection design is deliberate
and depends on the SQLite build.
"""

from __future__ import annotations

import re
import sqlite3
import time

import config

_RO_CONNS: dict[str, sqlite3.Connection] = {}


def _check_sqlite_threadsafety(threadsafety: int) -> None:
    """Validate that SQLite is built in serialized mode.

    Args:
        threadsafety: The value of ``sqlite3.threadsafety``.

    Raises:
        RuntimeError: If SQLite is not in serialized mode (value != 3).
    """
    if threadsafety != 3:
        raise RuntimeError(
            f"SQLite must be compiled in serialized mode (threadsafety=3) for MCP "
            f"read-only connections to be thread-safe; got threadsafety={threadsafety}. "
            f"See: https://www.sqlite.org/threadsafe.html"
        )


_check_sqlite_threadsafety(sqlite3.threadsafety)


def ro_conn() -> sqlite3.Connection:
    """Return the cached read-only connection for the configured database.

    Returns:
        A ``sqlite3.Connection`` opened ``mode=ro`` with ``Row`` factory.
    """
    path = config.DB_PATH
    c = _RO_CONNS.get(path)
    if c is None:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        c.row_factory = sqlite3.Row
        _RO_CONNS[path] = c
    return c


MAX_LIMIT = 500
DEFAULT_LIMIT = 200
QUERY_TIMEOUT_SECONDS = 10.0

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_LEADING_SELECT = re.compile(r"^(select|with)\b", re.I)


def validate_select(sql: str) -> str:
    """Return ``sql`` if it is a single read-only statement, else raise.

    Comments are stripped before inspection so a semicolon cannot be smuggled
    behind one.  The check is an allowlist — the statement must *begin* with
    SELECT or WITH — which is strictly safer than blocking known-bad verbs,
    because it also excludes PRAGMA, ATTACH and anything added to SQLite later.

    Args:
        sql: The caller-supplied statement.

    Returns:
        The original statement, unmodified, ready to execute.

    Raises:
        ValueError: If the statement is empty, multi-statement, or not a SELECT.
    """
    stripped = _BLOCK_COMMENT.sub(" ", sql or "")
    stripped = _LINE_COMMENT.sub(" ", stripped).strip()
    stripped = stripped.rstrip(";").strip()

    if not stripped:
        raise ValueError("empty query")
    if ";" in stripped:
        raise ValueError("multiple statements are not allowed")
    if not _LEADING_SELECT.match(stripped):
        raise ValueError("only SELECT and WITH queries are allowed")
    return sql


def run_query(sql: str, limit: int = DEFAULT_LIMIT) -> dict:
    """Execute a validated read-only query with a row cap and a time bound.

    Args:
        sql: Statement to run; validated before execution.
        limit: Maximum rows to return, clamped to ``MAX_LIMIT``.

    Returns:
        ``{"columns", "rows", "row_count", "truncated"}``.  ``truncated`` is
        True when more rows were available than were returned (PV-REQ-N-003).

    Raises:
        ValueError: If the statement fails validation.
        sqlite3.OperationalError: On a write attempt or a query that exceeds
            ``QUERY_TIMEOUT_SECONDS``.
    """
    validate_select(sql)
    capped = max(1, min(int(limit), MAX_LIMIT))
    c = ro_conn()

    deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS
    c.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        cur = c.execute(sql)
        # Fetch one extra row so truncation is detected rather than guessed.
        rows = cur.fetchmany(capped + 1)
        columns = [d[0] for d in cur.description] if cur.description else []
    finally:
        c.set_progress_handler(None, 0)

    truncated = len(rows) > capped
    rows = rows[:capped]
    return {
        "columns": columns,
        "rows": [list(r) for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }
