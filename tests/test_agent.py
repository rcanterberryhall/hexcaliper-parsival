import json
from unittest.mock import MagicMock, patch

import agent
from models import RawItem


def _raw(source="github", item_id="1", title="Test", body="content"):
    return RawItem(
        source=source, item_id=item_id, title=title, body=body,
        url="http://x", author="alice", timestamp="2024-01-01T00:00:00",
    )


def _mock_response(data: dict) -> MagicMock:
    m = MagicMock()
    m.json.return_value = {"response": json.dumps(data)}
    m.raise_for_status.return_value = None
    return m


def test_analyze_parses_full_response():
    payload = {
        "has_action": True,
        "priority": "high",
        "category": "task",
        "action_items": [{"description": "Ship it", "deadline": "2024-03-01", "owner": "me"}],
        "summary": "Deploy the release",
        "urgency_reason": "overdue",
    }
    with patch("agent.llm.generate", return_value=json.dumps(payload)):
        result = agent.analyze(_raw())

    assert result.has_action is True
    assert result.priority == "high"
    assert result.category == "task"
    assert result.summary == "Deploy the release"
    assert len(result.action_items) == 1
    assert result.action_items[0].description == "Ship it"
    assert result.action_items[0].deadline == "2024-03-01"


def test_analyze_defaults_on_empty_response():
    with patch("agent.llm.generate", return_value="{}"):
        result = agent.analyze(_raw(title="Fallback title"))

    assert result.priority == "medium"
    assert result.category == "fyi"
    assert result.summary == "Fallback title"  # falls back to item.title
    assert result.action_items == []


def test_analyze_jira_fallback_creates_action_item():
    """Jira items always get an action item even if LLM returns nothing."""
    item = RawItem(
        source="jira", item_id="PROJ-42", title="Fix login bug",
        body="", url="", author="", timestamp="2024-01-01",
        metadata={"due": "2024-04-01"},
    )
    with patch("agent.llm.generate", return_value="{}"):
        result = agent.analyze(item)

    assert len(result.action_items) == 1
    assert "Fix login bug" in result.action_items[0].description
    assert result.action_items[0].deadline == "2024-04-01"


def test_analyze_skips_action_items_without_description():
    payload = {
        "has_action": True,
        "priority": "low",
        "category": "fyi",
        "action_items": [{"description": "", "deadline": None, "owner": "me"}],
        "summary": "Nothing to do",
        "urgency_reason": None,
    }
    with patch("agent.llm.generate", return_value=json.dumps(payload)):
        result = agent.analyze(_raw())

    assert result.action_items == []


def test_analyze_batch_calls_progress_cb():
    items = [_raw(item_id=str(i)) for i in range(3)]
    calls = []

    with patch("agent.llm.generate", return_value="{}"):
        agent.analyze_batch(items, progress_cb=lambda i, t, s, title: calls.append(i))

    assert calls == [0, 1, 2]


def test_analyze_batch_skips_failed_items():
    items = [_raw(item_id="ok"), _raw(item_id="bad"), _raw(item_id="ok2")]

    with patch("agent.llm.generate", side_effect=Exception("network error")):
        results = agent.analyze_batch(items)

    # Errors are swallowed; no results but no crash
    assert results == []


# ── Recipient scope classification ───────────────────────────────────────────

class TestComputeRecipientScope:
    def test_no_recipients_is_direct(self):
        r = agent.compute_recipient_scope("user@co.com", "", "")
        assert r["scope"] == "direct"
        assert r["total"] == 0

    def test_single_to_user_is_direct(self):
        r = agent.compute_recipient_scope("user@co.com", "User <user@co.com>", "")
        assert r["scope"] == "direct"
        assert r["total"] == 1
        assert r["user_in_to"] is True

    def test_two_to_three_is_small(self):
        r = agent.compute_recipient_scope(
            "user@co.com",
            "User <user@co.com>, Bob <bob@co.com>, Carol <carol@co.com>",
            "",
        )
        assert r["scope"] == "small"
        assert r["total"] == 3

    def test_six_recipients_is_group(self):
        to = ", ".join(f"p{i} <p{i}@co.com>" for i in range(5)) + ", User <user@co.com>"
        r = agent.compute_recipient_scope("user@co.com", to, "")
        assert r["scope"] == "group"
        assert r["total"] == 6

    def test_twelve_recipients_is_broadcast(self):
        to = ", ".join(f"p{i} <p{i}@co.com>" for i in range(11)) + ", User <user@co.com>"
        r = agent.compute_recipient_scope("user@co.com", to, "")
        assert r["scope"] == "broadcast"
        assert r["total"] == 12

    def test_distribution_list_forces_broadcast(self):
        # Only 2 addresses, but one is a DL → broadcast
        r = agent.compute_recipient_scope(
            "user@co.com",
            "Eng Team <eng-team@co.com>, User <user@co.com>",
            "",
        )
        assert r["scope"] == "broadcast"
        assert "eng-team@co.com" in r["dls"]

    def test_dl_local_prefixes(self):
        for addr in ("all-hands@co.com", "dl-engineering@co.com", "everyone@co.com", "team@co.com"):
            assert agent._is_distribution_list(addr) is True, addr

    def test_dl_domain_patterns(self):
        for addr in ("maintainers@lists.kernel.org", "devs@groups.google.com"):
            assert agent._is_distribution_list(addr) is True, addr

    def test_personal_address_not_dl(self):
        for addr in ("alice@co.com", "bob.smith@acme.org", "user@co.com"):
            assert agent._is_distribution_list(addr) is False, addr

    def test_user_absent_from_visible_is_broadcast(self):
        # User received via BCC or mailing list — not in To/CC
        r = agent.compute_recipient_scope(
            "user@co.com",
            "Alice <alice@co.com>, Bob <bob@co.com>",
            "",
        )
        assert r["scope"] == "broadcast"
        assert r["user_in_to"] is False


