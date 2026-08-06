"""Tests for the parsival MCP server transport and auth (IFC-PV-001)."""

from unittest.mock import patch

import config

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
