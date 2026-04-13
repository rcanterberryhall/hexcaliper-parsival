"""Tests for the look-ahead board (parsival#48, #49, #50).

Covers CRUD, templates, instantiation, reschedule, detach, auto-complete,
and the cross-system LLM linking suggestion pool.
"""
from unittest.mock import patch

import db


def _wipe_lookahead():
    c = db.conn()
    for tbl in ("lookahead_card_link_suggestions",
                "lookahead_card_resources", "lookahead_card_links",
                "lookahead_card_deps", "lookahead_cards",
                "lookahead_template_task_resources",
                "lookahead_template_task_deps",
                "lookahead_template_tasks",
                "lookahead_template_instances",
                "lookahead_templates",
                "lookahead_resources", "project_shifts"):
        c.execute(f"DELETE FROM {tbl}")


def _card_payload(**overrides):
    body = {
        "title":           "Install panel",
        "project":         "P905",
        "assignee":        "Alice",
        "start_date":      "2026-04-15",
        "start_shift_num": 1,
        "end_date":        "2026-04-16",
        "end_shift_num":   2,
        "status":          "planned",
    }
    body.update(overrides)
    return body


# ── Card CRUD ─────────────────────────────────────────────────────────────────

def test_create_card_generates_uuid(client):
    _wipe_lookahead()
    r = client.post("/lookahead/cards", json=_card_payload())
    assert r.status_code == 200
    card = r.json()
    assert card["id"] and len(card["id"]) >= 32   # UUID string
    assert card["title"] == "Install panel"
    assert card["status"] == "planned"


def test_create_card_validates_required_fields(client):
    _wipe_lookahead()
    r = client.post("/lookahead/cards", json={"title": "no dates"})
    assert r.status_code == 400


def test_create_card_rejects_inverted_dates(client):
    _wipe_lookahead()
    r = client.post("/lookahead/cards", json=_card_payload(
        start_date="2026-04-20", end_date="2026-04-18"))
    assert r.status_code == 400


def test_create_card_rejects_bad_status(client):
    _wipe_lookahead()
    r = client.post("/lookahead/cards", json=_card_payload(status="on_fire"))
    assert r.status_code == 400


def test_list_cards_filters_by_project_and_window(client):
    _wipe_lookahead()
    client.post("/lookahead/cards", json=_card_payload(title="A", project="P905",
        start_date="2026-04-10", end_date="2026-04-11"))
    client.post("/lookahead/cards", json=_card_payload(title="B", project="P905",
        start_date="2026-04-20", end_date="2026-04-21"))
    client.post("/lookahead/cards", json=_card_payload(title="C", project="OTHER",
        start_date="2026-04-15", end_date="2026-04-15"))

    all_p905 = client.get("/lookahead/cards?project=P905").json()
    assert {c["title"] for c in all_p905} == {"A", "B"}

    windowed = client.get("/lookahead/cards?project=P905"
                          "&start=2026-04-15&end=2026-04-25").json()
    assert {c["title"] for c in windowed} == {"B"}


def test_patch_card_preserves_relations(client):
    _wipe_lookahead()
    card = client.post("/lookahead/cards", json=_card_payload()).json()
    client.patch(f"/lookahead/cards/{card['id']}", json={"status": "in_progress"})
    refreshed = client.get(f"/lookahead/cards/{card['id']}").json()
    assert refreshed["status"] == "in_progress"
    assert refreshed["title"] == card["title"]  # other fields intact


def test_delete_card_returns_ok(client):
    _wipe_lookahead()
    card = client.post("/lookahead/cards", json=_card_payload()).json()
    r = client.delete(f"/lookahead/cards/{card['id']}")
    assert r.json()["ok"] is True
    assert client.get(f"/lookahead/cards/{card['id']}").status_code == 404


# ── Dependencies / links / resources on cards ────────────────────────────────

