#!/usr/bin/env python3
"""
Issue Tracker: Persistent issue registry across rounds

Core principle: issues are tracked until explicitly resolved or deferred,
NOT dropped when dimension scores pass the gate.

Status lifecycle:
  open     → issue found, not yet addressed
  fixed    → issue resolved, tool confirms delta
  deferred → out of scope this session (must have reason)
  wontfix  → explicitly rejected (false positive, accepted risk)

Registry location: .sessi-work/issue_registry.json (persisted across rounds)
"""

import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
VALID_STATUS = {"open", "fixed", "deferred", "wontfix"}


def _issue_id(dimension: str, file: str, line, message: str) -> str:
    """Deterministic ID so repeat findings don't duplicate."""
    key = f"{dimension}|{file or ''}|{line or ''}|{message[:80]}"
    return hashlib.sha1(key.encode()).hexdigest()[:10]


def load(registry_path: str) -> dict:
    p = Path(registry_path)
    if not p.exists():
        return {"issues": [], "created": datetime.now().isoformat()}
    return json.loads(p.read_text())


def save(registry: dict, registry_path: str):
    Path(registry_path).parent.mkdir(parents=True, exist_ok=True)
    Path(registry_path).write_text(json.dumps(registry, indent=2, ensure_ascii=False))


def add_finding(registry: dict, finding: dict, dimension: str, round_num: int) -> str:
    """
    Add a finding to the registry. Idempotent: same finding → same ID → updated, not duplicated.

    finding: {severity, message, file?, line?, evidence?}
    Returns: issue_id
    """
    severity = finding.get("severity", "info")
    if severity not in SEVERITY_ORDER:
        severity = "info"

    file = finding.get("file") or ""
    line = finding.get("line")
    message = finding.get("message", "")

    iid = _issue_id(dimension, file, line, message)

    # Check if already tracked
    existing = next((i for i in registry["issues"] if i["id"] == iid), None)
    if existing:
        # Update last_seen round, keep status
        existing["last_seen_round"] = round_num
        return iid

    # New issue
    registry["issues"].append(
        {
            "id": iid,
            "dimension": dimension,
            "severity": severity,
            "file": file,
            "line": line,
            "message": message,
            "evidence": finding.get("evidence", ""),
            "status": "open",
            "round_found": round_num,
            "last_seen_round": round_num,
            "round_resolved": None,
            "resolution_note": None,
            "commit_sha": None,  # populated on mark_fixed
            "files_changed": [],  # populated on mark_fixed
        }
    )
    return iid


# Dimensions whose fixes must be verified by re-running a tool.
# mark_fixed() raises ValueError if tool_rerun_path is absent for these dims.
TOOL_VERIFIABLE_DIMS = {
    "linting",
    "type_safety",
    "test_coverage",
    "security",
    "secrets_scanning",
    "license_compliance",
    "mutation_testing",
}


def mark_fixed(
    registry: dict,
    issue_id: str,
    round_num: int,
    commit_sha: str = "",
    files_changed: list = None,
    note: str = "",
    tool_rerun_path: str = None,
):
    """
    Mark an issue fixed. Traceability fields (commit_sha, files_changed) are
    first-class so the final report can link every fix to its git evidence.

    Fix Verification Enforcement Gate (Anti-Laziness):
    - tool-verifiable dimensions MUST provide tool_rerun_path
    - commit_sha MUST be non-empty for all dimensions
    These guards prevent AI from marking issues as fixed without actual evidence.
    """
    issue = _find(registry, issue_id)

    # Gate 1: commit_sha mandatory for all fixes
    if not commit_sha:
        raise ValueError(
            f"Issue {issue_id}: commit_sha is required to mark an issue fixed. "
            "Cannot mark fixed without a git commit reference."
        )

    # Gate 2: tool_rerun_path mandatory for tool-verifiable dimensions
    dim = issue.get("dimension", "")
    if dim in TOOL_VERIFIABLE_DIMS and not tool_rerun_path:
        raise ValueError(
            f"Issue {issue_id} (dimension={dim}) is tool-verifiable — "
            "tool_rerun_path must point to the tool output after the fix. "
            "Re-run the tool and pass the output path."
        )

    issue["status"] = "fixed"
    issue["round_resolved"] = round_num
    issue["resolution_note"] = note or (f"Fixed in {commit_sha}" if commit_sha else "")
    issue["commit_sha"] = commit_sha or None
    issue["files_changed"] = files_changed or []
    issue["tool_rerun_path"] = tool_rerun_path or None


