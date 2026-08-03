"""Round 32 站0/站4 — a number the harness could not measure is not the number zero.

`compute_tool_score` returns None for the harness-internal return codes
(-1 skip, -2 timeout, -3 not found, -4 error, -5 missing config), and S4
handles that correctly and explicitly:

    harness/harness_bridge.py:1541
      # Other dimensions returning None mean the harness could not re-score
      # (e.g. a tool parse issue) — a framework-side gap, not proof of agent
      # fabrication.

Two scorers never reach it. They express "I could not measure this" and "I
measured, the answer is zero" with the same value:

    _score_pytest        no TOTAL line and no "N passed" -> 0.0
    _score_exit_code_binary   any non-zero exit          -> 0.0

Measured consequence, on a live P4 Gate 1:

    test_coverage:            harness scored 0.0, agent reported 100.0
    architecture_constraints: harness scored 0.0, agent reported 100.0
    -> details={"tool_score_fabrication": [...]}  -> BLOCKED

import-linter's 0.0 there was `Could not find package 'taskq_plus'` — the
harness had not put the project's source on PYTHONPATH (站3). The tool never
judged anything, and the framework filed the failure as the agent lying.

A third scorer has the same defect with the opposite sign, found while
measuring premise P3: `_score_pytest_benchmark` returns None only for exit 5.
On exit 2 (a collection error — the suite did not run) it finds no benchmark
rows to penalise and returns **100.0**. A crashed run scores full marks.

Both halves have to land together. Making the scorers return None while S4
still silently `continue`s would trade a false accusation for a silent pass —
the opposite error, equally wrong (Round 30's rule: an abstention is not a
pass). So the None branch has to speak, and it has to speak under the right
name: `infra_fail` already exists in the block-reason registry and says
exactly this ("Dimension scored zero because its tool could not run
(infrastructure, not quality)"). `tool_score_fabrication` is for the case
where the harness *did* measure and the agent's number disagreed.
"""
from __future__ import annotations

import pytest

import harness_cli  # noqa: F401  entry-first load order
from harness.tool_runners import compute_tool_score  # noqa: E402

pytestmark = [pytest.mark.core]


# ── the scorers ─────────────────────────────────────────────────────────

def test_pytest_cov_output_it_cannot_parse_is_not_zero_percent_coverage():
    """Real shape: pytest exits 2 on a collection error, prints no coverage
    table and no pass count. Today that is recorded as 0% coverage."""
    crashed = (
        "=========================== short test summary info "
        "============================\n"
        "ERROR 03-development/tests/test_probe.py\n"
        "!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection "
        "!!!!!!!!!!!!!!!!!!!\n"
    )
    assert compute_tool_score("pytest-cov", crashed, 2) is None, (
        "a pytest run that never collected a test was scored as 0% coverage; "
        "S4 then reports the agent's real 100% as fabrication"
    )


def test_a_real_coverage_number_still_parses():
    """The counter-case: the fix must not make every measurement unscoreable."""
    ok = (
        "Name                Stmts   Miss  Cover\n"
        "---------------------------------------\n"
        "src/app/core.py        10      1    90%\n"
        "---------------------------------------\n"
        "TOTAL                  10      1    90%\n"
        "1 passed in 0.10s\n"
    )
    assert compute_tool_score("pytest-cov", ok, 0) == 90.0


def test_a_genuinely_empty_but_successful_run_is_still_measured():
    """rc=0 with a pass count and no coverage table is a real measurement of
    the pass rate — unchanged behaviour, pinned so the None path cannot
    swallow it."""
    assert compute_tool_score("pytest", "3 passed in 0.2s", 0) == 100.0


def test_a_tool_that_could_not_start_is_not_a_failed_contract():
    """exit-code-binary conflates 'the contract is broken' with 'the tool
    could not run'. import-linter says which one it means, in words."""
    could_not_run = (
        "=============\n Import Linter\n=============\n\n"
        "Could not find package 'probeapp' in your Python path.\n"
    )
    assert compute_tool_score("import-linter", could_not_run, 1) is None, (
        "import-linter reporting that it cannot find the package at all was "
        "scored 0.0 — the same value a genuinely broken layer contract gets"
    )


def test_a_genuinely_broken_contract_is_still_zero():
    """The counter-case for the same scorer."""
    broken = (
        "-----------\nContracts\n-----------\n\n"
        "layers BROKEN\n\nContracts: 0 kept, 1 broken.\n"
    )
    assert compute_tool_score("import-linter", broken, 1) == 0.0


def test_a_benchmark_run_that_crashed_does_not_score_full_marks():
    """Found while measuring premise P3. Exit 5 (no benchmarks collected) is
    already None; exit 2 (collection error) falls through the row regex,
    finds nothing to penalise, and returns 100.0."""
    crashed = (
        "=========================== short test summary info "
        "============================\n"
        "ERROR 03-development/tests/test_perf.py\n"
    )
    assert compute_tool_score("pytest-benchmark", crashed, 2) != 100.0, (
        "a benchmark suite that failed to collect was awarded 100.0 — the "
        "scorer only checks for slow rows, and a crash has none"
    )
    assert compute_tool_score("pytest-benchmark", crashed, 2) is None


# ── the classification ──────────────────────────────────────────────────

def test_a_tool_the_harness_could_not_run_is_not_filed_as_fabrication():
    """Round 31 站6 fixed the sentence a timeout prints and left the key it is
    filed under. `.methodology/last_block.md` on the measured project shows a
    pyright timeout and a PYTHONPATH gap both appearing as
    `tool_score_fabrication` — the one block kind whose registered remediation
    is 'the score, not the run, is what failed'."""
    import inspect

    from harness import harness_bridge

    src = inspect.getsource(harness_bridge._run_harness_cross_validation)
    assert "infra_violations" in src or "cannot_verify" in src, (
        "_run_harness_cross_validation returns one flat list, so the raise "
        "site can only file every entry under tool_score_fabrication — "
        "including the ones that mean the harness itself could not measure"
    )


def test_the_two_block_kinds_stay_distinguishable_at_the_raise_site():
    """Both keys must reach GateBlockedError separately; a single merged list
    is how the mislabel happened."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / "harness" / "harness_bridge.py").read_text(encoding="utf-8")
    marker = '_s4_violations = _run_harness_cross_validation(ctx, raw)'
    assert marker not in src, (
        "the S4 call site still binds a single list; the fabrication verdict "
        "and the could-not-measure verdict need separate details keys "
        "(tool_score_fabrication / infra_fail), because their remediations "
        "point in opposite directions"
    )


def test_the_none_branch_does_not_silently_pass_a_required_tool():
    """Round 30's rule. Today `harness_score is None` with an agent score
    above threshold falls straight through to `continue` for every dimension
    except readability — the agent's number stands, unchecked and unrecorded,
    and the run leaves no trace that the check did not happen.

    Once the scorers above stop returning 0.0, this branch is where every one
    of those cases lands, so it is the branch that decides whether this round
    trades a false accusation for a silent pass.
    """
    import inspect

    from harness import harness_bridge

    src = inspect.getsource(harness_bridge._run_harness_cross_validation)
    assert "Skip rather than falsely blocking the gate (origin behaviour)." not in src, (
        "the harness-could-not-re-score branch still ends in a bare `continue` "
        "for every dimension but readability"
    )
    assert "record_degradation" in src, (
        "_run_harness_cross_validation never writes to the degradation ledger, "
        "so a dimension the harness could not measure leaves no evidence that "
        "it went unmeasured"
    )
