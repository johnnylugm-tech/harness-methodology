"""Constitution package — HR compliance checking modules.

Modules:
- bvs_runner: BVS phase-order invariant checker (HR-03)
- citation_parser: citation and claims extractor (HR-07, HR-09)
- verification_constitution_checker: wrapper bridging to enforcement.constitution_as_code

Public API:
- load_constitution() → str
- get_quality_thresholds() → dict
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

    These are the canonical thresholds; all downstream consumers
    (enforcement_config, verification_gate, framework_enforcer)
    should derive their values from here.
    """
    return {
        "correctness": 80,
        "security": 100,
        "maintainability": 70,
        "performance": 80,
        "coverage": 80,
    }


def get_gate_thresholds() -> dict:
    """Return per-gate minimum composite scores (canonical source).

    Gate 1: ≥75 (per-FR, 3 dims)
    Gate 2: ≥75 (P3 exit, 7 dims)
    Gate 3: ≥80 (P4/P5 exit, 12 dims)
    Gate 4: ≥85 (P6 exit, 12 dims, Hermes APPROVE required)
    """
    return {1: 75.0, 2: 75.0, 3: 80.0, 4: 85.0}


def get_constitution_threshold(phase: int) -> float:
    """Return constitution score threshold for a given phase.

    P1-P4: ≥60 (basic framework compliance)
    P5-P8: ≥80 (full constitution compliance)
    """
    return 60.0 if phase <= 4 else 80.0


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
