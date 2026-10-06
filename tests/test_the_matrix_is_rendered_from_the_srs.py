"""Round 114 站7 — TRACEABILITY_MATRIX.md has one owner, and its requirements come from the SRS.

Two findings on taskq-open, measured:

1. One file, two owners. At P1 an agent wrote ~470 lines of matrix and Agent B
   reviewed it; at the P3 exit `_regen_traceability_views` replaced it with a
   60-line render. The P1 review bought a document with a two-phase lifetime,
   its approval was stale by construction, the agent reverted the framework's
   render twice (d185fc4, 5fa5d92), and a project test was loosened to accept
   the render (a904f15). Every corpus project past P3 holds the render; every
   one before P3 holds an authored matrix the next advance discards. What the
   authored matrix planned — AC -> test case — TEST_INVENTORY.yaml already
   states (tc_id / ac / layer / test_function), so the matrix is a view.

2. The render's requirements came from SAD.md, code and tests — never from
   the SRS: `srs_section` was literally "SAD.md". Deleting FR-10 from a copy of
   taskq-open's SAD left check-artifact-consistency, check-spec-alignment and
   the P2 transition all green; and at P1, with no SAD, the render found two
   FRs (the template's). `spec_alignment.structural_fr_ids` — the existing
   single definition of "which FRs a document declares" — read on the 17
   corpus SRSs agrees with SAD and the gate-1 manifest on every finished
   project.

So the framework renders the matrix from P1 on, both builders take the
requirement universe from SRS + SAD, an SRS requirement missing from SAD is a
named gap, and the P1 matrix is no longer a reviewed deliverable.
"""

from __future__ import annotations

from pathlib import Path

_SRS = """\
# Software Requirements Specification

## 3. Functional Requirements

### FR-01: Submit a task
- **AC-1.1** returns an id

### FR-02: Run a task
- **AC-2.1** returns 202

### FR-03: Cancel a task
- **AC-3.1** returns 204
"""

_SAD = """\
# Software Architecture Document

| FR | Module |
|---|---|
| FR-01 | `pkg.api` |
| FR-02 | `pkg.service` |
"""


def _project(tmp_path: Path, *, sad: bool) -> Path:
    req = tmp_path / "01-requirements"
    req.mkdir(parents=True)
    (req / "SRS.md").write_text(_SRS, encoding="utf-8")
    if sad:
        arch = tmp_path / "02-architecture"
        arch.mkdir(parents=True)
        (arch / "SAD.md").write_text(_SAD, encoding="utf-8")
    return tmp_path


def test_at_p1_the_render_lists_every_srs_requirement(tmp_path):
    from scripts.build_traceability import build_traceability

    rt = build_traceability(_project(tmp_path, sad=False))
    assert sorted(rt.requirements) == ["FR-01", "FR-02", "FR-03"]
    assert {r.srs_section for r in rt.requirements.values()} == {"SRS.md"}
    assert {r.status.value for r in rt.requirements.values()} == {"pending"}


def test_an_srs_requirement_missing_from_the_sad_is_a_named_gap(tmp_path):
    from scripts.build_traceability import build_traceability

    rt = build_traceability(_project(tmp_path, sad=True))
    missing = rt.verify_completeness()["missing_mappings"]
    assert missing["fr_without_design"] == ["FR-03"]
    assert missing["fr_without_srs"] == []


def test_both_builders_answer_from_the_same_universe(tmp_path):
    from core.traceability.scanner import check_traceability
    from scripts.build_traceability import build_traceability

    proj = _project(tmp_path, sad=True)
    rt_render = build_traceability(proj)
    rt_gate, _ = check_traceability(proj)
    assert sorted(rt_render.requirements) == sorted(rt_gate.requirements)
    assert {k: v.srs_section for k, v in rt_render.requirements.items()} == \
        {k: v.srs_section for k, v in rt_gate.requirements.items()}


def test_the_rendered_view_names_the_design_gap(tmp_path):
    from scripts.build_traceability import build_traceability, generate_markdown_matrix

    proj = _project(tmp_path, sad=True)
    out = proj / "01-requirements" / "TRACEABILITY_MATRIX.md"
    generate_markdown_matrix(build_traceability(proj), out)
    text = out.read_text(encoding="utf-8")
    assert "FR without design" in text and "FR-03" in text


def test_the_advance_renders_the_matrix_from_phase_1(tmp_path):
    from cli.advance_prechecks import _precheck_early_stage_pass

    proj = _project(tmp_path, sad=False)
    _precheck_early_stage_pass(proj / "00-summary" / "Phase1_STAGE_PASS.md", 1, proj)
    matrix = proj / "01-requirements" / "TRACEABILITY_MATRIX.md"
    assert matrix.is_file() and "FR-03" in matrix.read_text(encoding="utf-8")


def test_the_p1_matrix_is_not_a_reviewed_deliverable():
    from core.quality_gate.legal_artifacts import PHASE_DELIVERABLE_PATHS, PHASE_DELIVERABLES

    assert "TRACEABILITY_MATRIX.md" not in PHASE_DELIVERABLES[1]
    # Still a P1 deliverable that has to exist — the framework writes it.
    assert PHASE_DELIVERABLE_PATHS[1]["TRACEABILITY_MATRIX.md"] == "01-requirements/TRACEABILITY_MATRIX.md"
