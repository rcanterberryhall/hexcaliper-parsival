"""Tests for the parsival MCP server transport and auth (IFC-PV-001)."""

import json
import os
import sqlite3
import subprocess
import sys
from unittest.mock import patch

import config
import db
import mcp_sql
import mcp_tools
import pytest

TOKEN = "test-token-abc123"


def _rpc(client, method, params=None, token=TOKEN, id_=1):
    """POST one JSON-RPC message; token=None omits the header entirely."""
    body = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        body["params"] = params
    headers = {}
    if token is not None:
        headers["X-Parsival-MCP-Token"] = token
    return client.post("/mcp", json=body, headers=headers)


def test_missing_token_is_rejected(client):
    with patch.object(config, "MCP_TOKEN", TOKEN):
        r = _rpc(client, "initialize", token=None)
    assert r.status_code == 401


def test_wrong_token_is_rejected(client):
    with patch.object(config, "MCP_TOKEN", TOKEN):
        r = _rpc(client, "initialize", token="not-the-token")
    assert r.status_code == 401


def test_unset_token_fails_closed(client):
    """An unset MCP_TOKEN must reject everything, not disable the check."""
    with patch.object(config, "MCP_TOKEN", ""):
        r = _rpc(client, "initialize", token="anything")
    assert r.status_code == 401


def test_initialize_returns_protocol_and_server_info(client):
    with patch.object(config, "MCP_TOKEN", TOKEN):
        r = _rpc(client, "initialize")
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["serverInfo"]["name"] == "parsival-ops"
    assert "tools" in result["capabilities"]


def test_initialized_notification_returns_202(client):
    with patch.object(config, "MCP_TOKEN", TOKEN):
        r = _rpc(client, "notifications/initialized")
    assert r.status_code == 202


def test_unknown_method_returns_jsonrpc_error(client):
    with patch.object(config, "MCP_TOKEN", TOKEN):
        r = _rpc(client, "no/such/method")
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32601


def test_error_body_never_contains_the_token(client):
    with patch.object(config, "MCP_TOKEN", TOKEN):
        r = _rpc(client, "initialize", token="wrong")
    assert TOKEN not in r.text


def test_non_ascii_token_header_is_rejected_not_500(client):
    """A header byte >= 0x80 must fail auth cleanly, not crash compare_digest.

    Starlette decodes raw header bytes as latin-1, so a header containing a
    byte >= 0x80 reaches ``_authorised`` as a non-ASCII str. httpx's own
    header validation only accepts ASCII str values, so the raw byte tuple
    form is used here to bypass it and exercise what the real ASGI server
    would deliver.
    """
    with patch.object(config, "MCP_TOKEN", TOKEN):
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers=[(b"X-Parsival-MCP-Token", b"\xff\xfe")],
        )
    assert r.status_code == 401


def test_tools_list_returns_registered_tool_specs(client):
    def _noop():
        return None

    with (
        patch.dict(mcp_tools.TOOL_REGISTRY, {"noop": _noop}),
        patch.dict(mcp_tools.TOOL_SCHEMAS, {"noop": {"type": "object", "properties": {}}}),
        patch.dict(mcp_tools.TOOL_DESCRIPTIONS, {"noop": "Does nothing."}),
        patch.object(config, "MCP_TOKEN", TOKEN),
    ):
        r = _rpc(client, "tools/list")
    assert r.status_code == 200
    tools = r.json()["result"]["tools"]
    assert {
        "name": "noop",
        "description": "Does nothing.",
        "inputSchema": {"type": "object", "properties": {}},
    } in tools


def test_tools_call_success_returns_serialized_result(client):
    def _echo(value):
        return {"echo": value}

    with (
        patch.dict(mcp_tools.TOOL_REGISTRY, {"echo": _echo}),
        patch.object(config, "MCP_TOKEN", TOKEN),
    ):
        r = _rpc(client, "tools/call", params={"name": "echo", "arguments": {"value": "hi"}})
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == {"echo": "hi"}


