"""tests/test_scheduler.py — Unit tests for the auto-scan scheduler.

Checks that:
 - scheduler_update() arms timers for non-zero intervals
 - scheduler_update() disables timers when interval is 0
 - get_schedule_status() returns correct per-source data
 - Existing timers are cancelled when schedule is replaced
 - Scans are skipped when one is already running
"""
import time
from unittest.mock import MagicMock, patch

import orchestrator


def _reset():
    """Clear any lingering schedule state between tests."""
    orchestrator.scheduler_update({})


def test_scheduler_update_arms_timers():
    _reset()
    orchestrator.scheduler_update({"slack": 60, "github": 0})
    with orchestrator._schedule_lock:
        assert "slack" in orchestrator._schedule
        assert orchestrator._schedule["slack"]["interval_min"] == 60
        assert orchestrator._schedule["slack"]["timer"] is not None
        assert "github" in orchestrator._schedule
        assert orchestrator._schedule["github"]["timer"] is None
    _reset()


def test_scheduler_update_clears_old_timers():
    _reset()
    orchestrator.scheduler_update({"slack": 60})
    with orchestrator._schedule_lock:
        old_timer = orchestrator._schedule["slack"]["timer"]
    # Replace schedule
    orchestrator.scheduler_update({"slack": 30})
    assert old_timer.finished.is_set()   # Timer.cancel() sets finished
    _reset()


def test_get_schedule_status_empty():
    _reset()
    status = orchestrator.get_schedule_status()
    assert isinstance(status, dict)
    assert len(status) == 0


def test_get_schedule_status_has_next_run():
    _reset()
    orchestrator.scheduler_update({"github": 30})
    status = orchestrator.get_schedule_status()
    assert "github" in status
    assert status["github"]["interval_min"] == 30
    assert status["github"]["next_run"] is not None   # ISO string
    assert status["github"]["last_run"] is None
    _reset()


def test_zero_interval_has_no_next_run():
    _reset()
    orchestrator.scheduler_update({"jira": 0})
    status = orchestrator.get_schedule_status()
    assert "jira" in status
    assert status["jira"]["next_run"] is None
    _reset()


def test_fire_skips_when_scan_running(monkeypatch):
    """Auto-scan should not start a new scan if one is already in progress."""
    _reset()
    monkeypatch.setitem(orchestrator._scan_state, "running", True)
    called = []
    monkeypatch.setattr(orchestrator, "run_scan", lambda srcs: called.append(srcs))
    orchestrator.scheduler_update({"slack": 999})

    # Manually fire (bypass timer wait)
    with orchestrator._schedule_lock:
        orchestrator._schedule["slack"]["interval_min"] = 999
    orchestrator._fire_auto_scan("slack")

    assert called == [], "run_scan should not be called while scan is running"
    monkeypatch.setitem(orchestrator._scan_state, "running", False)
    _reset()


def test_fire_calls_run_scan_when_idle(monkeypatch):
    """Auto-scan should call run_scan when no scan is running."""
    _reset()
    monkeypatch.setitem(orchestrator._scan_state, "running", False)
    called = []
    monkeypatch.setattr(orchestrator, "run_scan", lambda srcs: called.append(srcs))
    with orchestrator._schedule_lock:
        orchestrator._schedule["github"] = {
            "interval_min": 60,
            "next_run": None,
            "last_run": None,
            "timer": None,
        }
    orchestrator._fire_auto_scan("github")
    assert ["github"] in called
    _reset()


def test_unknown_source_ignored():
    _reset()
    orchestrator.scheduler_update({"notasource": 60})
    with orchestrator._schedule_lock:
        assert "notasource" not in orchestrator._schedule
    _reset()
