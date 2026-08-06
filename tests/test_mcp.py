"""Tests for the parsival MCP server transport and auth (IFC-PV-001)."""

import json
from unittest.mock import patch

import config
import mcp_tools

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
    assert r.json()["error"]["code"] == -32601


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
