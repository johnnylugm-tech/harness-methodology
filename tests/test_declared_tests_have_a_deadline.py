"""Round 114 站5 — a declared test no FR owns gets an owner and a deadline.

Measured with today's parser on the eight corpus projects that carry any
undelivered declared test: 89 of them, and every one is a NON-FR row — an
NFR section, the "Deferred to Downstream Phases" table, a smoke row. FR rows:
zero. FR tests have an owner (the P3 per-FR TDD loop) and a judge (Gate 1's
FR-scoped spec coverage); nothing writes the rest, and nothing asks for them
at any moment: spec coverage is a percentage over thresholds 60/80/90, so

  taskq-super   123 declared,  87 written, 36 missing (the whole deferred NFR table)
  taskq-new     116 declared,  91 written, 25 missing
  taskq-renew    89 declared,  81 written,  8 missing
  taskq-open    143 declared, 139 written,  4 missing

all reached Phase 8. Round 83 joined the `Deferred: AC — test_x` LINE form to
test existence; the table form, used by 15 of 17 projects, was never joined.

The deadline is the end of Phase 4, the testing phase (老闆裁定): P4 gets a
step that writes them before coverage and Gate 3 are measured, and
`advance-phase --completed 4` refuses while any remains. "Delivered" is
`delivery_outcome`, the one definition spec coverage and Round 83 use.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from core.quality_gate.spec_coverage import undelivered_declared_tests

REPO = Path(__file__).resolve().parents[1]

_TEST_SPEC = """\
# TEST_SPEC.md

### FR-01: Submit

| # | Test Function | Type | Derivation |
|---|---|---|---|
| 1 | `test_fr01_submit_returns_id` | happy | Q1 |
| 2 | `test_fr01_submit_rejects_empty` | validation | Q2 |

### NFR-01: Performance

| # | Test Function | Type | Derivation |
|---|---|---|---|
| 1 | `test_nfr01_submit_p95_under_50ms` | nfr_pattern | NP-01 |

## Deferred to Downstream Phases

| # | NFR | Test Function | Layer | Inputs | Title |
|---|---|---|---|---|---|
| 1 | NFR-03 | `test_nfr03_request_txn_commit_rollback` | unit | open_txn="0" | rollback (AC-N3.1) |
"""


def _project(tmp_path: Path, written: "tuple[str, ...]") -> Path:
    (tmp_path / "02-architecture").mkdir(parents=True)
    (tmp_path / "02-architecture" / "TEST_SPEC.md").write_text(_TEST_SPEC, encoding="utf-8")
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir(parents=True)
    body = "".join(f"def {name}():\n    assert True\n\n\n" for name in written)
    (tests / "test_all.py").write_text(body, encoding="utf-8")
    return tmp_path


_FILE = "03-development/tests/test_all.py"


def _passed(*names: str) -> dict:
    """`run_suite`'s outcome map, keyed `<rel>::<name>` as JUnit gives it."""
    return {f"{_FILE}::{n}": "passed" for n in names}


def test_only_non_fr_rows_are_listed(tmp_path):
    proj = _project(tmp_path, ("test_fr01_submit_returns_id",))
    found = undelivered_declared_tests(proj, non_fr=True, test_outcomes=_passed("test_fr01_submit_returns_id"))
    assert sorted(m["test_fn"] for m in found) == [
        "test_nfr01_submit_p95_under_50ms", "test_nfr03_request_txn_commit_rollback"]


def test_a_test_that_did_not_pass_is_not_delivered(tmp_path):
    names = ("test_nfr01_submit_p95_under_50ms", "test_nfr03_request_txn_commit_rollback")
    proj = _project(tmp_path, names)
    outcomes = {f"{_FILE}::test_nfr01_submit_p95_under_50ms": "passed",
                f"{_FILE}::test_nfr03_request_txn_commit_rollback": "skipped"}
    found = undelivered_declared_tests(proj, non_fr=True, test_outcomes=outcomes)
    assert [m["test_fn"] for m in found] == ["test_nfr03_request_txn_commit_rollback"]


def test_the_cli_exits_one_and_names_them(tmp_path):
    proj = _project(tmp_path, ())
    out = subprocess.run(
        [sys.executable, str(REPO / "harness_cli.py"), "undelivered-tests",
         "--project", str(proj), "--non-fr"],
        capture_output=True, text=True, cwd=str(REPO))
    assert out.returncode == 1, out.stdout + out.stderr
    assert "test_nfr03_request_txn_commit_rollback" in out.stdout
    assert "test_fr01_submit_returns_id" not in out.stdout


def test_the_p4_exit_refuses_while_any_remains(tmp_path):
    from cli import advance_prechecks
    from cli.exit_codes import EX_ADVANCE_DECLARED_TESTS_UNDELIVERED

    proj = _project(tmp_path, ("test_fr01_submit_returns_id",))
    outcomes = _passed("test_fr01_submit_returns_id")
    check = advance_prechecks._precheck_declared_tests_delivered
    assert check(4, proj, test_outcomes=outcomes) == EX_ADVANCE_DECLARED_TESTS_UNDELIVERED
    assert check(3, proj, test_outcomes=outcomes) is None
    names = _passed("test_fr01_submit_returns_id", "test_nfr01_submit_p95_under_50ms",
                    "test_nfr03_request_txn_commit_rollback")
    proj2 = _project(tmp_path / "all", ("test_fr01_submit_returns_id",
                                         "test_nfr01_submit_p95_under_50ms",
                                         "test_nfr03_request_txn_commit_rollback"))
    assert check(4, proj2, test_outcomes=names) is None


def test_the_p4_workflow_writes_them_before_gate_3():
    from scripts.workflowgen.spec_phase4 import generate_phase4

    js = generate_phase4()
    step = js.index("undelivered-tests --project")
    assert step < js.index("phase('Gate 3')"), "the tests must exist before Gate 3 measures them"


def test_p1_does_not_declare_suite_level_criteria_as_tests():
    from scripts.workflowgen.spec_phase1 import generate_phase1

    js = generate_phase1()
    p1 = js[js.index("Sub-Task 3/3 — TEST_INVENTORY.yaml"):]
    assert "make verify-system" in p1 and "do NOT give it a test function" in p1


def test_advance_phase_asks_the_deadline():
    """The check is called from the pipeline advance-phase runs, not only defined."""
    from tests.support.pipeline import pipeline_source

    src = pipeline_source("cli/phase_cmds.py", "_advance_prechecks", helper_prefix="_precheck_")
    assert "_precheck_declared_tests_delivered(completed_phase, project)" in src
