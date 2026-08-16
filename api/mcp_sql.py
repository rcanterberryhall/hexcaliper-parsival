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

Consequently, connection-level state is installed once at creation and never written
again.  Calling ``set_progress_handler`` or ``set_authorizer`` per request deadlocks the
interpreter: the thread already inside ``sqlite3_step`` holds the connection mutex and
needs the GIL to run its Python callback, while the thread installing the hook holds the
GIL and needs the connection mutex.  Neither yields, and because the stuck thread keeps
the GIL the whole process stops -- FastAPI included.  Per-request state belongs in
``_local`` instead.

Installing the hooks once is necessary but not sufficient.  Serialized mode makes
concurrent use of one connection *correct*, not *deadlock-free*: a thread entering
``execute`` blocks on the connection mutex while still holding the GIL, which is the
same inversion by a different door.  ``run_query`` therefore holds ``_QUERY_LOCK``
across its execute and fetch.  The window is timing-dependent and widens sharply as
cores get scarcer -- it was invisible on a 72-core developer machine and hung every
run on a two-core CI runner, so the regression test pins its own CPU affinity.
"""

from __future__ import annotations

import re
import sqlite3
import threading
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


_local = threading.local()

# Serialises the execute/fetch span.  Installing the hooks once removed the
# per-request half of the inversion, but not the half that ``execute`` itself
# creates: a thread entering the driver holds the GIL while it waits on the
# shared connection's mutex, and the thread already inside ``sqlite3_step``
# cannot run its progress-handler callback without that GIL.  Neither yields.
#
# A Python lock is the fix rather than a workaround, because
# ``Lock.acquire`` releases the GIL while it blocks -- a waiting thread holds
# nothing the running one needs.  It costs no throughput: these queries already
# serialise on the connection mutex, so the lock only makes the existing
# serialisation explicit and safe.
_QUERY_LOCK = threading.Lock()

_PROGRESS_INTERVAL = 10_000

# Everything a SELECT legitimately needs.  SQLITE_RECURSIVE is required for
# WITH RECURSIVE; omitting it would reject the CTEs this tool exists to run.
_READ_ONLY_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE,
    }
)

# The only pragmas reachable through this connection -- see _authorize_read_only.
_READ_ONLY_PRAGMAS = frozenset({"table_info", "index_list"})


def _deadline_expired() -> int:
    """Abort the calling thread's statement once its deadline has passed.

    Registered once per connection, so it must read the deadline from
    thread-local state rather than a closure: every thread sharing the
    connection runs this same callback for its own statement.

    Returns:
        1 to abort the running statement, 0 to let it continue.  A thread with
        no deadline set never aborts.
    """
    deadline = getattr(_local, "deadline", None)
    return 1 if deadline is not None and time.monotonic() > deadline else 0


def _authorize_read_only(
    action: int, arg1: str | None, arg2: str | None, dbname: str | None, source: str | None
) -> int:
    """Allow only the actions a SELECT needs; deny everything else.

    SQLite reports the *parsed* action, so this cannot be evaded by syntax the
    statement inspector fails to anticipate.  ``WITH cte AS (...) DELETE`` is
    the motivating case: it opens with a token ``validate_select`` accepts, yet
    SQLite reports SQLITE_DELETE here and it is refused.

    ``schema.describe`` shares this connection and introspects with PRAGMA, so
    the two pragmas it needs are allowed by name.  Naming them individually
    keeps ``journal_mode`` and friends denied; a blanket SQLITE_PRAGMA
    exemption would admit every pragma SQLite has.

    Args:
        action: One of the ``sqlite3.SQLITE_*`` authorizer action codes.
        arg1: For SQLITE_PRAGMA, the pragma name; otherwise table or view name.
        arg2: For SQLITE_PRAGMA, the pragma argument; otherwise column name.
        dbname: The database the action targets.
        source: Name of the innermost trigger or view responsible, if any.

    Returns:
        ``sqlite3.SQLITE_OK`` to allow, ``sqlite3.SQLITE_DENY`` to reject.
    """
    if action == sqlite3.SQLITE_PRAGMA:
        allowed = (arg1 or "").lower() in _READ_ONLY_PRAGMAS
        return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK if action in _READ_ONLY_ACTIONS else sqlite3.SQLITE_DENY


def ro_conn() -> sqlite3.Connection:
    """Return the cached read-only connection for the configured database.

    Both hooks are installed here, once, and never touched again -- see the
    module docstring for why mutating them per request deadlocks the process.
    They are attached before the connection is published to ``_RO_CONNS``, so
    no other thread can observe a partially configured connection and no lock
    is needed.

    Returns:
        A ``sqlite3.Connection`` opened ``mode=ro`` with ``Row`` factory, a
        read-only authorizer, and a deadline-aware progress handler.
    """
    path = config.DB_PATH
    c = _RO_CONNS.get(path)
    if c is None:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.set_progress_handler(_deadline_expired, _PROGRESS_INTERVAL)
        c.set_authorizer(_authorize_read_only)
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

    This is the first of two gates and the one that produces a readable error.
    It cannot see through a CTE, so ``WITH cte AS (...) DELETE`` passes here and
    is stopped by the connection's authorizer instead.

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
        ValueError: If the statement fails validation, either by inspection or
            because the connection's authorizer refused it.
        sqlite3.OperationalError: On a query that exceeds
            ``QUERY_TIMEOUT_SECONDS``, reported by SQLite as ``interrupted``.
    """
    validate_select(sql)
    capped = max(1, min(int(limit), MAX_LIMIT))
    c = ro_conn()

    # Thread-local, because the progress handler is shared by every thread on
    # this connection and each needs its own bound.
    _local.deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS
    try:
        # Held across the fetch as well as the execute: the statement is still
        # stepping during fetchmany, so releasing early would reopen the window.
        with _QUERY_LOCK:
            cur = c.execute(sql)
            # Fetch one extra row so truncation is detected rather than guessed.
            rows = cur.fetchmany(capped + 1)
            columns = [d[0] for d in cur.description] if cur.description else []
    except sqlite3.DatabaseError as exc:
        # Present an authorizer refusal as the fence rejecting the statement.
        # Left alone it surfaces as a bare "not authorized", which tells an MCP
        # client nothing about which statements are acceptable.
        if "not authorized" in str(exc):
            raise ValueError("only SELECT and WITH queries are allowed") from exc
        raise
    finally:
        _local.deadline = None

    truncated = len(rows) > capped
    rows = rows[:capped]
    return {
        "columns": columns,
        "rows": [list(r) for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }
