"""Tests for calendar proposal storage and the four calendar endpoints.

Covers PVC-REQ-F-010, F-013, F-014, F-015, F-017, F-018, F-020, F-021.
"""

import db


def _wipe():
    c = db.conn()
    for tbl in (
        "calendar_proposals",
        "lookahead_card_links",
        "lookahead_cards",
        "project_shifts",
        "items",
        "todos",
    ):
        c.execute(f"DELETE FROM {tbl}")


def _payload(**overrides) -> dict:
    body = {
        "kind": "card",
        "title": "FAT — P905 panel",
        "project": "P905",
        "start_date": "2026-08-20",
        "end_date": "2026-08-22",
        "start_shift_num": 1,
        "end_shift_num": 1,
        "notes": "Bay 3",
        "reason": "Multi-day supplier acceptance test",
    }
    body.update(overrides)
    return body


def test_a_proposal_round_trips_with_its_payload():
    _wipe()
    row = db.add_calendar_proposal("G1:2026-08-20T09:00:00", "card", _payload())
    assert row["kind"] == "card"
    assert row["decision"] is None
    assert row["payload"]["project"] == "P905"
    assert db.get_calendar_proposal(row["id"])["payload"]["title"] == "FAT — P905 panel"


def test_one_proposal_per_occurrence():
    """PVC-REQ-F-010 — a re-pull must not create a second proposal."""
    _wipe()
    first = db.add_calendar_proposal("G1:2026-08-20T09:00:00", "card", _payload())
    second = db.add_calendar_proposal("G1:2026-08-20T09:00:00", "card", _payload())
    assert first is not None
    assert second is None
    assert len(db.list_calendar_proposals("all")) == 1


def test_pending_listing_excludes_decided_rows():
    _wipe()
    keep = db.add_calendar_proposal("G1:a", "card", _payload())
    gone = db.add_calendar_proposal("G2:b", "card", _payload())
    db.decide_calendar_proposal(gone["id"], "rejected")
    assert [r["id"] for r in db.list_calendar_proposals("pending")] == [keep["id"]]
    assert [r["id"] for r in db.list_calendar_proposals("rejected")] == [gone["id"]]


def test_a_rejected_proposal_is_recorded_not_deleted():
    """PVC-REQ-F-020 — a queue that resurrects rejected items stops being used."""
    _wipe()
    row = db.add_calendar_proposal("G1:a", "card", _payload())
    db.decide_calendar_proposal(row["id"], "rejected")
    again = db.get_calendar_proposal_by_item("G1:a")
    assert again["decision"] == "rejected"
    assert again["decided_at"]


def test_re_deciding_is_refused():
    """Reopening a decided row is exactly what F-020 forbids."""
    import pytest

    _wipe()
    row = db.add_calendar_proposal("G1:a", "card", _payload())
    db.decide_calendar_proposal(row["id"], "accepted", card_id="card-1")
    with pytest.raises(ValueError):
        db.decide_calendar_proposal(row["id"], "rejected")


def test_accepting_can_refresh_the_payload_snapshot():
    """The payload doubles as the accepted-field snapshot Plan 2 compares against."""
    _wipe()
    row = db.add_calendar_proposal("G1:a", "card", _payload())
    updated = db.decide_calendar_proposal(
        row["id"], "accepted", card_id="card-1", payload=_payload(project="P910")
    )
    assert updated["card_id"] == "card-1"
    assert updated["payload"]["project"] == "P910"
