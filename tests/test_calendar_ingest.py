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


from unittest.mock import patch

import calendar_ingest as ci
import config


def test_project_is_guessed_from_subject_keywords():
    """OQ-PVC-006 — subject matched against the per-project keyword knobs."""
    config.PROJECTS = [
        {"name": "P905", "keywords": ["P905", "panel"], "learned_keywords": [], "senders": []},
        {"name": "P910", "keywords": ["P910"], "learned_keywords": [], "senders": []},
    ]
    assert ci.guess_project(_item()) == "P905"


def test_project_is_guessed_from_the_organiser():
    """OQ-PVC-006 — organiser/attendees matched against project senders."""
    config.PROJECTS = [
        {"name": "P910", "keywords": [], "learned_keywords": [], "senders": ["alice@example.com"]}
    ]
    item = _item()
    item.title = "Weekly sync"
    assert ci.guess_project(item) == "P910"


def test_an_unresolved_project_is_blank_not_a_guess():
    """A wrong guess costs a dropdown; a blank one is honest (PVC-REQ-F-017)."""
    config.PROJECTS = [
        {"name": "P910", "keywords": ["nothing"], "learned_keywords": [], "senders": []}
    ]
    item = _item()
    item.title = "Weekly sync"
    assert ci.guess_project(item) == ""


def test_classifier_parses_a_card_verdict():
    """PVC-REQ-F-012 / F-013 — scheduled work becomes a proposed card."""
    config.PROJECTS = []
    raw = '{"kind": "card", "project": "P905", "title": "FAT", "reason": "Multi-day supplier test"}'
    with patch.object(ci, "_generate", return_value=raw):
        verdict = ci.classify(_item())
    assert verdict["kind"] == "card"
    assert verdict["project"] == "P905"
    assert verdict["start_date"] == "2026-08-20"
    assert verdict["end_date"] == "2026-08-20"


def test_classifier_defaults_a_key_date_to_the_event_date():
    config.PROJECTS = []
    with patch.object(
        ci, "_generate", return_value='{"kind": "key_date", "reason": "Submission due"}'
    ):
        verdict = ci.classify(_item())
    assert verdict["kind"] == "key_date"
    assert verdict["date"] == "2026-08-20"


def test_a_link_verdict_naming_an_unknown_target_is_downgraded_to_ignore():
    """A hallucinated target must not become an unacceptable proposal."""
    config.PROJECTS = []
    raw = '{"kind": "link", "target_type": "situation", "target_id": "nope", "reason": "x"}'
    with (
        patch.object(ci, "_generate", return_value=raw),
        patch.object(ci, "_link_candidates", return_value=([], [])),
    ):
        verdict = ci.classify(_item())
    assert verdict["kind"] == "ignore"


def test_a_link_verdict_naming_a_real_situation_survives():
    config.PROJECTS = []
    raw = '{"kind": "link", "target_type": "situation", "target_id": "sit-1", "reason": "x"}'
    with (
        patch.object(ci, "_generate", return_value=raw),
        patch.object(
            ci, "_link_candidates", return_value=([{"id": "sit-1", "title": "Panel rework"}], [])
        ),
    ):
        verdict = ci.classify(_item())
    assert verdict["kind"] == "link"
    assert (verdict["target_type"], verdict["target_id"]) == ("situation", "sit-1")


def test_an_unparseable_response_means_retry_not_ignore():
    """Design §8 — a model failure leaves the event unclassified, no junk proposal."""
    config.PROJECTS = []
    with patch.object(ci, "_generate", return_value="the model rambled"):
        assert ci.classify(_item()) is None


def test_a_model_exception_means_retry_not_ignore():
    config.PROJECTS = []
    with patch.object(ci, "_generate", side_effect=RuntimeError("merLLM down")):
        assert ci.classify(_item()) is None


def test_an_unrecognised_kind_is_treated_as_ignore():
    """The model answered — it just answered badly.  Do not retry that forever."""
    config.PROJECTS = []
    with patch.object(ci, "_generate", return_value='{"kind": "banana"}'):
        assert ci.classify(_item())["kind"] == "ignore"
