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


def _store_source_item(item_id="G1:2026-08-20T09:00:00"):
    db.upsert_item(
        {
            "item_id": item_id,
            "source": "outlook_calendar",
            "title": "FAT — P905 panel",
            "author": "Alice Smith",
            "timestamp": "2026-08-20T09:00:00",
            "url": "",
            "category": "fyi",
            "summary": "Multi-day supplier acceptance test",
        }
    )


def test_the_queue_shows_the_proposal_beside_its_source_appointment(client):
    """PVC-REQ-F-019 — the user cannot ratify what they cannot see next to its evidence."""
    _wipe()
    _store_source_item()
    db.add_calendar_proposal(
        "G1:2026-08-20T09:00:00",
        "card",
        _payload(
            source_end="2026-08-22T17:00:00",
            source_location="Bay 3",
            source_organizer="Alice Smith <alice@example.com>",
            source_attendees=["Bob <bob@example.com>"],
        ),
    )
    rows = client.get("/calendar/proposals").json()
    assert len(rows) == 1
    assert rows[0]["payload"]["title"] == "FAT — P905 panel"
    assert rows[0]["source"]["title"] == "FAT — P905 panel"
    assert rows[0]["source"]["start"] == "2026-08-20T09:00:00"
    assert rows[0]["source"]["end"] == "2026-08-22T17:00:00"
    assert rows[0]["source"]["location"] == "Bay 3"
    assert rows[0]["source"]["organizer"] == "Alice Smith <alice@example.com>"
    assert rows[0]["source"]["attendees"] == ["Bob <bob@example.com>"]


def test_rejecting_records_the_decision(client):
    """PVC-REQ-F-020."""
    _wipe()
    row = db.add_calendar_proposal("G1:a", "card", _payload())
    assert client.post(f"/calendar/proposals/{row['id']}/reject").status_code == 200
    assert db.get_calendar_proposal(row["id"])["decision"] == "rejected"
    assert client.get("/calendar/proposals").json() == []


def test_re_deciding_is_a_400_and_an_unknown_id_is_a_404(client):
    _wipe()
    row = db.add_calendar_proposal("G1:a", "card", _payload())
    client.post(f"/calendar/proposals/{row['id']}/reject")
    assert client.post(f"/calendar/proposals/{row['id']}/reject").status_code == 400
    assert client.post("/calendar/proposals/99999/reject").status_code == 404


def test_pull_complete_records_the_window_bounds(client):
    """Design §3.7 — Plan 2's absence detection consumes this."""
    _wipe()
    body = {
        "window_start": "2026-08-07T06:00:00",
        "window_end": "2026-11-12T06:00:00",
        "item_ids": ["G1:2026-08-20T09:00:00"],
    }
    assert client.post("/calendar/pull-complete", json=body).json()["seen"] == 1
    state = db.get_model_state("calendar_last_pull")
    assert state["window_start"] == "2026-08-07T06:00:00"
    assert state["window_end"] == "2026-11-12T06:00:00"
    assert state["item_ids"] == ["G1:2026-08-20T09:00:00"]
    assert state["completed_at"]


def test_pull_complete_requires_both_bounds(client):
    """A pull with no window cannot scope absence, so it is refused."""
    _wipe()
    assert client.post("/calendar/pull-complete", json={"item_ids": []}).status_code == 400


import calendar_ingest as ci


def _shifts(project="P905"):
    db.upsert_project_shift(
        project, 1, {"label": "Days", "start_time": "06:00", "end_time": "14:00"}
    )
    db.upsert_project_shift(
        project, 2, {"label": "Back", "start_time": "14:00", "end_time": "22:00"}
    )
    db.upsert_project_shift(
        project, 3, {"label": "Nights", "start_time": "22:00", "end_time": "06:00"}
    )


