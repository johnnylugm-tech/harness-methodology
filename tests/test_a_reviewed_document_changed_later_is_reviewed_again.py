"""Round 114 站6 — a reviewed deliverable changed after its phase closed is reviewed again.

Round 113 站1 bound an approval to the bytes Agent B reviewed and checked the
binding only for the phase being completed. Measured on the corpus: SAD.md was
edited after Phase 2 in 4 of 15 projects (11 commits, 1 with an ADR record) —
taskq-new dropped `taskq.api.deps` from high_risk_modules, taskq-open's Gate 1
fixers rewrote typed constraints and SAB.json (a79ffd8, 5682a9f) — and SRS.md
was edited during Phase 2 (5c7f5b5). taskq-open finished Phase 8 with five
approvals describing bytes that no longer exist, and nothing asked.

老闆裁定 A: every advance asks for every closed phase's reviewed deliverables,
and the workflow sends a changed one to Agent B as a change review — Python
builds the context (the diff since the reviewed bytes, found in git) and binds
the new approval to the bytes it measured itself, never to a sha an agent
hands over.

Two things are not a change for B to review:
  * the framework's own render — SPEC_TRACKING.md's Status column, refreshed
    from P3 — is carried forward on the approval as a recorded framework
    write (`amend-sab` is not: an architecture amendment is the project's
    decision and is reviewed);
  * an approval written before review binding existed: nothing says what B
    saw, so it is recorded, not used to block a phase that closed long ago.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from core.quality_gate.agent_b_approvals import (
    file_sha256,
    record_framework_write,
    stale_approvals_before,
)

REPO = Path(__file__).resolve().parents[1]


def _git(proj: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(proj), *args], capture_output=True,
                          text=True, check=True).stdout


def _project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / "01-requirements").mkdir(parents=True)
    (proj / "02-architecture").mkdir()
    (proj / ".methodology" / "agent_b_approvals").mkdir(parents=True)
    _git(proj.parent, "init", "-q", str(proj))
    _git(proj, "config", "user.email", "t@example.com")
    _git(proj, "config", "user.name", "t")
    (proj / "01-requirements" / "SRS.md").write_text("# SRS\n\nAC-1.1 returns an id\n", encoding="utf-8")
    (proj / "02-architecture" / "SAD.md").write_text("# SAD\n\nlayers: api > service\n", encoding="utf-8")
    for did, rel in (("SRS.md", "01-requirements/SRS.md"), ("SAD.md", "02-architecture/SAD.md")):
        record = {"fr": did, "review_status": "APPROVE", "reason": "x" * 60,
                  "citations": [f"{rel}:1"], "docs_embedded": ["SPEC.md"],
                  "reviewed_sha256": file_sha256(proj / rel)}
        (proj / ".methodology" / "agent_b_approvals" / f"{did}.json").write_text(json.dumps(record))
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", "reviewed")
    return proj


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(REPO / "harness_cli.py"), *args],
                          capture_output=True, text=True, cwd=str(REPO))


def test_a_closed_phase_document_edited_later_is_named(tmp_path):
    proj = _project(tmp_path)
    assert stale_approvals_before(proj, 3) == []
    sad = proj / "02-architecture" / "SAD.md"
    sad.write_text(sad.read_text() + "high_risk_modules: []\n", encoding="utf-8")
    assert [r["id"] for r in stale_approvals_before(proj, 3)] == ["SAD.md"]
    # The phase that owns it is still the current phase at P2: not "before".
    assert stale_approvals_before(proj, 2) == []


def test_an_approval_from_before_binding_does_not_block_a_closed_phase(tmp_path):
    proj = _project(tmp_path)
    path = proj / ".methodology" / "agent_b_approvals" / "SRS.md.json"
    data = json.loads(path.read_text())
    data.pop("reviewed_sha256")
    path.write_text(json.dumps(data))
    (proj / "01-requirements" / "SRS.md").write_text("# SRS\n\nchanged\n", encoding="utf-8")
    assert stale_approvals_before(proj, 3) == []


def test_the_frameworks_own_rewrite_is_carried_forward(tmp_path):
    proj = _project(tmp_path)
    srs = proj / "01-requirements" / "SRS.md"
    before = file_sha256(srs)
    srs.write_text("# SRS\n\nAC-1.1 returns an id (status refreshed)\n", encoding="utf-8")
    record_framework_write(proj, "01-requirements/SRS.md", before, file_sha256(srs), "test-render")
    assert stale_approvals_before(proj, 3) == []
    srs.write_text(srs.read_text() + "hand edit\n", encoding="utf-8")
    assert [r["id"] for r in stale_approvals_before(proj, 3)] == ["SRS.md"]


def test_a_framework_write_does_not_launder_an_earlier_hand_edit(tmp_path):
    proj = _project(tmp_path)
    srs = proj / "01-requirements" / "SRS.md"
    srs.write_text("# SRS\n\nhand edit first\n", encoding="utf-8")
    edited = file_sha256(srs)
    srs.write_text("# SRS\n\nhand edit first, then rendered\n", encoding="utf-8")
    record_framework_write(proj, "01-requirements/SRS.md", edited, file_sha256(srs), "test-render")
    assert [r["id"] for r in stale_approvals_before(proj, 3)] == ["SRS.md"]


def test_the_cli_lists_closed_phase_changes(tmp_path):
    proj = _project(tmp_path)
    (proj / "02-architecture" / "SAD.md").write_text("# SAD\n\nchanged\n", encoding="utf-8")
    out = _cli("stale-approvals", "--project", str(proj), "--before", "3")
    assert out.returncode == 0, out.stderr
    assert 'STALE: ["SAD.md"]' in out.stdout


def test_a_change_review_is_bound_to_what_python_measured(tmp_path):
    proj = _project(tmp_path)
    sad = proj / "02-architecture" / "SAD.md"
    sad.write_text("# SAD\n\nlayers: api > service > repository\n", encoding="utf-8")
    ctx = _cli("review-change-context", "--project", str(proj), "--id", "SAD.md")
    assert ctx.returncode == 0, ctx.stderr
    context = json.loads((proj / ".sessi-work" / "review_ctx" / "SAD.md.json").read_text())
    assert context["previous_version_found"] is True
    assert "+layers: api > service > repository" in context["diff"]

    review = {"review_status": "APPROVE", "reason": "the added layer is consistent with SRS AC-1.1 and SPEC",
              "citations": ["02-architecture/SAD.md:3"], "gaps": []}
    bound = _cli("write-approval", "--project", str(proj), "--fr-id", "SAD.md",
                 "--bind-context", "--json", json.dumps(review))
    assert bound.returncode == 0, bound.stderr
    assert stale_approvals_before(proj, 3) == []
    record = json.loads((proj / ".methodology" / "agent_b_approvals" / "SAD.md.json").read_text())
    assert record["change_reviews"][-1]["to"] == file_sha256(sad)
    assert record["docs_embedded"] == ["SPEC.md"], "the original review's record is kept"


def test_a_bind_refuses_a_file_that_changed_after_its_context(tmp_path):
    proj = _project(tmp_path)
    sad = proj / "02-architecture" / "SAD.md"
    sad.write_text("# SAD\n\nfirst change\n", encoding="utf-8")
    _cli("review-change-context", "--project", str(proj), "--id", "SAD.md")
    sad.write_text("# SAD\n\nsecond change nobody reviewed\n", encoding="utf-8")
    review = {"review_status": "APPROVE", "reason": "y" * 60, "citations": ["02-architecture/SAD.md:1"]}
    bound = _cli("write-approval", "--project", str(proj), "--fr-id", "SAD.md",
                 "--bind-context", "--json", json.dumps(review))
    assert bound.returncode != 0
    assert [r["id"] for r in stale_approvals_before(proj, 3)] == ["SAD.md"]


def test_the_advance_refuses_while_a_closed_phase_change_is_unreviewed(tmp_path):
    from cli.advance_prechecks import _precheck_reviewed_deliverables_unchanged
    from cli.exit_codes import EX_ADVANCE_REVIEWED_DELIVERABLE_CHANGED

    proj = _project(tmp_path)
    assert _precheck_reviewed_deliverables_unchanged(3, proj) is None
    (proj / "02-architecture" / "SAD.md").write_text("# SAD\n\nchanged\n", encoding="utf-8")
    assert _precheck_reviewed_deliverables_unchanged(3, proj) == EX_ADVANCE_REVIEWED_DELIVERABLE_CHANGED


def test_a_retired_approval_id_is_not_asked_about(tmp_path):
    """TRACEABILITY_MATRIX.md is rendered since 站7; an approval file left from
    before must not send the framework's render to Agent B."""
    proj = _project(tmp_path)
    matrix = proj / "01-requirements" / "TRACEABILITY_MATRIX.md"
    matrix.write_text("# Traceability Matrix\n", encoding="utf-8")
    record = {"review_status": "APPROVE", "reviewed_sha256": "0" * 64}
    (proj / ".methodology" / "agent_b_approvals" / "TRACEABILITY_MATRIX.md.json").write_text(json.dumps(record))
    assert stale_approvals_before(proj, 3) == []


