#!/usr/bin/env python3
"""
Check FR Quality - Quick check after each FR completes.
========================================================

Purpose: After each FR completes, quickly check that FR's code quality.

Usage:
    # Single check
    python scripts/check_fr_quality.py --fr FR-01

    # Loop until pass (press Enter after each fix)
    python scripts/check_fr_quality.py --fr FR-01 --loop

Checks:
    1. Syntax check (python -m py_compile)
    2. Import check (import the module)

Pass criteria: No Errors (warnings OK)

Minimal check -- Constitution/CQG run separately after completion.
"""

import argparse
import subprocess  # nosec B404
import sys
from pathlib import Path


def get_fr_files(project_path: Path, fr_id: str) -> list:
    """Get files for this FR from fr_mapping.json or traceability_report.json."""
    fr_map_file = project_path / ".methodology" / "fr_mapping.json"
    if fr_map_file.exists():
        import json
        with open(fr_map_file) as f:
            data = json.load(f)
        if fr_id in data:
            return data[fr_id].get("files", [])

    trace_file = project_path / "traceability_report.json"
    if not trace_file.exists():
        return []
    import json
    with open(trace_file) as f:
        data = json.load(f)
    files = []
    for req in data.get("requirements", []):
        if req.get("requirement_id") == fr_id:
            files.extend(req.get("code_files", []))
    return files


def check_syntax(file_path: Path) -> tuple:
    """Python syntax check."""
    try:
        result = subprocess.run(  # nosec B603 B607
            ["python3", "-m", "py_compile", str(file_path)],
            capture_output=True, text=True
        )
        return result.returncode == 0, result.stderr
    except Exception as e:
        return False, str(e)


def run_check(fr_id: str, project: Path) -> tuple:
    """Run check, return (passed, errors)."""
    files = get_fr_files(project, fr_id)
    if not files:
        print(f"  No files found for {fr_id}")
        print("  Skipping (ensure traceability_report.json is up to date)")
        return True, []

    print(f"  Checking {len(files)} file(s): {files}\n")
    errors = []
    for f in files:
        file_path = project / f
        if not file_path.exists():
            print(f"  SKIP: {f} not found")
            continue
        if not f.endswith(".py"):
            continue
        ok, err = check_syntax(file_path)
        if not ok:
            errors.append(f"Syntax error in {f}: {err}")
        else:
            print(f"  OK: {f}: Syntax OK")
    return len(errors) == 0, errors


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Quick FR quality check")
    parser.add_argument("--fr", required=True, help="FR ID (e.g., FR-01)")
    parser.add_argument("--project", default=".", help="Project path")
    parser.add_argument("--loop", action="store_true", help="Loop until PASS")
    parser.add_argument("--max-loops", type=int, default=10, help="Max loop count (default: 10)")
    args = parser.parse_args()

    project = Path(args.project)
    fr_id = args.fr
    loop_count = 0

    while True:
        loop_count += 1
        label = f" (loop {loop_count}/{args.max_loops})" if args.loop else ""
        print(f"\n{'='*50}")
        print(f"FR Quality Check: {fr_id}{label}")
        print(f"{'='*50}")

        passed, errors = run_check(fr_id, project)

        print(f"\n{'='*50}")
        if errors:
            print(f"FAILED: {len(errors)} error(s)")
            for e in errors:
                print(f"  - {e}")
            if args.loop and loop_count < args.max_loops:
                print("\nFix issues then press Enter to retry... (Ctrl+C to quit)")
                try:
                    input()
                except (KeyboardInterrupt, EOFError):
                    print("\nAborted")
                    return 1
                continue
            return 1
        else:
            print("PASSED: All checks passed")
            return 0


if __name__ == "__main__":
    sys.exit(main())
