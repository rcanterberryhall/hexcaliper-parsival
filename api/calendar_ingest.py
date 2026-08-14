"""calendar_ingest.py — Server-side classification of Outlook calendar events.

Calendar items arrive through the unchanged ``/ingest`` contract under
``source="outlook_calendar"`` and branch out of the email orchestrator here
(``PVC-REQ-F-011``).  Three stages produce the verdict ``PVC-REQ-F-012``
requires — work, deadline, context, or ignorable::

    category override (F-028)  →  pre-filter (F-027)  →  model  →  proposal

Only survivors of the first two stages reach the model.  The email prompt is
not reused: it is tuned for recipient hierarchy and prose bodies, neither of
which an appointment has.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import calendar_filter
import config
import db
import llm

if TYPE_CHECKING:
    from models import RawItem

log = logging.getLogger("parsival.calendar")


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Defined locally rather than imported: ``db``, ``orchestrator`` and
    ``situation_manager`` each carry their own copy, so reaching into another
    module's private helper would be the odd one out.
    """
    return datetime.now(UTC).isoformat()


CALENDAR_SOURCE = calendar_filter.CALENDAR_SOURCE

_VERDICT_KINDS = ("card", "key_date", "link", "ignore")
_MAX_SITUATION_CANDIDATES = 15
_MAX_CARD_CANDIDATES = 15
_CARD_CANDIDATE_WINDOW_DAYS = 14


def _generate(prompt: str) -> str:
    """Call the LLM.  Split out so tests can patch one seam."""
    return llm.generate(
        prompt, format="json", num_predict=384, temperature=0.1, priority="background"
    )


def guess_project(item: RawItem) -> str:
    """Derive a project tag for an appointment, or return ``""``.

    Subject and location are matched against each project's configured and
    learned keywords; organiser and attendees against its configured and learned
    senders (OQ-PVC-006).  The project with the most matches wins; a tie or no
    match returns blank, because ``PVC-REQ-F-017`` makes the field editable
    before acceptance and a wrong guess costs the user a dropdown while a blank
    one costs them nothing.

    Args:
        item: A calendar ``RawItem``.

    Returns:
        A project name from ``config.PROJECTS``, or ``""``.
    """
    md = item.metadata or {}
    haystack = " ".join([item.title or "", md.get("location", "") or "", item.body or ""]).lower()
    people = " ".join([md.get("organizer", "") or "", *(md.get("attendees") or [])]).lower()

    scores: dict[str, int] = {}
    for p in config.PROJECTS or []:
        name = p.get("name") or ""
        if not name:
            continue
        keywords = list(p.get("keywords", [])) + list(p.get("learned_keywords", []))
        senders = list(p.get("senders", [])) + list(p.get("learned_senders", []))
        score = sum(1 for kw in keywords if kw and kw.lower() in haystack)
        score += sum(1 for s in senders if s and s.lower() in people)
        if score:
            scores[name] = score

    if not scores:
        return ""
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return ""  # ambiguous — say so rather than pick
    return ranked[0][0]


def _link_candidates(item: RawItem) -> tuple[list[dict], list[dict]]:
    """Return the situations and cards a context event could be linked to.

    Bounded on both sides — the prompt carries at most
    ``_MAX_SITUATION_CANDIDATES`` + ``_MAX_CARD_CANDIDATES`` rows, and a target
    outside these lists is rejected by :func:`classify`.

    Args:
        item: A calendar ``RawItem``.

    Returns:
        ``(situations, cards)`` as trimmed ``{"id", "title"}`` dicts.
    """
    day = ((item.metadata or {}).get("start") or item.timestamp or "")[:10]
    try:
        anchor = datetime.fromisoformat(day)
    except ValueError:
        anchor = None

    with db.lock:
        situations = db.get_active_situations()[:_MAX_SITUATION_CANDIDATES]
        if anchor:
            cards = db.list_lookahead_cards(
                start_date=(anchor - timedelta(days=_CARD_CANDIDATE_WINDOW_DAYS))
                .date()
                .isoformat(),
                end_date=(anchor + timedelta(days=_CARD_CANDIDATE_WINDOW_DAYS)).date().isoformat(),
            )[:_MAX_CARD_CANDIDATES]
        else:
            cards = []

    return (
        [
            {"id": s.get("situation_id", ""), "title": (s.get("title") or "")[:120]}
            for s in situations
        ],
        [{"id": c.get("id", ""), "title": (c.get("title") or "")[:120]} for c in cards],
    )


