#!/usr/bin/env python3
"""
Score Aggregation: Computes weighted overall score from per-dimension scores

Identifies failing dimensions sorted by impact (gap × normalized_weight).
Outputs JSON with overall_score, meets_target, failing_dimensions, breakdown.

Issue-driven completion: surfaces open_critical_count / open_high_count from
the issue registry so early-stop can gate on quality, not score alone.
"""

import os
import sys
import json
from pathlib import Path
from typing import Any

# Local import for issue registry integration
sys.path.insert(0, str(Path(__file__).parent))
try:
    import issue_tracker
except ImportError:
    issue_tracker = None


# ---------------------------------------------------------------------------
# Protocol Compliance Validator
# ---------------------------------------------------------------------------

class ScoreProtocolError(Exception):
    """Score files fail Execution Contract checks — agent MUST redo the dim.

    This is the machine-enforced gate that prevents the main agent from
    skipping tool execution or fabricating scores.
    """

    def __init__(self, errors: list[dict[str, Any]]):
        self.errors = errors
        lines = [
            f"PROTOCOL COMPLIANCE FAILED — {len(errors)} dimension(s) must be fixed:"
        ]
        for e in errors:
            lines.append(f"  [{e['dimension']}]")
            for issue in e["issues"]:
                lines.append(f"    - {issue}")
        super().__init__("\n".join(lines))



def _resolve_tool_outputs(tool_outputs: Any) -> str:
    """Normalize tool_outputs field (string or list) to a single path."""
    if isinstance(tool_outputs, list):
        return next((str(p) for p in tool_outputs if p), "")
    return str(tool_outputs) if tool_outputs else ""


def validate_score_file(
    dim_name: str,
    score_data: dict,
    project_root: "Path | None" = None,
) -> list[str]:
    """Validate a single score file against the pure-tool scoring contract.

    Returns list of issue strings. Empty list = valid.
    Rejecting rules (R1, R2, R4, R5, R8) block gate scoring.
    R7 is informational only (handled in _auto_fix_scores).

    Active rules:
      R1 — Required fields present.
      R2 — tool_outputs path exists and is non-empty when tool_score is set.
      R4 — score must equal tool_score (LLM cannot adjust the numeric score).
      R5 — Every finding must include an evidence field.
      R8 — tool_score must not be null for any dimension (all tiers require tools).

    Removed rules (LLM scoring abolished):
      R3  — Tier 1/2 gemini/hermes provider requirement.
      R6  — Tier 3 + llm_score ≥ 85 inflation gate.
      R8b — objective_primary llm deviation warning.

    Args:
        dim_name: dimension key (used in error context).
        score_data: parsed score JSON.
        project_root: used to resolve relative tool_outputs paths (R2).
            If None, paths are resolved against CWD (less reliable).
    """
    issues: list[str] = []

    ts = score_data.get("tool_score")
    sc = score_data.get("score")
    tool_outputs = _resolve_tool_outputs(score_data.get("tool_outputs", ""))

    # R1: Required fields must exist.
    # llm_score / llm_tier / llm_provider are now optional annotation fields.
    required = ["dimension", "round", "tool_score", "score", "tool_outputs"]
    for field in required:
        if field not in score_data:
            issues.append(f"R1: missing required field '{field}'")

    # R2: tool_outputs must reference an existing, non-empty file when tool_score is set.
    if tool_outputs:
        raw_path = Path(tool_outputs)
        output_path = (
            (project_root / raw_path) if (project_root and not raw_path.is_absolute())
            else raw_path
        )
        if not output_path.exists():
            issues.append(
                f"R2: [{dim_name}] tool_outputs '{tool_outputs}' does not exist — "
                "tool output file must be present"
            )
        elif output_path.stat().st_size == 0 and ts is not None:
            issues.append(
                f"R2: [{dim_name}] tool_outputs is empty but tool_score={ts} — "
                "cannot assign score without tool output evidence"
            )
    elif ts is not None:
        issues.append(
            f"R2: [{dim_name}] tool_outputs path is empty but tool_score is non-null"
        )

    # R4: score must equal tool_score — LLM annotation cannot adjust the numeric score.
    if ts is not None and sc is not None:
        if abs(sc - ts) > 1.5:
            issues.append(
                f"R4: [{dim_name}] score={sc} != tool_score={ts} — "
                "score must equal tool_score; LLM annotation cannot adjust the numeric score"
            )

    # R5: Every finding needs evidence.
    for i, f in enumerate(score_data.get("findings", [])):
        if not f.get("evidence"):
            msg_snip = (f.get("message") or "?")[:80]
            issues.append(
                f"R5: finding[{i}] ('{msg_snip}') missing 'evidence' field"
            )

    # R8: tool_score must not be null for ANY dimension.
    # All tiers require tool execution — there is no LLM fallback for scoring.
    if ts is None:
        issues.append(
            f"R8: [{dim_name}] tool_score=null is not permitted. "
            "Install the required tool and re-evaluate from Step 1 of evaluate_dimension.md. "
            "init-project blocks on missing tools; run-gate pre-checks before evaluation starts."
        )

    return issues


