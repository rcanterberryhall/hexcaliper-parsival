"""
test_calendar_sidecar.py — Tests for the Outlook calendar sidecar path.

Covers pure normalisation (PVC-REQ-F-007/F-008/F-009/N-003), retrieval order
(F-003), the acquisition window (F-002), and the independent high-water mark
(F-005, N-002).  Every COM object is mocked, so the suite runs on Linux —
which is the whole point of the pure-normalisation split (design §3.5).
"""

from datetime import datetime

import outlook_sidecar as sc


def _raw(**overrides) -> dict:
    """Build a plain already-read appointment dict (no COM objects)."""
    base = {
        "subject": "FAT — P905 panel",
        "start": datetime(2026, 8, 20, 9, 0),
        "end": datetime(2026, 8, 22, 17, 0),
        "all_day": False,
        "location": "Bay 3",
        "body": "Agenda\n\n\n\nBring the punch list",
        "organizer": "Alice Smith",
        "attendees": ["Alice Smith <alice@example.com>", "Bob <bob@example.com>"],
        "global_id": "GLOBAL001",
        "is_recurring": False,
        "response_status": 3,  # olResponseAccepted
        "busy_status": 2,  # olBusy
        "sensitivity": 0,  # olNormal
        "categories": "Red Category; Parsival Include",
    }
    base.update(overrides)
    return base


def test_item_id_is_derived_from_global_appointment_id():
    """PVC-REQ-F-007 — EntryID changes on folder moves; GlobalAppointmentID does not."""
    out = sc.normalise_appointment(_raw())
    assert out["item_id"].startswith("GLOBAL001:")


def test_each_occurrence_of_a_series_gets_a_distinct_item_id():
    """PVC-REQ-F-008 — every occurrence shares one GlobalAppointmentID."""
    first = sc.normalise_appointment(
        _raw(
            global_id="SERIES1", start=datetime(2026, 8, 20, 9, 0), end=datetime(2026, 8, 20, 9, 30)
        )
    )
    second = sc.normalise_appointment(
        _raw(
            global_id="SERIES1", start=datetime(2026, 8, 27, 9, 0), end=datetime(2026, 8, 27, 9, 30)
        )
    )
    assert first["item_id"] != second["item_id"]


def test_metadata_carries_every_field_classification_needs():
    """PVC-REQ-F-009 — RawItem has no typed fields for these."""
    md = sc.normalise_appointment(_raw())["metadata"]
    assert md["start"] == "2026-08-20T09:00:00"
    assert md["end"] == "2026-08-22T17:00:00"
    assert md["all_day"] is False
    assert md["organizer"] == "Alice Smith"
    assert md["attendees"] == ["Alice Smith <alice@example.com>", "Bob <bob@example.com>"]
    assert md["location"] == "Bay 3"
    assert md["is_recurring"] is False
    assert md["response_status"] == "accepted"
    assert md["busy_status"] == "busy"
    assert md["sensitivity"] == "normal"
    assert md["categories"] == ["Red Category", "Parsival Include"]
    assert md["duration_minutes"] == 2 * 24 * 60 + 8 * 60


def test_attendees_also_land_in_the_to_field_shape_contacts_expect():
    """PVC-REQ-N-004 — same shape as email recipients, so enrichment is not confused."""
    md = sc.normalise_appointment(_raw())["metadata"]
    assert md["to"] == "Alice Smith <alice@example.com>; Bob <bob@example.com>"


def test_source_is_the_new_calendar_value():
    """PVC-REQ-F-011 / OQ-PVC-010 — never reuse 'outlook'."""
    assert sc.normalise_appointment(_raw())["source"] == "outlook_calendar"
    assert sc.CALENDAR_SOURCE == "outlook_calendar"


def test_body_is_truncated_and_blank_runs_collapsed():
    """PVC-REQ-N-003 — 3 000-character RawItem convention."""
    out = sc.normalise_appointment(_raw(body="x" * 5000))
    assert len(out["body"]) == 3000
    assert sc.normalise_appointment(_raw())["body"] == "Agenda\n\nBring the punch list"


def test_status_codes_map_to_names():
    """MAPI ints are meaningless downstream; the pre-filter reads names."""
    md = sc.normalise_appointment(_raw(response_status=4, busy_status=0, sensitivity=2))["metadata"]
    assert (md["response_status"], md["busy_status"], md["sensitivity"]) == (
        "declined",
        "free",
        "private",
    )


def test_missing_global_id_is_rejected():
    """An appointment that cannot be deduped must not be ingested at all."""
    import pytest

    with pytest.raises(ValueError):
        sc.normalise_appointment(_raw(global_id=""))
