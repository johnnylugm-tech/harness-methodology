"""Round 113 站F — a test may not launch the suite it is part of.

taskq-sol's TEST_SPEC declares `test_nfr09_full_pytest_suite_passes_with_zero_skips`
(runs `pytest 03-development/tests -q`, a directory containing the test) and
`test_nfr12_verify_system_exits_zero_with_pass_marker` (runs `make
verify-system`, whose own step 2 runs the full suite). Either one, run in the
suite, runs itself again. The corpus shows what agents invent to survive it,
and every invention is broken:

  taskq-advance  skips when PYTEST_CURRENT_TEST is set — which pytest sets for
                 every test, so the zero-skip test skips itself on every run
  taskq-renew    skips under --cov, which is how the harness runs the suite
  taskq-api      runs `make verify-system` from inside the suite

Both acceptance criteria already have an executor that is not a test: the
harness's own suite run feeds phase_truth_verifier's zero-skip check, and the
toolchain runs `make verify-system` at Gates 2-4. A suite-level criterion is
recorded as `Deferred: AC-… — <that executor>` and does not become a test.

The check is a read of the test source (Python AST), asked at every finalize:
a test that re-enters the suite breaks the harness's run for every FR, not just
its own. `--collect-only` does not execute tests and is not flagged (taskq-cc).
"""

from __future__ import annotations

from pathlib import Path

from core.quality_gate.self_invoking_tests import self_invoking_tests


def _project(tmp_path: Path, body: str, name: str = "test_nfr.py") -> Path:
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir(parents=True)
    (tests / name).write_text("import subprocess, sys\n" + body, encoding="utf-8")
    return tmp_path


def test_running_pytest_over_its_own_directory_is_named(tmp_path):
    proj = _project(tmp_path, (
        "def test_nfr09_zero_skips():\n"
        "    subprocess.run([sys.executable, '-m', 'pytest', '03-development/tests', '-q'])\n"))
    found = self_invoking_tests(proj)
    assert len(found) == 1 and "test_nfr09_zero_skips" in found[0]


def test_running_pytest_with_no_path_is_named(tmp_path):
    # No path: pytest collects from the rootdir, which includes this test.
    proj = _project(tmp_path, "def test_x():\n    subprocess.run(['pytest', '-q'])\n")
    assert len(self_invoking_tests(proj)) == 1


def test_running_the_verification_target_is_named(tmp_path):
    proj = _project(tmp_path, (
        "def test_nfr12_verify_system():\n"
        "    subprocess.run(['make', 'verify-system'], capture_output=True)\n"))
    found = self_invoking_tests(proj)
    assert len(found) == 1 and "verify-system" in found[0]


def test_collect_only_does_not_execute_and_is_not_named(tmp_path):
    proj = _project(tmp_path, (
        "def test_skip_count():\n"
        "    subprocess.run([sys.executable, '-m', 'pytest', '03-development/tests', '--collect-only', '-q'])\n"))
    assert self_invoking_tests(proj) == []


def test_running_pytest_on_a_fixture_elsewhere_is_not_named(tmp_path):
    proj = _project(tmp_path, (
        "def test_plugin():\n"
        "    subprocess.run([sys.executable, '-m', 'pytest', 'fixtures/sample_project'])\n"))
    assert self_invoking_tests(proj) == []


def test_the_finalize_gate_refuses_it(tmp_path):
    import argparse

    from cli.gate_cmds import _finalize_gate_fr_checks

    proj = _project(tmp_path, "def test_x():\n    subprocess.run(['make', 'verify-system'])\n")
    rc = _finalize_gate_fr_checks(argparse.Namespace(gate=2, fr_id=None), proj)
    assert rc not in (None, 0)
