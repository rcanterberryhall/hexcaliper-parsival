"""A failed Situations, Assigned or Briefing load is stated, never rendered as an answer.

Covers parsival#112. All three loaders ended in ``catch(e) { console.warn(...) }``
and skipped their render, so what the operator saw was decided by each pane's
*static* markup: ``#situations-view`` and ``#intel-view`` ship a
``loading-placeholder`` spinner and ``#assigned-view`` ships nothing at all. One
silent catch therefore produced an eternal "Loading situations…", a blank
Assigned tab, and a stale Intel count.

Worse, ``setProject`` and ``onSortChange`` call ``renderSituations`` directly
against ``allSituations``, which is ``[]`` until a load succeeds — so the first
click after a failure printed "No situations detected yet", asserting that no
cross-source situation exists. ``GET /situations`` legitimately answers ``[]``
when nothing has formed, so the frontend was discarding the only thing that
separated "none" from "the request failed".

Every invariant below is derived from the shipped ``web/page/index.html`` rather
than hardcoded, so a rename or a re-introduced swallow fails here instead of
passing against a stale literal.
"""

import re
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "web" / "page" / "index.html"

# Each loader, the module state it owns, and the pane it is responsible for
# resolving. The pane ids are what make the severity concrete: a loader that
# skips its render hands the operator whatever that element's static markup says.
LOADERS = {
    "loadSituations": ("allSituations", "situations-view"),
    "loadAssigned": ("", "assigned-view"),
    "loadIntel": ("allIntel", "intel-view"),
}


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def style(page: str) -> str:
    """The page's CSS, so a rule check cannot be satisfied by JS that emits the name."""
    blocks = re.findall(r"<style\b[^>]*>(.*?)</style>", page, re.S)
    assert blocks, "the page has no <style> block"
    return "\n".join(blocks)


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


def _static_markup(page: str, element_id: str) -> str:
    """The shipped contents of a view container, before any JS touches it."""
    match = re.search(rf'<div id="{re.escape(element_id)}"[^>]*>(.*?)</div>\s*$', page, re.M)
    if match is None:
        match = re.search(rf'<div id="{re.escape(element_id)}"[^>]*>(.*)', page)
    assert match, f"no #{element_id} container in {PAGE.name}"
    return match.group(1)


# ── The failure must survive as a failure ─────────────────────────────────────


@pytest.mark.parametrize("loader", sorted(LOADERS))
def test_loader_does_not_report_only_to_the_console(page: str, loader: str) -> None:
    """console.warn is invisible to the operator the failure happened to."""
    body = _body(page, loader)
    catch = re.search(r"catch\s*\([^)]*\)\s*\{(.*)\}", body, re.S)
    assert catch, f"{loader} has no catch block"
    handler = catch.group(1)
    assert "statusMsg" in handler or "_loadErrorHtml" in handler, (
        f"{loader}'s failure path reaches neither #statusMsg nor a failure pane, so "
        "the only trace of a failed load is a console line"
    )


@pytest.mark.parametrize("loader,state", [(k, v[0]) for k, v in sorted(LOADERS.items()) if v[0]])
def test_loader_does_not_spell_a_failure_as_an_empty_result(
    page: str, loader: str, state: str
) -> None:
    """An empty list is a legitimate answer, so a failure must not become one."""
    body = _body(page, loader)
    catch = re.search(r"catch\s*\([^)]*\)\s*\{(.*)\}", body, re.S)
    assert catch, f"{loader} has no catch block"
    swallow = re.search(rf"{re.escape(state)}\s*=\s*(\[\]|\{{\}})", catch.group(1))
    assert not swallow, (
        f"{loader} assigns the empty value to {state} from its catch, so a transport "
        "failure is indistinguishable from a genuinely empty result"
    )


def test_situations_failure_records_the_servers_reason(page: str) -> None:
    body = _body(page, "loadSituations")
    assert "_sitLoadError" in body, "loadSituations does not record that a load failed"
    assert "e.message" in body, (
        "the recorded reason does not carry the server's message, so the operator is "
        "told something failed but not what"
    )


# ── Generation identity ───────────────────────────────────────────────────────


