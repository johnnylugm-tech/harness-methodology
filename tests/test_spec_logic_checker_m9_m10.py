"""Regression tests for spec_logic_checker.Scan — Bugs M09/M10.

M09 (line 78): "test" substring exclusion over-broadly rejects legitimate
   production files (latest_api.py, contest.py, attestation/, …).
   Should match path components (test/ or test_*.py), not substrings.
M10 (line 95): bare `except Exception: pass` per-file silently drops
   read errors (binary files, encoding errors). Should surface them
   so files_checked is accurate.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


def _load_module():
    scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    return importlib.import_module("spec_logic_checker")


@pytest.fixture
def module():
    return _load_module()


# ---------------------------------------------------------------------------
# Bug M09: substring "test" over-broadly excludes production files
# ---------------------------------------------------------------------------

class TestM09FileExclusion:
    def test_legit_production_files_are_not_excluded(
        self, module, tmp_path
    ):
        """Bug M09 regression: files whose name CONTAINS 'test' but are not
        test files (latest_api.py, contest.py, attestation/) must be scanned."""
        # create mixed files
        good_files = ["latest_api.py", "contest.py", "attestation.py"]
        for fname in good_files:
            (tmp_path / fname).write_text(
                'def foo():\n    return 1 + "."\n', encoding="utf-8"
            )
        # create a real test file that should be excluded
        (tmp_path / "test_foo.py").write_text(
            'def bar():\n    return 1 + "."\n', encoding="utf-8"
        )

        checker = module.SpecLogicChecker(str(tmp_path))
        result = checker.scan_python_files()

        # The 3 legit files should be scanned (issues found) and test_foo.py
        # should be excluded.
        # Count files scanned: result.files_checked
        assert result.files_checked == 3, (
            f"M09: should scan 3 legit files, scanned {result.files_checked}. "
            f"Files with 'test' substring were wrongly excluded."
        )

    def test_real_test_files_are_still_excluded(self, module, tmp_path):
        """Sanity: actual test files (test_*.py) are still excluded."""
        (tmp_path / "test_real.py").write_text("def x(): pass\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_inside.py").write_text(
            "def y(): pass\n", encoding="utf-8"
        )
        (tmp_path / "production.py").write_text("def z(): pass\n", encoding="utf-8")

        checker = module.SpecLogicChecker(str(tmp_path))
        result = checker.scan_python_files()
        assert result.files_checked == 1, (
            f"M09: only production.py should be scanned, got {result.files_checked}"
        )


# ---------------------------------------------------------------------------
# Bug M10: bare except swallows per-file read errors
# ---------------------------------------------------------------------------

class TestM10ReadErrorSurfaced:
    def test_unreadable_file_logs_issue_not_silent(
        self, module, tmp_path, monkeypatch
    ):
        """Bug M10 regression: when read_text fails for a file, the
        per-file read error must be recorded (e.g. as an issue or a
        counter), not silently swallowed."""
        (tmp_path / "good.py").write_text("def x(): pass\n", encoding="utf-8")
        (tmp_path / "bad.py").write_text("def y(): pass\n", encoding="utf-8")

        original_read_text = Path.read_text

        def selective_read(self, *a, **k):
            if self.name == "bad.py":
                raise OSError("permission denied")
            return original_read_text(self, *a, **k)

        monkeypatch.setattr(Path, "read_text", selective_read)

        checker = module.SpecLogicChecker(str(tmp_path))
        result = checker.scan_python_files()

        # Either the error is recorded as an issue, OR files_checked
        # reflects only the actually-scanned files (not the failed one).
        # Either way, the error must NOT be invisible.
        read_error_mentioned = any(
            "permission" in str(getattr(i, "description", "")).lower()
            or "bad.py" in str(getattr(i, "file_path", ""))
            for i in result.issues
        )
        # Verify that the result is consistent: either issues captured the
        # error, or files_checked was decremented to reflect the failure.
        assert read_error_mentioned or result.files_checked < 2, (
            f"M10: per-file read error must surface somewhere. "
            f"files_checked={result.files_checked} issues={result.issues}"
        )