def test_the_spec_tracking_status_render_is_not_sent_to_review(tmp_path):
    """The real framework writer: SPEC_TRACKING.md's Status column, refreshed
    from P3 by write_spec_tracking, carries the P1 approval forward."""
    from core.requirement_traceability import TraceStatus
    from core.traceability.spec_tracking_render import write_spec_tracking

    proj = _project(tmp_path)
    st = proj / "01-requirements" / "SPEC_TRACKING.md"
    st.write_text(
        "# Spec Tracking\n\n## Specification Status\n\n"
        "| FR ID | Spec Description | Intent Class | Decision Framework | Status | Notes |\n"
        "|-------|-----------------|--------------|-------------------|--------|-------|\n"
        "| FR-01 | submit | CRUD | fw | DRAFT | n |\n", encoding="utf-8")
    record = {"fr": "SPEC_TRACKING.md", "review_status": "APPROVE", "reason": "z" * 60,
              "citations": ["01-requirements/SPEC_TRACKING.md:1"], "docs_embedded": ["SRS.md"],
              "reviewed_sha256": file_sha256(st)}
    (proj / ".methodology" / "agent_b_approvals" / "SPEC_TRACKING.md.json").write_text(json.dumps(record))

    class _Req:
        status = TraceStatus.VERIFIED

    class _RT:
        requirements = {"FR-01": _Req()}

    write_spec_tracking(proj, _RT())
    assert "VERIFIED" in st.read_text()
    assert stale_approvals_before(proj, 3) == []


def test_advance_phase_asks_about_closed_phases():
    from tests.support.pipeline import pipeline_source

    src = pipeline_source("cli/phase_cmds.py", "_advance_prechecks", helper_prefix="_precheck_")
    assert "_precheck_reviewed_deliverables_unchanged(completed_phase, project)" in src


def test_every_advance_loop_after_p1_reviews_changes_first():
    from scripts.workflowgen.generate_workflows import generate

    for phase in range(2, 9):
        js = generate(phase)
        assert f"reviewChangedDeliverables({phase}, {{ phase: '" in js, phase
    assert "reviewChangedDeliverables(" not in generate(1)
