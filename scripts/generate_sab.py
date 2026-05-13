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


def _import_extract_sab_from_sad():
    """Import extract_sab_from_sad, trying both package locations."""
    try:
        from quality_gate.sab_parser import extract_sab_from_sad
        return extract_sab_from_sad
    except ImportError:
        pass
    try:
        from sab_parser import extract_sab_from_sad
        return extract_sab_from_sad
    except ImportError:
        raise ImportError(
            "sab_parser module not found in quality_gate.sab_parser or sab_parser. "
            "Check PYTHONPATH includes the harness-methodology root. "
            "See SAD.md §6 for the SAB block format."
        )


def parse_sad(sad_path: str) -> dict:
    """
    Parse SAD.md and return SAB dict keyed for harness_bridge compatibility.

    Keys returned:
        nfr_dim_map  - maps NFR IDs to quality dimension names
        constraints  - architecture constraints list
        high_risk    - high-risk module list
        (+ all fields from sab_spec.to_dict())

    Raises:
        RuntimeError if SAD.md has no SAB block or cannot be parsed.
    """
    extract_sab_from_sad = _import_extract_sab_from_sad()

    sab_spec = extract_sab_from_sad(sad_path)
    if sab_spec is None:
        raise RuntimeError(f"No SAB block found in {sad_path}")

    raw = sab_spec.to_dict()
    return {
        "nfr_dim_map": raw.get("nfr_dimension_mapping", {}),
        "constraints": raw.get("architecture_constraints", []),
        "high_risk": raw.get("high_risk_modules", []),
        **raw,
    }


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Generate SAB from SAD.md")
    parser.add_argument("--project", default=".", help="Project path")
    parser.add_argument("--output", default=".methodology/SAB.json", help="Output path")
    args = parser.parse_args()

    project = Path(args.project)
    sad_file = project / "02-architecture" / "SAD.md"
    output_file = project / args.output

    if not sad_file.exists():
        print(f"SAD.md not found: {sad_file}")
        return 1

    print(f"\n{'='*50}")
    print("SAB Generator")
    print(f"{'='*50}")
    print(f"Input: {sad_file}")
    print(f"Output: {output_file}")

    sys.path.insert(0, str(project))
    extract_sab_from_sad = _import_extract_sab_from_sad()

    sab_spec = extract_sab_from_sad(sad_file)
    if sab_spec is None:
        print("Failed to parse SAD.md - no SAB block found")
        return 1

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(sab_spec.to_dict(), f, indent=2, ensure_ascii=False)

    print("SAB generated successfully")
    print(f"  Modules: {len(sab_spec.modules)}")
    print(f"  Layers: {len(sab_spec.layers)}")
    print(f"  File: {output_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
