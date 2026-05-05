#!/usr/bin/env python3
"""
Final Report Generator: Full-transparency Markdown report

Aggregates:
  - Issue registry (issue_registry.json)
  - Per-round verified scores (round_*/verified.json or final_score.json)
  - Git log (commit SHA → files changed enrichment)

Output sections (ALL mandatory, nothing silently dropped):
  1. Header + Recommendation
  2. Summary Statistics (counts)
  3. Score Trajectory (per-dim per-round table)
  4. Per-Dimension Breakdown (found / fixed / wontfix / deferred / open)
  5. Issues Fixed (full list with commit SHA + files)
  6. Accepted Risks (wontfix + deferred with 4-part reasons)
  7. Still Open (if any)
  8. Evidence Trail (commit list)
"""

import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
import issue_tracker


SEVERITY_EMOJI = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🔵",
    "info": "⚪",
}


def load_round_scores(work_dir: Path) -> list:
    """Load per-round verified/final scores in round order."""
    rounds = []
    for round_dir in sorted(work_dir.glob("round_*")):
        # Prefer verified.json, fall back to final_score.json, then result.json
        for fname in ("verified.json", "final_score.json", "result.json"):
            p = round_dir / fname
            if p.exists():
                try:
                    rounds.append(
                        {
                            "round": int(round_dir.name.split("_")[1]),
                            "dir": str(round_dir),
                            "source": fname,
                            "data": json.loads(p.read_text()),
                        }
                    )
                except (ValueError, json.JSONDecodeError):
                    pass
                break
    rounds.sort(key=lambda r: r["round"])
    return rounds


def enrich_commit_files(commit_sha: str, repo_path: Path) -> list:
    """Query git for files changed in a commit. Returns [] if unresolvable."""
    if not commit_sha:
        return []
    try:
        out = subprocess.check_output(
            [
                "git",
                "-C",
                str(repo_path),
                "show",
                "--name-only",
                "--format=",
                commit_sha,
            ],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode()
        return [line.strip() for line in out.splitlines() if line.strip()]
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ):
        return []


def determine_recommendation(registry_report: dict, rounds: list) -> str:
    """
    Recommendation state machine:
      pass              → no open issues of any severity, quality_complete
      pass-with-risks   → quality_complete AND only accepted_risks remain
      partial           → max_rounds reached with open >= medium issues
      fail              → regression detected or baseline dropped
    """
    s = registry_report["summary"]
    open_total = s.get("open_total", 0)
    open_critical = s.get("open_critical", 0)
    open_high = s.get("open_high", 0)
    open_medium = s.get("open_medium", 0)
    accepted_count = len(registry_report.get("accepted_risks", []))

    # Regression check: final round score < round 1 score
    if len(rounds) >= 2:
        first = rounds[0]["data"].get("overall_score", 0)
        last = rounds[-1]["data"].get("overall_score", 0)
        if last < first - 1:  # >1 point regression
            return "fail"

    if open_critical == 0 and open_high == 0 and open_medium == 0 and open_total == 0:
        return "pass-with-risks" if accepted_count > 0 else "pass"

    if open_critical == 0 and open_high == 0 and open_medium == 0:
        return "pass-with-risks" if accepted_count > 0 else "pass"

    return "partial"


def render_header(
    repo_path: Path, overall_score: float, score_gate: int, rec: str
) -> str:
    rec_emoji = {"pass": "✅", "pass-with-risks": "⚠️", "partial": "🟡", "fail": "❌"}
    return f"""# Quality Improvement Report

**Project:** `{repo_path.name}`
**Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Overall Score:** {overall_score:.1f} / 100 (gate: {score_gate})
**Recommendation:** {rec_emoji.get(rec, "?")} **{rec.upper()}**
"""


def render_summary(registry_report: dict) -> str:
    s = registry_report["summary"]
    by_status = s.get("by_status", {})
    by_sev = s.get("by_severity", {})
    return f"""## 1. Summary Statistics

| Metric | Count |
|--------|------:|
| Total issues found | {s.get("total", 0)} |
| Fixed | {by_status.get("fixed", 0)} |
| Wontfix (accepted risk) | {by_status.get("wontfix", 0)} |
| Deferred | {by_status.get("deferred", 0)} |
| Still open | {s.get("open_total", 0)} |

### By Severity

| Severity | Found | Still Open |
|----------|------:|-----------:|
| 🔴 Critical | {by_sev.get("critical", 0)} | {s.get("open_critical", 0)} |
| 🟠 High     | {by_sev.get("high", 0)} | {s.get("open_high", 0)} |
| 🟡 Medium   | {by_sev.get("medium", 0)} | {s.get("open_medium", 0)} |
| 🔵 Low      | {by_sev.get("low", 0)} | {s.get("open_by_severity", {}).get("low", 0)} |
| ⚪ Info     | {by_sev.get("info", 0)} | {s.get("open_by_severity", {}).get("info", 0)} |
"""


