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

import subprocess
import sys



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


def test_the_scoping_ssot_never_falls_back_to_the_project_root():
    """Round 25 re-anchor: the scoping moved, the requirement did not.

    Round 22 pinned this by asserting `active_test_dir` appeared in
    _advance_prechecks's own source. Round 25 relocated the pytest invocation
    into core.quality_gate.test_suite_run — one implementation shared by the
    four call sites that each used to build their own argv — so the string is
    no longer in that function and a source-text anchor would only be
    measuring where the code happens to live.

    What actually has to hold is that resolve_targets never yields a target
    that means "the whole project root". Two of the four old call sites had
    exactly that fallback (`test_target = "."` when the layout directory was
    absent), which is the same collection bug from the other direction.
    """
    from core.quality_gate.test_suite_run import resolve_targets

    import tempfile
    with tempfile.TemporaryDirectory() as empty:
        test_target, cov_target = resolve_targets(empty)
    assert test_target not in (".", ""), (
        f"resolve_targets fell back to the project root ({test_target!r}) for a "
        "project with no test directory — pytest would then collect "
        "harness/tests/ as well (see the RED repro above)"
    )
    assert cov_target not in (".", ""), (
        f"resolve_targets fell back to the project root ({cov_target!r}) for a "
        "project with no source directory — coverage then counts the harness's "
        "own files as project source (measured: 95.98% vs the project's 100%)"
    )


def test_advance_prechecks_delegates_its_suite_run_to_the_ssot():
    """_advance_prechecks must not hand-roll a pytest argv again."""
    # Round 81 站6: the precheck pipeline is `_advance_prechecks` plus the
    # `_precheck_*` helpers extracted from it. Reading only the caller now
    # answers a question this test never meant to ask.
    from tests.support.pipeline import pipeline_source
    src = pipeline_source("cli/phase_cmds.py", "_advance_prechecks",
                          helper_prefix="_precheck_")
    assert "run_suite" in src, (
        "_advance_prechecks must obtain its test/coverage measurement from "
        "core.quality_gate.test_suite_run.run_suite"
    )
    assert '"pytest"' not in src, (
        "a hand-rolled pytest argv is back in _advance_prechecks — that is how "
        "one advance-phase came to run the whole suite five times, each with "
        "its own idea of which directory holds the tests"
    )
