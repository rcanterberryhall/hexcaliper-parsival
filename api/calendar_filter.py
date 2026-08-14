"""calendar_filter.py — Deterministic rules for calendar events.

Two stages run before any model call, in this order (design §4.2)::

    category override (F-028)  →  pre-filter (F-027)  →  model  →  proposal

The override runs **first**.  A force-include category has to survive the
pre-filter — if the filter ran first it would already have discarded the
20-minute recurring block that was deliberately tagged.  Force-ignore
short-circuits both stages.  That ordering is the entire value of
``PVC-REQ-F-028``: a deterministic lever that beats both the rules and the
model.

The pre-filter exists because Outlook owns recurrence (``CON-PVC-013``): over a
90-day window one daily meeting is ~90 individually classifiable events, and a
model call per event does not scale.  These rules are free, testable, and
tunable from the ``user_actions`` evidence loop that ``tuning.get`` already
exposes.

This module deliberately imports nothing but ``models`` — no DB, no LLM — so it
stays instant to test and cheap to re-tune.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models import RawItem

CALENDAR_SOURCE = "outlook_calendar"

# Outlook categories the user applies by hand.  Matched after normalisation, so
# "Parsival: Include", "parsival include" and "Parsival-Include" all hit.
FORCE_INCLUDE_CATEGORY = "parsival include"
FORCE_IGNORE_CATEGORY = "parsival ignore"

# Tuning knobs.  Both are thresholds rather than hard facts, so they live here
# as named constants and are expected to move once user_actions has evidence.
RECURRING_MAX_MINUTES = 30  # standup / 1:1 territory
SOLO_HOLD_MAX_MINUTES = 120  # an attendee-less block this short is a focus hold


def _normalise_category(value: str) -> str:
    """Reduce a category label to lowercase words for tolerant comparison."""
    return re.sub(r"[^a-z]+", " ", (value or "").lower()).strip()


def category_override(item: RawItem) -> str | None:
    """Return the user's explicit verdict for this event, if any.

    Args:
        item: A calendar ``RawItem``.

    Returns:
        ``"include"`` to force the event past the pre-filter and the model,
        ``"ignore"`` to drop it outright, or ``None`` when untagged.  Ignore
        wins if both categories are present, so the outcome never depends on
        category order.
    """
    cats = {_normalise_category(c) for c in (item.metadata or {}).get("categories") or []}
    if FORCE_IGNORE_CATEGORY in cats:
        return "ignore"
    if FORCE_INCLUDE_CATEGORY in cats:
        return "include"
    return None


def prefilter_reason(item: RawItem) -> str | None:
    """Return why this event cannot be work, a deadline, or context.

    Structural rules only — no model call, no DB read (``PVC-REQ-F-027``).

    Args:
        item: A calendar ``RawItem``.

    Returns:
        A short reason slug when the event should be dropped, else ``None``.
        The slug is stored on the recorded item so a later tuning pass can see
        which rule fired.
    """
    md = item.metadata or {}
    if md.get("response_status") == "declined":
        return "declined"
    if md.get("busy_status") == "free":
        # A hold you marked free is not a commitment.
        return "free_hold"
    if md.get("sensitivity") in ("private", "confidential"):
        # OQ-PVC-008 — personal and HR appointments should not be analysed at
        # all.  The force-include category is the escape hatch for a specific
        # one.
        return "private"

    all_day = bool(md.get("all_day"))
    duration = int(md.get("duration_minutes") or 0)
    attendees = md.get("attendees") or []

    if md.get("is_recurring") and not all_day and 0 < duration <= RECURRING_MAX_MINUTES:
        return "short_recurring"
    if not attendees and not all_day and 0 < duration <= SOLO_HOLD_MAX_MINUTES:
        return "solo_hold"
    return None