def mark_deferred(registry: dict, issue_id: str, round_num: int, reason: str):
    if not reason:
        raise ValueError("deferred status requires a reason")
    issue = _find(registry, issue_id)
    issue["status"] = "deferred"
    issue["round_resolved"] = round_num
    issue["resolution_note"] = reason


def mark_wontfix(registry: dict, issue_id: str, round_num: int, reason: str):
    if not reason:
        raise ValueError("wontfix status requires a reason")
    issue = _find(registry, issue_id)
    issue["status"] = "wontfix"
    issue["round_resolved"] = round_num
    issue["resolution_note"] = reason


def _find(registry: dict, issue_id: str) -> dict:
    for i in registry["issues"]:
        if i["id"] == issue_id:
            return i
    raise KeyError(f"Issue {issue_id} not in registry")


def summary(registry: dict) -> dict:
    """Return counts by severity and status."""
    counts = {"by_severity": {}, "by_status": {}, "open_by_severity": {}}
    for issue in registry["issues"]:
        sev = issue["severity"]
        status = issue["status"]
        counts["by_severity"][sev] = counts["by_severity"].get(sev, 0) + 1
        counts["by_status"][status] = counts["by_status"].get(status, 0) + 1
        if status == "open":
            counts["open_by_severity"][sev] = counts["open_by_severity"].get(sev, 0) + 1

    counts["total"] = len(registry["issues"])
    counts["open_total"] = counts["by_status"].get("open", 0)
    counts["open_critical"] = counts["open_by_severity"].get("critical", 0)
    counts["open_high"] = counts["open_by_severity"].get("high", 0)
    counts["open_medium"] = counts["open_by_severity"].get("medium", 0)
    return counts


def open_issues(registry: dict, severity_filter: list = None) -> list:
    """Return open issues, sorted by severity (critical first)."""
    issues = [i for i in registry["issues"] if i["status"] == "open"]
    if severity_filter:
        issues = [i for i in issues if i["severity"] in severity_filter]
    issues.sort(key=lambda i: (SEVERITY_ORDER.get(i["severity"], 99), i["round_found"]))
    return issues


def accepted_risks(registry: dict) -> list:
    """
    Return deferred + wontfix issues, sorted by severity. Used by the final
    report to surface 'found but intentionally not fixed' items with their
    reasons — so nothing silently disappears.
    """
    issues = [i for i in registry["issues"] if i["status"] in ("deferred", "wontfix")]
    issues.sort(
        key=lambda i: (
            SEVERITY_ORDER.get(i["severity"], 99),
            i.get("round_resolved") or 0,
        )
    )
    return issues


def by_dimension(registry: dict) -> dict:
    """
    Per-dimension breakdown: for each dimension, how many issues were found,
    fixed, deferred, wontfix, still open — plus severity histogram.
    Enables the per-dimension summary table in the final report.
    """
    dims = {}
    for i in registry["issues"]:
        d = i["dimension"]
        if d not in dims:
            dims[d] = {
                "found": 0,
                "fixed": 0,
                "deferred": 0,
                "wontfix": 0,
                "open": 0,
                "by_severity": {},
                "issues": [],
            }
        dims[d]["found"] += 1
        dims[d][i["status"]] += 1
        sev = i["severity"]
        dims[d]["by_severity"][sev] = dims[d]["by_severity"].get(sev, 0) + 1
        dims[d]["issues"].append(i)
    return dims


def report(registry: dict) -> dict:
    """
    Compact report for the final summary:
      - summary counts
      - open issues (still to fix)
      - accepted risks (deferred + wontfix with reasons)
      - fixed issues (with commit_sha + files_changed)
      - per-dimension breakdown
    """
    s = summary(registry)
    fixed = [i for i in registry["issues"] if i["status"] == "fixed"]
    fixed.sort(
        key=lambda i: (
            SEVERITY_ORDER.get(i["severity"], 99),
            i.get("round_resolved") or 0,
        )
    )
    return {
        "summary": s,
        "open": open_issues(registry),
        "accepted_risks": accepted_risks(registry),
        "fixed": fixed,
        "fixed_count": len(fixed),
        "by_dimension": by_dimension(registry),
    }


