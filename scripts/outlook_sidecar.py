"""
outlook_sidecar.py — Outlook email ingestion sidecar (Windows).

Reads recent emails from the local Outlook client via ``win32com`` and
POSTs them to the Parsival ``/ingest`` endpoint.  Intended to run on the
Windows host machine where Outlook is installed; the Parsival Docker stack
does not have access to ``win32com`` and relies on this script to supply
email data.

Cloudflare Access service token credentials are stored in Windows Credential
Manager via the ``keyring`` library and never written to disk or code.  Run
with ``--setup`` once to store them:

    python outlook_sidecar.py --setup

Verify the full pipeline (Outlook → API → ingest) before committing to a
seed run:

    python outlook_sidecar.py --test

Then seed the database with 30 days of history:

    python outlook_sidecar.py --seed

To seed AND automatically start LLM project inference, use:

    python outlook_sidecar.py --seed-and-infer

This ingests emails, then calls POST /seed to kick off the map-reduce
project discovery workflow.  It polls until the LLM has proposed projects
and is waiting for your review, then prints the UI URL.

Then run normally (or via Task Scheduler) to ingest emails:

    python outlook_sidecar.py

The normal run tracks a high-water mark (the timestamp through which email has
been ingested) in ``%LOCALAPPDATA%\\hexcaliper-parsival\\sidecar_state.json``.
Each run backfills everything since that mark instead of a fixed window, so the
schedule is gap-proof across arbitrarily long absences (host off / logged out)
rather than losing anything older than the lookback window.

Usage::

    pip install requests pywin32 keyring
    python outlook_sidecar.py --setup          # first-time credential setup
    python outlook_sidecar.py --test           # verify pipeline with 5 emails
    python outlook_sidecar.py --seed           # seed 30 days of history
    python outlook_sidecar.py --seed-and-infer # seed + auto-start project inference
    python outlook_sidecar.py                  # normal / scheduled run

Schedule with Windows Task Scheduler to run every 30–60 minutes.
"""

import contextlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

PAGE_API_URL = "https://parsival.hexcaliper.com/page/api"
LOOKBACK_HOURS = 48
MAX_EMAILS = 75
SEED_LOOKBACK_HOURS = 720  # 30 days
SEED_MAX_EMAILS = 500
TEST_LOOKBACK_HOURS = 24
TEST_MAX_EMAILS = 5

# Windows Credential Manager service name used by keyring.
_KEYRING_SERVICE = "hexcaliper-squire"
_KEY_CLIENT_ID = "cf_client_id"
_KEY_CLIENT_SECRET = "cf_client_secret"

# High-water-mark state.  The scheduled run persists the timestamp through which
# email has been ingested, so after any offline gap (machine off / logged out)
# the next run backfills *everything* since that mark rather than a fixed window.
# Stored under LOCALAPPDATA so it survives git operations and log cleanup.
_STATE_DIR = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "hexcaliper-parsival"
_STATE_FILE = _STATE_DIR / "sidecar_state.json"
_HWM_OVERLAP_HOURS = 1  # re-scan a little before the mark; /ingest dedupes by item_id
_HWM_MAX_EMAILS = 5000  # generous per-folder cap for long-gap backfills


def _load_high_water_mark() -> "datetime | None":
    """
    Return the persisted high-water mark, or ``None`` if it is unset or unreadable.

    A missing/corrupt state file is treated as "no mark" so the caller falls back
    to the default first-run window rather than crashing.

    :return: The datetime through which email has been ingested, or ``None``.
    :rtype: datetime | None
    """
    try:
        # utf-8-sig tolerates a BOM if some other tool wrote the file.
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8-sig"))
        return datetime.fromisoformat(data["last_ingested_through"])
    except Exception:
        return None


