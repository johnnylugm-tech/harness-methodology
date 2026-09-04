"""Phase 4's numbers are checked once, then travel four phases unchecked.

Round 97, two halves.

C1 — `check_coverage_report` cannot see the claim the corpus writes.
Its claim regex requires the literal phrase `Line coverage`:

    ^[\\s\\-*]*Line coverage[^\\d]*(\\d{2,3}(?:\\.\\d)?)\\s*%

Measured over the eleven COVERAGE_REPORT.md files in the corpus:

    that phrase                      1 / 11
    pytest-cov's own `TOTAL … N%`   11 / 11   (one distinct value each)

So on ten of eleven projects the check found no numeric claim and returned
nothing — which is exactly what `check_test_count_reconciliation`'s docstring
next door criticises it for ("returns nothing when it finds no numeric
claim"). The `TOTAL` shape is already parsed twelve lines below, for the
ACTUAL side of the same comparison.

C2 — the reconciliation stops at Phase 4, and the tree does not.
`if phase != 4: return []`, with a real reason: Phase 5-8 has no `check_pytest`
ahead of it, so calling it there would execute the whole suite again. But the
later gates already ran it, and left the run at
`.methodology/gate_evidence/gate{N}/test_coverage.txt`. Measured on the seven
corpus projects that have that evidence, against their shipped Phase-4
documents:

    taskq-api      321 vs 321                      agree
    taskq-redo     242 vs 242                      agree
    taskq-cc       283 vs 287                      four tests added after P4
    taskq-cc-new   241 vs 236
    taskq-new      299 vs 338
    taskq-final    227 vs 267,  97% vs 100%        the audit's own D3 came from here
    taskq-super   7563 vs 331                      measured over a different tree

Five of seven disagree, and they are not the same defect. Growth after Phase 4
is normal; a count above the whole suite the framework measured is a document
about another tree. Equality would charge the first (Round 42), and a
tolerance would be a number invented to fit the corpus — so the block is the
one case that needs no threshold, and the rest is recorded with both numbers
beside each other.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


def _evidence(project: Path, gate: int, *, passed: int, total_pct: int) -> None:
    d = project / ".methodology" / "gate_evidence" / f"gate{gate}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "test_coverage.txt").write_text(
        "Name                     Stmts   Miss  Cover\n"
        "src/app.py                 100      0   100%\n"
        "-----------------------------------------------\n"
        f"TOTAL                     1000      0   {total_pct}%\n"
        f"{passed} passed, 2 warnings in 19.88s\n",
        encoding="utf-8",
    )


def _p4_docs(project: Path, *, passed: int, cov_pct: int) -> None:
    d = project / "04-testing"
    d.mkdir(parents=True, exist_ok=True)
    (d / "TEST_RESULTS.md").write_text(
        f"# Test Results\n\n```\n{passed} passed, 2 warnings in 18.51s\n```\n",
        encoding="utf-8",
    )
    (d / "COVERAGE_REPORT.md").write_text(
        f"# Coverage\n\n## Overall coverage\n\n```\nTOTAL    1198    30    {cov_pct}%\n```\n",
        encoding="utf-8",
    )


# ── C1: the claim the corpus actually writes ────────────────────────────────

def test_the_total_row_is_a_coverage_claim(tmp_path):
    """11/11 corpus reports state it this way; 1/11 use the phrase."""
    from core.quality_gate.cross_artifact import coverage_claim

    _p4_docs(tmp_path, passed=227, cov_pct=97)
    assert coverage_claim(
        (tmp_path / "04-testing" / "COVERAGE_REPORT.md").read_text(encoding="utf-8")
    ) == 97.0


def test_the_total_row_with_branch_coverage_is_a_coverage_claim():
    """Branch coverage (--cov-branch) adds Branch and BrPart columns (4 integer columns)."""
    from core.quality_gate.cross_artifact import coverage_claim

    assert coverage_claim("TOTAL    100    20    10    2    75%\n") == 75.0
    assert coverage_claim("| TOTAL | 100 | 20 | 10 | 2 | 75% |\n") == 75.0



def test_the_line_coverage_phrase_still_reads():
    """Negative control: the one shape that worked must keep working."""
    from core.quality_gate.cross_artifact import coverage_claim

    assert coverage_claim("- Line coverage: 85%\n") == 85.0


def test_a_report_with_no_number_is_still_no_claim():
    """Round 32: could-not-measure is not a failing measurement. This function
    reports absence as None; the caller decides."""
    from core.quality_gate.cross_artifact import coverage_claim

    assert coverage_claim("# Coverage\n\nprose only.\n") is None


# ── C2: the later phases ────────────────────────────────────────────────────

def test_a_count_above_the_whole_suite_blocks(tmp_path):
    """taskq-super: 7563 in the document, 331 measured. A document cannot
    describe more tests than the suite has — no tolerance needed to say so."""
    from core.quality_gate.cross_artifact import check_delivered_report_freshness

    _evidence(tmp_path, 4, passed=331, total_pct=99)
    _p4_docs(tmp_path, passed=7563, cov_pct=100)
    v = check_delivered_report_freshness(tmp_path, 8)
    assert any(x["severity"] == "CRITICAL" for x in v), v
    assert any("7563" in x["issue"] and "331" in x["issue"] for x in v), v


def test_normal_growth_after_phase_four_is_reported_not_blocked(tmp_path):
    """taskq-cc: 283 in the document, 287 measured. Four tests were added after
    Phase 4, which is what a pipeline does. Charging a project for that is
    Round 42's defect."""
    from core.quality_gate.cross_artifact import check_delivered_report_freshness

    _evidence(tmp_path, 4, passed=287, total_pct=100)
    _p4_docs(tmp_path, passed=283, cov_pct=100)
    v = check_delivered_report_freshness(tmp_path, 8)
    assert v, "the drift is invisible"
    assert not any(x["severity"] == "CRITICAL" for x in v), v
    assert any("283" in x["issue"] and "287" in x["issue"] for x in v), v


