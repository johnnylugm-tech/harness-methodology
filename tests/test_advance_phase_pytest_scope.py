"""Round 22 — _advance_prechecks's pytest-coverage check must scope
collection to the project's own test directory (ProjectLayout.
active_test_dir), not rely on bare `pytest` + cwd=project default
discovery.

Root cause: harness/ lives as a submodule INSIDE the project tree. Its own
pyproject.toml `testpaths = ["tests"]` only takes effect when pytest's
rootdir resolves to harness/ itself (i.e. invoked from harness/); invoked
from the project root with no explicit path, pytest's default recursive
discovery also collects harness/tests/*.py — including any harness
self-test with in-repo golden-fixture drift that is not part of the
project's own TDD evidence. Confirmed on taskq Phase 4 workflow
wf_8b3a3f79-12b: advance-phase's coverage check failed on a harness
golden-file self-test, not on project code.

Gate 1's own pytest-cov ToolSpec (harness/toolchains/registry.py) never
has this problem — its cmd is always `("pytest", "{test_target}", ...)`,
an explicit target. _advance_prechecks's bare call was the one exception;
this closes it using the same ProjectLayout.active_test_dir SSOT the
function already uses for active_src_dir two lines above.
"""

import inspect
import subprocess
import sys

from cli.phase_cmds import _advance_prechecks


def _make_project_with_harness_sibling(tmp_path, *, harness_test_passes: bool):
    """A minimal project: 03-development/{src,tests} with one passing test,
    plus a harness/tests/ sibling whose own test either passes or fails —
    reproducing the exact taskq shape (harness/ vendored inside the tree).
    """
    src_dir = tmp_path / "03-development" / "src" / "fakepkg"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")

    tests_dir = tmp_path / "03-development" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_x.py").write_text(
        "def test_value():\n    assert True\n", encoding="utf-8"
    )

    harness_tests_dir = tmp_path / "harness" / "tests"
    harness_tests_dir.mkdir(parents=True)
    body = "assert True\n" if harness_test_passes else "assert False\n"
    (harness_tests_dir / "test_harness_self.py").write_text(
        f"def test_harness_self():\n    {body}", encoding="utf-8"
    )
    return tmp_path


def test_bare_pytest_from_project_root_sweeps_in_harness_tests(tmp_path):
    """Proves the ROOT CAUSE mechanism is real: a bare `pytest` invocation
    (no explicit path — the pre-fix _advance_prechecks shape) run with
    cwd=project collects and fails on a harness/tests/ sibling's failure,
    even though that test has nothing to do with the project's own TDD
    coverage. This is the RED half of the repro.
    """
    project = _make_project_with_harness_sibling(tmp_path, harness_test_passes=False)
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "--tb=no", "-q"],
        cwd=str(project), capture_output=True, text=True,
    )
    assert r.returncode != 0, (
        "expected the bare/unscoped pytest call to fail on the unrelated "
        "harness/tests/test_harness_self.py — if this now passes, the "
        "reproduction no longer models the bug"
    )
    assert "test_harness_self" in r.stdout


def test_scoped_pytest_ignores_harness_tests_sibling(tmp_path):
    """The GREEN half: invoking pytest with an explicit project-test-dir
    path (what the fix makes _advance_prechecks do) collects only the
    project's own tests and is unaffected by the harness/tests/ failure.
    """
    project = _make_project_with_harness_sibling(tmp_path, harness_test_passes=False)
    test_dir = project / "03-development" / "tests"
    r = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_dir), "--tb=no", "-q"],
        cwd=str(project), capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout
    assert "test_harness_self" not in r.stdout


def test_advance_prechecks_pytest_call_binds_active_test_dir_ssot():
    """Delegation check: _advance_prechecks must build its pytest cmd from
    ProjectLayout.active_test_dir (the same SSOT active_src_dir already
    uses two lines above it), not a bare `pytest` call with no path.
    """
    src = inspect.getsource(_advance_prechecks)
    assert "active_test_dir" in src, (
        "_advance_prechecks must scope its pytest invocation via "
        "ProjectLayout.active_test_dir — a bare `pytest` call with no "
        "explicit path also collects harness/tests/ when harness/ is "
        "vendored inside the project tree (see the RED repro in "
        "test_bare_pytest_from_project_root_sweeps_in_harness_tests)."
    )
