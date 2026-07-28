"""Round 24 站2b/2c — rendered artifacts and citations must survive checking.

Two guards, one theme: a check that only asks "does this field exist" passes
things that are not true.

2b — QUALITY_REPORT.md vs the gate result it renders. The regression fixture
is the exact shape committed by the run-all-by-workflow P1-P8 validation run:
gate4_result.json says `mutation_testing: {score: null,
excluded_by_feature_flag: true}` while 06-quality/QUALITY_REPORT.md line 19
says `| Mutation Testing | 0/100 | ✗ FAIL |`, because the agent worked around a
crash in the generator by rewriting null to 0 in a temp copy of the gate
result. Everything downstream passed: the gate result itself was never
touched, so no integrity check had anything to complain about.

2c — Agent B citations. The same run's QUALITY_REPORT.md approval cited
`:7` and `:13` for a claim about the Mutation Testing row (line 19), and
described the report as showing "Mutation Testing excluded by feature flag",
text the report does not contain. Existence checking cannot catch that
particular lie, and this suite says so explicitly — it pins the boundary
(position exists) rather than pretending to check support.
"""

from __future__ import annotations

import json

import pytest

from core.quality_gate.agent_b_approvals import unresolvable_citations
from core.quality_gate.quality_report_verify import (
    find_latest_gate_result,
    verify_quality_report,
)

pytestmark = [pytest.mark.core]


def _project(tmp_path, breakdown, gate1=None, gate_num=4):
    (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".methodology" / f"gate{gate_num}_result.json").write_text(
        json.dumps({"gate": gate_num, "composite_score": 97.4, "breakdown": breakdown}),
        encoding="utf-8",
    )
    if gate1 is not None:
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"gate_results": {"gate1": gate1}}), encoding="utf-8"
        )
    return tmp_path


def _write_report(tmp_path, dim_rows, fr_rows=()):
    from core.utils.project_layout import ProjectLayout

    out = ProjectLayout(tmp_path).quality_report_path
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Quality Report",
        "",
        "## Assessment Dimensions",
        "",
        "| Dimension | Score | Status | Detail |",
        "|-----------|-------|--------|--------|",
    ]
    lines += [f"| {label} | {score} | {status} |  |" for label, score, status in dim_rows]
    if fr_rows:
        lines += [
            "",
            "## Per-FR Gate 1 Summary",
            "",
            "| FR ID | Score | Status |",
            "|-------|-------|--------|",
        ]
        lines += [f"| {fr} | {score} | ✓ PASS |" for fr, score in fr_rows]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


# ── 2b ──────────────────────────────────────────────────────────────────

def test_agreeing_report_has_no_violations(tmp_path):
    _project(tmp_path, {
        "linting": {"score": 100.0},
        "mutation_testing": {"score": None, "excluded_by_feature_flag": True},
    })
    _write_report(tmp_path, [
        ("Linting", "100.0/100", "✓ PASS"),
        ("Mutation Testing", "N/A", "⊘ EXCLUDED"),
    ])
    assert verify_quality_report(tmp_path) == []


def test_null_score_rendered_as_zero_fail_is_caught(tmp_path):
    """THE run-all-by-workflow defect, verbatim."""
    _project(tmp_path, {
        "linting": {"score": 100.0},
        "mutation_testing": {"score": None, "excluded_by_feature_flag": True},
    })
    _write_report(tmp_path, [
        ("Linting", "100.0/100", "✓ PASS"),
        ("Mutation Testing", "0/100", "✗ FAIL"),
    ])
    violations = verify_quality_report(tmp_path)
    assert len(violations) == 1
    assert "Mutation Testing" in violations[0]
    assert "score=null" in violations[0]
    assert "never zero" in violations[0]
    assert "finalize-gate" in violations[0]  # remediation is actionable


def test_altered_numeric_score_is_caught(tmp_path):
    _project(tmp_path, {"security": {"score": 62.0}})
    _write_report(tmp_path, [("Security", "97.0/100", "✓ PASS")])
    violations = verify_quality_report(tmp_path)
    assert len(violations) == 1
    assert "97.0" in violations[0] and "62.0" in violations[0]


def test_display_rounding_is_not_a_violation(tmp_path):
    """94.25287356321839 rendered as 94.3 is agreement, not drift."""
    _project(tmp_path, {"traceability": {"score": 94.25287356321839}})
    _write_report(tmp_path, [("Traceability", "94.3/100", "✓ PASS")])
    assert verify_quality_report(tmp_path) == []


