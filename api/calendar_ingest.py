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

import calendar_filter
import config
import db
import llm

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


def guess_project(item) -> str:
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


def _link_candidates(item) -> tuple[list[dict], list[dict]]:
    """Return the situations and cards a context event could be linked to.

    Bounded on both sides — the prompt carries at most
    ``_MAX_SITUATION_CANDIDATES`` + ``_MAX_CARD_CANDIDATES`` rows, and a target
    outside these lists is rejected by :func:`classify`.

    Args:
        item: A calendar ``RawItem``.

    Returns:
        ``(situations, cards)`` as trimmed ``{"id", "title"}`` dicts.
    """
    day = (item.metadata or {}).get("start", item.timestamp or "")[:10]
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


def classify(item) -> dict | None:
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
