"""Which of the failing tests in the framework's own run belong to THIS FR.

Round 77 站1/站2. S4 executes `pytest-cov` itself — gate1_per_fr.yaml declares
`requires_tool_execution: true` for test_coverage, and
`harness_bridge._run_harness_cross_validation` holds the full stdout in a local
and writes it to `.methodology/gate_evidence/test_coverage_harness.txt`. Forty
lines later, S4-B decided whether this FR's tests were red by regex over the
agent's 500-character `tool_evidence` excerpt. Round 67 / Round 72's mother
pattern: the framework computed the truth and the verdict read somewhere else.

Round 76 (1d111daa) scoped that regex per FR. The diagnosis was right — a
sibling FR's failing test must not block a healthy FR (Round 42: the run's own
SCOPE RULES forbid FR-08 from touching FR-01's code) — but it scoped a parse of
prose the agent pastes, and it turned fail-closed into fail-open:
`if failed_paths: … return []` sat ahead of the `N failed` summary check, so
one recognisable FAILED line waived every failure the regex could not see.
Measured against that commit, with `fr_id="FR-08"`:

    FAILED tests/test_fr01.py::test_x - AssertionError
    20 failed, 59 passed in 6.17s                        ->  []   (PASS)

This module is where that decision lives now, for the same reason
`verify_target.blocking_reason` and `arch_constraints.unconfigured_blocking_reason`
live outside harness_bridge: the bridge holds raise sites, the decision holds
the measurement and the record of what it is worth.

Nothing here parses the agent's evidence. Everything here treats "I could not
read this run" as its own answer, never as "nothing failed" (Round 32 / Round
35), and the caller's fall-back for that answer is the fail-closed rule.
"""
from __future__ import annotations

from pathlib import Path

__all__ = [
    "PER_TEST_OUTCOME_TOOLS",
    "readable_run_output",
    "scoped_test_failures",
    "waived_test_failures",
    "record_waived_test_failures",
    "declared_tests_failed",
    "record_measured_tests_failed",
]

# The tools whose stdout `test_suite_run.failing_nodeids` can read. Named
# rather than inferred from whether the parse happens to match: Round 76's
# per-FR branch fell back to the whole-run rule whenever its regex missed, and
# the operator could not tell a scoped verdict from an unscoped one.
#
# vitest/jest are deliberately absent. Their per-test outcomes are not readable
# here, and the framework's own JS run is NOT per-FR scoped — the per-FR scope
# for JS is a test-TITLE filter the agent applies (`cli/gate_cmds.py`'s
# `-t "test_frNN"`). Blocking a JS gate on the framework's whole-suite copy
# would hand JS projects the exact defect this round removes from Python ones,
# so they keep the pre-Round-76 rule. Recorded in tests/MEASUREMENT_SINKS.yaml
# under `gate:out-of-scope-test-failures`'s `reopen_when`.
PER_TEST_OUTCOME_TOOLS = ("pytest", "pytest-cov", "pytest-cov-integration")


def readable_run_output(
    framework_run: "tuple[str, str, int] | None",
) -> str:
    """The framework's own test-run stdout, or `""` when there is none to read.

    One predicate, two readers — `scoped_test_failures` here and
    harness_bridge's `_parse_skip_counts` — so "did the framework's run answer
    this?" cannot get two answers. `_parse_skip_counts`'s ledger row labels
    its `source` from the same call, which is the whole point: a row saying
    `harness-run` beside a number that came from the agent's excerpt is the
    defect this round is about, one layer down.
    """
    if framework_run is None:
        return ""
    tool, output = framework_run[0], framework_run[1]
    return output or "" if tool in PER_TEST_OUTCOME_TOOLS else ""


def scoped_test_failures(
    fr_id: "str | None",
    framework_run: "tuple[str, str, int] | None",
) -> "tuple[list[str], list[str]] | None":
    """`(this FR's failing nodeids, everyone else's)` from the framework's run.

    *framework_run* is `(tool, output, returncode)` — the run S4 performed via
    `harness.tool_runners.run_tool`, not anything the agent wrote.

    ``None`` when the failures cannot be scoped to an FR at all: no FR named,
    the harness did not run a test tool, the tool was not one whose per-test
    outcomes are readable, or its output could not be reconciled against its
    own counts line. Every one of those is "I could not read this", which the
    caller must handle as such and never as "nothing failed".

    Ownership is `test_suite_run.select_fr_outcomes` — the same predicate
    `fr_suite_verdict` uses for TDD-GREEN, so the FR→test convention has one
    implementation. Round 76 wrote a sixth copy (`f"test_fr{int(n):02d}" in
    path`) and got three corpus shapes wrong: `test_fr7.py` read as another
    FR's, `test_fr100.py` read as FR-10's, and `src/test_fr08_util.py` read as
    FR-08's own test.
    """
    output = readable_run_output(framework_run)
    if not fr_id or not output:
        return None
    from core.quality_gate.test_suite_run import (
        failing_nodeids,
        select_fr_outcomes,
    )
    nodeids = failing_nodeids(output)
    if nodeids is None:
        return None
    mine = set(select_fr_outcomes({n: "failed" for n in nodeids}, fr_id))
    return (
        [n for n in nodeids if n in mine],
        [n for n in nodeids if n not in mine],
    )