def _auto_fix_scores(scores: dict) -> list[str]:
    """Apply automatic corrections. Returns warning messages."""
    warnings: list[str] = []

    for dim_name, score_data in scores.items():
        ts = score_data.get("tool_score")
        sc = score_data.get("score")

        # R4 auto-fix: enforce score = tool_score
        if ts is not None and sc is not None:
            if abs(sc - ts) > 1.5:
                score_data["score"] = ts
                score_data["_score_autofixed"] = True
                score_data["_score_autofix_from"] = sc
                warnings.append(
                    f"{dim_name}: score auto-fixed {sc} → {ts} "
                    f"(tool_score={ts}; LLM annotation cannot override)"
                )

        # R7: flag missing tool_note when tool_score is null (warning only)
        if score_data.get("tool_score") is None:
            if "tool_note" not in score_data:
                warnings.append(
                    f"{dim_name}: tool_score=null but no 'tool_note' "
                    "— explain why the tool was unavailable"
                )

    return warnings


def _validate_all_scores(scores: dict, project_root: "Path | None" = None):
    """Validate all score files. Raises ScoreProtocolError on rejection-level failures.

    Auto-fix is applied first (R4: score = tool_score), then hard checks (R1, R2, R4, R5, R8).
    R7 (missing tool_note) is a warning only and does not block scoring.

    Args:
        scores: mapping of dim_name → score_data.
        project_root: project root for resolving relative tool_outputs paths (R2).
    """
    # Phase 1: auto-fix (R4) and collect soft warnings (R7)
    warnings = _auto_fix_scores(scores)
    for w in warnings:
        print(f"[score.py] WARNING: {w}", file=sys.stderr)

    # Phase 2: hard validation (R1-R6)
    all_errors = []
    for dim_name, score_data in scores.items():
        issues = validate_score_file(dim_name, score_data, project_root=project_root)
        if issues:
            all_errors.append({"dimension": dim_name, "issues": issues})

    if all_errors:
        raise ScoreProtocolError(all_errors)


def load_scores(round_dir):
    """
    Load all dimension scores from round directory

    Expected: .sessi-work/round_<n>/scores/*.json
    Each file contains: {"score": 0-100, ...} and optionally "dimension";
    if omitted, the dimension name is inferred from the filename stem.
    """
    scores_dir = Path(round_dir) / "scores"
    if not scores_dir.exists():
        raise FileNotFoundError(f"Scores directory not found: {scores_dir}")

    scores = {}
    for score_file in sorted(scores_dir.glob("*.json")):
        with open(score_file, "r") as f:
            dim_score = json.load(f)
            # Support both explicit "dimension" key and filename-based inference
            dim_name = dim_score.get("dimension", score_file.stem)
            scores[dim_name] = dim_score

    if not scores:
        raise ValueError(f"No score files found in {scores_dir}")

    # Infer project root: assumes round_dir == <project>/.sessi-work/round_N/
    # (parent = .sessi-work/, parent.parent = project root).
    # Implicit contract: all callers — harness_cli.py main() and plan-phase —
    # follow this layout. If load_scores() is ever invoked with an arbitrary
    # directory structure, pass project_root explicitly to _validate_all_scores()
    # instead of relying on this inference.
    project_root = Path(round_dir).resolve().parent.parent
    _validate_all_scores(scores, project_root=project_root)
    return scores


