# Parsival → lancellmot Document Chip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Related in lancellmot" chip to parsival situation cards that resolves project tags to lancellmot projects via a strict alias table and surfaces top documents inline.

**Architecture:** New `lancellmot_aliases` SQLite table stores explicit project-name → lancellmot-project-id mappings. New `api/lancellmot_client.py` wraps lancellmot's HTTP API. Five new FastAPI routes expose CRUD for aliases, a projects proxy (for the Settings dropdown), and `docs-for-tag` (the render path). UI adds a dropdown column to each Settings project row and a chip (with three visual states: resolved, unmapped, unreachable) on each situation card.

**Tech Stack:** Python 3.11+, FastAPI, SQLite (via `api/db.py`), `requests` for HTTP, vanilla JS for UI (existing `web/page/index.html`), pytest + httpx mocking.

**Spec:** `docs/superpowers/specs/2026-04-19-parsival-lancellmot-document-chip-design.md`
**Issue:** [parsival#43](https://github.com/rcanterberryhall/hexcaliper-parsival/issues/43)

---

## File Structure

| File | Role |
|---|---|
| `api/config.py` | Add `LANCELLMOT_URL` env var |
| `api/db.py` | Add `lancellmot_aliases` table + 4 CRUD helpers |
| `api/lancellmot_client.py` | NEW — HTTP client (list_projects, list_documents, LancellmotUnavailable) |
| `api/app.py` | Add 5 routes under `/api/lancellmot/*` |
| `web/page/index.html` | Settings dropdown column + situation-card chip |
| `docker-compose.yml` | Wire `LANCELLMOT_URL` env into parsival-api service |
| `tests/test_lancellmot_aliases.py` | NEW — DB CRUD tests |
| `tests/test_lancellmot_client.py` | NEW — HTTP client tests |
| `tests/test_lancellmot_routes.py` | NEW — route integration tests |
| `README.md` | Document the feature |

---

## Task 1: Config + schema migration

**Files:**
- Modify: `api/config.py`
- Modify: `api/db.py` (init function, around line 240)
- Modify: `docker-compose.yml`
- Test: `tests/test_lancellmot_aliases.py` (NEW)

- [ ] **Step 1: Write the failing test**

Create `tests/test_lancellmot_aliases.py`:

```python
"""Tests for lancellmot_aliases table + CRUD helpers."""
from api import db


def test_lancellmot_aliases_table_exists():
    cols = {r[1] for r in db.conn().execute(
        "PRAGMA table_info(lancellmot_aliases)"
    ).fetchall()}
    assert cols == {
        "parsival_project",
        "lancellmot_project_id",
        "lancellmot_project_name",
        "created_at",
        "updated_at",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_lancellmot_aliases.py -v`
Expected: FAIL with empty set (table missing)

- [ ] **Step 3: Add table to db.py init**

In `api/db.py`, find the init function's `CREATE TABLE` block (around line 240, near existing tables). Add:

```python
    CREATE TABLE IF NOT EXISTS lancellmot_aliases (
        parsival_project          TEXT PRIMARY KEY,
        lancellmot_project_id     TEXT NOT NULL,
        lancellmot_project_name   TEXT NOT NULL,
        created_at                TEXT NOT NULL,
        updated_at                TEXT NOT NULL
    );
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_lancellmot_aliases.py -v`
Expected: PASS

- [ ] **Step 5: Add LANCELLMOT_URL to config.py**

In `api/config.py`, just after the `MERLLM_URL` line (~line 80):

```python
LANCELLMOT_URL = _get("LANCELLMOT_URL", "http://host.docker.internal:8080")
```

- [ ] **Step 6: Wire env var in docker-compose.yml**

Find the parsival-api `environment:` block and add:

```yaml
      LANCELLMOT_URL:   "http://host.docker.internal:8080"
```

- [ ] **Step 7: Commit**

```bash
git add api/config.py api/db.py docker-compose.yml tests/test_lancellmot_aliases.py
git commit -m "feat(#43): add lancellmot_aliases table + LANCELLMOT_URL config"
```

---

## Task 2: DB CRUD helpers for aliases

**Files:**
- Modify: `api/db.py` (append helpers at module scope, near other CRUD helpers)
- Test: `tests/test_lancellmot_aliases.py`

- [ ] **Step 1: Write failing test for upsert + get_by_tag**

Append to `tests/test_lancellmot_aliases.py`:

```python
def test_upsert_creates_alias():
    db.upsert_lancellmot_alias(
        parsival_project="Ethylene-Cracker-3",
        lancellmot_project_id="proj-123",
        lancellmot_project_name="ethylene-cracker-3",
    )
    row = db.get_lancellmot_alias_for_tag("Ethylene-Cracker-3")
    assert row is not None
    assert row["lancellmot_project_id"] == "proj-123"
    assert row["lancellmot_project_name"] == "ethylene-cracker-3"
    assert row["created_at"]
    assert row["updated_at"]


def test_upsert_updates_existing_alias():
    db.upsert_lancellmot_alias(
        parsival_project="Alpha",
        lancellmot_project_id="old-id",
        lancellmot_project_name="old-name",
    )
    original = db.get_lancellmot_alias_for_tag("Alpha")
    db.upsert_lancellmot_alias(
        parsival_project="Alpha",
        lancellmot_project_id="new-id",
        lancellmot_project_name="new-name",
    )
    updated = db.get_lancellmot_alias_for_tag("Alpha")
    assert updated["lancellmot_project_id"] == "new-id"
    assert updated["lancellmot_project_name"] == "new-name"
    assert updated["created_at"] == original["created_at"]
    assert updated["updated_at"] >= original["updated_at"]


def test_get_lancellmot_alias_returns_none_for_missing():
    assert db.get_lancellmot_alias_for_tag("Nonexistent") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_lancellmot_aliases.py -v`
Expected: FAIL — `AttributeError: module 'api.db' has no attribute 'upsert_lancellmot_alias'`

- [ ] **Step 3: Implement upsert + get_by_tag**

Append to `api/db.py` (at module scope, after existing CRUD helpers):

```python
def upsert_lancellmot_alias(
    parsival_project: str,
    lancellmot_project_id: str,
    lancellmot_project_name: str,
) -> None:
    now = datetime.utcnow().isoformat()
    conn().execute(
        "INSERT INTO lancellmot_aliases "
        "(parsival_project, lancellmot_project_id, lancellmot_project_name, "
        " created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(parsival_project) DO UPDATE SET "
        "  lancellmot_project_id = excluded.lancellmot_project_id, "
        "  lancellmot_project_name = excluded.lancellmot_project_name, "
        "  updated_at = excluded.updated_at",
        (parsival_project, lancellmot_project_id, lancellmot_project_name, now, now),
    )
    conn().commit()


def get_lancellmot_alias_for_tag(parsival_project: str) -> dict | None:
    row = conn().execute(
        "SELECT parsival_project, lancellmot_project_id, lancellmot_project_name, "
        "       created_at, updated_at "
        "FROM lancellmot_aliases WHERE parsival_project = ?",
        (parsival_project,),
    ).fetchone()
    if row is None:
        return None
    return {
        "parsival_project": row[0],
        "lancellmot_project_id": row[1],
        "lancellmot_project_name": row[2],
        "created_at": row[3],
        "updated_at": row[4],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_lancellmot_aliases.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Write failing tests for list + delete**

Append:

```python
def test_list_lancellmot_aliases_returns_all():
    db.upsert_lancellmot_alias("A", "idA", "nameA")
    db.upsert_lancellmot_alias("B", "idB", "nameB")
    rows = db.list_lancellmot_aliases()
    names = {r["parsival_project"] for r in rows}
    assert {"A", "B"}.issubset(names)


def test_delete_lancellmot_alias_removes_row():
    db.upsert_lancellmot_alias("Doomed", "id-doom", "name-doom")
    assert db.get_lancellmot_alias_for_tag("Doomed") is not None
    db.delete_lancellmot_alias("Doomed")
    assert db.get_lancellmot_alias_for_tag("Doomed") is None


def test_delete_lancellmot_alias_missing_is_noop():
    db.delete_lancellmot_alias("Never-Existed")  # must not raise
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_lancellmot_aliases.py -v`
Expected: FAIL — missing `list_lancellmot_aliases` / `delete_lancellmot_alias`

- [ ] **Step 7: Implement list + delete**

Append to `api/db.py`:

```python
def list_lancellmot_aliases() -> list[dict]:
    rows = conn().execute(
        "SELECT parsival_project, lancellmot_project_id, lancellmot_project_name, "
        "       created_at, updated_at "
        "FROM lancellmot_aliases "
        "ORDER BY parsival_project"
    ).fetchall()
    return [
        {
            "parsival_project": r[0],
            "lancellmot_project_id": r[1],
            "lancellmot_project_name": r[2],
            "created_at": r[3],
            "updated_at": r[4],
        }
        for r in rows
    ]


def delete_lancellmot_alias(parsival_project: str) -> None:
    conn().execute(
        "DELETE FROM lancellmot_aliases WHERE parsival_project = ?",
        (parsival_project,),
    )
    conn().commit()
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_lancellmot_aliases.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 9: Commit**

```bash
git add api/db.py tests/test_lancellmot_aliases.py
git commit -m "feat(#43): CRUD helpers for lancellmot_aliases"
```

---

## Task 3: HTTP client (api/lancellmot_client.py)

**Files:**
- Create: `api/lancellmot_client.py`
- Create: `tests/test_lancellmot_client.py`

- [ ] **Step 1: Write failing test for list_projects success**

Create `tests/test_lancellmot_client.py`:

```python
"""Tests for api.lancellmot_client."""
from unittest.mock import patch, MagicMock
import pytest
from api import lancellmot_client


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
    with patch("api.lancellmot_client.requests.get",
               return_value=_ok_response(payload)) as mock_get:
        result = lancellmot_client.list_projects()
    assert result == payload
    call_url = mock_get.call_args[0][0]
    assert "/workspace/projects" in call_url
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_lancellmot_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.lancellmot_client'`

- [ ] **Step 3: Create the client module**

Create `api/lancellmot_client.py`:

```python
"""HTTP client for lancellmot's workspace + documents API.

Used by parsival to resolve project tags to lancellmot projects and fetch
related documents for the situation-card chip.
"""
import requests

from api import config


DEFAULT_TIMEOUT = 2.0


class LancellmotUnavailable(Exception):
    """Raised on network error, timeout, or non-2xx response from lancellmot."""


def list_projects() -> list[dict]:
    """Return all lancellmot projects. Raises LancellmotUnavailable on failure."""
    url = f"{config.LANCELLMOT_URL}/workspace/projects"
    try:
        resp = requests.get(url, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise LancellmotUnavailable(str(exc)) from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_lancellmot_client.py -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for failure modes**

Append to `tests/test_lancellmot_client.py`:

```python
def test_list_projects_raises_on_network_error():
    with patch("api.lancellmot_client.requests.get",
               side_effect=__import__("requests").ConnectionError("boom")):
        with pytest.raises(lancellmot_client.LancellmotUnavailable):
            lancellmot_client.list_projects()


def test_list_projects_raises_on_timeout():
    with patch("api.lancellmot_client.requests.get",
               side_effect=__import__("requests").Timeout("slow")):
        with pytest.raises(lancellmot_client.LancellmotUnavailable):
            lancellmot_client.list_projects()


def test_list_projects_raises_on_5xx():
    bad = MagicMock()
    bad.status_code = 503
    bad.raise_for_status.side_effect = __import__("requests").HTTPError("503")
    with patch("api.lancellmot_client.requests.get", return_value=bad):
        with pytest.raises(lancellmot_client.LancellmotUnavailable):
            lancellmot_client.list_projects()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_lancellmot_client.py -v`
Expected: PASS (existing try/except already covers these paths)

- [ ] **Step 7: Write failing test for list_documents**

Append:

```python
def test_list_documents_returns_trimmed_list():
    payload = [{"id": f"d{i}", "filename": f"doc{i}.pdf"} for i in range(10)]
    with patch("api.lancellmot_client.requests.get",
               return_value=_ok_response(payload)) as mock_get:
        result = lancellmot_client.list_documents("proj-1", limit=5)
    assert len(result) == 5
    assert result[0]["filename"] == "doc0.pdf"
    call_url = mock_get.call_args[0][0]
    assert "/documents" in call_url
    assert "project_id=proj-1" in call_url


def test_list_documents_default_limit_is_5():
    payload = [{"id": f"d{i}", "filename": f"doc{i}.pdf"} for i in range(20)]
    with patch("api.lancellmot_client.requests.get",
               return_value=_ok_response(payload)):
        result = lancellmot_client.list_documents("proj-1")
    assert len(result) == 5


def test_list_documents_raises_on_failure():
    with patch("api.lancellmot_client.requests.get",
               side_effect=__import__("requests").ConnectionError("boom")):
        with pytest.raises(lancellmot_client.LancellmotUnavailable):
            lancellmot_client.list_documents("proj-1")
```

- [ ] **Step 8: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_lancellmot_client.py -v`
Expected: FAIL — `AttributeError: module 'api.lancellmot_client' has no attribute 'list_documents'`

- [ ] **Step 9: Implement list_documents**

Append to `api/lancellmot_client.py`:

```python
def list_documents(project_id: str, limit: int = 5) -> list[dict]:
    """Return first `limit` documents for a lancellmot project.

    Raises LancellmotUnavailable on network error, timeout, or non-2xx response.
    """
    url = f"{config.LANCELLMOT_URL}/documents"
    params = {"project_id": project_id}
    try:
        resp = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        docs = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise LancellmotUnavailable(str(exc)) from exc
    return docs[:limit]
```

Note: the test asserts `project_id=proj-1` appears in the URL. `requests` serializes params into the query string, so this works.

- [ ] **Step 10: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_lancellmot_client.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 11: Commit**

```bash
git add api/lancellmot_client.py tests/test_lancellmot_client.py
git commit -m "feat(#43): HTTP client for lancellmot projects + documents"
```

---

## Task 4: Alias CRUD routes + projects proxy

**Files:**
- Modify: `api/app.py` (add 4 new routes near other `/api/*` groupings)
- Create: `tests/test_lancellmot_routes.py`

- [ ] **Step 1: Write failing test for GET /api/lancellmot/aliases**

Create `tests/test_lancellmot_routes.py`:

```python
"""Integration tests for /api/lancellmot/* routes."""
from unittest.mock import patch
from fastapi.testclient import TestClient

from api.app import app
from api import db, lancellmot_client


client = TestClient(app)


def test_get_aliases_returns_list():
    db.upsert_lancellmot_alias("Foo", "id-foo", "foo-name")
    db.upsert_lancellmot_alias("Bar", "id-bar", "bar-name")
    resp = client.get("/api/lancellmot/aliases")
    assert resp.status_code == 200
    body = resp.json()
    names = {a["parsival_project"] for a in body}
    assert {"Foo", "Bar"}.issubset(names)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_lancellmot_routes.py -v`
Expected: FAIL — 404 (route missing)

- [ ] **Step 3: Implement the route**

In `api/app.py`, add near the other settings-adjacent routes (e.g. after the `/noise-filters` group, around line 1510):

```python
@app.get("/api/lancellmot/aliases")
def list_lancellmot_aliases_route():
    return db.list_lancellmot_aliases()
```

Ensure `from api import lancellmot_client` is imported at top of app.py (near other api imports).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_lancellmot_routes.py -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for PUT + DELETE**

Append:

```python
def test_put_alias_creates_new():
    resp = client.put("/api/lancellmot/aliases", json={
        "parsival_project": "NewProj",
        "lancellmot_project_id": "id-new",
        "lancellmot_project_name": "new-name",
    })
    assert resp.status_code == 200
    row = db.get_lancellmot_alias_for_tag("NewProj")
    assert row["lancellmot_project_id"] == "id-new"


def test_put_alias_updates_existing():
    db.upsert_lancellmot_alias("Upd", "old-id", "old-name")
    resp = client.put("/api/lancellmot/aliases", json={
        "parsival_project": "Upd",
        "lancellmot_project_id": "new-id",
        "lancellmot_project_name": "new-name",
    })
    assert resp.status_code == 200
    row = db.get_lancellmot_alias_for_tag("Upd")
    assert row["lancellmot_project_id"] == "new-id"


def test_delete_alias_removes():
    db.upsert_lancellmot_alias("Gone", "id-g", "g-name")
    resp = client.delete("/api/lancellmot/aliases/Gone")
    assert resp.status_code == 200
    assert db.get_lancellmot_alias_for_tag("Gone") is None
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_lancellmot_routes.py -v`
Expected: FAIL (405 or 404 — routes missing)

- [ ] **Step 7: Implement PUT + DELETE**

Append in `api/app.py`:

```python
@app.put("/api/lancellmot/aliases")
def put_lancellmot_alias(payload: dict):
    try:
        parsival = payload["parsival_project"]
        lid = payload["lancellmot_project_id"]
        lname = payload["lancellmot_project_name"]
    except KeyError as k:
        raise HTTPException(status_code=400, detail=f"missing field: {k}")
    db.upsert_lancellmot_alias(parsival, lid, lname)
    return {"ok": True}


@app.delete("/api/lancellmot/aliases/{parsival_project}")
def delete_lancellmot_alias_route(parsival_project: str):
    db.delete_lancellmot_alias(parsival_project)
    return {"ok": True}
```

Verify `HTTPException` is already imported at the top of `api/app.py`; if not, add it to the existing fastapi import line.

- [ ] **Step 8: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_lancellmot_routes.py -v`
Expected: PASS (4 tests)

- [ ] **Step 9: Write failing tests for projects proxy**

Append:

```python
def test_get_lancellmot_projects_proxies_client():
    fake_projects = [{"id": "p1", "name": "alpha"}, {"id": "p2", "name": "beta"}]
    with patch("api.app.lancellmot_client.list_projects",
               return_value=fake_projects):
        resp = client.get("/api/lancellmot/projects")
    assert resp.status_code == 200
    assert resp.json() == fake_projects


def test_get_lancellmot_projects_503_on_unavailable():
    with patch("api.app.lancellmot_client.list_projects",
               side_effect=lancellmot_client.LancellmotUnavailable("boom")):
        resp = client.get("/api/lancellmot/projects")
    assert resp.status_code == 503
    assert resp.json() == {"error": "unreachable"}
```

- [ ] **Step 10: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_lancellmot_routes.py -v`
Expected: FAIL — route missing (404)

- [ ] **Step 11: Implement projects proxy route**

Append in `api/app.py`:

```python
@app.get("/api/lancellmot/projects")
def get_lancellmot_projects():
    try:
        return lancellmot_client.list_projects()
    except lancellmot_client.LancellmotUnavailable:
        return JSONResponse(status_code=503, content={"error": "unreachable"})
```

Verify `JSONResponse` is imported (`from fastapi.responses import JSONResponse`). If not, add it.

- [ ] **Step 12: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_lancellmot_routes.py -v`
Expected: PASS (6 tests)

- [ ] **Step 13: Commit**

```bash
git add api/app.py tests/test_lancellmot_routes.py
git commit -m "feat(#43): alias CRUD routes + lancellmot projects proxy"
```

---

## Task 5: docs-for-tag route (render path)

**Files:**
- Modify: `api/app.py`
- Modify: `tests/test_lancellmot_routes.py`

- [ ] **Step 1: Write failing test for resolved (ok) path**

Append to `tests/test_lancellmot_routes.py`:

```python
def test_docs_for_tag_ok_path():
    db.upsert_lancellmot_alias("Alpha", "proj-a", "alpha-proj")
    fake_docs = [
        {"id": "d1", "filename": "spec.pdf"},
        {"id": "d2", "filename": "procedure.md"},
    ]
    with patch("api.app.lancellmot_client.list_documents",
               return_value=fake_docs) as mock_docs:
        resp = client.get("/api/lancellmot/docs-for-tag?tag=Alpha")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["project_name"] == "alpha-proj"
    assert body["project_id"] == "proj-a"
    assert body["docs"] == fake_docs
    mock_docs.assert_called_once_with("proj-a", limit=5)


def test_docs_for_tag_unmapped():
    resp = client.get("/api/lancellmot/docs-for-tag?tag=NoAlias")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unmapped"
    assert body["tag"] == "NoAlias"


def test_docs_for_tag_unreachable():
    db.upsert_lancellmot_alias("Beta", "proj-b", "beta-proj")
    with patch("api.app.lancellmot_client.list_documents",
               side_effect=lancellmot_client.LancellmotUnavailable("down")):
        resp = client.get("/api/lancellmot/docs-for-tag?tag=Beta")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unreachable"
    assert body["tag"] == "Beta"


def test_docs_for_tag_respects_limit_param():
    db.upsert_lancellmot_alias("Gamma", "proj-g", "gamma-proj")
    with patch("api.app.lancellmot_client.list_documents",
               return_value=[]) as mock_docs:
        resp = client.get("/api/lancellmot/docs-for-tag?tag=Gamma&limit=3")
    assert resp.status_code == 200
    mock_docs.assert_called_once_with("proj-g", limit=3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_lancellmot_routes.py -v`
Expected: FAIL (404 — route missing)

- [ ] **Step 3: Implement the route**

Append in `api/app.py`:

```python
@app.get("/api/lancellmot/docs-for-tag")
def docs_for_tag(tag: str, limit: int = 5):
    alias = db.get_lancellmot_alias_for_tag(tag)
    if alias is None:
        return {"status": "unmapped", "tag": tag}
    try:
        docs = lancellmot_client.list_documents(
            alias["lancellmot_project_id"], limit=limit
        )
    except lancellmot_client.LancellmotUnavailable:
        return {"status": "unreachable", "tag": tag}
    return {
        "status": "ok",
        "tag": tag,
        "project_id": alias["lancellmot_project_id"],
        "project_name": alias["lancellmot_project_name"],
        "docs": docs,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_lancellmot_routes.py -v`
Expected: PASS (10 tests total in this file)

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `python3 -m pytest tests/ -v`
Expected: all tests PASS (~460+ with new additions)

- [ ] **Step 6: Commit**

```bash
git add api/app.py tests/test_lancellmot_routes.py
git commit -m "feat(#43): /api/lancellmot/docs-for-tag endpoint"
```

---

## Task 6: Settings UI — Projects dropdown column

**Files:**
- Modify: `web/page/index.html`

This task has no unit tests (pure HTML/JS). Verification is manual via browser after `docker compose up -d --build parsival-api`.

- [ ] **Step 1: Add the dropdown column to project-list header + rows**

In `web/page/index.html` near line 1508:

```html
<div class="project-row-head" id="project-list-head" style="display:none">
  <span>Name / Parent</span><span>Description</span><span>Keywords</span><span>Senders</span><span>lancellmot</span><span></span>
</div>
```

Find the JS function that builds each project row (search for `addProjectRow` and `renderProjects` or similar). Add a new `<select>` cell to each row's innerHTML. Example addition to the row template:

```html
<select class="lancellmot-select" data-parsival-project="${escapeHtml(p.name)}">
  <option value="">— none —</option>
</select>
```

- [ ] **Step 2: Add JS to populate dropdowns from /api/lancellmot/projects**

Add to the settings-loading code (search for `loadSettings` or `openSettings`):

```javascript
async function populateLancellmotDropdowns() {
  let projects = [];
  try {
    const r = await fetch('/api/lancellmot/projects');
    if (!r.ok) throw new Error('unreachable');
    projects = await r.json();
  } catch (_) {
    projects = null;  // will show as error state below
  }
  const aliasesResp = await fetch('/api/lancellmot/aliases');
  const aliases = aliasesResp.ok ? await aliasesResp.json() : [];
  const aliasByTag = {};
  for (const a of aliases) aliasByTag[a.parsival_project] = a;

  document.querySelectorAll('.lancellmot-select').forEach(sel => {
    const parsivalProj = sel.dataset.parsivalProject;
    sel.innerHTML = '';
    const none = document.createElement('option');
    none.value = '';
    none.textContent = '— none —';
    sel.appendChild(none);
    if (projects === null) {
      sel.disabled = true;
      const err = document.createElement('option');
      err.textContent = 'lancellmot unreachable';
      err.disabled = true;
      sel.appendChild(err);
      return;
    }
    for (const p of projects) {
      const o = document.createElement('option');
      o.value = p.id;
      o.textContent = p.name;
      o.dataset.name = p.name;
      sel.appendChild(o);
    }
    const current = aliasByTag[parsivalProj];
    if (current) sel.value = current.lancellmot_project_id;

    sel.addEventListener('change', async () => {
      if (sel.value === '') {
        await fetch('/api/lancellmot/aliases/' + encodeURIComponent(parsivalProj),
                    { method: 'DELETE' });
        return;
      }
      const picked = sel.options[sel.selectedIndex];
      await fetch('/api/lancellmot/aliases', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          parsival_project: parsivalProj,
          lancellmot_project_id: sel.value,
          lancellmot_project_name: picked.dataset.name,
        }),
      });
    });
  });
}
```

Call `populateLancellmotDropdowns()` from the end of the existing `renderProjects()` (or wherever project rows are rebuilt) AND from `openSettings()` after project rows render.

- [ ] **Step 3: Add minimal CSS for the new column**

Near the existing `.project-row-head` CSS, add:

```css
.lancellmot-select {
  font-size: 11px;
  padding: 3px 6px;
  border-radius: var(--r);
  border: 1px solid var(--border2);
  background: var(--surface);
  color: var(--text);
  max-width: 160px;
}
```

- [ ] **Step 4: Rebuild and visually verify**

Run: `docker compose up -d --build parsival-api`

Open the UI, click ⚙, scroll to Projects. For each project row:
- Dropdown is present
- Dropdown lists lancellmot projects
- Selecting a project and reopening Settings shows it persisted
- Selecting "— none —" removes the alias (visible via `curl /api/lancellmot/aliases`)
- If lancellmot is down: dropdown shows "lancellmot unreachable" and is disabled

- [ ] **Step 5: Commit**

```bash
git add web/page/index.html
git commit -m "feat(#43): Settings dropdown column for lancellmot project mapping"
```

---

## Task 7: Situation card chip

**Files:**
- Modify: `web/page/index.html`

No unit tests — verification via browser.

- [ ] **Step 1: Add chip CSS**

Add near other card chip styles:

```css
.lmot-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  padding: 2px 7px;
  border-radius: 10px;
  border: 1px solid var(--border2);
  background: rgba(64, 184, 255, 0.08);
  color: var(--blue);
  cursor: pointer;
  margin-left: 6px;
  position: relative;
}
.lmot-chip.unmapped {
  background: transparent;
  color: var(--muted);
  border-color: var(--border2);
  border-style: dashed;
}
.lmot-chip.unreachable {
  background: rgba(255, 180, 60, 0.10);
  color: #e6a23c;
  border-color: #c07a1a;
}
.lmot-popover {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  min-width: 240px;
  max-width: 320px;
  background: var(--surface);
  border: 1px solid var(--border2);
  border-radius: 6px;
  padding: 8px 10px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.4);
  z-index: 100;
  font-size: 11px;
}
.lmot-popover a { display: block; padding: 2px 0; color: var(--blue); text-decoration: none; }
.lmot-popover a:hover { text-decoration: underline; }
.lmot-popover-head { color: var(--muted); font-weight: bold; margin-bottom: 4px; }
```

- [ ] **Step 2: Add chip-render JS**

Add a helper function to the script section:

```javascript
async function renderLancellmotChips(situationCard, projectTags) {
  // situationCard: DOM node of the card
  // projectTags: array of parsival project name strings
  if (!projectTags || projectTags.length === 0) return;
  const host = situationCard.querySelector('.lmot-chip-host');
  if (!host) return;  // card template missing the host element
  host.innerHTML = '';
  for (const tag of projectTags) {
    const chip = document.createElement('span');
    chip.className = 'lmot-chip';
    chip.textContent = tag + ' · …';
    host.appendChild(chip);
    try {
      const r = await fetch('/api/lancellmot/docs-for-tag?tag=' + encodeURIComponent(tag));
      const body = await r.json();
      if (body.status === 'ok') {
        chip.textContent = tag + ' · ' + body.docs.length + ' doc' + (body.docs.length === 1 ? '' : 's');
        chip.onmouseenter = () => showLancellmotPopover(chip, body);
        chip.onmouseleave = () => hideLancellmotPopover(chip);
        chip.onclick = () => window.open(
          (window.LANCELLMOT_WEB_URL || '/lancellmot') + '/project/' + body.project_id,
          '_blank');
      } else if (body.status === 'unmapped') {
        chip.classList.add('unmapped');
        chip.textContent = tag + ' · Map →';
        chip.title = 'No lancellmot mapping yet. Click to map.';
        chip.onclick = () => openSettingsFocusedOn(tag);
      } else {
        chip.classList.add('unreachable');
        chip.textContent = tag + ' · ⚠ unreachable';
        chip.title = "Couldn't reach lancellmot — retry in a moment";
        chip.onclick = () => renderLancellmotChips(situationCard, projectTags);
      }
    } catch (_) {
      chip.classList.add('unreachable');
      chip.textContent = tag + ' · ⚠ unreachable';
    }
  }
}

