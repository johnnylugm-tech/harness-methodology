"""Round 109 站6 — the framework filled in the project's answer.

`min_coverage_floor` returned `DEFAULT_MIN_COVERAGE = 80.0` when a project
declared no `quality_targets.min_coverage`. That is the framework answering a
question only the project can answer, and then every downstream check judging
the project against the framework's answer as if the project had given it.

The ledger's reason for leaving it (docs/PROPOSAL_ADJUDICATIONS.md:9349) was
that returning `None` "would block every project that does not write this key".
Measured 2026-09-08 over the corpus:

    declared  14 / 14      (80 ×3, 100 ×11)
    absent     0
    malformed  0

Zero projects would be blocked. The stated cost of the fix does not exist, and
the default has been standing in for a declaration nobody was ever asked to
make. Same shape as Round 108 站A's `state["integrity"]`, one layer along: a
value the framework supplies, read back as if it were a measurement.

WHAT IS DELIBERATELY *NOT* SYMMETRIC

An absent key and a malformed one are different facts and keep different
answers. Absent means nobody declared a floor — there is no number, and
`None` says so. Malformed means somebody declared one and it cannot be read;
that path still falls back to 80.0, unchanged, because the function's own
docstring gives the reason (a typo in quality_targets should not crash a
gate) and because the corpus has zero instances, so changing it would be a
decision taken with no measurement behind it. Recorded in the ledger with a
re-open condition rather than quietly made uniform.

THE FOUR CALL SITES

  cli/advance_checks.py      the live per-FR Gate 1 coverage check
  cli/fr_cmds.py             COVERAGE-FIX's false-positive test
  cli/gate_cmds.py           `int(...)` — a TypeError waiting for the first
                             project that declared nothing
  scripts/phase8_doc_gen.py  a release-doc template field

`cli/fr_cmds.py`'s site has no behavioural driver here and this file does not
pretend otherwise: the branch is inside `cmd_run_fr_step`, which no test in
this suite drives (`tests/test_coverage_fix_fallback.py` re-implements the
stamp logic rather than calling the command, and
`tests/test_fr_step_no_progress_self_doubt.py` reaches only an extracted
helper). What holds there is structural — the comparison is guarded by
`_cov_min is not None`, so an undeclared floor cannot be read as the agent
having been wrong — plus a ledger row naming the missing key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.quality_gate import DEFAULT_MIN_COVERAGE, CoverageChecker, min_coverage_floor

pytestmark = [pytest.mark.core]


def _degradations(project: Path) -> list[dict]:
    path = project / ".methodology" / "degradations.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── the reader ─────────────────────────────────────────────────────────────

def test_an_undeclared_floor_is_not_a_number() -> None:
    """The defect, stated as who owns the answer."""
    assert min_coverage_floor({"fr_ids": ["FR-01"]}) is None, (
        "a project that declared no coverage floor was handed the framework's "
        "own 80.0, and every check downstream then judged it against a number "
        "nobody in that project ever wrote")
    assert min_coverage_floor(None) is None
    assert min_coverage_floor({"quality_targets": {}}) is None


def test_a_declared_floor_is_returned_unchanged() -> None:
    """Control: the fix must not touch the fourteen projects that DO declare."""
    assert min_coverage_floor({"quality_targets": {"min_coverage": 100}}) == 100.0
    assert min_coverage_floor({"quality_targets": {"min_coverage": "80"}}) == 80.0


def test_a_malformed_floor_still_falls_back() -> None:
    """The asymmetry, pinned so it is a decision and not a leftover.

    Somebody declared something unreadable; that is a different fact from
    nobody declaring anything, and the function's contract (a typo must not
    crash a gate) is unchanged. Zero corpus instances, so making this uniform
    would be a change with no measurement behind it.
    """
    assert min_coverage_floor(
        {"quality_targets": {"min_coverage": "eighty"}}) == DEFAULT_MIN_COVERAGE
    assert min_coverage_floor(
        {"quality_targets": {"min_coverage": None}}) == DEFAULT_MIN_COVERAGE


def test_the_checker_class_does_not_keep_a_second_copy() -> None:
    """`CoverageChecker.DEFAULT_MIN_COVERAGE` was a second 80.0 in the same
    file whose first comment block exists to stop this key having four
    readings. One constant, read by both.

    THE RUNTIME CHECK ALONE IS VACUOUS AND IS KEPT ANYWAY. `is` passed on the
    unfixed code: CPython folds equal float constants within a module, so two
    separately typed `80.0`s were already the same object, and the assertion
    below could not tell a copy from a reference. It is kept because it is
    what catches the two DIVERGING in value later, which is the failure this
    is really about. What proves the class body no longer re-declares the
    number is the source assertion beside it — narrow on purpose: one name,
    one file, an exact-match line.
    """
    assert CoverageChecker.DEFAULT_MIN_COVERAGE == DEFAULT_MIN_COVERAGE

    source = (Path(__file__).resolve().parents[1]
              / "core" / "quality_gate" / "__init__.py").read_text(encoding="utf-8")
    assert "    DEFAULT_MIN_COVERAGE = DEFAULT_MIN_COVERAGE\n" in source, (
        "CoverageChecker no longer reads the module constant. A literal in "
        "the class body is the fifth reading of a key whose comment block "
        "exists because it already had four")


# ── the call sites ─────────────────────────────────────────────────────────

def _project_with_one_fr(tmp_path: Path, targets: "dict | None") -> Path:
    meth = tmp_path / ".methodology"
    meth.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"fr_ids": ["FR-01"],
                      "fr_module_traceability": {"FR-01": ["src/a.py"]}}
    if targets is not None:
        manifest["quality_targets"] = targets
    (meth / "quality_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    (meth / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": 3}), encoding="utf-8")
    return tmp_path


def test_the_advance_check_refuses_to_pass_on_a_floor_it_does_not_have(
    monkeypatch, tmp_path,
):
    """Round 32/35: could-not-measure is not a pass.

    `_check_gate1_live_coverage` returns 0 for "all FRs pass" and 14 for a
    real coverage failure. With no declared floor there is no comparison to
    make, and returning 0 would report an unmade judgement as a made one.

    The live measurement is stubbed at its PUBLIC name so the run reaches the
    comparison: an unstubbed tmp_path project has no tests, so pytest cannot
    run and the function blocks two branches earlier for an unrelated (and
    correct) reason — which is how the first version of this test passed
    `rc != 0` while never executing the code it was written for.
    """
    from core.quality_gate import gate1_evidence

    from cli.advance_checks import _check_gate1_live_coverage

    project = _project_with_one_fr(tmp_path, targets=None)
    monkeypatch.setattr(
        gate1_evidence, "validate_fr_coverage_immediate", lambda *a, **k: 95.0)
    rc = _check_gate1_live_coverage(project, completed_phase=3)

    assert rc != 0, (
        "an FR project with no declared coverage floor advanced as though its "
        "live Gate 1 coverage had been checked and passed")
    rows = [r for r in _degradations(project) if "min_coverage" in r.get("why", "")]
    assert rows, (
        f"the block named no remedy: {_degradations(project)}")
    assert rows[0]["owner"] == "project", (
        f"declaring a coverage floor is the project's to do: {rows[0]}")


def test_the_advance_check_does_not_block_a_project_that_declared_one(
    monkeypatch, tmp_path,
):
    """Control: the block above must be about the MISSING declaration.

    Without this, returning 14 unconditionally would satisfy the test beside
    it — and the fourteen corpus projects that do declare a floor would all
    stop advancing.
    """
    from core.quality_gate import gate1_evidence

    from cli.advance_checks import _check_gate1_live_coverage

    project = _project_with_one_fr(tmp_path, targets={"min_coverage": 80})
    monkeypatch.setattr(
        gate1_evidence, "validate_fr_coverage_immediate", lambda *a, **k: 95.0)
    _check_gate1_live_coverage(project, completed_phase=3)

    assert not [r for r in _degradations(project)
                if r.get("component") == "advance:gate1-coverage"], (
        "a project that declared min_coverage was told it had not")


def test_the_release_doc_does_not_print_a_floor_nobody_declared(tmp_path):
    """`min_coverage` lands in a shipped release document.

    Before the fix it printed `80` for a project that declared nothing —
    a number a reader would take for the project's own target. `None` would
    render as the literal string "None", which is worse than either.
    """
    from scripts.phase8_doc_gen import _collect

    project = _project_with_one_fr(tmp_path, targets=None)
    value = _collect(project)["min_coverage"]

    assert value != DEFAULT_MIN_COVERAGE, (
        "a release document published the framework's default as the "
        "project's coverage target")
    assert "None" not in str(value), (
        f"the template field renders as {value!r}")
    assert min_coverage_floor is not None  # import is load-bearing above


def test_the_release_doc_still_prints_a_declared_floor(tmp_path):
    """Control, so the assertion above cannot pass by emptying the field."""
    from scripts.phase8_doc_gen import _collect

    project = _project_with_one_fr(tmp_path, targets={"min_coverage": 100})
    assert _collect(project)["min_coverage"] == 100.0


def test_the_gate_does_not_hand_an_agent_a_threshold_nobody_set() -> None:
    """The sharpest form of this defect.

    `run-gate`'s non-code-FR branch tells the agent, in prose: "Score this
    dimension as {cov_threshold} (= threshold)." With an undeclared floor that
    sentence handed the agent the framework's own 80 to write down as a
    measured score — the framework filling in the answer and then reading it
    back as the project's.
    """
    from cli.gate_cmds import _non_code_coverage_instruction

    with_floor = _non_code_coverage_instruction(100)
    assert "Score this dimension as 100" in with_floor

    without = _non_code_coverage_instruction(None)
    assert "Score this dimension as" not in without, (
        f"the framework still names a score for a threshold nobody declared: "
        f"{without!r}")
    assert "min_coverage" in without, (
        f"the instruction has to name what is missing: {without!r}")
