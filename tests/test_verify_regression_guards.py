"""Tests for scripts/verify_regression_guards.py — the guard-test registry checker.

The registry (tests/REGRESSION_GUARDS.yaml) pins tests that guard previously
fixed bugs. History motivated this: the sqlite-swallow fix (ff98cc7) was
regressed AND its guard test deleted in the same span, with nothing catching
it — 667 test functions have been deleted across this repo's history with
zero detection. The verifier fails closed: a missing guard test, an unreadable
registry, or an empty registry all exit non-zero.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "verify_regression_guards.py"
REAL_REGISTRY = REPO_ROOT / "tests" / "REGRESSION_GUARDS.yaml"


def _run(registry: Path, repo_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--registry", str(registry),
         "--repo-root", str(repo_root)],
        capture_output=True, text=True, timeout=120,
    )


def _sandbox(tmp_path: Path, test_source: str) -> Path:
    """Create a minimal repo-like dir with one test file; return its root."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_guarded.py").write_text(
        textwrap.dedent(test_source), encoding="utf-8"
    )
    return tmp_path


class TestVerifierSandbox:
    def test_all_guards_present_exits_0(self, tmp_path):
        root = _sandbox(tmp_path, """
            def test_pinned_bug():
                assert True
        """)
        reg = tmp_path / "guards.yaml"
        reg.write_text(
            "- test: tests/test_guarded.py::test_pinned_bug\n"
            '  bug: "sample bug"\n',
            encoding="utf-8",
        )
        result = _run(reg, root)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_missing_guard_exits_1_and_names_bug(self, tmp_path):
        root = _sandbox(tmp_path, """
            def test_pinned_bug():
                assert True
        """)
        reg = tmp_path / "guards.yaml"
        reg.write_text(
            "- test: tests/test_guarded.py::test_deleted_guard\n"
            '  bug: "sqlite swallow regression marker"\n',
            encoding="utf-8",
        )
        result = _run(reg, root)
        assert result.returncode == 1
        # The operator must see WHICH bug just lost its guard.
        assert "sqlite swallow regression marker" in result.stdout
        assert "test_deleted_guard" in result.stdout

    def test_parametrized_guard_matches_bare_node_id(self, tmp_path):
        root = _sandbox(tmp_path, """
            import pytest

            @pytest.mark.parametrize("n", [1, 2])
            def test_param_guard(n):
                assert n
        """)
        reg = tmp_path / "guards.yaml"
        reg.write_text(
            "- test: tests/test_guarded.py::test_param_guard\n"
            '  bug: "parametrized guard"\n',
            encoding="utf-8",
        )
        result = _run(reg, root)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_unparseable_registry_exits_1(self, tmp_path):
        root = _sandbox(tmp_path, """
            def test_pinned_bug():
                assert True
        """)
        reg = tmp_path / "guards.yaml"
        reg.write_text("{ this is: not, valid yaml", encoding="utf-8")
        result = _run(reg, root)
        assert result.returncode == 1

    def test_empty_registry_exits_1(self, tmp_path):
        """An emptied registry must not pass vacuously — emptying it is how a
        deletion would dodge the check while looking like cleanup."""
        root = _sandbox(tmp_path, """
            def test_pinned_bug():
                assert True
        """)
        reg = tmp_path / "guards.yaml"
        reg.write_text("[]\n", encoding="utf-8")
        result = _run(reg, root)
        assert result.returncode == 1

    def test_missing_registry_file_exits_1(self, tmp_path):
        root = _sandbox(tmp_path, """
            def test_pinned_bug():
                assert True
        """)
        result = _run(tmp_path / "nope.yaml", root)
        assert result.returncode == 1

    def test_uncollectable_guard_file_exits_1(self, tmp_path):
        """A guard file that errors on import must fail closed, not report
        the guard as vacuously missing-or-fine."""
        root = _sandbox(tmp_path, """
            import module_that_does_not_exist_anywhere

            def test_pinned_bug():
                assert True
        """)
        reg = tmp_path / "guards.yaml"
        reg.write_text(
            "- test: tests/test_guarded.py::test_pinned_bug\n"
            '  bug: "import-broken guard file"\n',
            encoding="utf-8",
        )
        result = _run(reg, root)
        assert result.returncode == 1


class TestRealRegistry:
    """The suite guards itself: deleting a registered guard test makes this
    test fail even before the hook/CI wiring runs."""

    def test_real_registry_all_guards_collect(self):
        result = _run(REAL_REGISTRY, REPO_ROOT)
        assert result.returncode == 0, result.stdout + result.stderr
