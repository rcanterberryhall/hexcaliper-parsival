"""The Contacts pane keeps its controls out of the region a reload repaints.

Regression cover for parsival#108. `loadContacts` used to blank `#contacts-view`,
the container that `renderContactsView` had built the search input, the four
action buttons and `#contactsEditor` into. Assigning `innerHTML` detaches the
nodes already inside, so a debounced keystroke destroyed the very input that
fired it — costing focus, the caret, anything typed while the request was in
flight, and an open editor's unsaved field values.

The invariant these tests hold is structural, because that is what the defect
was: the controls that must survive a reload live in the static markup, and the
repainted region (`#contactsTable`) is a strict subset of the pane. A future
edit that moves the search box back inside the repainted region fails here
rather than in a browser.

Two tests must pass against the pre-fix file as well — the pane still offers
search and both rebuild actions, and `/contacts` still reports an unfiltered
total — so the suite cannot be satisfied by deleting the feature it protects.
"""

import re
from pathlib import Path

import db
import pytest

INDEX = Path(__file__).resolve().parents[1] / "web" / "page" / "index.html"


@pytest.fixture(scope="module")
def html():
    return INDEX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def lines(html):
    return html.split("\n")


def _js_function_body(lines, declaration):
    """Return the source of a top-level JS function declared at column 0.

    Reads from the declaration to the next line that is exactly ``}`` at column
    zero. Brace counting is unreliable in this file because the render helpers
    are built out of template literals containing braces of their own.
    """
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(declaration)),
        None,
    )
    assert start is not None, f"declaration not found: {declaration}"
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i] == "}"),
        None,
    )
    assert end is not None, f"unterminated function: {declaration}"
    return "\n".join(lines[start : end + 1])


def _contacts_view_markup(lines):
    """Return the static markup of the #contacts-view container."""
    start = next(
        (i for i, line in enumerate(lines) if 'id="contacts-view"' in line),
        None,
    )
    assert start is not None, "#contacts-view not found in the shipped markup"
    depth = 0
    for i in range(start, len(lines)):
        depth += len(re.findall(r"<div", lines[i]))
        depth -= len(re.findall(r"</div>", lines[i]))
        if depth <= 0:
            return "\n".join(lines[start : i + 1])
    pytest.fail("#contacts-view is never closed")


# ── the controls live in the static markup ───────────────────────────────────


@pytest.mark.parametrize(
    "element_id",
    ["contactsSearch", "contactsCount", "contactsEditor", "contactsTable"],
)
def test_contacts_controls_are_static_markup(lines, element_id):
    """Each control the pane must keep across a reload is in the markup.

    A control emitted by `renderContactsView` instead is destroyed every time
    the view reloads, which for the search input means on its own keystroke.
    """
    assert f'id="{element_id}"' in _contacts_view_markup(lines)


def test_contacts_pane_is_only_ever_shown_or_hidden(html):
    """The JS touches #contacts-view to toggle visibility and nothing else.

    Stated this way rather than as "no `innerHTML` assignment" because the
    defect bound the container to a local first — `const view =
    $('contacts-view')` — so a search for the assignment spelled out in full
    finds nothing while the pane is still being blanked.
    """
    uses = [m.end() for m in re.finditer(r"\$\(['\"]contacts-view['\"]\)", html)]
    assert uses, "the pane is never looked up, so this invariant is not being tested"
    for end in uses:
        assert html[end:].startswith(".style.display"), (
            f"#contacts-view is used for something other than visibility: {html[end : end + 60]!r}"
        )


def test_render_does_not_rebuild_the_search_box_or_the_editor(lines):
    """`renderContactsView` emits rows only.

    Re-emitting `id="contactsSearch"` would replace the live input with a fresh
    element — the text would reappear from its `value` attribute while focus and
    the caret were silently lost. Re-emitting an empty `id="contactsEditor"`
    would discard an open edit.
    """
    body = _js_function_body(lines, "function renderContactsView(")
    assert 'id="contactsSearch"' not in body
    assert 'id="contactsEditor"' not in body


def test_render_targets_the_table_region(lines):
    body = _js_function_body(lines, "function renderContactsView(")
    assert "$('contactsTable')" in body


# ── a reload cannot be overtaken, and cannot lose the toolbar ────────────────


def test_load_contacts_checks_a_generation_token_before_rendering(lines):
    """The token is compared after the fetch and before the first render.

    Placement is the whole point: a check before the `await` cannot know it has
    been overtaken. Clearing the box issues the broadest query and so the
    slowest one, which is exactly the load that would otherwise land last and
    draw over a narrower search typed after it.
    """
    body = _js_function_body(lines, "async function loadContacts(")
    arm = body.index("++_contactsSeq")
    fetched = body.index("await api(")
    check = body.index("seq !== _contactsSeq")
    rendered = body.index("renderContactsView(")
    assert arm < fetched, "the generation token is taken before the request"
    assert fetched < check < rendered, "the token is checked between the fetch and the render"


def test_load_contacts_failure_keeps_the_toolbar(lines):
    """The error message replaces the rows, not the pane.

    Blanking the pane leaves an error line and no control to retry with — the
    only way back was switching views and returning.
    """
    body = _js_function_body(lines, "async function loadContacts(")
    catch = body.index("catch (e)")
    assert "$('contactsTable')" in body[:catch], "the spinner targets the table region"
    assert "contacts-view" not in body[catch:], "the catch must not touch the whole pane"


def test_load_contacts_spinner_targets_the_table_region(lines):
    body = _js_function_body(lines, "async function loadContacts(")
    assert "Loading contacts" in body
    assert "$('contacts-view')" not in body


# ── sorting reports the count the load reported ──────────────────────────────


def test_sort_path_reports_the_backend_total(lines):
    """`_rerenderContacts` passes the stored total, not the row count.

    Passing `_contactsCache.length` made clicking a column header rewrite
    "7 of 214 contacts" into "7 of 7 contacts" — the same rows, now claiming the
    database holds only those that matched.
    """
    body = _js_function_body(lines, "function _rerenderContacts(")
    assert "_contactsCache.length" not in body
    assert "_contactsTotal" in body


# ── guards: these hold against the pre-fix file too ──────────────────────────


def test_contacts_pane_still_offers_search_and_both_rebuild_actions(html):
    """The pane keeps the controls the fix moved, rather than losing them."""
    assert 'oninput="onContactsSearch(this.value)"' in html
    for action in ("newContact()", "rebuildContacts()", "reparseSignatures()"):
        assert f'onclick="{action}"' in html


def test_contacts_endpoint_total_is_the_unfiltered_count(client):
    """`/contacts` reports matched rows and the whole-table count separately.

    This is the fact that makes the footer's "N of M contacts" meaningful, and
    so the fact that made the sort path's substitution a misreport rather than a
    harmless rename.
    """
    for name, employer in [
        ("Rob Ackerman", "Acme Controls"),
        ("Alice Byrne", "Acme Controls"),
        ("Tom Quist", "Northwind Ltd"),
    ]:
        db.insert_contact({"name": name, "employer": employer})

    body = client.get("/contacts", params={"query": "Northwind"}).json()
    assert len(body["contacts"]) == 1
    assert body["total"] == 3
