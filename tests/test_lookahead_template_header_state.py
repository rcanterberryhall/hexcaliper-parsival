"""The look-ahead template editor's header survives a task edit (parsival#120).

`_laRenderTemplateEditor` rebuilds the whole modal body from `_LA.editingTemplate`
whenever a task is added, removed, given a resource or re-scheduled. The task
controls write through to that object on input; the five header controls (name,
owner, default project, duration unit, description) used to be read only at save
time, out of the DOM, so every repaint reverted them -- a rename plus any task
edit saved the template under its old name.

The source assertions below are derived from the shipped `web/page/index.html`
rather than hardcoded, so a later edit that moves or renames these functions
fails loudly instead of silently testing nothing.
"""

import re
from datetime import date
from pathlib import Path

import db

PAGE = Path(__file__).resolve().parents[1] / "web" / "page" / "index.html"


def _slice_function(name: str) -> str:
    """Return the body of a top-level `function <name>` from the shipped page."""
    src = PAGE.read_text(encoding="utf-8")
    lines = src.split("\n")
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^(async )?function {re.escape(name)}\s*\(", line):
            start = i
            break
    assert start is not None, f"{name} not found in {PAGE.name} -- did it move or get renamed?"
    for j in range(start + 1, len(lines)):
        if re.match(r"^(async )?function \w+\s*\(", lines[j]):
            return "\n".join(lines[start:j])
    return "\n".join(lines[start:])


# ── The header controls write through, exactly as the task controls do ───────


def test_every_header_control_writes_through_to_the_editor_state():
    """Each laTpl* control in the editor calls _laTplEditHeader on input."""
    body = _slice_function("_laRenderTemplateEditor")
    controls = re.findall(r"<(input|select)\b[^>]*\bid=\"(laTpl\w+)\"[^>]*>", body)
    assert controls, "no laTpl* header controls found -- the editor markup moved"

    missing = []
    for _tag, ctrl_id in controls:
        # Re-find the whole tag for this id so the handler check reads its attributes.
        tag = re.search(rf"<(?:input|select)\b[^>]*\bid=\"{ctrl_id}\"[^>]*>", body).group(0)
        if "_laTplEditHeader(" not in tag:
            missing.append(ctrl_id)
    assert not missing, f"header controls with no write-through handler: {missing}"


def test_header_controls_cover_every_field_the_renderer_reads():
    """The fields rendered from the state are the same ones written back to it."""
    body = _slice_function("_laRenderTemplateEditor")
    written = set(re.findall(r"_laTplEditHeader\('(\w+)'", body))
    assert written == {
        "name",
        "owner",
        "default_project_tag",
        "duration_unit",
        "description",
    }, f"header write-through covers {sorted(written)}"


def test_edit_header_assigns_onto_the_editing_template():
    """_laTplEditHeader stores onto _LA.editingTemplate, the render's source."""
    fn = _slice_function("_laTplEditHeader")
    assert "_LA.editingTemplate[field] = value" in fn


# ── The save path no longer treats the DOM as the store ──────────────────────


def test_save_does_not_read_header_values_out_of_the_dom():
    """_laSaveTemplate reads the state; a DOM read would revert with the repaint."""
    fn = _slice_function("_laSaveTemplate")
    dom_reads = re.findall(r"\$\('(laTpl\w+)'\)", fn)
    assert not dom_reads, f"_laSaveTemplate still reads the DOM for {dom_reads}"


def test_save_still_requires_a_name():
    """The name guard is kept -- the fix must not drop the POST precondition."""
    fn = _slice_function("_laSaveTemplate")
    assert "if (!t.name)" in fn
    assert "Name is required." in fn


def test_save_still_trims_the_header_text_fields():
    """Trimming survives the move off the DOM, so '  x  ' is not stored verbatim."""
    fn = _slice_function("_laSaveTemplate")
    for field in ("name", "owner", "default_project_tag"):
        assert re.search(rf"t\.{field}\s*=\s*\(t\.{field} \|\| ''\)\.trim\(\)", fn), (
            f"{field} is no longer trimmed at save"
        )


# ── The repaint set the fix had to survive, derived from the file ────────────


def test_the_editor_repaint_has_the_callers_the_fix_assumed():
    """Six task-side actions re-render the body; each would revert a DOM-only header."""
    src = PAGE.read_text(encoding="utf-8")
    callers = set()
    current = None
    for line in src.split("\n"):
        sig = re.match(r"^(?:async )?function (\w+)\s*\(", line)
        if sig:
            current = sig.group(1)
        if "_laRenderTemplateEditor()" in line and current:
            callers.add(current)
    assert {
        "_laTplAddTask",
        "_laTplRemoveTask",
        "_laTplAddReq",
        "_laTplRemoveReq",
        "_laTplEditWorkPreset",
        "openTemplateEditor",
    } <= callers, f"repaint callers changed: {sorted(callers)}"


def test_the_renderer_still_rebuilds_the_whole_body():
    """The repaint is real: the fix keeps the render, it does not neuter it."""
    body = _slice_function("_laRenderTemplateEditor")
    assert "$('laModalBody').innerHTML" in body


# ── Backend premises: these must hold on both refs ───────────────────────────


def _wipe_templates():
    c = db.conn()
    for tbl in (
        "lookahead_template_task_resources",
        "lookahead_template_task_deps",
        "lookahead_template_tasks",
        "lookahead_template_instances",
        "lookahead_templates",
    ):
        c.execute(f"DELETE FROM {tbl}")


def test_patch_persists_a_rename(client):
    """A PATCH carrying a name stores it -- so a reverted name stores the old one."""
    _wipe_templates()
    created = client.post(
        "/lookahead/templates",
        json={"name": "Weekly inspection", "duration_unit": "calendar_days", "tasks": []},
    )
    assert created.status_code == 200
    tpl_id = created.json()["id"]

    renamed = client.patch(f"/lookahead/templates/{tpl_id}", json={"name": "Biweekly inspection"})
    assert renamed.status_code == 200
    assert client.get(f"/lookahead/templates/{tpl_id}").json()["name"] == "Biweekly inspection"

    # The silent-loss path: the editor re-sending the stored name is accepted.
    reverted = client.patch(f"/lookahead/templates/{tpl_id}", json={"name": "Weekly inspection"})
    assert reverted.status_code == 200
    assert client.get(f"/lookahead/templates/{tpl_id}").json()["name"] == "Weekly inspection"


def test_patch_persists_duration_unit(client):
    """duration_unit is patchable, so reverting it really does change what is stored."""
    _wipe_templates()
    created = client.post(
        "/lookahead/templates",
        json={"name": "Shift job", "duration_unit": "calendar_days", "tasks": []},
    )
    tpl_id = created.json()["id"]

    client.patch(f"/lookahead/templates/{tpl_id}", json={"duration_unit": "business_days"})
    assert client.get(f"/lookahead/templates/{tpl_id}").json()["duration_unit"] == "business_days"


def test_duration_unit_drives_instantiation_date_math(client):
    """business_days skips the weekend, so a reverted unit reschedules real cards."""
    _wipe_templates()
    task = {
        "local_id": "A",
        "title": "Inspect",
        "offset_start_days": 1,
        "offset_start_shift": 1,
        "duration_shifts": 1,
    }
    calendar = client.post(
        "/lookahead/templates",
        json={"name": "Cal", "duration_unit": "calendar_days", "tasks": [task]},
    ).json()
    business = client.post(
        "/lookahead/templates",
        json={"name": "Biz", "duration_unit": "business_days", "tasks": [task]},
    ).json()

    # Start on a Friday, so a one-day offset lands on Saturday under calendar-day
    # math and skips to Monday under business-day math. The date is derived, not
    # asserted by hand, so the premise cannot rot into a weekday that proves nothing.
    friday = date(2026, 4, 17)
    assert friday.weekday() == 4, "fixture date is no longer a Friday"
    start = friday.isoformat()

    cal_cards = client.post(
        f"/lookahead/templates/{calendar['id']}/instantiate",
        json={"start_date": start, "project_tag": "P905"},
    ).json()
    biz_cards = client.post(
        f"/lookahead/templates/{business['id']}/instantiate",
        json={"start_date": start, "project_tag": "P905"},
    ).json()

    cal_start = _first_card_start(cal_cards)
    biz_start = _first_card_start(biz_cards)
    assert cal_start != biz_start, (
        f"duration_unit did not change the schedule ({cal_start} vs {biz_start})"
    )


def _first_card_start(payload):
    cards = payload.get("cards")
    assert cards, f"instantiate returned no cards: {payload}"
    return cards[0]["start_date"]
