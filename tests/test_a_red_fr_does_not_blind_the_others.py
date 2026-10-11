"""Round 118 站2 — one FR's TDD-RED must not leave every other FR unmeasured.

`fr_suite_verdict` names "an import of the not-yet-written module" as the
canonical shape of a TDD-RED state, and Round 77 scopes a Gate 1 verdict to
the FR's own tests so that another FR's red cannot block it. Both per-FR
measurements ran pytest in its default mode, where one collection error
interrupts the whole session: nothing runs, no coverage is written.

taskq-retry, 2026-10-11: FR-10's RED commit landed (`test_fr10.py` imports the
not-yet-written `taskq_api.api.error_handlers`), the next dispatch hit the
account's session limit, and the relaunched run reached FR-01 first. FR-01's
23 tests all pass, yet its S4 `pytest-cov` run exited 2 with no score
(`infra_fail`), `fr_suite_verdict("FR-01")` read RED so TDD-GREEN ran again
(an empty `feat(FR-01): GREEN`), and COVERAGE-FIX chased a LOW_COVERAGE the
framework could not measure. FR-02 followed the same path.

The fix tolerates collection errors in per-FR measurements only, and records
each collection error as an `error` outcome of the file it belongs to, so the
FR whose module is missing stays RED. Whole-suite measurements are unchanged:
for them an uncollectable test file is a red suite.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import harness_cli  # noqa: F401  entry-first load order
from cli.fr_cmds import _fr_step_already_done  # noqa: E402
from core.quality_gate import test_suite_run as suite_run  # noqa: E402

pytestmark = [pytest.mark.core]


_SRC = (
    '"""Widget implementation.\n\n[FR-01]\n"""\n\n\n'
    "def handle(value):\n"
    '    """Upper-case the value. [FR-01]"""\n'
    "    return value.upper()\n"
)
_FR01_PASSING = (
    "from widget import handle\n\n\n"
    "def test_fr01_handles_input():\n"
    "    assert handle('x') == 'X'\n"
)
# The canonical RED shape: the module under test does not exist yet.
_FR10_RED = (
    "from widget_errors import render  # noqa: F401  (FR-10, not written yet)\n\n\n"
    "def test_fr10_renders():\n"
    "    assert render() == 'problem'\n"
)


def _project(tmp_path: Path, *, commits: "list[str]") -> Path:
    src = tmp_path / "03-development" / "src"
    tests = tmp_path / "03-development" / "tests"
    src.mkdir(parents=True)
    tests.mkdir(parents=True)
    (tmp_path / ".methodology").mkdir()
    (src / "widget.py").write_text(_SRC, encoding="utf-8")
    (tests / "test_fr01.py").write_text(_FR01_PASSING, encoding="utf-8")
    (tests / "test_fr10.py").write_text(_FR10_RED, encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent / '03-development' / 'src'))\n",
        encoding="utf-8",
    )
    (tmp_path / ".methodology" / "state.json").write_text(
        json.dumps({"language": "python", "current_phase": 3}), encoding="utf-8")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": "/usr/bin:/bin:/usr/local/bin"}
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    for subject in commits:
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", subject],
                       cwd=tmp_path, check=True, env=env)
    return tmp_path


@pytest.fixture(autouse=True)
def _fresh_memo():
    suite_run._CACHE.clear()
    yield
    suite_run._CACHE.clear()


# ── per-FR verdicts ──────────────────────────────────────────────────────────


def test_another_frs_red_does_not_make_this_fr_red(tmp_path):
    project = _project(tmp_path, commits=["init"])
    assert suite_run.fr_suite_verdict(project, "FR-01") == suite_run.GREEN


def test_the_fr_whose_module_is_missing_stays_red(tmp_path):
    """Tolerating the collection error must not turn the canonical RED into UNKNOWN.

    `_fr_tests_say` reads UNKNOWN as "keep the commit evidence's answer", so an
    unattributed collection error would let FR-10's RED/GREEN steps be judged
    on commit messages alone.
    """
    project = _project(tmp_path, commits=["init"])
    assert suite_run.fr_suite_verdict(project, "FR-10") == suite_run.RED


def test_green_is_done_while_another_fr_sits_at_red(tmp_path):
    project = _project(tmp_path, commits=[
        "test(RED): failing test for FR-01", "feat(FR-01): GREEN",
        "test(RED): failing test for FR-10",
    ])
    assert _fr_step_already_done("TDD-GREEN", "FR-01", project, phase=3) is True, (
        "FR-01's own tests pass; another FR's import-error RED made its GREEN "
        "step look unfinished and re-dispatched it"
    )


def test_per_fr_coverage_is_measured_while_another_fr_sits_at_red(tmp_path):
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate

    project = _project(tmp_path, commits=["init"])
    assert validate_fr_coverage_immediate(project, fr_id="FR-01") is not None


# ── S4 (Gate 1 test_coverage) ────────────────────────────────────────────────


def test_gate1_s4_scores_coverage_while_another_fr_sits_at_red(tmp_path):
    from core.quality_gate.fr_test_scope import scoped_test_failures
    from harness.tool_runners import compute_tool_score, run_tool

    project = _project(tmp_path, commits=["init"])
    extra = suite_run.per_fr_pytest_args(1, "pytest-cov")
    output, rc = run_tool("pytest-cov", str(project), extra_args=extra)
    assert compute_tool_score("pytest-cov", output, rc) is not None
    run = ("pytest-cov", output, rc)
    assert scoped_test_failures("FR-01", run)[0] == []
    assert scoped_test_failures("FR-10", run)[0] == ["03-development/tests/test_fr10.py"]


def test_only_a_per_fr_gate_tolerates_collection_errors():
    assert suite_run.per_fr_pytest_args(1, "pytest-cov") == (
        "--continue-on-collection-errors",)
    for gate in (2, 3, 4):
        assert suite_run.per_fr_pytest_args(gate, "pytest-cov") == ()
    assert suite_run.per_fr_pytest_args(1, "ruff") == ()


def test_the_s4_call_site_asks_for_the_per_fr_args() -> None:
    import ast

    from tests.support.pipeline import inlined

    fn = inlined("harness/harness_bridge.py", "_run_harness_cross_validation",
                 helper_prefix="_s4_")
    called = {
        n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))
    }
    assert "per_fr_pytest_args" in called


# ── whole-suite measurements are unchanged ───────────────────────────────────


def test_the_whole_suite_run_still_reads_an_uncollectable_file_as_red(tmp_path):
    project = _project(tmp_path, commits=["init"])
    result = suite_run.run_suite(project)
    assert result.ran and result.returncode == 2 and not result.passed
    assert result.test_outcomes == {}


def test_a_whole_suite_gate_still_gets_no_score(tmp_path):
    from harness.tool_runners import compute_tool_score, run_tool

    project = _project(tmp_path, commits=["init"])
    output, rc = run_tool("pytest-cov", str(project),
                          extra_args=suite_run.per_fr_pytest_args(2, "pytest-cov"))
    assert rc == 2 and compute_tool_score("pytest-cov", output, rc) is None