def test_card_dependencies_are_round_trippable(client):
    _wipe_lookahead()
    a = client.post("/lookahead/cards", json=_card_payload(title="A")).json()
    b = client.post("/lookahead/cards",
                    json=_card_payload(title="B", depends_on=[a["id"]])).json()
    assert b["depends_on"] == [a["id"]]

    # replacement semantics: PATCH with empty list clears deps
    client.patch(f"/lookahead/cards/{b['id']}", json={"depends_on": []})
    assert client.get(f"/lookahead/cards/{b['id']}").json()["depends_on"] == []


def test_card_self_dependency_ignored(client):
    _wipe_lookahead()
    a = client.post("/lookahead/cards", json=_card_payload(title="A")).json()
    client.patch(f"/lookahead/cards/{a['id']}", json={"depends_on": [a["id"]]})
    assert client.get(f"/lookahead/cards/{a['id']}").json()["depends_on"] == []


def test_card_deps_cascade_on_delete(client):
    _wipe_lookahead()
    a = client.post("/lookahead/cards", json=_card_payload(title="A")).json()
    b = client.post("/lookahead/cards",
                    json=_card_payload(title="B", depends_on=[a["id"]])).json()
    client.delete(f"/lookahead/cards/{a['id']}")
    refreshed = client.get(f"/lookahead/cards/{b['id']}").json()
    assert refreshed["depends_on"] == []


def test_card_links_validate_type(client):
    _wipe_lookahead()
    card = client.post("/lookahead/cards", json=_card_payload(
        links=[{"type": "todo", "id": "42"},
               {"type": "bogus", "id": "1"}])).json()
    types = {l["type"] for l in card["links"]}
    assert types == {"todo"}


def test_card_bom_and_resource_status_update(client):
    _wipe_lookahead()
    bob = client.post("/lookahead/resources",
                      json={"name": "Bob", "type": "person"}).json()
    card = client.post("/lookahead/cards", json=_card_payload(
        resources=[{"resource_id": bob["id"], "quantity": 1, "status": "needed"}])).json()
    assert len(card["resources"]) == 1
    assert card["resources"][0]["status"] == "needed"

    r = client.patch(f"/lookahead/cards/{card['id']}/resources/{bob['id']}",
                     json={"status": "secured"})
    updated = r.json()
    assert updated["resources"][0]["status"] == "secured"


# ── Resource catalog ─────────────────────────────────────────────────────────

def test_resource_crud_cycle(client):
    _wipe_lookahead()
    r = client.post("/lookahead/resources",
                    json={"name": "Crane", "type": "equipment"})
    res = r.json()
    assert res["name"] == "Crane" and res["type"] == "equipment"

    client.patch(f"/lookahead/resources/{res['id']}",
                 json={"notes": "5-ton"})
    fetched = [x for x in client.get("/lookahead/resources").json()
               if x["id"] == res["id"]][0]
    assert fetched["notes"] == "5-ton"

    client.delete(f"/lookahead/resources/{res['id']}")
    remaining = [x for x in client.get("/lookahead/resources").json()
                 if x["id"] == res["id"]]
    assert remaining == []


def test_resource_rejects_invalid_type(client):
    _wipe_lookahead()
    r = client.post("/lookahead/resources",
                    json={"name": "X", "type": "nonsense"})
    assert r.status_code == 400


def test_resource_list_filters_by_type(client):
    _wipe_lookahead()
    client.post("/lookahead/resources", json={"name": "Alice", "type": "person"})
    client.post("/lookahead/resources", json={"name": "Crane", "type": "equipment"})
    only_people = client.get("/lookahead/resources?type=person").json()
    assert [r["name"] for r in only_people] == ["Alice"]


# ── Project shift schedules ──────────────────────────────────────────────────

def test_shift_upsert_and_list(client):
    _wipe_lookahead()
    client.put("/lookahead/shifts/P905/1", json={
        "label": "1st", "start_time": "06:00", "end_time": "16:00",
        "days": "M,T,W,Th",
    })
    client.put("/lookahead/shifts/P905/2", json={
        "label": "2nd", "start_time": "14:00", "end_time": "24:00",
        "days": "M,T,W,Th",
    })
    shifts = client.get("/lookahead/shifts?project=P905").json()
    assert len(shifts) == 2
    assert shifts[0]["shift_num"] == 1 and shifts[0]["start_time"] == "06:00"

    # Upsert overwrites
    client.put("/lookahead/shifts/P905/1",
               json={"label": "1st updated", "start_time": "07:00",
                     "end_time": "17:00", "days": "M,T,W,Th,F"})
    reread = client.get("/lookahead/shifts?project=P905").json()[0]
    assert reread["label"] == "1st updated" and reread["days"] == "M,T,W,Th,F"


