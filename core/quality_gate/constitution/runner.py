#!/usr/bin/env python3
"""
Constitution Runner — executable quality gate constitution check.

Called by:
- phase_hooks.py: preflight_constitution() / postflight_constitution()
- framework_enforcer.py: check_constitution()
- harness_cli.py / run-phase integration

Provides:
    run_constitution_check(check_type, docs_path, current_phase=1, check_mode="preflight")
    → ConstitutionResult(score, passed, violations, dimensions)

Multi-dimensional scoring (aligned with methodology-v2 TH-03~TH-06):
    - correctness: FR-ID format, acceptance criteria, test cases, section structure
    - security: auth/validation/encryption/sanitize keyword density (actual secret
      scanning is gitleaks at Gate 2/3/4, not this doc-level heuristic)
    - maintainability: docstring, module structure, naming conventions
    - coverage: test coverage references, FR↔test traceability
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional

import json

from core.quality_gate.constitution.profile import get_profile


# ── Phase-to-check-type mapping (single source of truth) ───────────────────
# Public — re-exported via core.quality_gate.constitution so phase_hooks,
# framework_enforcer, and other callers share one canonical mapping.
# Preflight mode reads state.json to find completed phases, then uses this
# map to determine which check_type (file filter) to apply for each phase.
PHASE_CHECK_TYPES: Dict[int, str] = {
    1: "srs",
    2: "sad",
    3: "implementation",
    4: "test_plan",
    5: "verification",
    6: "quality_report",
    7: "risk_management",
    8: "configuration",
}

# Backwards-compatible alias for any in-tree callers that imported the
# underscored name during the transition window.
_PHASE_CHECK_TYPES = PHASE_CHECK_TYPES


@dataclass
class ConstitutionResult:
    """Result of a constitution compliance check."""

    score: float = 0.0
    passed: bool = False
    violations: List[Dict] = field(default_factory=list)
    check_type: str = ""
    phase: int = 1
    check_mode: str = "preflight"
    dimensions: Dict[str, float] = field(default_factory=dict)
    violation_problem_types: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "passed": self.passed,
            "violations": len(self.violations),
            "violation_details": self.violations,
            "check_type": self.check_type,
            "phase": self.phase,
            "dimensions": self.dimensions,
        }

    def to_fix_context(self) -> dict:
        """Serialize for AutoFixEngine consumption."""
        problem_type = "missing_artifact" if self.score == 0.0 else "low_constitution_score"
        severity = "critical" if self.score < 80.0 else "high"
        return {
            "source": "constitution/runner",
            "problem_type": problem_type,
            "severity": severity,
            "phase": self.phase,
            "dimensions": dict(self.dimensions),
            "score": self.score,
            "violations": self.violations,
            "files": [v.get("file", "") for v in self.violations if v.get("file")],
        }


# ── State discovery helpers (preflight phase boundary) ─────────────────────


def _get_completed_phases(state_path: Path) -> List[int]:
    """Return sorted list of completed phase numbers.

    Primary source (closed-loop): ``current_phase`` from state.json.
    If ``current_phase = N`` (N >= 2), phases 1..N-1 are implicitly
    completed — no agent action needed; ``advance-phase`` writes this.
    At Phase 1 nothing is completed (vacuous pass).

    Legacy fallback: ``phase_completed`` key (for projects that have it
    but whose state.json predates this fix).
    """
    if not state_path.exists():
        return []
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        # ── Primary: derive from current_phase (closed-loop) ──────────
        current = state.get("current_phase")
        if isinstance(current, int) and current > 1:
            return list(range(1, current))
        # ── Legacy fallback: phase_completed key ──────────────────────
        completed = state.get("phase_completed", {})
        if completed:
            return sorted(int(k) for k in completed.keys())
        return []
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return []


def _vacuous_result(check_type: str, phase: int, check_mode: str) -> ConstitutionResult:
    """Return a vacuous-pass ConstitutionResult (no artifacts to evaluate).

    Used when the target directory does not exist or when no phases have
    been completed yet.  Artifact *existence* is verified separately by the
    artifact enforcer (phase_artifact_enforcer.py); the constitution runner
    only evaluates the *quality* of artifacts that exist.

    ``check_mode`` is required (no default) — callers must be explicit about
    whether they're producing a preflight or postflight result so that the
    returned ConstitutionResult is correctly attributed.
    """
    return ConstitutionResult(
        score=100.0,
        passed=True,
        violations=[],
        check_type=check_type,
        phase=phase,
        check_mode=check_mode,
        dimensions={"correctness": 100.0, "security": 100.0,
                     "maintainability": 100.0, "coverage": 100.0},
    )


# ── All configurable values now live in ConstitutionProfile ──────────────────
# (core/quality_gate/constitution/profile.py). The module-level constants below
# have been replaced by profile lookups via get_profile().
#
# To customize: create .methodology/constitution_profile.json in your project.


def _should_scan_file(file_path: Path, check_type: str) -> bool:
    """Determine if a file should be scanned based on check_type filter.

    When check_type is "all" or has no matching filter, all files pass.
    Unknown check_type values emit a warning.
    """
    if check_type == "all" or not check_type:
        return True
    profile = get_profile()
    keywords = profile.file_filter_keywords(check_type)
    if keywords is None:
        warnings.warn(f"Unknown constitution check_type: {check_type!r} — scanning all files")
        return True
    if not keywords:
        return True
    file_lower = file_path.name.lower()
    return any(kw in file_lower for kw in keywords)


_STUB_PLACEHOLDER_RE = re.compile(r'(?<!\$)\{[A-Za-z][A-Za-z0-9_ ]*\}')


def _is_stub_template(content: str) -> bool:
    """Detect un-filled template files by counting {placeholder} patterns.

    Templates from init-project contain {Project Name}, {placeholder}, etc.
    These should not be scored as failing artifacts — they're waiting for
    the user to fill them in.

    Regex only matches simple word/phrase placeholders (letters, digits,
    underscores, spaces) and excludes:
    - Shell variable expansions: ${VAR} or ${VAR:-default}  (no $ prefix)
    - Python/code with dots:     {Platform.TELEGRAM}         (. not in class)
    - Set/dict literals:         {key: value}                (: not in class)

    Threshold is 8 to avoid false positives on filled documents that
    contain a handful of incidental code-variable references like {user_id}
    or {http_code} in citation snippets.  A real unfilled template has
    15+ placeholders; filled risk/config docs have ≤ 5 such patterns.
    """
    return len(_STUB_PLACEHOLDER_RE.findall(content)) >= 8


_TEMPLATE_STUB_SENTINEL = "<!-- harness:template-stub -->"


def _has_stub_sentinel(content: str) -> bool:
    """Return True iff a template-stub sentinel is present in *content*.

    Content is pre-lowered by _scan_file_compliance; sentinel is lowercase
    to match. Authors remove the sentinel line as soon as they start
    writing real content. The sentinel is an explicit author opt-out,
    co-equal with _is_stub_template (both return a vacuous 100/100/100/100
    so the file does not poison the directory-level average).
    """
    return _TEMPLATE_STUB_SENTINEL in content


def _keyword_density(content: str, keywords: List[str]) -> float:
    """Compute keyword density score 0-100 for a set of keywords.

    Content is expected to be pre-lowered by _scan_file_compliance.
    Keywords may be mixed-case — we lower them for matching consistency.

    Heuristic, not a correctness guarantee: keyword *presence* is a weak proxy for
    whether a concern (auth / validation / encryption / …) was actually addressed —
    text can mention a keyword without implementing it. It is paired with
    `_keyword_stuffing_penalty` (D1 anti-stuffing) to deter gaming, and applies to
    documentation/spec artifacts; the authoritative quality signal for code dimensions
    is the framework AST / independent tool scores, not this density.
    """
    if not keywords:
        return 100.0
    hits = sum(1 for kw in keywords if kw.lower() in content)
    return min(hits / len(keywords), 1.0) * 100.0


def _keyword_stuffing_penalty(
    content: str,
    keywords: List[str],
    is_markdown: bool = False,
) -> float:
    """Detect unnatural keyword clustering (D1: anti-stuffing).

    Returns a penalty factor 0.0–1.0:
      1.0 = natural distribution (no penalty)
      <1.0 = suspicious clustering (penalty applied multiplicatively)

    Three checks (strict, for code):
    1. Position stddev across ALL occurrences (not just first): genuinely
       distributed keywords have stddev ~0.2–0.4; clustered keywords <0.1.
    2. Decile density cap: >50% of ALL occurrences in a single 10% segment.
    3. Tail density ratio: >50% of ALL occurrences in the last 15% of doc.

    Markdown note (bug #3 fix): For .md docs, position-based clustering is
    the natural result of section/table structure (FR headers concentrated
    in §2 tables, AC in §7 JSON block). Strict 0.05 stddev threshold falsely
    flags every real spec doc; markdown docs use a relaxed threshold
    (0.025 — half of strict) and skip the decile cap. Only the tail-density
    check is retained as a true stuffing signal in any file type.
    Repro: integration-test P1 SRS.md scored 73% (FAIL threshold 75%).

    Content is expected to be pre-lowered by _scan_file_compliance.
    Keywords are lowered before matching for case-consistency with _keyword_density.
    """
    if not keywords or len(content) < 200:
        return 1.0

    total_len = max(len(content), 1)
    positions: list[float] = []

    for kw in keywords:
        kw_lower = kw.lower()
        for m in re.finditer(re.escape(kw_lower), content):
            positions.append(m.start() / total_len)

    if len(positions) < 3:
        return 1.0  # Too few occurrences to measure distribution

    import statistics as _stats
    pos_stdev = _stats.pstdev(positions)

    # ── Check 1: position stddev (relaxed for markdown) ────────────────
    _stddev_severe = 0.025 if is_markdown else 0.05
    _stddev_moderate = 0.05 if is_markdown else 0.10
    _stddev_mild = 0.075 if is_markdown else 0.15
    if pos_stdev < _stddev_severe:
        return 0.5   # Severe clustering — 50% penalty
    if pos_stdev < _stddev_moderate:
        return 0.7   # Moderate clustering — 30% penalty
    if pos_stdev < _stddev_mild:
        return 0.85  # Mild clustering — 15% penalty

    # ── Check 2: decile density cap (strict only; markdown skipped) ───
    # Rationale: real spec docs have FR headers concentrated in the
    # requirements table, which is the intended organization, not stuffing.
    if not is_markdown:
        decile_hits = [0] * 10
        for pos in positions:
            d = min(int(pos * 10), 9)
            decile_hits[d] += 1

        max_decile = max(decile_hits)
        if max_decile > len(positions) * 0.5 and len(positions) >= 6:
            return 0.7

    # ── Check 3: tail density ratio (last 15% of document) ─────────────
    # Keyword stuffing often concentrates in a "keyword dump" at the end.
    # This is a true stuffing signal in any file type (a 15% tail with >50%
    # of all keyword occurrences is a dump, not a real section structure).
    tail_hits = sum(1 for p in positions if p > 0.85)
    if len(positions) >= 4 and tail_hits / len(positions) > 0.5:
        return 0.6  # >50% of occurrences in last 15% — stuffing pattern

    return 1.0


def _scan_file_compliance(file_path: Path, phase: Optional[int] = None) -> Dict[str, float]:
    """Scan a single file for constitution compliance across 4 dimensions.

    Args:
        file_path: Path to the file to scan.
        phase: Pipeline phase (1-8). When provided, per-phase keyword overrides
               from ConstitutionProfile are used. None uses global keywords.

    Returns a dict with keys: correctness, security, maintainability, coverage.
    Each value is 0-100.
    """
    empty = {"correctness": 0.0, "security": 0.0,
             "maintainability": 0.0, "coverage": 0.0}

    if not file_path.exists() or not file_path.is_file():
        return empty

    try:
        content = file_path.read_text(encoding="utf-8").lower()
    except Exception:
        return empty

    if len(content) < 100:
        return empty

    if _is_stub_template(content):
        return {"correctness": 100.0, "security": 100.0,
                "maintainability": 100.0, "coverage": 100.0}

    if _has_stub_sentinel(content):
        return {"correctness": 100.0, "security": 100.0,
                "maintainability": 100.0, "coverage": 100.0}

    profile = get_profile()

    # ── Correctness (40% keyword density + 30% structure + 30% FR refs) ──
    c_keywords = profile.dimension_keywords_for_phase("correctness", phase)
    c_kw = _keyword_density(content, c_keywords)
    c_stuff_penalty = _keyword_stuffing_penalty(
        content, c_keywords, is_markdown=file_path.suffix.lower() == ".md"
    )
    c_kw *= c_stuff_penalty
    section_count = content.count("\n## ") + content.count("\n# ")
    c_structure = min(section_count / 5.0, 1.0) * 100.0
    has_fr = "fr-" in content
    has_nfr = "nfr-" in content
    has_ac = "acceptance criteria" in content
    c_refs = ((1 if has_fr else 0) + (1 if has_nfr else 0) + (1 if has_ac else 0)) / 3.0 * 100.0
    correctness = c_kw * 0.4 + c_structure * 0.3 + c_refs * 0.3

    # ── Security (keyword density only) ──
    # Hardcoded-secret detection was removed: it was a weak substring check
    # (a handful of literal `var = "..."` patterns, docs-only) that almost always
    # returned 100 and gave a false sense of secrets coverage. Real secret scanning
    # is gitleaks at Gate 2/3/4 (P3+, secrets_scanning dim, threshold 100), which is
    # independently re-run by S4 cross-validation and cannot be faked.
    s_keywords = profile.dimension_keywords_for_phase("security", phase)
    s_kw = _keyword_density(content, s_keywords)
    s_stuff_penalty = _keyword_stuffing_penalty(content, s_keywords)
    s_kw *= s_stuff_penalty
    security = s_kw

    # ── Maintainability (keyword density + structure signals) ──
    m_keywords = profile.dimension_keywords_for_phase("maintainability", phase)
    m_kw = _keyword_density(content, m_keywords)
    m_stuff_penalty = _keyword_stuffing_penalty(content, m_keywords)
    m_kw *= m_stuff_penalty
    maintainability = m_kw * 0.7 + c_structure * 0.3

    # ── Coverage (keyword density) ──
    cov_keywords = profile.dimension_keywords_for_phase("coverage", phase)
    cov_kw = _keyword_density(content, cov_keywords)
    cov_stuff_penalty = _keyword_stuffing_penalty(content, cov_keywords)
    cov_kw *= cov_stuff_penalty
    coverage = cov_kw

    return {
        "correctness": round(correctness, 1),
        "security": round(security, 1),
        "maintainability": round(maintainability, 1),
        "coverage": round(coverage, 1),
    }


def _dimensions_for_phase(phase: int) -> List[str]:
    """Return the active constitution dimensions for a given phase.

    Delegates to ConstitutionProfile.active_dimensions(phase).
    Customizable via .methodology/constitution_profile.json → phases.N.active_dimensions.
    """
    return get_profile().active_dimensions(phase)


def _threshold_for_dimension(dim: str, phase: int) -> float:
    """Return the per-dimension threshold for a given phase.

    P1-P4: per-dimension thresholds from profile (correctness=100, security=100, etc.)
    P5-P8: composite baseline of 80.0 (TH-02 composite threshold takes over)

    Customizable via .methodology/constitution_profile.json → dimensions.D.threshold.
    """
    if phase <= 4:
        return get_profile().dimension_threshold(dim, phase)
    return 80.0


def _aggregate_score(dim_scores: Dict[str, float], active_dims: List[str]) -> float:
    """Compute aggregate constitution score from dimension scores.

    Uses minimum-of-dimensions (bottleneck principle):
    a chain is only as strong as its weakest link.
    """
    if not active_dims:
        return 100.0
    relevant = [dim_scores.get(d, 0.0) for d in active_dims]
    return min(relevant)


def check_single_file(file_path: Path, phase: int) -> ConstitutionResult:
    """Score a single file against the constitution for *phase*.

    Returns a ConstitutionResult with check_type='single_file'.
    The caller is responsible for the missing-file guard — pass an
    existing regular file or the behaviour is undefined.
    """
    dims = _scan_file_compliance(file_path, phase=phase)
    active = _dimensions_for_phase(phase)
    score = _aggregate_score(dims, active)
    composite_threshold = get_profile().composite_threshold(phase)
    return ConstitutionResult(
        score=round(score, 1),
        passed=score >= composite_threshold,
        violations=[],
        check_type="single_file",
        phase=phase,
        check_mode="postflight",
        dimensions=dims,
    )


def _scan_directory(docs_path: Path, phase: int, check_type: str) -> ConstitutionResult:
    """Scan docs directory for constitution compliance.

    Args:
        docs_path: Path to the docs/ directory.
        phase: Current pipeline phase (1-8).
        check_type: One of "all", "srs", "sad", "implementation", etc.

    Returns:
        ConstitutionResult with score, passed, violations, and per-dimension scores.
    """
    violations: List[Dict] = []
    all_dim_scores: Dict[str, List[float]] = {
        "correctness": [], "security": [], "maintainability": [], "coverage": [],
    }

    from core.utils.project_layout import ProjectLayout
    layout = ProjectLayout(docs_path.parent if docs_path.name == "docs" else docs_path)
    
    target_dirs = [docs_path]

    # Only add the numbered phase dir when docs_path is the canonical "docs/"
    # directory. When docs/ is absent and run_constitution_check() fallback
    # redirects to the numbered dir itself, avoid appending it again (double-scan).
    if docs_path.name == "docs":
        phase_dir = layout.get_phase_dir(phase)
        if phase_dir.exists() and phase_dir.resolve() != docs_path.resolve():
            target_dirs.append(phase_dir)

    # P3+: scan Python source files only — .md compliance docs are gameable (keyword stuffing).
    # P1/P2: scan .md (SRS.md, SAD.md are the actual deliverables for those phases).
    _scan_pattern = "*.py" if (phase is not None and phase >= 3) else "*.md"

    files_scanned = 0
    for directory in target_dirs:
        if not directory.exists():
            continue
        for item in directory.rglob(_scan_pattern):
            if any(part.startswith(".") for part in item.relative_to(directory).parts):
                continue
            if get_profile().is_excluded(item, phase=phase):
                continue
            if not _should_scan_file(item, check_type):
                continue
            dims = _scan_file_compliance(item, phase=phase)
            for d, v in dims.items():
                all_dim_scores[d].append(v)
            files_scanned += 1

    if files_scanned == 0:
        # No scannable artifacts in this directory — vacuously pass.
        # Artifact *existence* is checked separately by the artifact enforcer
        # (phase_artifact_enforcer.py); the constitution runner only evaluates
        # the *quality* of artifacts that exist.  An empty directory has no
        # quality issues to flag.
        return ConstitutionResult(
            score=100.0,
            passed=True,
            violations=[],
            check_type=check_type,
            phase=phase,
            dimensions={"correctness": 100.0, "security": 100.0,
                       "maintainability": 100.0, "coverage": 100.0},
        )

    # Aggregate per-dimension averages
    agg_dims: Dict[str, float] = {}
    for d, scores in all_dim_scores.items():
        agg_dims[d] = round(sum(scores) / len(scores), 1) if scores else 0.0

    active_dims = _dimensions_for_phase(phase)
    score = _aggregate_score(agg_dims, active_dims)
    const_threshold = get_profile().composite_threshold(phase)
    passed = score >= const_threshold

    # Generate per-dimension violations
    for dim in active_dims:
        dim_threshold = _threshold_for_dimension(dim, phase)
        dim_score = agg_dims.get(dim, 0.0)
        if dim_score < dim_threshold:
            violations.append({
                "dimension": dim,
                "score": dim_score,
                "threshold": dim_threshold,
                "message": f"{dim} score {dim_score:.0f}% < {dim_threshold:.0f}% threshold",
                "rule": get_profile().dimension_rule(dim),
            })

    # Low file-level scores as additional violations
    for dim in active_dims:
        dim_scores = all_dim_scores.get(dim, [])
        for fs in dim_scores:
            if fs < 30:
                violations.append({
                    "dimension": dim,
                    "score": round(fs, 1),
                    "message": f"Low {dim} score ({fs:.0f}%) in scanned file",
                    "rule": "TH-02",
                })

    return ConstitutionResult(
        score=round(score, 1),
        passed=passed,
        violations=violations,
        check_type=check_type,
        phase=phase,
        dimensions=agg_dims,
    )


def _resolve_project_root(docs_path: Path) -> Path:
    """Resolve the project root from a docs/phase-directory path.

    Walks upward from ``docs_path`` looking for a directory that contains
    ``.methodology/state.json``.  If none is found, falls back to the
    parent-of-docs / self-is-project heuristic so existing callers that
    pass non-canonical paths continue to behave.

    Examples:
        /proj/docs            → /proj         (state.json found at /proj/.methodology/)
        /proj/02-architecture → /proj         (walks up one level)
        /proj                 → /proj         (state.json found directly)
        /tmp/empty/docs       → /tmp/empty    (no state.json found; fallback)
    """
    candidates: List[Path] = []
    # Start with docs_path itself, then climb. Cap at 6 levels — projects
    # nested deeper than that would already be unusual.
    cur = docs_path if docs_path.is_dir() else docs_path.parent
    for _ in range(6):
        candidates.append(cur)
        from core.utils.project_layout import ProjectLayout
        if ProjectLayout(cur).state_json_path.exists():
            return cur
        if cur.parent == cur:  # filesystem root
            break
        cur = cur.parent

    # Fallback: legacy heuristic — parent if path is named "docs", else self.
    if docs_path.name == "docs":
        return docs_path.parent
    return docs_path


def _preflight_check(
    docs_path: Path,
    current_phase: int,
    strict: bool,
    requested_check_type: str = "all",
) -> ConstitutionResult:
    """Preflight constitution check — scan only completed phases' artifacts.

    Derives completed phases from ``current_phase`` in state.json
    (closed-loop: if current_phase = N, phases 1..N-1 are implicitly
    completed).  Then scans the **most recently completed** phase's
    directory using that phase's own check_type and profile.

    If current_phase is 1 (or state.json is missing), returns a
    vacuous pass — there are simply no prior artifacts to verify.

    If the caller passed a specific ``requested_check_type`` (anything other
    than "all" or an empty string) and it differs from the previous phase's
    check_type, emit a warning so the override isn't silent.
    """
    project_root = _resolve_project_root(docs_path)
    from core.utils.project_layout import ProjectLayout
    state_path = ProjectLayout(project_root).state_json_path

    completed = _get_completed_phases(state_path)
    if not completed:
        # No prior phases completed → nothing to verify
        return _vacuous_result("preflight", current_phase, "preflight")

    # Scan the most recently completed phase's artifacts
    prev_phase = completed[-1]
    prev_check_type = PHASE_CHECK_TYPES.get(prev_phase, "all")

    # Surface the silent-override case: caller asked for a specific check_type
    # but preflight will use the previous phase's own check_type instead.
    if (
        requested_check_type
        and requested_check_type != "all"
        and requested_check_type != prev_check_type
    ):
        warnings.warn(
            f"Preflight check_type override: caller requested {requested_check_type!r} "
            f"but preflight will scan completed phase {prev_phase} as {prev_check_type!r}. "
            "Pass check_mode='postflight' to honor the requested check_type, or "
            "pass check_type='all' to suppress this warning.",
            stacklevel=3,
        )

    from core.utils.project_layout import ProjectLayout
    layout = ProjectLayout(project_root)
    target = layout.get_phase_dir(prev_phase)

    if not target.exists():
        # Previous phase directory absent → nothing to scan (vacuous pass)
        return _vacuous_result(prev_check_type, current_phase, "preflight")

    result = _scan_directory(target, prev_phase, prev_check_type)
    result.check_mode = "preflight"

    if strict and not result.passed:
        raise RuntimeError(
            f"Constitution check FAILED (preflight on completed phase {prev_phase}): "
            f"score={result.score:.0f}% "
            f"(threshold={get_profile().composite_threshold(prev_phase)}%), "
            f"violations={len(result.violations)}, "
            f"dims={result.dimensions}"
        )

    return result


def run_constitution_check(
    check_type: str,
    docs_path: str,
    current_phase: int = 1,
    check_mode: str = "preflight",
    *,
    strict: bool = False,
) -> ConstitutionResult:
    """Run constitution compliance check against project artifacts.

    This is the primary entry point used by phase_hooks, framework_enforcer,
    and the CLI.

    **Preflight vs Postflight semantics**:

    - ``check_mode="preflight"`` (default): Scans the **most recently
      completed** phase's artifacts (read from ``state.json.phase_completed``).
      The current phase's directory is deliberately **skipped** because its
      artifacts have not been written yet.  This prevents the chicken-and-egg
      problem where entering Phase 2 scans stale ``02-architecture/`` files
      and fails.

    - ``check_mode="postflight"``: Scans the **current** phase's artifacts
      (existing behavior) — the phase just finished, so its artifacts should
      exist and should pass quality gate.

    Args:
        check_type: Artifact type to check ("all", "srs", "sad", "implementation",
                    "test_plan", "verification", "quality_report", "risk_management",
                    "configuration").  **Ignored in preflight mode** — the check
                    type is derived from the most recently completed phase.
        docs_path: Path to the docs/ directory (or project root).
        current_phase: Pipeline phase number (1-8).
        check_mode: "preflight" or "postflight".
        strict: If True, raise on critical violations instead of returning.

    Returns:
        ConstitutionResult with .score, .passed, .violations, .dimensions.

    Raises:
        RuntimeError: If strict=True and the scanned artifacts score below
            the phase threshold.  A missing or empty directory is treated as
            a vacuous pass (artifact existence is checked separately by the
            artifact enforcer) and never raises.
    """
    path = Path(docs_path)

    # ── Preflight: scan completed phases from state.json ───────────────
    if check_mode == "preflight":
        return _preflight_check(path, current_phase, strict, check_type)

    # ── Postflight (and any other mode): existing behavior ──────────────
    if not path.exists():
        from core.utils.project_layout import ProjectLayout
        project_root = _resolve_project_root(path)
        layout = ProjectLayout(project_root)
        target = layout.get_phase_dir(current_phase) if path.name == "docs" else path
        if target.exists():
            path = target

    if not path.exists():
        return _vacuous_result(check_type, current_phase, check_mode)

    result = _scan_directory(path, current_phase, check_type)
    result.check_mode = check_mode

    if strict and not result.passed:
        raise RuntimeError(
            f"Constitution check FAILED: score={result.score:.0f}% "
            f"(threshold={get_profile().composite_threshold(current_phase)}%), "
            f"violations={len(result.violations)}, "
            f"dims={result.dimensions}"
        )

    return result
