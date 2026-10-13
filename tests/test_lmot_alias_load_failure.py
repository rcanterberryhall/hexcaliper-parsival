"""The Settings lancellmot dropdown must not render a failed alias read as "no mapping".

Covers parsival#118: ``populateLancellmotDropdowns`` collapsed a failed
``GET /lancellmot/aliases`` into an empty list, which left every project's
select on its ``— none —`` option — a claim that nothing is mapped — and wrote
``''`` into ``dataset.current``, the value parsival#96's refused-save revert
treats as the last mapping the server confirmed.

The backend premise tests come first: they establish that the two requests the
function issues fail independently (only the alias read takes ``db.lock``), that
the board's chip endpoint resolves the same mapping server-side and so can
contradict the dropdown, and that the alias upsert stores whatever project id it
is handed — which is why the unresolved placeholder must never be submitted.
"""

import threading
from pathlib import Path
from unittest.mock import patch

import db
import lancellmot_client
import pytest

_FRONTEND = Path(__file__).resolve().parents[1] / "web" / "page" / "index.html"


def _frontend_lines() -> list[str]:
    return _FRONTEND.read_text(encoding="utf-8").split("\n")


def _slice_between(start_prefix: str, end_prefix: str) -> str:
    """Return the shipped source between two signature markers.

    Slicing the real asset means these tests exercise shipped code; asserting
    the markers were found means a rename fails loudly instead of silently
    testing an empty string.
    """
    lines = _frontend_lines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(start_prefix)), -1)
    assert start >= 0, f"marker not found: {start_prefix!r}"
    end = next(
        (i for i, ln in enumerate(lines) if i > start and ln.startswith(end_prefix)),
        -1,
    )
    assert end > start, f"end marker not found after {start_prefix!r}: {end_prefix!r}"
    return "\n".join(lines[start:end])


@pytest.fixture
def populate_src() -> str:
    """The shipped body of ``populateLancellmotDropdowns``."""
    return _slice_between(
        "async function populateLancellmotDropdowns() {",
        "// Persist (or clear) a project's lancellmot alias",
    )


@pytest.fixture
def change_handler_src() -> str:
    """The shipped body of ``onLancellmotSelectChange``."""
    return _slice_between(
        "async function onLancellmotSelectChange(sel) {",
        "async function clearNoiseFilter() {",
    )


# ── backend premises ─────────────────────────────────────────────────────────


def test_alias_list_endpoint_reports_the_mappings_the_dropdown_starts_from(client):
    """``GET /lancellmot/aliases`` is the only source for the select's initial value."""
    db.upsert_lancellmot_alias("Line7", "lmot-7", "line-7-upgrade")
    resp = client.get("/lancellmot/aliases")
    assert resp.status_code == 200
    by_tag = {a["parsival_project"]: a for a in resp.json()}
    assert by_tag["Line7"]["lancellmot_project_id"] == "lmot-7"


def test_projects_proxy_answers_503_when_lancellmot_is_down(client):
    """The projects fetch has a failure contract the UI already renders honestly.

    A 503 is what makes the select disable itself with a visible "lancellmot
    unreachable" option (parsival#43). The alias read had no equivalent, which
    is the asymmetry parsival#118 is about.
    """
    with patch.object(
        lancellmot_client,
        "list_projects",
        side_effect=lancellmot_client.LancellmotUnavailable("down"),
    ):
        resp = client.get("/lancellmot/projects")
    assert resp.status_code == 503
    assert resp.json() == {"error": "unreachable"}


def test_docs_for_tag_resolves_the_mapping_server_side(client):
    """The board's chip reads the alias table through its own endpoint.

    So a mapped project's chip can resolve documents at the same moment the
    Settings dropdown claims the project is unmapped — the two panes read one
    table through two endpoints, and only one of them was honest about failure.
    """
    db.upsert_lancellmot_alias("Line7", "lmot-7", "line-7-upgrade")
    with patch.object(lancellmot_client, "list_documents", return_value=[{"id": "d1"}]):
        mapped = client.get("/lancellmot/docs-for-tag", params={"tag": "Line7"})
    assert mapped.status_code == 200
    assert mapped.json()["status"] == "ok"
    assert mapped.json()["project_name"] == "line-7-upgrade"

    unmapped = client.get("/lancellmot/docs-for-tag", params={"tag": "NotMapped"})
    assert unmapped.json()["status"] == "unmapped"


