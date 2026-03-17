"""tests/test_correlator.py — Tests for cross-source correlation logic."""
import pytest
from correlator import extract_references, score_situation


def test_extract_jira_keys():
    refs = extract_references("Re: PROJ-142 is blocked", "see ENG-99 for context")
    assert "proj-142" in refs
    assert "eng-99" in refs


def test_extract_pr_numbers():
    refs = extract_references("PR #89 needs review", "")
    assert any("89" in r for r in refs)


def test_extract_deduplicates():
    refs = extract_references("PROJ-1 and PROJ-1", "also PROJ-1")
    assert refs.count("proj-1") == 1


def test_extract_returns_empty_for_clean_text():
    refs = extract_references("Weekly standup notes", "No blockers today.")
    assert refs == []


def test_score_situation_higher_for_more_sources():
    items_two = [
        {"source": "jira",    "priority": "medium", "hierarchy": "project", "timestamp": "2026-03-17T10:00:00+00:00"},
        {"source": "outlook", "priority": "medium", "hierarchy": "project", "timestamp": "2026-03-17T10:00:00+00:00"},
    ]
    items_one = [
        {"source": "jira",    "priority": "medium", "hierarchy": "project", "timestamp": "2026-03-17T10:00:00+00:00"},
        {"source": "jira",    "priority": "medium", "hierarchy": "project", "timestamp": "2026-03-17T10:00:00+00:00"},
    ]
    assert score_situation(["a", "b"], items_two) > score_situation(["a", "b"], items_one)


def test_score_situation_higher_for_user_hierarchy():
    items_user = [
        {"source": "jira",  "priority": "medium", "hierarchy": "user",    "timestamp": "2026-03-17T10:00:00+00:00"},
        {"source": "slack", "priority": "medium", "hierarchy": "user",    "timestamp": "2026-03-17T10:00:00+00:00"},
    ]
    items_proj = [
        {"source": "jira",  "priority": "medium", "hierarchy": "project", "timestamp": "2026-03-17T10:00:00+00:00"},
        {"source": "slack", "priority": "medium", "hierarchy": "project", "timestamp": "2026-03-17T10:00:00+00:00"},
    ]
    assert score_situation(["a", "b"], items_user) > score_situation(["a", "b"], items_proj)


def test_score_situation_higher_for_high_priority():
    base_ts    = "2026-03-17T10:00:00+00:00"
    items_high = [{"source": "jira",  "priority": "high", "hierarchy": "project", "timestamp": base_ts},
                  {"source": "slack", "priority": "high", "hierarchy": "project", "timestamp": base_ts}]
    items_low  = [{"source": "jira",  "priority": "low",  "hierarchy": "project", "timestamp": base_ts},
                  {"source": "slack", "priority": "low",  "hierarchy": "project", "timestamp": base_ts}]
    assert score_situation(["a", "b"], items_high) > score_situation(["a", "b"], items_low)


def test_score_situation_recency_decay():
    from datetime import datetime, timezone, timedelta
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    old    = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    items_recent = [{"source": "jira",  "priority": "medium", "hierarchy": "project", "timestamp": recent},
                    {"source": "slack", "priority": "medium", "hierarchy": "project", "timestamp": recent}]
    items_old    = [{"source": "jira",  "priority": "medium", "hierarchy": "project", "timestamp": old},
                    {"source": "slack", "priority": "medium", "hierarchy": "project", "timestamp": old}]
    assert score_situation(["a", "b"], items_recent) > score_situation(["a", "b"], items_old)