def test_situations_load_claims_a_token_before_its_first_await(page: str) -> None:
    """include_resolved returns MORE rows, so the abandoned load is the slower one."""
    body = _body(page, "loadSituations")
    assert "++_sitSeq" in body, (
        "loadSituations does not claim a sequence number, so it cannot tell it was "
        "overtaken — ticking Show resolved and unticking it renders the ticked result"
    )
    assert body.index("++_sitSeq") < body.index("await "), (
        "the sequence number must be claimed before the first await"
    )


def test_situations_generation_check_sits_between_fetch_and_store(page: str) -> None:
    """Placement is the whole point: after the await, before anything is stored."""
    body = _body(page, "loadSituations")
    check = re.search(r"if \(seq !== _sitSeq\) return", body)
    assert check, "loadSituations never compares its sequence number against the latest"
    store = re.search(r"^\s+allSituations\s*=", body, re.M)
    assert store, "loadSituations never stores a result"
    assert body.index("await ") < check.start(), (
        "the sequence check runs before the fetch, so it cannot detect an overtaking"
    )
    assert check.start() < store.start(), (
        "the sequence check runs after the result has already been stored, which is "
        "exactly the write it exists to prevent"
    )


def test_situations_load_captures_its_filters_before_awaiting(page: str) -> None:
    """A query rebuilt after the await can describe a filter state nobody chose."""
    body = _body(page, "loadSituations")
    first_await = body.index("await ")
    tail = body[first_await:]
    live_reads = [r for r in ("showHidden", "showResolved") if re.search(rf"\b{r}\b", tail)]
    assert not live_reads, (
        "loadSituations re-reads the live filter controls ("
        + ", ".join(live_reads)
        + ") after awaiting, instead of the state it captured at entry"
    )


# ── No control may assert a filter that was never applied ─────────────────────


def test_a_failed_filter_driven_load_reverts_the_controls(page: str) -> None:
    """A checkbox moves natively, so leaving it alone is already a false claim."""
    body = _body(page, "loadSituations")
    catch = re.search(r"catch\s*\([^)]*\)\s*\{(.*)\}", body, re.S)
    assert catch, "loadSituations has no catch block"
    assert "_revertSituationFilters" in catch.group(1), (
        "a failed filter-driven load leaves Show hidden / Show resolved ticked over a "
        "list that was never refetched, and nothing on screen disagrees"
    )
    revert = _body(page, "_revertSituationFilters")
    for control in ("showHidden", "showResolved"):
        assert control in revert, f"_revertSituationFilters does not restore {control}"
    assert "_sitApplied" in revert, (
        "_revertSituationFilters does not restore the filter state the displayed data "
        "was actually fetched under"
    )


def test_situations_records_the_filters_its_data_describes(page: str) -> None:
    body = _body(page, "loadSituations")
    applied = re.search(r"_sitApplied\s*=\s*filters", body)
    assert applied, "loadSituations never records which filters the stored data describes"
    store = re.search(r"^\s+allSituations\s*=", body, re.M)
    assert store and store.start() < applied.start(), (
        "the applied filters must be recorded alongside the data they describe"
    )


@pytest.mark.parametrize("handler", ["onShowResolved", "onShowHidden"])
def test_checkbox_handlers_mark_their_load_as_filter_driven(page: str, handler: str) -> None:
    """Only a filter-driven failure may revert a control; a background poll may not."""
    body = _body(page, handler)
    calls = re.findall(r"load(?:Situations|Intel)\((.*?)\)", body)
    assert calls, f"{handler} triggers no situations or intel load"
    for args in calls:
        assert "fromFilter" in args, (
            f"{handler} loads without marking the load filter-driven, so a failure "
            "cannot put the checkbox back"
        )


# ── Every pane is resolved on every render ────────────────────────────────────


def test_situations_pane_ships_a_spinner_that_a_failure_must_clear(page: str) -> None:
    """The premise of mechanism 1: skipping the render leaves this on screen."""
    assert "loading-placeholder" in _static_markup(page, "situations-view"), (
        "if #situations-view no longer ships a spinner this test's premise has moved; "
        "re-derive what a skipped render now leaves on screen"
    )


