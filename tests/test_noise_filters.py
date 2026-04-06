"""
test_noise_filters.py — B6: Pre-scan noise filters.

Tests for noise_filter.py matching logic and the /noise-filters endpoints.
"""
import pytest
from dataclasses import dataclass, field


# ── noise_filter unit tests ───────────────────────────────────────────────────

import noise_filter as nf


@dataclass
class _Item:
    source:    str = "github"
    item_id:   str = "test-1"
    title:     str = "Daily digest"
    body:      str = ""
    url:       str = ""
    author:    str = "bot@noreply.com"
    timestamp: str = "2026-04-05T00:00:00"
    metadata:  dict = field(default_factory=dict)


def test_sender_contains_match():
    item  = _Item(author="noreply@github.com")
    rules = [{"type": "sender_contains", "value": "noreply@"}]
    matched, rtype = nf.should_filter(item, rules)
    assert matched
    assert rtype == "sender_contains"


def test_sender_contains_no_match():
    item  = _Item(author="alice@example.com")
    rules = [{"type": "sender_contains", "value": "noreply@"}]
    matched, _ = nf.should_filter(item, rules)
    assert not matched


def test_subject_contains_match():
    item  = _Item(title="Out of Office: Alice is away")
    rules = [{"type": "subject_contains", "value": "Out of Office"}]
    matched, rtype = nf.should_filter(item, rules)
    assert matched


def test_subject_contains_case_insensitive():
    item  = _Item(title="out of office notification")
    rules = [{"type": "subject_contains", "value": "Out of Office"}]
    matched, _ = nf.should_filter(item, rules)
    assert matched


def test_source_repo_match():
    item  = _Item(source="github", metadata={"repo": "acme/alerts"})
    rules = [{"type": "source_repo", "value": "acme/alerts"}]
    matched, rtype = nf.should_filter(item, rules)
    assert matched
    assert rtype == "source_repo"


def test_source_repo_no_partial_match():
    item  = _Item(source="github", metadata={"repo": "acme/alerts-extended"})
    rules = [{"type": "source_repo", "value": "acme/alerts"}]
    matched, _ = nf.should_filter(item, rules)
    assert not matched


def test_distribution_list_match():
    item  = _Item(metadata={"to": "all-company@example.com"})
    rules = [{"type": "distribution_list", "value": "all-company@"}]
    matched, _ = nf.should_filter(item, rules)
    assert matched


def test_no_rules_passes_all():
    item = _Item()
    matched, _ = nf.should_filter(item, [])
    assert not matched


def test_validate_rule_valid():
    assert nf.validate_rule({"type": "sender_contains", "value": "noreply@"}) is None


def test_validate_rule_bad_type():
    err = nf.validate_rule({"type": "banana", "value": "x"})
    assert err is not None


def test_validate_rule_empty_value():
    err = nf.validate_rule({"type": "sender_contains", "value": ""})
    assert err is not None


# ── /noise-filters API tests ──────────────────────────────────────────────────

def test_get_noise_filters_empty(client):
    r = client.get("/noise-filters")
    assert r.status_code == 200
    assert r.json() == []


def test_add_noise_filter(client):
    r = client.post("/noise-filters", json={"type": "sender_contains", "value": "noreply@"})
    assert r.status_code == 200
    rules = r.json()
    assert len(rules) == 1
    assert rules[0]["type"] == "sender_contains"
    assert rules[0]["value"] == "noreply@"


def test_add_noise_filter_invalid_type(client):
    r = client.post("/noise-filters", json={"type": "banana", "value": "x"})
    assert r.status_code == 422


def test_add_noise_filter_empty_value(client):
    r = client.post("/noise-filters", json={"type": "sender_contains", "value": "  "})
    assert r.status_code == 422


def test_delete_noise_filter(client):
    client.post("/noise-filters", json={"type": "sender_contains", "value": "noreply@"})
    client.post("/noise-filters", json={"type": "subject_contains", "value": "digest"})
    r = client.delete("/noise-filters/0")
    assert r.status_code == 200
    rules = r.json()
    assert len(rules) == 1
    assert rules[0]["type"] == "subject_contains"


def test_delete_noise_filter_out_of_range(client):
    r = client.delete("/noise-filters/99")
    assert r.status_code == 404


def test_count_filtered_items_zero(client):
    r = client.get("/noise-filters/count")
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_settings_includes_noise_filters(client):
    client.post("/noise-filters", json={"type": "sender_contains", "value": "bot@"})
    r = client.get("/settings")
    assert r.status_code == 200
    assert len(r.json().get("noise_filters", [])) == 1