def test_tools_call_failure_returns_is_error_not_protocol_error(client):
    """PV-REQ-F-003: a tool exception surfaces as isError, not a JSON-RPC error."""

    def _boom():
        raise ValueError("kaboom")

    with (
        patch.dict(mcp_tools.TOOL_REGISTRY, {"boom": _boom}),
        patch.object(config, "MCP_TOKEN", TOKEN),
    ):
        r = _rpc(client, "tools/call", params={"name": "boom", "arguments": {}})
    assert r.status_code == 200
    body = r.json()
    assert "error" not in body
    result = body["result"]
    assert result["isError"] is True
    assert "kaboom" in result["content"][0]["text"]


def test_tools_call_unknown_tool_returns_jsonrpc_error(client):
    with patch.object(config, "MCP_TOKEN", TOKEN):
        r = _rpc(client, "tools/call", params={"name": "no-such-tool", "arguments": {}})
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32602


import inspect


def _call(client, name, arguments=None, token=TOKEN):
    """Invoke one tool and return the parsed tool result."""
    import json as _json

    r = _rpc(client, "tools/call", {"name": name, "arguments": arguments or {}}, token=token)
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    payload = _json.loads(result["content"][0]["text"]) if not result["isError"] else None
    return result, payload


def test_schema_matches_signature():
    """Schema property names ARE the wire contract (CON-PV-008).

    Green unit tests cannot see a mismatch here because they call the Python
    function directly; only dispatch-by-keyword-expansion exercises it. This
    test pins it structurally.
    """
    for name, fn in mcp_tools.TOOL_REGISTRY.items():
        sig = inspect.signature(fn)
        params = set(sig.parameters)
        schema = mcp_tools.TOOL_SCHEMAS[name]
        props = set(schema.get("properties", {}))
        required = set(schema.get("required", []))

        assert props <= params, f"{name}: schema declares unknown args {props - params}"
        assert required <= props, f"{name}: required not declared in properties"

        no_default = {p for p, v in sig.parameters.items() if v.default is inspect.Parameter.empty}
        assert no_default <= required, (
            f"{name}: params {no_default - required} have no default and are not required"
        )


def test_every_tool_has_a_description():
    for name in mcp_tools.TOOL_REGISTRY:
        assert mcp_tools.TOOL_DESCRIPTIONS.get(name, "").strip(), f"{name} has no description"


def test_tools_list_includes_schema_describe(client):
    with patch.object(config, "MCP_TOKEN", TOKEN):
        r = _rpc(client, "tools/list")
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert "schema.describe" in names
    for t in r.json()["result"]["tools"]:
        assert t["inputSchema"]["type"] == "object"


def test_schema_describe_lists_tables(client):
    with patch.object(config, "MCP_TOKEN", TOKEN):
        _, payload = _call(client, "schema.describe")
    assert "lookahead_cards" in payload["tables"]
    assert "situations" in payload["tables"]
    assert not any(t.startswith("sqlite_") for t in payload["tables"])


def test_schema_describe_one_table(client):
    with patch.object(config, "MCP_TOKEN", TOKEN):
        _, payload = _call(client, "schema.describe", {"table": "lookahead_cards"})
    cols = {c["name"] for c in payload["columns"]}
    assert {"id", "title", "project", "start_date", "end_date", "status"} <= cols


def test_schema_describe_rejects_unknown_table(client):
    with patch.object(config, "MCP_TOKEN", TOKEN):
        result, _ = _call(client, "schema.describe", {"table": "no_such_table"})
    assert result["isError"] is True


def test_schema_describe_rejects_injection_attempt(client):
    with patch.object(config, "MCP_TOKEN", TOKEN):
        result, _ = _call(client, "schema.describe", {"table": "items) --"})
    assert result["isError"] is True