def test_dropped_dimension_is_caught(tmp_path):
    """A dimension missing from the report is a dimension nobody reviews."""
    _project(tmp_path, {"linting": {"score": 100.0}, "security": {"score": 80.0}})
    _write_report(tmp_path, [("Linting", "100.0/100", "✓ PASS")])
    violations = verify_quality_report(tmp_path)
    assert len(violations) == 1
    assert "security" in violations[0]


def test_invented_dimension_is_caught(tmp_path):
    _project(tmp_path, {"linting": {"score": 100.0}})
    _write_report(tmp_path, [
        ("Linting", "100.0/100", "✓ PASS"),
        ("Vibes", "100.0/100", "✓ PASS"),
    ])
    violations = verify_quality_report(tmp_path)
    assert len(violations) == 1
    assert "Vibes" in violations[0]


def test_na_shown_for_a_measured_dimension_is_caught(tmp_path):
    _project(tmp_path, {"performance": {"score": 55.0}})
    _write_report(tmp_path, [("Performance", "N/A", "⊘ FRAMEWORK-OWNED")])
    violations = verify_quality_report(tmp_path)
    assert len(violations) == 1
    assert "shows N/A" in violations[0]


def test_per_fr_score_drift_is_caught(tmp_path):
    _project(tmp_path, {"linting": {"score": 100.0}},
             gate1={"FR-01": {"score": 88.0}, "FR-02": {"score": 100.0}})
    _write_report(tmp_path, [("Linting", "100.0/100", "✓ PASS")],
                  fr_rows=[("FR-01", "100.0"), ("FR-02", "100.0")])
    violations = verify_quality_report(tmp_path)
    assert len(violations) == 1
    assert "FR-01" in violations[0]


def test_missing_report_is_not_this_functions_problem(tmp_path):
    """Existence is the caller's deliverable check (finalize-gate fails the
    gate on a generation failure) — this function only compares what is there."""
    _project(tmp_path, {"linting": {"score": 100.0}})
    assert verify_quality_report(tmp_path) == []


def test_methodology_copy_wins_over_sessi_work(tmp_path):
    """finalize-gate patches composite_score/verdict into the .methodology
    copy; .sessi-work still holds the agent's unpatched self-assessment."""
    (tmp_path / ".methodology").mkdir(parents=True)
    (tmp_path / ".sessi-work").mkdir(parents=True)
    (tmp_path / ".methodology" / "gate4_result.json").write_text(
        json.dumps({"composite_score": 97.4}), encoding="utf-8")
    (tmp_path / ".sessi-work" / "gate4_result.json").write_text(
        json.dumps({"composite_score": 12.0}), encoding="utf-8")
    gate_num, data = find_latest_gate_result(tmp_path)
    assert (gate_num, data["composite_score"]) == (4, 97.4)


def test_highest_gate_wins(tmp_path):
    (tmp_path / ".methodology").mkdir(parents=True)
    for n in (1, 3):
        (tmp_path / ".methodology" / f"gate{n}_result.json").write_text(
            json.dumps({"gate": n}), encoding="utf-8")
    assert find_latest_gate_result(tmp_path)[0] == 3


def test_corrupt_gate_result_is_skipped_not_crashed(tmp_path):
    (tmp_path / ".methodology").mkdir(parents=True)
    (tmp_path / ".methodology" / "gate4_result.json").write_text("{trunc", encoding="utf-8")
    (tmp_path / ".methodology" / "gate3_result.json").write_text(
        json.dumps({"gate": 3}), encoding="utf-8")
    assert find_latest_gate_result(tmp_path)[0] == 3


# ── 2c ──────────────────────────────────────────────────────────────────

def test_citation_to_a_real_line_resolves(tmp_path):
    (tmp_path / "SRS.md").write_text("a\nb\nc\n", encoding="utf-8")
    assert unresolvable_citations(tmp_path, ["SRS.md:2"]) == []


def test_citation_past_end_of_file_is_flagged(tmp_path):
    (tmp_path / "SRS.md").write_text("a\nb\nc\n", encoding="utf-8")
    bad = unresolvable_citations(tmp_path, ["SRS.md:4"])
    assert len(bad) == 1 and "file has 3 lines" in bad[0]


def test_citation_to_a_missing_file_is_flagged(tmp_path):
    bad = unresolvable_citations(tmp_path, ["nope/SAD.md:1"])
    assert len(bad) == 1 and "no such file" in bad[0]


def test_whole_file_citation_needs_only_the_file(tmp_path):
    (tmp_path / "SAD.md").write_text("x\n", encoding="utf-8")
    assert unresolvable_citations(tmp_path, ["SAD.md"]) == []