function showLancellmotPopover(chip, body) {
  hideLancellmotPopover(chip);
  const pop = document.createElement('div');
  pop.className = 'lmot-popover';
  const lmotWeb = window.LANCELLMOT_WEB_URL || '/lancellmot';
  let html = '<div class="lmot-popover-head">' + escapeHtml(body.project_name) + '</div>';
  for (const d of body.docs) {
    html += '<a href="' + lmotWeb + '/document/' + encodeURIComponent(d.id) +
            '" target="_blank">' + escapeHtml(d.filename) + '</a>';
  }
  html += '<a href="' + lmotWeb + '/project/' + encodeURIComponent(body.project_id) +
          '" target="_blank" style="margin-top:4px;border-top:1px solid var(--border2);padding-top:4px">All docs in lancellmot →</a>';
  pop.innerHTML = html;
  chip.appendChild(pop);
}

function hideLancellmotPopover(chip) {
  const existing = chip.querySelector('.lmot-popover');
  if (existing) existing.remove();
}
```

Note: if `escapeHtml` does not already exist in index.html, use the existing equivalent (search for how other card content sanitizes strings — likely a helper or `textContent` usage).

- [ ] **Step 3: Add chip host element to situation card template**

Find the situation-card rendering (search for `situation-card` or the function that builds situation cards). Add a host span near the project tag meta line:

```html
<span class="lmot-chip-host"></span>
```

After the card is appended to the DOM, call:

```javascript
renderLancellmotChips(cardEl, situation.project_tags || []);
```

Where `cardEl` is the card DOM node and `situation.project_tags` is the existing project tag array on the situation object.

- [ ] **Step 4: Rebuild and visually verify all three chip states**

Run: `docker compose up -d --build parsival-api`

Verify in browser:
- **Resolved:** situation tagged with a mapped project shows active chip; hover reveals popover with filenames; clicking a filename opens lancellmot doc viewer
- **Unmapped:** situation tagged with an unmapped project shows dim "Map →" chip; clicking opens Settings with the right project row focused (Task 8 handles focus)
- **Unreachable:** stop lancellmot container (`docker stop lancellmot-api` or equivalent); cards show amber "⚠ unreachable" chip; tooltip present; clicking re-fetches

- [ ] **Step 5: Commit**

```bash
git add web/page/index.html
git commit -m "feat(#43): situation card lancellmot chip (resolved/unmapped/unreachable)"
```

---

## Task 8: Click-through from unmapped chip to Settings

**Files:**
- Modify: `web/page/index.html`

- [ ] **Step 1: Implement openSettingsFocusedOn(tag)**

Add to the script section:

```javascript
function openSettingsFocusedOn(parsivalProject) {
  openSettings();  // existing function that shows the modal
  // Wait one tick for DOM to render, then scroll & focus.
  setTimeout(() => {
    const rows = document.querySelectorAll('#project-list [data-project-name]');
    for (const row of rows) {
      if (row.dataset.projectName === parsivalProject) {
        row.scrollIntoView({ behavior: 'smooth', block: 'center' });
        const sel = row.querySelector('.lancellmot-select');
        if (sel) {
          sel.focus();
          row.style.transition = 'background 0.6s';
          row.style.background = 'rgba(64, 184, 255, 0.12)';
          setTimeout(() => { row.style.background = ''; }, 1400);
        }
        return;
      }
    }
  }, 150);
}
```

Verify that project rows have `data-project-name="${p.name}"` set — if not, add it to the row template in the rendering code (same place Task 6 modified).

- [ ] **Step 2: Rebuild and visually verify**

Run: `docker compose up -d --build parsival-api`

Click an unmapped "Map →" chip. Expected:
- Settings modal opens
- Projects section is scrolled into view
- The project row for the clicked tag flashes blue briefly
- The lancellmot dropdown on that row is focused (keyboard-ready)

- [ ] **Step 3: Commit**

```bash
git add web/page/index.html
git commit -m "feat(#43): click-through from unmapped chip to Settings project row"
```

---

## Task 9: Documentation

**Files:**
- Modify: `README.md`
- Modify: `web/page/index.html` (help modal section)

- [ ] **Step 1: Add lancellmot integration section to README.md**

Find the existing "Configuration" or "Features" section. Append:

```markdown
### lancellmot document linking