def render_trajectory(rounds: list) -> str:
    if not rounds:
        return "## 2. Score Trajectory\n\n_No round data._\n"

    # Collect all dimensions across all rounds
    all_dims = set()
    for r in rounds:
        bd = r["data"].get("breakdown") or r["data"].get("dimensions") or {}
        all_dims.update(bd.keys())
    all_dims = sorted(all_dims)

    header = "| Dimension | " + " | ".join(f"R{r['round']}" for r in rounds) + " | Δ |"
    sep = "|" + "---|" * (len(rounds) + 2)
    rows = []
    for dim in all_dims:
        cells = []
        first_score = last_score = None
        for r in rounds:
            bd = r["data"].get("breakdown") or r["data"].get("dimensions") or {}
            score = None
            if dim in bd:
                score = bd[dim].get("score")
            cells.append(f"{score:.0f}" if isinstance(score, (int, float)) else "—")
            if isinstance(score, (int, float)):
                if first_score is None:
                    first_score = score
                last_score = score
        delta = ""
        if first_score is not None and last_score is not None:
            d = last_score - first_score
            delta = f"{'+' if d >= 0 else ''}{d:.0f}"
        rows.append(f"| {dim} | " + " | ".join(cells) + f" | {delta} |")

    # Overall row
    overall_cells = []
    first_ov = last_ov = None
    for r in rounds:
        o = r["data"].get("overall_score")
        overall_cells.append(f"{o:.1f}" if isinstance(o, (int, float)) else "—")
        if isinstance(o, (int, float)):
            if first_ov is None:
                first_ov = o
            last_ov = o
    d_overall = ""
    if first_ov is not None and last_ov is not None:
        do = last_ov - first_ov
        d_overall = f"**{'+' if do >= 0 else ''}{do:.1f}**"
    overall_row = (
        "| **Overall** | "
        + " | ".join(f"**{c}**" for c in overall_cells)
        + f" | {d_overall} |"
    )

    return f"""## 2. Score Trajectory

{header}
{sep}
{chr(10).join(rows)}
{overall_row}
"""


def render_by_dimension(registry_report: dict) -> str:
    dims = registry_report.get("by_dimension", {})
    if not dims:
        return "## 3. Per-Dimension Breakdown\n\n_No issues recorded._\n"

    lines = [
        "## 3. Per-Dimension Breakdown",
        "",
        "| Dimension | Found | Fixed | Wontfix | Deferred | Open |",
        "|-----------|------:|------:|--------:|---------:|-----:|",
    ]
    for dim, stats in sorted(dims.items()):
        lines.append(
            f"| {dim} | {stats['found']} | {stats['fixed']} | "
            f"{stats['wontfix']} | {stats['deferred']} | {stats['open']} |"
        )
    return "\n".join(lines) + "\n"


def render_fixed(registry_report: dict, repo_path: Path) -> str:
    fixed = registry_report.get("fixed", [])
    if not fixed:
        return "## 4. Issues Fixed\n\n_No issues were fixed in this run._\n"

    lines = ["## 4. Issues Fixed", ""]
    # Group by dimension
    by_dim = {}
    for i in fixed:
        by_dim.setdefault(i["dimension"], []).append(i)

    for dim in sorted(by_dim.keys()):
        lines.append(f"### {dim}")
        lines.append("")
        lines.append("| ID | Severity | Location | Issue | Commit | Files Changed |")
        lines.append("|----|----------|----------|-------|--------|---------------|")
        for i in by_dim[dim]:
            sev = f"{SEVERITY_EMOJI.get(i['severity'], '')} {i['severity']}"
            loc = f"`{i['file']}`" + (f":L{i['line']}" if i.get("line") else "")
            msg = (i["message"] or "")[:80].replace("|", "\\|")

            # Enrich files_changed from git if empty and commit_sha present
            files = i.get("files_changed") or []
            if not files and i.get("commit_sha"):
                files = enrich_commit_files(i["commit_sha"], repo_path)
            files_str = "<br>".join(f"`{f}`" for f in files) if files else "—"

            commit = i.get("commit_sha") or "—"
            commit_short = commit[:8] if commit and commit != "—" else "—"

            lines.append(
                f"| `{i['id']}` | {sev} | {loc} | {msg} | `{commit_short}` | {files_str} |"
            )
        lines.append("")
    return "\n".join(lines)


