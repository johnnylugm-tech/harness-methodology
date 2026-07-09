"""Cross-artifact consistency gates — machine-catch P1/P2 agent hallucinations.

Two decidable checks (no LLM), upgrading prompt rules the audit found agents
still violate:

  * check_forward_refs (audit issue 3): a `NN-stage/FILE.md` reference in a
    P1/P2 artifact must name a real framework deliverable for that stage —
    catches invented filenames (02-architecture/ARCHITECTURE.md when the P2
    deliverable is SAD.md), which otherwise 404 any downstream automation.

  * check_nfr_adr_coverage (audit issue 2): every SRS NFR must appear in
    ADR.md's traceability TABLE — catches an NFR silently dropped from the
    table (NFR-06 was dropped while still appearing in a prose roll-up, so a
    "mentioned anywhere" check would miss it).

Same shape as spec_alignment.py: decidable, ingestion-agnostic, never guesses
(an unrecognizable ADR table → needs_review, not a false verdict).
"""

from __future__ import annotations

import re
from pathlib import Path

from core.quality_gate import Violation
from core.quality_gate.legal_artifacts import LEGAL_ARTIFACTS
from core.traceability.scanner import extract_nfr_ids_from_srs
from core.utils.project_layout import ProjectLayout

__all__ = ["check_forward_refs", "check_nfr_adr_coverage"]

# Legal deliverable filenames per stage directory (forward-reference whitelist).
# Authoritative list lives in `core.quality_gate.legal_artifacts` (single source
# of truth shared with `harness_cli._PHASE_DELIVERABLES` via the same module).
# See legal_artifacts.py for the rationale and the prior DRY violation that
# motivated the consolidation.

# `02-architecture/adr/ADR.md` / `./01-requirements/SRS.md` — stage dir, optional
# sub-dirs, filename. The filename class excludes '/' so it is the last segment.
_REF = re.compile(r"(0\d-[a-z]+)/(?:[a-z0-9_]+/)*([A-Za-z_][A-Za-z0-9_.-]*\.(?:md|ya?ml))")
_NFR = re.compile(r"NFR-(\d+)")


def _adr_path(project: Path) -> Path:
    """ADR.md — under the canonical `adr/` sub-dir if present, else directly in
    the architecture dir. Uses ProjectLayout (no hand-built phase-dir path)."""
    arch = ProjectLayout(project).phase2_architecture_dir
    sub = arch / "adr" / "ADR.md"
    return sub if sub.exists() else arch / "ADR.md"


def _scan_files(project: Path) -> list[Path]:
    layout = ProjectLayout(project)
    cands = [
        layout.traceability_matrix_path,
        layout.spec_tracking_path,
        layout.sad_path,
        _adr_path(project),
    ]
    return [p for p in cands if p.exists()]


def check_forward_refs(project: Path) -> list[Violation]:
    project = Path(project)
    violations: list[Violation] = []
    seen: set[tuple[str, str, str]] = set()
    for path in _scan_files(project):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in _REF.finditer(text):
            stage_dir, filename = m.group(1), m.group(2)
            legal = LEGAL_ARTIFACTS.get(stage_dir)
            if legal is None or filename in legal:
                continue  # unknown stage dir (don't second-guess) / legal name
            key = (path.name, stage_dir, filename)
            if key in seen:
                continue
            seen.add(key)
            violations.append(Violation(
                check_type="illegal_forward_ref", rule_id=filename, severity="error",
                message=(f"{path.name} references {stage_dir}/{filename}, which is not a "
                         f"framework deliverable for {stage_dir} (legal: "
                         f"{', '.join(sorted(legal))}) — invented filename / broken forward "
                         f"reference (any automation indexing it 404s)")))
    return violations


def _adr_table_nfrs(text: str) -> set[str] | None:
    """NFR-IDs listed in ADR.md's traceability TABLE, or None if no such table.

    A table = consecutive `|`-rows whose header contains 'adr' and ('served' or
    'fr'); NFRs are collected from the data rows only, so a prose roll-up
    outside the table does not count as coverage.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        s = line.strip().lower()
        if s.startswith("|") and "adr" in s and ("served" in s or "fr" in s):
            nfrs: set[str] = set()
            for row in lines[i + 1:]:
                t = row.strip()
                if not t.startswith("|"):
                    break
                if set(t) <= set("|-: "):
                    continue  # separator row
                nfrs.update(f"NFR-{int(n):02d}" for n in _NFR.findall(t))
            return nfrs
    return None


def check_nfr_adr_coverage(project: Path) -> list[Violation]:
    project = Path(project)
    srs_nfrs = {
        n for n in extract_nfr_ids_from_srs(ProjectLayout(project).srs_path)
        if n != "NFR-99"
    }
    if not srs_nfrs:
        return []
    adr = _adr_path(project)
    if not adr.exists():
        return [Violation(
            check_type="adr_missing", rule_id="ADR", severity="error",
            message=f"SRS.md declares {len(srs_nfrs)} NFR(s) but ADR.md is missing")]
    table_nfrs = _adr_table_nfrs(adr.read_text(encoding="utf-8", errors="replace"))
    if table_nfrs is None:
        return [Violation(
            check_type="adr_table_missing", rule_id="ADR", severity="info",
            message=("ADR.md has no recognizable traceability table (a header row with "
                     "'ADR' + 'served'/'FR') — NFR coverage cannot be verified (needs_review)"))]
    violations: list[Violation] = []
    for nfr in sorted(srs_nfrs - table_nfrs):
        violations.append(Violation(
            check_type="nfr_not_traced", rule_id=nfr, severity="error",
            message=(f"{nfr} is declared in SRS.md but absent from ADR.md's traceability "
                     f"table (a prose mention does not count) — NFR→ADR coverage gap")))
    return violations
