"""A declared test is delivered when it ran and passed, not when it exists.

Round 87 站1. `spec_coverage` decided delivery by "a `def` with this name is
somewhere in the test tree". Nothing asked what it asserts, whether it ran, or
whether it passed. The `traceability` dimension score IS that ratio — taskq-cc's
71/118 = 60.16949152542372% is the number in its committed gate2_result.json to
the last digit — so the whole of what that dimension measured was a set of names.

Measured on three projects built from a byte-identical SPEC.md (f0e437b9…):

    taskq-cc       Gate 2 composite 92.66   traceability 60.17   47 undelivered
    taskq-cc-new   Gate 2 composite 91.59   traceability 61.54   40 undelivered
    taskq-redo     Gate 2 composite 98.04   traceability 100.0    0 undelivered

taskq-redo is the one whose rate limiter is a module-level dict where SPEC §5.2
requires a table, whose `downgrade()` is `pass`, and whose NFR suite asserts
`skip_count >= 0` and `import pytest_benchmark`. It scored highest. The 29
correctly-named stubs that took it from 55.38% (BLOCKED at 60) to 100% were
written in the Gate-2-exit commit itself, not in any FR's TDD loop.

The rule applied here is not new. `core.traceability.scanner` has stated and
implemented it since Round 73 — "only a mention inside a function whose own
outcome is `passed` does [count] (matching NFR-09's own rule: VERIFIED requires
the test to have 'actually ran and passed', not merely exist)" — and
`test_suite_run.run_suite` has produced the per-test map on every run since.
Both halves were computed every time. Nothing joined them.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.quality_gate.spec_coverage import delivery_outcome, spec_coverage_report

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent

_ACTUAL = {"test_passes", "test_skips", "test_fails", "test_uncollected"}
_OUTCOMES = {
    "03-development/tests/t.py::test_passes": "passed",
    "03-development/tests/t.py::test_skips": "skipped",
    "03-development/tests/t.py::Klass.test_fails": "failed",
}


@pytest.mark.parametrize("fn,expected", [
    ("test_passes", "delivered"),
    ("test_skips", "skipped"),
    ("test_fails", "failed"),
    ("test_uncollected", "not_collected"),
    ("test_never_written", "absent"),
])
def test_delivery_reads_the_runs_own_outcome(fn: str, expected: str) -> None:
    """The four ways a declared test can fail to verify anything, kept apart.

    Before this, all five collapsed to two: the name is there or it isn't.
    """
    assert delivery_outcome(fn, _ACTUAL, _OUTCOMES) == expected


def test_a_skipping_test_does_not_deliver_its_declaration() -> None:
    """The shape that shipped: `pytest.skip` when a tool is absent.

    taskq-redo's `test_every_dep_license_in_allowlist` skips when pip-licenses
    is not installed, and `test_integration_coverage_at_least_80_percent` skips
    when the coverage artifact is missing. Both counted as delivered evidence
    for their NFR, which is how NFR-07's allowlist grew to include GPL, LGPL
    and MPL with nothing red.
    """
    assert delivery_outcome("test_skips", _ACTUAL, _OUTCOMES) != "delivered"


def test_no_outcomes_preserves_presence_only() -> None:
    """`None` is "not measured", and must not be read as "not delivered".

    Same contract `scan_test_fr_coverage` documents: a non-Python project, or
    a caller with no live run, gets the pre-Round-87 answer rather than a
    report that every criterion lost its verifier.
    """
    for fn in ("test_skips", "test_fails", "test_uncollected"):
        assert delivery_outcome(fn, _ACTUAL, None) == "delivered"
    assert delivery_outcome("test_never_written", _ACTUAL, None) == "absent"


def test_report_says_why_each_declaration_is_undelivered(tmp_path: Path) -> None:
    """"nobody wrote it" and "it was written and skipped" are different repairs.

    The block message (站2) names the criterion and the reason; it cannot do
    that if the report throws the reason away, which is what the old
    `missing = [i for i in items if i["test_fn"] not in actual_fns]` did.
    """
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    # Two of the three declarations are written; one never was. Both written
    # ones exist as `def`s — the old rule could not tell them apart.
    (tests_dir / "t.py").write_text(
        "def test_passes():\n    assert True\n\n"
        "def test_skips():\n    import pytest; pytest.skip('tool absent')\n",
        encoding="utf-8",
    )
    items = [
        {"test_fn": "test_passes", "type": "unit", "derivation": "Q1", "fr_id": "FR-01"},
        {"test_fn": "test_skips", "type": "unit", "derivation": "Q2", "fr_id": "NFR-07"},
        {"test_fn": "test_never_written", "type": "unit", "derivation": "Q3", "fr_id": "NFR-01"},
    ]
    report = spec_coverage_report(tmp_path, test_outcomes=_OUTCOMES, _items=items)
    whys = {m["test_fn"]: m["why"] for m in report["missing"]}
    assert whys == {"test_skips": "skipped", "test_never_written": "absent"}, (
        "every undelivered row must carry the reason it is undelivered"
    )
    assert report["declared"] == 3
    assert report["implemented"] == 1, (
        "only test_passes delivered; test_skips exists as a `def` and is the "
        "whole point — under the presence-only rule it counted"
    )


def test_delivered_has_exactly_one_definition() -> None:
    """The gate score and the blocking check must not re-implement the rule.

    `check_ac_deferral_targets` (Round 83 站3) blocked on "no `def` of that
    name" while `spec_coverage` scored on the same condition — two statements
    of one rule, and a stub satisfied both. This pins that the blocking site
    IMPORTS the scorer's helper rather than deciding for itself: a rule with
    two implementations is a rule with two answers, which is the defect this
    round exists to repair.

    Structural, not a snapshot of the import line: it asks whether any name
    other than `delivery_outcome` in that function decides deliveredness.
    """
    src = (REPO / "core" / "quality_gate" / "artifact_consistency.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "check_ac_deferral_targets"),
        None,
    )
    assert fn is not None, "check_ac_deferral_targets is gone — was it renamed?"
    called = {
        n.func.id for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "delivery_outcome" in called, (
        "check_ac_deferral_targets no longer calls spec_coverage.delivery_outcome. "
        "It is the framework's blocking statement of 'this criterion has no "
        "verifier'; if it decides that for itself, it can disagree with the "
        "score computed from the same declarations."
    )
    body = ast.unparse(fn)
    assert "not in actual" not in body, (
        "check_ac_deferral_targets is testing set membership directly again. "
        "That is the presence-only rule delivery_outcome replaced."
    )