def test_shift_rejects_invalid_num(client):
    _wipe_lookahead()
    r = client.put("/lookahead/shifts/P905/7", json={"label": "x"})
    assert r.status_code == 400


def test_shift_delete_scoped_to_project(client):
    _wipe_lookahead()
    client.put("/lookahead/shifts/P905/1", json={"label": "1st"})
    client.put("/lookahead/shifts/OTHER/1", json={"label": "1st-other"})
    client.delete("/lookahead/shifts/P905/1")
    remaining = client.get("/lookahead/shifts").json()
    assert [s["project_tag"] for s in remaining] == ["OTHER"]


# ── Overview ─────────────────────────────────────────────────────────────────

def test_overview_groups_and_sorts_by_earliest(client):
    _wipe_lookahead()
    client.post("/lookahead/cards", json=_card_payload(project="LATE",
        start_date="2026-04-20", end_date="2026-04-21"))
    client.post("/lookahead/cards", json=_card_payload(project="EARLY",
        start_date="2026-04-10", end_date="2026-04-11"))
    rows = client.get("/lookahead/overview").json()
    assert [r["project"] for r in rows] == ["EARLY", "LATE"]
    assert len(rows[0]["cards"]) == 1


# ── Templates (parsival#49) ───────────────────────────────────────────────────

def _template_payload(**overrides):
    """Two-task template: A (day 0, 1 shift), B (day 2, 2 shifts) depends on A."""
    body = {
        "name": "Weekly inspection",
        "description": "Routine weekly check",
        "owner": "Alice",
        "duration_unit": "calendar_days",
        "default_project_tag": "P905",
        "tasks": [
            {
                "local_id": "A",
                "title": "Pre-check",
                "offset_start_days": 0,
                "offset_start_shift": 1,
                "duration_shifts": 1,
            },
            {
                "local_id": "B",
                "title": "Main inspection",
                "offset_start_days": 2,
                "offset_start_shift": 1,
                "duration_shifts": 2,
                "depends_on": ["A"],
            },
        ],
    }
    body.update(overrides)
    return body


def test_template_create_and_round_trip(client):
    _wipe_lookahead()
    r = client.post("/lookahead/templates", json=_template_payload())
    assert r.status_code == 200
    tpl = r.json()
    assert tpl["name"] == "Weekly inspection"
    assert tpl["version"] == 1
    assert len(tpl["tasks"]) == 2
    task_b = [t for t in tpl["tasks"] if t["local_id"] == "B"][0]
    assert task_b["depends_on"] == ["A"]


def test_template_requires_name(client):
    _wipe_lookahead()
    r = client.post("/lookahead/templates", json={"tasks": []})
    assert r.status_code == 400


def test_template_rejects_invalid_duration_unit(client):
    _wipe_lookahead()
    r = client.post("/lookahead/templates",
                    json=_template_payload(duration_unit="weeks"))
    assert r.status_code == 400


def test_template_patch_bumps_version_and_replaces_tasks(client):
    _wipe_lookahead()
    tpl = client.post("/lookahead/templates", json=_template_payload()).json()
    assert tpl["version"] == 1
    r = client.patch(f"/lookahead/templates/{tpl['id']}", json={
        "description": "Updated",
        "tasks": [{"local_id": "solo", "title": "Just one",
                   "offset_start_days": 0, "duration_shifts": 1}],
    })
    refreshed = r.json()
    assert refreshed["version"] == 2
    assert refreshed["description"] == "Updated"
    assert [t["local_id"] for t in refreshed["tasks"]] == ["solo"]


