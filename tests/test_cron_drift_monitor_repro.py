"""Regression tests for cron_drift_monitor.main() — Bugs M07/M08.

M07 (line 80-91): main try/except prints '[ERROR] {e}' to sys.stderr while
   stdout is redirected to log file. stderr is NOT redirected → error
   only visible in console, never in the log file.
M08 (line 80-91): same — no traceback captured, just str(e), so root cause
   analysis is impossible.
"""

from __future__ import annotations

import importlib
import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest


def _load_module():
    scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    return importlib.import_module("cron_drift_monitor")


@pytest.fixture
def module():
    return _load_module()


def _run_capture(module, monkeypatch, project_path: str | None = None):
    """Run main() and capture (rc, stdout, stderr, log_file_content).

    The script writes to logs/drift_monitor.log relative to its own
    installation directory (Path(__file__).parent.parent), NOT to
    DRIFT_PROJECT_PATH. So we read that fixed location and trim to the
    portion that was added during the call.
    """
    if project_path is None:
        project_path = str(Path(__file__).resolve().parents[1])

    monkeypatch.setenv("DRIFT_PROJECT_PATH", project_path)

    script_log = (
        Path(__file__).resolve().parents[1] / "logs" / "drift_monitor.log"
    )

    # Read existing content to isolate the delta after the call
    before = (
        script_log.read_text(encoding="utf-8") if script_log.exists() else ""
    )

    out_buf, err_buf = io.StringIO(), io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        try:
            rc = module.main()
        except SystemExit as e:
            rc = e.code

    after = script_log.read_text(encoding="utf-8") if script_log.exists() else ""
    delta = after[len(before):]
    return rc, out_buf.getvalue(), err_buf.getvalue(), delta


# ---------------------------------------------------------------------------
# Bug M07/M08: monitor exception must be captured in log file with traceback
# ---------------------------------------------------------------------------

class TestMonitorExceptionCaptured:
    def test_exception_appears_in_log_file(
        self, module, tmp_path, monkeypatch
    ):
        """Bug M07 regression: when monitor raises, the error message AND
        traceback must be in the log file (logs/drift_monitor.log), not
        just on stderr/console. Currently stderr bypasses the redirect."""
        # Make _run_monitor raise
        def _boom(_p):  # noqa: ARG001
            raise RuntimeError("simulated drift detector failure")
        monkeypatch.setattr(module, "_run_monitor", _boom)

        rc, _, _, log_text = _run_capture(
            module, monkeypatch, project_path=str(tmp_path),
        )
        assert rc == 1, f"M07: should return 1 on failure, got {rc}"
        assert "simulated drift detector failure" in log_text, (
            f"M07: error message must be in log file, "
            f"log_text={log_text!r}"
        )
        assert "Traceback" in log_text, (
            f"M08: traceback must be in log file, log_text={log_text!r}"
        )

    def test_log_file_gets_clean_state_on_success(self, module):
        """Sanity: when monitor runs without exception, the second call's
        log delta should NOT contain a Traceback — only the success line."""
        # snapshot after the first test's call
        script_log = (
            Path(__file__).resolve().parents[1] / "logs" / "drift_monitor.log"
        )
        before = script_log.read_text(encoding="utf-8") if script_log.exists() else ""

        out_buf, err_buf = io.StringIO(), io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            try:
                module.main()
            except SystemExit:
                pass

        after = script_log.read_text(encoding="utf-8") if script_log.exists() else ""
        delta = after[len(before):]
        assert "Traceback" not in delta, (
            f"unexpected traceback in success log: {delta!r}"
        )