def test_mcp_sql_guards_non_serialized_builds():
    """Verify that a non-serialized SQLite build is rejected at import.

    The check happens at module import time, so we test the function directly
    rather than trying to reload the module in a test.
    """
    import mcp_sql

    # Should raise RuntimeError for any value != 3
    with pytest.raises(RuntimeError, match="threadsafety=2"):
        mcp_sql._check_sqlite_threadsafety(2)

    with pytest.raises(RuntimeError, match="threadsafety=1"):
        mcp_sql._check_sqlite_threadsafety(1)

    with pytest.raises(RuntimeError, match="threadsafety=0"):
        mcp_sql._check_sqlite_threadsafety(0)

    # Should succeed for 3
    mcp_sql._check_sqlite_threadsafety(3)  # No exception


@pytest.mark.parametrize(
    "stmt",
    [
        "INSERT INTO todos (description) VALUES ('x')",
        "UPDATE todos SET done = 1",
        "DELETE FROM todos",
        "DROP TABLE todos",
        "CREATE TABLE x (a int)",
        "ALTER TABLE todos ADD COLUMN z TEXT",
        "PRAGMA table_info(todos)",
        "ATTACH DATABASE '/tmp/x.db' AS x",
        "VACUUM",
        "SELECT 1; DROP TABLE todos",
        "  ; SELECT 1",
        "",
        "   ",
    ],
)
def test_validate_select_rejects(stmt):
    with pytest.raises(ValueError):
        mcp_sql.validate_select(stmt)


@pytest.mark.parametrize(
    "stmt",
    [
        "SELECT 1",
        "select id from todos",
        "  SELECT id FROM todos;  ",
        "WITH x AS (SELECT 1 AS n) SELECT n FROM x",
        "SELECT 1 -- trailing comment",
        "/* leading */ SELECT 1",
    ],
)
def test_validate_select_accepts(stmt):
    assert mcp_sql.validate_select(stmt)


def test_comment_cannot_smuggle_a_second_statement():
    """A semicolon hidden behind a comment must not slip through."""
    with pytest.raises(ValueError):
        mcp_sql.validate_select("SELECT 1 /* x */ ; DELETE FROM todos")


def test_readonly_connection_refuses_writes_at_the_driver():
    """Writes are refused on this connection, whichever layer catches them.

    Two independent guards stand behind this: the authorizer refuses the
    statement at prepare time, and ``mode=ro`` refuses the write at the driver.
    ``DatabaseError`` is the common parent of both refusals, so the assertion
    holds if either guard is removed and only fails if both are.
    """
    c = mcp_sql.ro_conn()
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("CREATE TABLE should_not_exist (a int)")


def test_readonly_connection_refuses_unlisted_pragmas():
    """Only the pragmas schema.describe needs are reachable.

    ``table_info`` and ``index_list`` are allowed by name, so a pragma outside
    that pair must still be refused rather than riding in on a blanket
    SQLITE_PRAGMA exemption.
    """
    c = mcp_sql.ro_conn()
    assert c.execute("PRAGMA table_info(todos)").fetchall() != []
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("PRAGMA journal_mode")


def test_sql_query_returns_columns_and_rows(client):
    db.conn().execute("DELETE FROM lookahead_cards")
    client.post("/lookahead/cards", json=_card_payload_for_sql())
    with patch.object(config, "MCP_TOKEN", TOKEN):
        _, payload = _call(client, "sql.query", {"sql": "SELECT id, title FROM lookahead_cards"})
    assert payload["columns"] == ["id", "title"]
    assert payload["row_count"] == 1
    assert payload["truncated"] is False


def test_sql_query_caps_rows_and_reports_truncation(client):
    db.conn().execute("DELETE FROM lookahead_cards")
    for i in range(5):
        client.post("/lookahead/cards", json=_card_payload_for_sql(title=f"card {i}"))
    with patch.object(config, "MCP_TOKEN", TOKEN):
        _, payload = _call(
            client, "sql.query", {"sql": "SELECT id FROM lookahead_cards", "limit": 2}
        )
    assert payload["row_count"] == 2
    assert payload["truncated"] is True