def waived_test_failures(
    fr_id: "str | None",
    framework_run: "tuple[str, str, int] | None",
) -> list[str]:
    """The failing nodeids this FR's gate does NOT block on.

    A per-FR gate answers for its own FR. Blocking FR-08 because FR-01's test
    is red asks it to fix code the run's own SCOPE RULES forbid it to touch
    (Round 42 — a project obeying the substance must not be charged for the
    letter), so these do not block.

    They are also not caught by "the owning FR's GATE1", which is what Round 76
    promised. The Phase 3 FR loop is forward-only (`for (const frId of frIds)`
    with an `alreadyDone` skip) and S4-B runs only at `gate_num == 1`, so an FR
    already behind never re-runs; and a failing test in a file no FR owns —
    `tests/integration/test_api_flow.py`, `tests/test_nfr09_ac3.py` — has no
    owning gate at all. What does catch them is
    `cli/phase_cmds.py::_advance_prechecks`, which runs the whole suite at
    every phase transition and refuses to advance while any test is red. Its
    own limits are real and recorded: it is guarded by `src_dir.is_dir()` and
    `_suite.ran`, so a project `run_suite` declines to measure has no executor
    behind this waiver and the ledger row is the only record.
    """
    scoped = scoped_test_failures(fr_id, framework_run)
    return [] if scoped is None else scoped[1]


def record_waived_test_failures(
    project_root: "str | Path", fr_id: "str | None", phase: int,
    framework_run: "tuple[str, str, int] | None",
) -> list[str]:
    """Leave a record of what this FR's gate declined to block on, and return it.

    Round 77 站2. The waiver's only artifact was one `print()`: gone with the
    console, its count per-test while its sample was per-file (three files
    named for twenty tests, no ellipsis, FR-04 and FR-05 never mentioned), and
    a docstring claiming it reached "the verify log"
    (`.methodology/gate_verify.jsonl`, core/quality_gate/gate_verify.py) that
    nothing on this path has ever written to. Round 46 站2 answered the
    identical question twenty lines below in the same function — "how many
    tests did not run at this gate" — with a ledger row, for the same reason:
    so it is answerable after the run without a person having watched the
    console.

    Public and side-effecting on purpose. The call site is inside
    `finalize_gate`, which no test can drive this far cheaply, and
    `tests/test_patch_discipline.py` refuses a test that reaches in by patching
    private seams. So the seam is the function.
    """
    waived = waived_test_failures(fr_id, framework_run)
    if not waived:
        return []
    print(
        f"[WARN] {fr_id} GATE1: {len(waived)} failing test(s) outside this "
        f"FR's scope — not blocking this gate; advance-phase refuses to "
        f"advance while any test is red: " + ", ".join(sorted(waived))
    )
    from core.degradation_ledger import record_degradation
    record_degradation(
        project_root, "gate:out-of-scope-test-failures",
        f"{len(waived)} failing test(s) are outside {fr_id}'s scope and did "
        f"not block its Gate 1",
        why=("a per-FR gate answers for its own FR; the run's SCOPE RULES "
             "forbid this FR from touching another's code. The executor is "
             "_advance_prechecks, which runs the whole suite at every phase "
             "transition and refuses to advance while any test is red"),
        data={"nodeids": sorted(waived), "fr_id": fr_id, "phase": phase},
        owner="project",
    )
    return waived


def declared_tests_failed(raw: dict) -> "int | None":
    """The agent's own `tests_failed` for test_coverage, or None if unstated.

    The GATE1 prompt has called this field REQUIRED since it was written and
    nothing in the tree has ever read it — measured Round 77, one grep, zero
    production readers. A required field nobody reads is a declaration the
    project pays to write and the framework never has to honour.
    """
    entry = (raw.get("breakdown") or {}).get("test_coverage")
    if not isinstance(entry, dict):
        return None
    value = entry.get("tests_failed")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def record_measured_tests_failed(
    raw: dict, fr_id: "str | None", phase: int,
    framework_run: "tuple[str, str, int] | None",
    project_root: "str | Path",
) -> "int | None":
    """Write the framework's own count of this FR's failing tests into *raw*.

    Returns the number written, or None when the framework could not measure —
    in which case the agent's declaration stands and `_check_tests_failed`
    blocks on it if it is non-zero.

    Round 77 站5. This is what S4 already does for `score`
    (`s4_score_verdict`: "the framework ran the tool, so the framework's
    number is the score"), applied to the one field beside it that no reader
    had. `raw` is `ctx.finalized_result`, which `build_persisted_gate_result`
    merges over the agent's document, so the number reaches the committed
    artifact rather than only the console.

    A mismatch is recorded and NOT blocked. Every case where an under-report
    hides a real failure is already blocked by the verdict itself, so a block
    here could only catch an OVER-report — an agent pasting the whole suite's
    summary count, which is exactly what the prompt asked for until Round 77
    站3 removed the ambiguity. Charging a project for obeying yesterday's
    instruction is Round 42's defect. The row is how the next round finds out
    whether the prompt change worked.
    """
    scoped = scoped_test_failures(fr_id, framework_run)
    if scoped is None:
        return None
    measured = len(scoped[0])
    declared = declared_tests_failed(raw)
    entry = (raw.setdefault("breakdown", {})).setdefault("test_coverage", {})
    if isinstance(entry, dict):
        entry["tests_failed"] = measured
    if declared is not None and declared != measured:
        print(
            f"  [S4-B] tests_failed: agent={declared} | framework={measured} "
            f"({fr_id}'s own tests, from the harness's own run) — using the "
            f"framework's count"
        )
        from core.degradation_ledger import record_degradation
        record_degradation(
            project_root, "gate:tests-failed-declared",
            f"agent declared tests_failed={declared} for {fr_id}; the "
            f"harness's own run counts {measured}",
            why=("the field is the agent's and nothing had ever read it; the "
                 "framework now writes its own number into the committed "
                 "result. Not blocked — an over-report is what the prompt "
                 "asked for before Round 77 站3"),
            data={"declared": declared, "measured": measured,
                  "fr_id": fr_id, "phase": phase}, owner="project",
        )
    return measured
