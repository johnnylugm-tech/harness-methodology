"""Tests for the PRD/canonical_spec ↔ SRS alignment gate (Direction A).

check_spec_alignment fills the ONE boundary the pipeline never machine-checks:
the front edge PRD/canonical_spec → SRS (phase_artifact_enforcer.py:77 states
"Phase 1 input is the user-provided PRD (external, not checked here)"). It is
distinct from:
  * preflight_fr_spec_consistency  — SAD ↔ TEST_SPEC FR-set parity
  * preflight_traceability (4a/4b/4c) — SRS/SAD → code/test/NFR coverage

Behavioural contract (tests behaviour, not implementation):
  * ingestion mode (PROJECT_BRIEF declares canonical_spec):
      - a canonical FR absent from SRS  → error (dropped requirement)
      - an SRS FR absent from canonical → error (invented requirement)
      - a canonical FR the SRS records as `FR-NN-deferred` → NOT dropped
  * elicitation mode (no canonical_spec) → no ground truth → [] (N/A)
  * canonical with no `### FR-NN` anchors → info needs_review, never a false error
  * declared canonical_spec file missing → error (fail-closed)
"""

from __future__ import annotations

from pathlib import Path

from core.quality_gate.spec_alignment import check_spec_alignment
from core.utils.project_layout import ProjectLayout


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _project(tmp_path: Path, *, canonical: str | None, srs: str | None) -> Path:
    """Build a minimal project. canonical=None → elicitation mode."""
    if canonical is not None:
        _write(tmp_path / "PROJECT_BRIEF.md", "canonical_spec: SPEC.md\n")
        _write(tmp_path / "SPEC.md", canonical)
    else:
        _write(tmp_path / "PROJECT_BRIEF.md", "# brief\nno canonical here\n")
    if srs is not None:
        _write(ProjectLayout(tmp_path).srs_path, srs)
    return tmp_path


_CANON_3 = "### FR-01: login\n### FR-02: logout\n### FR-03: refresh\n"


def test_dropped_requirement_blocks(tmp_path: Path) -> None:
    proj = _project(tmp_path, canonical=_CANON_3, srs="### FR-01: login\n### FR-03: refresh\n")
    vs = check_spec_alignment(proj)
    errors = [v for v in vs if v.severity == "error"]
    assert len(errors) == 1
    assert "FR-02" in errors[0].message
    assert "dropped" in errors[0].message.lower()


def test_invented_requirement_blocks(tmp_path: Path) -> None:
    proj = _project(
        tmp_path,
        canonical="### FR-01: login\n### FR-02: logout\n",
        srs="### FR-01: login\n### FR-02: logout\n### FR-09: telepathy\n",
    )
    vs = check_spec_alignment(proj)
    errors = [v for v in vs if v.severity == "error"]
    assert len(errors) == 1
    assert "FR-09" in errors[0].message
    assert "invent" in errors[0].message.lower()


def test_clean_ingestion_passes(tmp_path: Path) -> None:
    proj = _project(
        tmp_path,
        canonical="### FR-01: login\n### FR-02: logout\n",
        srs="### FR-01: login\n### FR-02: logout\n",
    )
    assert check_spec_alignment(proj) == []


def test_elicitation_mode_is_not_applicable(tmp_path: Path) -> None:
    # No canonical_spec declared → nothing to check fidelity against.
    proj = _project(tmp_path, canonical=None, srs="### FR-01: login\n")
    assert check_spec_alignment(proj) == []


def test_unstructured_canonical_needs_review(tmp_path: Path) -> None:
    proj = _project(
        tmp_path,
        canonical="# Product PRD\n\nThe system should let users log in and out.\n",
        srs="### FR-01: login\n",
    )
    vs = check_spec_alignment(proj)
    assert [v.severity for v in vs] == ["info"]
    assert "review" in vs[0].message.lower()


def test_deferred_requirement_not_dropped(tmp_path: Path) -> None:
    # Canonical FR-02 is TBD; SRS records it as FR-02-deferred per the
    # NFR-99 / FR-XX-deferred ingestion convention → must NOT count as dropped.
    proj = _project(
        tmp_path,
        canonical="### FR-01: login\n### FR-02: logout (TBD)\n",
        srs="### FR-01: login\n\n- FR-02-deferred: logout pending stakeholder decision\n",
    )
    assert [v for v in check_spec_alignment(proj) if v.severity == "error"] == []


def test_missing_canonical_file_blocks(tmp_path: Path) -> None:
    _write(tmp_path / "PROJECT_BRIEF.md", "canonical_spec: SPEC.md\n")
    _write(ProjectLayout(tmp_path).srs_path, "### FR-01: login\n")
    vs = check_spec_alignment(tmp_path)
    errors = [v for v in vs if v.severity == "error"]
    assert len(errors) == 1
    assert "canonical" in errors[0].message.lower()


def test_missing_srs_blocks_in_ingestion(tmp_path: Path) -> None:
    proj = _project(tmp_path, canonical=_CANON_3, srs=None)
    vs = check_spec_alignment(proj)
    assert any(v.severity == "error" for v in vs)


def test_zero_pad_normalisation(tmp_path: Path) -> None:
    # canonical FR-1 vs SRS FR-01 must be treated as the same requirement.
    proj = _project(
        tmp_path,
        canonical="### FR-1: login\n### FR-2: logout\n",
        srs="### FR-01: login\n### FR-02: logout\n",
    )
    assert check_spec_alignment(proj) == []


# ── preflight wiring (phase-gated blocking + composition guard) ──────────────


def _hooks(project: Path, phase: int):
    from core.phase_hooks import PhaseHooks

    return PhaseHooks(str(project), phase=phase, enable_kill_switch=False)


def test_preflight_informational_at_p1_blocking_at_p2(tmp_path: Path) -> None:
    # canonical FR-01/02/03, SRS only FR-01 → 2 dropped requirements.
    proj = _project(tmp_path, canonical=_CANON_3, srs="### FR-01: login\n")
    r1 = _hooks(proj, 1).preflight_spec_alignment()
    assert r1["passed"] is True and r1["errors"] == 2  # P1: still authoring
    r2 = _hooks(proj, 2).preflight_spec_alignment()
    assert r2["passed"] is False and r2["blocking"] is True and r2["errors"] == 2


def test_preflight_skips_in_elicitation_mode(tmp_path: Path) -> None:
    proj = _project(tmp_path, canonical=None, srs="### FR-01: login\n")
    r = _hooks(proj, 2).preflight_spec_alignment()
    assert r["passed"] is True and r.get("skipped") is True


def test_spec_alignment_is_wired_into_preflight_all() -> None:
    """Composition guard: the gate must stay in the blocking aggregate so an
    agent cannot bypass it by advancing a phase (REGRESSION_GUARDS-pinned)."""
    import inspect

    from core.phase_hooks import PhaseHooks

    src = inspect.getsource(PhaseHooks._do_preflight_all)
    assert "preflight_spec_alignment" in src, (
        "spec_alignment gate dropped from _do_preflight_all — the front-edge "
        "canonical↔SRS check would no longer block phase advance"
    )