def test_the_coverage_claim_is_reconciled_too(tmp_path):
    """taskq-final: the document says 97% and explains a failure cluster that
    no longer exists; the delivered tree measures 100%. That document is where
    the audit of this project got its 'coverage is only 97%' finding."""
    from core.quality_gate.cross_artifact import check_delivered_report_freshness

    _evidence(tmp_path, 4, passed=267, total_pct=100)
    _p4_docs(tmp_path, passed=227, cov_pct=97)
    v = check_delivered_report_freshness(tmp_path, 8)
    assert any("97" in x["issue"] and "100" in x["issue"] for x in v), v


def test_agreement_is_silent(tmp_path):
    """taskq-api and taskq-redo. A check that fires on a clean delivery is
    noise, and noise is what buries the row that matters."""
    from core.quality_gate.cross_artifact import check_delivered_report_freshness

    _evidence(tmp_path, 4, passed=242, total_pct=100)
    _p4_docs(tmp_path, passed=242, cov_pct=100)
    assert check_delivered_report_freshness(tmp_path, 8) == []


def test_no_recorded_measurement_is_not_an_accusation(tmp_path):
    """The whole point of reading recorded evidence is that it costs no run.
    Where there is none, there is nothing to compare — not a finding."""
    from core.quality_gate.cross_artifact import check_delivered_report_freshness

    _p4_docs(tmp_path, passed=227, cov_pct=97)
    assert check_delivered_report_freshness(tmp_path, 8) == []


def test_it_does_not_run_the_suite(tmp_path, monkeypatch):
    """The reason Phase 4 kept this check to itself was cost. Reading the
    recorded run answers the question without paying it — so if this ever
    starts executing pytest, the original objection is back."""
    import subprocess

    from core.quality_gate import cross_artifact

    def _boom(*a, **k):
        raise AssertionError("check_delivered_report_freshness executed a subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(cross_artifact, "measured_suite", _boom)
    _evidence(tmp_path, 4, passed=287, total_pct=100)
    _p4_docs(tmp_path, passed=283, cov_pct=100)
    cross_artifact.check_delivered_report_freshness(tmp_path, 8)


def test_phase_four_keeps_its_own_check(tmp_path):
    """Negative control: Phase 4 already reconciles against a live run and
    blocks on any mismatch. That check is working — taskq-super's 7563 is in
    its docstring — and this one must not take it over."""
    from core.quality_gate.cross_artifact import check_delivered_report_freshness

    _evidence(tmp_path, 4, passed=287, total_pct=100)
    _p4_docs(tmp_path, passed=283, cov_pct=100)
    assert check_delivered_report_freshness(tmp_path, 4) == []


def test_the_newest_gate_is_the_one_compared(tmp_path):
    """The delivered tree is the last one measured, not the first."""
    from core.quality_gate.cross_artifact import check_delivered_report_freshness

    _evidence(tmp_path, 2, passed=227, total_pct=97)
    _evidence(tmp_path, 4, passed=267, total_pct=100)
    _p4_docs(tmp_path, passed=227, cov_pct=97)
    v = check_delivered_report_freshness(tmp_path, 8)
    assert any("267" in x["issue"] for x in v), v


def test_the_caller_carries_both_answers_out(tmp_path):
    """Driven through `run_cross_artifact_checks`, because a check that runs
    and has its answer discarded is what this round keeps finding.

    The counter-proof for the two checks above dropped `violations.extend(`
    and left the calls and the `checks_ran` count in place. Every guard
    stayed green: the per-check tests call the checks directly, and
    `test_cross_artifact_thorough` reads only the count. Round 24 — a number
    computed and thrown away — inside the guards written for Round 24.
    """
    from core.quality_gate.cross_artifact import run_cross_artifact_checks

    _evidence(tmp_path, 4, passed=331, total_pct=100)
    _p4_docs(tmp_path, passed=7563, cov_pct=100)
    cfg = tmp_path / "08-config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "CONFIG_RECORDS.md").write_text(
        "| Production | `uvicorn taskq_api.main:app` |\n", encoding="utf-8")
    src = tmp_path / "03-development" / "src" / "taskq_api"
    src.mkdir(parents=True, exist_ok=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "app.py").write_text("app = 1\n", encoding="utf-8")

    out = run_cross_artifact_checks(tmp_path, 8)
    issues = " | ".join(v.get("issue", "") for v in out["violations"])
    assert "7563" in issues, (
        f"the freshness check ran and its answer never left: {out}")
    assert "taskq_api.main" in issues, (
        f"the module check ran and its answer never left: {out}")
    assert out["passed"] is False, (
        "a report describing 7563 tests in a 331-test tree is CRITICAL and "
        "the caller reported the phase as passing"
    )
