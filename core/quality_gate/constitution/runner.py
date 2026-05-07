#!/usr/bin/env python3
"""
Constitution Runner — executable quality gate constitution check.

Called by:
- phase_hooks.py: preflight_constitution() / postflight_constitution()
- framework_enforcer.py: check_constitution()
- cli.py: cmd_constitution() / run-phase integration

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

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict

from constitution import get_constitution_threshold


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


# ── Per-phase directory mapping ──────────────────────────────────────────────

_PHASE_DIR_MAP: Dict[int, str] = {
    1: "01-requirements",
    2: "02-architecture",
    3: "03-development",
    4: "04-testing",
    5: "05-verify",
    6: "06-quality",
    7: "07-risk",
    8: "08-config",
}

# ── Dimension-specific keyword sets ──────────────────────────────────────────

_CORRECTNESS_KEYWORDS: List[str] = [
    "fr-", "nfr-", "acceptance criteria", "test case",
    "### fr-", "## fr-", "requirement", "specification",
    "traceability matrix", "srs", "sad",
]

_SECURITY_KEYWORDS: List[str] = [
    "auth", "validation", "sanitize", "encrypt", "hmac",
    "signature", "verify", "rbac", "permission", "token",
    "pii", "mask", "secret", "whitelist", "tls",
    "compare_digest", "input sanitizer", "rate limit",
    "security", "vulnerability",
]

_MAINTAINABILITY_KEYWORDS: List[str] = [
    "docstring", "type hint", "dataclass", "abc",
    "interface", "module", "class", "def",
    "import", "from", "snake_case", "PascalCase",
]

_COVERAGE_KEYWORDS: List[str] = [
    "test coverage", "pytest", "unit test", "integration test",
    "mock", "fixture", "assert", "coverage report",
    "test plan", "regression",
]

# Per-dimension rule mapping for violation reporting
_DIM_RULE_MAP: Dict[str, str] = {
    "correctness": "TH-03",
    "security": "TH-04",
    "maintainability": "TH-05",
    "coverage": "TH-06",
}

# Patterns that indicate hardcoded secrets (security violation)
_SECRET_PATTERNS: List[str] = [
    "password = \"", "password = '",
    "secret_key = \"", "secret_key = '",
    "api_key = \"", "api_key = '",
    "token = \"", "token = '",
]

# ── check_type → file name keyword filtering ──────────────────────────────────

_CHECK_TYPE_FILTERS: Dict[str, List[str]] = {
    "srs": ["srs", "spec", "requirement", "fr-", "nfr-"],
    "sad": ["sad", "architecture", "adr", "design"],
    "implementation": ["implementation", "compliance", "source"],
    "test_plan": ["test_plan", "test_report", "test_result"],
    "verification": ["verify", "verification", "baseline"],
    "quality_report": ["quality_report", "quality", "monitoring"],
    "risk_management": ["risk", "assessment", "risk_register"],
    "configuration": ["config", "release", "configuration"],
    "all": [],
}


def _should_scan_file(file_path: Path, check_type: str) -> bool:
    """Determine if a file should be scanned based on check_type filter.

    When check_type is "all" or has no matching filter, all files pass.
    Unknown check_type values emit a warning.
    """
    if check_type == "all" or not check_type:
        return True
    keywords = _CHECK_TYPE_FILTERS.get(check_type)
    if keywords is None:
        warnings.warn(f"Unknown constitution check_type: {check_type!r} — scanning all files")
        return True
    if not keywords:
        return True
    file_lower = file_path.name.lower()
    return any(kw in file_lower for kw in keywords)


def _keyword_density(content: str, keywords: List[str]) -> float:
    """Compute keyword density score 0-100 for a set of keywords."""
    if not keywords:
        return 100.0
    hits = sum(1 for kw in keywords if kw in content)
    return min(hits / len(keywords), 1.0) * 100.0


def _has_hardcoded_secrets(content: str) -> bool:
    """Check for hardcoded secret patterns in content."""
    content_lower = content.lower()
    for pattern in _SECRET_PATTERNS:
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

    # ── Correctness (40% keyword density + 30% structure + 30% FR refs) ──
    c_kw = _keyword_density(content, _CORRECTNESS_KEYWORDS)
    section_count = content.count("\n## ") + content.count("\n# ")
    c_structure = min(section_count / 5.0, 1.0) * 100.0
    has_fr = "fr-" in content
    has_nfr = "nfr-" in content
    has_ac = "acceptance criteria" in content
    c_refs = ((1 if has_fr else 0) + (1 if has_nfr else 0) + (1 if has_ac else 0)) / 3.0 * 100.0
    correctness = c_kw * 0.4 + c_structure * 0.3 + c_refs * 0.3

    # ── Security (keyword density + no hardcoded secrets) ──
    s_kw = _keyword_density(content, _SECURITY_KEYWORDS)
    s_secrets = 0.0 if _has_hardcoded_secrets(content) else 100.0
    security = s_kw * 0.6 + s_secrets * 0.4

    # ── Maintainability (keyword density + structure signals) ──
    m_kw = _keyword_density(content, _MAINTAINABILITY_KEYWORDS)
    maintainability = m_kw * 0.7 + c_structure * 0.3

    # ── Coverage (keyword density) ──
    cov_kw = _keyword_density(content, _COVERAGE_KEYWORDS)
    coverage = cov_kw

    return {
        "correctness": round(correctness, 1),
        "security": round(security, 1),
        "maintainability": round(maintainability, 1),
        "coverage": round(coverage, 1),
    }


def _dimensions_for_phase(phase: int) -> List[str]:
    """Return the active constitution dimensions for a given phase.

    P1:      correctness + security (TH-03=100%, TH-04=100%)
    P2-P3:   correctness + security + maintainability (TH-03/TH-04=100%, TH-05>90%)
    P4:      correctness + security + maintainability + coverage (TH-03~TH-06)
    P5-P8:   all 4 dimensions composite (TH-02 >=80%)

    P3 uses maintainability but NOT coverage as a constitution dimension.
    Coverage for P3 is checked via TH-11 (>=70% pytest coverage) separately.
    """
    if phase <= 1:
        return ["correctness", "security"]
    if phase <= 3:
        return ["correctness", "security", "maintainability"]
    return ["correctness", "security", "maintainability", "coverage"]


def _threshold_for_dimension(dim: str, phase: int) -> float:
    """Return the per-dimension threshold for a given phase.

    TH-03 correctness: =100% (P1-P4)
    TH-04 security: =100% (P1-P4)
    TH-05 maintainability: >90% (P2-P4)
    TH-06 coverage: >90% (P4)
    TH-02 composite: ≥80% (P5-P8)
    """
    if phase <= 4:
        thresholds = {
            "correctness": 100.0,
            "security": 100.0,
            "maintainability": 90.0,
            "coverage": 90.0,
        }
        return thresholds.get(dim, 80.0)
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

    phase_dir = _PHASE_DIR_MAP.get(phase, "docs")
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
        if phase <= 2:
            return ConstitutionResult(
                score=100.0,
                passed=True,
                violations=[],
                check_type=check_type,
                phase=phase,
                dimensions={"correctness": 100.0, "security": 100.0,
                           "maintainability": 100.0, "coverage": 100.0},
            )
        return ConstitutionResult(
            score=0.0,
            passed=False,
            violations=[{
                "file": str(docs_path),
                "score": 0.0,
                "message": "No markdown artifacts found for constitution check",
                "rule": "TH-02",
            }],
            check_type=check_type,
            phase=phase,
            dimensions={"correctness": 0.0, "security": 0.0,
                       "maintainability": 0.0, "coverage": 0.0},
        )

    # Aggregate per-dimension averages
    agg_dims: Dict[str, float] = {}
    for d, scores in all_dim_scores.items():
        agg_dims[d] = round(sum(scores) / len(scores), 1) if scores else 0.0

    active_dims = _dimensions_for_phase(phase)
    score = _aggregate_score(agg_dims, active_dims)
    const_threshold = get_constitution_threshold(phase)
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
                "rule": _DIM_RULE_MAP.get(dim, "TH-02"),
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
        RuntimeError: If strict=True and check fails.
    """
    path = Path(docs_path)

    if not path.exists():
        phase_dir_name = _PHASE_DIR_MAP.get(current_phase, "")
        alt_path = path.parent / phase_dir_name if path.name == "docs" else path
        if alt_path.exists():
            path = alt_path

    if not path.exists():
        if current_phase <= 2:
            return ConstitutionResult(
                score=100.0,
                passed=True,
                violations=[],
                check_type=check_type,
                phase=current_phase,
                check_mode=check_mode,
                dimensions={"correctness": 100.0, "security": 100.0,
                           "maintainability": 100.0, "coverage": 100.0},
            )
        result = ConstitutionResult(
            score=0.0,
            passed=False,
            violations=[{
                "file": str(path),
                "score": 0.0,
                "message": f"Directory not found: {path}",
                "rule": "TH-02",
            }],
            check_type=check_type,
            phase=current_phase,
            check_mode=check_mode,
            dimensions={"correctness": 0.0, "security": 0.0,
                       "maintainability": 0.0, "coverage": 0.0},
        )
        if strict:
            raise RuntimeError(f"Constitution check failed: directory not found: {path}")
        return result

    result = _scan_directory(path, current_phase, check_type)
    result.check_mode = check_mode

    if strict and not result.passed:
        raise RuntimeError(
            f"Constitution check FAILED: score={result.score:.0f}% "
            f"(threshold={get_constitution_threshold(current_phase)}%), "
            f"violations={len(result.violations)}, "
            f"dims={result.dimensions}"
        )

    return result
