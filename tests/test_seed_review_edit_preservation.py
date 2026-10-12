"""A failed seed Apply must keep the operator's reviewed projects and topics.

Two groups of tests live here.

The *premise* tests establish what the frontend fix rests on and pass on both
refs.  Restoring the review form is only the right answer if pressing Apply a
second time is safe, and only necessary if the backend cannot hand the edits
back: ``seeder.apply`` merges by project name instead of appending a duplicate,
and ``GET /seed/status`` reports the model's original suggestions, never the
operator's revisions of them.

The *source* tests read the shipped ``web/page/index.html`` and fail before the
fix.  They are anchored by position — the ``applying`` repaint destroys the form
*before* the request is issued, so what matters is which state the ``catch``
renders and whether it is handed the values collected ahead of that repaint.
Matching on nearby wording would green on a cosmetic message added beside the
same discarded edits.
"""

import re
from pathlib import Path

import pytest
import seeder

_PAGE = Path(__file__).resolve().parents[1] / "web" / "page" / "index.html"


# ── Frontend source helpers ───────────────────────────────────────────────────


def _extract_fn(src: str, name: str) -> str:
    """Return the full text of ``function <name>(...){...}``, async or not.

    Brace-matches to the closing brace so the slice is the whole function and
    cannot silently become "the first few lines" if the body grows.
    """
    for decl in (f"async function {name}(", f"function {name}("):
        start = src.find(decl)
        if start != -1:
            break
    assert start != -1, f"{name} not found in the shipped page"
    i = src.index("{", start)
    depth = 0
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces while slicing {name}")


