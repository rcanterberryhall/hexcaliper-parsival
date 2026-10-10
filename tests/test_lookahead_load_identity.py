"""The look-ahead board never paints one subject's data under another's label.

Covers parsival#110: ``_laLoadData`` had thirteen call sites and no generation
token, so the board showed whichever response landed last while the toolbar
showed the newest click; it branched on ``_LA.mode``/``_LA.project`` at fetch
entry while ``renderLookahead`` re-read them at paint time; and every ``catch``
collapsed a transport failure into the same empty value an empty window
produces, so a 502 was rendered as "No cards in the 14-day window for <project>"
with an invitation to add one.

Every invariant below is derived from the shipped ``web/page/index.html`` rather
than hardcoded, so a future rename or a re-introduced swallow fails here instead
of passing against a stale literal.
"""

import re
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "web" / "page" / "index.html"

# The loaders whose failure path must not be spelled as an empty result, mapped
# to the `_LA` slice each one owns.
LOADERS = {
    "_laLoadData": ("cards", "overview", "shifts"),
    "_laRefreshProjects": ("projects",),
    "_laLoadResources": ("resources",),
}


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def _body(page: str, name: str) -> str:
    """Return the source of one top-level function, found by its signature."""
    pattern = re.compile(rf"^(?:async )?function {re.escape(name)}\(", re.M)
    match = pattern.search(page)
    assert match, f"{name} is not a top-level function in {PAGE.name}"
    lines = page[match.start() :].split("\n")
    for i, line in enumerate(lines[1:], start=1):
        if line == "}":
            return "\n".join(lines[: i + 1])
    pytest.fail(f"no closing brace found for {name}")


def test_load_data_claims_a_generation_token_at_entry(page: str) -> None:
    """A load must be able to recognise that a later one has superseded it."""
    body = _body(page, "_laLoadData")
    assert "++_laDataSeq" in body, (
        "_laLoadData does not claim a sequence number, so it cannot tell it was "
        "overtaken — the board shows whichever response lands last"
    )
    entry = body.index("++_laDataSeq")
    first_await = body.index("await ")
    assert entry < first_await, "the sequence number must be claimed before the first await"


def test_generation_check_sits_between_the_fetches_and_the_assignment(page: str) -> None:
    """Placement is the whole point: after the awaits, before anything is stored."""
    body = _body(page, "_laLoadData")
    check = re.search(r"if \(seq !== _laDataSeq\) return", body)
    assert check, "_laLoadData never compares its sequence number against the latest"
    last_await = body.rindex("await ")
    store = re.search(r"Object\.assign\(_LA,|^\s+_LA\.\w+\s*=", body, re.M)
    assert store, "_laLoadData never stores a result"
    assert last_await < check.start(), (
        "the sequence check runs before the last fetch, so a response that arrives "
        "afterwards is still written to the board"
    )
    assert check.start() < store.start(), (
        "the sequence check runs after the result has already been stored, which is "
        "exactly the write it exists to prevent"
    )


def test_load_data_builds_every_url_from_the_captured_subject(page: str) -> None:
    """A helper that re-reads mutable globals can request two subjects in one load."""
    body = _body(page, "_laLoadData")
    live_reads = sorted(set(re.findall(r"_LA\.(mode|project|rangeStart|rangeEnd)\b", body)))
    assert not live_reads, (
        "_laLoadData reads the mutable selection "
        f"({', '.join('_LA.' + r for r in live_reads)}) instead of the subject it "
        "captured at entry, so one load can issue requests for two different "
        "windows or projects"
    )
    assert "_laSubject()" in body, "_laLoadData does not capture the subject it is loading for"


@pytest.mark.parametrize("loader,slices", sorted(LOADERS.items()))
def test_no_loader_spells_a_failure_as_an_empty_result(
    page: str, loader: str, slices: tuple
) -> None:
    """An empty window and a 502 must not resolve to the same value."""
    body = _body(page, loader)
    for name in slices:
        swallow = re.search(rf"catch\s*\([^)]*\)\s*{{[^}}]*_LA\.{name}\s*=\s*(\[\]|{{}})", body)
        assert not swallow, (
            f"{loader} assigns the empty value to _LA.{name} from a catch block, so a "
            f"transport failure is indistinguishable from an empty result"
        )


def test_load_data_records_a_reason_when_it_fails(page: str) -> None:
    body = _body(page, "_laLoadData")
    assert "_LA.dataError" in body, "_laLoadData does not record why a load failed"
    assert "e.message" in body, "the recorded reason does not carry the server's message"


def test_render_compares_the_loaded_subject_before_painting(page: str) -> None:
    """A board must never be painted under a label its data does not match."""
    body = _body(page, "renderLookahead")
    compare = body.find("_laSameSubject")
    assert compare >= 0, (
        "renderLookahead paints without checking what the data on hand describes, so "
        "a filter repaint during a project switch shows the old project's cards"
    )
    for board in ("_laRenderProjectBoard()", "_laRenderOverviewBoard()"):
        assert compare < body.index(board), (
            f"renderLookahead calls {board} before confirming the data describes the "
            "subject the toolbar is showing"
        )


def test_render_states_a_failure_instead_of_an_empty_window(page: str) -> None:
    body = _body(page, "renderLookahead")
    error = body.find("_LA.dataError")
    assert error >= 0, "renderLookahead ignores the recorded failure reason"
    for board in ("_laRenderProjectBoard()", "_laRenderOverviewBoard()"):
        assert error < body.index(board), (
            f"renderLookahead reaches {board} before handling a failed load, so the "
            "failure is rendered as an empty schedule"
        )
    assert "_laRetryLoad()" in body, "the failure state offers no way to retry the load"


def test_every_load_data_call_site_repaints_the_board(page: str) -> None:
    """A reload whose result is never rendered leaves the stale board on screen."""
    lines = page.split("\n")
    unrendered = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if "_laLoadData()" not in line or stripped.startswith(("//", "*")):
            continue
        if re.match(r"^(async )?function _laLoadData\(", line):
            continue
        window = "\n".join(lines[i : i + 3])
        if "renderLookahead" not in window:
            unrendered.append(f"line {i + 1}: {line.strip()}")
    assert not unrendered, (
        "these _laLoadData() call sites reload the board's data and never repaint it, "
        "so the refreshed values do not reach the screen: " + "; ".join(unrendered)
    )


def test_failure_state_classes_are_all_styled(page: str) -> None:
    """Every class the failure markup emits needs a rule, or the message is unstyled."""
    body = _body(page, "renderLookahead")
    emitted = set(re.findall(r'class="(la-error[\w-]*)"', body))
    assert emitted, "renderLookahead emits no failure-state markup"
    for cls in sorted(emitted):
        assert re.search(rf"^\.{re.escape(cls)}\s*{{", page, re.M), (
            f"renderLookahead emits .{cls} but the page has no rule for it"
        )


def test_a_genuinely_empty_window_is_still_reported_as_empty(page: str) -> None:
    """Guard: reporting failures honestly must not turn real emptiness into an error."""
    assert "No cards in the 14-day window for" in page, (
        "the genuine empty-window wording was removed, so a project with nothing "
        "scheduled now reports a problem it does not have"
    )
    assert "No projects with cards in the 14-day window" in page