def test_shift_is_resolved_from_the_appointment_start_time():
    """OQ-PVC-004 — no schema change; project_shifts already holds HH:MM windows."""
    _wipe()
    _shifts()
    assert ci.resolve_shift_num("P905", "09:00") == 1
    assert ci.resolve_shift_num("P905", "15:30") == 2


def test_an_overnight_shift_window_wraps():
    _wipe()
    _shifts()
    assert ci.resolve_shift_num("P905", "23:30") == 3
    assert ci.resolve_shift_num("P905", "02:00") == 3


def test_shift_falls_back_to_one_when_unconfigured_or_unmatched():
    """CON-PVC-008 — the card schema's own column default."""
    _wipe()
    assert ci.resolve_shift_num("P999", "09:00") == 1
    assert ci.resolve_shift_num("P905", "not-a-time") == 1


def test_accepting_a_card_proposal_creates_a_card_and_its_todo(client):
    """PVC-REQ-F-013 / F-018 / F-021 — through the API layer, so the mirror holds."""
    _wipe()
    _shifts()
    _store_source_item()
    row = db.add_calendar_proposal("G1:2026-08-20T09:00:00", "card", _payload())
    resp = client.post(f"/calendar/proposals/{row['id']}/accept")
    assert resp.status_code == 200
    card = resp.json()["card"]
    assert (card["title"], card["project"]) == ("FAT — P905 panel", "P905")
    assert (card["start_date"], card["end_date"]) == ("2026-08-20", "2026-08-22")
    # parsival#85: every card mirrors into a todo, and only the API layer does that.
    assert db.get_card_todo_id(card["id"]) is not None


def test_the_start_shift_comes_from_the_appointment_time(client):
    _wipe()
    _shifts()
    _store_source_item()
    payload = _payload()
    payload["source_start"] = "2026-08-20T15:00:00"
    row = db.add_calendar_proposal("G1:2026-08-20T09:00:00", "card", payload)
    card = client.post(f"/calendar/proposals/{row['id']}/accept").json()["card"]
    assert card["start_shift_num"] == 2


def test_the_project_can_be_corrected_at_acceptance(client):
    """PVC-REQ-F-017 — attribution must be editable before acceptance."""
    _wipe()
    _store_source_item()
    row = db.add_calendar_proposal("G1:2026-08-20T09:00:00", "card", _payload(project=""))
    card = client.post(f"/calendar/proposals/{row['id']}/accept", json={"project": "P910"}).json()[
        "card"
    ]
    assert card["project"] == "P910"
    assert db.get_calendar_proposal(row["id"])["payload"]["project"] == "P910"


def test_accepting_a_card_with_no_project_is_refused(client):
    """A card cannot be placed on a per-project board without a project."""
    _wipe()
    _store_source_item()
    row = db.add_calendar_proposal("G1:2026-08-20T09:00:00", "card", _payload(project=""))
    resp = client.post(f"/calendar/proposals/{row['id']}/accept")
    assert resp.status_code == 400
    assert db.get_calendar_proposal(row["id"])["decision"] is None


def test_no_card_exists_before_acceptance(client):
    """PVC-REQ-F-018 — manual-first is a founding principle, not a preference."""
    _wipe()
    _store_source_item()
    db.add_calendar_proposal("G1:2026-08-20T09:00:00", "card", _payload())
    assert db.list_lookahead_cards() == []


def test_accepting_the_same_proposal_twice_is_refused_not_a_server_error(client):
    """A double-click or retried accept must 400, not orphan a second card."""
    _wipe()
    _shifts()
    _store_source_item()
    row = db.add_calendar_proposal("G1:2026-08-20T09:00:00", "card", _payload())
    first = client.post(f"/calendar/proposals/{row['id']}/accept")
    assert first.status_code == 200
    second = client.post(f"/calendar/proposals/{row['id']}/accept")
    assert second.status_code == 400
    assert len(db.list_lookahead_cards()) == 1


