"""Constitution package — HR compliance checking modules.

Modules:
- bvs_runner: BVS phase-order invariant checker (HR-03)
- citation_parser: citation and claims extractor (HR-07, HR-09)
- verification_constitution_checker: wrapper bridging to enforcement.constitution_as_code

Public API:
- load_constitution() → str
- get_quality_thresholds() → dict
- get_th_rules() → dict (TH-01~TH-17)
- get_phase_thresholds(phase) → dict
- check_quality_gate(code_metrics) → dict
- validate_constitution_compliance(project_path) → dict
- compile_constitution(path) → CompiledConstitution
- verify_agent_output(constitution, output) → dict
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import List, Dict, Optional as _Optional

# ---------------------------------------------------------------------------
# Re-exports from submodules
# ---------------------------------------------------------------------------
from constitution.bvs_runner import BVSRunner
from constitution.citation_parser import CitationParser
from constitution.verification_constitution_checker import VerificationConstitutionChecker

__all__ = [
    "BVSRunner",
    "CitationParser",
    "VerificationConstitutionChecker",
    "load_constitution",
    "get_quality_thresholds",
    "get_gate_thresholds",
    "get_constitution_threshold",
    "get_th_rules",
    "get_phase_thresholds",
    "get_error_levels",
    "check_quality_gate",
    "validate_constitution_compliance",
    "CompiledConstitution",
    "compile_constitution",
    "verify_agent_output",
]

# ---------------------------------------------------------------------------
# Core API (aligned with methodology-v2)
# ---------------------------------------------------------------------------

def load_constitution() -> str:
    """Load the team constitution document.

    Returns:
        Constitution content as string, or fallback message if absent.
    """
    const_path = Path(__file__).parent / "CONSTITUTION.md"
    if const_path.exists():
        return const_path.read_text(encoding="utf-8")
    return "CONSTITUTION.md not found"


def get_quality_thresholds() -> dict:
    """Return quality gate thresholds per dimension.

    Proxies through ConstitutionProfile — customizable via
    .methodology/constitution_profile.json → dimensions.

    Aligned with TH-03 (correctness=100%), TH-04 (security=100%),
    TH-05 (maintainability>90%), TH-06 (coverage>90%).
    """
    from core.quality_gate.constitution.profile import get_profile
    p = get_profile()
    return {
        "correctness": p.dimension_threshold("correctness"),
        "security": p.dimension_threshold("security"),
        "maintainability": p.dimension_threshold("maintainability"),
        "performance": 80,  # not a constitution dimension — hardware/platform metric
        "coverage": p.dimension_threshold("coverage"),
    }


def get_gate_thresholds() -> dict:
    """Return per-gate minimum composite scores (canonical source).

    Gate 1: ≥75 (per-FR, 3 dims)
    Gate 2: ≥75 (P3 exit, 10 dims)
    Gate 3: ≥80 (P4/P5 exit, 15 dims)
    Gate 4: ≥85 (P6 exit, 15 dims, Hermes APPROVE required)
    """
    return {1: 75.0, 2: 75.0, 3: 80.0, 4: 85.0}


def get_constitution_threshold(phase: int) -> float:
    """Return constitution score threshold for a given phase.

    Proxies through ConstitutionProfile — customizable via
    .methodology/constitution_profile.json → phases.N.composite_threshold.
    """
    from core.quality_gate.constitution.profile import get_profile
    return get_profile().composite_threshold(phase)


def get_th_rules() -> dict:
    """Return all TH-01 ~ TH-17 threshold rules.

    Canonical source aligned with methodology-v2 v9.1.
    Each entry: (metric, threshold, phases, verify_method).
    """
    return {
        "TH-01": ("ASPICE Compliance Rate", ">80%", (1, 2, 3, 4, 5, 6, 7, 8), "trace-check"),
        "TH-02": ("Constitution Total Score", ">=80%", (5, 6, 7, 8), "run-gate D12"),
        "TH-03": ("Constitution Correctness", "=100%", (1, 2, 3, 4), "run-constitution"),
        "TH-04": ("Constitution Security", "=100%", (1, 2, 3, 4), "run-constitution"),
        "TH-05": ("Constitution Maintainability", ">90%", (2, 3, 4), "run-constitution"),
        "TH-06": ("Constitution Test Coverage", ">90%", (4,), "run-constitution"),
        "TH-07": ("Logic Correctness Score", ">=90", (5, 6, 7, 8), "phase-verify"),
        "TH-08": ("AgentEvaluator Standard", ">=80", (1, 2), "evaluate"),
        "TH-09": ("AgentEvaluator Strict", ">=90", (3, 4, 5, 6, 7, 8), "evaluate --strict"),
        "TH-10": ("Test Pass Rate", "=100%", (3, 4, 5, 6, 7, 8), "pytest"),
        "TH-11": ("Unit Test Coverage", ">=70%", (3,), "coverage"),
        "TH-12": ("Unit Test Coverage", ">=80%", (4, 5, 6, 7, 8), "coverage"),
        "TH-13": ("SRS FR Coverage", "=100%", (4, 5, 6, 7, 8), "trace-check"),
        "TH-14": ("Specification Completeness", "=100%", (1,), "verify-spec"),
        "TH-15": ("Phase Truth", ">90%", (1, 2, 3, 4, 5, 6, 7, 8), "phase-verify"),
        "TH-16": ("Code-to-SAD Mapping Rate", "=100%", (3,), "trace-check"),
        "TH-17": ("FR-to-Test Mapping Rate", ">=90%", (4,), "trace-check"),
    }


def get_phase_thresholds(phase: int) -> dict:
    """Return the subset of TH rules applicable to a given phase.

    Args:
        phase: Pipeline phase number (1-8).

    Returns:
        Dict of TH-ID → (metric, threshold, phases, verify_method).
    """
    all_rules = get_th_rules()
    return {
        th_id: rule
        for th_id, rule in all_rules.items()
        if phase in rule[2]
    }


def get_error_levels() -> dict:
    """Return error level definitions."""
    return {
        "L1": {"name": "配置錯誤", "recoverable": False},
        "L2": {"name": "API 錯誤", "recoverable": True},
        "L3": {"name": "業務錯誤", "recoverable": True},
        "L4": {"name": "預期異常", "recoverable": True},
        "L5": {"name": "環境錯誤", "recoverable": False},
        "L6": {"name": "災難錯誤", "recoverable": False},
    }


def check_quality_gate(code_metrics: dict) -> dict:
    """Check whether code metrics meet quality thresholds.

    Args:
        code_metrics: dict with keys matching get_quality_thresholds()

    Returns:
        {"passed": bool, "details": dict, "failed": list, "summary": str}
    """
    thresholds = get_quality_thresholds()
    results = {}
    failed = []

    for metric, threshold in thresholds.items():
        actual = code_metrics.get(metric, 0)
        passed = actual >= threshold
        results[metric] = {"actual": actual, "threshold": threshold, "passed": passed}
        if not passed:
            failed.append(metric)

    return {
        "passed": len(failed) == 0,
        "details": results,
        "failed": failed,
        "summary": f"{len(failed)}/{len(thresholds)} metrics passed",
    }


def validate_constitution_compliance(project_path: str = ".") -> dict:
    """Validate project compliance with constitution.

    Args:
        project_path: Project root directory.

    Returns:
        Compliance result dict.
    """
    try:
        const_path = Path(project_path) / "constitution" / "CONSTITUTION.md"
        if not const_path.exists():
            return {"compliant": False, "message": "CONSTITUTION.md not found", "checks": []}
        const = compile_constitution(str(const_path))
        return {
            "compliant": True,
            "version": const.version,
            "hash": const.hash,
            "checks": [{"spec": s["name"], "hash": s["hash"]} for s in const.specs],
        }
    except Exception as e:
        return {"compliant": False, "message": str(e), "checks": []}


# ---------------------------------------------------------------------------
# Compiled Constitution (TDAD artifact)
# ---------------------------------------------------------------------------

class CompiledConstitution:
    """TDAD-style compiled constitution artifact.

    Immutable constraints, versioned compliance standards,
    verifiable behavioral specs.
    """

    def __init__(self, constitution_text: str) -> None:
        self.original_text = constitution_text
        self.version = self._compute_version(constitution_text)
        self.specs = self._parse_specs(constitution_text)
        self.hash = self._compute_hash(constitution_text)

    def _compute_version(self, text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()[:8]

    def _compute_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def _parse_specs(self, text: str) -> List[Dict]:
        specs = []
        sections = text.split("\n## ")
        for section in sections:
            if section.strip():
                specs.append({
                    "name": section.split("\n")[0].strip(),
                    "content": section,
                    "hash": hashlib.md5(section.encode()).hexdigest()[:8],
                })
        return specs

    def verify(self, agent_output: str) -> dict:
        """Verify agent output against constitution rules.

        Returns:
            {"compliant": bool, "violations": list, "score": float, "version": str, "hash": str}
        """
        violations = []

        forbidden_keywords = ["bypass", "skip", "--no-verify"]
        for kw in forbidden_keywords:
            if kw in agent_output.lower():
                violations.append({
                    "keyword": kw,
                    "severity": "high",
                    "description": f"Forbidden keyword '{kw}' found",
                })

        if not re.search(r"\[[A-Z]+-\d+\]", agent_output):
            violations.append({
                "keyword": "task_id",
                "severity": "medium",
                "description": "No task_id found in output",
            })

        score = max(0, 100 - len(violations) * 20)
        return {
            "compliant": len(violations) == 0,
            "violations": violations,
            "score": score,
            "version": self.version,
            "hash": self.hash,
        }

    def to_json(self) -> str:
        import json
        return json.dumps({
            "version": self.version,
            "hash": self.hash,
            "specs_count": len(self.specs),
            "specs": self.specs,
        }, indent=2, ensure_ascii=False)


def compile_constitution(constitution_path: _Optional[str] = None) -> CompiledConstitution:
    """Compile constitution into immutable artifact.

    Args:
        constitution_path: Path to CONSTITUTION.md. Defaults to package CONSTITUTION.md.

    Returns:
        CompiledConstitution instance.
    """
    path = Path(constitution_path) if constitution_path else Path(__file__).parent / "CONSTITUTION.md"
    text = path.read_text(encoding="utf-8")
    return CompiledConstitution(text)


def verify_agent_output(constitution: CompiledConstitution, output: str) -> dict:
    """Verify agent output against compiled constitution.

    Returns:
        Verification result dict.
    """
    return constitution.verify(output)
