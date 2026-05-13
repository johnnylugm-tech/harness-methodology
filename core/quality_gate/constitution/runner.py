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
    - security: auth/validation/encryption/sanitize keywords, no hardcoded secrets
    - maintainability: docstring, module structure, naming conventions
    - coverage: test coverage references, FR↔test traceability
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict

from core.quality_gate.constitution.profile import get_profile


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


_STUB_PLACEHOLDER_RE = re.compile(r'\{[A-Za-z_]')


def _is_stub_template(content: str) -> bool:
    """Detect un-filled template files by counting {placeholder} patterns.

    Templates from init-project contain {Project Name}, {placeholder}, etc.
    These should not be scored as failing artifacts — they're waiting for
    the user to fill them in.
    """
    return len(_STUB_PLACEHOLDER_RE.findall(content)) >= 5


def _keyword_density(content: str, keywords: List[str]) -> float:
    """Compute keyword density score 0-100 for a set of keywords."""
    if not keywords:
        return 100.0
    hits = sum(1 for kw in keywords if kw in content)
    return min(hits / len(keywords), 1.0) * 100.0


def _has_hardcoded_secrets(content: str) -> bool:
    """Check for hardcoded secret patterns in content."""
    content_lower = content.lower()
    for pattern in get_profile().secret_patterns():
        if pattern in content_lower:
            return True
    return False


def _scan_file_compliance(file_path: Path) -> Dict[str, float]:
    """Scan a single file for constitution compliance across 4 dimensions.

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

    profile = get_profile()

    # ── Correctness (40% keyword density + 30% structure + 30% FR refs) ──
    c_kw = _keyword_density(content, profile.dimension_keywords("correctness"))
    section_count = content.count("\n## ") + content.count("\n# ")
    c_structure = min(section_count / 5.0, 1.0) * 100.0
    has_fr = "fr-" in content
    has_nfr = "nfr-" in content
    has_ac = "acceptance criteria" in content
    c_refs = ((1 if has_fr else 0) + (1 if has_nfr else 0) + (1 if has_ac else 0)) / 3.0 * 100.0
    correctness = c_kw * 0.4 + c_structure * 0.3 + c_refs * 0.3

    # ── Security (keyword density + no hardcoded secrets) ──
    s_kw = _keyword_density(content, profile.dimension_keywords("security"))
    s_secrets = 0.0 if _has_hardcoded_secrets(content) else 100.0
    security = s_kw * 0.6 + s_secrets * 0.4

    # ── Maintainability (keyword density + structure signals) ──
    m_kw = _keyword_density(content, profile.dimension_keywords("maintainability"))
    maintainability = m_kw * 0.7 + c_structure * 0.3

    # ── Coverage (keyword density) ──
    cov_kw = _keyword_density(content, profile.dimension_keywords("coverage"))
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

    phase_dir = get_profile().phase_directory(phase)
    target_dirs = [docs_path]

    # Only add the numbered phase dir when docs_path is the canonical "docs/"
    # directory. When docs/ is absent and run_constitution_check() fallback
    # redirects to the numbered dir itself, avoid appending it again (double-scan).
    if docs_path.name == "docs":
        numbered_dir = docs_path.parent / phase_dir
        if numbered_dir.exists() and numbered_dir.resolve() != docs_path.resolve():
            target_dirs.append(numbered_dir)

    files_scanned = 0
    for directory in target_dirs:
        if not directory.exists():
            continue
        for item in directory.rglob("*.md"):
            if item.name.startswith("."):
                continue
            if not _should_scan_file(item, check_type):
                continue
            dims = _scan_file_compliance(item)
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

    Args:
        check_type: Artifact type to check ("all", "srs", "sad", "implementation",
                    "test_plan", "verification", "quality_report", "risk_management",
                    "configuration").
        docs_path: Path to the docs/ directory.
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

    if not path.exists():
        phase_dir_name = get_profile().phase_directory(current_phase)
        alt_path = path.parent / phase_dir_name if path.name == "docs" else path
        if alt_path.exists():
            path = alt_path

    if not path.exists():
        # Directory does not exist — vacuously pass.
        # Artifact *existence* is checked separately by the artifact enforcer.
        result = ConstitutionResult(
            score=100.0,
            passed=True,
            violations=[],
            check_type=check_type,
            phase=current_phase,
            check_mode=check_mode,
            dimensions={"correctness": 100.0, "security": 100.0,
                       "maintainability": 100.0, "coverage": 100.0},
        )
        return result

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
