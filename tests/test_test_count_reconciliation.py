"""The count in the artifact and the count the framework took (Round 55 站4).

`04-testing/TEST_RESULTS.md` is prose the agent writes. `run_suite` is the
measurement the framework takes, scoped by `resolve_targets` to the project's
own test directory. The two have never met.

Measured 2026-08-17 on taskq-super: the artifact's own source-of-truth block
reads `4 failed, 7563 passed, 3 skipped, 2 warnings in 281.16s`, and the same
document explains it — "4 failed (all `harness/tests/`)" and "plus the bulk of
the harness guard suite". The agent ran `pytest` from the repository root,
where the vendored copy of this framework lives. The framework's own
measurement of that tree is 349 tests. The 7,563 then travelled unchallenged
into `05-verification/BASELINE.md` and `VERIFICATION_REPORT.md`.

The P4 prompt said why, in `scripts/workflowgen/spec_phase4.py`: "Real
execution is enforced by advance-phase pytest --cov-fail-under=100, **not by
string-matching this doc**." The framework declared that it does not read the
number, and the number was written accordingly.

Four of the seven projects carry a summary line measured over a wider tree than
the one they deliver: taskq-super (7570), taskq-plus (6866 beside its own 441),
run-all-by-workflow (6256 beside its own 59), and taskq-advance, whose document
has no machine-readable summary line at all. taskq-api's single line (326) is
the one honest case.

Anchor: pytest's own terminal summary, identified by the mandatory `in <T>s`
suffix rather than by a prose table. A markdown row saying `| **Total** | 441 |`
is the agent's transcription; the summary line is the runner's output.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_RESULTS_INFLATED = """\
# Test Results — Phase 4

| Metric | Value |
| --- | --- |
| Working dir | `/Users/johnny/projects/taskq-super` |

Source of truth:

```
4 failed, 7563 passed, 3 skipped, 2 warnings in 281.16s (0:04:41)
```
"""

_RESULTS_HONEST = """\
# Test Results — Phase 4

```
321 passed, 5 skipped in 6.70s
```
"""

_RESULTS_NO_SUMMARY = """\
# Test Results — Phase 4

- **7139 passed** — the bulk of the suite.
- **1 failed** — see "Deferred / Failing Issues" below.
"""


class _Suite:
    """The shape `run_suite` returns, reduced to what this check reads."""

    def __init__(self, outcomes: int, skipped: int) -> None:
        self.ran = True
        self.test_outcomes = {f"t.py::t{i}": "passed" for i in range(outcomes)}
        self.skipped = skipped
        self.test_target = "03-development/tests"


def _project(tmp_path: Path, body: str) -> Path:
    (tmp_path / "04-testing").mkdir(parents=True)
    (tmp_path / "04-testing" / "TEST_RESULTS.md").write_text(body, encoding="utf-8")
    return tmp_path


@pytest.fixture
def measured(monkeypatch):
    """Pin the framework's own measurement; the real one executes pytest."""
    def _pin(outcomes: int, skipped: int = 0):
        from core.quality_gate import cross_artifact

        monkeypatch.setattr(
            cross_artifact, "_measured_suite",
            lambda _root: _Suite(outcomes, skipped), raising=False)
    return _pin


def test_a_wider_run_recorded_as_the_project_result_is_critical(tmp_path, measured):
    """7,570 in the document, 349 in the tree the framework measured."""
    from core.quality_gate.cross_artifact import check_test_count_reconciliation

    measured(349)
    v = check_test_count_reconciliation(_project(tmp_path, _RESULTS_INFLATED), 4)
    assert v, "the artifact claimed twenty times the tests the project has"
    assert all(x["severity"] == "CRITICAL" for x in v)
    issue = " ".join(x["issue"] for x in v)
    assert "7570" in issue and "349" in issue, (
        "both numbers belong in the message — a reader who sees only one "
        "cannot tell which tree was measured"
    )
    assert "03-development/tests" in issue, (
        "the real cause is almost always two different trees, so the message "
        "has to name the target the framework used"
    )


def test_a_matching_summary_is_silent(tmp_path, measured):
    """taskq-api's shape: one run, scoped to the project, and it agrees."""
    from core.quality_gate.cross_artifact import check_test_count_reconciliation

    measured(321, skipped=5)
    assert check_test_count_reconciliation(_project(tmp_path, _RESULTS_HONEST), 4) == []


def test_no_machine_readable_summary_is_also_a_violation(tmp_path, measured):
    """Abstaining is not passing (Round 46), applied to this check at birth.

    `check_coverage_report` next door returns no violations when the document
    carries no numeric claim. That is the shape this one deliberately does not
    copy: a test-results document with nothing to reconcile has not been
    reconciled.
    """
    from core.quality_gate.cross_artifact import check_test_count_reconciliation

    measured(349)
    v = check_test_count_reconciliation(_project(tmp_path, _RESULTS_NO_SUMMARY), 4)
    assert v, "a document with no summary line reported nothing to compare"
    issue = " ".join(x["issue"] for x in v)
    assert "349" in issue, "say what the framework measured, so the fix is one edit"


def test_the_check_runs_inside_the_phase_gate(tmp_path, measured):
    """`run_cross_artifact_checks` is the executor phase-truth already calls."""
    from core.quality_gate.cross_artifact import run_cross_artifact_checks

    measured(349)
    result = run_cross_artifact_checks(_project(tmp_path, _RESULTS_INFLATED), 4)
    assert result["critical_count"] >= 1
    assert result["passed"] is False
