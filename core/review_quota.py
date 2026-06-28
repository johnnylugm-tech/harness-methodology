"""review_quota.py — cap and triage LLM reviewer findings to converge faster.

Root cause (H of 5-meta-pattern convergence plan): B reviewer / code review
LLM emits unbounded findings each round. Each "address findings round N"
triggers another A-edit → B-review cycle, with no convergence ceiling. In
17 days (2026-05-12 → 2026-06-28), "address findings round N" appeared in
commit messages at least 11 times (round 2 × 5, rounds 3/6/7/8/9/10/11
each once). No terminal condition: rounds grow indefinitely.

This module caps findings to a fixed quota and categorises them so the
top-of-list (must-fix) is acted on, while the tail (should-fix, nit) is
returned to the caller for triage instead of throwing.

Public API:
  enforce_quota(findings, max_count=5) -> (kept, overflow)
  categorize_finding(finding) -> Literal['must-fix', 'should-fix', 'nit']
  auto_close_stale(stale_findings, commits_back=3) -> list[closed findings]

Categorisation rules (severity-first, with evidence_type override):
  - high severity + real_invention       -> must-fix
  - medium severity + real_invention     -> should-fix
  - low severity (any)                   -> nit
  - high severity + over_interpretation  -> should-fix (HR-12: capped at medium)
  - high severity + methodology_artifact -> nit (framework-side issue, not code)
  - any severity, missing canonical_ref  -> nit (low-confidence)

Severity weighting: A single 'must-fix' counts as 3 toward the quota; a
'should-fix' counts as 2; a 'nit' counts as 1. This lets quota reflect
effort, not raw count.

Commonality: framework-level. Applied to all LLM reviewers (B, code
review, PR review).
"""

from __future__ import annotations

from typing import Literal


# Severity weights — used by enforce_quota to allocate quota fairly
SEVERITY_WEIGHT: dict[str, int] = {
    "must-fix": 3,
    "should-fix": 2,
    "nit": 1,
}

# Default max quota (in severity-weight units)
DEFAULT_MAX_QUOTA = 8  # = ~3 must-fix, or ~5 should-fix, or ~8 nit


FindingCategory = Literal["must-fix", "should-fix", "nit"]


def categorize_finding(finding: dict) -> FindingCategory:
    """Categorize a finding based on severity + evidence_type.

    Rules (in order):
      1. If severity == 'high' and evidence_type in (None, 'real_invention')
         → must-fix
      2. If severity == 'high' and evidence_type == 'over_interpretation'
         → should-fix (HR-12: over_interpretation caps at medium)
      3. If severity == 'high' and evidence_type == 'methodology_artifact'
         → nit (framework-side, not source-code)
      4. If severity == 'medium' and evidence_type in (None, 'real_invention')
         → should-fix
      5. Otherwise → nit (low severity, missing canonical_ref, etc.)
    """
    severity = (finding.get("severity") or "").lower()
    evidence_type = (finding.get("evidence_type") or "").lower()

    if severity == "high":
        if evidence_type in (None, "", "real_invention"):
            return "must-fix"
        if evidence_type == "over_interpretation":
            return "should-fix"
        if evidence_type == "methodology_artifact":
            return "nit"
        # Unknown evidence_type: treat conservatively as must-fix (not nit)
        return "must-fix"

    if severity == "medium" and evidence_type in (None, "", "real_invention"):
        return "should-fix"

    return "nit"


def enforce_quota(
    findings: list[dict],
    max_quota: int = DEFAULT_MAX_QUOTA,
) -> tuple[list[dict], list[dict]]:
    """Split findings into (kept, overflow) under a weighted quota.

    Quota is in SEVERITY_WEIGHT units (must-fix=3, should-fix=2, nit=1).
    Findings are processed in input order; the first N (by weight) are kept,
    the rest go to overflow for caller triage.

    Each finding dict gets a 'category' field added ('must-fix'/'should-fix'/
    'nit') so callers can render the kept list with severity labels.

    Examples:
      enforce_quota([{sev:'high', evidence:'real_invention'}]) -> kept, []
      enforce_quota([{sev:'high'}])  # 1 must-fix = 3 weight
      enforce_quota([{sev:'low'}] * 8)  # 8 nit = 8 weight, fits in quota=8
      enforce_quota([{sev:'low'}] * 9)  # 9 nit = 9 weight, overflow 1
    """
    kept: list[dict] = []
    overflow: list[dict] = []
    used_quota = 0

    for f in findings:
        category = categorize_finding(f)
        weight = SEVERITY_WEIGHT[category]
        annotated = {**f, "category": category}
        if used_quota + weight <= max_quota:
            kept.append(annotated)
            used_quota += weight
        else:
            overflow.append(annotated)

    return kept, overflow


