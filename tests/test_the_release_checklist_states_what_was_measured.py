"""Round 115 站6 — RELEASE_CHECKLIST.md states what the framework measured and what only a human can.

Measured: in 11 of 11 corpus projects that reached Phase 8,
08-config/RELEASE_CHECKLIST.md is the template's five unchecked boxes, byte
for byte. `phase8_doc_gen` "renders" it, but the template has no placeholder,
so nothing is filled in — while the P8 prompt told the agent to "KEEP the
framework-generated Gate 4 PASS proof, quality_manifest composite_score, FR
coverage, git tag/hash intact": content that never existed. Phase 8 is a
release CANDIDATE (老闆裁定): the framework states the facts it holds (phases
recorded, Gate 4's verdict, open confirmed critical/high findings, the
commit), names where the CI verdict is enforced, and lists sign-off,
provisioning and rollback as PENDING-HUMAN — it neither ticks them nor blocks
on them.

Not rendered: a count of HIGH risks. The corpus's RISK_REGISTER.md files
use at least five severity notations (HIGH, 高, emoji, a numeric score, a
"Very Low" scale); a count read from them would be the framework's guess.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.phase8_doc_gen import generate


def _project(tmp_path: Path, *, hunt: "list | None", phases: "tuple[int, ...]" = (1, 2, 3, 4, 5, 6, 7)) -> Path:
    m = tmp_path / ".methodology"
    m.mkdir(parents=True)
    (m / "state.json").write_text(json.dumps({
        "project": "demo", "current_phase": 8,
        "phase_completed": {str(n): {"commit": "abc"} for n in phases}}))
    (m / "quality_manifest.json").write_text(json.dumps({"gate_results": {}}))
    (m / "gate4_result.json").write_text(json.dumps({"verdict": "PASS", "composite_score": 98.02}))
    if hunt is not None:
        (m / "bug_hunt_report.json").write_text(json.dumps({"findings": hunt}))
    return tmp_path


def _checklist(proj: Path) -> str:
    generate(proj)
    return (proj / "08-config" / "RELEASE_CHECKLIST.md").read_text(encoding="utf-8")


def test_the_framework_items_carry_values_not_checkboxes(tmp_path):
    hunt = [{"id": "auth#1", "severity": "high", "confirmed": True, "resolution": {"status": "open"}}]
    text = _checklist(_project(tmp_path, hunt=hunt))
    assert "P1–P7 recorded" in text
    assert "PASS (composite 98.02)" in text
    assert "1 unresolved: auth#1 (open)" in text
    assert "last_milestone_head.p8" in text and "exit 51" in text
    assert "{" not in text and "${" not in text, "every placeholder is filled"


def test_the_human_items_are_pending_and_unticked(tmp_path):
    text = _checklist(_project(tmp_path, hunt=[]))
    pending = text.split("## PENDING-HUMAN")[1]
    assert pending.count("- [ ]") == 3 and "- [x]" not in pending
    assert "not verified by the framework" in text


def test_a_missing_phase_and_a_missing_hunt_are_said(tmp_path):
    text = _checklist(_project(tmp_path, hunt=None, phases=(1, 2, 4, 5, 6, 7)))
    assert "missing P3" in text
    assert "not measured" in text


def test_the_auditor_passes_a_checklist_whose_human_items_are_ticked():
    from scripts.phase_auditor import PhaseAuditor

    class _A(PhaseAuditor):
        def __init__(self, text):  # pylint: disable=super-init-not-called
            self._text = text
            self.found = []

            class _R:
                def add(_s, f):
                    self.found.append(f)
            self.result = _R()

        def _content(self, _paths):
            return self._text

    ticked = "## PENDING-HUMAN\n- [x] a\n- [x] b\n- [x] c\n"
    a = _A(ticked)
    a._check_release_checklist_depth()
    assert a.found[0].severity == "PASS"
    a = _A(ticked.replace("[x] c", "[ ] c"))
    a._check_release_checklist_depth()
    assert a.found[0].severity == "WARNING"


def test_the_p8_reviewer_is_not_told_to_keep_content_that_does_not_exist():
    from scripts.workflowgen.generate_workflows import generate

    js = generate(8)
    assert "KEEP the framework-generated Gate 4 PASS proof" not in js
    assert "never tick a PENDING-HUMAN box" in js
    assert "pipeline complete" not in js.lower()
