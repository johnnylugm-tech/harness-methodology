"""PRD/canonical_spec ↔ SRS alignment gate (Direction A).

Fills the one boundary the pipeline never machine-checks: the front edge
canonical_spec → SRS.md. `phase_artifact_enforcer` records that the PRD is
"external, not checked here", and every *downstream* boundary is already
covered elsewhere:
  * preflight_fr_spec_consistency — SAD ↔ TEST_SPEC FR-set parity
  * preflight_traceability (4a/4b/4c) — SRS/SAD → code / test / NFR coverage

This module mechanically enforces the INGESTION MODE prompt rule
R-CANONICAL-INTERP-001 ("100% transcribe … cite <canonical-line>") that today
only Agent A/B (LLM) uphold. It is decidable and ingestion-mode-only:
elicitation mode (no canonical_spec) has no ground truth to check against, so
the check returns empty (N/A) rather than fabricating a verdict — matching the
red_assertion engine's "does not guess" contract.

FR-IDs are compared as SETS over the two documents. Only *structural* FR forms
are read (never prose mentions), so a stray "FR-01" in a sentence cannot create
a phantom requirement.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.quality_gate import Violation
from core.quality_gate.parsers import SRS_SUBSECTION_PREFIX
from core.utils.project_layout import ProjectLayout

__all__ = ["check_spec_alignment", "resolve_canonical_spec"]

# Structural FR-ID forms — never a bare prose mention:
#   heading   `### FR-01: ...`          (canonical SPEC.md / SRS flat layout)
#   heading   `### 3.1 FR-01 ...`       (SRS subsection-numbered layout:
#              SRS_SUBSECTION_PREFIX before FR-NN — the same SRS that uses
#              §3 Functional Requirements / §3.1 FR-01 / §3.2 FR-02 TOC
#              convention; without it the gate false-positives every FR as
#              "dropped" on a structurally complete SRS)
#   table id  `| FR-01 | ...`            (SRS §2 table layout; `\b` after
#              digit also catches `| FR-01.AC1 |` style AC rows)
#   json id   `"id": "FR-01"`            (SRS §7 FR:START machine block)
_FR_HEADING = re.compile(
    r"^#{1,6}\s*" + SRS_SUBSECTION_PREFIX + r"FR-(\d+)\b", re.MULTILINE)
_FR_TABLE = re.compile(r"^\|\s*FR-(\d+)\b", re.MULTILINE)
_FR_JSON = re.compile(r'"id"\s*:\s*"FR-(\d+)"')
# A deferral is a structure, not a sentence (Round 69 站5).
#
# This module's docstring states the contract every pattern above honours:
# "Only *structural* FR forms are read (never prose mentions), so a stray
# 'FR-01' in a sentence cannot create a phantom requirement." `_FR_DEFERRED`
# was the one exception — an unanchored scan of the whole file. While it only
# fed the dropped-requirement branch that was already too wide; 6181d52
# subtracted the same set on the INVENTED axis, where it does something
# stronger: an SRS with a complete `### FR-12:` section is silenced as long as
# the two words `FR-12-deferred` appear anywhere in the file, including in a
# sentence explaining that FR-12 was NOT deferred.
#
# The three forms below are the ones the corpus writes: heading (taskq-new
# SRS.md:1402), table row (taskq SRS.md:599-603), bold bullet (taskq-super
# SRS.md:1085). Restricting to them changes the verdict on none of the nine
# corpus projects.
#
# (?<!N): "NFR-06-deferred" must not phantom-excuse FR-06 from front-edge
# coverage (parity-locked by tests/test_fr_token_parity.py).
_FR_DEFERRED_FORMS = (
    re.compile(r"^#{1,6}\s*" + SRS_SUBSECTION_PREFIX
               + r"(?<!N)FR-(\d+)-deferred\b", re.MULTILINE),
    re.compile(r"^\|\s*(?<!N)FR-(\d+)-deferred\b", re.MULTILINE),
    re.compile(r"^\s*[-*]\s*\*{0,2}\s*(?<!N)FR-(\d+)-deferred\b", re.MULTILINE),
)

# canonical_spec declaration in PROJECT_BRIEF.md — two accepted layouts, same
# as the P1 fallback in cli/project_cmds.py (kept in sync; ~4 lines, replicated
# rather than shared to avoid a cli→core import edge).
#
# Both forms capture the path token only (\S+) so trailing metadata like
# `SPEC.md (v3.0.0, 2026-07-04, 5 FR / 6 NFR / 8 env vars)` is stripped
# rather than treated as part of the file path. Without this the heading
# form regressed silently when PROJECT_BRIEF.md added a version annotation
# after the path (see test_heading_form_with_trailing_metadata).
_CANON_INLINE = re.compile(r"^\s*canonical_spec\s*:\s*(\S+)\s*$", re.MULTILINE)
_CANON_HEADING = re.compile(r"^##\s*canonical_spec\s*$\n+(\S+)", re.MULTILINE)


def _fid(num: str) -> str:
    """Zero-pad an FR number so `FR-1` and `FR-01` compare equal."""
    return f"FR-{int(num):02d}"


def _structural_fr_ids(text: str) -> set[str]:
    ids: set[str] = set()
    for pat in (_FR_HEADING, _FR_TABLE, _FR_JSON):
        ids.update(_fid(m) for m in pat.findall(text))
    return ids


def _deferred_fr_ids(text: str) -> set[str]:
    ids: set[str] = set()
    for pat in _FR_DEFERRED_FORMS:
        ids.update(_fid(m) for m in pat.findall(text))
    return ids


def resolve_canonical_spec(project: Path) -> str | None:
    """Return the canonical_spec relative path declared in PROJECT_BRIEF.md, or
    None when none is declared (elicitation mode)."""
    brief = project / "PROJECT_BRIEF.md"
    if not brief.exists():
        return None
    text = brief.read_text(encoding="utf-8", errors="replace")
    m = _CANON_INLINE.search(text) or _CANON_HEADING.search(text)
    return m.group(1).strip() if m else None


def check_spec_alignment(project: Path) -> list[Violation]:
    """Return Violations for canonical_spec ↔ SRS FR-set divergence.

    error == blocking defect (dropped / invented requirement, or a broken
    ingestion setup); info == needs_review (canonical not mechanically
    enumerable). Empty list == aligned, or elicitation mode (N/A).
    """
    project = Path(project)
    canonical_rel = resolve_canonical_spec(project)
    if canonical_rel is None:
        return []  # elicitation mode — no ground truth, N/A

    canonical_path = Path(canonical_rel)
    if not canonical_path.is_absolute():
        canonical_path = project / canonical_rel
    if not canonical_path.exists():
        return [Violation(
            check_type="canonical_missing", rule_id="SA", severity="error",
            message=(f"PROJECT_BRIEF.md declares canonical_spec {canonical_rel!r} "
                     f"but the file does not exist at {canonical_path}"))]

    canonical_frs = _structural_fr_ids(
        canonical_path.read_text(encoding="utf-8", errors="replace"))
    if not canonical_frs:
        return [Violation(
            check_type="canonical_unstructured", rule_id="SA", severity="info",
            message=("canonical_spec has no `### FR-NN` requirement anchors — "
                     "PRD→SRS coverage cannot be mechanically verified; Agent B "
                     "must confirm fidelity (needs_review)"))]

    srs_path = ProjectLayout(project).srs_path
    if not srs_path.exists():
        return [Violation(
            check_type="srs_missing", rule_id="SA", severity="error",
            message=(f"canonical_spec declares {len(canonical_frs)} FR(s) but "
                     f"SRS.md is missing at {srs_path} — ingestion incomplete"))]

    srs_text = srs_path.read_text(encoding="utf-8", errors="replace")
    srs_frs = _structural_fr_ids(srs_text)
    srs_deferred = _deferred_fr_ids(srs_text)

    violations: list[Violation] = []
    for fid in sorted(canonical_frs - srs_frs - srs_deferred):
        violations.append(Violation(
            check_type="dropped_requirement", rule_id=fid, severity="error",
            message=(f"canonical_spec declares {fid} but SRS.md has no such FR "
                     f"(dropped requirement — ingestion must transcribe 100% or "
                     f"record it as {fid}-deferred / NFR-99)")))
    # `### FR-99-deferred: ...` is the framework-blessed way to record an
    # explicit out-of-scope deferral; the dropped-requirement branch already
    # subtracts `srs_deferred` so a heading like that is invisible on the
    # "canonical declared but SRS missing" axis. Symmetric parity on the
    # "SRS declares but canonical doesn't" axis was missing — `FR-99-deferred`
    # would otherwise read as an invented requirement. Subtract `srs_deferred`
    # here too.
    for fid in sorted(srs_frs - canonical_frs - srs_deferred):
        violations.append(Violation(
            check_type="invented_requirement", rule_id=fid, severity="error",
            message=(f"SRS.md declares {fid} with no counterpart in canonical_spec "
                     f"(invented requirement — every FR must trace to a canonical "
                     f"source clause)")))
    return violations