Situation cards display a chip linking to related documents in lancellmot.
Resolution is via an explicit alias table — each parsival project is mapped
to a lancellmot project in Settings → Projects. No fuzzy matching.

- **Setup:** set `LANCELLMOT_URL` in env (default `http://host.docker.internal:8080`)
- **Mapping:** Settings → Projects → pick lancellmot project per row
- **Unmapped tags:** show "Map →" chip on cards; click to jump to Settings
- **lancellmot down:** chip renders amber with "unreachable" tooltip
```

- [ ] **Step 2: Add help-modal entry**

In the help modal table (search for `<table>` inside the help section), add a row:

```html
<tr><td>Map →</td><td>Project tag has no lancellmot mapping. Click to open Settings and pick the lancellmot counterpart. Mappings are explicit — parsival never guesses.</td></tr>
<tr><td>⚠ unreachable</td><td>Couldn't reach lancellmot. Click the chip to retry, or verify lancellmot is running.</td></tr>
```

- [ ] **Step 3: Commit**

```bash
git add README.md web/page/index.html
git commit -m "docs(#43): README + help-modal entries for lancellmot chip"
```

---

## Task 10: Final verification + PR

- [ ] **Step 1: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 2: Visual smoke test, all states**

- Resolved chip with popover and doc links
- Unmapped chip → Settings focus
- Unreachable chip when lancellmot is down
- Settings dropdown updates alias round-trip
- Multi-project situation shows multiple chips

- [ ] **Step 3: Push branch and open PR**

```bash
git push
gh pr create --repo rcanterberryhall/hexcaliper-parsival \
  --title "feat(#43): lancellmot document chip on situation cards" \
  --body "$(cat <<'EOF'
