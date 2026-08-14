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


from unittest.mock import MagicMock


class _RecordingItems:
    """A mock Outlook Items collection that records the order of operations.

    Outlook silently returns recurrence *masters* rather than occurrences unless
    IncludeRecurrences is set and the collection is sorted before it is
    restricted.  The bug is invisible at runtime — you just get less data — so
    the order is asserted directly (PVC-REQ-F-003).
    """

    def __init__(self, appointments, calls):
        self._appts = list(appointments)
        self._cursor = 0
        self.calls = calls
        self.restrict_filter = None

    def __setattr__(self, name, value):
        if name == "IncludeRecurrences":
            self.calls.append("IncludeRecurrences")
        object.__setattr__(self, name, value)

    def Sort(self, field, descending=False):
        self.calls.append(f"Sort{field}")

    def Restrict(self, filter_str):
        self.calls.append("Restrict")
        self.restrict_filter = filter_str
        return self

    def GetFirst(self):
        self._cursor = 0
        return self.GetNext()

    def GetNext(self):
        if self._cursor >= len(self._appts):
            return None
        appt = self._appts[self._cursor]
        self._cursor += 1
        return appt


def _com_dt(dt):
    m = MagicMock()
    m.year, m.month, m.day = dt.year, dt.month, dt.day
    m.hour, m.minute, m.second = dt.hour, dt.minute, dt.second
    return m


def _make_appt(subject="Standup", global_id="G1", start=None, end=None, **attrs):
    start = start or datetime(2026, 8, 20, 9, 0)
    end = end or datetime(2026, 8, 20, 9, 30)
    appt = MagicMock()
    appt.Subject = subject
    appt.Start = _com_dt(start)
    appt.End = _com_dt(end)
    appt.AllDayEvent = attrs.get("all_day", False)
    appt.Location = attrs.get("location", "")
    appt.Body = attrs.get("body", "")
    appt.Organizer = attrs.get("organizer", "Alice Smith")
    appt.GlobalAppointmentID = global_id
    appt.IsRecurring = attrs.get("is_recurring", False)
    appt.ResponseStatus = attrs.get("response_status", 3)
    appt.BusyStatus = attrs.get("busy_status", 2)
    appt.Sensitivity = attrs.get("sensitivity", 0)
    appt.Categories = attrs.get("categories", "")
    appt.Recipients = attrs.get("recipients", [])
    return appt


def _make_ns(appointments, calls):
    ns = MagicMock()
    folder = MagicMock()
    folder.Items = _RecordingItems(appointments, calls)
    ns.GetDefaultFolder = MagicMock(return_value=folder)
    return ns


def test_retrieval_sets_include_recurrences_and_sorts_before_restricting():
    """PVC-REQ-F-003 — any other order returns masters, not occurrences."""
    calls = []
    ns = _make_ns([_make_appt()], calls)
    sc._fetch_calendar_folder(ns, datetime(2026, 8, 13), datetime(2026, 11, 11))
    assert calls == ["IncludeRecurrences", "Sort[Start]", "Restrict"]


def test_retrieval_reads_the_calendar_folder():
    """PVC-REQ-F-001 — folder 9 is olFolderCalendar; email uses 6 and 5."""
    calls = []
    ns = _make_ns([_make_appt()], calls)
    sc._fetch_calendar_folder(ns, datetime(2026, 8, 13), datetime(2026, 11, 11))
    ns.GetDefaultFolder.assert_called_once_with(9)


def test_restriction_covers_both_past_and_future():
    """PVC-REQ-F-002 — email looks backward only; look-ahead needs forward reach."""
    calls = []
    ns = _make_ns([_make_appt()], calls)
    sc._fetch_calendar_folder(ns, datetime(2026, 8, 13, 0, 0), datetime(2026, 11, 11, 0, 0))
    filt = ns.GetDefaultFolder.return_value.Items.restrict_filter
    assert "[Start] >= '08/13/2026 12:00 AM'" in filt
    assert "[Start] <= '11/11/2026 12:00 AM'" in filt


def test_every_occurrence_is_returned_not_just_the_master():
    """PVC-REQ-F-008 end-to-end through the fetch loop."""
    calls = []
    appts = [
        _make_appt(global_id="SERIES1", start=datetime(2026, 8, 20, 9, 0)),
        _make_appt(global_id="SERIES1", start=datetime(2026, 8, 27, 9, 0)),
        _make_appt(global_id="SERIES1", start=datetime(2026, 9, 3, 9, 0)),
    ]
    out = sc._fetch_calendar_folder(
        _make_ns(appts, calls), datetime(2026, 8, 13), datetime(2026, 11, 11)
    )
    assert len({i["item_id"] for i in out}) == 3


def test_an_unreadable_appointment_is_skipped_not_fatal():
    """Design §8 — a per-item COM failure must not stall the run."""
    calls = []
    broken = _make_appt(global_id="")  # no GlobalAppointmentID → ValueError
    good = _make_appt(global_id="G2")
    out = sc._fetch_calendar_folder(
        _make_ns([broken, good], calls), datetime(2026, 8, 13), datetime(2026, 11, 11)
    )
    assert [i["item_id"].split(":")[0] for i in out] == ["G2"]


def test_fetch_is_capped():
    """A pull must be bounded even if the window expands or a series is endless."""
    calls = []
    appts = [
        _make_appt(global_id=f"G{n}", start=datetime(2026, 8, 20, 9, n % 60)) for n in range(10)
    ]
    out = sc._fetch_calendar_folder(
        _make_ns(appts, calls), datetime(2026, 8, 13), datetime(2026, 11, 11), max_items=4
    )
    assert len(out) == 4