def _save_high_water_mark(ts: "datetime") -> None:
    """
    Persist the high-water mark.

    Best-effort: a write failure only means the next run uses a wider lookback,
    so it is logged as a warning rather than raised.

    :param ts: The datetime through which email is now confirmed ingested.
    :type ts: datetime
    """
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(
            json.dumps({"last_ingested_through": ts.isoformat()}, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"WARNING: could not persist high-water mark: {e}", flush=True)


def _load_credentials() -> tuple[str, str]:
    """
    Load Cloudflare Access service token credentials from Windows Credential Manager.

    :return: Tuple of ``(client_id, client_secret)``.
    :rtype: tuple[str, str]
    :raises SystemExit: If ``keyring`` is not installed or credentials have not
                        been stored yet (run with ``--setup`` first).
    """
    try:
        import keyring
    except ImportError:
        sys.exit("ERROR: keyring not installed.  Run: pip install keyring")

    client_id = keyring.get_password(_KEYRING_SERVICE, _KEY_CLIENT_ID) or ""
    client_secret = keyring.get_password(_KEYRING_SERVICE, _KEY_CLIENT_SECRET) or ""

    if not client_id or not client_secret:
        sys.exit(
            "ERROR: Cloudflare Access credentials not found.\n"
            "Run: python outlook_sidecar.py --setup"
        )
    return client_id, client_secret


def _setup() -> None:
    """
    Interactively store Cloudflare Access credentials in Windows Credential Manager.

    Prompts for the CF Access Client ID and Client Secret, then persists them
    via ``keyring`` so they are never written to code or environment variables.

    :raises SystemExit: If ``keyring`` is not installed.
    """
    try:
        import keyring
    except ImportError:
        sys.exit("ERROR: keyring not installed.  Run: pip install keyring")

    print("Hexcaliper Parsival — Credential Setup")
    print("Credentials will be stored in Windows Credential Manager.\n")
    client_id = input("CF Access Client ID:     ").strip()
    client_secret = input("CF Access Client Secret: ").strip()

    if not client_id or not client_secret:
        sys.exit("ERROR: Both values are required.")

    keyring.set_password(_KEYRING_SERVICE, _KEY_CLIENT_ID, client_id)
    keyring.set_password(_KEYRING_SERVICE, _KEY_CLIENT_SECRET, client_secret)
    print("\nCredentials saved to Windows Credential Manager.")


def _read_recipients(msg) -> tuple[list[str], list[str]]:
    """
    Extract To and CC recipient strings from a COM message object.

    Returns ``(to_list, cc_list)`` as lists of ``"Name <addr>"`` strings.
    Falls back to the plain ``msg.To`` / ``msg.CC`` string properties if the
    Recipients COM collection is inaccessible.

    :return: Tuple of ``(to_list, cc_list)``.
    :rtype: tuple[list[str], list[str]]
    """
    to_list, cc_list = [], []
    try:
        for r in msg.Recipients:
            addr = getattr(r, "Address", "") or ""
            name = getattr(r, "Name", "") or ""
            entry = f"{name} <{addr}>" if name and addr else (name or addr)
            rtype = getattr(r, "Type", 1)
            if rtype == 1:  # olTo
                to_list.append(entry)
            elif rtype == 2:  # olCC
                cc_list.append(entry)
    except Exception:
        to_list = [getattr(msg, "To", "") or ""]
        cc_list = [getattr(msg, "CC", "") or ""]
    return to_list, cc_list


def _normalise_subject(subject: str) -> str:
    """
    Strip Re:/Fwd:/Fw: prefixes from a subject line for use as conversation_topic.

    :param subject: Raw email subject string.
    :return: Cleaned subject with reply/forward prefixes removed.
    """
    return re.sub(r"^(Re|Fwd?|AW|WG):\s*", "", subject or "", flags=re.IGNORECASE).strip()


# ── Calendar acquisition (parsival calendar ingest, Plan 1) ───────────────────
#
# Appointments travel the same /ingest contract as email but under their own
# source value, so the server can branch without inspecting payload shape
# (PVC-REQ-F-011, OQ-PVC-010).  COM access is confined to _read_appointment;
# normalise_appointment below is pure, which is what makes the field mapping
# testable on Linux CI where win32com does not exist (design §3.5).

CALENDAR_SOURCE = "outlook_calendar"
_CALENDAR_FOLDER_ID = 9  # olFolderCalendar
CALENDAR_BACK_DAYS = 7  # OQ-PVC-009 — past events serve context only
CALENDAR_FORWARD_DAYS = 90  # OQ-PVC-009 — catches quarterly milestones and long-lead FATs
_CALENDAR_MAX_ITEMS = 2000  # bound on one pull; a 90-day window is normally far under this
_MAPI_TIME_FMT = "%m/%d/%Y %I:%M %p"

# MAPI enum → name.  Downstream rules read names, never the raw ints.
_RESPONSE_STATUS = {
    0: "none",
    1: "organizer",
    2: "tentative",
    3: "accepted",
    4: "declined",
    5: "not_responded",
}
_BUSY_STATUS = {0: "free", 1: "tentative", 2: "busy", 3: "out_of_office", 4: "working_elsewhere"}
_SENSITIVITY = {0: "normal", 1: "personal", 2: "private", 3: "confidential"}


def normalise_appointment(raw: dict) -> dict:
    """Convert an already-read appointment dict into an ``/ingest`` payload.

    Pure by construction — ``raw`` holds plain Python values read from COM by
    :func:`_read_appointment`, never COM objects.  That separation is what makes
    ``PVC-REQ-F-007``/``F-008``/``F-009`` testable on a machine without Outlook.

    The ``item_id`` is composite: every occurrence of a recurring series shares
    one ``GlobalAppointmentID``, so the id alone would collapse thirteen weekly
    reviews into one item.  Pairing it with the occurrence start keeps the id
    stable across folder and mailbox moves while staying unique per occurrence
    (``PVC-REQ-F-007``, ``PVC-REQ-F-008``).

    Args:
        raw: Already-read appointment values.  Requires ``start`` (datetime)
            and ``global_id`` (non-empty str).

    Returns:
        A dict matching the ``RawItem`` ingest contract.

    Raises:
        ValueError: If ``start`` or ``global_id`` is missing.  Such an item
            cannot be deduplicated, so it must not be ingested.
    """
    start = raw.get("start")
    global_id = str(raw.get("global_id") or "")
    if start is None:
        raise ValueError("appointment has no Start")
    if not global_id:
        raise ValueError("appointment has no GlobalAppointmentID")

    end = raw.get("end") or start
    body = re.sub(r"\n{3,}", "\n\n", (raw.get("body") or "")).strip()
    attendees = [a for a in (raw.get("attendees") or []) if a]
    categories = [c.strip() for c in (raw.get("categories") or "").split(";") if c.strip()]

    return {
        "source": CALENDAR_SOURCE,
        "item_id": f"{global_id}:{start.isoformat()}",
        "title": raw.get("subject") or "(no subject)",
        "body": body[:3000],
        "url": "",
        "author": raw.get("organizer") or "",
        "timestamp": start.isoformat(),
        "metadata": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "all_day": bool(raw.get("all_day")),
            "duration_minutes": int((end - start).total_seconds() // 60),
            "location": raw.get("location") or "",
            "organizer": raw.get("organizer") or "",
            "attendees": attendees,
            # Same shape as the email path's recipient string so contact
            # enrichment consumes it identically (PVC-REQ-N-004).
            "to": "; ".join(attendees),
            "is_recurring": bool(raw.get("is_recurring")),
            "response_status": _RESPONSE_STATUS.get(
                int(raw.get("response_status") or 0), "unknown"
            ),
            "busy_status": _BUSY_STATUS.get(int(raw.get("busy_status") or 0), "unknown"),
            "sensitivity": _SENSITIVITY.get(int(raw.get("sensitivity") or 0), "unknown"),
            "categories": categories,
        },
    }


def _com_datetime(value) -> "datetime | None":
    """Convert a COM date value to a naive ``datetime``, or ``None``.

    Args:
        value: A COM date object exposing ``year``..``second``, or ``None``.

    Returns:
        The equivalent ``datetime``, or ``None`` when *value* is falsy or
        unreadable.
    """
    if value is None:
        return None
    try:
        return datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)
    except Exception:
        return None


