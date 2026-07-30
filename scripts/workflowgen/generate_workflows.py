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

# Composite outputs: generated from the SAME per-phase generators above rather
# than from a spec of their own, so they cannot drift from what the eight
# phase files ship. Keyed by name because `--phase` means a methodology phase
# number (1-8) and run-all is not one.
COMPOSITES: dict[str, tuple[object, str]] = {}


def _composites() -> dict[str, tuple[object, str]]:
    # Imported lazily: spec_runall imports generate() from this module.
    if not COMPOSITES:
        from scripts.workflowgen.spec_runall import generate_runall
        COMPOSITES["run-all"] = (generate_runall, "run-all.js")
    return COMPOSITES


def generate_composite(name: str) -> str:
    fn, _ = _composites()[name]
    # run-all inlines the per-phase generators' output, which generate() has
    # already rewritten, so the wrapper must be injected once for the composite
    # as a whole rather than per inlined section.
    return _inject_dispatch_wrapper(fn())  # type: ignore[operator]


def _inject_dispatch_wrapper(text: str) -> str:
    """Route every `agent()` call through `dispatch()` (Round 26 站5).

    ONE place decides all 118 call sites. The alternative was editing nine
    spec_phase* modules, where the same rewrite would have to be repeated and
    could be forgotten in the tenth — and `tests/test_workflow_js_conventions.py`
    now fails on a raw `await agent(` in generated output, so a future spec that
    hand-writes one cannot slip through either.

    Placed after the meta object because `meta` must remain the first statement
    (test_meta_is_first_statement). A file with no dispatches is left untouched.
    """
    if "await agent(" not in text:
        return text
    from scripts.workflowgen.spec_shared import render_dispatch_wrapper

    marker = "\n}\n"  # the meta object's closing brace
    meta_end = text.index("export const meta = {") + 1
    close = text.index(marker, meta_end) + len(marker)
    return (
        text[:close]
        + render_dispatch_wrapper()
        + text[close:].replace("await agent(", "await dispatch(")
    )


def generate_raw(phase: int) -> str:
    """A phase's generated JS BEFORE the dispatch wrapper is injected.

    spec_runall consumes this, not `generate`: run-all inlines the eight phase
    bodies into one file, so injecting per phase would emit eight copies of
    `const __dispatchLog` (a duplicate-declaration SyntaxError) and would shift the
    anchors its excision table matches. The composite injects once, over the
    assembled file.
    """
    if phase not in GENERATORS:
        raise KeyError(f"phase {phase} has no workflowgen generator yet (migrated phases: {sorted(GENERATORS)})")
    fn, _ = GENERATORS[phase]
    return fn()


def generate(phase: int) -> str:
    return _inject_dispatch_wrapper(generate_raw(phase))


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
    targets: list[tuple[str, Path]] = [
        (generate(p), _target_path(p)) for p in phases
    ]
    if not args.phase:
        targets.extend(
            (generate_composite(name), WORKFLOWS_DIR / filename)
            for name, (_, filename) in sorted(_composites().items())
        )
    any_diff = False
    for text, target in targets:
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