def _parse_json_object(text: str) -> dict | None:
    """Parse the first JSON object in *text*, or return ``None``."""
    if not text:
        return None
    # Backtick fences written as `{3} rather than three literal backticks, so
    # this code survives being pasted into a Markdown plan.
    cleaned = re.sub(r"^`{3}(?:json)?|`{3}$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def classify(item: RawItem) -> dict | None:
    """Classify one calendar event with the model.

    Only events that survived the override and pre-filter reach here.

    Args:
        item: A calendar ``RawItem``.

    Returns:
        A verdict dict with keys ``kind``, ``project``, ``title``, ``reason``,
        ``start_date``, ``end_date``, ``date``, ``target_type``, ``target_id``.
        Returns ``None`` when the model call failed or its answer could not be
        parsed — that means "retry on the next pull", not "ignore", so a merLLM
        outage does not silently discard a week of meetings (design §8).
    """
    md = item.metadata or {}
    start_date = (md.get("start") or item.timestamp or "")[:10]
    end_date = (md.get("end") or md.get("start") or item.timestamp or "")[:10]
    project_hint = guess_project(item)
    situations, cards = _link_candidates(item)

    event = json.dumps(
        {
            "subject": item.title,
            "organizer": md.get("organizer", ""),
            "attendees": (md.get("attendees") or [])[:12],
            "location": md.get("location", ""),
            "start": md.get("start", ""),
            "end": md.get("end", ""),
            "all_day": md.get("all_day", False),
            "recurring": md.get("is_recurring", False),
            "body": (item.body or "")[:800],
        }
    )
    prompt = (
        "You triage a plant engineer's calendar. Classify ONE appointment.\n"
        f"Appointment: {event}\n"
        f"Project keyword match (may be wrong or blank): {project_hint or '(none)'}\n"
        f"Open situations: {json.dumps(situations)}\n"
        f"Nearby planned cards: {json.dumps(cards)}\n\n"
        "Choose exactly one kind:\n"
        '  "card"     — scheduled work that belongs on a planning board '
        "(a FAT, a site visit, a design review for the engineer's own project)\n"
        '  "key_date" — the appointment marks a deadline or milestone rather '
        "than work to attend\n"
        '  "link"     — the appointment is context for an open situation or a '
        "planned card listed above\n"
        '  "ignore"   — routine, informational, or not this engineer\'s work\n\n'
        "Reply with a JSON object and nothing else:\n"
        '{"kind": "card|key_date|link|ignore", "project": "<project name or \\"\\">", '
        '"title": "<short title>", "date": "<YYYY-MM-DD, key_date only>", '
        '"target_type": "situation|card", "target_id": "<id from the lists above>", '
        '"reason": "<one short sentence>"}\n'
        'Use "ignore" when unsure. Never invent a target_id.'
    )

    try:
        raw = _generate(prompt)
    except Exception as exc:
        log.warning("calendar classify failed for %s: %s", item.item_id, exc)
        return None

    parsed = _parse_json_object(raw)
    if parsed is None:
        log.warning("calendar classify returned unparseable output for %s", item.item_id)
        return None

    kind = str(parsed.get("kind") or "").strip()
    if kind not in _VERDICT_KINDS:
        kind = "ignore"

    verdict = {
        "kind": kind,
        "project": str(parsed.get("project") or project_hint or "").strip(),
        "title": str(parsed.get("title") or item.title or "").strip()[:200],
        "reason": str(parsed.get("reason") or "").strip()[:300],
        "start_date": start_date,
        "end_date": end_date,
        "date": str(parsed.get("date") or "").strip()[:10] or start_date,
        "target_type": "",
        "target_id": "",
    }

    if kind == "link":
        target_type = str(parsed.get("target_type") or "").strip()
        target_id = str(parsed.get("target_id") or "").strip()
        valid = {"situation": {s["id"] for s in situations}, "card": {c["id"] for c in cards}}
        if target_id and target_id in valid.get(target_type, set()):
            verdict["target_type"] = target_type
            verdict["target_id"] = target_id
        else:
            # An invented target cannot be landed, so there is nothing to
            # propose.  Downgrade rather than store an unacceptable row.
            verdict["kind"] = "ignore"
            verdict["reason"] = "link target not in candidate set"

    return verdict


def _record_drop(item: RawItem, reason: str) -> None:
    """Store a dropped event so it is auditable and never re-classified.

    ``/ingest`` deduplicates against the items table, so writing this row is
    what stops the event coming back through the model on every four-hourly
    pull (``PVC-REQ-F-016``, ``PVC-REQ-F-010``).  ``category='filtered'`` is the
    existing convention for "stored but invisible in the normal UI", shared with
    the e-mail noise filter.

    Args:
        item: The calendar ``RawItem`` being dropped.
        reason: The rule slug that fired, recorded for later tuning.
    """
    with db.lock:
        if db.get_item(item.item_id):
            return
        db.upsert_item(
            {
                "item_id": item.item_id,
                "source": item.source,
                "title": item.title,
                "author": item.author,
                "timestamp": item.timestamp,
                "url": item.url,
                "has_action": 0,
                "priority": "low",
                "category": "filtered",
                "summary": f"[calendar drop: {reason}]",
                "urgency": None,
                "action_items": "[]",
                "to_field": (item.metadata or {}).get("to", ""),
                "processed_at": _now_iso(),
            }
        )


def _record_proposal(item: RawItem, verdict: dict) -> None:
    """Store the source event and its proposal.

    The event row is written first so the review queue can show the appointment
    beside the proposed outcome (``PVC-REQ-F-019``, Plan 2's surface) and so the
    next pull deduplicates.  The proposal row is the only thing the user acts
    on; nothing reaches the board without acceptance (``PVC-REQ-F-018``).

    Args:
        item: The calendar ``RawItem``.
        verdict: A verdict dict from :func:`classify` with a non-ignore kind.
    """
    kind = verdict["kind"]
    md = item.metadata or {}
    payload = {
        "kind": kind,
        "title": verdict.get("title") or item.title,
        "project": verdict.get("project", ""),
        "reason": verdict.get("reason", ""),
        "source_item_id": item.item_id,
        "source_title": item.title,
        "source_start": md.get("start", ""),
        "source_end": md.get("end", ""),
        "source_location": md.get("location", ""),
        "source_organizer": md.get("organizer", ""),
        "source_attendees": md.get("attendees") or [],
    }
    if kind == "card":
        payload.update(
            {
                "start_date": verdict.get("start_date", ""),
                "end_date": verdict.get("end_date", ""),
            }
        )
    elif kind == "key_date":
        payload["date"] = verdict.get("date", "")
    elif kind == "link":
        payload.update(
            {
                "target_type": verdict.get("target_type", ""),
                "target_id": verdict.get("target_id", ""),
            }
        )

    with db.lock:
        db.upsert_item(
            {
                "item_id": item.item_id,
                "source": item.source,
                "title": item.title,
                "author": item.author,
                "timestamp": item.timestamp,
                "url": item.url,
                "has_action": 0,
                "priority": "low",
                "category": "fyi",
                "summary": verdict.get("reason", "") or f"[calendar {kind} proposal]",
                "urgency": None,
                "action_items": "[]",
                "project_tag": verdict.get("project") or None,
                "body_preview": (item.body or "")[:2000],
                "to_field": md.get("to", ""),
                "processed_at": _now_iso(),
            }
        )
        db.add_calendar_proposal(item.item_id, kind, payload)


def handle_item(item: RawItem) -> None:
    """Run one calendar event through override, pre-filter, model, and storage.

    Stage order is load-bearing (design §4.2): the override runs first so a
    force-include category survives the pre-filter, and force-ignore
    short-circuits both later stages.

    A model failure returns without writing anything, so the event is picked up
    again on the next pull rather than being silently discarded (design §8).

    Args:
        item: A ``RawItem`` whose source is ``outlook_calendar``.
    """
    override = calendar_filter.category_override(item)
    if override == "ignore":
        _record_drop(item, "category_ignore")
        return
    if override != "include":
        reason = calendar_filter.prefilter_reason(item)
        if reason:
            _record_drop(item, reason)
            return

    verdict = classify(item)
    if verdict is None:
        return  # model failure — retry next pull, no row written
    if verdict["kind"] == "ignore":
        _record_drop(item, "classified_ignore")
        return
    _record_proposal(item, verdict)