@pytest.mark.timeout(30)
def test_projects_answers_while_db_lock_is_held_but_the_alias_read_blocks(client):
    """The two requests fail independently, which is what makes parsival#118 reachable.

    ``/lancellmot/projects`` is a network proxy that takes no lock, while
    ``/lancellmot/aliases`` is a local SQLite read taken under ``db.lock`` — a
    re-entrant lock held across ``seeder.apply``'s per-item retag loop. So a long
    write leaves the dropdown fully populated with real projects while the
    request carrying the mapping is the one that stalls into a proxy timeout.
    """
    db.upsert_lancellmot_alias("Line7", "lmot-7", "line-7-upgrade")
    held = threading.Event()
    release = threading.Event()

    def hog():
        with db.lock:
            held.set()
            release.wait(20)

    hog_thread = threading.Thread(target=hog, daemon=True)
    hog_thread.start()
    try:
        assert held.wait(5), "lock was never acquired by the holder thread"

        with patch.object(lancellmot_client, "list_projects", return_value=[{"id": "lmot-7"}]):
            projects = client.get("/lancellmot/projects")
        assert projects.status_code == 200, "the projects proxy must not need db.lock"

        alias_result: dict = {}

        def read_aliases():
            alias_result["code"] = client.get("/lancellmot/aliases").status_code

        reader = threading.Thread(target=read_aliases, daemon=True)
        reader.start()
        reader.join(1.0)
        assert reader.is_alive(), "the alias read must block while db.lock is held"
    finally:
        release.set()
        hog_thread.join(5)

    reader.join(10)
    assert alias_result.get("code") == 200


def test_alias_upsert_stores_the_project_id_verbatim(client):
    """Nothing validates the stored id, so the placeholder must never be submitted.

    ``PUT /lancellmot/aliases`` writes ``lancellmot_project_id`` through as given,
    which is why the unresolved sentinel is fenced in the change handler rather
    than relying on the endpoint to reject it.
    """
    resp = client.put(
        "/lancellmot/aliases",
        json={
            "parsival_project": "Line7",
            "lancellmot_project_id": "__not_a_real_id__",
            "lancellmot_project_name": "whatever",
        },
    )
    assert resp.status_code == 200
    assert db.get_lancellmot_alias_for_tag("Line7")["lancellmot_project_id"] == "__not_a_real_id__"


# ── the shipped frontend ─────────────────────────────────────────────────────


def test_alias_load_failure_is_recorded_not_swallowed(populate_src):
    """The failed alias read must leave a reason behind, not an empty list."""
    assert "try { aliases = await api('/lancellmot/aliases'); } catch (_) {}" not in populate_src
    assert "aliasError" in populate_src, "the alias read's failure must be captured"
    assert "if (aliasError) {" in populate_src


def test_failed_alias_load_selects_an_unresolved_placeholder(populate_src):
    """On failure the control holds a value meaning "not read", not ``''``."""
    lines = _frontend_lines()
    decl = [ln for ln in lines if ln.startswith("const LMOT_ALIAS_UNRESOLVED")]
    assert len(decl) == 1, "the unresolved sentinel must have exactly one definition"

    assert "unresolved.disabled = true;" in populate_src, "the placeholder must not be pickable"
    assert "sel.value = LMOT_ALIAS_UNRESOLVED;" in populate_src

    # dataset.current is parsival#96's revert target: it must record the
    # placeholder, never a fabricated empty mapping.
    assert "sel.dataset.current = sel.value;" in populate_src
    current_at = populate_src.index("sel.dataset.current = sel.value;")
    assert populate_src.index("sel.value = LMOT_ALIAS_UNRESOLVED;") < current_at


def test_unresolved_sentinel_literal_is_not_duplicated():
    """One definition, every other use by identifier (the vocabulary-drift lesson)."""
    src = _FRONTEND.read_text(encoding="utf-8")
    decl = [ln for ln in src.split("\n") if ln.startswith("const LMOT_ALIAS_UNRESOLVED")]
    literal = decl[0].split("'")[1]
    assert src.count(f"'{literal}'") == 1, "the sentinel's literal must appear only in its const"
    assert src.count("LMOT_ALIAS_UNRESOLVED") >= 3


def test_change_handler_refuses_the_unresolved_sentinel(change_handler_src):
    """A change carrying the placeholder must write nothing to the alias table."""
    assert "if (sel.value === LMOT_ALIAS_UNRESOLVED) return;" in change_handler_src
    guard_at = change_handler_src.index("if (sel.value === LMOT_ALIAS_UNRESOLVED) return;")
    for write in ("await api('/lancellmot/aliases'", "await api('/lancellmot/aliases/"):
        if write in change_handler_src:
            assert guard_at < change_handler_src.index(write), "the guard must precede the write"


def test_successful_alias_load_restores_the_select_title(populate_src):
    """A later successful load must not keep the earlier failure's text on screen."""
    assert populate_src.count("sel.title") >= 2, "both branches must set the title"
    assert "sel.title = 'Map this project to a lancellmot project';" in populate_src


def test_projects_unreachable_branch_is_preserved(populate_src):
    """parsival#43's behaviour is unchanged: no project list means a disabled select."""
    assert "if (projects === null) {" in populate_src
    assert "sel.disabled = true;" in populate_src
    assert "err.textContent = 'lancellmot unreachable';" in populate_src
