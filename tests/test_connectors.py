from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import connector_github
import connector_slack
import connector_jira
import connector_outlook
import config


# ── GitHub ────────────────────────────────────────────────────────────────────

def test_github_skips_when_not_configured(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_PAT", "")
    assert connector_github.fetch() == []


def test_github_skips_on_placeholder_pat(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_PAT", "ghp_your-placeholder")
    assert connector_github.fetch() == []


def test_github_ts_normalises_z():
    assert connector_github._ts("2024-01-15T10:00:00Z") == "2024-01-15T10:00:00+00:00"


def test_github_ts_empty_string():
    assert connector_github._ts("") == ""


def test_github_fetch_returns_items(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_PAT", "ghp_realtoken")
    monkeypatch.setattr(config, "GITHUB_USERNAME", "alice")
    monkeypatch.setattr(config, "LOOKBACK_HOURS", 48)

    now = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    notifications = [
        {
            "id": "n1",
            "updated_at": now,
            "subject": {"title": "PR merged", "url": None, "type": "PullRequest"},
            "repository": {"full_name": "org/repo"},
            "reason": "mention",
        }
    ]

    def fake_get(path, params=None):
        if path == "/notifications":
            return notifications
        if path == "/search/issues":
            return {"items": []}
        if path == "/issues":
            return []
        return {}

    def fake_get_paginated(path, params=None, max_items=500):
        return fake_get(path, params) if isinstance(fake_get(path, params), list) else []

    with patch("connector_github.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.side_effect = fake_get
        mock_resp.headers = {}
        mock_get.return_value = mock_resp

        with patch("connector_github._get", side_effect=fake_get), \
             patch("connector_github._get_paginated", side_effect=fake_get_paginated):
            items = connector_github.fetch()

    assert len(items) == 1
    assert items[0].source == "github"
    assert items[0].item_id == "n1"


# ── Slack ─────────────────────────────────────────────────────────────────────

def test_slack_skips_when_not_configured(monkeypatch):
    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "")
    assert connector_slack.fetch() == []


def test_slack_skips_on_placeholder_token(monkeypatch):
    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "xoxb-your-placeholder")
    assert connector_slack.fetch() == []


def test_slack_returns_empty_on_api_error(monkeypatch):
    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "xoxb-realtoken")
    monkeypatch.setattr(config, "LOOKBACK_HOURS", 48)
    monkeypatch.setattr(config, "SLACK_CHANNELS", [])

    with patch("connector_slack.requests.get", side_effect=Exception("connection refused")):
        items = connector_slack.fetch()

    assert items == []


# ── Slack dedup (parsival#69) ─────────────────────────────────────────────────

def test_slack_unseen_filter_roundtrip():
    import db
    db.conn().execute("DELETE FROM slack_seen_messages")
    ts_list = ["100.0", "200.0", "300.0"]
    # Nothing marked yet → all unseen
    assert db.slack_unseen_message_ts("W1", "C1", ts_list) == set(ts_list)
    db.slack_mark_messages_seen("W1", "C1", ["100.0", "200.0"])
    # Only the unmarked one comes back
    assert db.slack_unseen_message_ts("W1", "C1", ts_list) == {"300.0"}
    # Scoping by (team, channel) — same ts in another channel is unaffected
    assert db.slack_unseen_message_ts("W1", "C2", ts_list) == set(ts_list)
    # Idempotent re-mark
    db.slack_mark_messages_seen("W1", "C1", ["100.0"])
    assert db.slack_unseen_message_ts("W1", "C1", ts_list) == {"300.0"}


def test_slack_fetch_skips_already_seen_channel(monkeypatch):
    """Channel where every message has been seen in a prior scan emits nothing."""
    import db
    db.conn().execute("DELETE FROM slack_seen_messages")
    monkeypatch.setattr(config, "SLACK_USER_TOKENS", [])
    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "xoxb-real")
    monkeypatch.setattr(config, "LOOKBACK_HOURS", 48)
    monkeypatch.setattr(config, "SLACK_CHANNELS", [])

    # Pre-mark the two msg timestamps as already seen; legacy path uses "" team.
    db.slack_mark_messages_seen("", "CABC", ["1700000000.0", "1700000100.0"])

    def fake_get_impl(token, endpoint, params=None):
        if endpoint == "auth.test":
            return {"ok": True, "user_id": "UBOT", "team": "T1"}
        if endpoint == "conversations.list":
            return {"ok": True, "channels": [{"id": "CABC", "name": "general"}]}
        if endpoint == "conversations.history":
            return {"ok": True, "messages": [
                {"ts": "1700000100.0", "text": "<@UBOT> ping",   "user": "U1"},
                {"ts": "1700000000.0", "text": "<@UBOT> older",  "user": "U1"},
            ]}
        return {"ok": True}

    with patch("connector_slack._get", side_effect=fake_get_impl):
        items = connector_slack.fetch()

    assert items == []


