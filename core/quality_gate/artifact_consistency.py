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

  * check_module_fr_coverage: TRACEABILITY_MATRIX.md's own §3/§4 AC rows
    (Design column `` `module::func` `` citations + each FR/NFR subsection's
    trailing "**Linked Modules**: `module`" line) are the ground truth for
    which module owns which FR/NFR. TRACEABILITY_MATRIX.md's own §5.3
    reverse-coverage table must match that ground truth exactly (its own
    heading claims exhaustive coverage, so an omission is a self-contradiction
    — e.g. `taskq.store` cited under FR-05 by an AC row but absent from §5.3's
    `taskq.store` row). SPEC_TRACKING.md's narrower §5 Module Ownership table
    (explicitly scoped to "high-risk modules per C-11", not a completeness
    claim) is only checked for unbacked ownership claims — an FR/NFR the
    ground truth attributes exclusively to a *different* module (e.g.
    assigning FR-05 to `taskq.executor` when every FR-05 AC row only ever
    cites `taskq.cli`/`taskq.store`). Module *names* are project-specific
    (unlike FR-/NFR-IDs, a harness-wide structural convention), so this check
    never treats an external file (e.g. SPEC.md) as ground truth — only
    TRACEABILITY_MATRIX.md's own internally-established citations count.

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

__all__ = ["check_forward_refs", "check_nfr_adr_coverage", "check_module_fr_coverage"]

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


# ── module_fr_coverage: TRACEABILITY_MATRIX.md-internal ground truth ────────
# Heading line carrying an FR-NN/NFR-NN token anywhere after the leading `#`s
# (real headings are e.g. "### 3.1 FR-01 — ..." / "### 4.1 NFR-01 — ...", not
# immediately after the hashes) — marks the start of that requirement's
# subsection.
_FR_NFR_HEADING = re.compile(r"^#{1,6}[^\n]*?\b(FR|NFR)-(\d+)\b", re.MULTILINE)
# Any heading line at all — the boundary that ends a subsection, regardless of
# whether the next heading itself carries an FR/NFR token (a subsection must
# not leak into the next unrelated heading, e.g. "## 5. Backward Traceability").
_ANY_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)
_FR_NFR_ID = re.compile(r"\b(FR|NFR)-(\d+)\b")
# `module.sub::func` or `module.sub::Class.method` — Design-column citation in
# an AC row. Requires `::` so a bare backtick token (a test name, a filename)
# is never mistaken for a module. The function part allows dots (`[\w.]+`, not
# `\w+`) — a real citation style in this project (`taskq.breaker::Breaker.tick`)
# was silently dropped by a bare `\w+`, which stops at the first `.` and then
# fails the whole match since the required trailing backtick is never reached.
_MODULE_FUNC_REF = re.compile(r"`([a-z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+)::[\w.]+`")
# `module.sub` bare — used only inside a "**Linked Modules**" line, where no
# function suffix is expected.
_MODULE_BARE_REF = re.compile(r"`([a-z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+)`")
_LINKED_MODULES_LINE = re.compile(r"^.*\*\*Linked Modules\*\*.*$", re.MULTILINE)
# A table row whose first cell is a bare backtick-quoted dotted module name —
# the shape both TRACEABILITY_MATRIX.md §5.3 and SPEC_TRACKING.md §5 use for
# their module-ownership rows.
_OWNERSHIP_ROW = re.compile(
    r"^\|\s*`([a-z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+)`.*$", re.MULTILINE)


def _zid(kind: str, num: str) -> str:
    return f"{kind}-{int(num):02d}"


def _module_to_frs_ground_truth(text: str) -> dict[str, set[str]]:
    """Derive ``{module: {FR-NN/NFR-NN}}`` from TRACEABILITY_MATRIX.md's own
    forward citations: AC-row `` `module::func` `` Design-column references,
    plus each subsection's trailing "**Linked Modules**: `module`" line (a
    secondary/derived relationship the AC table alone omits — e.g. a module
    that only *calls* another module's function, not one directly cited by an
    AC row's Design column).

    Subsections are delimited by ``#{1,6} ... FR-NN`` / ``NFR-NN`` heading
    lines (the same structural convention `spec_alignment.py`'s
    ``_FR_HEADING`` relies on for FR-ID recognition) — never by a specific
    prose subsection title (e.g. "Design Element -> FR/NFR Coverage Matrix"),
    which is not a harness-mandated convention and free to drift between P1
    runs. A subsection ends at the next heading of ANY kind, not only the
    next FR/NFR heading — otherwise the last subsection (e.g. NFR-10) would
    leak into every following section (the §5 reverse-coverage tables, §6-11)
    and pollute the ground truth with unrelated module mentions.
    """
    fr_headings = list(_FR_NFR_HEADING.finditer(text))
    heading_starts = [m.start() for m in _ANY_HEADING.finditer(text)]
    result: dict[str, set[str]] = {}
    for h in fr_headings:
        fr_id = _zid(h.group(1), h.group(2))
        end = next((pos for pos in heading_starts if pos > h.start()), len(text))
        section = text[h.end():end]
        modules: set[str] = set(m.group(1) for m in _MODULE_FUNC_REF.finditer(section))
        for line_m in _LINKED_MODULES_LINE.finditer(section):
            modules.update(m.group(1) for m in _MODULE_BARE_REF.finditer(line_m.group(0)))
        for mod in modules:
            result.setdefault(mod, set()).add(fr_id)
    return result