# ── Action item post-processing ───────────────────────────────────────────────

def _ai(desc, owner="me"):
    from models import ActionItem
    return ActionItem(description=desc, deadline=None, owner=owner)


class TestPostprocessActionItems:
    def _scope(self, label):
        return {
            "scope": label, "to_count": 0, "cc_count": 0, "total": 12,
            "dls": [], "user_in_to": True, "user_in_cc": False,
        }

    def test_direct_scope_is_noop(self):
        items = [_ai("Do thing")]
        r = agent.postprocess_action_items(
            items, {"scope": "direct", "total": 1, "dls": []},
            "please do this", "Alice", "alice@co.com",
        )
        assert len(r) == 1

    def test_small_scope_is_noop(self):
        items = [_ai("Do thing")]
        r = agent.postprocess_action_items(
            items, {"scope": "small", "total": 3, "dls": []},
            "please do this", "Alice", "alice@co.com",
        )
        assert len(r) == 1

    def test_broadcast_strips_owner_me_when_user_not_named(self):
        items = [_ai("Review the doc")]
        r = agent.postprocess_action_items(
            items, self._scope("broadcast"),
            "Everyone please review the doc by Friday.",
            "Alice", "alice@co.com",
        )
        assert r == []

    def test_broadcast_keeps_owner_me_when_user_named_in_body(self):
        items = [_ai("Review the doc")]
        r = agent.postprocess_action_items(
            items, self._scope("broadcast"),
            "Alice, please review the doc by Friday.",
            "Alice", "alice@co.com",
        )
        assert len(r) == 1

    def test_broadcast_keeps_owner_me_when_email_in_body(self):
        items = [_ai("Review the doc")]
        r = agent.postprocess_action_items(
            items, self._scope("broadcast"),
            "Need alice@co.com to handle this.",
            "Alice Smith", "alice@co.com",
        )
        assert len(r) == 1

    def test_broadcast_always_keeps_owner_other(self):
        """Delegated-work tracking — owner=<other person> survives regardless."""
        items = [
            _ai("Pull drawings for P905", owner="Mike"),
            _ai("Generic thing",          owner="me"),
        ]
        r = agent.postprocess_action_items(
            items, self._scope("broadcast"),
            "Mike, please pull the drawings for P905 by Thursday.",
            "Alice", "alice@co.com",
        )
        # "me" stripped (Alice not named), "Mike" kept
        assert len(r) == 1
        assert r[0].owner == "Mike"

    def test_group_scope_strips_owner_me_but_keeps_others(self):
        items = [
            _ai("Approve budget",         owner="me"),
            _ai("Sarah to review specs",  owner="Sarah"),
        ]
        r = agent.postprocess_action_items(
            items, self._scope("group") | {"scope": "group", "total": 7},
            "Sarah, can you review the specs? Also team, please approve budget.",
            "Alice", "alice@co.com",
        )
        # Alice not named; "me" item stripped, "Sarah" item kept
        assert len(r) == 1
        assert r[0].owner == "Sarah"

    def test_first_name_match_keeps_owner_me(self):
        items = [_ai("Review the doc")]
        r = agent.postprocess_action_items(
            items, self._scope("broadcast"),
            "Alice — can you review this?",
            "Alice Smith", "alice.smith@co.com",
        )
        assert len(r) == 1
