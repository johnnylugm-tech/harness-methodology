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


class TestRegistryCompleteness:
    """Round 33 站0/站5 — the registry has only ever been checked in one
    direction.

    `main()` asks "is every registered guard still present?". Nothing asks
    "was the guard for this fix registered at all?", so the registry grows
    only when someone remembers. Measured on 8637c6a..4bdc0fb: three bug-fix
    commits added twelve-plus test functions across three new files
    (test_phase2_template_h1_drift.py, test_unresolvable_citations_annotation.py,
    and the two drift guards inside test_sab_parser.py / test_workflowgen.py),
    and the registry count did not move off 239.

    The preflight and postflight registries both carry a completeness
    meta-test for exactly this reason (Round 15 站A, Station E). This one did
    not.
    """

    def test_added_test_files_without_a_registry_entry_are_reported(self):
        """The signal only exists at commit/push time — which file is NEW — so
        the check takes the added paths as input rather than scanning the tree
        (6665 tests exist; almost none of them are guards, and demanding an
        entry for each would be a machine that cries wolf)."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("_vrg", SCRIPT)
        assert spec and spec.loader
        vrg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vrg)

        entries = [{"test": "tests/test_registered.py::test_a", "bug": "x"}]
        unregistered = vrg.unregistered_test_files(
            entries, ["tests/test_registered.py", "tests/test_brand_new.py"]
        )
        assert unregistered == ["tests/test_brand_new.py"], (
            "a new test file with no registry entry was not reported; nothing "
            "in the tooling asks whether a fix brought its guard with it "
            f"(got {unregistered})"
        )

    def test_a_non_test_path_is_not_demanded_to_be_a_guard(self):
        """Discriminating half: the check keys on new *test files*, not on
        every added path, or every commit that touches a conftest or a fixture
        directory gets blocked."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("_vrg", SCRIPT)
        assert spec and spec.loader
        vrg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vrg)

        assert vrg.unregistered_test_files([], ["core/quality_gate/thing.py"]) == []
        assert vrg.unregistered_test_files([], ["tests/conftest.py"]) == []