def _check_ownership_table(text: str, source_label: str,
                            module_to_frs: dict[str, set[str]],
                            check_missing: bool) -> list[Violation]:
    """Compare each module-ownership row's declared FR/NFR set against the
    ground truth derived from TRACEABILITY_MATRIX.md's own forward citations.

    Two directions, both severity="error" (confirmed, decidable defects):
      * missing (only when *check_missing*) — ground truth requires this
        FR/NFR for the module, the row omits it (matrix self-contradiction /
        stale coverage table). Only applied to TRACEABILITY_MATRIX.md's own
        §5.3, whose own heading ("Design Element -> FR/NFR Coverage Matrix")
        claims to be an exhaustive reverse index. SPEC_TRACKING.md's §5 is
        explicitly scoped to "high-risk modules per C-11" ownership
        assignment, not an exhaustiveness claim — demanding it list every
        FR/NFR a module touches would be a false positive against its own
        stated, narrower purpose.
      * mismatched — the row claims an FR/NFR the ground truth attributes
        exclusively to *other* module(s) (e.g. SPEC_TRACKING.md assigning
        FR-05 to `taskq.executor` when only `taskq.cli`/`taskq.store` are ever
        cited under FR-05). FR/NFRs never cited by *any* module in the ground
        truth are not flagged either way — no ground truth to judge an
        unbacked claim against, so silence rather than guess (same contract
        as check_nfr_adr_coverage's unrecognizable-table case).
    """
    fr_to_modules: dict[str, set[str]] = {}
    for mod, frs in module_to_frs.items():
        for fr in frs:
            fr_to_modules.setdefault(fr, set()).add(mod)

    violations: list[Violation] = []
    for row in _OWNERSHIP_ROW.finditer(text):
        module = row.group(1)
        row_text = row.group(0)
        declared = {_zid(k, n) for k, n in _FR_NFR_ID.findall(row_text)}
        required = module_to_frs.get(module, set())

        if check_missing:
            for missing in sorted(required - declared):
                violations.append(Violation(
                    check_type="module_coverage_gap", rule_id=f"{module}:{missing}",
                    severity="error",
                    message=(f"{source_label}: `{module}` is missing {missing} — "
                             f"TRACEABILITY_MATRIX.md's own AC/Linked-Modules citations "
                             f"attribute {missing} to `{module}` but this ownership row "
                             f"omits it")))

        for extra in sorted(declared - required):
            owners = fr_to_modules.get(extra)
            if not owners:
                continue  # no confirmed owner anywhere — nothing to contradict
            violations.append(Violation(
                check_type="module_ownership_mismatch", rule_id=f"{module}:{extra}",
                severity="error",
                message=(f"{source_label}: `{module}` claims {extra}, but "
                         f"TRACEABILITY_MATRIX.md's AC citations attribute {extra} "
                         f"only to {', '.join('`' + o + '`' for o in sorted(owners))}")))
    return violations


def check_module_fr_coverage(project: Path) -> list[Violation]:
    """P1 self/cross-consistency gate: TRACEABILITY_MATRIX.md's §3/§4 AC-row
    module citations are ground truth; its own §5.3 reverse-coverage table and
    SPEC_TRACKING.md's §5 Module Ownership table must agree with it (no
    omissions, no unbacked claims). Never uses an external file (e.g.
    SPEC.md) as ground truth — module names are project-specific, unlike the
    harness-wide FR-/NFR-ID convention, so only TRACEABILITY_MATRIX.md's own
    internally-established citations are trusted.
    """
    project = Path(project)
    layout = ProjectLayout(project)
    matrix_path = layout.traceability_matrix_path
    if not matrix_path.exists():
        return []
    matrix_text = matrix_path.read_text(encoding="utf-8", errors="replace")
    module_to_frs = _module_to_frs_ground_truth(matrix_text)
    if not module_to_frs:
        return []  # no forward citations found — nothing to check against

    violations = _check_ownership_table(
        matrix_text, matrix_path.name, module_to_frs, check_missing=True)

    spec_tracking_path = layout.spec_tracking_path
    if spec_tracking_path.exists():
        spec_tracking_text = spec_tracking_path.read_text(encoding="utf-8", errors="replace")
        violations += _check_ownership_table(
            spec_tracking_text, spec_tracking_path.name, module_to_frs, check_missing=False)
    return violations