def _strip_comments(js: str) -> str:
    """Return ``js`` with // and /* */ comments removed, string literals intact.

    The source assertions below are about code, not prose: a comment explaining
    why the ``error`` screen is wrong must not read as the code still rendering
    it.  Quoted text survives, so user-facing wording inside a template literal
    is still assertable.  ``test_strip_comments_actually_strips`` keeps this from
    silently degrading into the identity function and greening everything.
    """
    out: list[str] = []
    i, n = 0, len(js)
    quote = None
    while i < n:
        c = js[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(js[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and js[i + 1] == "/":
            while i < n and js[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and js[i + 1] == "*":
            end = js.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _extract_catch(fn_src: str) -> str:
    """Return the body of the first ``catch (...) { ... }`` in ``fn_src``."""
    m = re.search(r"catch\s*\(\s*\w*\s*\)\s*\{", fn_src)
    assert m is not None, "function has no catch block"
    i = m.end() - 1
    depth = 0
    while i < len(fn_src):
        if fn_src[i] == "{":
            depth += 1
        elif fn_src[i] == "}":
            depth -= 1
            if depth == 0:
                return fn_src[m.end() : i]
        i += 1
    raise AssertionError("unbalanced braces while slicing catch body")


def _review_case(src: str) -> str:
    """Return the ``case 'review':`` block of ``renderSeedState``."""
    fn = _extract_fn(src, "renderSeedState")
    start = fn.find("case 'review':")
    assert start != -1, "renderSeedState no longer has a 'review' case"
    end = fn.find("case 'applying':", start)
    assert end != -1, "the 'applying' case no longer follows 'review'"
    return fn[start:end]


@pytest.fixture(scope="module")
def page() -> str:
    return _PAGE.read_text(encoding="utf-8")


# ── Premise: a second Apply is safe, so offering a retry is correct ───────────


def test_apply_merges_a_repeated_project_instead_of_duplicating_it(client):
    """Re-applying after a failed apply must converge, not double up.

    The fix restores the review form and invites the operator to press
    **Apply & Re-analyse** again.  That is only sound because ``seeder.apply``
    keys on the lower-cased project name: a name already in settings has its
    new keywords merged in rather than being appended as a second project.
    """
    body = {
        "projects": [{"name": "Backbone", "keywords": ["bgp"], "description": "", "senders": []}],
        "topics": ["peering"],
        "retag": False,
    }
    first = client.post("/seed/apply", json=body).json()
    assert first["projects_added"] == 1

    second = client.post("/seed/apply", json=body).json()
    assert second["projects_added"] == 0, "a repeated Apply added the project twice"

    names = [p["name"] for p in client.get("/settings").json()["projects"]]
    assert names.count("Backbone") == 1, f"project duplicated across two applies: {names}"


def test_apply_merges_new_keywords_into_the_existing_project(client):
    """A retry carrying an extra keyword must widen the project, not replace it."""
    client.post(
        "/seed/apply",
        json={
            "projects": [{"name": "Backbone", "keywords": ["bgp"]}],
            "topics": [],
            "retag": False,
        },
    )
    client.post(
        "/seed/apply",
        json={
            "projects": [{"name": "Backbone", "keywords": ["mpls"]}],
            "topics": [],
            "retag": False,
        },
    )
    proj = next(p for p in client.get("/settings").json()["projects"] if p["name"] == "Backbone")
    kws = [k.lower() for k in proj.get("keywords", [])]
    assert "bgp" in kws and "mpls" in kws, f"a retry lost or replaced keywords: {kws}"


def test_seed_status_reports_suggestions_not_operator_edits(client):
    """The backend cannot hand the edits back, so the form is the only copy.

    ``GET /seed/status`` returns the job dict the map-reduce pass produced.
    Nothing in the seed API accepts a partial edit, so once the review form is
    repainted the operator's revisions exist only in the page.
    """
    seeder._seed_job = {
        "status": "review",
        "state": "review",
        "projects": [{"name": "LLM Suggested", "keywords": ["raw"]}],
        "topics": ["suggested-topic"],
    }
    st = client.get("/seed/status").json()
    assert st["projects"] == [{"name": "LLM Suggested", "keywords": ["raw"]}]
    assert st["topics"] == ["suggested-topic"]


# ── Source: the failure path restores the review form ────────────────────────


def test_failed_apply_renders_the_review_state_not_the_terminal_error_screen(page):
    """The ``catch`` must return to ``review``; ``error`` is the start-over screen.

    ``case 'error'`` offers "Try Again", which calls
    ``renderSeedState({state:'start'})`` and re-runs the whole map-reduce pass.
    Rendering it after a failed apply is what discarded the edits.
    """
    catch = _strip_comments(_extract_catch(_extract_fn(page, "applySeedConfig")))
    assert "'review'" in catch, (
        "a failed apply does not re-render the review screen, so the operator's "
        "edited projects and topics are still discarded"
    )
    assert "'error'" not in catch, (
        "a failed apply still renders the terminal error screen, whose Try Again "
        "button restarts the seed from the beginning"
    )


def test_failed_apply_is_handed_the_values_collected_before_the_repaint(page):
    """Anchored by position: the restore must use values read before the repaint.

    The ``applying`` repaint assigns ``seedBody.innerHTML`` and destroys every
    input, so a restore can only come from variables bound earlier in the
    function.  Re-reading the DOM inside the ``catch`` would find nothing.
    """
    fn = _strip_comments(_extract_fn(page, "applySeedConfig"))
    repaint = fn.find("'applying'")
    assert repaint != -1, "applySeedConfig no longer repaints to the applying screen"
    catch = _extract_catch(fn)

    assert "querySelectorAll" not in catch, (
        "the failure path re-reads the DOM, which the applying repaint has already cleared"
    )
    assert "topics" in catch, "the failure path does not carry the curated topic set back"

    restored = re.search(r"projects\s*:\s*([A-Za-z_$][\w$]*)", catch)
    assert restored is not None, "the failure path passes no projects to the review render"
    binding = re.search(rf"\b(?:const|let|var)\s+{restored.group(1)}\b", fn[:repaint])
    assert binding is not None, (
        f"the restored projects variable '{restored.group(1)}' is not bound before the "
        f"applying repaint, so it cannot hold the operator's edits"
    )


def test_every_row_is_collected_for_restore_and_only_named_rows_are_sent(page):
    """An unnamed row must survive a failed apply but stay out of the payload.

    The pre-fix code pushed a row only ``if (name)``, so the single collected
    array both omitted a half-typed row and was the only thing available to
    restore from.  Collection and the payload filter have to be separate steps.
    """
    fn = _strip_comments(_extract_fn(page, "applySeedConfig"))
    assert not re.search(r"if\s*\(\s*name\s*\)\s*\w*\.push", fn), (
        "rows are still filtered as they are collected, so a row left unnamed is "
        "dropped from the restore as well as from the payload"
    )
    assert re.search(r"\.filter\(\s*\w+\s*=>\s*\w+\.name\s*\)", fn), (
        "no name filter is applied to the payload; unnamed rows would be sent to /seed/apply"
    )


def test_review_screen_states_the_reason_the_apply_failed(page):
    """The restored form must carry the server's reason, not just reappear.

    A form that silently returns looks like the Apply button did nothing.
    """
    review = _strip_comments(_review_case(page))
    assert "st.error" in review, (
        "the review case ignores an error passed to it, so a restored form gives "
        "no indication that the apply failed"
    )
    catch = _strip_comments(_extract_catch(_extract_fn(page, "applySeedConfig")))
    assert re.search(r"error\s*:", catch), "the failure path passes no reason to the review render"


def test_failure_message_does_not_claim_nothing_was_applied(page):
    """A timeout can leave settings written and the retag unfinished.

    ``seeder.apply`` saves settings and calls ``config.apply_overrides`` before
    the per-item retag loop, so a 502 or 524 can arrive with the projects
    already persisted.  The message must not assert the stored state is
    unchanged.
    """
    review = _strip_comments(_review_case(page))
    for forbidden in (
        "nothing was applied",
        "nothing was saved",
        "no changes were applied",
        "no changes were saved",
        "were not applied",
        "were not saved",
    ):
        assert forbidden not in review.lower(), (
            f"the review screen claims {forbidden!r}, which a partial apply contradicts"
        )


# ── Guards: the fix must not be satisfied by removing the feature ────────────


def test_apply_still_shows_progress_while_the_request_is_in_flight(page):
    """Keeping the form must not cost the progress feedback.

    ``/seed/apply`` retags the corpus synchronously and is slow, so the
    ``applying`` screen is the only sign the click was received.
    """
    fn = _extract_fn(page, "applySeedConfig")
    assert "'applying'" in fn, "applySeedConfig no longer reports that it is applying"


def test_apply_still_posts_the_reviewed_configuration(page):
    """Guard: the fix must not be satisfied by never sending the payload."""
    fn = _extract_fn(page, "applySeedConfig")
    assert "'/seed/apply'" in fn, "applySeedConfig no longer posts to /seed/apply"
    assert "retag" in fn, "the retag flag is no longer sent"


def test_successful_apply_still_polls_for_the_reanalyze_transition(page):
    """Guard: the success path must still hand off to the poll."""
    fn = _extract_fn(page, "applySeedConfig")
    assert "startSeedPolling" in fn, (
        "applySeedConfig no longer starts the poll, so the backend's reanalyzing "
        "transition would never reach the screen"
    )


def test_strip_comments_actually_strips(page):
    """Guard: the source assertions are worthless if the stripper is a no-op."""
    fn = _extract_fn(page, "applySeedConfig")
    assert "//" in fn, "applySeedConfig has no comments left to strip"
    stripped = _strip_comments(fn)
    assert "//" not in stripped, "comments survived the stripper"
    assert "'/seed/apply'" in stripped, "the stripper ate a string literal"
    assert "renderSeedState" in stripped, "the stripper ate live code"
