#!/usr/bin/env python3
"""
Generate RELEASE_NOTES.md from git log and quality_manifest.json.

Usage:
    python3 scripts/generate_release_notes.py --project /path/to/project
    python3 scripts/generate_release_notes.py --project . \
        --since v0.1.0 --output RELEASE_NOTES.md

Output:
    Creates RELEASE_NOTES.md at project root with:
    - Features (structured from git log)
    - Bug Fixes
    - Quality Scores (from latest gate result)
    - Known Issues
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def _git_log(project: Path, since: str | None) -> list[str]:
    """Run git log and return commit message lines."""
    cmd = ["git", "-C", str(project), "log", "--oneline", "--no-decorate"]
    if since:
        cmd.append(f"{since}..HEAD")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,  # nosec B603 B607
                                timeout=30)
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _categorize_commits(commits: list[str]) -> dict[str, list[str]]:
    """Categorize commits by type prefixes."""
    categories: dict[str, list[str]] = {
        "Features": [],
        "Bug Fixes": [],
        "Enhancements": [],
        "Other": [],
    }
    for commit in commits:
        # Remove commit hash for cleaner output
        msg = re.sub(r'^[a-f0-9]+\s+', '', commit, count=1)
        if re.match(r'^(feat|feature|add|implement|new)', msg, re.IGNORECASE):
            categories["Features"].append(f"- {msg}")
        elif re.match(r'^(fix|bug|hotfix|patch)', msg, re.IGNORECASE):
            categories["Bug Fixes"].append(f"- {msg}")
        elif re.match(r'^(refactor|update|improve|enhance|optimize)', msg, re.IGNORECASE):
            categories["Enhancements"].append(f"- {msg}")
        else:
            categories["Other"].append(f"- {msg}")
    return categories


def _get_latest_gate_score(project: Path) -> dict[str, Any]:
    """Extract latest gate score from quality_manifest.json."""
    manifest_path = project / ".methodology" / "quality_manifest.json"
    if not manifest_path.exists():
        return {"score": "N/A", "gate": "N/A", "error": "manifest not found"}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # Bug M20 fix: previously a malformed manifest returned silent
        # N/A. Now the failure cause is included so release notes can
        # surface that the gate score is not actually known.
        return {
            "score": "N/A",
            "gate": "N/A",
            "error": f"malformed manifest JSON: {exc}",
        }
    except OSError as exc:
        return {
            "score": "N/A",
            "gate": "N/A",
            "error": f"manifest read error: {exc}",
        }
    gate_results = manifest.get("gate_results", {})
    # Find highest completed gate
    for gate_num in (4, 3, 2):
        gate_data = gate_results.get(f"gate{gate_num}", {})
        if isinstance(gate_data, dict) and gate_data.get("quality_complete"):
            return {"gate": gate_num, "score": gate_data.get("score", "N/A")}
    return {"score": "N/A", "gate": "N/A"}


def generate_release_notes(project_root: str,
                           output_path: str | None = None,
                           since: str | None = None) -> str:
    """Generate RELEASE_NOTES.md and write to disk."""
    project = Path(project_root).resolve()

    # Find last release tag if --since not provided
    if not since:
        try:
            result = subprocess.run(  # nosec B603 B607
                ["git", "-C", str(project), "describe", "--tags", "--abbrev=0"],
                capture_output=True, text=True, timeout=10,
            )
            since = result.stdout.strip() if result.returncode == 0 else None
        except (subprocess.TimeoutExpired, FileNotFoundError):
            since = None

    commits = _git_log(project, since)
    categories = _categorize_commits(commits)
    quality = _get_latest_gate_score(project)

    lines: list[str] = [
        "# Release Notes",
        "",
        f"> **Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> **Version**: {since or 'development'}",
        "",
        "---",
        "",
        "## Quality Score",
        f"- **Gate {quality['gate']}**: {quality['score']}/100",
        "",
        "---",
        "",
    ]

    for section, items in categories.items():
        if items:
            lines.append(f"## {section}")
            lines.append("")
            lines.extend(items[:20])  # cap at 20 per section
            lines.append("")

    lines.extend([
        "---",
        "",
        "## Known Issues",
        "",
        "> See `06-quality/QUALITY_REPORT.md` for detailed defect tracking.",
        "> See `07-risk/RISK_REGISTER.md` for risk-mitigation status.",
        "",
        "---",
        "",
        "_Report auto-generated by harness-methodology/scripts/generate_release_notes.py_",
        "",
    ])

    content = "\n".join(lines)
    out = Path(output_path) if output_path else project / "RELEASE_NOTES.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    print(f"[RENOTES] Written → {out}  ({len(lines)} lines)")
    return str(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate RELEASE_NOTES.md")
    parser.add_argument("--project", default=".", help="Project root (default: .)")
    parser.add_argument("--output", default=None, help="Output path (default: RELEASE_NOTES.md)")
    parser.add_argument("--since", default=None, help="Git ref for log range (default: last tag)")
    args = parser.parse_args()
    generate_release_notes(args.project, args.output, args.since)
    return 0


if __name__ == "__main__":
    sys.exit(main())