def test_render_situations_resolves_a_failure_before_any_other_outcome(page: str) -> None:
    body = _body(page, "renderSituations")
    error = body.find("_sitLoadError")
    assert error >= 0, (
        "renderSituations ignores the recorded failure, so a failed load leaves the "
        "'Loading situations…' spinner turning for the life of the page"
    )
    for later in ("empty-msg", "situationCard(s)"):
        assert error < body.index(later), (
            f"renderSituations reaches {later} before handling a failed load, so the "
            "failure is rendered as an answer"
        )


def test_render_assigned_resolves_a_failure(page: str) -> None:
    """#assigned-view ships empty, so a skipped render is a blank tab."""
    assert _static_markup(page, "assigned-view").strip() == "", (
        "if #assigned-view now ships markup, re-derive what a failed load shows"
    )
    body = _body(page, "renderAssigned")
    error = body.find("_assignedError")
    assert error >= 0, "renderAssigned ignores the recorded failure, leaving the tab blank"
    assert error < body.index("empty-msg"), (
        "renderAssigned reaches its empty state before handling a failed load, so a "
        "failure is reported as 'No assigned tasks.'"
    )


def test_briefing_failure_states_the_reason_and_offers_a_retry(page: str) -> None:
    body = _body(page, "loadBriefing")
    catch = re.search(r"catch\s*\([^)]*\)\s*\{(.*)\}", body, re.S)
    assert catch, "loadBriefing has no catch block"
    handler = catch.group(1)
    assert "e.message" in handler, (
        "the briefing failure states no reason, so a 502 and a generation error read identically"
    )
    assert "_loadErrorHtml" in handler, "the briefing failure offers no way to retry"


def test_failure_panes_carry_a_retry_control(page: str) -> None:
    body = _body(page, "_loadErrorHtml")
    assert "onclick" in body and "retryCall" in body, (
        "the failure pane has no retry control, so the only recovery is a page reload"
    )


# ── The two empty cases its siblings already distinguish ──────────────────────


def test_render_situations_distinguishes_empty_from_filtered_out(page: str) -> None:
    """renderTodos and renderItems both branch here; renderSituations did not."""
    body = _body(page, "renderSituations")
    empty = re.search(r"if \(!visible\.length\) \{(.*?)\n  \}", body, re.S)
    assert empty, "renderSituations has no empty-state branch"
    block = empty.group(1)
    assert re.search(r"situations\.length === 0", block), (
        "renderSituations prints one message whether nothing has formed or nothing "
        "survives the filter, so a filtered operator is told no situation exists"
    )
    assert "Nothing matches this filter." in block, (
        "the filtered case needs the wording its sibling renderers already use"
    )


def test_siblings_still_distinguish_the_two_empty_cases(page: str) -> None:
    """Guard: the wording copied above must stay copied from somewhere real."""
    for sibling in ("renderTodos", "renderItems"):
        body = _body(page, sibling)
        assert "Nothing matches this filter." in body, (
            f"{sibling} no longer distinguishes a filtered-out list from an empty one, "
            "so the convention this fix follows has moved"
        )


def test_a_genuinely_empty_situation_list_still_reads_as_empty(page: str) -> None:
    """Guard: reporting failures honestly must not turn real emptiness into an error."""
    assert "No situations detected yet." in page, (
        "the genuine empty wording was removed, so a workspace with no situations now "
        "reports a problem it does not have"
    )
    assert "No assigned tasks." in page


# ── The failure markup is styled ──────────────────────────────────────────────


@pytest.mark.parametrize("helper", ["_loadErrorHtml", "_staleBannerHtml"])
def test_failure_markup_classes_are_styled(page: str, style: str, helper: str) -> None:
    """A class with no rule renders the failure as unstyled body text."""
    body = _body(page, helper)
    emitted = set()
    for attr in re.findall(r'class="([^"${}]+)"', body):
        emitted.update(attr.split())
    new = sorted(c for c in emitted if "error" in c or "stale" in c)
    assert new, f"{helper} emits no failure-specific class"
    for cls in new:
        assert re.search(rf"\.{re.escape(cls)}[\s,.:{{]", style), (
            f"{helper} emits .{cls} but the page's CSS has no rule for it"
        )
