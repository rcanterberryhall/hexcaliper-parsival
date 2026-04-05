"""tests/test_deep_analysis.py — Tests for deep analysis endpoints."""
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app import situations_tbl, analyses, intel_tbl


def _sit(sit_id=None, item_ids=None, project_tag=None):
    sit_id = sit_id or str(uuid.uuid4())
    return {
        "situation_id":     sit_id,
        "title":            "Service degradation detected",
        "summary":          "Multiple sources indicate elevated error rates.",
        "status":           "in_progress",
        "item_ids":         item_ids or ["item-1", "item-2"],
        "sources":          ["github", "slack"],
        "project_tag":      project_tag,
        "score":            1.8,
        "priority":         "high",
        "open_actions":     [{"description": "Investigate root cause", "owner": "me"}],
        "references":       [],
        "key_context":      None,
        "last_updated":     "2026-04-01T10:00:00+00:00",
        "created_at":       "2026-04-01T10:00:00+00:00",
        "score_updated_at": "2026-04-01T10:00:00+00:00",
        "dismissed":        False,
    }


def _analysis(item_id, source="github"):
    return {
        "item_id": item_id, "source": source, "title": f"Item {item_id}",
        "author": "eng@example.com", "timestamp": "2026-04-01T10:00:00+00:00",
        "url": "", "has_action": False, "priority": "high", "category": "fyi",
        "summary": "Error spike on service.", "urgency": None,
        "action_items": "[]", "processed_at": "2026-04-01T10:00:00+00:00",
    }


# ── POST /situations/{id}/deep-analysis ───────────────────────────────────────

def test_submit_deep_analysis_returns_job_id(client):
    situations_tbl.insert(_sit(sit_id="sit-da-1", item_ids=["item-1"]))
    analyses.insert(_analysis("item-1"))

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"ok": True, "id": "job-abc-123"}

    with patch("app.http_requests.post", return_value=mock_response) as mock_post:
        r = client.post("/situations/sit-da-1/deep-analysis")

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["job_id"] == "job-abc-123"

    # Verify the request was sent to merLLM batch submit
    call_args = mock_post.call_args
    assert "/api/batch/submit" in call_args[0][0]
    payload = call_args[1]["json"]
    assert payload["source_app"] == "parsival"
    assert "Service degradation detected" in payload["prompt"]
    assert "Investigate root cause" in payload["prompt"]


def test_submit_deep_analysis_404_for_unknown_situation(client):
    r = client.post("/situations/nonexistent-sit/deep-analysis")
    assert r.status_code == 404


def test_submit_deep_analysis_502_when_merllm_unreachable(client):
    situations_tbl.insert(_sit(sit_id="sit-da-2"))

    with patch("app.http_requests.post", side_effect=Exception("connection refused")):
        r = client.post("/situations/sit-da-2/deep-analysis")

    assert r.status_code == 502
    assert "merLLM unreachable" in r.json()["detail"]


# ── POST /situations/{id}/deep-analysis/save ──────────────────────────────────

def test_save_deep_analysis_stores_intel_item(client):
    situations_tbl.insert(_sit(sit_id="sit-save-1", item_ids=["item-3"], project_tag="proj-x"))
    analyses.insert(_analysis("item-3"))

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "job-xyz", "result": "Deep analysis text here."}

    with patch("app.http_requests.get", return_value=mock_response):
        r = client.post("/situations/sit-save-1/deep-analysis/save",
                        json={"job_id": "job-xyz"})

    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Verify intel item was stored
    items = [i for i in intel_tbl.all() if i.get("source") == "deep_analysis"]
    assert len(items) == 1
    assert items[0]["fact"] == "Deep analysis text here."
    assert items[0]["item_id"] == "item-3"
    assert items[0]["project_tag"] == "proj-x"


def test_save_deep_analysis_404_for_unknown_situation(client):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"result": "some result"}

    with patch("app.http_requests.get", return_value=mock_response):
        r = client.post("/situations/nonexistent/deep-analysis/save",
                        json={"job_id": "job-xyz"})
    assert r.status_code == 404


def test_save_deep_analysis_409_when_job_not_complete(client):
    situations_tbl.insert(_sit(sit_id="sit-save-2"))

    mock_response = MagicMock()
    mock_response.status_code = 409
    mock_response.json.return_value = {"detail": "Job status: queued"}

    with patch("app.http_requests.get", return_value=mock_response):
        r = client.post("/situations/sit-save-2/deep-analysis/save",
                        json={"job_id": "job-not-done"})
    assert r.status_code == 409


def test_save_deep_analysis_requires_job_id(client):
    situations_tbl.insert(_sit(sit_id="sit-save-3"))
    r = client.post("/situations/sit-save-3/deep-analysis/save", json={})
    assert r.status_code == 422


# ── GET /batch/status/{job_id} ────────────────────────────────────────────────

def test_proxy_batch_status_returns_job_info(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "job-abc", "status": "queued", "source_app": "parsival"
    }

    with patch("app.http_requests.get", return_value=mock_response):
        r = client.get("/batch/status/job-abc")

    assert r.status_code == 200
    assert r.json()["status"] == "queued"


def test_proxy_batch_status_404_when_job_not_found(client):
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.json.return_value = {"detail": "Job not found"}

    with patch("app.http_requests.get", return_value=mock_response):
        r = client.get("/batch/status/no-such-job")

    assert r.status_code == 404


def test_proxy_batch_status_502_when_merllm_unreachable(client):
    with patch("app.http_requests.get", side_effect=Exception("timeout")):
        r = client.get("/batch/status/job-abc")
    assert r.status_code == 502
