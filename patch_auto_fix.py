import re
from pathlib import Path

# Fix core/traceability/auto_fix_propose.py
propose_py = Path("core/traceability/auto_fix_propose.py")
text = propose_py.read_text()
text = re.sub(
    r"def rollback\(project: Path\) -> None:.*?import subprocess.*?subprocess\.run\(\s*\[\"git\", \"checkout\", \"--\", \"\.\"\][^)]+\)\n",
    '''def rollback(project: Path, applied_diffs: list[str] = None) -> None:
    """Best-effort rollback of auto-fix changes.
    
    If applied_diffs is provided, reverses them in LIFO order using `git apply -R`.
    This safely preserves the developer's uncommitted work.
    """
    import subprocess
    if not applied_diffs:
        return
        
    for diff in reversed(applied_diffs):
        if not diff.strip():
            continue
        subprocess.run(
            ["git", "apply", "-R", "-p1", "-"],
            input=diff, text=True,
            cwd=project, capture_output=True,
        )
''',
    text, flags=re.DOTALL
)
propose_py.write_text(text)

# Fix core/auto_fix/strategies.py
strat_py = Path("core/auto_fix/strategies.py")
text = strat_py.read_text()
text = text.replace(
    "rollback(project_root)\n            last_msg = f\"round {round_idx+1}: apply failed ({apply_msg})\"",
    "rollback(project_root, applied_diffs)\n            last_msg = f\"round {round_idx+1}: apply failed ({apply_msg})\""
)
text = text.replace(
    "rollback(project_root)\n    _rt_clean, report_clean = check_traceability(project_root)",
    "rollback(project_root, applied_diffs)\n    _rt_clean, report_clean = check_traceability(project_root)"
)
text = text.replace(
    "    last_diff = \"\"\n    last_msg = \"\"\n    for round_idx in range(max_rounds):",
    "    applied_diffs = []\n    last_diff = \"\"\n    last_msg = \"\"\n    for round_idx in range(max_rounds):"
)
text = text.replace(
    "last_msg = (f\"round {round_idx+1}: applied but {len(still_uncoded)} \"\n                    f\"uncoded / {len(still_untested)} untested remain\")",
    "last_msg = (f\"round {round_idx+1}: applied but {len(still_uncoded)} \"\n                    f\"uncoded / {len(still_untested)} untested remain\")\n        applied_diffs.append(diff_text)"
)
strat_py.write_text(text)
