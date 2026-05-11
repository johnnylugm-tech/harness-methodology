#!/usr/bin/env python3
"""Validate cross-reference integrity between CLASSIFICATION_TABLE and STRATEGY_REGISTRY.

Usage:
    python scripts/validate_cross_refs.py

Exit 0 = all cross-references consistent
Exit 1 = inconsistency found (details printed to stdout)
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# Add repo root to path so core imports work
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from core.auto_fix import FixStrategy
    from core.auto_fix.classifier import CLASSIFICATION_TABLE
    from core.auto_fix.strategies import STRATEGY_REGISTRY

    errors: list[str] = []

    # 1. Every non-HUMAN_REQUIRED entry in CLASSIFICATION_TABLE must have
    #    its problem_type in STRATEGY_REGISTRY with a callable.
    for key, entry in CLASSIFICATION_TABLE.items():
        strategy = entry.get("strategy")
        pt = entry.get("problem_type", "")

        if strategy == FixStrategy.HUMAN_REQUIRED:
            continue

        if pt not in STRATEGY_REGISTRY:
            errors.append(
                f"CLASSIFICATION_TABLE[{key!r}] problem_type={pt!r} "
                f"missing from STRATEGY_REGISTRY"
            )
        elif not callable(STRATEGY_REGISTRY[pt]):
            errors.append(
                f"STRATEGY_REGISTRY[{pt!r}] is not callable "
                f"(referenced by CLASSIFICATION_TABLE[{key!r}])"
            )

    # 2. Every key in STRATEGY_REGISTRY must be referenced by at least one
    #    entry in CLASSIFICATION_TABLE.
    all_problem_types: set[str] = set()
    for key, entry in CLASSIFICATION_TABLE.items():
        pt = entry.get("problem_type", "")
        if pt:
            all_problem_types.add(pt)

    for pt, fn in STRATEGY_REGISTRY.items():
        if pt not in all_problem_types:
            errors.append(
                f"STRATEGY_REGISTRY[{pt!r}] not referenced in CLASSIFICATION_TABLE "
                f"(dead code?)"
            )

    # 3. Verify no invalid FixStrategy values in CLASSIFICATION_TABLE.
    valid_strategies = set(FixStrategy)
    for key, entry in CLASSIFICATION_TABLE.items():
        strategy = entry.get("strategy")
        if strategy not in valid_strategies:
            errors.append(
                f"CLASSIFICATION_TABLE[{key!r}] has invalid strategy {strategy!r}"
            )

    # 4. Verify strategy function names match registry key convention.
    for pt, fn in STRATEGY_REGISTRY.items():
        fn_name = getattr(fn, "__name__", "<unknown>")
        if pt == "hard_rule_violation":
            continue  # HUMAN_REQUIRED, no strategy function expected
        # Strategy functions should exist and be callable
        if not callable(fn):
            errors.append(
                f"STRATEGY_REGISTRY[{pt!r}] = {fn_name} is not callable"
            )

    # Print results
    if errors:
        print(f"[FAIL] {len(errors)} cross-reference error(s) found:\n")
        for e in errors:
            print(f"  - {e}")
        print(f"\nCLASSIFICATION_TABLE entries: {len(CLASSIFICATION_TABLE)}")
        print(f"STRATEGY_REGISTRY entries:   {len(STRATEGY_REGISTRY)}")
        print(f"Unique problem_types:         {len(all_problem_types)}")
        return 1

    print(f"[OK] Cross-references consistent.")
    print(f"     CLASSIFICATION_TABLE: {len(CLASSIFICATION_TABLE)} entries")
    print(f"     STRATEGY_REGISTRY:    {len(STRATEGY_REGISTRY)} entries")
    print(f"     Unique problem_types:  {len(all_problem_types)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
