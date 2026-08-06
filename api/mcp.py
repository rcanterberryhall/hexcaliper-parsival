"""MCP server transport for parsival (IFC-PV-001).

JSON-RPC 2.0 over HTTP.  The route is registered bare as ``/mcp`` because
nginx rewrites ``^/page/api/(.*)`` to ``/$1`` (CON-PV-001), so the reachable
URL is ``/page/api/mcp``.

Authentication is a shared secret supplied in ``X-Parsival-MCP-Token`` and
compared in constant time.  An unset ``config.MCP_TOKEN`` rejects every
request rather than disabling the check (PV-REQ-N-008): this endpoint reaches
the whole database, so failing open is not an acceptable degradation.

Tool failures are returned as an ``isError`` result rather than a JSON-RPC
protocol error (PV-REQ-F-003) so the caller can read the message and retry;
a protocol error would abort the call.
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import Any

import config
import mcp_tools
from fastapi import APIRouter, Header, HTTPException, Response

_log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "parsival-ops"
SERVER_VERSION = "0.1.0"

router = APIRouter()


def _authorised(supplied: str | None) -> bool:
    """Return True when ``supplied`` matches the configured token.

    Args:
        supplied: The value of the ``X-Parsival-MCP-Token`` header, or None.

    Returns:
        True only when a token is configured and the supplied value matches it.
    """
    expected = config.MCP_TOKEN or ""
    if not expected:
        return False
    return hmac.compare_digest(supplied or "", expected)


def _result(id_: Any, result: dict) -> dict:
    """Build a JSON-RPC success envelope."""
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _error(id_: Any, code: int, message: str) -> dict:
    """Build a JSON-RPC error envelope."""
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


@router.post("/mcp")
def mcp_endpoint(
    body: dict,
    x_parsival_mcp_token: str | None = Header(default=None),
):
    """Handle one JSON-RPC request against the tool registry.

    Args:
        body: The JSON-RPC request object.
        x_parsival_mcp_token: Shared-secret header.

    Returns:
        A JSON-RPC response dict, or a bare 202 Response for notifications.

    Raises:
        HTTPException: 401 when the token is absent, wrong, or unconfigured.
    """
    if not _authorised(x_parsival_mcp_token):
        raise HTTPException(status_code=401, detail="invalid or missing MCP token")

    method = body.get("method")
    id_ = body.get("id")
    params = body.get("params") or {}

    if method == "notifications/initialized":
        return Response(status_code=202)

    if method == "initialize":
        return _result(
            id_,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method == "tools/list":
        return _result(id_, {"tools": mcp_tools.tool_specs()})

    if method == "tools/call":
        name = params.get("name", "")
        if name not in mcp_tools.TOOL_REGISTRY:
            return _error(id_, -32601, f"unknown tool: {name}")
        try:
            out = mcp_tools.dispatch(name, params.get("arguments") or {})
        except Exception as exc:  # noqa: BLE001 - tool errors travel on the wire
            _log.warning("mcp tool %s failed: %s", name, exc)
            return _result(id_, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        return _result(
            id_,
            {
                "content": [{"type": "text", "text": json.dumps(out, default=str)}],
                "isError": False,
            },
        )

    return _error(id_, -32601, f"unknown method: {method}")