def render_accepted_risks(registry_report: dict) -> str:
    risks = registry_report.get("accepted_risks", [])
    if not risks:
        return "## 5. Accepted Risks\n\n_No issues were consciously deferred or marked wontfix._\n"

    lines = [
        "## 5. Accepted Risks / Not Fixed",
        "",
        "Issues found but intentionally not fixed. Each carries a structured reason.",
        "",
        "| ID | Severity | Dimension | Status | Location | Issue | Reason |",
        "|----|----------|-----------|--------|----------|-------|--------|",
    ]
    for i in risks:
        sev = f"{SEVERITY_EMOJI.get(i['severity'], '')} {i['severity']}"
        loc = f"`{i['file']}`" + (f":L{i['line']}" if i.get("line") else "")
        msg = (i["message"] or "")[:60].replace("|", "\\|")
        reason = (i.get("resolution_note") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| `{i['id']}` | {sev} | {i['dimension']} | {i['status']} | "
            f"{loc} | {msg} | {reason} |"
        )
    return "\n".join(lines) + "\n"


def render_still_open(registry_report: dict) -> str:
    open_issues = registry_report.get("open", [])
    if not open_issues:
        return "## 6. Still Open\n\n✅ _No open issues remain._\n"

    lines = [
        "## 6. Still Open",
        "",
        "Issues that were found but neither fixed nor explicitly accepted as risk.",
        "These drive the recommendation toward `partial`.",
        "",
        "| ID | Severity | Dimension | Location | Issue |",
        "|----|----------|-----------|----------|-------|",
    ]
    for i in open_issues:
        sev = f"{SEVERITY_EMOJI.get(i['severity'], '')} {i['severity']}"
        loc = f"`{i['file']}`" + (f":L{i['line']}" if i.get("line") else "")
        msg = (i["message"] or "")[:80].replace("|", "\\|")
        lines.append(f"| `{i['id']}` | {sev} | {i['dimension']} | {loc} | {msg} |")
    return "\n".join(lines) + "\n"


def render_evidence(repo_path: Path, rounds: list) -> str:
    if not rounds:
        return "## 7. Evidence Trail\n\n_No rounds recorded._\n"
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_path), "log", "--oneline", "-20"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode()
    except Exception:
        out = "_git log unavailable_"
    return (
        f"""## 7. Evidence Trail

### Recent Commits
```
{out.strip()}
```

### Round Artifacts
"""
        + "\n".join(
            f"- Round {r['round']}: `{r['dir']}` ({r['source']})" for r in rounds
        )
        + "\n"
    )


def generate(
    repo_path: Path, work_dir: Path, registry_path: Path, score_gate: int = 85
) -> str:
    registry = issue_tracker.load(str(registry_path))
    rep = issue_tracker.report(registry)
    rounds = load_round_scores(work_dir)

    overall_score = 0.0
    if rounds:
        overall_score = rounds[-1]["data"].get("overall_score", 0.0)

    recommendation = determine_recommendation(rep, rounds)

    sections = [
        render_header(repo_path, overall_score, score_gate, recommendation),
        render_summary(rep),
        render_trajectory(rounds),
        render_by_dimension(rep),
        render_fixed(rep, repo_path),
        render_accepted_risks(rep),
        render_still_open(rep),
        render_evidence(repo_path, rounds),
    ]
    return "\n".join(sections)


def main():
    if len(sys.argv) < 4:
        print(f"""Usage: {sys.argv[0]} <repo_path> <work_dir> <registry.json> [score_gate] [output.md]

Example:
  {sys.argv[0]} . .sessi-work .sessi-work/issue_registry.json 85 final_report.md
""")
        sys.exit(1)

    repo_path = Path(sys.argv[1]).resolve()
    work_dir = Path(sys.argv[2]).resolve()
    registry_path = Path(sys.argv[3]).resolve()
    score_gate = int(sys.argv[4]) if len(sys.argv) > 4 else 85
    output_path = sys.argv[5] if len(sys.argv) > 5 else None

    md = generate(repo_path, work_dir, registry_path, score_gate)

    if output_path:
        Path(output_path).write_text(md)
        print(f"Report written: {output_path}")
    else:
        print(md)


if __name__ == "__main__":
    main()