def _apply_crg_subscores(scores, crg_metrics):
    """
    CRG is the authoritative scorer for structural dimensions.

    Architecture and error_handling scores come DIRECTLY from CRG metrics
    (not from LLM evaluation). Like ruff for linting or mypy for type_safety,
    CRG is the sole scoring source for structural dimensions.

    Applied to:
      architecture     ← community_cohesion.score (only)
      error_handling   ← flow_coverage.score (only)

    Raises RuntimeError if crg_metrics is missing when structural dims are present.
    """
    if not crg_metrics:
        if "architecture" in scores or "error_handling" in scores:
            raise RuntimeError(
                "CRG metrics required for structural dimension scoring "
                "(architecture, error_handling). Run crg_analysis.py metrics first."
            )
        return {}

    adjustments = {}

    # architecture = CRG community_cohesion (authoritative — not min with LLM)
    cohesion = (crg_metrics.get("community_cohesion") or {}).get("score")
    if "architecture" in scores:
        if cohesion is None:
            raise RuntimeError(
                "CRG community_cohesion.score missing from crg_metrics — "
                "cannot score architecture dimension. "
                "Run crg_analysis.py metrics first."
            )
        scores["architecture"]["score"] = cohesion
        scores["architecture"]["scorer"] = "crg"
        scores["architecture"]["crg_cohesion_score"] = cohesion
        adjustments["architecture"] = {
            "score": cohesion,
            "source": "crg_community_cohesion",
        }

    # error_handling = CRG flow_coverage (authoritative — not min with LLM)
    flow = (crg_metrics.get("flow_coverage") or {}).get("score")
    if "error_handling" in scores:
        if flow is None:
            raise RuntimeError(
                "CRG flow_coverage.score missing from crg_metrics — "
                "cannot score error_handling dimension. "
                "Run crg_analysis.py metrics first."
            )
        scores["error_handling"]["score"] = flow
        scores["error_handling"]["scorer"] = "crg"
        scores["error_handling"]["crg_flow_score"] = flow
        adjustments["error_handling"] = {
            "score": flow,
            "source": "crg_flow_coverage",
        }

    return adjustments


