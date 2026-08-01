"""Tests for lancellmot_client (parsival#43)."""

from unittest.mock import MagicMock, patch

import lancellmot_client
import pytest


def _ok_response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_list_projects_returns_list_of_dicts():
    payload = [
        {"id": "p1", "name": "alpha"},
        {"id": "p2", "name": "beta"},
    ]
    with patch("lancellmot_client.requests.get", return_value=_ok_response(payload)) as mock_get:
        result = lancellmot_client.list_projects()
    assert result == payload
    call_url = mock_get.call_args[0][0]
    # The /api prefix is load-bearing: without it lancellmot's nginx serves the
    # SPA index.html with a 200, so the mistake reads as "unreachable" forever.
    assert call_url.endswith("/api/workspace/projects")


def test_list_projects_raises_on_network_error():
    with (
        patch(
            "lancellmot_client.requests.get",
            side_effect=__import__("requests").ConnectionError("boom"),
        ),
        pytest.raises(lancellmot_client.LancellmotUnavailable),
    ):
        lancellmot_client.list_projects()


def test_list_projects_raises_on_timeout():
    with (
        patch("lancellmot_client.requests.get", side_effect=__import__("requests").Timeout("slow")),
        pytest.raises(lancellmot_client.LancellmotUnavailable),
    ):
        lancellmot_client.list_projects()


def test_list_projects_raises_on_5xx():
    bad = MagicMock()
    bad.status_code = 503
    bad.raise_for_status.side_effect = __import__("requests").HTTPError("503")
    with (
        patch("lancellmot_client.requests.get", return_value=bad),
        pytest.raises(lancellmot_client.LancellmotUnavailable),
    ):
        lancellmot_client.list_projects()


def test_html_body_with_200_is_unavailable():
    """A 200 carrying HTML is treated as unavailable, not an unhandled crash.

    This is the wrong-base-URL case, and it is not hypothetical: pointing
    LANCELLMOT_URL at the host without the /api prefix hits lancellmot's SPA
    fallback, which answers 200 with index.html rather than 404.
    """
    import requests as req

    html = MagicMock()
    html.status_code = 200
    html.raise_for_status.return_value = None
    html.json.side_effect = req.exceptions.JSONDecodeError("Expecting value", "<!DOCTYPE html>", 0)
    with patch("lancellmot_client.requests.get", return_value=html):
        with pytest.raises(lancellmot_client.LancellmotUnavailable):
            lancellmot_client.list_projects()
        with pytest.raises(lancellmot_client.LancellmotUnavailable):
            lancellmot_client.list_documents("proj-1")


def test_list_documents_returns_trimmed_list():
    payload = [{"id": f"d{i}", "filename": f"doc{i}.pdf"} for i in range(10)]
    with patch("lancellmot_client.requests.get", return_value=_ok_response(payload)) as mock_get:
        result = lancellmot_client.list_documents("proj-1", limit=5)
    assert len(result) == 5
    assert result[0]["filename"] == "doc0.pdf"
    call_url = mock_get.call_args[0][0]
    assert call_url.endswith("/api/documents")
    assert mock_get.call_args.kwargs["params"] == {"project_id": "proj-1"}


def test_list_documents_default_limit_is_5():
    payload = [{"id": f"d{i}", "filename": f"doc{i}.pdf"} for i in range(20)]
    with patch("lancellmot_client.requests.get", return_value=_ok_response(payload)):
        result = lancellmot_client.list_documents("proj-1")
    assert len(result) == 5


def test_list_documents_raises_on_failure():
    with (
        patch(
            "lancellmot_client.requests.get",
            side_effect=__import__("requests").ConnectionError("boom"),
        ),
        pytest.raises(lancellmot_client.LancellmotUnavailable),
    ):
        lancellmot_client.list_documents("proj-1")