def saturation_check(
    registry: dict, current_round: int, saturation_rounds: int = 3
) -> bool:
    """
    Return True if no NEW issues were found in the last N rounds.
    Used for issue-saturation early-stop (in addition to score gate).
    """
    if current_round < saturation_rounds:
        return False

    lookback_start = current_round - saturation_rounds + 1
    new_in_recent = [
        i for i in registry["issues"] if i["round_found"] >= lookback_start
    ]
    return len(new_in_recent) == 0


# ============ CLI ============


def main():
    if len(sys.argv) < 3:
        print(f"""Usage:
  {sys.argv[0]} summary <registry.json>
  {sys.argv[0]} add <registry.json> <dimension> <round> <finding.json>
  {sys.argv[0]} fix <registry.json> <issue_id> <round> <commit_sha> [files_csv] [note] [tool_rerun_path]
  {sys.argv[0]} defer <registry.json> <issue_id> <round> <reason>
  {sys.argv[0]} wontfix <registry.json> <issue_id> <round> <reason>
  {sys.argv[0]} open <registry.json> [severity_filter]
  {sys.argv[0]} accepted <registry.json>
  {sys.argv[0]} report <registry.json>
  {sys.argv[0]} saturation <registry.json> <current_round> [saturation_rounds]
""")
        sys.exit(1)

    cmd = sys.argv[1]
    path = sys.argv[2]
    registry = load(path)

    if cmd == "summary":
        print(json.dumps(summary(registry), indent=2))

    elif cmd == "add":
        dim, rnd, finding_file = sys.argv[3], int(sys.argv[4]), sys.argv[5]
        with open(finding_file) as f:
            finding = json.load(f)
        iid = add_finding(registry, finding, dim, rnd)
        save(registry, path)
        print(iid)

    elif cmd == "fix":
        # fix <registry> <id> <round> <commit_sha> [files_csv] [note] [tool_rerun_path]
        iid, rnd = sys.argv[3], int(sys.argv[4])
        commit_sha = sys.argv[5] if len(sys.argv) > 5 else ""
        files_csv = sys.argv[6] if len(sys.argv) > 6 else ""
        note = sys.argv[7] if len(sys.argv) > 7 else ""
        tool_rerun_path = sys.argv[8] if len(sys.argv) > 8 else None
        files = (
            [f.strip() for f in files_csv.split(",") if f.strip()] if files_csv else []
        )
        mark_fixed(
            registry,
            iid,
            rnd,
            commit_sha=commit_sha,
            files_changed=files,
            note=note,
            tool_rerun_path=tool_rerun_path,
        )
        save(registry, path)
        print(f"fixed: {iid}")

    elif cmd == "defer":
        iid, rnd, reason = sys.argv[3], int(sys.argv[4]), sys.argv[5]
        mark_deferred(registry, iid, rnd, reason)
        save(registry, path)
        print(f"deferred: {iid}")

    elif cmd == "wontfix":
        iid, rnd, reason = sys.argv[3], int(sys.argv[4]), sys.argv[5]
        mark_wontfix(registry, iid, rnd, reason)
        save(registry, path)
        print(f"wontfix: {iid}")

    elif cmd == "open":
        sev_filter = sys.argv[3].split(",") if len(sys.argv) > 3 else None
        print(json.dumps(open_issues(registry, sev_filter), indent=2))

    elif cmd == "accepted":
        print(json.dumps(accepted_risks(registry), indent=2))

    elif cmd == "report":
        print(json.dumps(report(registry), indent=2))

    elif cmd == "saturation":
        rnd = int(sys.argv[3])
        lookback = int(sys.argv[4]) if len(sys.argv) > 4 else 3
        print(
            json.dumps(
                {
                    "saturated": saturation_check(registry, rnd, lookback),
                    "lookback_rounds": lookback,
                    "current_round": rnd,
                }
            )
        )

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