def test_slack_fetch_emits_only_new_messages(monkeypatch):
    """Only the unseen message becomes a RawItem; its ts is then marked seen."""
    import db
    db.conn().execute("DELETE FROM slack_seen_messages")
    monkeypatch.setattr(config, "SLACK_USER_TOKENS", [])
    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "xoxb-real")
    monkeypatch.setattr(config, "LOOKBACK_HOURS", 48)
    monkeypatch.setattr(config, "SLACK_CHANNELS", [])

    db.slack_mark_messages_seen("", "CABC", ["1700000000.0"])

    def fake_get_impl(token, endpoint, params=None):
        if endpoint == "auth.test":
            return {"ok": True, "user_id": "UBOT", "team": "T1"}
        if endpoint == "conversations.list":
            return {"ok": True, "channels": [{"id": "CABC", "name": "general"}]}
        if endpoint == "conversations.history":
            return {"ok": True, "messages": [
                {"ts": "1700000100.0", "text": "<@UBOT> new",   "user": "U1"},
                {"ts": "1700000000.0", "text": "<@UBOT> older", "user": "U1"},
            ]}
        if endpoint == "users.info":
            return {"ok": True, "user": {"real_name": "Alice"}}
        return {"ok": True}

    with patch("connector_slack._get", side_effect=fake_get_impl):
        items = connector_slack.fetch()

    assert len(items) == 1
    assert items[0].item_id == "CABC_1700000100.0"
    # Connector records the new ts so a subsequent scan doesn't re-emit it.
    assert db.slack_unseen_message_ts("", "CABC", ["1700000100.0"]) == set()


# ── Jira ──────────────────────────────────────────────────────────────────────

def test_jira_skips_when_not_configured(monkeypatch):
    monkeypatch.setattr(config, "JIRA_TOKEN", "")
    monkeypatch.setattr(config, "JIRA_DOMAIN", "")
    assert connector_jira.fetch() == []


def test_jira_skips_on_placeholder_domain(monkeypatch):
    monkeypatch.setattr(config, "JIRA_TOKEN", "realtoken")
    monkeypatch.setattr(config, "JIRA_DOMAIN", "yourcompany.atlassian.net")
    assert connector_jira.fetch() == []


def test_jira_text_extraction_from_adf():
    adf = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "Hello"},
                {"type": "text", "text": " world"},
            ]}
        ]
    }
    result = connector_jira._text(adf)
    assert "Hello" in result
    assert "world" in result


def test_jira_text_empty_input():
    assert connector_jira._text(None) == ""
    assert connector_jira._text("") == ""


def test_jira_text_plain_string():
    assert connector_jira._text("plain text") == "plain text"


def test_jira_fetch_returns_items(monkeypatch):
    monkeypatch.setattr(config, "JIRA_TOKEN", "realtoken")
    monkeypatch.setattr(config, "JIRA_DOMAIN", "mycompany.atlassian.net")
    monkeypatch.setattr(config, "JIRA_JQL", "assignee = currentUser()")
    monkeypatch.setattr(config, "LOOKBACK_HOURS", 48)

    fake_response = {
        "issues": [{
            "key": "PROJ-1",
            "fields": {
                "summary": "Fix the thing",
                "description": None,
                "status": {"name": "In Progress"},
                "priority": {"name": "High"},
                "reporter": {"displayName": "Bob"},
                "updated": "2026-03-13T10:00:00+00:00",
                "duedate": "2026-03-20",
                "comment": {"comments": []},
                "issuetype": {"name": "Story"},
                "project": {"name": "My Project"},
            }
        }]
    }

    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = fake_response

    with patch("connector_jira.requests.get", return_value=mock_resp):
        items = connector_jira.fetch()

    assert len(items) == 1
    assert items[0].item_id == "PROJ-1"
    assert items[0].source == "jira"
    assert "Fix the thing" in items[0].title
    assert items[0].metadata["due"] == "2026-03-20"


# ── Outlook ───────────────────────────────────────────────────────────────────

def test_outlook_always_returns_empty():
    assert connector_outlook.fetch() == []
