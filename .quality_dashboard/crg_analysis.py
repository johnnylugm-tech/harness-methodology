#!/usr/bin/env python3
"""
CRG Analysis: Convert raw reconnaissance data into structured, deterministic
metrics with explicit thresholds.

This is the "deep integration" layer. Without it, LLM reads CRG tool outputs
and makes subjective calls. With it, formulas + thresholds produce concrete
decisions that downstream scripts (score.py, improvement_plan.md,
evaluate_dimension.md) can act on without LLM judgment.

Input:  .sessi-work/crg_reconnaissance.json   (written by crg_reconnaissance.md)
Output: .sessi-work/crg_metrics.json          (consumed by score.py and prompts)

Primary metrics:
  - risk_score                  (from reconnaissance; 0.0-1.0)
  - eval_depth                  (deep | standard | fast — drives token budget)
  - community_cohesion_score    (0-100, feeds architecture sub-score)
  - flow_coverage_score         (0-100, feeds error_handling sub-score)
  - dead_code_ratio             (%, escalates severity if > threshold)
  - hub_risk_map                (fan_in → severity; feeds seed_issues)

Explicit thresholds (documented, reviewable, changeable):

  RISK_DEEP_THRESHOLD          0.7    risk_score ≥ this → deep analysis mandatory
  RISK_FAST_THRESHOLD          0.3    risk_score < this → fast scan allowed
  COHESION_HEALTHY             0.4    cohesion ≥ this = healthy community
  COMMUNITY_OVERSIZED          50     size > this = god-module candidate
  DEAD_CODE_ESCALATE_RATIO     0.05   dead/total > 5% → escalate low → medium
  HUB_CRITICAL_FAN_IN          15     fan_in ≥ this = critical severity
  HUB_HIGH_FAN_IN              8      fan_in ≥ this = high severity
  FLOW_GOOD_HANDLER_PCT        80     ≥ this% flows with handlers = healthy

All thresholds are ENV-overridable via CRG_* env vars for experimentation.
"""

import os
import sys
import json
from pathlib import Path

# Local imports
sys.path.insert(0, str(Path(__file__).parent))
try:
    import issue_tracker
except ImportError:
    issue_tracker = None


