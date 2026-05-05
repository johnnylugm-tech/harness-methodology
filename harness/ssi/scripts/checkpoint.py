#!/usr/bin/env python3
"""
Checkpoint: Persists round and final reports

Generates round_<n>.json (snapshot) and markdown summary.
Final report shows trajectory across rounds with per-dimension deltas.
"""

import sys
import json
from pathlib import Path
from datetime import datetime


def create_round_snapshot(round_num, scores, overall_score):
    """
    Create snapshot for a round

    Args:
        round_num: Round number (1, 2, 3, ...)
        scores: dict of dimension -> {score, ...}
        overall_score: Overall score from score.py

    Returns:
        Snapshot dict
    """
    return {
        "round": round_num,
        "timestamp": datetime.now().isoformat(),
        "overall_score": overall_score,
        "dimensions": scores,
    }


def create_round_summary(snapshot, prev_snapshot=None):
    """
    Create markdown summary for a round

    Args:
        snapshot: Current round snapshot
        prev_snapshot: Previous round snapshot (for deltas)

    Returns:
        Markdown string
    """
    lines = []
    lines.append(f"# Round {snapshot['round']}")
    lines.append(f"*{snapshot['timestamp']}*")
    lines.append("")

    # Overall score
    overall = snapshot["overall_score"]
    lines.append(f"## Overall Score: {overall:.1f}/100")
    lines.append("")

    # Per-dimension scores
    lines.append("## Dimension Scores")
    lines.append("")
    lines.append("| Dimension | Score | Change |")
    lines.append("|-----------|-------|--------|")

    for dim_name in sorted(snapshot["dimensions"].keys()):
        dim_score = snapshot["dimensions"][dim_name]
        score = dim_score.get("score", 0)

        # Calculate delta
        delta = ""
        if prev_snapshot and dim_name in prev_snapshot["dimensions"]:
            prev_score = prev_snapshot["dimensions"][dim_name].get("score", 0)
            delta_val = score - prev_score
            if delta_val > 0:
                delta = f"+{delta_val:.1f}"
            elif delta_val < 0:
                delta = f"{delta_val:.1f}"
            else:
                delta = "—"
        else:
            delta = "—"

        lines.append(f"| {dim_name} | {score:.1f} | {delta} |")

    lines.append("")

    # Findings
    if any(d.get("findings") for d in snapshot["dimensions"].values()):
        lines.append("## Key Findings")
        lines.append("")
        for dim_name, dim_score in snapshot["dimensions"].items():
            findings = dim_score.get("findings", [])
            if findings:
                lines.append(f"### {dim_name}")
                for finding in findings:
                    lines.append(f"- {finding}")
                lines.append("")

    return "\n".join(lines)


def load_all_rounds(work_dir):
    """Load snapshots from all rounds"""
    work_path = Path(work_dir)
    rounds = {}

    for round_dir in sorted(work_path.glob("round_*")):
        if round_dir.is_dir():
            # Find the actual round file
            round_files = list(round_dir.glob("round_*.json"))
            if round_files:
                with open(round_files[0], "r") as f:
                    snapshot = json.load(f)
                    rounds[snapshot["round"]] = snapshot

    return rounds


