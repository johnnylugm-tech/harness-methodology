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

# Ensure harness root (parent of scripts/) is on sys.path so core.quality_gate is importable
_HARNESS_ROOT = Path(__file__).parent.parent
if str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))


def _import_extract_sab_from_sad():
    """Import extract_sab_from_sad from core.quality_gate.sab_parser."""
    try:
        from core.quality_gate.sab_parser import extract_sab_from_sad
        return extract_sab_from_sad
    except ImportError:
        pass
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
            "sab_parser module not found in core.quality_gate.sab_parser. "
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
    # Short-alias keys used by harness_bridge (different names from the raw keys).
    # All other raw keys (including nfr_traceability) are included verbatim via **raw.
    return {
        "nfr_dim_map": raw.get("nfr_dimension_mapping", {}),
        "constraints":  raw.get("architecture_constraints", []),
        "high_risk":    raw.get("high_risk_modules", []),
        **raw,
    }


def main():
    """CLI entry point.

    Default mode: parse SAD.md §5 SAB block → write .methodology/SAB.json.
    --validate mode: parse + static-check the SAB block, exit 0 (ok) / 1 (errors).
    Contract: core/quality_gate/sab_parser.py:render_canonical_sab_template()
    """
    parser = argparse.ArgumentParser(
        description=(
            "Generate or validate the SAB block in SAD.md §5. "
            "Contract: core/quality_gate/sab_parser.py:render_canonical_sab_template()"
        ),
    )
    parser.add_argument("--project", default=".", help="Project path")
    parser.add_argument(
        "--output", default=".methodology/SAB.json",
        help="Output path (generate mode only; ignored by --validate)",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help=(
            "Parse + validate the SAB block and exit 0 (ok) / 1 (errors). "
            "Use in CI or plan checkpoints to catch bad SAB blocks early."
        ),
    )
    args = parser.parse_args()

    project = Path(args.project)
    sad_file = project / "02-architecture" / "SAD.md"

    if not sad_file.exists():
        print(f"SAD.md not found: {sad_file}", file=sys.stderr)
        return 1

    # ── Validate-only path ────────────────────────────────────────────────
    if args.validate:
        from core.quality_gate.sab_parser import validate_sab_block
        errors = validate_sab_block(sad_file)
        if errors:
            print(f"SAB validation FAILED ({len(errors)} error(s)):", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            print(
                f"\nFix the SAB block in {sad_file} §5.\n"
                "See core/quality_gate/sab_parser.py docstring for the contract,\n"
                "or call render_canonical_sab_template() for a working example.",
                file=sys.stderr,
            )
            return 1
        print(f"SAB validation PASSED: {sad_file}")
        return 0

    # ── Generate path (default) ───────────────────────────────────────────
    output_file = project / args.output

    print(f"\n{'='*50}")
    print("SAB Generator")
    print(f"{'='*50}")
    print(f"Input: {sad_file}")
    print(f"Output: {output_file}")

    sys.path.insert(0, str(project))
    extract_sab_from_sad = _import_extract_sab_from_sad()

    try:
        sab_spec = extract_sab_from_sad(sad_file)
    except RuntimeError as exc:
        print(
            f"FAILED to parse SAB block in {sad_file}:\n  {exc}\n\n"
            "Fix the SAB block to match core/quality_gate/sab_parser.py contract.\n"
            "Run `python3 scripts/generate_sab.py --validate --project .` for static checks.",
            file=sys.stderr,
        )
        return 1

    if sab_spec is None:
        print(f"Failed to parse {sad_file} - no SAB block found", file=sys.stderr)
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
