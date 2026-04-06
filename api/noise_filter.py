"""
noise_filter.py — Pre-scan noise filter evaluation.

Filters are evaluated against each RawItem before the LLM pipeline runs.
Matching items are stored with category='filtered' and skipped by the analyser.

Supported rule types
--------------------
- sender_contains   — case-insensitive substring match on item.author
- subject_contains  — case-insensitive substring match on item.title
- source_repo       — exact match on metadata["repo"] (GitHub only)
- distribution_list — case-insensitive substring match on metadata["to"]

Rule schema (as stored in settings["noise_filters"])::

    [
      {"type": "sender_contains",    "value": "noreply@"},
      {"type": "subject_contains",   "value": "Out of Office"},
      {"type": "source_repo",        "value": "monitoring-alerts"},
      {"type": "distribution_list",  "value": "all-company@"},
    ]
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models import RawItem

VALID_RULE_TYPES = frozenset({
    "sender_contains",
    "subject_contains",
    "source_repo",
    "distribution_list",
})


def _matches(rule: dict, item: "RawItem") -> bool:
    rtype = rule.get("type", "")
    value = rule.get("value", "")
    if not value:
        return False

    if rtype == "sender_contains":
        return value.lower() in (item.author or "").lower()

    if rtype == "subject_contains":
        return value.lower() in (item.title or "").lower()

    if rtype == "source_repo":
        return (item.metadata or {}).get("repo", "") == value

    if rtype == "distribution_list":
        recipients = (item.metadata or {}).get("to", "")
        return value.lower() in recipients.lower()

    return False


def should_filter(item: "RawItem", rules: list[dict]) -> tuple[bool, str | None]:
    """
    Return ``(True, matched_rule_type)`` if any rule matches *item*,
    else ``(False, None)``.
    """
    for rule in rules:
        if _matches(rule, item):
            return True, rule.get("type", "unknown")
    return False, None


def validate_rule(rule: dict) -> str | None:
    """
    Validate a single filter rule.  Returns an error string or None if valid.
    """
    if rule.get("type") not in VALID_RULE_TYPES:
        return f"Unknown filter type '{rule.get('type')}'. Valid: {sorted(VALID_RULE_TYPES)}"
    if not rule.get("value", "").strip():
        return "Filter value must not be empty."
    return None