def auto_close_stale(
    stale_findings: list[dict],
    commits_back: int = 3,
) -> list[dict]:
    """Mark findings older than `commits_back` commits as closed if not re-raised.

    Each stale finding is annotated with:
      - 'auto_closed': True
      - 'auto_close_reason': 'stale: not re-raised in last N commits'
      - 'commits_back': N

    The caller is responsible for verifying that the finding has NOT been
    re-raised in the last N commits (we just provide the annotation helper;
    actual commit-history lookups happen in the workflow layer).

    Commonality: used by review aggregation in the test/quality phase to
    prevent indefinite pile-up of "this was raised 8 commits ago and still
    unfixed" findings.
    """
    closed: list[dict] = []
    for f in stale_findings:
        closed.append({
            **f,
            "auto_closed": True,
            "auto_close_reason": f"stale: not re-raised in last {commits_back} commits",
            "commits_back": commits_back,
        })
    return closed


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _selftest() -> int:
    failures = []

    # Test categorize
    cases = [
        ({"severity": "high", "evidence_type": "real_invention"}, "must-fix"),
        ({"severity": "high", "evidence_type": "over_interpretation"}, "should-fix"),
        ({"severity": "high", "evidence_type": "methodology_artifact"}, "nit"),
        ({"severity": "high"}, "must-fix"),  # missing evidence_type defaults
        ({"severity": "medium"}, "should-fix"),
        ({"severity": "medium", "evidence_type": "real_invention"}, "should-fix"),
        ({"severity": "low"}, "nit"),
        ({"severity": "low", "evidence_type": "real_invention"}, "nit"),
        ({}, "nit"),  # missing fields
    ]
    for inp, expected in cases:
        got = categorize_finding(inp)
        if got != expected:
            failures.append(f"  categorize({inp}) = {got!r}, expected {expected!r}")

    # Test enforce_quota — 1 must-fix = 3 weight (fits in quota=8)
    findings = [{"severity": "high", "evidence_type": "real_invention", "msg": "x"}]
    kept, overflow = enforce_quota(findings, max_quota=8)
    if len(kept) != 1 or len(overflow) != 0:
        failures.append(f"  enforce_quota 1 must-fix: kept={len(kept)}, overflow={len(overflow)}")

    # Test enforce_quota — 3 must-fix = 9 weight (overflow 1)
    findings = [{"severity": "high", "evidence_type": "real_invention", "msg": f"x{i}"} for i in range(3)]
    kept, overflow = enforce_quota(findings, max_quota=8)
    if len(kept) != 2 or len(overflow) != 1:
        failures.append(f"  enforce_quota 3 must-fix: kept={len(kept)}, overflow={len(overflow)}")
    if kept and kept[0].get("category") != "must-fix":
        failures.append(f"  category annotation missing: {kept[0]}")

    # Test enforce_quota — 8 nit = 8 weight (fits)
    findings = [{"severity": "low", "msg": f"x{i}"} for i in range(8)]
    kept, overflow = enforce_quota(findings, max_quota=8)
    if len(kept) != 8 or len(overflow) != 0:
        failures.append(f"  enforce_quota 8 nit: kept={len(kept)}, overflow={len(overflow)}")

    # Test enforce_quota — 9 nit = 9 weight (overflow 1)
    findings = [{"severity": "low", "msg": f"x{i}"} for i in range(9)]
    kept, overflow = enforce_quota(findings, max_quota=8)
    if len(kept) != 8 or len(overflow) != 1:
        failures.append(f"  enforce_quota 9 nit: kept={len(kept)}, overflow={len(overflow)}")

    # Test auto_close_stale
    findings = [{"severity": "high", "msg": "x"}]
    closed = auto_close_stale(findings, commits_back=3)
    if not closed[0].get("auto_closed"):
        failures.append(f"  auto_close_stale missing auto_closed: {closed[0]}")
    if closed[0].get("commits_back") != 3:
        failures.append(f"  auto_close_stale commits_back: {closed[0]}")

    if failures:
        print("review_quota self-test FAILED:")
        for f in failures:
            print(f)
        return 1
    print(f"review_quota self-test PASSED ({len(cases)} categorize cases + 4 enforce_quota cases + 1 auto_close)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())