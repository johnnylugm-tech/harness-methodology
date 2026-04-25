#!/usr/bin/env python3
"""
Check FR Full - Full check after each FR Reviewer APPROVE.
===========================================================

Purpose: Run complete checks after each FR is approved by Reviewer.

Usage:
    # Full check (recommended)
    python scripts/check_fr_full.py --fr FR-01 --project /path/to/project

    # Iterative check
    python scripts/check_fr_full.py --fr FR-01 --project /path/to/project --loop

Check layers:
    1. Lightweight: Syntax + Import (~30s)
    2. Constitution: BVS + HR-09 (~1min)
    3. CQG: Linter + Complexity for FR files only (~1min)

Pass criteria:
    - Lightweight: No Error
    - Constitution: No BLOCK (warnings OK)
    - CQG: No Error (warnings OK)
"""

import argparse
import subprocess
import sys
import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
METHODOLOGY_V2_DIR = SCRIPT_DIR.parent
QUALITY_GATE_DIR = METHODOLOGY_V2_DIR / "quality_gate"


def get_fr_files(project_path: Path, fr_id: str) -> list:
    """Get files for this FR from fr_mapping.json."""
    fr_map_file = project_path / ".methodology" / "fr_mapping.json"
    if fr_map_file.exists():
        import json
        with open(fr_map_file) as f:
            data = json.load(f)
        if fr_id in data:
            return data[fr_id].get("files", [])
    return []


def run_linter(project: Path, files: list) -> tuple:
    """Run linter check."""
    if not files:
        return True, []
    print(f"  Linting {len(files)} files...")
    errors = []
    py_files = [project / f for f in files if f.endswith(".py")]
    if not py_files:
        return True, []
    linter_cmd = None
    for cmd in ["pylint", "pylint3"]:
        if subprocess.run(["which", cmd], capture_output=True).returncode == 0:
            linter_cmd = cmd
            break
    if not linter_cmd:
        print("  pylint not found, skipping Lint")
        return True, []
    for py_file in py_files:
        result = subprocess.run(
            [linter_cmd, "--errors-only", str(py_file)],
            capture_output=True, text=True, cwd=str(project)
        )
        if result.returncode != 0 and result.stderr:
            errors.append(f"  {py_file.name}: {result.stderr.split(chr(10))[0]}")
    return len(errors) == 0, errors


def run_complexity(project: Path, files: list) -> tuple:
    """Run complexity check."""
    if not files:
        return True, []
    print(f"  Checking complexity for {len(files)} files...")
    errors = []
    py_files = [project / f for f in files if f.endswith(".py")]
    if not py_files:
        return True, []
    if subprocess.run(["which", "radon"], capture_output=True).returncode != 0:
        print("  radon not found, skipping Complexity")
        return True, []
    for py_file in py_files:
        result = subprocess.run(
            ["radon", "cc", "-a", "-m", "10", str(py_file)],
            capture_output=True, text=True, cwd=str(project)
        )
        if result.returncode != 0 and result.stdout:
            lines = result.stdout.strip().split("\n")
            if lines:
                errors.append(f"  {py_file.name}: {lines[-1]}")
    return len(errors) == 0, errors


def run_check(name: str, cmd: list, project: str, cwd: str = None) -> tuple:
    """Run a check command."""
    print(f"\n{'='*50}")
    print(f"  {name}")
    print(f"{'='*50}")
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{METHODOLOGY_V2_DIR}:{env.get('PYTHONPATH', '')}"
    exec_cwd = cwd or str(METHODOLOGY_V2_DIR)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=exec_cwd, env=env
        )
        output = result.stdout + result.stderr
        if result.returncode == 0:
            print(f"PASS: {name}")
        else:
            print(f"FAIL: {name}")
            print(output[-500:] if len(output) > 500 else output)
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        print(f"FAIL: {name}: TIMEOUT")
        return False, "Timeout"
    except Exception as e:
        print(f"FAIL: {name}: ERROR - {e}")
        return False, str(e)


def main():
    parser = argparse.ArgumentParser(description="Full quality check for each FR")
    parser.add_argument("--fr", required=True, help="FR ID (e.g., FR-01)")
    parser.add_argument("--project", default=".", help="Project path")
    parser.add_argument("--loop", action="store_true", help="Loop until PASS")
    parser.add_argument("--max-loops", type=int, default=10)
    parser.add_argument("--skip-constitution", action="store_true")
    parser.add_argument("--skip-cqg", action="store_true")
    parser.add_argument("--methodology", default=None, help="Methodology path (auto-detected)")
    args = parser.parse_args()

    project = str(Path(args.project).resolve())
    fr_id = args.fr

    global METHODOLOGY_V2_DIR, QUALITY_GATE_DIR, SCRIPT_DIR
    if args.methodology:
        METHODOLOGY_V2_DIR = Path(args.methodology).resolve()
        SCRIPT_DIR = METHODOLOGY_V2_DIR / "scripts"
        QUALITY_GATE_DIR = METHODOLOGY_V2_DIR / "quality_gate"

    print(f"Project: {project}")
    print(f"Harness: {METHODOLOGY_V2_DIR}")

    loop_count = 0
    while True:
        loop_count += 1
        print(f"\n{'#'*60}")
        print(f"# FR Full Check: {fr_id} (loop {loop_count}/{args.max_loops})")
        print(f"{'#'*60}")

        all_passed = True

        # Layer 1: Syntax
        passed, _ = run_check(
            "Layer 1: Syntax + Import",
            ["python3", str(SCRIPT_DIR / "check_fr_quality.py"), "--fr", fr_id, "--project", project],
            project
        )
        if not passed:
            all_passed = False

        # Layer 2: Constitution
        if not args.skip_constitution:
            passed, _ = run_check(
                "Layer 2: Constitution (BVS + HR-09)",
                ["python3", "-m", "quality_gate.constitution.runner",
                 "--type", "implementation", "--project", project],
                project
            )
            if not passed:
                all_passed = False

        # Layer 3: CQG
        if not args.skip_cqg:
            print(f"\n{'='*50}")
            print(f"  Layer 3: CQG (Linter + Complexity)")
            print(f"{'='*50}")
            fr_files = get_fr_files(Path(project), fr_id)
            if not fr_files:
                print(f"  No files found for {fr_id}")
            else:
                print(f"  FR files: {fr_files}")
                lint_ok, lint_errs = run_linter(Path(project), fr_files)
                for err in lint_errs[:5]:
                    print(err)
                cx_ok, cx_errs = run_complexity(Path(project), fr_files)
                for err in cx_errs[:5]:
                    print(err)
                if lint_ok and cx_ok:
                    print("PASS: Layer 3: CQG")
                else:
                    print("FAIL: Layer 3: CQG")
                    all_passed = False

        print(f"\n{'='*60}")
        if all_passed:
            print(f"ALL CHECKS PASSED for {fr_id}")
            return 0
        print(f"SOME CHECKS FAILED for {fr_id}")
        if args.loop and loop_count < args.max_loops:
            print("\nFix issues and press Enter to retry... (Ctrl+C to quit)")
            try:
                input()
            except (KeyboardInterrupt, EOFError):
                print("\nAborted")
                return 1
            continue
        return 1


if __name__ == "__main__":
    sys.exit(main())