def test_template_delete_cascades_tasks(client):
    _wipe_lookahead()
    tpl = client.post("/lookahead/templates", json=_template_payload()).json()
    client.delete(f"/lookahead/templates/{tpl['id']}")
    assert client.get(f"/lookahead/templates/{tpl['id']}").status_code == 404


def test_instantiate_materializes_cards_with_correct_dates(client):
    _wipe_lookahead()
    tpl = client.post("/lookahead/templates", json=_template_payload()).json()
    r = client.post(f"/lookahead/templates/{tpl['id']}/instantiate",
                    json={"start_date": "2026-05-04", "project_tag": "P905"})
    assert r.status_code == 200
    inst = r.json()
    assert inst["status"] == "active"
    assert inst["template_version"] == 1
    cards_by_local = {c["template_task_local_id"]: c for c in inst["cards"]}
    # Task A: day 0, shift 1, duration 1 → starts & ends 2026-05-04 shift 1
    assert cards_by_local["A"]["start_date"] == "2026-05-04"
    assert cards_by_local["A"]["end_date"]   == "2026-05-04"
    assert cards_by_local["A"]["start_shift_num"] == 1
    assert cards_by_local["A"]["end_shift_num"]   == 1
    # Task B: day 2, shift 1, duration 2 → starts 2026-05-06, ends 2026-05-06 shift 2
    assert cards_by_local["B"]["start_date"] == "2026-05-06"
    assert cards_by_local["B"]["end_date"]   == "2026-05-06"
    assert cards_by_local["B"]["end_shift_num"] == 2
    # Dep wiring: B depends_on [A.id]
    assert cards_by_local["B"]["depends_on"] == [cards_by_local["A"]["id"]]


def test_instantiate_business_days_skips_weekends(client):
    _wipe_lookahead()
    body = _template_payload(duration_unit="business_days",
                             tasks=[{"local_id": "x", "title": "X",
                                     "offset_start_days": 3, "duration_shifts": 1}])
    tpl = client.post("/lookahead/templates", json=body).json()
    # 2026-05-04 is a Monday → +3 business days → 2026-05-07 (Thu)
    inst = client.post(f"/lookahead/templates/{tpl['id']}/instantiate",
                       json={"start_date": "2026-05-04",
                             "project_tag": "P905"}).json()
    assert inst["cards"][0]["start_date"] == "2026-05-07"


def test_instantiate_copies_named_resources_to_bom(client):
    _wipe_lookahead()
    crane = client.post("/lookahead/resources",
                        json={"name": "Crane", "type": "equipment"}).json()
    body = _template_payload(tasks=[{
        "local_id": "x", "title": "Lift",
        "offset_start_days": 0, "duration_shifts": 1,
        "resource_requirements": [
            {"resource_type": "equipment",
             "named_resource_id": crane["id"], "quantity": 1},
            {"resource_type": "person", "role": "rigger", "quantity": 2},
        ],
    }])
    tpl = client.post("/lookahead/templates", json=body).json()
    inst = client.post(f"/lookahead/templates/{tpl['id']}/instantiate",
                       json={"start_date": "2026-05-04",
                             "project_tag": "P905"}).json()
    card = inst["cards"][0]
    # Named requirement → BOM entry; generic role-only requirement is skipped
    # at instantiation time (user assigns it later by editing the card).
    assert len(card["resources"]) == 1
    assert card["resources"][0]["resource_id"] == crane["id"]


def test_reschedule_instance_shifts_all_cards(client):
    _wipe_lookahead()
    tpl = client.post("/lookahead/templates", json=_template_payload()).json()
    inst = client.post(f"/lookahead/templates/{tpl['id']}/instantiate",
                       json={"start_date": "2026-05-04",
                             "project_tag": "P905"}).json()
    r = client.patch(f"/lookahead/instances/{inst['id']}",
                     json={"start_date": "2026-05-11"})
    rescheduled = r.json()
    by_local = {c["template_task_local_id"]: c for c in rescheduled["cards"]}
    # Shift is +7 calendar days.
    assert by_local["A"]["start_date"] == "2026-05-11"
    assert by_local["B"]["start_date"] == "2026-05-13"


