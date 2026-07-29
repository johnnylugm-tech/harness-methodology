"""Round 25 — one execution of the project's test suite, many judgements.

Measured on the run-all-by-workflow P1-P8 evidence run, a single
`advance-phase --completed 3` ran the whole suite FIVE times in one process,
seconds apart, with nothing changing in between: 55.4s of prechecks of which
53.7s was pytest. Across P1→P8 that was 18 executions and ~195s, while every
non-test check in advance-phase summed to about 2s.

The duplication was downstream of the real defect: four call sites each
hand-rolled the pytest argv, so "the project's tests" had three definitions and
"the project's source" two. These tests hold the consolidation in place —
the argv scan stops a fifth definition appearing, the execution count stops the
sharing quietly regressing, and the fingerprint tests pin the one thing the
sharing is allowed to be wrong about.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.quality_gate.test_suite_run import (
    reset_suite_cache,
    resolve_targets,
    run_suite,
    suite_timeout,
)

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent

# Every module reachable from _advance_prechecks that used to run the suite
# itself. Deliberately NOT the whole repo: stage_pass_generator, cross_artifact,
# confidence_scorer and auto_fix also invoke pytest, but they are not inside one
# advance-phase call, and folding them in would be a different change.
_ADVANCE_SUITE_CONSUMERS = (
    "cli/phase_cmds.py",
    "core/quality_gate/gate1_evidence.py",
    "core/quality_gate/phase_truth_verifier.py",
    "enforcement/framework_enforcer.py",
)


def _pytest_argv_sites(source: str) -> list[int]:
    """Line numbers of list/tuple literals that look like a pytest argv.

    Matches the shape every one of the four call sites used:
    ``[sys.executable, "-m", "pytest", ...]``. A string mentioning pytest in
    prose or a docstring is not a match — only a literal sequence element.
    """
    hits: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        literals = [
            e.value for e in node.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
        if "pytest" in literals and "-m" in literals:
            hits.append(node.lineno)
    return hits


def test_no_module_in_the_advance_path_builds_its_own_pytest_argv():
    """Zero allowlist. The fix is always the same one call: run_suite()."""
    offenders = []
    for rel in _ADVANCE_SUITE_CONSUMERS:
        src = (REPO / rel).read_text(encoding="utf-8")
        offenders += [f"{rel}:{line}" for line in _pytest_argv_sites(src)]
    assert not offenders, (
        "a hand-rolled pytest argv is back inside the advance-phase call "
        "graph — use core.quality_gate.test_suite_run.run_suite(). Five "
        "separate argvs is how one advance-phase came to run the same tests "
        "five times, each with its own idea of which directory holds them:\n  "
        + "\n  ".join(offenders)
    )


def test_the_scanner_would_see_a_reintroduced_argv():
    """Negative control — a scanner that matches nothing proves nothing."""
    reintroduced = (
        "import sys, subprocess\n"
        "def f(t):\n"
        "    return subprocess.run([sys.executable, '-m', 'pytest', t, '-q'])\n"
    )
    assert _pytest_argv_sites(reintroduced) == [3]


# ── one execution, five judgements ──────────────────────────────────────

class _Ledger:
    """How many times the suite actually executed, counted from outside.

    The project's own test appends a line to a file outside the project on
    every run, so the count is a black-box observation of real pytest
    executions — no patching of run_suite's internals, and no way for the
    count to agree with a broken implementation.
    """

    def __init__(self, path: Path):
        self.path = path

    @property
    def n(self) -> int:
        if not self.path.is_file():
            return 0
        return len([ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln])


@pytest.fixture
def measured_project(tmp_path):
    """A real, runnable one-test project that records each execution."""
    marker = tmp_path / "executions.log"
    project = tmp_path / "proj"
    (project / "03-development" / "src").mkdir(parents=True)
    (project / "03-development" / "tests").mkdir(parents=True)
    (project / ".methodology").mkdir()
    (project / "03-development" / "src" / "mod.py").write_text(
        "def value():\n    return 1\n", encoding="utf-8")
    # The marker append is the FIRST statement so it records the execution even
    # if a later edit in one of these tests breaks the import — the ledger must
    # count runs, not successes.
    _test_body = (
        f"open({str(marker)!r}, 'a').write('ran\\n')\n"
        "import sys\n"
        "sys.path.insert(0, '03-development/src')\n"
        "from mod import value\n"
        "def test_value():\n    assert value() == 1\n"
    )
    (project / "03-development" / "tests" / "test_mod.py").write_text(
        _test_body, encoding="utf-8")
    reset_suite_cache()
    yield project, _Ledger(marker), _test_body
    reset_suite_cache()


def test_all_five_consumers_share_one_execution(measured_project):
    """The whole point of the round, as one assertion.

    Before: gate1_evidence, FrameworkEnforcer, check_pytest, check_coverage and
    the _advance_prechecks TDD block each spawned pytest. The last one's bar
    (100% coverage, all green) logically implies the other four (all green,
    ≥70, ≥80), and nothing changes between them inside one process.
    """
    project, ledger, test_body = measured_project

    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
    from enforcement.framework_enforcer import FrameworkEnforcer

    assert validate_fr_coverage_immediate(project) == 100.0
    assert FrameworkEnforcer(str(project), phase=4).check_coverage_threshold()["passed"]
    verifier = PhaseTruthVerifier(str(project), 3)
    assert verifier.check_pytest()[0] is True
    assert verifier.check_coverage()[0] is True
    assert run_suite(project).passed is True

    assert ledger.n == 1, (
        f"the suite ran {ledger.n} times for five judgements — each "
        "consumer is commissioning its own measurement again"
    )


def test_force_bypasses_the_memo(measured_project):
    project, ledger, test_body = measured_project
    run_suite(project)
    run_suite(project, force=True)
    assert ledger.n == 2


# ── the fingerprint tripwire ────────────────────────────────────────────

def test_a_source_edit_between_consumers_forces_a_fresh_run(measured_project):
    """The memo's one obligation.

    The measurement is shared across a whole advance-phase, and advance-phase
    writes files while it runs (STAGE_PASS, TRACEABILITY_MATRIX). Those live
    outside src/ and tests/ and were verified not to move the result. A change
    that IS inside src/ or tests/ must not be served from the memo — otherwise
    "share one execution" becomes "believe a stale verdict".
    """
    project, ledger, test_body = measured_project
    run_suite(project)
    assert ledger.n == 1
    (project / "03-development" / "src" / "mod.py").write_text(
        "def value():\n    return 1  # edited\n", encoding="utf-8")
    run_suite(project)
    assert ledger.n == 2, "a source edit was served from the memo"


def test_a_test_edit_also_forces_a_fresh_run(measured_project):
    project, ledger, test_body = measured_project
    run_suite(project)
    (project / "03-development" / "tests" / "test_mod.py").write_text(
        test_body + "\ndef test_extra():\n    assert value() == 1\n",
        encoding="utf-8")
    run_suite(project)
    assert ledger.n == 2


def test_a_config_edit_also_forces_a_fresh_run(measured_project):
    """pyproject/.coveragerc/conftest change what the suite reports without any
    source or test file changing — the fingerprint covers them for that reason.
    """
    project, ledger, test_body = measured_project
    run_suite(project)
    (project / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts = '-q'\n", encoding="utf-8")
    run_suite(project)
    assert ledger.n == 2


def test_writing_an_unrelated_document_does_not_force_a_rerun(measured_project):
    """The counterpart: advance-phase's own artifact writes must not thrash the
    memo, or the sharing buys nothing. Verified against the real project too —
    STAGE_PASS + TRACEABILITY_MATRIX regeneration left (passed, coverage) and
    the pass/skip counts byte-identical.
    """
    project, ledger, test_body = measured_project
    run_suite(project)
    (project / "00-summary").mkdir()
    (project / "00-summary" / "Phase8_STAGE_PASS.md").write_text(
        "# regenerated\n", encoding="utf-8")
    run_suite(project)
    assert ledger.n == 1


# ── target resolution ───────────────────────────────────────────────────

def test_targets_come_from_project_layout(tmp_path):
    (tmp_path / "03-development" / "src").mkdir(parents=True)
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    assert resolve_targets(tmp_path) == (
        "03-development/tests", "03-development/src")


def test_an_explicit_coveragerc_source_wins(tmp_path):
    """A project that scoped its own coverage meant it."""
    (tmp_path / "03-development" / "src").mkdir(parents=True)
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    (tmp_path / ".coveragerc").write_text(
        "[run]\nsource =\n    03-development/src/pkg\n", encoding="utf-8")
    _, cov_target = resolve_targets(tmp_path)
    assert cov_target == "03-development/src/pkg"


def test_a_coveragerc_that_says_dot_does_not_win(tmp_path):
    """`source = .` is coverage's default, not a decision. Honouring it is how
    FrameworkEnforcer came to count the harness's own harness_cli.py shim and
    conftest.py as project source (95.98% against a project that is at 100%).
    """
    (tmp_path / "03-development" / "src").mkdir(parents=True)
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    (tmp_path / ".coveragerc").write_text("[run]\nsource = .\n", encoding="utf-8")
    _, cov_target = resolve_targets(tmp_path)
    assert cov_target == "03-development/src"


def test_the_reported_coverage_is_exact_not_a_truncated_integer(tmp_path):
    """Runs a real project whose coverage is provably fractional.

    coverage's `TOTAL … n%` terminal line truncates: 249/250 statements prints
    as `99%`. Station 0 measured this and disproved the risk originally claimed
    for it — truncation never inflates, and for the integer thresholds in use
    (70 / 80 / 100) `floor(x) >= T ⟺ x >= T`, so the verdicts agree. What it
    does cost is honesty in the diagnostic (85.0% standing in for 85.9%) and
    correctness against a fractional quality_manifest `min_coverage`. Reading
    coverage's JSON totals costs nothing and removes the whole question.

    An implementation that went back to the regex reports 99.0 here.
    """
    project = tmp_path / "proj"
    src = project / "03-development" / "src"
    tests = project / "03-development" / "tests"
    src.mkdir(parents=True)
    tests.mkdir(parents=True)
    body = ["def covered():"] + [f"    x{i} = {i}" for i in range(248)] + ["    return 0"]
    body += ["", "def never_called():", "    return 1"]
    (src / "mod.py").write_text("\n".join(body) + "\n", encoding="utf-8")
    (tests / "test_mod.py").write_text(
        "import sys\n"
        "sys.path.insert(0, '03-development/src')\n"
        "from mod import covered\n"
        "def test_c():\n    assert covered() == 0\n",
        encoding="utf-8",
    )
    reset_suite_cache()
    try:
        result = run_suite(project)
    finally:
        reset_suite_cache()
    assert result.ran and result.coverage is not None
    assert result.coverage != int(result.coverage), (
        f"coverage came back as the whole number {result.coverage} — that is "
        "the truncated terminal line, not coverage's own totals"
    )
    assert result.coverage == pytest.approx(99.6, abs=0.1)
    assert not (result.coverage >= 100.0), "99.6% must not clear a 100% bar"


def test_a_project_with_nothing_to_measure_reports_that(tmp_path):
    result = run_suite(tmp_path)
    assert result.ran is False
    assert result.coverage is None
    assert result.reason


# ── boundaries ──────────────────────────────────────────────────────────

def test_a_js_project_is_not_measured_here(tmp_path):
    """R25-DEFER-1: js/ts is out of scope this round, by 老闆's ruling.

    run_suite must decline rather than run pytest against a TypeScript tree.
    Declining also means every js/ts caller stays on the path it already had,
    so "not fixing js" cannot turn into "breaking js".
    """
    import json

    (tmp_path / "03-development" / "src").mkdir(parents=True)
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "state.json").write_text(
        json.dumps({"language": "typescript", "current_phase": 3}), encoding="utf-8")
    result = run_suite(tmp_path)
    assert result.ran is False
    assert "typescript" in result.reason


def test_timeout_precedence_is_configurable_with_a_floor(tmp_path):
    """Unifies four settings: configurable (300 default), gate1's hardcoded
    120, FrameworkEnforcer's hardcoded 300, and the TDD block's absence of any
    timeout. An unbounded suite in an unattended run is a stall with no upper
    bound, which is why one number is needed rather than none.
    """
    assert suite_timeout(tmp_path) >= 30
