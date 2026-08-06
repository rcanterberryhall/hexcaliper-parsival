"""Read-only SQLite access for the parsival MCP server (PV-REQ-N-001).

A connection opened ``mode=ro`` is physically separate from ``db.py``'s shared
write connection.  Two consequences, both deliberate: a write attempted here
fails at the driver even if statement inspection is bypassed, and a long
analytical query cannot contend for the write lock (CON-PV-004, WAL is on).

Connections are cached per path because ``config.DB_PATH`` is set by the test
harness before import and differs from production.
"""

from __future__ import annotations

import sqlite3

import config

_RO_CONNS: dict[str, sqlite3.Connection] = {}


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