def test_detach_card_keeps_it_out_of_reschedule(client):
    _wipe_lookahead()
    tpl = client.post("/lookahead/templates", json=_template_payload()).json()
    inst = client.post(f"/lookahead/templates/{tpl['id']}/instantiate",
                       json={"start_date": "2026-05-04",
                             "project_tag": "P905"}).json()
    detached_card = [c for c in inst["cards"]
                     if c["template_task_local_id"] == "A"][0]
    client.post(f"/lookahead/cards/{detached_card['id']}/detach")
    client.patch(f"/lookahead/instances/{inst['id']}",
                 json={"start_date": "2026-05-11"})
    # Re-fetch the detached card directly — it should not have moved.
    fresh = client.get(f"/lookahead/cards/{detached_card['id']}").json()
    assert fresh["start_date"] == "2026-05-04"
    assert fresh["template_instance_id"] is None


def test_instance_autocompletes_when_all_cards_done(client):
    _wipe_lookahead()
    tpl = client.post("/lookahead/templates", json=_template_payload()).json()
    inst = client.post(f"/lookahead/templates/{tpl['id']}/instantiate",
                       json={"start_date": "2026-05-04",
                             "project_tag": "P905"}).json()
    for card in inst["cards"][:-1]:
        client.patch(f"/lookahead/cards/{card['id']}", json={"status": "done"})
    # Not yet complete — one card still planned.
    assert client.get(f"/lookahead/instances/{inst['id']}").json()["status"] == "active"
    # Flip the last one.
    client.patch(f"/lookahead/cards/{inst['cards'][-1]['id']}",
                 json={"status": "done"})
    assert client.get(f"/lookahead/instances/{inst['id']}").json()["status"] == "complete"


def test_template_task_carries_linked_procedure_doc_to_card(client):
    _wipe_lookahead()
    tpl = client.post("/lookahead/templates", json=_template_payload(tasks=[{
        "local_id": "x", "title": "Follow SOP",
        "offset_start_days": 0, "duration_shifts": 1,
        "linked_procedure_doc": "https://docs.example/sop-42",
    }])).json()
    inst = client.post(f"/lookahead/templates/{tpl['id']}/instantiate",
                       json={"start_date": "2026-05-04",
                             "project_tag": "P905"}).json()
    assert inst["cards"][0]["linked_procedure_doc"] == "https://docs.example/sop-42"


# ── Cross-system linking (parsival#50) ────────────────────────────────────────

def _seed_item(item_id, project, title, timestamp="2026-04-15T08:00:00"):
    """Drop a minimal items row directly so the annotator has something to chew on."""
    db.upsert_item({
        "item_id": item_id, "source": "email", "title": title,
        "author": "", "timestamp": timestamp, "project_tag": project,
        "summary": f"{title} summary", "category": "task", "priority": "medium",
        "action_items": [], "goals": [], "key_dates": [],
        "information_items": [], "references": [],
    })


def test_annotate_card_persists_suggestions_from_llm(client):
    _wipe_lookahead()
    db.conn().execute("DELETE FROM items")
    _seed_item("em-1", "P905", "Panel install scheduling")
    _seed_item("em-2", "P905", "Totally unrelated coffee order")
    card = client.post("/lookahead/cards", json=_card_payload(
        title="Panel install", project="P905")).json()

    fake_response = '[{"item_id": "em-1", "reason": "scheduling thread"}]'
    with patch("app._llm.generate", return_value=fake_response):
        r = client.post(f"/lookahead/cards/{card['id']}/annotate")
    assert r.json()["created"] == 1

    suggestions = client.get(f"/lookahead/cards/{card['id']}/suggestions").json()
    assert len(suggestions) == 1
    assert suggestions[0]["target_id"] == "em-1"
    assert suggestions[0]["reason"] == "scheduling thread"
    assert suggestions[0]["target_title"] == "Panel install scheduling"