def test_sql_query_write_is_rejected_as_tool_error(client):
    with patch.object(config, "MCP_TOKEN", TOKEN):
        result, _ = _call(client, "sql.query", {"sql": "DELETE FROM todos"})
    assert result["isError"] is True


def test_sql_query_limit_is_clamped_to_maximum(client):
    """A limit above MAX_LIMIT yields MAX_LIMIT rows and reports truncation.

    The query must generate more than MAX_LIMIT rows for the clamp to be
    observable at all; asserting against a single-row query would hold
    identically whether or not the clamp were applied.
    """
    sql = (
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 600) "
        "SELECT x FROM c"
    )
    with patch.object(config, "MCP_TOKEN", TOKEN):
        _, payload = _call(client, "sql.query", {"sql": sql, "limit": 99999})
    assert payload["row_count"] == mcp_sql.MAX_LIMIT
    assert payload["truncated"] is True


def test_sql_query_rejects_dml_hidden_behind_a_cte(client):
    """A CTE prefix must not smuggle DML past the read-only fence.

    SQLite accepts ``WITH ... DELETE``, so such a statement opens with a token
    ``validate_select`` allows.  The rejection has to come from the fence
    itself; the connection's ``mode=ro`` would otherwise mask the hole behind
    a driver-level error that says nothing about the statement being illegal.
    """
    with patch.object(config, "MCP_TOKEN", TOKEN):
        result, _ = _call(client, "sql.query", {"sql": "WITH x AS (SELECT 1) DELETE FROM todos"})
    assert result["isError"] is True
    message = result["content"][0]["text"]
    assert "readonly database" not in message
    assert "only SELECT" in message


def test_sql_query_aborts_when_it_exceeds_the_deadline():
    """A query running past QUERY_TIMEOUT_SECONDS is interrupted (PV-REQ-N-004).

    The bound is carried in thread-local state and read by a progress handler
    shared with every other thread on the connection, so this also pins that
    the handler consults the calling thread's deadline rather than nothing.
    """
    slow = (
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 20000000) "
        "SELECT count(*) FROM c"
    )
    with (
        patch.object(mcp_sql, "QUERY_TIMEOUT_SECONDS", 0.2),
        pytest.raises(sqlite3.OperationalError, match="interrupted"),
    ):
        mcp_sql.run_query(slow)


_CONCURRENCY_SCRIPT = """
import os, sqlite3, sys, tempfile, threading

sys.path.insert(0, sys.argv[1])
_db = os.path.join(tempfile.mkdtemp(), "concurrency.db")
sqlite3.connect(_db).close()
os.environ["DB_PATH"] = _db

import config
config.DB_PATH = _db
import mcp_sql

SLOW = ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 200000) "
        "SELECT count(*) FROM c")

def work():
    for _ in range(8):
        mcp_sql.run_query(SLOW, limit=1)

threads = [threading.Thread(target=work) for _ in range(3)]
for t in threads:
    t.start()
for t in threads:
    t.join()
print("COMPLETED")
"""


def test_concurrent_sql_queries_do_not_deadlock():
    """Overlapping sql.query calls must not wedge the interpreter (PV-REQ-N-004).

    Mutating connection state per request inverts the lock order between the
    GIL and the SQLite connection mutex: the thread inside ``sqlite3_step``
    holds the connection and wants the GIL to run its callback, while the
    thread installing a hook holds the GIL and wants the connection.

    This runs in a subprocess because the deadlock starves the main thread as
    well -- an in-process timeout could never fire to report the failure.
    """
    api_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api")
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _CONCURRENCY_SCRIPT, api_dir],
            capture_output=True,
            text=True,
            timeout=40,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("concurrent sql.query calls deadlocked the interpreter")
    assert proc.returncode == 0, proc.stderr
    assert "COMPLETED" in proc.stdout


