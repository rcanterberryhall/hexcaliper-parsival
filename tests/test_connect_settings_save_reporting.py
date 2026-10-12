"""Connect Slack / Connect Teams must not enter OAuth on a failed settings save.

Two groups of tests live here.

The *premise* tests establish what the frontend fix rests on and pass on both
refs: the connect endpoints refuse to build an authorization URL unless the
client id is in the running config, and it is ``POST /settings`` that puts it
there.  Without them, "the redirect can only land on a 400" is an assertion
about behaviour nobody checked.

The *source* tests read the shipped ``web/page/index.html`` and fail before the
fix.  They are anchored by position — a guarded return has to come before the
``window.location.href`` assignment — because a test that merely greps for the
word "catch" near the navigation would pass on a pre-flight guard that shields
the real defect.
"""

import re
from pathlib import Path

import config
import pytest

_PAGE = Path(__file__).resolve().parents[1] / "web" / "page" / "index.html"


# ── Frontend source helpers ───────────────────────────────────────────────────


def _page_source() -> str:
    return _PAGE.read_text(encoding="utf-8")


def _extract_fn(src: str, name: str) -> str:
    """Return the full text of ``async function <name>(...){...}``.

    Brace-matches to the closing brace so the slice is the whole function and
    cannot silently become "the first few lines" if the body grows.
    """
    start = src.find(f"async function {name}(")
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


@pytest.fixture(scope="module")
def page() -> str:
    return _page_source()


@pytest.mark.parametrize("fn_name", ["connectSlack", "connectTeams"])
def test_connect_fn_has_no_empty_catch(page, fn_name):
    """An empty ``catch(e) {}`` is what discarded the failed save."""
    fn = _extract_fn(page, fn_name)
    assert not re.search(r"catch\s*\(\s*\w*\s*\)\s*\{\s*\}", fn), (
        f"{fn_name} still swallows its settings-save failure in an empty catch"
    )


@pytest.mark.parametrize(
    "fn_name,endpoint",
    [("connectSlack", "/page/api/slack/connect"), ("connectTeams", "/page/api/teams/connect")],
)
def test_navigation_is_preceded_by_a_guarded_return(page, fn_name, endpoint):
    """The redirect must sit after a ``return`` that a failed save can reach.

    Anchored by position rather than by wording: whatever the guard looks like,
    an early ``return`` has to appear before the navigation, or a failure falls
    through into the OAuth flow the way it did before the fix.
    """
    fn = _extract_fn(page, fn_name)
    nav = fn.find("window.location.href")
    assert nav != -1, f"{fn_name} no longer navigates to {endpoint}"
    guard = re.search(r"\breturn\b", fn[:nav])
    assert guard is not None, (
        f"{fn_name} assigns window.location.href with no preceding return, so a "
        f"failed settings save still enters the OAuth flow"
    )


@pytest.mark.parametrize("fn_name", ["connectSlack", "connectTeams"])
def test_connect_fn_still_navigates_on_success(page, fn_name):
    """Guard: the fix must not be satisfied by never navigating at all."""
    fn = _extract_fn(page, fn_name)
    provider = "slack" if fn_name == "connectSlack" else "teams"
    assert f"/page/api/{provider}/connect" in fn, (
        f"{fn_name} must still redirect to the {provider} connect endpoint on success"
    )


def test_failed_save_is_reported_into_the_settings_msg_span(page):
    """The reason has to reach the span the Settings tab already reports into.

    ``saveSettings`` reports a failed ``/settings`` POST into ``#settings-msg``;
    the connect paths POST the same endpoint and must not be the two callers
    whose failures are invisible.
    """
    helper = _extract_fn(page, "_saveSettingsForConnect")
    assert "settings-msg" in helper, "the failed save is not reported into #settings-msg"
    assert "settings-msg err" in helper, "the failure is not marked as an error"


def test_report_does_not_claim_nothing_was_saved(page):
    """A 500 can leave the record written and the config reload skipped.

    ``save_settings`` commits before the project-tag sync and
    ``config.apply_overrides``, so the message states that saving failed without
    asserting the stored value is unchanged.
    """
    helper = _extract_fn(page, "_saveSettingsForConnect")
    for forbidden in ("nothing was saved", "were not saved", "no changes were saved"):
        assert forbidden not in helper.lower(), (
            f"the message claims {forbidden!r}, which a partial save contradicts"
        )


def test_both_connect_paths_share_one_save_helper(page):
    """One helper, so the two providers cannot drift apart again."""
    for fn_name in ("connectSlack", "connectTeams"):
        fn = _extract_fn(page, fn_name)
        assert "_saveSettingsForConnect" in fn, (
            f"{fn_name} does not route through the shared helper"
        )


def test_helper_preserves_keep_existing_secret_semantics(page):
    """An empty secret input must stay out of the body.

    ``POST /settings`` merges into the existing record, so sending an empty
    string for a secret would overwrite a real credential with a blank.
    """
    helper = _extract_fn(page, "_saveSettingsForConnect")
    assert "SECRET_FIELDS.has" in helper and "el.value" in helper, (
        "the helper no longer omits empty secret inputs"
    )


# ── Backend premise tests (pass on both refs) ─────────────────────────────────


@pytest.mark.parametrize(
    "attr,endpoint",
    [("SLACK_CLIENT_ID", "/slack/connect"), ("TEAMS_CLIENT_ID", "/teams/connect")],
)
def test_connect_endpoint_refuses_without_a_saved_client_id(client, monkeypatch, attr, endpoint):
    """Premise: the redirect target is a 400 telling the operator to save first.

    This is why swallowing the save failure and navigating anyway cannot
    succeed — it routes the operator to advice about the step that just failed.
    """
    monkeypatch.setattr(config, attr, "")
    r = client.get(endpoint, follow_redirects=False)
    assert r.status_code == 400
    assert "save it in Settings first" in r.json()["detail"]


@pytest.mark.parametrize(
    "attr,field,endpoint",
    [
        ("SLACK_CLIENT_ID", "slack_client_id", "/slack/connect"),
        ("TEAMS_CLIENT_ID", "teams_client_id", "/teams/connect"),
    ],
)
def test_saving_settings_is_what_unlocks_the_connect_endpoint(
    client, monkeypatch, attr, field, endpoint
):
    """Premise: ``POST /settings`` hot-reloads config, which the redirect needs.

    Establishes the dependency end to end — the same endpoint answers 400
    before the save and 302 after it — so the save is a precondition of the
    redirect rather than an optimisation.
    """
    monkeypatch.setattr(config, attr, "")
    assert client.get(endpoint, follow_redirects=False).status_code == 400

    r = client.post("/settings", json={field: "client-id-from-the-form"})
    assert r.status_code == 200
    assert getattr(config, attr) == "client-id-from-the-form"

    r = client.get(endpoint, follow_redirects=False)
    assert r.status_code == 302
    assert "client-id-from-the-form" in r.headers["location"]


def test_settings_post_returns_warnings_the_form_reports(client):
    """Premise: a successful save answers ``{"ok", "warnings"}``.

    The helper's success path depends on this call resolving rather than
    throwing, which is what ``api()`` turns a non-2xx into.
    """
    r = client.post("/settings", json={"slack_client_id": "abc"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["warnings"], list)
