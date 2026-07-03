#!/usr/bin/env python3
"""
confidence_scorer.py — Script-based confidence scoring (no LLM).

Computes a deterministic confidence score (0-100) from tool outputs.
Used to decide whether P1/P2 push-checkpoint can be automatically approved.

Metrics (C1-C7):
    C1  artifact_completeness  Phase artifacts present + non-empty
    C2  test_coverage          pytest-cov percentage
    C3  linting                ruff violation density
    C4  type_safety            pyright/mypy error count
    C5  test_pass_rate         pytest passed / total
    C6  security               bandit HIGH/MEDIUM finding density
    C7  traceability           FR coverage in quality_manifest.json

Usage:
    from core.quality_gate.confidence_scorer import compute_confidence, should_auto_approve_p1p2

    conf = compute_confidence(project_path, phase=1)
    if should_auto_approve_p1p2(conf):
        # skip manual push-checkpoint review
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

# ── Thresholds ────────────────────────────────────────────────────────────────

AUTO_APPROVE_P1P2_THRESHOLD: float = 88.0      # push-checkpoint auto-pass

# ── Phase → int mapping (mirrors Phase enum in phase_artifact_enforcer.py) ────

_PHASE_INT_TO_ENUM_NAME = {
    1: "SPECIFY",
    2: "PLAN",
    3: "IMPLEMENT",
    4: "VERIFY",
    5: "SYSTEM_TEST",
    6: "QUALITY",
    7: "RISK",
    8: "CONFIG",
    9: "MAINTENANCE",
}

# Phases with source code (linting/coverage/type safety apply)
_CODE_PHASES = {3, 4, 5, 6, 7, 8, 9}

# ── Metric weights by phase type ──────────────────────────────────────────────

_WEIGHTS_DOC = {          # P1/P2: docs only
    "artifact_completeness": 0.65,
    "traceability":          0.35,
}

_WEIGHTS_CODE = {         # P3-P8: code + docs
    "artifact_completeness": 0.15,
    "test_coverage":         0.20,
    "linting":               0.20,
    "type_safety":           0.15,
    "test_pass_rate":        0.15,
    "security":              0.10,
    "traceability":          0.05,
}


def _get_weights(phase: int, available: set[str]) -> dict[str, float]:
    """Return weight map filtered to available metrics."""
    base = _WEIGHTS_CODE if phase in _CODE_PHASES else _WEIGHTS_DOC
    filtered = {k: v for k, v in base.items() if k in available}
    # Re-normalise so weights always sum to 1.0
    total = sum(filtered.values())
    if total == 0:
        return {}
    return {k: v / total for k, v in filtered.items()}


# ── Individual metric functions ────────────────────────────────────────────────
# Each returns (score: float | None, detail: str)
# score=None → metric unavailable, excluded from composite

def _score_artifact_completeness(project: Path, phase: int, **_kw) -> tuple[Optional[float], str]:
    """C1: Check required phase artifacts exist and are non-empty."""
    try:
        # Import inline to avoid circular import at module level
        _import_path = str(Path(__file__).parent.parent)
        if _import_path not in sys.path:
            sys.path.insert(0, _import_path)
        from quality_gate.phase_artifact_enforcer import Phase, PhaseArtifactRegistry
    except ImportError:
        return None, "phase_artifact_enforcer unavailable"

    phase_name = _PHASE_INT_TO_ENUM_NAME.get(phase)
    if not phase_name:
        return None, f"unknown phase {phase}"

    try:
        phase_enum = Phase[phase_name]
    except KeyError:
        return None, f"Phase enum {phase_name} not found"

    registry = PhaseArtifactRegistry(str(project))
    required = registry.PHASE_ARTIFACTS.get(phase_enum, {}).get("artifacts", [])

    if not required:
        return 100.0, "no artifacts required for this phase"

    present = sum(
        1 for a in required
        if (project / a).exists() and (project / a).stat().st_size > 0
    )
    score = present / len(required) * 100.0
    return score, f"{present}/{len(required)} artifacts present"


def _score_test_coverage(project: Path, timeout: int = 90, **_kw) -> tuple[Optional[float], str]:
    """C2: pytest --cov percentage (reads cached report if fresh)."""
    # Try to read an existing coverage.json first
    def _extract_pct(data: dict) -> Optional[float]:
        """Extract coverage % from coverage.json totals; handles old and new pytest-cov schemas."""
        totals = data.get("totals", {})
        pct = totals.get("percent_covered")
        if pct is None:
            # pytest-cov ≥ 4.x may emit percent_covered_display (string) or covered_lines/num_statements
            raw = totals.get("percent_covered_display")
            if raw is not None:
                try:
                    pct = float(str(raw).rstrip("%"))
                except ValueError:
                    pass
        if pct is None:
            num = totals.get("num_statements", 0)
            covered = totals.get("covered_lines", 0)
            if num:
                pct = covered / num * 100.0
        return float(pct) if pct is not None else None

    for candidate in [project / "coverage.json", project / ".coverage.json"]:
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                pct = _extract_pct(data)
                if pct is not None:
                    return pct, f"coverage={pct:.1f}% (cached)"
            except (json.JSONDecodeError, KeyError):
                pass

    # Run pytest --cov
    try:
        subprocess.run(
            [sys.executable, "-m", "pytest",
             "--cov=.", "--cov-report=json", "-q", "--tb=no",
             "--no-header"],
            cwd=str(project),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        cov_file = project / "coverage.json"
        if cov_file.exists():
            data = json.loads(cov_file.read_text(encoding="utf-8"))
            pct = _extract_pct(data) or 0.0
            return float(pct), f"coverage={pct:.1f}%"
        return None, "coverage.json not produced"
    except subprocess.TimeoutExpired:
        return None, "pytest-cov timeout"
    except FileNotFoundError:
        return None, "pytest not found"


def _score_linting(project: Path, timeout: int = 30, **_kw) -> tuple[Optional[float], str]:
    """C3: ruff check — score decreases with violation count."""
    try:
        result = subprocess.run(
            ["ruff", "check", ".", "--output-format=json", "--quiet"],
            cwd=str(project),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        try:
            violations = json.loads(result.stdout or "[]")
            n = len(violations)
        except json.JSONDecodeError:
            n = result.stdout.count("\n") if result.stdout else 0

        # score = 100 - 2 * violations, floor at 0
        score = max(0.0, 100.0 - n * 2.0)
        return score, f"{n} violation(s) → score={score:.0f}"
    except subprocess.TimeoutExpired:
        return None, "ruff timeout"
    except FileNotFoundError:
        return None, "ruff not installed"


def _score_type_safety(project: Path, timeout: int = 60, **_kw) -> tuple[Optional[float], str]:
    """C4: pyright error count — score decreases with error count."""
    try:
        result = subprocess.run(
            ["pyright", "--outputjson", "."],
            cwd=str(project),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        try:
            data = json.loads(result.stdout or "{}")
            errors = data.get("summary", {}).get("errorCount", 0)
        except json.JSONDecodeError:
            errors = result.stdout.count("error:") + result.stderr.count("error:")

        score = max(0.0, 100.0 - errors * 2.0)
        return score, f"{errors} error(s) → score={score:.0f}"
    except subprocess.TimeoutExpired:
        return None, "pyright timeout"
    except FileNotFoundError:
        pass  # try mypy

    try:
        result = subprocess.run(
            [sys.executable, "-m", "mypy", ".", "--ignore-missing-imports",
             "--no-error-summary"],
            cwd=str(project),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        errors = result.stdout.count(": error:") + result.stderr.count(": error:")
        score = max(0.0, 100.0 - errors * 2.0)
        return score, f"{errors} error(s) [mypy] → score={score:.0f}"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None, "pyright/mypy not available"


def _score_test_pass_rate(project: Path, timeout: int = 90, **_kw) -> tuple[Optional[float], str]:
    """C5: pytest pass/fail ratio."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--tb=no", "-q", "--no-header"],
            cwd=str(project),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        # Parse "N passed, M failed" from output
        output = result.stdout + result.stderr
        passed_m = re.search(r"(\d+) passed", output)
        failed_m = re.search(r"(\d+) failed", output)
        passed = int(passed_m.group(1)) if passed_m else 0
        failed = int(failed_m.group(1)) if failed_m else 0

        if passed == 0 and failed == 0:
            return None, "no tests found"

        score = passed / (passed + failed) * 100.0
        return score, f"{passed} passed / {passed+failed} total"
    except subprocess.TimeoutExpired:
        return None, "pytest timeout"
    except FileNotFoundError:
        return None, "pytest not found"