## Summary
- Strict alias table mapping parsival project tags → lancellmot projects
- Situation cards display chip with three states (resolved / unmapped / unreachable)
- Settings gains a dropdown column per project row for mapping
- Click unmapped chip → Settings opens focused on that project row

## Spec
docs/superpowers/specs/2026-04-19-parsival-lancellmot-document-chip-design.md

Closes #43.
EOF
)"
```

---

## Self-Review Notes

**Spec coverage:** Every item in the spec's "Architecture" section maps to a task:
- Data model → Task 1 ✓
- HTTP client → Task 3 ✓
- 5 API routes → Tasks 4 + 5 ✓
- UI changes (card + Settings + click-through) → Tasks 6 + 7 + 8 ✓
- Error handling (amber chip, unreachable responses) → covered across Tasks 3, 5, 7 ✓
- Testing → covered in Tasks 1-5 ✓
- Docs → Task 9 ✓

**Placeholder scan:** No TBDs or "TODO" stubs remain. `escapeHtml` is flagged with a fallback note in Task 7 Step 2.

**Type consistency:** Function names used consistently — `upsert_lancellmot_alias`, `get_lancellmot_alias_for_tag`, `list_lancellmot_aliases`, `delete_lancellmot_alias`. Route paths consistent. Chip status strings ("ok" / "unmapped" / "unreachable") match across server + client.
