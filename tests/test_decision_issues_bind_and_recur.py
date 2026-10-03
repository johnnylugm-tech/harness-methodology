"""Round 113 站9 — what the Phase 2 decision gate (91fd4ba9) still let through.

91fd4ba9 made the P2 exit refuse an SRS `*-deferred` id the SAB does not
register. Four holes remained, each measured on taskq-sol:

1. A `resolved` row passed when its `resolution_ref` named any file that
   exists. taskq-sol's ADR.md exists and says, of the very decisions it would
   be cited for, "remain unresolved" (ADR.md:116, :140, :159, :212). The
   reference now has to be `path:line`, and that line has to name the issue
   and say `resolved` — the same evidence rule `review_ref` already follows
   (core/quality_gate/property_check._review_disposition_resolves).
2. TEST_SPEC case Inputs carry `precondition="… from ADR (SRS §7 FR-01-deferred)"`
   — 12 cases across 6 ids in taskq-sol — and nothing read them. A test Phase 3
   must write cannot depend on a decision that is still open at Phase 3.
3. An `open` row with `blocks_phase: 5` was checked once, entering Phase 3,
   and never again. It is now asked at every later boundary — and only the
   rows' own deadlines are; registration stays a P2-exit question, so a
   project that passed P2 before this gate existed is not judged by it twice.
4. The P2 prompt told agents to mark an ambiguous case
   `skip_reason: spec_gap_resolved_in_p3` and said check-test-spec-consistency
   "will reject ambiguous cases that lack one of these". No check reads either.
"""

from __future__ import annotations

import ast
from pathlib import Path

from core.quality_gate.decision_issues import (
    decision_issue_findings,
    due_open_decision_findings,
)


def _srs(project: Path, text: str) -> None:
    path = project / "01-requirements" / "SRS.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _adr(project: Path, text: str) -> str:
    path = project / "02-architecture" / "adr" / "ADR.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return "02-architecture/adr/ADR.md"


def _test_spec(project: Path, inputs: str) -> None:
    path = project / "02-architecture" / "TEST_SPEC.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# TEST_SPEC.md\n\n### FR-01: Tasks\n\n"
        "| # | Test Function | Inputs | Type | Q |\n|---|---|---|---|---|\n"
        f"| 1 | `test_fr01_x` | {inputs} | validation | Q2 |\n",
        encoding="utf-8",
    )


def _resolved(ref: str) -> list:
    return [{"id": "FR-01-deferred", "status": "resolved", "resolution_ref": ref}]


# ── 1. a resolution names the issue and says it is resolved ─────────────

def test_a_file_that_exists_is_not_a_resolution(tmp_path):
    _srs(tmp_path, "FR-01-deferred\n")
    rel = _adr(tmp_path, "# ADR-1\nFR-01-deferred: resolved — name is unique per key\n")
    found = decision_issue_findings(tmp_path, _resolved(rel + "#ADR-1"), entering_phase=3)
    assert found and "path:line" in found[0]


def test_a_line_that_says_unresolved_is_not_a_resolution(tmp_path):
    _srs(tmp_path, "FR-01-deferred\n")
    rel = _adr(tmp_path, "# ADR-1\nFR-01-deferred: this ordering remains unresolved\n")
    found = decision_issue_findings(tmp_path, _resolved(rel + ":2"), entering_phase=3)
    assert found and "does not resolve" in found[0]


def test_a_line_naming_another_issue_is_not_a_resolution(tmp_path):
    _srs(tmp_path, "FR-01-deferred\n")
    rel = _adr(tmp_path, "# ADR-1\nFR-02-deferred: resolved — tail is 4096 bytes\n")
    assert decision_issue_findings(tmp_path, _resolved(rel + ":2"), entering_phase=3)


def test_a_line_naming_the_issue_as_resolved_is_a_resolution(tmp_path):
    _srs(tmp_path, "FR-01-deferred\n")
    rel = _adr(tmp_path, "# ADR-1\nFR-01-deferred: resolved — name is unique per key\n")
    assert decision_issue_findings(tmp_path, _resolved(rel + ":2"), entering_phase=3) == []


# ── 2. a test Phase 3 writes cannot hang on an open decision ────────────

def test_a_case_that_depends_on_an_open_decision_blocks_phase_3(tmp_path):
    _srs(tmp_path, "FR-01-deferred\n")
    _test_spec(tmp_path, 'precondition="field map from ADR (SRS §7 FR-01-deferred)"; expected_status="422"')
    rows = [{"id": "FR-01-deferred", "status": "open", "blocks_phase": 5}]
    found = decision_issue_findings(tmp_path, rows, entering_phase=3)
    assert any("TEST_SPEC" in f and "FR-01-DEFERRED" in f for f in found), found


def test_a_case_whose_decision_is_resolved_passes(tmp_path):
    _srs(tmp_path, "FR-01-deferred\n")
    _test_spec(tmp_path, 'precondition="field map from ADR (SRS §7 FR-01-deferred)"')
    rel = _adr(tmp_path, "# ADR-1\nFR-01-deferred: resolved — blacklist is <>;\n")
    assert decision_issue_findings(tmp_path, _resolved(rel + ":2"), entering_phase=3) == []


def test_a_case_citing_an_unregistered_id_is_named(tmp_path):
    _srs(tmp_path, "")
    _test_spec(tmp_path, 'precondition="from ADR (FR-09-deferred)"')
    found = decision_issue_findings(tmp_path, [], entering_phase=3)
    assert any("FR-09-DEFERRED" in f for f in found), found


# ── 3. an open row's own deadline is asked at every boundary ────────────

def test_an_open_row_is_asked_again_when_its_phase_arrives(tmp_path):
    rows = [{"id": "NFR-02-deferred", "status": "open", "blocks_phase": 5}]
    assert due_open_decision_findings(rows, entering_phase=4) == []
    assert "blocks entry to Phase 5" in due_open_decision_findings(rows, entering_phase=5)[0]


def test_later_boundaries_do_not_re_ask_registration(tmp_path):
    # An SRS id with no row is the P2 exit's question, not Phase 5's.
    assert due_open_decision_findings([], entering_phase=5) == []


def test_advance_phase_asks_the_later_boundaries() -> None:
    from tests.support.pipeline import inlined

    fn = inlined("cli/phase_cmds.py", "_advance_prechecks", helper_prefix="_precheck_")
    called = {
        n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))
    }
    assert "due_open_decision_findings" in called


# ── 4. the prompt stops promising a check that does not exist ───────────

def test_the_p2_prompt_does_not_invite_a_phase_3_spec_gap():
    from scripts.workflowgen.spec_phase2 import generate_phase2

    js = generate_phase2()
    assert "spec_gap_resolved_in_p3" not in js
    assert "will reject ambiguous cases that lack one of these" not in js
    assert "decision_issues" in js
