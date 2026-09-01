"""Tests for the canonical spec (SPEC.md) ↔ SRS alignment gate (Direction A).

check_spec_alignment fills the ONE boundary the pipeline never machine-checks:
the front edge PRD/SPEC.md → SRS (phase_artifact_enforcer.py:77 states
"Phase 1 input is the user-provided PRD (external, not checked here)"). It is
distinct from:
  * preflight_fr_spec_consistency  — SAD ↔ TEST_SPEC FR-set parity
  * preflight_traceability (4a/4b/4c) — SRS/SAD → code/test/NFR coverage

Behavioural contract (tests behaviour, not implementation):
  * both documents present:
      - a canonical FR absent from SRS  → error (dropped requirement)
      - an SRS FR absent from canonical → error (invented requirement)
      - a canonical FR the SRS records as `FR-NN-deferred` → NOT dropped
  * canonical with no `### FR-NN` anchors → info needs_review, never a false error
  * SPEC.md present, SRS.md absent → error (ingestion incomplete)
  * SPEC.md absent, SRS.md present → error (the SRS has no source) — Round 84
  * neither present → [] (Phase 1 has produced nothing; not a mode, and not a
    finding — see `test_neither_document_present_is_not_an_accusation`)
"""

from __future__ import annotations

from pathlib import Path

from core.quality_gate.spec_alignment import check_spec_alignment
from core.utils.project_layout import ProjectLayout


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _project(tmp_path: Path, *, canonical: str | None, srs: str | None) -> Path:
    """Build a minimal project. canonical=None → no SPEC.md on disk."""
    if canonical is not None:
        _write(tmp_path / "SPEC.md", canonical)
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


def test_neither_document_present_is_not_an_accusation(tmp_path: Path) -> None:
    """Round 84: no SPEC.md and no SRS.md means Phase 1 has produced nothing.

    This is the only N/A row left after the mode switch retired, and it exists
    for a measured reason: this framework repo has neither file and its
    pre-push hook runs `run-phase --phase 1` on itself. Naming a defect here
    would accuse it of losing a file it never had.
    """
    assert check_spec_alignment(_project(tmp_path, canonical=None, srs=None)) == []


def test_srs_without_canonical_spec_blocks(tmp_path: Path) -> None:
    """Round 84: the row the old mode switch read as good news.

    With `canonical_spec` living in PROJECT_BRIEF.md, deleting that file (or
    the spec it pointed at) silently downgraded the project to elicitation and
    this gate returned [] on an SRS whose requirements had no source at all.
    """
    proj = _project(tmp_path, canonical=None, srs="### FR-01: login\n")
    errors = [v for v in check_spec_alignment(proj) if v.severity == "error"]
    assert [v.check_type for v in errors] == ["canonical_missing"]


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


def test_deferred_requirement_not_invented(tmp_path: Path) -> None:
    """Symmetric to test_deferred_requirement_not_dropped.

    The SRS carries `### FR-99-deferred:` — explicit deferral of a
    requirement the canonical_spec never declared. The invented-requirement
    check must NOT call this out as a fabricated FR; the previous code only
    subtracted `srs_deferred` on the dropped-requirement axis and missed
    this case, surfacing on taskq-new 2026-08-22 as a blocking
    spec_alignment divergence for `FR-99`.
    """
    proj = _project(
        tmp_path,
        canonical="### FR-01: login\n",
        srs=("### FR-01: login\n\n"
             "### FR-99-deferred: SPEC §6 (folder structure) "
             "was explicitly removed\n"),
    )
    assert [v for v in check_spec_alignment(proj) if v.severity == "error"] == []


def test_missing_srs_blocks_in_ingestion(tmp_path: Path) -> None:
    proj = _project(tmp_path, canonical=_CANON_3, srs=None)
    vs = check_spec_alignment(proj)
    assert any(v.severity == "error" for v in vs)


def test_canonical_spec_is_project_root_spec_md(tmp_path: Path) -> None:
    """The location is `ProjectLayout.spec_path` and nothing else reads it.

    Round 84 replaced a PROJECT_BRIEF.md field with the constant the framework
    already stated five other ways. A SPEC.md anywhere but the project root is
    not the canonical spec — proving the path is not merely "some file named
    SPEC.md that happens to be nearby".
    """
    _write(tmp_path / "01-requirements" / "SPEC.md", _CANON_3)  # wrong location
    _write(ProjectLayout(tmp_path).srs_path, _CANON_3)
    errors = [v for v in check_spec_alignment(tmp_path) if v.severity == "error"]
    assert [v.check_type for v in errors] == ["canonical_missing"]

    _write(ProjectLayout(tmp_path).spec_path, _CANON_3)  # right location
    assert check_spec_alignment(tmp_path) == []