def test_annotate_skips_already_linked_items(client):
    _wipe_lookahead()
    db.conn().execute("DELETE FROM items")
    _seed_item("em-1", "P905", "Linked already")
    card = client.post("/lookahead/cards", json=_card_payload(
        title="X", project="P905",
        links=[{"type": "item", "id": "em-1"}])).json()

    with patch("app._llm.generate",
               return_value='[{"item_id": "em-1", "reason": "x"}]'):
        r = client.post(f"/lookahead/cards/{card['id']}/annotate")
    assert r.json()["created"] == 0


def test_annotate_is_idempotent_per_target(client):
    _wipe_lookahead()
    db.conn().execute("DELETE FROM items")
    _seed_item("em-1", "P905", "Something")
    card = client.post("/lookahead/cards", json=_card_payload(
        title="X", project="P905")).json()
    fake = '[{"item_id": "em-1", "reason": "match"}]'
    with patch("app._llm.generate", return_value=fake):
        client.post(f"/lookahead/cards/{card['id']}/annotate")
        r2 = client.post(f"/lookahead/cards/{card['id']}/annotate")
    assert r2.json()["created"] == 0
    assert len(client.get(f"/lookahead/cards/{card['id']}/suggestions").json()) == 1


def test_accept_suggestion_creates_card_link(client):
    _wipe_lookahead()
    db.conn().execute("DELETE FROM items")
    _seed_item("em-1", "P905", "Relevant thread")
    card = client.post("/lookahead/cards", json=_card_payload(
        title="X", project="P905")).json()
    with patch("app._llm.generate",
               return_value='[{"item_id": "em-1", "reason": "r"}]'):
        client.post(f"/lookahead/cards/{card['id']}/annotate")
    sugg = client.get(f"/lookahead/cards/{card['id']}/suggestions").json()[0]

    r = client.post(f"/lookahead/suggestions/{sugg['id']}/accept")
    assert r.json()["decision"] == "accepted"
    refreshed = client.get(f"/lookahead/cards/{card['id']}").json()
    assert {"type": "item", "id": "em-1"} in refreshed["links"]
    # Accepted suggestion no longer surfaces as pending.
    assert client.get(f"/lookahead/cards/{card['id']}/suggestions").json() == []


def test_reject_suggestion_does_not_create_link(client):
    _wipe_lookahead()
    db.conn().execute("DELETE FROM items")
    _seed_item("em-1", "P905", "Noise thread")
    card = client.post("/lookahead/cards", json=_card_payload(
        title="X", project="P905")).json()
    with patch("app._llm.generate",
               return_value='[{"item_id": "em-1", "reason": "r"}]'):
        client.post(f"/lookahead/cards/{card['id']}/annotate")
    sugg = client.get(f"/lookahead/cards/{card['id']}/suggestions").json()[0]
    client.post(f"/lookahead/suggestions/{sugg['id']}/reject")
    refreshed = client.get(f"/lookahead/cards/{card['id']}").json()
    assert refreshed["links"] == []


def test_annotate_project_fans_out_across_cards(client):
    _wipe_lookahead()
    db.conn().execute("DELETE FROM items")
    _seed_item("em-1", "P905", "Candidate")
    client.post("/lookahead/cards", json=_card_payload(title="A", project="P905"))
    client.post("/lookahead/cards", json=_card_payload(title="B", project="P905"))
    with patch("app._llm.generate",
               return_value='[{"item_id": "em-1", "reason": "r"}]'):
        r = client.post("/lookahead/annotate-project",
                        json={"project": "P905"})
    body = r.json()
    assert body["processed"] == 2
    assert body["new_suggestions"] == 2


def test_delete_instance_cascades_attached_cards(client):
    _wipe_lookahead()
    tpl = client.post("/lookahead/templates", json=_template_payload()).json()
    inst = client.post(f"/lookahead/templates/{tpl['id']}/instantiate",
                       json={"start_date": "2026-05-04",
                             "project_tag": "P905"}).json()
    card_ids = [c["id"] for c in inst["cards"]]
    client.delete(f"/lookahead/instances/{inst['id']}")
    for cid in card_ids:
        assert client.get(f"/lookahead/cards/{cid}").status_code == 404
    assert client.get(f"/lookahead/instances/{inst['id']}").status_code == 404