def compute_overall_score(scores, config, registry=None, crg_metrics=None):
    """
    Compute weighted overall score from per-dimension scores

    Args:
        scores: dict of dimension_name -> {score, tool_score, llm_score, ...}
        config: resolved config with dimensions and weights
        registry: optional issue-registry dict for open-issue counts
        crg_metrics: optional dict from crg_analysis.py metrics output.
            When provided, architecture/error_handling scores are min'd
            against the CRG community-cohesion / flow-coverage sub-scores.

    Returns:
        {
            "overall_score": float (0-100),
            "meets_target": bool,          # score gate only
            "quality_complete": bool,      # score gate AND no open critical/high
            "score_gate": int,
            "open_critical_count": int,
            "open_high_count": int,
            "open_medium_count": int,
            "open_total": int,
            "failing_dimensions": [...],
            "breakdown": {...},
            "crg_adjustments": {...}       # what CRG pulled down, and why
        }
    """
    # Apply CRG sub-score adjustments first (deep integration)
    crg_adjustments = _apply_crg_subscores(scores, crg_metrics) or {}

    dimensions = config["dimensions"]
    # Support both legacy `target` and new `score_gate` naming
    quality_cfg = config.get("quality", {})
    score_gate = quality_cfg.get("score_gate", quality_cfg.get("target", 85))

    breakdown = {}
    weighted_sum = 0
    weight_sum = 0

    for dim_name, dim_config in dimensions.items():
        if not dim_config.get("enabled", False):
            continue

        if dim_name not in scores:
            raise ValueError(f"Missing score for dimension: {dim_name}")

        dim_score = scores[dim_name]
        score = dim_score.get("score", 0)
        weight = dim_config["weight"]

        weighted_score = score * weight
        weighted_sum += weighted_score
        weight_sum += weight

        dim_target = dim_config.get("target", 100)
        gap = max(0, dim_target - score)

        breakdown[dim_name] = {
            "score": score,
            "target": dim_target,
            "gap": gap,
            "weight": weight,
            "weighted_score": weighted_score,
        }

    # Overall score (normalized by enabled weights)
    overall_score = weighted_sum / weight_sum if weight_sum > 0 else 0

    # Identify failing dimensions (sorted by impact = gap × weight)
    failing = []
    for dim_name, dim_info in breakdown.items():
        if dim_info["gap"] > 0:
            impact = dim_info["gap"] * dim_info["weight"]
            failing.append(
                {
                    "dimension": dim_name,
                    "score": dim_info["score"],
                    "target": dim_info["target"],
                    "gap": dim_info["gap"],
                    "weight": dim_info["weight"],
                    "impact": impact,
                }
            )

    # Sort by impact descending
    failing.sort(key=lambda x: x["impact"], reverse=True)

    # Issue-registry integration (issue-driven completion)
    open_critical = open_high = open_medium = open_total = 0
    if registry is not None and issue_tracker is not None:
        s = issue_tracker.summary(registry)
        open_critical = s.get("open_critical", 0)
        open_high = s.get("open_high", 0)
        open_medium = s.get("open_medium", 0)
        open_total = s.get("open_total", 0)

    meets_score_gate = overall_score >= score_gate
    quality_complete = meets_score_gate and open_critical == 0 and open_high == 0

    return {
        "overall_score": round(overall_score, 2),
        "score_gate": score_gate,
        "target": score_gate,  # legacy alias for backward compat
        "meets_target": meets_score_gate,
        "quality_complete": quality_complete,
        "open_critical_count": open_critical,
        "open_high_count": open_high,
        "open_medium_count": open_medium,
        "open_total": open_total,
        "failing_dimensions": failing,
        "breakdown": breakdown,
        "crg_adjustments": crg_adjustments,
    }


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <round_dir> [config.json] [issue_registry.json]")
        print("  round_dir: path to .sessi-work/round_<n>")
        print("  config.json: resolved config (optional, uses defaults if omitted)")
        print("  issue_registry.json: persistent issue registry (optional)")
        print(
            "  env CRG_METRICS_PATH: path to crg_metrics.json (default: .sessi-work/crg_metrics.json)"
        )
        sys.exit(1)

    round_dir = sys.argv[1]
    config_path = sys.argv[2] if len(sys.argv) > 2 else None
    registry_path = sys.argv[3] if len(sys.argv) > 3 else None

    try:
        # Load scores
        scores = load_scores(round_dir)

        # Load config
        if config_path:
            with open(config_path, "r") as f:
                config = json.load(f)
        else:
            # Use minimal defaults if no config provided
            config = {
                "quality": {"score_gate": 85},
                "dimensions": {
                    dim: {"enabled": True, "weight": 1.0 / len(scores)}
                    for dim in scores.keys()
                },
            }

        # Load issue registry (optional but recommended)
        registry = None
        if registry_path and Path(registry_path).exists() and issue_tracker is not None:
            registry = issue_tracker.load(registry_path)
        elif issue_tracker is not None:
            # Default location: <round_dir>/../issue_registry.json
            default_reg = Path(round_dir).parent / "issue_registry.json"
            if default_reg.exists():
                registry = issue_tracker.load(str(default_reg))

        # Load CRG metrics (deep-integration input, optional)
        crg_metrics = None
        crg_path = os.environ.get(
            "CRG_METRICS_PATH",
            str(Path(round_dir).parent / "crg_metrics.json"),
        )
        if Path(crg_path).exists():
            try:
                with open(crg_path) as f:
                    crg_metrics = json.load(f)
            except (json.JSONDecodeError, OSError):
                crg_metrics = None

        # Compute score
        result = compute_overall_score(
            scores, config, registry=registry, crg_metrics=crg_metrics
        )

        print(json.dumps(result, indent=2))

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
