"""Guards the single-timer invariant of the scan poll in web/page/index.html.

`scanTimer` is one module-level handle. Before #106 three separate sites
assigned `setInterval(pollScan, 1000)` to it and one site cleared it, so a
second start overwrote a live handle and orphaned an interval that nothing
could then reach. These tests assert the shipped markup keeps arming and
tearing down in one place each, and that the `situations_pending` branch which
makes the second start reachable still re-arms the buttons it is there to
re-arm.
"""

import re
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "web" / "page" / "index.html"


def _source():
    return PAGE.read_text(encoding="utf-8")


def _function_bodies(src):
    """Map name -> body text for every function declared at column 0.

    The inline script declares its top-level functions unindented and closes
    each with a `}` in column 0, so the extent is read off that rather than by
    counting braces through template literals.
    """
    lines = src.split("\n")
    starts = {}
    for i, line in enumerate(lines):
        m = re.match(r"(?:async )?function ([A-Za-z_$][\w$]*)\s*\(", line)
        if m:
            starts[m.group(1)] = i
    bodies = {}
    for name, start in starts.items():
        end = next((j for j in range(start + 1, len(lines)) if lines[j] == "}"), len(lines) - 1)
        bodies[name] = "\n".join(lines[start : end + 1])
    return bodies


ARM = "scanTimer = setInterval(pollScan, 1000)"
TEARDOWN = "clearInterval(scanTimer)"


def test_scan_poll_is_armed_in_exactly_one_place():
    src = _source()
    assert src.count(ARM) == 1, "the scan poll must be armed only inside startScanPoll()"
    assert ARM in _function_bodies(src)["startScanPoll"]


def test_scan_poll_is_torn_down_in_exactly_one_place():
    src = _source()
    assert src.count(TEARDOWN) == 1, "the scan poll must be cleared only inside stopScanPoll()"
    assert TEARDOWN in _function_bodies(src)["stopScanPoll"]


def test_arming_clears_any_poll_already_running():
    body = _function_bodies(_source())["startScanPoll"]
    assert body.index("stopScanPoll()") < body.index(ARM), (
        "startScanPoll() must clear the previous handle before assigning a new one, "
        "or the previous interval is orphaned beyond the reach of clearInterval"
    )


def test_no_other_function_arms_the_poll_directly():
    offenders = [
        name
        for name, body in _function_bodies(_source()).items()
        if name != "startScanPoll" and "setInterval(pollScan" in body
    ]
    assert offenders == [], f"these functions bypass startScanPoll(): {offenders}"


def test_the_three_starters_and_the_finisher_use_the_helpers():
    bodies = _function_bodies(_source())
    for name in ("startScan", "startReanalyze", "pollStatus"):
        assert "startScanPoll()" in bodies[name], f"{name} no longer arms the poll via the helper"
    assert "stopScanPoll()" in bodies["pollScan"], (
        "pollScan's completion branch no longer stops the poll via the helper"
    )


def test_situations_pending_branch_still_rearms_both_buttons():
    """The branch that makes a second start reachable is deliberate, not the bug.

    A scan may be started while situation synthesis finishes on the GPU, so the
    fix must not be "stop offering the buttons".
    """
    body = _function_bodies(_source())["pollScan"]
    branch = body[body.index("sitsPending > 0") : body.index("// Fully done")]
    assert "$('scanBtn').onclick          = startScan;" in branch
    assert "$('reanalyzeBtn').onclick     = startReanalyze;" in branch


def test_poll_status_keeps_its_already_polling_guard():
    assert "if (scanTimer) return;" in _function_bodies(_source())["pollStatus"]
