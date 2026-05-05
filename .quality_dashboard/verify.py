#!/usr/bin/env python3
"""
Verification: Deterministic anti-bias check that caps unsupported claims

Compares pre/post tool outputs and git diffs.
Caps unsupported gains. Returns verification result for downstream decisions.
"""

import sys
import json
import subprocess
from pathlib import Path

# Anti-bias constants
EVIDENCE_THRESHOLD = 10  # Points that require diff/tool evidence
CAP_DELTA = 3  # Max Δ without diff evidence
TOOL_DIFF_MIN_LINES = 3  # Min lines of diff for evidence

# Self-consistency gate constants
CONSISTENCY_JUMP_THRESHOLD = 15  # Score jump requiring ≥3 diff-backed evidence pieces
CONSISTENCY_HIGH_SCORE_MIN = 85  # Scores ≥ this require inflation_capped check
CONSISTENCY_MIN_EVIDENCE_PIECES = 3  # Min evidence required for large jumps


def get_git_diff(repo_path, since_commit="HEAD~1"):
    """Get git diff since last commit"""
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "diff", since_commit + "..HEAD", "--stat"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout
    except Exception as e:
        print(f"Warning: could not get git diff: {e}", file=sys.stderr)
        return ""


def count_diff_lines(diff_output):
    """Count lines changed in git diff output"""
    lines_changed = 0
    for line in diff_output.split("\n"):
        if " | " in line:  # stat line like "file.py | 10 ++"
            parts = line.split("|")
            if len(parts) > 1:
                stats = parts[1].strip()
                # Extract number from "+++ ---"
                plus_minus = stats.replace("+", "").replace("-", "").strip()
                if plus_minus.isdigit():
                    lines_changed += int(plus_minus)
    return lines_changed


def load_result(result_path):
    """Load raw evaluation result"""
    with open(result_path, "r") as f:
        return json.load(f)


def load_pre_state(round_dir):
    """Load previous round's scores if available"""
    prev_round = int(Path(round_dir).name.split("_")[1]) - 1
    if prev_round < 1:
        return {}

    prev_score_files = (Path(round_dir).parent / f"round_{prev_round}" / "scores").glob(
        "*.json"
    )
    prev_state = {}
    for score_file in prev_score_files:
        with open(score_file, "r") as f:
            dim_score = json.load(f)
            dim_name = dim_score["dimension"]
            prev_state[dim_name] = dim_score.get("score", 0)

    return prev_state


def self_consistency_gate(
    dim_result: dict, dim_name: str, previous_score: float, diff_lines: int
) -> dict:
    """
    Self-Consistency Uncertainty Gate (Anti-Hallucination, Anti-Self-Congratulation).

    Detects two failure modes:
    1. Large score jumps (> CONSISTENCY_JUMP_THRESHOLD) without sufficient diff evidence
    2. High scores (≥ CONSISTENCY_HIGH_SCORE_MIN) that lack inflation_capped marker
       or have contradictory tool/llm signals

    Returns:
        {
            "flagged": bool,
            "reason": str,        # empty if not flagged
            "action": str,        # "cap", "warn", "ok"
            "cap_to": int | None, # if action == "cap"
        }
    """
    current_score = dim_result.get("score", 0)
    llm_score = dim_result.get("llm_score", current_score)
    tool_score = dim_result.get("tool_score")
    delta = current_score - previous_score
    findings = dim_result.get("findings", [])

    # --- Check 1: Large jump needs ≥3 diff-backed evidence pieces ---
    if delta > CONSISTENCY_JUMP_THRESHOLD:
        # Count evidence: tool output + diff lines + findings with evidence
        evidence_count = 0
        if dim_result.get("tool_outputs"):
            evidence_count += 1
        if diff_lines >= TOOL_DIFF_MIN_LINES:
            evidence_count += 1
        # Each finding with a non-empty evidence field counts as one piece
        evidence_count += sum(1 for f in findings if f.get("evidence", "").strip())

        if evidence_count < CONSISTENCY_MIN_EVIDENCE_PIECES:
            return {
                "flagged": True,
                "reason": (
                    f"{dim_name}: score jumped +{delta:.0f} pts but only "
                    f"{evidence_count}/{CONSISTENCY_MIN_EVIDENCE_PIECES} evidence pieces found. "
                    f"Large jumps require tool output + diff + ≥1 finding with evidence."
                ),
                "action": "cap",
                "cap_to": int(previous_score + CONSISTENCY_JUMP_THRESHOLD),
            }

    # --- Check 2: High scores require negative space proof ---
    if llm_score >= CONSISTENCY_HIGH_SCORE_MIN:
        inflation_capped = dim_result.get("inflation_capped", False)
        da_challenge = dim_result.get("da_challenge", None)

        # If llm_score is high but inflation_capped check was bypassed
        if not inflation_capped and da_challenge is None:
            return {
                "flagged": True,
                "reason": (
                    f"{dim_name}: llm_score={llm_score} ≥ {CONSISTENCY_HIGH_SCORE_MIN} "
                    "but no Step 2c high-score confirmation found "
                    "(inflation_capped and da_challenge fields both absent). "
                    "High scores require negative space proof."
                ),
                "action": "warn",
                "cap_to": None,
            }

    # --- Check 3: Contradictory signals (tool vs LLM diverge > 20pts) ---
    if tool_score is not None and abs(llm_score - tool_score) > 20:
        return {
            "flagged": True,
            "reason": (
                f"{dim_name}: tool_score={tool_score} vs llm_score={llm_score} "
                f"diverge by {abs(llm_score - tool_score):.0f} pts — contradictory signals. "
                "Final score uses min(), but the gap indicates unreliable LLM judgment."
            ),
            "action": "warn",
            "cap_to": None,
        }

    return {"flagged": False, "reason": "", "action": "ok", "cap_to": None}