def test_zero_pad_normalisation(tmp_path: Path) -> None:
    # canonical FR-1 vs SRS FR-01 must be treated as the same requirement.
    proj = _project(
        tmp_path,
        canonical="### FR-1: login\n### FR-2: logout\n",
        srs="### FR-01: login\n### FR-02: logout\n",
    )
    assert check_spec_alignment(proj) == []


def test_srs_section_numbered_fr_headings_aligned(tmp_path: Path) -> None:
    """Real SRS layouts number functional requirements under a §3 sub-section
    (`### 3.1 FR-01`, `### 3.2 FR-02`, etc. — typical TOC convention where
    §3 = Functional Requirements and the FR is at §3.N). The gate must read
    both this form and the canonical `### FR-NN:` form, otherwise a
    structurally complete SRS (such as this repo's own 01-requirements/SRS.md)
    is false-positived as "dropped" for every FR."""
    proj = _project(
        tmp_path,
        canonical="### FR-01: login\n### FR-02: logout\n### FR-03: refresh\n",
        srs=("### 3.1 FR-01 login\n\n"
             "### 3.2 FR-02 logout\n\n"
             "### 3.3 FR-03 refresh\n"),
    )
    assert check_spec_alignment(proj) == []


def test_srs_section_numbered_fr_dropped_still_blocks(tmp_path: Path) -> None:
    """Subsection-numbered SRS, FR-02 genuinely dropped — must still block
    (the heading-form extension must not silently pass a real gap)."""
    proj = _project(
        tmp_path,
        canonical="### FR-01: login\n### FR-02: logout\n### FR-03: refresh\n",
        srs="### 3.1 FR-01 login\n\n### 3.3 FR-03 refresh\n",
    )
    errors = [v for v in check_spec_alignment(proj) if v.severity == "error"]
    assert len(errors) == 1
    assert "FR-02" in errors[0].message and "dropped" in errors[0].message.lower()


def test_srs_three_level_section_numbered_fr_aligned(tmp_path: Path) -> None:
    """Even deeper subsection numbers (`### 4.2.1 FR-NN ...`) must read — the
    regex prefix accepts arbitrarily nested subsection IDs (one or more
    decimal-numbered levels), not just one-level `3.N`."""
    proj = _project(
        tmp_path,
        canonical="### FR-01: x\n### FR-02: y\n",
        srs="### 3.1.1 FR-01 x\n\n### 4.2.3 FR-02 y\n",
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


def test_preflight_blocks_at_p2_when_the_srs_has_no_canonical_source(
    tmp_path: Path,
) -> None:
    """The preflight used to skip here, on the strength of a declaration.

    Round 84: it asks the checker and the checker reads disk, so an SRS whose
    canonical spec is gone blocks at P2 instead of being waved through as
    "elicitation mode".
    """
    proj = _project(tmp_path, canonical=None, srs="### FR-01: login\n")
    r = _hooks(proj, 2).preflight_spec_alignment()
    assert r["passed"] is False and r["blocking"] is True and r["errors"] == 1


def test_preflight_says_na_rather_than_covered_when_nothing_exists(
    tmp_path: Path, capsys
) -> None:
    """Empty violations has two causes; the hook must not report them alike.

    With neither document on disk the old wording ("SRS.md covers
    canonical_spec") was a statement about two files that were not there.
    """
    r = _hooks(_project(tmp_path, canonical=None, srs=None), 2).preflight_spec_alignment()
    assert r["passed"] is True and r["errors"] == 0
    out = capsys.readouterr().out
    assert "N/A" in out and "has not produced requirements yet" in out
    assert "covers the canonical spec" not in out


def test_spec_alignment_is_wired_into_preflight_all() -> None:
    """Composition guard: the gate must stay in the blocking aggregate so an
    agent cannot bypass it by advancing a phase (REGRESSION_GUARDS-pinned).

    Mechanism upgraded with the PREFLIGHT_CHECKS registry: membership in the
    registry IS composition — tests/test_preflight_registry.py proves
    _do_preflight_all runs exactly the registry."""
    from core.phase_hooks import PREFLIGHT_CHECKS

    assert ("spec_alignment", "preflight_spec_alignment") in PREFLIGHT_CHECKS, (
        "spec_alignment gate dropped from PREFLIGHT_CHECKS — the front-edge "
        "canonical↔SRS check would no longer block phase advance"
    )
