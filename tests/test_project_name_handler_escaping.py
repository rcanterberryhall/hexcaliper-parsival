"""An apostrophe in a project name does not disable the project's controls.

Regression cover for parsival#122. Four render sites interpolated an
operator-typed project name through ``esc`` into a **single-quoted JS string
literal** inside an ``onclick`` attribute -- the sidebar project row, the
project picker's tag/untag button, and the look-ahead overview board's lane
label and card. ``Murphy's Bay`` rendered as ``setProject('Murphy's Bay',this)``,
which ends the string literal early, so the handler failed to compile and the
control became a dead click. Nothing reported it: an inline handler's body is
compiled when the click fires, not when the markup is parsed.

The invariant these tests hold is that the name never reaches a JS parsing
context at all. It travels in a ``data-project`` attribute and the handler reads
it back through ``this.dataset.project``, so no quoting rule has to be chosen
correctly at each site.

``test_html_escaping_cannot_protect_a_js_string_in_a_handler`` records why the
narrower fix was rejected: making ``esc`` escape ``'`` as ``escapeHtml`` does
would not have worked, because the parser decodes the attribute value before the
body is compiled, so ``&#39;`` arrives at the JS parser as a bare apostrophe.
That test and ``test_sidebar_still_offers_every_project_and_an_all_row`` both
pass against the pre-fix file, so the suite cannot be satisfied by deleting the
controls it protects.
"""

import html as html_module
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parents[1] / "web" / "page" / "index.html"

# The project name is operator free text: it is typed in Settings and stored
# verbatim, so an apostrophe in it is ordinary rather than adversarial.
OPERATOR_PROJECT_NAME_EXPRESSIONS = ("p.name", "row.project")


@pytest.fixture(scope="module")
def html():
    return INDEX.read_text(encoding="utf-8")


def _onclick_attributes(source):
    """Return every ``onclick`` attribute value in the source, undecoded."""
    return re.findall(r'onclick="([^"]*)"', source)


def _single_quoted_interpolations(handler_body):
    """Return the ``${...}`` expressions this handler drops into a JS string.

    Matches an interpolation delimited by single quotes on both sides, which is
    the shape that breaks: the quotes are the JS string literal's own.
    """
    return re.findall(r"'\$\{([^}]*)\}'", handler_body)


def test_no_operator_typed_project_name_sits_in_a_handler_string(html):
    """No inline handler builds a JS string literal out of a project name."""
    offenders = []
    for handler in _onclick_attributes(html):
        for expression in _single_quoted_interpolations(handler):
            if any(e in expression for e in OPERATOR_PROJECT_NAME_EXPRESSIONS):
                offenders.append((expression, handler))
    assert offenders == [], (
        "a project name is interpolated into a single-quoted JS string inside "
        "an onclick; an apostrophe in it makes the control a dead click: "
        + "; ".join(expr for expr, _ in offenders)
    )


# One per fixed render site: the sidebar row, the picker's tag/untag button
# (whose handler name is chosen in JS), and the look-ahead lane label and card.
REQUIRED_DATASET_HANDLER_CALLS = (
    "setProject(this.dataset.project,this)",
    "(this.dataset.project,this.dataset.itemId,event)",
    "_laJumpToProject(this.dataset.project)",
    "_laJumpToProject(this.dataset.project,this.dataset.cardId)",
)


@pytest.mark.parametrize("handler_call", REQUIRED_DATASET_HANDLER_CALLS)
def test_each_project_control_reads_the_name_from_the_dom(html, handler_call):
    """Every control that acts on a project takes the name from the DOM."""
    assert handler_call in html, f"no site invokes the handler as {handler_call}"


def test_every_dataset_project_read_has_a_data_project_attribute_to_read(html):
    """A handler reading ``this.dataset.project`` is emitted with the attribute.

    Checked per render statement rather than file-wide, so a site that reads the
    dataset without setting it cannot be covered by a sibling that does. The
    look-ahead card spans lines, so the attribute may sit just above the
    handler.
    """
    lines = html.split("\n")
    for i, line in enumerate(lines):
        if "this.dataset.project" not in line:
            continue
        statement = "\n".join(lines[max(0, i - 2) : i + 1])
        assert "data-project=" in statement, (
            f"line {i + 1} reads this.dataset.project but no data-project is "
            f"set on the element: {line.strip()}"
        )


def test_a_rendered_apostrophe_name_keeps_the_handler_free_of_operator_text():
    """The decoded handler body holds no operator text, so it always parses."""
    name = "Murphy's Bay"
    # What the fixed sidebar site emits for this name: esc() escapes the
    # attribute, and the handler body is a fixed string.
    markup = (
        '<div class="srow" data-project="%s" '
        'onclick="setProject(this.dataset.project,this)"></div>'
        % name.replace("'", "&#39;")
    )

    captured = {}

    class Collector(HTMLParser):
        def handle_starttag(self, tag, attrs):
            captured.update(dict(attrs))

    Collector().feed(markup)

    # The browser hands the handler body to the JS parser after decoding.
    body = captured["onclick"]
    assert "'" not in body, f"handler body still carries a quote: {body}"
    assert "Murphy" not in body, f"handler body still carries operator text: {body}"
    # And the name itself survives the round trip intact.
    assert captured["data-project"] == name


def test_html_escaping_cannot_protect_a_js_string_in_a_handler():
    """Escaping ``'`` as ``&#39;`` would not have fixed this.

    Attribute values are decoded before the handler body is compiled, so the
    entity is gone by the time the JS parser sees it. This is a property of the
    platform rather than of the file, so it holds before and after the fix and
    documents the rejected alternative.
    """
    escaped = "setProject('Murphy&#39;s Bay',this)"
    raw = "setProject('Murphy's Bay',this)"
    assert html_module.unescape(escaped) == raw
    # Three apostrophes: the two literal delimiters plus the operator's own,
    # which is what ends the string early.
    assert html_module.unescape(escaped).count("'") == 3


def test_sidebar_still_offers_every_project_and_an_all_row(html):
    """The controls this change touches still exist and still list projects."""
    assert "allProjects.forEach(p => {" in html
    assert "setProject('all',this)" in html
    assert "setProject('__none__',this)" in html
    assert "function setProject(p, el) {" in html