def verify(result_path, round_dir, repo_path):
    """
    Verify evaluation result against evidence

    Returns:
        {
            "verified": true/false,
            "verification": {
                "capped": [{"dimension": "...", "claim": X, "evidence": Y, "capped_to": Z}],
                "regressions": [{"dimension": "...", "before": X, "after": Y, "delta": -Z}],
                "evidence_ok": [...]
            }
        }
    """
    result = load_result(result_path)
    pre_state = load_pre_state(round_dir)
    git_diff = get_git_diff(repo_path)
    diff_lines = count_diff_lines(git_diff)

    capped = []
    regressions = []
    evidence_ok = []
    consistency_flags = []

    for dim_name, dim_result in result.items():
        if not isinstance(dim_result, dict):
            continue

        current_score = dim_result.get("score", 0)
        previous_score = pre_state.get(dim_name, 0)
        delta = current_score - previous_score

        # --- Self-consistency gate (runs for every dimension) ---
        sc = self_consistency_gate(dim_result, dim_name, previous_score, diff_lines)
        if sc["flagged"]:
            consistency_flags.append(
                {
                    "dimension": dim_name,
                    "reason": sc["reason"],
                    "action": sc["action"],
                    "cap_to": sc["cap_to"],
                }
            )
            if sc["action"] == "cap" and sc["cap_to"] is not None:
                result[dim_name]["score"] = sc["cap_to"]
                result[dim_name]["consistency_capped"] = True
                current_score = sc["cap_to"]
                delta = current_score - previous_score

        # Check for suspicious gains
        if delta > 0:
            # Gain > EVIDENCE_THRESHOLD needs evidence
            if delta > EVIDENCE_THRESHOLD:
                has_tool_output = bool(dim_result.get("tool_outputs"))
                has_diff = diff_lines >= TOOL_DIFF_MIN_LINES

                if has_tool_output or has_diff:
                    evidence_ok.append(
                        {
                            "dimension": dim_name,
                            "delta": delta,
                            "evidence": "tool_output"
                            if has_tool_output
                            else f"git_diff({diff_lines} lines)",
                        }
                    )
                else:
                    # Cap unsupported gain
                    capped_delta = min(delta, CAP_DELTA)
                    capped_score = previous_score + capped_delta

                    capped.append(
                        {
                            "dimension": dim_name,
                            "claim": current_score,
                            "evidence": "none",
                            "capped_to": capped_score,
                        }
                    )

                    # Update result for downstream use
                    result[dim_name]["score"] = capped_score

        # Check for regressions
        elif delta < 0:
            regressions.append(
                {
                    "dimension": dim_name,
                    "before": previous_score,
                    "after": current_score,
                    "delta": delta,
                }
            )

    return {
        "verified": len(capped) == 0 and len(regressions) == 0,
        "verification": {
            "capped": capped,
            "regressions": regressions,
            "evidence_ok": evidence_ok,
            "consistency_flags": consistency_flags,
        },
        "result": result,
    }


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <result.json> <round_dir> [repo_path]")
        print("  result.json: raw evaluation results")
        print("  round_dir: path to .sessi-work/round_<n>")
        print("  repo_path: repository path (default: current dir)")
        sys.exit(1)

    result_path = sys.argv[1]
    round_dir = sys.argv[2]
    repo_path = sys.argv[3] if len(sys.argv) > 3 else "."

    try:
        verified = verify(result_path, round_dir, repo_path)
        print(json.dumps(verified, indent=2))

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
