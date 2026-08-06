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

import sqlite3

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