def _card_payload_for_sql(**overrides):
    body = {
        "title": "MCP fixture card",
        "project": "P905",
        "assignee": "",
        "start_date": "2026-04-15",
        "end_date": "2026-04-16",
        "status": "planned",
    }
    body.update(overrides)
    return body


def test_projects_list_includes_config_and_card_counts(client):
    db.conn().execute("DELETE FROM lookahead_cards")
    client.post("/lookahead/cards", json=_card_payload_for_sql())
    with (
        patch.object(config, "MCP_TOKEN", TOKEN),
        patch.object(config, "PROJECTS", [{"name": "P905", "parent": "", "keywords": ["ride"]}]),
    ):
        _, payload = _call(client, "projects.list")
    entry = payload["projects"][0]
    assert entry["name"] == "P905"
    assert entry["keywords"] == ["ride"]
    assert entry["card_count"] == 1
    assert entry["shifts"] == []


def test_cards_list_spans_an_arbitrary_range(client):
    """No 14-day limit — the board's constraint does not apply here."""
    db.conn().execute("DELETE FROM lookahead_cards")
    client.post(
        "/lookahead/cards",
        json=_card_payload_for_sql(start_date="2029-04-02", end_date="2029-04-02", title="far"),
    )
    with patch.object(config, "MCP_TOKEN", TOKEN):
        _, payload = _call(client, "cards.list", {"start": "2026-01-01", "end": "2030-12-31"})
    assert payload["count"] == 1
    assert payload["cards"][0]["title"] == "far"


def test_cards_list_filters_by_status(client):
    db.conn().execute("DELETE FROM lookahead_cards")
    client.post("/lookahead/cards", json=_card_payload_for_sql(title="planned one"))
    client.post("/lookahead/cards", json=_card_payload_for_sql(title="done one", status="done"))
    with patch.object(config, "MCP_TOKEN", TOKEN):
        _, payload = _call(client, "cards.list", {"status": "done"})
    assert payload["count"] == 1
    assert payload["cards"][0]["title"] == "done one"


def test_cards_list_filters_by_project(client):
    db.conn().execute("DELETE FROM lookahead_cards")
    client.post("/lookahead/cards", json=_card_payload_for_sql(project="P905"))
    client.post("/lookahead/cards", json=_card_payload_for_sql(project="P1309", title="other"))
    with patch.object(config, "MCP_TOKEN", TOKEN):
        _, payload = _call(client, "cards.list", {"project": "P1309"})
    assert payload["count"] == 1
    assert payload["cards"][0]["project"] == "P1309"


def _seed_situation(sit_id="sit-mcp-1", dismissed=0, item_ids=None):
    """Insert one situation row directly; returns its id."""
    import json as _json

    db.conn().execute(
        "INSERT OR REPLACE INTO situations "
        "(situation_id, title, summary, status, item_ids, sources, project_tag, "
        " score, priority, open_actions, dismissed) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            sit_id,
            "Panel delivery slipped",
            "Vendor moved the date.",
            "open",
            _json.dumps(item_ids or []),
            _json.dumps(["email"]),
            "P905",
            0.8,
            "high",
            _json.dumps([{"description": "Call vendor"}]),
            dismissed,
        ),
    )
    return sit_id


def test_situations_list_excludes_dismissed_by_default(client):
    db.conn().execute("DELETE FROM situations")
    _seed_situation("sit-open", dismissed=0)
    _seed_situation("sit-gone", dismissed=1)
    with patch.object(config, "MCP_TOKEN", TOKEN):
        _, payload = _call(client, "situations.list")
    ids = {s["situation_id"] for s in payload["situations"]}
    assert ids == {"sit-open"}


