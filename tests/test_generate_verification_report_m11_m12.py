"""Regression tests for generate_verification_report — Bugs M11/M12.

M11 (line 60): _extract_acceptance_criteria regex requires trailing colon
   after FR header and AC line. SRS variants like '### FR-01 —' (em-dash)
   or '### FR-01 ' (space) are silently skipped.
M12 (line 250): main() catches Exception but only prints str(exc) — no
   traceback. Comment claims 'surface error rather than silent fail'
   but the surface is incomplete.
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
    return importlib.import_module("generate_verification_report")


@pytest.fixture
def module():
    return _load_module()


# ---------------------------------------------------------------------------
# Bug M11: regex too strict, must accept em-dash / space / other delimiters
# ---------------------------------------------------------------------------

class TestM11ExtractAcceptanceCriteria:
    def test_em_dash_header_is_accepted(self, module, tmp_path):
        """Bug M11 regression: '### FR-01 — ...' (em-dash) header must be
        recognized as a new FR section."""
        srs = tmp_path / "SRS.md"
        srs.write_text(
            "# SRS\n"
            "### FR-01 — Description\n"
            "AC-FR-01-1: must do X\n",
            encoding="utf-8",
        )
        acs = module._extract_acceptance_criteria(srs)
        assert "FR-01" in acs, f"M11: em-dash header skipped, got {list(acs)}"
        assert any("AC-FR-01-1" in line for line in acs["FR-01"])

    def test_space_header_is_accepted(self, module, tmp_path):
        """Bug M11 regression: '### FR-01 ' (space only, no colon/dash)
        header must still be recognized."""
        srs = tmp_path / "SRS.md"
        srs.write_text(
            "# SRS\n"
            "### FR-02 Description text\n"
            "AC-FR-02-1: must do Y\n",
            encoding="utf-8",
        )
        acs = module._extract_acceptance_criteria(srs)
        assert "FR-02" in acs, f"M11: space header skipped, got {list(acs)}"

    def test_colon_header_still_works(self, module, tmp_path):
        """Sanity: traditional '### FR-03: foo' still works."""
        srs = tmp_path / "SRS.md"
        srs.write_text(
            "# SRS\n"
            "### FR-03: Classic colon\n"
            "AC-FR-03-1: must do Z\n",
            encoding="utf-8",
        )
        acs = module._extract_acceptance_criteria(srs)
        assert "FR-03" in acs


# ---------------------------------------------------------------------------
# Bug M12: main() must print traceback on failure
# ---------------------------------------------------------------------------

class TestM12MainExceptionHasTraceback:
    def test_failure_prints_traceback(self, module, tmp_path, monkeypatch):
        """Bug M12 regression: when generate_verification_report raises,
        main() must print a full traceback to stderr, not just str(exc)."""
        def _boom(_p):  # noqa: ARG001
            raise RuntimeError("simulated generator failure")
        monkeypatch.setattr(module, "generate_verification_report", _boom)

        # Clear sys.argv to avoid pytest args leaking into argparse
        monkeypatch.setattr(sys, "argv", ["generate_verification_report", "--project", str(tmp_path)])

        out_buf, err_buf = io.StringIO(), io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            try:
                rc = module.main()
            except SystemExit as e:
                rc = e.code
        err_text = err_buf.getvalue()
        assert rc == 1, f"M12: should return 1 on failure, got {rc}"
        assert "Traceback" in err_text, (
            f"M12: stderr must contain traceback, got {err_text!r}"
        )
        assert "simulated generator failure" in err_text, (
            f"M12: stderr must contain the error message, got {err_text!r}"
        )