def _read_attendees(appt) -> list[str]:
    """Extract attendee strings from an appointment's Recipients collection.

    Falls back to the plain ``RequiredAttendees``/``OptionalAttendees`` string
    properties when the COM collection is inaccessible, matching how
    :func:`_read_recipients` degrades on the email path.

    Args:
        appt: An Outlook ``AppointmentItem`` COM object.

    Returns:
        Attendees as ``"Name <addr>"`` strings.
    """
    out: list[str] = []
    try:
        for r in appt.Recipients:
            addr = getattr(r, "Address", "") or ""
            name = getattr(r, "Name", "") or ""
            entry = f"{name} <{addr}>" if name and addr else (name or addr)
            if entry:
                out.append(entry)
    except Exception:
        out = []
    if not out:
        joined = "; ".join(
            s
            for s in (
                getattr(appt, "RequiredAttendees", "") or "",
                getattr(appt, "OptionalAttendees", "") or "",
            )
            if isinstance(s, str) and s
        )
        out = [s.strip() for s in joined.split(";") if s.strip()]
    return out


def _read_appointment(appt) -> dict:
    """Read every COM property this feature needs into a plain dict.

    This function is the entire COM boundary for calendar acquisition; nothing
    downstream of it touches a COM object.  Keeping the boundary this narrow is
    what lets the field mapping be unit-tested on Linux (design §3.5).

    Args:
        appt: An Outlook ``AppointmentItem`` COM object.

    Returns:
        Plain Python values keyed for :func:`normalise_appointment`.
    """
    return {
        "subject": getattr(appt, "Subject", "") or "",
        "start": _com_datetime(getattr(appt, "Start", None)),
        "end": _com_datetime(getattr(appt, "End", None)),
        "all_day": bool(getattr(appt, "AllDayEvent", False)),
        "location": getattr(appt, "Location", "") or "",
        "body": getattr(appt, "Body", "") or "",
        "organizer": getattr(appt, "Organizer", "") or "",
        "attendees": _read_attendees(appt),
        "global_id": str(getattr(appt, "GlobalAppointmentID", "") or ""),
        "is_recurring": bool(getattr(appt, "IsRecurring", False)),
        "response_status": int(getattr(appt, "ResponseStatus", 0) or 0),
        "busy_status": int(getattr(appt, "BusyStatus", 0) or 0),
        "sensitivity": int(getattr(appt, "Sensitivity", 0) or 0),
        "categories": getattr(appt, "Categories", "") or "",
    }