def _score_security(project: Path, timeout: int = 60, **_kw) -> tuple[Optional[float], str]:
    """C6: bandit severity findings — HIGH=-20, MEDIUM=-5 each."""
    try:
        result = subprocess.run(
            ["bandit", "-r", ".", "-f", "json", "-q"],
            cwd=str(project),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        try:
            data = json.loads(result.stdout or "{}")
            results = data.get("results", [])
            high = sum(1 for r in results if r.get("issue_severity") == "HIGH")
            medium = sum(1 for r in results if r.get("issue_severity") == "MEDIUM")
            low = sum(1 for r in results if r.get("issue_severity") == "LOW")
        except json.JSONDecodeError:
            return None, "bandit output unparseable"

        score = max(0.0, 100.0 - high * 20.0 - medium * 5.0 - low * 1.0)
        return score, f"HIGH={high} MED={medium} LOW={low} → score={score:.0f}"
    except subprocess.TimeoutExpired:
        return None, "bandit timeout"
    except FileNotFoundError:
        return None, "bandit not installed"


def _score_traceability(project: Path, **_kw) -> tuple[Optional[float], str]:
    """C7: FR coverage from quality_manifest.json."""
    from core.utils.project_layout import ProjectLayout
    manifest_path = ProjectLayout(project).quality_manifest_path
    if not manifest_path.exists():
        return 50.0, "quality_manifest.json not found (partial credit)"

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, "quality_manifest.json is not valid JSON"

    fr_ids: list = manifest.get("fr_ids", [])
    if not fr_ids:
        return 50.0, "no FR IDs defined in manifest"

    gates = manifest.get("gate_results", {})
    # Gate 1 is per-FR (key: gate1_FR-xx); Gates 2-4 are project-level (key: gate2/3/4).
    # A project-level gate pass means the whole project cleared that phase, so all
    # FRs get credit.  A generic "gate1" key does NOT credit all FRs — it must be
    # the FR-specific key "gate1_{fr}" to avoid false positives.
    project_gate_passed = any(
        gates.get(f"gate{g}", {}).get("quality_complete")
        for g in [2, 3, 4]
    )
    passed_frs = sum(
        1 for fr in fr_ids
        if (
            project_gate_passed
            or gates.get(f"gate1_{fr}", {}).get("quality_complete")
        )
    )
    # Even with 0 gates passed, give partial credit if FRs are defined
    if passed_frs == 0:
        return 40.0, f"{len(fr_ids)} FR(s) defined, 0 gates passed"

    score = passed_frs / len(fr_ids) * 100.0
    return score, f"{passed_frs}/{len(fr_ids)} FRs with gate pass"


# ── Ordered metric registry ────────────────────────────────────────────────────

_METRIC_FUNCS: dict[str, Callable[..., tuple[Optional[float], str]]] = {
    "artifact_completeness": _score_artifact_completeness,
    "test_coverage":         _score_test_coverage,
    "linting":               _score_linting,
    "type_safety":           _score_type_safety,
    "test_pass_rate":        _score_test_pass_rate,
    "security":              _score_security,
    "traceability":          _score_traceability,
}


# ── Public API ─────────────────────────────────────────────────────────────────

def compute_confidence(
    project: Path,
    phase: int,
    timeout: int = 60,
) -> dict:
    """Compute script-based confidence score.

    Returns:
        {
            "composite": float,          # 0-100 weighted average
            "scores": {
                metric: {"score": float, "detail": str}
            },
            "skipped": list[str],        # metrics unavailable/not-applicable
        }
    """
    raw: dict[str, tuple] = {}
    skipped: list[str] = []

    # Decide which metrics to attempt for this phase
    applicable = set(_METRIC_FUNCS.keys())
    if phase not in _CODE_PHASES:
        applicable -= {"test_coverage", "linting", "type_safety", "test_pass_rate", "security"}

    for name, fn in _METRIC_FUNCS.items():
        if name not in applicable:
            skipped.append(name)
            continue
        score, detail = fn(project=project, phase=phase, timeout=timeout)
        if score is None:
            skipped.append(name)
        else:
            raw[name] = (score, detail)

    scores = {name: {"score": v[0], "detail": v[1]} for name, v in raw.items()}
    weights = _get_weights(phase, set(scores.keys()))

    composite = (
        sum(scores[m]["score"] * w for m, w in weights.items())
        if weights else 0.0
    )

    return {
        "composite": composite,
        "scores": scores,
        "skipped": skipped,
    }


def should_auto_approve_p1p2(conf: dict) -> bool:
    """Return True if P1/P2 confidence meets auto-approve threshold."""
    return conf.get("composite", 0.0) >= AUTO_APPROVE_P1P2_THRESHOLD


def format_confidence_report(conf: dict) -> str:
    """Human-readable confidence breakdown for CLI output."""
    lines = [f"  Confidence composite: {conf['composite']:.1f}/100"]
    for name, data in conf.get("scores", {}).items():
        lines.append(f"    {name:<24} {data['score']:>5.1f}  ({data['detail']})")
    if conf.get("skipped"):
        lines.append(f"  Skipped: {', '.join(conf['skipped'])}")
    return "\n".join(lines)