def test_situations_list_can_include_dismissed(client):
    db.conn().execute("DELETE FROM situations")
    _seed_situation("sit-open", dismissed=0)
    _seed_situation("sit-gone", dismissed=1)
    with patch.object(config, "MCP_TOKEN", TOKEN):
        _, payload = _call(client, "situations.list", {"include_dismissed": True})
    assert payload["count"] == 2


def test_situations_list_respects_limit(client):
    db.conn().execute("DELETE FROM situations")
    for i in range(4):
        _seed_situation(f"sit-{i}")
    with patch.object(config, "MCP_TOKEN", TOKEN):
        _, payload = _call(client, "situations.list", {"limit": 2})
    assert payload["count"] == 2


def test_situations_get_resolves_contributing_items(client):
    db.conn().execute("DELETE FROM situations")
    # NB: the helper is upsert_item, and the primary key column is item_id,
    # not id. db.get_item() queries `WHERE item_id = ?`.
    db.upsert_item(
        {
            "item_id": "item-mcp-1",
            "source": "email",
            "title": "Panel ship date moved",
            "summary": "Now week 40.",
            "url": "https://example.invalid/1",
        }
    )
    _seed_situation("sit-items", item_ids=["item-mcp-1"])
    with patch.object(config, "MCP_TOKEN", TOKEN):
        _, payload = _call(client, "situations.get", {"situation_id": "sit-items"})
    assert payload["item_count"] == 1
    assert payload["items"][0]["title"] == "Panel ship date moved"
    assert payload["open_actions"][0]["description"] == "Call vendor"


def test_situations_get_unknown_id_is_a_tool_error(client):
    with patch.object(config, "MCP_TOKEN", TOKEN):
        result, _ = _call(client, "situations.get", {"situation_id": "nope"})
    assert result["isError"] is True


# Settings keys that must never appear in a tuning payload. Extend this list
# whenever a credential field is added to the settings record.
_SECRET_KEYS = [
    "escalation_api_key",
    "cf_client_id",
    "cf_client_secret",
    "slack_client_secret",
    "github_pat",
    "jira_token",
]


def test_tuning_get_returns_knobs_and_evidence(client):
    with (
        patch.object(config, "MCP_TOKEN", TOKEN),
        patch.object(config, "NOISE_KEYWORDS", ["unsubscribe"]),
        patch.object(config, "ASSIGNMENT_CORRECTIONS", [{"from": "Bob", "to": "Alice"}]),
        patch.object(
            config, "PROJECTS", [{"name": "P905", "keywords": ["ride"], "learned_keywords": ["rv"]}]
        ),
    ):
        _, payload = _call(client, "tuning.get")
    assert payload["global_keywords"]["noise"] == ["unsubscribe"]
    assert payload["assignment_corrections"] == [{"from": "Bob", "to": "Alice"}]
    assert payload["project_keywords"]["P905"]["keywords"] == ["ride"]
    assert payload["project_keywords"]["P905"]["learned_keywords"] == ["rv"]
    assert "recent_actions" in payload


def test_tuning_get_leaks_no_credentials(client):
    """The settings record co-locates tuning knobs with API tokens."""
    with patch.object(config, "MCP_TOKEN", TOKEN):
        result, payload = _call(client, "tuning.get")
    blob = result["content"][0]["text"].lower()
    for key in _SECRET_KEYS:
        assert key not in blob
    assert "sk-ant" not in blob
    assert "ghp_" not in blob


def test_tuning_get_caps_recent_actions(client):
    for i in range(5):
        db.conn().execute(
            "INSERT INTO user_actions (item_id, action_type, timestamp) VALUES (?,?,?)",
            (f"item-{i}", "tag", f"2026-08-0{i + 1}T00:00:00+00:00"),
        )
    with patch.object(config, "MCP_TOKEN", TOKEN):
        _, payload = _call(client, "tuning.get", {"action_limit": 2})
    assert len(payload["recent_actions"]) == 2