def test_accepting_a_key_date_writes_it_to_the_analysis_row(client):
    """PVC-REQ-F-014 / CON-PVC-006 — the key-date path, not a parallel store."""
    _wipe()
    _store_source_item()
    payload = {
        "kind": "key_date",
        "title": "Drawing submission due",
        "project": "P905",
        "date": "2026-09-01",
        "reason": "Contractual submission milestone",
        "source_item_id": "G1:2026-08-20T09:00:00",
    }
    row = db.add_calendar_proposal("G1:2026-08-20T09:00:00", "key_date", payload)
    assert client.post(f"/calendar/proposals/{row['id']}/accept").status_code == 200

    import json

    stored = json.loads(db.get_item("G1:2026-08-20T09:00:00")["key_dates"])
    assert stored[0]["date"] == "2026-09-01"
    assert stored[0]["description"] == "Drawing submission due"
    assert db.get_calendar_proposal(row["id"])["decision"] == "accepted"


def test_accepting_a_link_to_a_card_records_it_on_that_card(client):
    """PVC-REQ-F-015 — closes the hole where the decision was made."""
    _wipe()
    _store_source_item()
    card = client.post(
        "/lookahead/cards",
        json={
            "title": "Panel rework",
            "project": "P905",
            "start_date": "2026-08-19",
            "end_date": "2026-08-21",
        },
    ).json()
    payload = {
        "kind": "link",
        "title": "Rework decision meeting",
        "target_type": "card",
        "target_id": card["id"],
        "reason": "The rework scope was agreed here",
        "source_item_id": "G1:2026-08-20T09:00:00",
    }
    row = db.add_calendar_proposal("G1:2026-08-20T09:00:00", "link", payload)
    assert client.post(f"/calendar/proposals/{row['id']}/accept").status_code == 200
    links = db.get_lookahead_card(card["id"])["links"]
    assert {"type": "item", "id": "G1:2026-08-20T09:00:00"} in [
        {"type": lnk["type"], "id": str(lnk["id"])} for lnk in links
    ]


def test_accepting_a_link_to_a_situation_joins_the_event_to_it(client):
    """PVC-REQ-F-015 — situations are joined the way every other item joins one."""
    _wipe()
    _store_source_item()
    db.insert_situation(
        {
            "situation_id": "sit-1",
            "title": "Panel rework",
            "summary": "",
            "status": "in_progress",
            "item_ids": [],
            "sources": [],
            "project_tag": "P905",
            "score": 0.0,
            "priority": "medium",
            "open_actions": [],
            "references": [],
            "key_context": None,
            "last_updated": "2026-08-20T09:00:00",
            "created_at": "2026-08-20T09:00:00",
            "score_updated_at": "2026-08-20T09:00:00",
        }
    )
    payload = {
        "kind": "link",
        "title": "Rework decision meeting",
        "target_type": "situation",
        "target_id": "sit-1",
        "reason": "The rework scope was agreed here",
        "source_item_id": "G1:2026-08-20T09:00:00",
    }
    row = db.add_calendar_proposal("G1:2026-08-20T09:00:00", "link", payload)
    assert client.post(f"/calendar/proposals/{row['id']}/accept").status_code == 200
    assert db.get_item("G1:2026-08-20T09:00:00")["situation_id"] == "sit-1"
    assert "G1:2026-08-20T09:00:00" in db.get_situation("sit-1")["item_ids"]


def test_accepting_a_link_whose_target_has_vanished_is_a_400(client):
    """A target deleted between proposal and acceptance must not 500."""
    _wipe()
    _store_source_item()
    payload = {
        "kind": "link",
        "target_type": "situation",
        "target_id": "gone",
        "reason": "x",
        "source_item_id": "G1:2026-08-20T09:00:00",
    }
    row = db.add_calendar_proposal("G1:2026-08-20T09:00:00", "link", payload)
    assert client.post(f"/calendar/proposals/{row['id']}/accept").status_code == 400
    assert db.get_calendar_proposal(row["id"])["decision"] is None
