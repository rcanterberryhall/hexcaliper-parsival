"""Tests for the server-side calendar path.

Covers the category override and pre-filter (PVC-REQ-F-027, F-028), classifier
parsing, and the ingest branch (F-011, F-012, F-016, F-004).
"""

import calendar_filter as cf
from models import RawItem


def _item(**md) -> RawItem:
    """Build a calendar RawItem with sensible 'ordinary meeting' defaults."""
    metadata = {
        "start": "2026-08-20T09:00:00",
        "end": "2026-08-20T10:00:00",
        "all_day": False,
        "duration_minutes": 60,
        "location": "Bay 3",
        "organizer": "Alice Smith <alice@example.com>",
        "attendees": ["Bob <bob@example.com>"],
        "to": "Bob <bob@example.com>",
        "is_recurring": False,
        "response_status": "accepted",
        "busy_status": "busy",
        "sensitivity": "normal",
        "categories": [],
    }
    metadata.update(md)
    return RawItem(
        source=cf.CALENDAR_SOURCE,
        item_id="G1:2026-08-20T09:00:00",
        title="Design review — P905",
        body="",
        url="",
        author=metadata["organizer"],
        timestamp=metadata["start"],
        metadata=metadata,
    )


def test_an_ordinary_meeting_survives_the_prefilter():
    assert cf.prefilter_reason(_item()) is None


def test_declined_events_are_dropped():
    """OQ-PVC-007 — a declined meeting is not your work."""
    assert cf.prefilter_reason(_item(response_status="declined")) == "declined"


def test_tentative_events_are_kept():
    """OQ-PVC-007 — a tentative FAT is exactly what belongs on the board early."""
    assert cf.prefilter_reason(_item(response_status="tentative")) is None


def test_free_holds_are_dropped():
    assert cf.prefilter_reason(_item(busy_status="free")) == "free_hold"


def test_private_events_are_dropped_entirely():
    """OQ-PVC-008 — a private appointment should not be analysed at all."""
    assert cf.prefilter_reason(_item(sensitivity="private")) == "private"
    assert cf.prefilter_reason(_item(sensitivity="confidential")) == "private"


def test_short_recurring_blocks_are_dropped():
    """The standup / 1:1 rule — the reason the pre-filter exists at all."""
    assert cf.prefilter_reason(_item(is_recurring=True, duration_minutes=15)) == "short_recurring"


def test_a_long_recurring_meeting_survives():
    assert cf.prefilter_reason(_item(is_recurring=True, duration_minutes=120)) is None


def test_attendee_less_holds_are_dropped():
    assert cf.prefilter_reason(_item(attendees=[], duration_minutes=90)) == "solo_hold"


def test_an_all_day_solo_event_survives():
    """An all-day event with no attendees is how a deadline usually looks."""
    assert cf.prefilter_reason(_item(attendees=[], all_day=True, duration_minutes=1440)) is None


def test_force_include_and_force_ignore_categories_are_recognised():
    """PVC-REQ-F-028 — a deterministic lever that beats rules and model alike."""
    assert cf.category_override(_item(categories=["Parsival Include"])) == "include"
    assert cf.category_override(_item(categories=["parsival: ignore"])) == "ignore"
    assert cf.category_override(_item(categories=["Red Category"])) is None
    assert cf.category_override(_item()) is None


def test_force_ignore_wins_when_both_categories_are_present():
    """A deterministic tie-break beats an arbitrary one."""
    assert (
        cf.category_override(_item(categories=["Parsival Include", "Parsival Ignore"])) == "ignore"
    )


def test_unrelated_categories_with_digits_or_punctuation_do_not_trigger_override():
    """Protect against silent false positives from similar category names.

    Categories like "Parsival Include 2" or "Parsival Include!!!" should not
    accidentally trigger the force-include override. Only the exact override
    categories (after normalising separators) should match.
    """
    assert cf.category_override(_item(categories=["Parsival Include 2"])) is None
    assert cf.category_override(_item(categories=["2 Parsival Include"])) is None
    assert cf.category_override(_item(categories=["Parsival Include!!!"])) is None
    assert cf.category_override(_item(categories=["Parsival Ignore-v2"])) is None