def _fetch_calendar_folder(
    ns,
    window_start: "datetime",
    window_end: "datetime",
    max_items: int = _CALENDAR_MAX_ITEMS,
) -> list[dict]:
    """Fetch appointments from the default calendar folder over a date window.

    The order of the three MAPI calls is load-bearing and is asserted by test:
    ``IncludeRecurrences`` must be set, then the collection sorted, and only
    then restricted.  Any other order returns recurrence *masters* instead of
    occurrences, and parsival has no way to expand a master — Outlook is the
    sole source of truth for recurrence (``CON-PVC-013``, ``PVC-REQ-F-003``).

    Iteration uses ``GetFirst``/``GetNext`` rather than ``Count``/``Item(i)``:
    on a recurrence-expanded collection the index-based accessors are not
    reliable.

    Args:
        ns: MAPI namespace COM object.
        window_start: Earliest appointment start to fetch.
        window_end: Latest appointment start to fetch.
        max_items: Hard cap on the number of appointments returned.

    Returns:
        Normalised appointment dicts ready for ``POST /ingest``.
    """
    folder = ns.GetDefaultFolder(_CALENDAR_FOLDER_ID)
    items = folder.Items
    items.IncludeRecurrences = True
    items.Sort("[Start]")
    lo = window_start.strftime(_MAPI_TIME_FMT)
    hi = window_end.strftime(_MAPI_TIME_FMT)
    items = items.Restrict(f"[Start] >= '{lo}' AND [Start] <= '{hi}'")

    out: list[dict] = []
    appt = items.GetFirst()
    while appt is not None and len(out) < max_items:
        # A single unreadable or un-dedupable appointment is skipped, the
        # same discipline the email loop applies (design §8).
        with contextlib.suppress(Exception):
            out.append(normalise_appointment(_read_appointment(appt)))
        appt = items.GetNext()
    return out


def _fetch_folder(
    ns,
    folder_id: int,
    cutoff: "datetime",
    max_emails: int,
    direction: str,
    time_field: str,
) -> list[dict]:
    """
    Fetch emails from a single Outlook folder.

    :param ns: MAPI namespace COM object.
    :param folder_id: Outlook folder constant (6 = Inbox, 5 = Sent).
    :param cutoff: Only fetch messages at or after this datetime.
    :param max_emails: Maximum number of messages to return.
    :param direction: ``"received"`` or ``"sent"``.
    :param time_field: COM property name for the message timestamp
                       (``"ReceivedTime"`` for Inbox, ``"SentOn"`` for Sent).
    :return: List of normalised email dicts.
    """
    folder = ns.GetDefaultFolder(folder_id)
    messages = folder.Items
    messages.Sort(f"[{time_field}]", True)

    cutoff_str = cutoff.strftime("%m/%d/%Y %I:%M %p")
    try:
        messages = messages.Restrict(f"[{time_field}] >= '{cutoff_str}'")
        messages.Sort(f"[{time_field}]", True)
    except Exception:
        pass

    items = []
    count = messages.Count
    for index in range(1, min(count, max_emails) + 1):
        try:
            msg = messages.Item(index)
            subject = getattr(msg, "Subject", None)
            ts_com = getattr(msg, time_field, None)
            if ts_com is None:
                continue

            dt = datetime(
                ts_com.year,
                ts_com.month,
                ts_com.day,
                ts_com.hour,
                ts_com.minute,
                ts_com.second,
            )
            if dt < cutoff:
                break

            body = re.sub(r"\n{3,}", "\n\n", (getattr(msg, "Body", "") or "")).strip()
            sender_name = getattr(msg, "SenderName", "") or ""
            sender_email = getattr(msg, "SenderEmailAddress", "") or ""
            to_list, cc_list = _read_recipients(msg)

            # Detect reply/forward via LastVerbExecuted (inbox only; sent = 0):
            # 102 = olReplyToSender, 103 = olReplyToAll, 104 = olForward
            last_verb = getattr(msg, "LastVerbExecuted", 0) or 0
            is_replied = last_verb in (102, 103)
            is_forwarded = last_verb == 104
            replied_at = None
            if last_verb in (102, 103, 104):
                try:
                    rv = msg.LastVerbExecutionTime
                    replied_at = datetime(
                        rv.year,
                        rv.month,
                        rv.day,
                        rv.hour,
                        rv.minute,
                        rv.second,
                    ).isoformat()
                except Exception:
                    pass

            conv_id = getattr(msg, "ConversationID", "") or ""
            conv_topic = _normalise_subject(getattr(msg, "ConversationTopic", "") or subject or "")

            items.append(
                {
                    "source": "outlook",
                    "item_id": str(getattr(msg, "EntryID", "")),
                    "title": subject or "(no subject)",
                    "body": body[:3000],
                    "url": "",
                    "author": f"{sender_name} <{sender_email}>".strip(),
                    "timestamp": dt.isoformat(),
                    "metadata": {
                        "direction": direction,
                        "is_read": getattr(msg, "UnRead", True) is False,
                        "to": "; ".join(to_list),
                        "cc": "; ".join(cc_list),
                        "is_replied": is_replied,
                        "is_forwarded": is_forwarded,
                        "replied_at": replied_at,
                        "conversation_id": conv_id,
                        "conversation_topic": conv_topic,
                    },
                }
            )
        except Exception:
            continue

    return items


