#!/usr/bin/env python3
"""
Constitution Runner — executable quality gate constitution check.

Called by:
- phase_hooks.py: preflight_constitution() / postflight_constitution()
- framework_enforcer.py: check_constitution()
- cli.py: cmd_constitution() / run-phase integration

Provides:
    run_constitution_check(check_type, docs_path, current_phase=1, check_mode="preflight")
    → ConstitutionResult(score, passed, violations)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict


@dataclass
class ConstitutionResult:
    """Result of a constitution compliance check."""

    score: float = 0.0
    passed: bool = False
    violations: List[Dict] = field(default_factory=list)
    check_type: str = ""
    phase: int = 1
    check_mode: str = "preflight"

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "passed": self.passed,
            "violations": len(self.violations),
            "violation_details": self.violations,
            "check_type": self.check_type,
            "phase": self.phase,
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

# Keywords that indicate constitution compliance in artifacts
_COMPLIANCE_KEYWORDS: List[str] = [
    "quality gate",
    "test coverage",
    "constitution",
    "traceability",
    "srs",
    "sad",
    "fr-",
    "nfr-",
    "acceptance criteria",
]


def _scan_file_compliance(file_path: Path) -> float:
    """Scan a single file for constitution compliance signals.

    Returns a score 0-100 based on keyword density and structural completeness.
    """
    if not file_path.exists() or not file_path.is_file():
        return 0.0

    try:
        content = file_path.read_text(encoding="utf-8").lower()
    except Exception:
        return 0.0

    if len(content) < 100:
        return 0.0

    score = 0.0

    # 1. Keyword presence (40%)
    kw_hits = sum(1 for kw in _COMPLIANCE_KEYWORDS if kw in content)
    kw_score = min(kw_hits / max(len(_COMPLIANCE_KEYWORDS), 1), 1.0) * 40

    # 2. Structural completeness — check for sections (30%)
    section_count = content.count("\n## ") + content.count("\n# ")
    structure_score = min(section_count / 5.0, 1.0) * 30

    # 3. FR/NFR/ task reference presence (30%)
    has_fr = "fr-" in content or "fr_" in content
    has_nfr = "nfr-" in content or "nfr_" in content
    has_trace = "trace" in content or "matrix" in content
    ref_score = (0.1 if has_fr else 0) + (0.1 if has_nfr else 0) + (0.1 if has_trace else 0)
    ref_score *= 100

    score = min(kw_score + structure_score + ref_score, 100.0)
    return score


def _scan_directory(docs_path: Path, phase: int, check_type: str) -> ConstitutionResult:
    """Scan docs directory for constitution compliance.

    Args:
        docs_path: Path to the docs/ directory.
        phase: Current pipeline phase (1-8).
        check_type: One of "all", "srs", "sad", "implementation", etc.

    Returns:
        ConstitutionResult with score, passed, and violations.
    """
    violations: List[Dict] = []
    scores: List[float] = []

    phase_dir = _PHASE_DIR_MAP.get(phase, "docs")
    target_dirs = [docs_path]

    # If a numbered phase directory exists, scan it too
    numbered_dir = docs_path.parent / phase_dir if docs_path.name == "docs" else docs_path
    if numbered_dir.exists():
        target_dirs.append(numbered_dir)

    files_scanned = 0
    for directory in target_dirs:
        if not directory.exists():
            continue
        for item in directory.rglob("*.md"):
            if item.name.startswith("."):
                continue
            file_score = _scan_file_compliance(item)
            scores.append(file_score)
            files_scanned += 1
            if file_score < 30:
                violations.append({
                    "file": str(item.relative_to(docs_path.parent)),
                    "score": round(file_score, 1),
                    "message": f"Low constitution compliance ({file_score:.0f}%)",
                    "rule": "TH-02",
                })

    if not scores:
        if phase <= 2:
            return ConstitutionResult(
                score=100.0,
                passed=True,
                violations=[],
                check_type=check_type,
                phase=phase,
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
        )

    avg_score = sum(scores) / len(scores)
    const_threshold = 60.0 if phase <= 4 else 80.0
    passed = avg_score >= const_threshold

    return ConstitutionResult(
        score=round(avg_score, 1),
        passed=passed,
        violations=violations,
        check_type=check_type,
        phase=phase,
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
        ConstitutionResult with .score, .passed, .violations.

    Raises:
        RuntimeError: If strict=True and check fails.
    """
    path = Path(docs_path)

    # If docs_path doesn't exist, try to infer from project root
    if not path.exists():
        # Try looking for phase-named directories
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
        )
        if strict:
            raise RuntimeError(f"Constitution check failed: directory not found: {path}")
        return result

    result = _scan_directory(path, current_phase, check_type)
    result.check_mode = check_mode

    if strict and not result.passed:
        raise RuntimeError(
            f"Constitution check FAILED: score={result.score:.0f}% "
            f"(threshold={60 if current_phase <= 4 else 80}%), "
            f"violations={len(result.violations)}"
        )

    return result
