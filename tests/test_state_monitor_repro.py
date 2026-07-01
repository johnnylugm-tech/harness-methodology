"""Regression tests for state_monitor.check_trends() — Bug M01/M02/M03.

M01 (line 55): silently swallows started_at parse error → elapsed stays 0,
   timeout-alert logic silently disabled.
M02 (line 76): default integrity_score=100 means missing field → no alert
   when actual integrity may be low (silently masks integrity failures).
M03 (line 87): silently swallows state.json write error → alerts may be
   sent but state update is lost without operator awareness.
"""

from __future__ import annotations

import importlib
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest


def _load_module():
    scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    return importlib.import_module("state_monitor")


@pytest.fixture
def module():
    return _load_module()


def _write_state(project: Path, *, started_at=None, integrity_score=None,
                 blocks=0, ab_rounds=0, current_phase="P1"):
    state = {
        "current_phase": current_phase,
        "phase_state": {},
    }
    ps = state["phase_state"]
    if started_at is not None:
        ps["started_at"] = started_at
    if integrity_score is not None:
        ps["integrity_score"] = integrity_score
    if blocks:
        ps["blocks"] = blocks
    if ab_rounds:
        ps["ab_rounds"] = ab_rounds
    methodology = project / ".methodology"
    methodology.mkdir(parents=True, exist_ok=True)
    (methodology / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return methodology / "state.json"


def _run_capture(module, project: Path):
    """Run check_trends and capture (stdout, stderr, returncode)."""
    out_buf, err_buf = io.StringIO(), io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        rc = module.check_trends(str(project))
    return rc, out_buf.getvalue(), err_buf.getvalue()


def _no_telegram(*args, **kwargs):  # noqa: ARG001
    """Drop-in replacement for send_telegram_alert that records nothing."""
    del args, kwargs
    return None


def _capture_alerts(monkeypatch, module):
    """Patch send_telegram_alert to return the list of alerts it received."""
    captured: list = []

    def _capture(_state, alerts):  # noqa: ARG001
        del _state
        captured.extend(alerts)

    monkeypatch.setattr(module, "send_telegram_alert", _capture)
    return captured


# ---------------------------------------------------------------------------
# Bug M01: started_at parse error must surface traceback, not silently disable
# ---------------------------------------------------------------------------

class TestM01StartedAtParseError:
    def test_invalid_started_at_logs_traceback_not_silent(
        self, module, tmp_path, monkeypatch
    ):
        """Bug M01 regression: when started_at is unparseable, the operator
        must see the traceback (or a clear reason) on stderr, not just
        a one-line 'Failed to parse' that hides the actual ValueError cause."""
        _write_state(tmp_path, started_at="not-a-date")
        # ensure no telegram is sent
        monkeypatch.setattr(module, "send_telegram_alert", _no_telegram)
        _, _, stderr = _run_capture(module, tmp_path)
        # The error message should mention a real cause (ValueError, Invalid isoformat)
        assert "Invalid" in stderr or "Traceback" in stderr, (
            f"M01: parse failure must surface traceback or cause, got stderr={stderr!r}"
        )
        # elapsed should be flagged, not silently 0
        assert "elapsed" in stderr.lower() or "Traceback" in stderr, (
            f"M01: should warn about elapsed calculation, got stderr={stderr!r}"
        )

    def test_invalid_started_at_returns_1(self, module, tmp_path, monkeypatch):
        """The function should return non-zero on parse failure so cron sees it."""
        _write_state(tmp_path, started_at="garbage")
        monkeypatch.setattr(module, "send_telegram_alert", _no_telegram)
        rc, _, _ = _run_capture(module, tmp_path)
        assert rc != 0, "M01: parse failure should return non-zero so cron knows"


# ---------------------------------------------------------------------------
# Bug M02: missing integrity_score must NOT silently default to 100
# ---------------------------------------------------------------------------

class TestM02MissingIntegrityScore:
    def test_missing_integrity_score_triggers_alert(
        self, module, tmp_path, monkeypatch
    ):
        """Bug M02 regression: when integrity_score is missing, monitor must
        treat it as unknown/alert-worthy, not as perfect 100. Otherwise
        silent failures in the integrity tracking pass undetected."""
        alerts = _capture_alerts(monkeypatch, module)
        _write_state(tmp_path)  # no integrity_score
        _run_capture(module, tmp_path)
        assert any(a["type"] == "INTEGRITY_LOW" for a in alerts), (
            f"M02: missing integrity_score must trigger INTEGRITY_LOW, "
            f"got types={[a['type'] for a in alerts]}"
        )

    def test_low_integrity_still_alerts(self, module, tmp_path, monkeypatch):
        """Sanity: low integrity_score still triggers alert."""
        alerts = _capture_alerts(monkeypatch, module)
        _write_state(tmp_path, integrity_score=10)
        rc, _, _ = _run_capture(module, tmp_path)
        assert rc == 1
        assert any(a["type"] == "INTEGRITY_LOW" for a in alerts), (
            f"INTEGRITY_LOW alert expected, got types={[a['type'] for a in alerts]}"
        )


# ---------------------------------------------------------------------------
# Bug M03: state.json write failure must surface traceback
# ---------------------------------------------------------------------------

class TestM03WriteError:
    def test_write_failure_returns_nonzero(self, module, tmp_path, monkeypatch):
        """Bug M03 regression: when state.json write fails (e.g. permission),
        the function must return non-zero so cron knows state was not
        updated. Silent return hides the loss of state updates."""
        state_path = _write_state(tmp_path)

        # force write_text to raise
        def _boom(*args, **kwargs):  # noqa: ARG001
            del args, kwargs
            raise OSError("disk full")
        monkeypatch.setattr(state_path.__class__, "write_text", _boom)
        monkeypatch.setattr(module, "send_telegram_alert", _no_telegram)

        rc, _, stderr = _run_capture(module, tmp_path)
        assert rc != 0, (
            f"M03: write failure must return non-zero, got rc={rc} "
            f"stderr={stderr!r}"
        )
        # traceback or disk full must appear
        assert "Traceback" in stderr or "disk full" in stderr, (
            f"M03: write failure must surface cause, stderr={stderr!r}"
        )
