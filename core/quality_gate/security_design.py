"""
Security Design — threat-model-as-code for SAD.md §6.

CONTRACT (single source of truth — do not duplicate in templates/docs):
  Marker:    <!-- SEC:START --> ... <!-- SEC:END --> (REQUIRED, same
             convention as the SAB block — see sab_parser.py).
  Body:      ```yaml fenced (recommended) or raw (no fence).
  Root key:  `security_design:` (REQUIRED — unlike the SAB parser's lenient
             fallback, a missing root key is a reportable violation here).
  Fields:
    version         (str, informational)
    applicability   "full" | "none" — REQUIRED.
    justification   (str, required + >=20 chars when applicability: none;
                     ignored when applicability: full)
    trust_boundaries (list of {id: "TB-NN", name, description}, >=1 when full)
    threats          (list of {id: "T-NN", boundary, category (STRIDE),
                     description, mitigation, owner_module, nfr (optional),
                     verified_by}, >=1 per boundary when full)
    verified_by is a SINGLE test name, not a comma-separated list — each
    threat must be independently verifiable by exactly one test (R8 checks
    that name actually exists in the test source at phase>=5). A threat
    that needs more than one test is a sign it should be split into
    separate T-NN entries, not a reason to relax this to a list.

Round 10 (gap-analysis response): security review previously relied on
SRS/SAD keyword density (Bug #35 — proven to false-positive-fail honest
tool-type projects like tts-new/taskq; the `security` dimension was
REMOVED from P1/P3/P4 constitution scoring as a direct result — see
tests/test_constitution_profile.py). This module replaces keyword scoring
with a decidable structural check: every project MUST declare either a
STRIDE-lite threat model (trust boundaries + threats + mitigations +
which test verifies each) or an explicit, justified `applicability: none`
for genuinely security-irrelevant projects. R1-R8 below are pure
structural/cross-reference checks — an honest `none` always passes.

For the canonical template, call render_canonical_security_template() — do
not hand-write the YAML anywhere else (templates/SAD.md's §6 is a verbatim
snapshot of this function's output, locked by
tests/test_security_design.py::test_sad_template_sec_block_is_factory_snapshot).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml

from core.harness_config import get_feature
from core.quality_gate import Violation
from core.quality_gate.sab_amender import sab_module_candidate
from core.quality_gate.sab_parser import extract_sab_from_sad
from core.traceability.scanner import extract_nfr_ids_from_srs
from core.utils.lang_patterns import iter_test_files, project_language
from core.utils.project_layout import ProjectLayout

__all__ = [
    "STRIDE_CATEGORIES",
    "extract_security_block",
    "render_canonical_security_template",
    "check_security_design",
]

# STRIDE — the standard six threat categories (Spoofing, Tampering,
# Repudiation, Information disclosure, Denial of service, Elevation of
# privilege). Membership only; error messages sort alphabetically for
# determinism, the canonical template hardcodes the mnemonic order.
STRIDE_CATEGORIES: frozenset[str] = frozenset({
    "spoofing", "tampering", "repudiation", "information_disclosure",
    "denial_of_service", "elevation_of_privilege",
})

_MIN_JUSTIFICATION_LEN = 20

_SEC_BLOCK_RE = re.compile(
    r"<!--\s*SEC:START\s*-->(.*?)<!--\s*SEC:END\s*-->",
    re.DOTALL,
)
_CODE_FENCE_RE = re.compile(r"```(?:yaml|json)?\s*(.*?)```", re.DOTALL)
_TB_ID_RE = re.compile(r"^TB-\d{2}$")
_T_ID_RE = re.compile(r"^T-\d{2}$")
_TEST_NAME_RE = re.compile(r"^test_[a-z0-9_]+$")


def extract_security_block(sad_path) -> Optional[dict]:
    """Parse SAD.md and return the raw parsed YAML dict from the
    <!-- SEC:START/END --> block — NOT unwrapped from its `security_design:`
    root key (check_security_design owns that validation, so a wrong or
    missing root key is a reportable R2 violation, not a silent fallback).

    Returns None if no SEC block is found at all. Raises RuntimeError if the
    block exists but cannot be parsed as YAML.
    """
    sad_path = Path(sad_path)
    if not sad_path.is_file():
        return None
    content = sad_path.read_text(encoding="utf-8", errors="replace")

    block_match = _SEC_BLOCK_RE.search(content)
    if not block_match:
        return None

    block = block_match.group(1)
    fence_match = _CODE_FENCE_RE.search(block)
    yaml_str = fence_match.group(1) if fence_match else block

    try:
        data = yaml.safe_load(yaml_str)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Failed to parse SEC YAML block in {sad_path}: {exc}") from exc

    return data if isinstance(data, dict) else {}


# ─────────────────────────────────────────────────────────────────────────────
# Canonical template factory — SINGLE SOURCE OF TRUTH
# ─────────────────────────────────────────────────────────────────────────────
# templates/SAD.md §6 MUST render this function instead of hand-writing YAML
# (same discipline as sab_parser.render_canonical_sab_template()).


def render_canonical_security_template(
    module_example: str = "app.api.webhooks",
    nfr_id: str = "NFR-02",
) -> str:
    """Return the canonical SEC YAML block as a string (no surrounding
    markers/fence).

    EXAMPLE — replace placeholder values (boundary/threat ids, module, nfr)
    with your project's real values.

    Callers that need the full markdown form wrap the output:
        '<!-- SEC:START -->\\n```yaml\\n' + render_canonical_security_template() + '```\\n<!-- SEC:END -->'
    """
    lines: list[str] = []
    lines.append("security_design:")
    lines.append('  version: "1.0"')
    lines.append("  applicability: full   # full | none — none REQUIRES justification and skips the rest")
    lines.append('  justification: ""     # required (>=20 chars) when applicability: none')
    lines.append("  trust_boundaries:     # EXAMPLE — replace with your project's real boundaries")
    lines.append("    - id: TB-01")
    lines.append('      name: "external HTTP input"')
    lines.append('      description: "requests crossing from unauthenticated clients into the API layer"')
    lines.append("  threats:              # STRIDE-lite — every boundary needs >=1 threat")
    lines.append("    - id: T-01")
    lines.append("      boundary: TB-01")
    lines.append("      category: tampering   # spoofing|tampering|repudiation|information_disclosure|denial_of_service|elevation_of_privilege")
    lines.append('      description: "malformed payload mutates task state without validation"')
    lines.append('      mitigation: "schema validation + reject on unknown fields"')
    lines.append(f'      owner_module: "{module_example}"   # MUST be a module declared in the SAB block (§5)')
    lines.append(f"      nfr: {nfr_id}                        # optional — MUST exist in SRS when present")
    lines.append('      verified_by: "test_sec_t01_malformed_payload_rejected"   # single test name only — NOT "test_a, test_b"; split multi-test threats into separate T-NN entries')
    lines.append("")

    return "\n".join(lines)


def check_security_design(project, phase: Optional[int] = None) -> list[Violation]:
    """Decidable completeness check for SAD.md §6's SEC block.

    Returns [] when: the feature is disabled, SAD.md doesn't exist yet (its
    own existence is preflight_previous_phase_artifacts' job), or *phase* is
    given and < 3 (the block is a P2 deliverable — informational, not yet
    enforced, at P1/P2; same convention as check_nfr_adr_coverage). R1-R7
    run whenever *phase* is None (no phase context — check everything
    structural) or >= 3. R8 (test existence) is stricter: it only runs for
    an explicit phase >= 5, never for phase=None — a bare tooling call with
    no phase information must not demand tests a P2/P3 threat model hasn't
    written yet.
    """
    project = Path(project)
    if not get_feature(project, "security_design"):
        return []

    layout = ProjectLayout(project)
    sad_path = layout.sad_path
    if not sad_path.is_file():
        return []

    if phase is not None and phase < 3:
        return []

    try:
        raw = extract_security_block(sad_path)
    except RuntimeError as exc:
        return [Violation(
            check_type="security_design", rule_id="SEC-R2",
            message=f"SAD.md §6 SEC block failed to parse: {exc}",
            file=str(sad_path), severity="error",
        )]

    if raw is None:
        return [Violation(
            check_type="security_design", rule_id="SEC-R1",
            message=(
                "SAD.md has no <!-- SEC:START -->...<!-- SEC:END --> Security "
                "Design block. Paste the output of "
                "`core.quality_gate.security_design.render_canonical_security_template()` "
                "into SAD.md §6 (replace EXAMPLE values), or set "
                "features.security_design=false in .methodology/harness_config.json "
                "to opt out (see docs/CONFIGURATION.md)."
            ),
            file=str(sad_path), severity="error",
        )]

    sec = raw.get("security_design") if isinstance(raw, dict) else None
    if not isinstance(sec, dict):
        return [Violation(
            check_type="security_design", rule_id="SEC-R2",
            message="SAD.md §6 SEC block must have a `security_design:` root "
                    "key mapping to a YAML mapping — paste the canonical "
                    "template rather than hand-writing the block.",
            file=str(sad_path), severity="error",
        )]

    violations: list[Violation] = []

    applicability = sec.get("applicability")
    if applicability not in ("full", "none"):
        violations.append(Violation(
            check_type="security_design", rule_id="SEC-R3",
            message=f"security_design.applicability must be 'full' or "
                    f"'none', got {applicability!r}.",
            file=str(sad_path), severity="error",
        ))
        return violations

    if applicability == "none":
        justification = sec.get("justification", "")
        if not isinstance(justification, str) or len(justification.strip()) < _MIN_JUSTIFICATION_LEN:
            violations.append(Violation(
                check_type="security_design", rule_id="SEC-R3",
                message=(
                    f"applicability: none requires a justification of "
                    f">= {_MIN_JUSTIFICATION_LEN} chars explaining why this "
                    "project has no security-relevant attack surface."
                ),
                file=str(sad_path), severity="error",
            ))
        return violations

    # applicability == "full" — R4: trust boundaries.
    boundaries = sec.get("trust_boundaries")
    if not isinstance(boundaries, list) or not boundaries:
        violations.append(Violation(
            check_type="security_design", rule_id="SEC-R4",
            message="applicability: full requires >=1 entry in trust_boundaries.",
            file=str(sad_path), severity="error",
        ))
        boundaries = []

    seen_tb_ids: set[str] = set()
    valid_tb_ids: set[str] = set()
    for tb in boundaries:
        if not isinstance(tb, dict):
            violations.append(Violation(
                check_type="security_design", rule_id="SEC-R4",
                message=f"trust_boundaries entry must be a mapping, got {type(tb).__name__}.",
                file=str(sad_path), severity="error",
            ))
            continue
        tb_id = tb.get("id")
        if not isinstance(tb_id, str) or not _TB_ID_RE.match(tb_id):
            violations.append(Violation(
                check_type="security_design", rule_id="SEC-R4",
                message=f"trust_boundaries entry id {tb_id!r} must match TB-NN.",
                file=str(sad_path), severity="error",
            ))
        elif tb_id in seen_tb_ids:
            violations.append(Violation(
                check_type="security_design", rule_id="SEC-R4",
                message=f"trust_boundaries id {tb_id!r} is duplicated.",
                file=str(sad_path), severity="error",
            ))
        else:
            seen_tb_ids.add(tb_id)
            valid_tb_ids.add(tb_id)
        if not tb.get("name"):
            violations.append(Violation(
                check_type="security_design", rule_id="SEC-R4",
                message=f"trust_boundaries entry {tb_id!r} is missing a non-empty name.",
                file=str(sad_path), severity="error",
            ))

    # R5: threats.
    threats = sec.get("threats")
    if not isinstance(threats, list) or not threats:
        violations.append(Violation(
            check_type="security_design", rule_id="SEC-R5",
            message="applicability: full requires >=1 entry in threats.",
            file=str(sad_path), severity="error",
        ))
        threats = []

    seen_t_ids: set[str] = set()
    boundaries_with_threats: set[str] = set()
    security_nfrs_referenced: set[str] = set()
    srs_nfr_ids = extract_nfr_ids_from_srs(layout.srs_path)
    verified_by_names: list[str] = []

    for t in threats:
        if not isinstance(t, dict):
            violations.append(Violation(
                check_type="security_design", rule_id="SEC-R5",
                message=f"threats entry must be a mapping, got {type(t).__name__}.",
                file=str(sad_path), severity="error",
            ))
            continue

        t_id = t.get("id")
        if not isinstance(t_id, str) or not _T_ID_RE.match(t_id):
            violations.append(Violation(
                check_type="security_design", rule_id="SEC-R5",
                message=f"threats entry id {t_id!r} must match T-NN.",
                file=str(sad_path), severity="error",
            ))
        elif t_id in seen_t_ids:
            violations.append(Violation(
                check_type="security_design", rule_id="SEC-R5",
                message=f"threats id {t_id!r} is duplicated.",
                file=str(sad_path), severity="error",
            ))
        else:
            seen_t_ids.add(t_id)

        boundary = t.get("boundary")
        if boundary not in valid_tb_ids:
            violations.append(Violation(
                check_type="security_design", rule_id="SEC-R5",
                message=f"threat {t_id!r} references unknown boundary {boundary!r}.",
                file=str(sad_path), severity="error",
            ))
        else:
            boundaries_with_threats.add(boundary)

        category = t.get("category")
        if category not in STRIDE_CATEGORIES:
            violations.append(Violation(
                check_type="security_design", rule_id="SEC-R5",
                message=f"threat {t_id!r} category {category!r} is not a "
                        f"STRIDE category ({', '.join(sorted(STRIDE_CATEGORIES))}).",
                file=str(sad_path), severity="error",
            ))

        if not t.get("description"):
            violations.append(Violation(
                check_type="security_design", rule_id="SEC-R5",
                message=f"threat {t_id!r} is missing a non-empty description.",
                file=str(sad_path), severity="error",
            ))
        if not t.get("mitigation"):
            violations.append(Violation(
                check_type="security_design", rule_id="SEC-R5",
                message=f"threat {t_id!r} is missing a non-empty mitigation.",
                file=str(sad_path), severity="error",
            ))

        verified_by = t.get("verified_by")
        if not isinstance(verified_by, str) or not _TEST_NAME_RE.match(verified_by):
            violations.append(Violation(
                check_type="security_design", rule_id="SEC-R5",
                message=f"threat {t_id!r} verified_by {verified_by!r} must be "
                        "a test name matching test_[a-z0-9_]+.",
                file=str(sad_path), severity="error",
            ))
        else:
            verified_by_names.append(verified_by)

        nfr = t.get("nfr")
        if nfr is not None:
            if nfr not in srs_nfr_ids:
                violations.append(Violation(
                    check_type="security_design", rule_id="SEC-R7",
                    message=f"threat {t_id!r} nfr {nfr!r} not found in SRS.md.",
                    file=str(sad_path), severity="error",
                ))
            else:
                security_nfrs_referenced.add(nfr)

    for tb_id in valid_tb_ids:
        if tb_id not in boundaries_with_threats:
            violations.append(Violation(
                check_type="security_design", rule_id="SEC-R5",
                message=f"trust boundary {tb_id!r} has zero threats.",
                file=str(sad_path), severity="error",
            ))

    # R6/R7 share one SAB parse (owner_module cross-check + security-NFR
    # reverse-reference check).
    try:
        sab_spec = extract_sab_from_sad(sad_path)
    except RuntimeError:
        sab_spec = None

    # R6: owner_module must be declared in the SAB block's modules.
    if sab_spec is None:
        violations.append(Violation(
            check_type="security_design", rule_id="SEC-R6",
            message="SAD.md has no parseable SAB block (§5) — owner_module "
                    "cross-check skipped (SAB validity is preflight_sab_check's job).",
            file=str(sad_path), severity="info",
        ))
    else:
        registered = {
            candidate.strip()
            for m in sab_spec.modules
            if isinstance((candidate := sab_module_candidate(m)), str) and candidate.strip()
        }
        for t in threats:
            if not isinstance(t, dict):
                continue
            owner_module = t.get("owner_module")
            if owner_module and owner_module not in registered:
                violations.append(Violation(
                    check_type="security_design", rule_id="SEC-R6",
                    message=f"threat {t.get('id')!r} owner_module "
                            f"{owner_module!r} is not declared in the SAB "
                            "block's modules.",
                    file=str(sad_path), severity="error",
                ))

    # R7: every SAB security-typed NFR must be referenced by >=1 threat.nfr.
    if sab_spec is not None:
        for nfr_id, nfr_data in sab_spec.nfr_traceability.items():
            if isinstance(nfr_data, dict) and str(nfr_data.get("type", "")).lower() == "security":
                if nfr_id not in security_nfrs_referenced:
                    violations.append(Violation(
                        check_type="security_design", rule_id="SEC-R7",
                        message=f"SAB NFR {nfr_id!r} is typed security but "
                                "is not referenced by any threats[].nfr.",
                        file=str(sad_path), severity="error",
                    ))

    # R8: from Phase 5, every verified_by test name must exist on disk.
    # Unlike R1-R7 (which run when phase is None — "CLI with no phase
    # context" means check everything structural), R8 requires an actual
    # int >= 5: a bare CLI/tooling call with no phase information must not
    # demand tests that a P2/P3 threat model legitimately hasn't written yet.
    if phase is not None and phase >= 5 and verified_by_names:
        language = project_language(project)
        tests_dir = layout.active_test_dir
        test_sources = "\n".join(
            f.read_text(encoding="utf-8", errors="replace")
            for f in iter_test_files(tests_dir, language)
        )
        for name in verified_by_names:
            if name not in test_sources:
                violations.append(Violation(
                    check_type="security_test_missing", rule_id="SEC-R8",
                    message=f"threat verification test {name!r} not found "
                            f"under {tests_dir} — write the test before Phase 5.",
                    file=str(sad_path), severity="error",
                ))

    return violations
