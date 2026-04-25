#!/usr/bin/env python3
"""
Generate SAB - Generate Software Architecture Baseline from SAD.md
==================================================================

Purpose: After Phase 2, generate SAB from SAD.md.

Usage:
    python scripts/generate_sab.py --project /path/to/project

Output:
    .methodology/SAB.json - Structured Architecture Baseline
"""

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Generate SAB from SAD.md")
    parser.add_argument("--project", default=".", help="Project path")
    parser.add_argument("--output", default=".methodology/SAB.json", help="Output path")
    args = parser.parse_args()

    project = Path(args.project)
    sad_file = project / "SAD.md"
    output_file = project / args.output

    if not sad_file.exists():
        print(f"SAD.md not found: {sad_file}")
        return 1

    print(f"\n{'='*50}")
    print(f"SAB Generator")
    print(f"{'='*50}")
    print(f"Input: {sad_file}")
    print(f"Output: {output_file}")

    sys.path.insert(0, str(project))
    try:
        from quality_gate.sab_parser import SabParser, extract_sab_from_sad
    except ImportError:
        from sab_parser import SabParser, extract_sab_from_sad

    sab_spec = extract_sab_from_sad(sad_file)
    if sab_spec is None:
        print("Failed to parse SAD.md - no SAB block found")
        return 1

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(sab_spec.to_dict(), f, indent=2, ensure_ascii=False)

    print(f"SAB generated successfully")
    print(f"  Modules: {len(sab_spec.modules)}")
    print(f"  Layers: {len(sab_spec.layers)}")
    print(f"  File: {output_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