# ---------- Thresholds (ENV-overridable for experiments) ----------
def _tf(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _ti(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


RISK_DEEP_THRESHOLD = _tf("CRG_RISK_DEEP", 0.7)
RISK_FAST_THRESHOLD = _tf("CRG_RISK_FAST", 0.3)
COHESION_HEALTHY = _tf("CRG_COHESION_HEALTHY", 0.4)
COMMUNITY_OVERSIZED = _ti("CRG_COMMUNITY_OVERSIZED", 50)
DEAD_CODE_ESCALATE_RATIO = _tf("CRG_DEAD_CODE_RATIO", 0.05)
HUB_CRITICAL_FAN_IN = _ti("CRG_HUB_CRIT_FANIN", 15)
HUB_HIGH_FAN_IN = _ti("CRG_HUB_HIGH_FANIN", 8)
FLOW_GOOD_HANDLER_PCT = _ti("CRG_FLOW_GOOD_PCT", 80)


# ---------- Severity map for get_suggested_questions output ----------
# Maps CRG's suggested-question categories to (dimension, severity).
# Deep integration: CRG's own priorities become registry issues automatically.
SUGGESTED_Q_SEVERITY_MAP = {
    "bridge_needs_tests": ("test_coverage", "high"),
    "untested_hubs": ("test_coverage", "high"),
    "untested_hotspots": ("test_coverage", "medium"),
    "cross_community_coupling": ("architecture", "medium"),
    "thin_communities": ("architecture", "medium"),
    "god_modules": ("architecture", "high"),
    "dead_code": ("architecture", "low"),
    "surprising_connections": ("architecture", "medium"),
}


# =============== Metric Computations ===============


def compute_eval_depth(risk_score):
    """Risk-based eval depth gate. Drives token budget per dimension."""
    if risk_score is None:
        return "standard"
    if risk_score >= RISK_DEEP_THRESHOLD:
        return "deep"  # full LLM reasoning, read hubs + bridges
    if risk_score < RISK_FAST_THRESHOLD:
        return "fast"  # tool-only, skip LLM for Tier 3
    return "standard"


def compute_community_cohesion_score(communities: list) -> dict:
    """
    Aggregate community cohesion into a 0-100 architecture sub-score.

    Formula:
      healthy = count(cohesion >= COHESION_HEALTHY AND size <= COMMUNITY_OVERSIZED)
      score   = 100 * healthy / total_communities

    Each unhealthy community is classified for diagnostic output.
    """
    if not communities:
        return {"score": 100, "healthy": 0, "total": 0, "unhealthy": []}

    unhealthy = []
    healthy = 0
    for c in communities:
        cohesion = c.get("cohesion", 1.0)
        size = c.get("size", 0)
        reasons = []
        if cohesion < COHESION_HEALTHY:
            reasons.append(f"low_cohesion({cohesion:.2f})")
        if size > COMMUNITY_OVERSIZED:
            reasons.append(f"oversized({size})")
        if reasons:
            unhealthy.append(
                {
                    "name": c.get("name", "unknown"),
                    "cohesion": cohesion,
                    "size": size,
                    "issues": reasons,
                }
            )
        else:
            healthy += 1

    total = len(communities)
    score = round(100.0 * healthy / total, 1) if total > 0 else 100
    return {
        "score": score,
        "healthy": healthy,
        "total": total,
        "unhealthy": unhealthy,
    }


def compute_flow_coverage_score(flows: list) -> dict:
    """
    Convert flow data to 0-100 error_handling sub-score.

    Each flow is expected to have `has_error_handler: bool`.
    Formula:
      score = 100 * flows_with_handler / total_flows
      Cap: scores above FLOW_GOOD_HANDLER_PCT reported as healthy.
    """
    if not flows:
        return {"score": 100, "with_handler": 0, "total": 0, "missing": []}

    with_handler = sum(1 for f in flows if f.get("has_error_handler"))
    total = len(flows)
    score = round(100.0 * with_handler / total, 1) if total > 0 else 100
    missing = [
        f.get("name", "unknown") for f in flows if not f.get("has_error_handler")
    ]

    return {
        "score": score,
        "with_handler": with_handler,
        "total": total,
        "missing": missing,
        "healthy": score >= FLOW_GOOD_HANDLER_PCT,
    }


def compute_dead_code_ratio(dead_code: list, total_nodes: int) -> dict:
    """
    Dead-code ratio + severity escalation decision.

    escalate_severity=True → reconnaissance should promote `low` dead_code
    findings to `medium` so they show up in the open-issue queue.
    """
    count = len(dead_code or [])
    ratio = (count / total_nodes) if total_nodes > 0 else 0.0
    escalate = ratio > DEAD_CODE_ESCALATE_RATIO
    return {
        "count": count,
        "total_nodes": total_nodes,
        "ratio": round(ratio, 4),
        "ratio_pct": round(ratio * 100, 2),
        "escalate_severity": escalate,
        "escalated_to": "medium" if escalate else "low",
    }


def compute_hub_risk_map(hubs: list, knowledge_gaps: list) -> dict:
    """
    Map each hub to a severity based on fan_in AND whether it is untested.

    untested hub is a knowledge-gap match; tested hub with high fan_in is
    still a risk (a fix can cascade) but lower severity than untested hub.
    """
    gap_names = {g.get("name") for g in (knowledge_gaps or []) if g.get("name")}

    mapped = []
    crit = high = medium = 0
    for h in hubs or []:
        name = h.get("name", "unknown")
        fan_in = h.get("fan_in", 0)
        untested = name in gap_names

        # Severity ladder: untested is one rung higher than tested
        if fan_in >= HUB_CRITICAL_FAN_IN:
            sev = "critical" if untested else "high"
        elif fan_in >= HUB_HIGH_FAN_IN:
            sev = "high" if untested else "medium"
        else:
            sev = "medium" if untested else "low"

        mapped.append(
            {
                "name": name,
                "file": h.get("file"),
                "fan_in": fan_in,
                "untested": untested,
                "severity": sev,
            }
        )

        if sev == "critical":
            crit += 1
        elif sev == "high":
            high += 1
        elif sev == "medium":
            medium += 1

    return {
        "hubs": mapped,
        "critical_count": crit,
        "high_count": high,
        "medium_count": medium,
    }


# =============== Top-Level Orchestration ===============


def _validate_recon(recon: dict) -> list:
    """
    Return a list of warning strings when key CRG fields are absent.

    These fields are populated by CRG MCP tools during reconnaissance.
    If they are missing it means CRG tools were unavailable — metrics
    will return safe defaults (no false negatives, no errors) but the
    caller should know the data is incomplete.
    """
    warnings = []
    expected = {
        "risk_score": "get_minimal_context",
        "high_risk_hubs": "get_hub_nodes",
        "low_cohesion_communities": "list_communities",
        "untested_hotspots": "get_knowledge_gaps",
        "dead_code": "refactor_tool(dead_code)",
        "flows": "list_flows / get_affected_flows",
        "suggested_questions": "get_suggested_questions",
    }
    for field, source_tool in expected.items():
        if recon.get(field) is None:
            warnings.append(
                f"  missing '{field}' (populated by {source_tool}) — "
                "CRG MCP tools may have been unavailable during reconnaissance"
            )
    return warnings


def compute_metrics(recon: dict) -> dict:
    """
    Run all metric computations. Single call for full analysis.

    Graceful degradation: if CRG MCP tools were unavailable during
    reconnaissance, key fields will be absent. All sub-computations
    handle missing/empty data with safe defaults:
      - risk_score=None  → eval_depth='standard' (not fast, not deep)
      - empty communities → cohesion_score=100 (no unhealthy signal)
      - empty flows       → flow_coverage=100
      - empty dead_code   → no escalation
      - empty hubs        → no hub-risk issues

    Warnings are emitted to stderr (and included in output) so the
    caller can tell the difference between "no issues found" and
    "data not available".
    """
    warnings = _validate_recon(recon)
    if warnings:
        print(
            "[crg_analysis] WARNING: reconnaissance data incomplete.\n"
            + "\n".join(warnings)
            + "\n  → safe defaults applied; metrics reflect NO CRG signal.",
            file=sys.stderr,
        )

    risk_score = recon.get("risk_score")
    stats = recon.get("graph_stats", {})
    total_nodes = stats.get("nodes", 0) or 0

    communities = (
        recon.get("low_cohesion_communities", [])
        + recon.get("communities", [])  # accept either key
    )
    # de-dup by name if both present
    seen = set()
    deduped = []
    for c in communities:
        n = c.get("name")
        if n and n in seen:
            continue
        if n:
            seen.add(n)
        deduped.append(c)

    return {
        "risk_score": risk_score,
        "eval_depth": compute_eval_depth(risk_score),
        "data_warnings": warnings,  # empty list = full CRG data present
        "thresholds": {
            "risk_deep": RISK_DEEP_THRESHOLD,
            "risk_fast": RISK_FAST_THRESHOLD,
            "cohesion_healthy": COHESION_HEALTHY,
            "community_oversized": COMMUNITY_OVERSIZED,
            "dead_code_escalate_ratio": DEAD_CODE_ESCALATE_RATIO,
            "hub_critical_fan_in": HUB_CRITICAL_FAN_IN,
            "hub_high_fan_in": HUB_HIGH_FAN_IN,
            "flow_good_handler_pct": FLOW_GOOD_HANDLER_PCT,
        },
        "community_cohesion": compute_community_cohesion_score(deduped),
        "flow_coverage": compute_flow_coverage_score(recon.get("flows", [])),
        "dead_code": compute_dead_code_ratio(recon.get("dead_code", []), total_nodes),
        "hub_risk_map": compute_hub_risk_map(
            recon.get("high_risk_hubs", []),
            recon.get("untested_hotspots", []),
        ),
        "suggested_questions": recon.get("suggested_questions", []),
    }


# =============== Issue Seeding ===============


def seed_issues_from_suggested_questions(
    registry: dict, metrics: dict, round_num: int = 0
) -> list:
    """
    Turn `get_suggested_questions` output into registry issues via
    SUGGESTED_Q_SEVERITY_MAP. Returns list of seeded issue_ids.
    """
    if issue_tracker is None:
        return []

    seeded = []
    for q in metrics.get("suggested_questions", []):
        # Each question should carry: {category, text, target?, file?}
        cat = q.get("category", "").lower().replace(" ", "_")
        dim_sev = SUGGESTED_Q_SEVERITY_MAP.get(cat)
        if not dim_sev:
            continue
        dim, sev = dim_sev
        finding = {
            "severity": sev,
            "message": q.get("text", f"CRG-suggested: {cat}"),
            "file": q.get("file"),
            "line": q.get("line"),
            "evidence": f"CRG get_suggested_questions: category={cat}",
        }
        iid = issue_tracker.add_finding(registry, finding, dim, round_num)
        seeded.append({"id": iid, "category": cat, "dim": dim, "severity": sev})
    return seeded


# =============== CLI ===============


def _help():
    print(f"""Usage: {sys.argv[0]} <command> [args]

Commands:
  metrics <recon.json> [out.json]
      Compute structured metrics from reconnaissance data.
      Default out: .sessi-work/crg_metrics.json

  depth_gate <recon.json>
      Print recommended eval depth: deep | standard | fast
      Exit 0 on success; prints ONLY the depth keyword for shell consumption.

  seed_issues <recon.json> <registry.json> <round>
      Auto-create registry issues from suggested_questions using severity map.
      Prints JSON list of seeded issue_ids.

  thresholds
      Print effective thresholds (after ENV overrides).
""")


def _load_recon(path: str) -> dict:
    return json.loads(Path(path).read_text())


def main():
    if len(sys.argv) < 2:
        _help()
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "metrics":
        if len(sys.argv) < 3:
            _help()
            sys.exit(1)
        recon = _load_recon(sys.argv[2])
        out = sys.argv[3] if len(sys.argv) > 3 else ".sessi-work/crg_metrics.json"
        metrics = compute_metrics(recon)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(metrics, indent=2, ensure_ascii=False))
        print(json.dumps(metrics, indent=2))

    elif cmd == "depth_gate":
        if len(sys.argv) < 3:
            _help()
            sys.exit(1)
        recon = _load_recon(sys.argv[2])
        print(compute_eval_depth(recon.get("risk_score")))

    elif cmd == "seed_issues":
        if len(sys.argv) < 5:
            _help()
            sys.exit(1)
        if issue_tracker is None:
            print("issue_tracker module unavailable", file=sys.stderr)
            sys.exit(1)
        recon_path = sys.argv[2]
        reg_path = sys.argv[3]
        round_num = int(sys.argv[4])
        recon = _load_recon(recon_path)
        metrics = compute_metrics(recon)
        registry = issue_tracker.load(reg_path)
        seeded = seed_issues_from_suggested_questions(registry, metrics, round_num)
        issue_tracker.save(registry, reg_path)
        print(json.dumps(seeded, indent=2))

    elif cmd == "thresholds":
        print(
            json.dumps(
                {
                    "risk_deep": RISK_DEEP_THRESHOLD,
                    "risk_fast": RISK_FAST_THRESHOLD,
                    "cohesion_healthy": COHESION_HEALTHY,
                    "community_oversized": COMMUNITY_OVERSIZED,
                    "dead_code_escalate_ratio": DEAD_CODE_ESCALATE_RATIO,
                    "hub_critical_fan_in": HUB_CRITICAL_FAN_IN,
                    "hub_high_fan_in": HUB_HIGH_FAN_IN,
                    "flow_good_handler_pct": FLOW_GOOD_HANDLER_PCT,
                },
                indent=2,
            )
        )

    else:
        _help()
        sys.exit(1)


if __name__ == "__main__":
    main()