def test_absolute_citation_path_is_accepted(tmp_path):
    f = tmp_path / "SRS.md"
    f.write_text("a\nb\n", encoding="utf-8")
    assert unresolvable_citations(tmp_path, [f"{f}:2"]) == []


def test_line_and_column_citation_is_accepted(tmp_path):
    (tmp_path / "SRS.md").write_text("a\nb\n", encoding="utf-8")
    assert unresolvable_citations(tmp_path, ["SRS.md:2:11"]) == []


def test_empty_citation_is_flagged(tmp_path):
    assert unresolvable_citations(tmp_path, ["   "]) == ["<empty citation>"]


def test_line_zero_is_flagged(tmp_path):
    (tmp_path / "SRS.md").write_text("a\n", encoding="utf-8")
    assert len(unresolvable_citations(tmp_path, ["SRS.md:0"])) == 1


def test_existence_check_does_not_claim_to_verify_support(tmp_path):
    """Boundary pin (2c): a citation pointing at a real but irrelevant line
    passes. This is the documented limit, not an oversight — checking support
    needs a second LLM judgement, which is the Round 21 failure shape."""
    report = tmp_path / "QUALITY_REPORT.md"
    report.write_text("\n".join(f"line {i}" for i in range(1, 30)), encoding="utf-8")
    assert unresolvable_citations(tmp_path, ["QUALITY_REPORT.md:13"]) == []


# ── 2a ──────────────────────────────────────────────────────────────────

def test_gate4_deliverable_generation_failure_fails_the_gate(tmp_path, monkeypatch, capsys):
    """Was `[WARN] ... skipped` with the gate passing."""
    from cli import gate_cmds
    from cli.exit_codes import EX_HARNESS_BUG

    def _boom(_script):
        raise TypeError("'>=' not supported between instances of 'NoneType' and 'int'")

    monkeypatch.setattr(gate_cmds, "load_harness_script", _boom)
    rc = gate_cmds._generate_gate4_deliverables(tmp_path, 6)
    assert rc == EX_HARNESS_BUG
    out = capsys.readouterr().out
    assert "[BLOCKED]" in out
    assert "QUALITY_REPORT.md" in out
    assert "harness defect" in out
    assert "Do NOT hand-write" in out
    assert "crash-triage" in out
    assert "[WARN]" not in out
    ledger = tmp_path / ".sessi-work" / "degradations.jsonl"
    assert ledger.is_file(), "a swallowed-then-blocked generation must leave a ledger trail"


def test_gate4_deliverables_pass_through_when_report_agrees(tmp_path, monkeypatch):
    from cli import gate_cmds

    _project(tmp_path, {"linting": {"score": 100.0}})
    _write_report(tmp_path, [("Linting", "100.0/100", "✓ PASS")])

    class _Mod:
        generate_quality_report = staticmethod(lambda _p: None)
        generate_release_notes = staticmethod(lambda _p: None)

    monkeypatch.setattr(gate_cmds, "load_harness_script", lambda _s: _Mod)
    assert gate_cmds._generate_gate4_deliverables(tmp_path, 6) is None


def test_gate4_blocks_when_the_rendered_report_disagrees(tmp_path, monkeypatch, capsys):
    from cli import gate_cmds

    _project(tmp_path, {
        "mutation_testing": {"score": None, "excluded_by_feature_flag": True},
    })
    _write_report(tmp_path, [("Mutation Testing", "0/100", "✗ FAIL")])

    class _Mod:
        generate_quality_report = staticmethod(lambda _p: None)
        generate_release_notes = staticmethod(lambda _p: None)

    monkeypatch.setattr(gate_cmds, "load_harness_script", lambda _s: _Mod)
    assert gate_cmds._generate_gate4_deliverables(tmp_path, 6) == 1
    out = capsys.readouterr().out
    assert "[BLOCKED]" in out
    assert "Mutation Testing" in out
    assert "Do not hand-edit" in out
    assert "finalize-gate --gate 4" in out


def test_every_gate4_deliverable_is_checked_not_just_the_first():
    """Both generators must be in the fail-the-gate loop — RELEASE_NOTES.md
    carried the identical swallow one line below QUALITY_REPORT.md's."""
    from cli.gate_cmds import _GATE4_DELIVERABLES

    artifacts = {a for _s, _f, a in _GATE4_DELIVERABLES}
    assert artifacts == {"QUALITY_REPORT.md", "RELEASE_NOTES.md"}