def fetch(lookback_hours: int = LOOKBACK_HOURS, max_emails: int = MAX_EMAILS) -> list[dict]:
    """
    Connect to the local Outlook client and fetch recent emails from both
    Inbox and Sent Items.

    Uses ``win32com.client`` to access the MAPI namespace.  Each message is
    enriched with ``conversation_id`` and ``conversation_topic`` for
    graph-context linkage, and a ``direction`` flag (``"received"`` /
    ``"sent"``) so the LLM can apply appropriate hierarchy rules.

    :return: List of normalised email dicts ready for ``POST /ingest``.
    :rtype: list[dict]
    :raises SystemExit: If ``pywin32`` is not installed or Outlook is not running.
    """
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        sys.exit("ERROR: pywin32 not installed.  Run: pip install pywin32")

    pythoncom.CoInitialize()
    try:
        try:
            print("Connecting to Outlook...", flush=True)
            ns = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        except Exception as e:
            sys.exit(f"ERROR: Could not connect to Outlook — is it running? ({e})")

        cutoff = datetime.now() - timedelta(hours=lookback_hours)

        print("Fetching Inbox...", flush=True)
        inbox_items = _fetch_folder(
            ns,
            folder_id=6,
            cutoff=cutoff,
            max_emails=max_emails,
            direction="received",
            time_field="ReceivedTime",
        )
        print(f"  Inbox: {len(inbox_items)} items", flush=True)

        print("Fetching Sent Items...", flush=True)
        sent_items = _fetch_folder(
            ns,
            folder_id=5,
            cutoff=cutoff,
            max_emails=max_emails,
            direction="sent",
            time_field="SentOn",
        )
        print(f"  Sent:  {len(sent_items)} items", flush=True)

        return inbox_items + sent_items
    finally:
        pythoncom.CoUninitialize()


_POST_BATCH = 50  # items per /ingest request — keeps payloads well under nginx limits

# Resilience knobs for post().  Transient failures (5xx, timeouts) are retried;
# a batch that still fails is bisected to isolate a single "poison" item, which
# is skipped so one malformed email cannot stall the whole run.  If more than
# _MAX_SKIPPED_ITEMS are dropped the failure looks systemic, so the run aborts
# without advancing the high-water mark and retries everything next time.
_POST_RETRIES = 3  # attempts per group before bisecting / skipping
_POST_BACKOFF_SEC = 2  # base backoff between retries (scaled by attempt number)
_MAX_SKIPPED_ITEMS = 10  # abort the run if more than this many items are dropped


