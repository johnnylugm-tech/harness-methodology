#!/usr/bin/env python3
"""Facade for the workflow JS generator — mirrors scripts/generate_full_plan.py.

Usage:
    python3 scripts/workflowgen/generate_workflows.py --check --phase 8
    python3 scripts/workflowgen/generate_workflows.py --write --phase 8
    python3 scripts/workflowgen/generate_workflows.py --check   # all migrated phases
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.workflowgen import phase_specs  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".claude" / "workflows"

# phase -> (generator function, output filename). Only phases migrated to
# workflowgen appear here; un-migrated phases are untouched hand-maintained
# files (see docs/WORKFLOW_ALIGNMENT_AUDIT.md for migration status).
GENERATORS = {
    1: (phase_specs.generate_phase1, "phase1-requirements.js"),
    2: (phase_specs.generate_phase2, "phase2-architecture.js"),
    3: (phase_specs.generate_phase3, "phase3-implementation.js"),
    4: (phase_specs.generate_phase4, "phase4-testing.js"),
    5: (phase_specs.generate_phase5, "phase5-verification.js"),
    6: (phase_specs.generate_phase6, "phase6-quality.js"),
    7: (phase_specs.generate_phase7, "phase7-risk.js"),
    8: (phase_specs.generate_phase8, "phase8-config.js"),
}


def generate(phase: int) -> str:
    if phase not in GENERATORS:
        raise KeyError(f"phase {phase} has no workflowgen generator yet (migrated phases: {sorted(GENERATORS)})")
    fn, _ = GENERATORS[phase]
    return fn()


def _target_path(phase: int) -> Path:
    _, filename = GENERATORS[phase]
    return WORKFLOWS_DIR / filename


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", type=int, choices=sorted(GENERATORS), help="single phase (default: all migrated phases)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write generated output to .claude/workflows/")
    mode.add_argument("--check", action="store_true", help="compare generated output against the on-disk file; exit 1 on any diff")
    args = parser.parse_args()

    phases = [args.phase] if args.phase else sorted(GENERATORS)
    any_diff = False
    for phase in phases:
        text = generate(phase)
        target = _target_path(phase)
        if args.write:
            target.write_text(text, encoding="utf-8")
            print(f"[workflowgen] wrote {target.relative_to(REPO_ROOT)} ({len(text)} bytes)")
        else:
            current = target.read_text(encoding="utf-8") if target.exists() else ""
            if current != text:
                any_diff = True
                print(f"[workflowgen] DRIFT: {target.relative_to(REPO_ROOT)} differs from generator output")
            else:
                print(f"[workflowgen] OK: {target.relative_to(REPO_ROOT)} matches generator output")
    return 1 if (args.check and any_diff) else 0


if __name__ == "__main__":
    raise SystemExit(main())