def create_final_report(work_dir):
    """
    Create final report showing trajectory across rounds

    Args:
        work_dir: .sessi-work directory

    Returns:
        Markdown string
    """
    rounds = load_all_rounds(work_dir)

    if not rounds:
        return "# Final Report\n\nNo rounds completed."

    lines = []
    lines.append("# Final Quality Improvement Report")
    lines.append("")

    # Overall trajectory
    lines.append("## Quality Score Trajectory")
    lines.append("")
    lines.append("| Round | Score | Change |")
    lines.append("|-------|-------|--------|")

    prev_score = None
    for round_num in sorted(rounds.keys()):
        snapshot = rounds[round_num]
        score = snapshot["overall_score"]

        delta = ""
        if prev_score is not None:
            delta_val = score - prev_score
            if delta_val > 0:
                delta = f"+{delta_val:.1f}"
            elif delta_val < 0:
                delta = f"{delta_val:.1f}"
            else:
                delta = "—"
        else:
            delta = "baseline"

        lines.append(f"| {round_num} | {score:.1f} | {delta} |")
        prev_score = score

    lines.append("")

    # Per-dimension trajectory
    lines.append("## Per-Dimension Improvement")
    lines.append("")

    first_round = min(rounds.keys())
    last_round = max(rounds.keys())

    first_snapshot = rounds[first_round]
    last_snapshot = rounds[last_round]

    lines.append("| Dimension | Start | End | Total Change |")
    lines.append("|-----------|-------|-----|--------------|")

    for dim_name in sorted(first_snapshot["dimensions"].keys()):
        start_score = first_snapshot["dimensions"][dim_name].get("score", 0)
        end_score = last_snapshot["dimensions"][dim_name].get("score", 0)
        delta = end_score - start_score

        if delta > 0:
            delta_str = f"+{delta:.1f}"
        elif delta < 0:
            delta_str = f"{delta:.1f}"
        else:
            delta_str = "—"

        lines.append(
            f"| {dim_name} | {start_score:.1f} | {end_score:.1f} | {delta_str} |"
        )

    lines.append("")

    # Summary statistics
    lines.append("## Summary")
    lines.append("")
    initial_score = rounds[first_round]["overall_score"]
    final_score = rounds[last_round]["overall_score"]
    total_improvement = final_score - initial_score

    lines.append(f"- **Initial Score:** {initial_score:.1f}/100")
    lines.append(f"- **Final Score:** {final_score:.1f}/100")
    lines.append(f"- **Total Improvement:** {total_improvement:+.1f} points")
    lines.append(f"- **Rounds:** {len(rounds)}")
    lines.append("")

    return "\n".join(lines)


def save_round_checkpoint(round_num, scores, overall_score, work_dir):
    """
    Save checkpoint for a round

    Args:
        round_num: Round number
        scores: Per-dimension scores dict
        overall_score: Overall score
        work_dir: .sessi-work directory
    """
    round_dir = Path(work_dir) / f"round_{round_num}"
    round_dir.mkdir(parents=True, exist_ok=True)

    # Save snapshot JSON
    snapshot = create_round_snapshot(round_num, scores, overall_score)
    snapshot_file = round_dir / f"round_{round_num}.json"
    with open(snapshot_file, "w") as f:
        json.dump(snapshot, f, indent=2)

    # Save markdown summary
    summary = create_round_summary(snapshot)
    summary_file = round_dir / f"round_{round_num}.md"
    with open(summary_file, "w") as f:
        f.write(summary)

    print(f"Checkpoint saved: {round_dir}")


def save_final_checkpoint(work_dir):
    """
    Save final report

    Args:
        work_dir: .sessi-work directory
    """
    work_path = Path(work_dir)
    report = create_final_report(work_dir)

    report_file = work_path / "final_report.md"
    with open(report_file, "w") as f:
        f.write(report)

    print(f"Final report saved: {report_file}")


def main():
    if len(sys.argv) < 3:
        print(
            f"Usage: {sys.argv[0]} <round|final> <round_num|work_dir> <scores.json> [overall_score]"
        )
        print("  Round checkpoint: round <round_num> <scores.json> <overall_score>")
        print("  Final checkpoint: final <work_dir>")
        sys.exit(1)

    command = sys.argv[1]

    if command == "round":
        if len(sys.argv) < 5:
            print(
                "Error: round checkpoint requires round_num, scores.json, overall_score"
            )
            sys.exit(1)

        round_num = int(sys.argv[2])
        scores_file = sys.argv[3]
        overall_score = float(sys.argv[4])
        work_dir = sys.argv[5] if len(sys.argv) > 5 else ".sessi-work"

        with open(scores_file, "r") as f:
            scores = json.load(f)

        save_round_checkpoint(round_num, scores, overall_score, work_dir)

    elif command == "final":
        work_dir = sys.argv[2]
        save_final_checkpoint(work_dir)

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