def _exit_on_http_error(exc: "requests.HTTPError") -> None:
    """
    Turn an opaque ``HTTPError`` into an actionable exit message.

    Cloudflare Access returns 401/403 when the service-token Client ID or
    Secret is wrong or missing. The bare ``requests`` exception text only
    says ``401 Client Error: Unauthorized for url: ...`` — easy to
    misread as an API bug. Trap those two codes explicitly and point the
    operator at ``--setup``. Anything else exits with the underlying
    message (still more informative than the old generic handler).
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (401, 403):
        sys.exit(
            f"ERROR: Cloudflare Access rejected the request ({status}).\n"
            "Your stored CF-Access-Client-Id / Client-Secret is wrong, "
            "expired, or missing a policy binding.\n"
            "Run: python outlook_sidecar.py --setup to update credentials."
        )
    sys.exit(f"ERROR: HTTP {status or '??'} from API — {exc}")


def _ingest_with_retry(items: list[dict], headers: dict, dropped: list[dict]) -> tuple[int, int]:
    """
    POST one group of items to ``/ingest``, retrying transient failures and
    bisecting to isolate a poison item.

    Retries up to ``_POST_RETRIES`` times on 5xx / timeouts (deterministic 4xx,
    except 429, are not retried).  If a multi-item group still fails it is split
    in half and each half retried independently; a lone item that keeps failing
    is appended to ``dropped`` and skipped, so one bad email cannot stall the run.

    Connection loss and 401/403 auth rejection are treated as systemic and abort
    the process (via ``sys.exit``) so the high-water mark is not advanced and the
    whole run is retried next time.  The same abort fires once more than
    ``_MAX_SKIPPED_ITEMS`` have been dropped, since that looks systemic rather
    than a handful of malformed emails.

    :param items: The group of email dicts to POST.
    :param headers: CF Access auth headers.
    :param dropped: Accumulator list; poison items are appended here.
    :return: ``(received, skipped)`` counts reported by the API for this group.
    :rtype: tuple[int, int]
    """
    last_exc = None
    for attempt in range(1, _POST_RETRIES + 1):
        try:
            r = requests.post(
                f"{PAGE_API_URL}/ingest",
                json={"items": items},
                headers=headers,
                timeout=30,
            )
            r.raise_for_status()
            result = r.json()
            return result.get("received", 0), result.get("skipped", 0)
        except requests.ConnectionError:
            # Endpoint unreachable — systemic; abort so the mark isn't advanced.
            sys.exit(f"ERROR: Could not reach API at {PAGE_API_URL} — is the appliance reachable?")
        except requests.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (401, 403):
                _exit_on_http_error(e)  # bad creds — systemic, abort
            last_exc = e
            # Deterministic client errors (except 429) won't change on retry.
            if status is not None and 400 <= status < 500 and status != 429:
                break
        except requests.Timeout as e:
            last_exc = e
        except Exception as e:
            last_exc = e

        if attempt < _POST_RETRIES:
            time.sleep(_POST_BACKOFF_SEC * attempt)
            print(f"    retry {attempt}/{_POST_RETRIES - 1} after error: {last_exc}", flush=True)

    # Retries exhausted.  Bisect a multi-item group to isolate the offender.
    if len(items) > 1:
        mid = len(items) // 2
        recv_a, skip_a = _ingest_with_retry(items[:mid], headers, dropped)
        recv_b, skip_b = _ingest_with_retry(items[mid:], headers, dropped)
        return recv_a + recv_b, skip_a + skip_b

    # A single item that still fails is a poison item — record and skip it.
    bad = items[0]
    dropped.append(bad)
    print(
        f"    SKIP uningestable item {bad.get('item_id', '?')} "
        f"({(bad.get('title', '') or '')[:60]!r}): {last_exc}",
        flush=True,
    )
    if len(dropped) > _MAX_SKIPPED_ITEMS:
        sys.exit(
            f"ERROR: aborting — more than {_MAX_SKIPPED_ITEMS} items failed to ingest, "
            "which looks systemic rather than a few bad emails. State not advanced; "
            "will retry next run."
        )
    return 0, 0


def post(items: list[dict], client_id: str, client_secret: str) -> None:
    """
    POST fetched email items to the Parsival ``/ingest`` endpoint in batches.

    Large seed runs (500 emails) would exceed nginx's default body-size limit
    in a single request.  Items are chunked into batches of ``_POST_BATCH``
    and POSTed sequentially; the API deduplicates by ``item_id`` so retries
    are safe.

    Each batch is posted via :func:`_ingest_with_retry`, which retries transient
    failures and bisects to isolate and skip a single poison item rather than
    aborting the whole run.  Genuinely systemic failures (unreachable endpoint,
    bad credentials, or more than ``_MAX_SKIPPED_ITEMS`` drops) still abort so
    the high-water mark is not advanced and the run is retried next time.

    :param items: List of email dicts as returned by ``fetch()``.
    :type items: list[dict]
    :param client_id: CF Access service token Client ID.
    :type client_id: str
    :param client_secret: CF Access service token Client Secret.
    :type client_secret: str
    :raises SystemExit: On systemic failure (unreachable API, auth rejection, or
                        too many uningestable items).
    """
    if not items:
        print("No emails found in lookback window.")
        return

    headers = {
        "CF-Access-Client-Id": client_id,
        "CF-Access-Client-Secret": client_secret,
    }
    total_received = total_skipped = 0
    dropped: list[dict] = []
    batches = [items[i : i + _POST_BATCH] for i in range(0, len(items), _POST_BATCH)]
    for idx, batch in enumerate(batches, 1):
        recv, skip = _ingest_with_retry(batch, headers, dropped)
        total_received += recv
        total_skipped += skip
        print(f"  Batch {idx}/{len(batches)}: accepted {recv}, skipped {skip}", flush=True)

    if dropped:
        ids = ", ".join(d.get("item_id", "?") for d in dropped)
        print(
            f"WARNING: {len(dropped)} item(s) could not be ingested and were skipped: {ids}",
            flush=True,
        )

    print(
        f"Done — {len(items)} sent, {total_received} accepted, {total_skipped} skipped"
        + (f", {len(dropped)} dropped." if dropped else "."),
        flush=True,
    )


def _test() -> None:
    """
    Verify the end-to-end pipeline with a small batch before a seed run.

    Fetches up to ``TEST_MAX_EMAILS`` from the last ``TEST_LOOKBACK_HOURS``
    and POSTs them to ``/ingest``.  Prints a clear pass/fail summary so the
    operator can confirm Outlook access, Cloudflare credentials, and API
    reachability are all working before committing to a full seed.

    :raises SystemExit: On any connection or API error (same as normal mode).
    """
    cf_id, cf_secret = _load_credentials()
    print(
        f"TEST MODE — fetching up to {TEST_MAX_EMAILS} emails "
        f"from the last {TEST_LOOKBACK_HOURS}h...",
        flush=True,
    )
    emails = fetch(lookback_hours=TEST_LOOKBACK_HOURS, max_emails=TEST_MAX_EMAILS)
    print(f"Found {len(emails)} emails", flush=True)
    if not emails:
        print(
            "WARNING: No emails found in the test window. "
            "Try a longer lookback or check that Outlook has recent mail.",
            flush=True,
        )
        return
    post(emails, cf_id, cf_secret)
    print(
        "\nTest passed. Outlook access, credentials, and API are working.\n"
        f"Run with --seed to ingest the last {SEED_LOOKBACK_HOURS // 24} days.",
        flush=True,
    )


def _seed_and_infer() -> None:
    """
    Seed 30 days of history, then automatically start the LLM project
    inference workflow (POST /seed) and poll until the state machine
    reaches the ``review`` state.

    The user must visit the UI to review and confirm the proposed projects
    before they are applied.  This function does not block past the
    ``review`` state — it simply removes the manual step of remembering
    to start inference after a seed run.
    """
    cf_id, cf_secret = _load_credentials()

    seed_start = datetime.now()
    print(
        f"SEED+INFER MODE — fetching Outlook emails "
        f"(last {SEED_LOOKBACK_HOURS}h / {SEED_LOOKBACK_HOURS // 24} days, "
        f"cap {SEED_MAX_EMAILS})...",
        flush=True,
    )
    emails = fetch(lookback_hours=SEED_LOOKBACK_HOURS, max_emails=SEED_MAX_EMAILS)
    print(f"Found {len(emails)} emails", flush=True)
    post(emails, cf_id, cf_secret)
    # Seeding ingests everything up to now — establish the high-water mark so the
    # first scheduled run continues seamlessly from here.
    _save_high_water_mark(seed_start)
    print(f"High-water mark set to {seed_start.isoformat()}", flush=True)

    headers = {
        "CF-Access-Client-Id": cf_id,
        "CF-Access-Client-Secret": cf_secret,
    }

    print("\nStarting project inference (POST /seed)...", flush=True)
    try:
        r = requests.post(f"{PAGE_API_URL}/seed", json={}, headers=headers, timeout=30)
        r.raise_for_status()
    except requests.ConnectionError:
        sys.exit(f"ERROR: Could not reach API at {PAGE_API_URL} — is the appliance reachable?")
    except requests.HTTPError as e:
        _exit_on_http_error(e)
    except Exception as e:
        sys.exit(f"ERROR starting seed state machine: {e}")

    terminal_states = {"review", "error", "done", "idle"}
    last_progress = ""
    while True:
        time.sleep(5)
        try:
            r = requests.get(f"{PAGE_API_URL}/seed/status", headers=headers, timeout=15)
            r.raise_for_status()
            job = r.json()
        except Exception as e:
            print(f"  (poll error: {e})", flush=True)
            continue

        state = job.get("state", "")
        progress = job.get("progress", "")

        if progress != last_progress:
            print(f"  [{state}] {progress}", flush=True)
            last_progress = progress

        if state in terminal_states:
            break

    if state == "review":
        print(
            "\nProject inference complete — the LLM has proposed projects for review.\n"
            "Open the Parsival UI to review, edit, and confirm:\n"
            f"  {PAGE_API_URL.replace('/page/api', '/page/')}\n"
            "After confirming, Parsival will re-analyse all items with the new project config.",
            flush=True,
        )
    elif state == "error":
        progress = job.get("progress", "unknown error")
        sys.exit(f"ERROR: Seed inference failed — {progress}")
    else:
        print(f"Seed state machine ended in state '{state}'.", flush=True)


def _run_scheduled() -> None:
    """
    Normal scheduled run with high-water-mark tracking.

    Computes the lookback window from the persisted high-water mark (the point
    through which email has already been ingested) rather than a fixed window,
    then advances the mark after a successful post.  This makes the scheduled
    run gap-proof for arbitrarily long absences: after the host has been off or
    logged out for hours or weeks, the next run backfills every email since the
    mark.  On the very first run — before any mark exists — it falls back to the
    default ``LOOKBACK_HOURS`` window.

    The mark advances to the newest email actually seen (not wall-clock ``now``),
    so mail that Outlook has not finished syncing past that point is picked up on
    a later run instead of being skipped.  When no new mail is found, the mark
    advances to the run start, since the window is confirmed empty through then.

    Coverage is bounded by ``_HWM_MAX_EMAILS`` per folder; if that cap is hit the
    oldest mail in a very large backfill may be truncated, so a warning points
    the operator at ``--seed``.
    """
    cf_id, cf_secret = _load_credentials()

    run_start = datetime.now()
    hwm = _load_high_water_mark()
    if hwm is None:
        lookback_hours = LOOKBACK_HOURS
        print(f"No prior state — first run, fetching last {lookback_hours}h...", flush=True)
    else:
        # Re-scan a small overlap before the mark; /ingest dedupes by item_id.
        span = (run_start - hwm) + timedelta(hours=_HWM_OVERLAP_HOURS)
        lookback_hours = max(1, int(span.total_seconds() // 3600) + 1)
        print(
            f"Last ingested through {hwm.isoformat()} — backfilling {lookback_hours}h...",
            flush=True,
        )

    emails = fetch(lookback_hours=lookback_hours, max_emails=_HWM_MAX_EMAILS)
    print(f"Found {len(emails)} emails", flush=True)

    if len(emails) >= _HWM_MAX_EMAILS:
        print(
            "WARNING: hit the fetch cap — the oldest emails in this backfill may "
            "not have been retrieved. Run 'python outlook_sidecar.py --seed' to "
            "backfill fully.",
            flush=True,
        )

    post(emails, cf_id, cf_secret)  # sys.exits on failure, so reaching here == success

    # Advance the mark only after a successful post.  Use the newest email seen
    # (never moving backward); if nothing new, the window is clean through run_start.
    new_mark = max(datetime.fromisoformat(e["timestamp"]) for e in emails) if emails else run_start
    if hwm is not None:
        new_mark = max(new_mark, hwm)
    _save_high_water_mark(new_mark)
    print(f"High-water mark advanced to {new_mark.isoformat()}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--setup":
        _setup()
    elif len(sys.argv) > 1 and sys.argv[1] == "--test":
        _test()
    elif len(sys.argv) > 1 and sys.argv[1] == "--seed-and-infer":
        _seed_and_infer()
    elif len(sys.argv) > 1 and sys.argv[1] == "--seed":
        cf_id, cf_secret = _load_credentials()
        seed_start = datetime.now()
        print(
            f"SEED MODE — fetching Outlook emails (last {SEED_LOOKBACK_HOURS}h / {SEED_LOOKBACK_HOURS // 24} days, cap {SEED_MAX_EMAILS})...",
            flush=True,
        )
        emails = fetch(lookback_hours=SEED_LOOKBACK_HOURS, max_emails=SEED_MAX_EMAILS)
        print(f"Found {len(emails)} emails", flush=True)
        post(emails, cf_id, cf_secret)
        # Seeding ingests everything up to now — establish the high-water mark so
        # the first scheduled run continues seamlessly from here.
        _save_high_water_mark(seed_start)
        print(f"High-water mark set to {seed_start.isoformat()}", flush=True)
    else:
        _run_scheduled()
