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

from scripts.workflow_audit import js_parse  # noqa: E402
from scripts.workflowgen import artifact_limits, phase_specs  # noqa: E402

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

# Outputs keyed by NAME rather than by phase number, because `--phase` means a
# methodology phase (1-8) and neither of these is one. Two kinds live here:
#
#   run-all         composed from the eight per-phase generators above, so it
#                   cannot drift from what those files ship;
#   harness-repair  a standalone spec — its subject is harness-methodology
#                   itself, not a project phase (Round 48 站4).
#
# Each entry's generator returns FINISHED text: run-all guards its inlined
# bodies with its own driver, harness-repair applies the top-level boundary
# itself. generate_composite therefore does not wrap, it just calls.
COMPOSITES: dict[str, tuple[object, str]] = {}


def _composites() -> dict[str, tuple[object, str]]:
    # Imported lazily: both specs import from this module.
    if not COMPOSITES:
        from scripts.workflowgen.spec_repair import generate_repair
        from scripts.workflowgen.spec_runall import generate_runall
        COMPOSITES["run-all"] = (generate_runall, "run-all.js")
        COMPOSITES["harness-repair"] = (generate_repair, "harness-repair.js")
    return COMPOSITES


def generate_composite(name: str) -> str:
    """Call the named generator. It returns FINISHED text — this does not wrap.

    Round 48 站4 moved the dispatch-wrapper injection INTO each generator, and
    it was not a stylistic move. This function used to inject, and when
    harness-repair (which needs the top-level boundary, so it already had to
    inject before wrapping) was added, the text got injected twice: the second
    pass inserted a second wrapper AND rewrote the first wrapper's own
    `await agent(` into `await dispatch(`, so `dispatch` called itself.

    `node --check` passed. `generate_workflows.py --check` passed. All 130
    workflow tests passed. The only thing that saw it was the simulation
    testbed, where the run died with `Maximum call stack size exceeded` — which
    is the whole reason Round 12 站1 built that testbed.
    """
    fn, _ = _composites()[name]
    return fn()  # type: ignore[operator]


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


def _inject_halt_helper(text: str) -> str:
    """Declare `halt()` once per generated file (Round 50 站3).

    Same shape and same reason as `_inject_dispatch_wrapper` above: one place
    decides, and a spec module cannot forget it. Placed after the meta object
    for the same constraint (`meta` must remain the first statement), and
    skipped entirely for a file with no halt sites — bug-hunt-crg.js has none.

    run-all inlines eight phase bodies, so it must NOT be injected per phase:
    eight `function halt` declarations in one file is a duplicate-declaration
    SyntaxError. spec_runall consumes generate_raw() and injects once over the
    assembled file, exactly as it does for the dispatch wrapper.
    """
    if "halt(" not in text:
        return text
    from scripts.workflowgen import js_blocks as _B

    marker = "\n}\n"  # the meta object's closing brace
    meta_end = text.index("export const meta = {") + 1
    close = text.index(marker, meta_end) + len(marker)
    return text[:close] + "\n" + _B.HALT_FN_BLOCK + text[close:]


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


def _wrap_top_level_boundary(text: str) -> str:
    """Give a standalone phase file the crash boundary run-all already has.

    The Workflow runtime's entire error behaviour is to terminate the run: an
    `agent()` call that REJECTS — a transient transport error, the shape that
    took a live run down 83 dispatches in — propagates out of the script and
    the operator is left with a dead run carrying no phase, no reason and no
    resume point. run-all survives this because its driver wraps each
    `runPhaseN()` call (Round 23); the eight files it is generated FROM never
    got the same protection. Swept in the sim testbed by throwing at every
    dispatch label in turn: 84 of their 217 labels killed the run outright,
    against 0 of run-all's 85.

    Applied here rather than in the eight spec modules for the same reason the
    dispatch wrapper is: one place decides all eight, and there is no ninth
    that can be forgotten. Applied to `generate` and NOT to `generate_raw`, so
    run-all — which inlines the raw bodies into functions its driver already
    guards — is untouched and does not end up double-wrapped.

    The body is spliced in verbatim, without re-indenting: keeping it
    byte-identical to the raw generator output means the golden diffs stay
    readable and the run-all equivalence assertions keep comparing like with
    like. `return` inside `try` returns from the enclosing async function the
    runtime evaluates the file in, so every existing early return still works.

    Everything after `meta` goes inside the try, INCLUDING the dispatch wrapper.
    Leaving the wrapper outside was the first attempt and it broke all eight
    files: `REPO` and `PY` are `let`/`const` declared in the body, so moving the
    body into a block put them out of reach of `__dispatchFlushPreamble`, which
    reads both. One block, one scope.
    """
    marker = "\n}\n"  # the meta object's closing brace
    meta_end = text.index("export const meta = {") + 1
    close = text.index(marker, meta_end) + len(marker)
    head, body = text[:close], text[close:]
    return (
        head
        + "\n// ── Round 28: top-level crash boundary ─────────────────────────────────\n"
        "// The runtime does not catch anything; an uncaught throw ends the run with\n"
        "// no result at all. Everything below runs inside this try so a failed\n"
        "// dispatch becomes a structured return the operator can act on. Body is\n"
        "// spliced verbatim (not re-indented) to keep it byte-identical to the\n"
        "// generator output run-all inlines.\n"
        "try {\n"
        + body.rstrip("\n")
        + "\n} catch (err) {\n"
        "  const msg = (err && err.message) ? err.message : String(err)\n"
        "  return {\n"
        "    error: 'workflow crashed: ' + msg.slice(0, 300),\n"
        "    workflow: meta.name,\n"
        "    crashed: true,\n"
        "    note: 'An agent dispatch threw instead of returning a result — most often a "
        "transient transport error, which the Workflow runtime does not retry or catch. "
        "Nothing was skipped silently: relaunch this workflow and its GUARD/sentinel checks "
        "short-circuit the work that already completed.',\n"
        "  }\n"
        "}\n"
    )


def generate(phase: int) -> str:
    return _wrap_top_level_boundary(
        _inject_halt_helper(_inject_dispatch_wrapper(generate_raw(phase)))
    )


def _target_path(phase: int) -> Path:
    _, filename = GENERATORS[phase]
    return WORKFLOWS_DIR / filename


def validate_generated(filename: str, text: str) -> "list[str]":
    """Every reason *text* must not be written to *filename*, or an empty list.

    Round 60 站1. The two properties checked here — it parses the way the
    runtime parses it, and it fits under the ceilings — were enforced only by
    ``tests/test_workflow_js_conventions.py``. `f4be095` shipped a file that
    broke both because the author ran a self-selected subset of the suite that
    did not include that file. They are properties of the artifact, so the
    producer holds them; the conventions test still guards the *shipped*
    files, which a hand edit can reach without passing through here.

    A missing `node` is a problem, not a pass: writing a file nobody could
    parse is precisely the defect this closes (Round 30 — abstaining is not
    passing). `--check` reads nothing from disk and is subject to the same
    rule, so there is one behaviour to remember; a machine without node runs
    neither this nor the test that skips itself for the same reason.
    """
    problems = list(artifact_limits.size_problems(filename, text))
    if not js_parse.node_available():
        problems.append(
            f"{filename}: cannot verify the runtime can parse it — `node` is "
            f"not on PATH. Install Node.js (dev-only dependency) and re-run."
        )
        return problems
    diagnostic = js_parse.parse_problem(text)
    if diagnostic:
        problems.append(f"{filename}: the Workflow runtime cannot parse this:\n{diagnostic}")
    return problems


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
    # Validate every target before writing any of them. A partial write leaves
    # the phase files and run-all disagreeing about which generation they came
    # from, and the operator with no way to tell which half is current.
    problems: list[str] = []
    for text, target in targets:
        problems += validate_generated(target.name, text)
    if problems:
        for problem in problems:
            print(f"[workflowgen] INVALID: {problem}")
        print(
            f"[workflowgen] {len(problems)} problem(s) — nothing was "
            f"{'written' if args.write else 'compared'}."
        )
        return 1

    any_diff = False
    for text, target in targets:
        if args.write:
            target.write_text(text, encoding="utf-8")
            # len(text) counts characters; the ceilings, the runtime cap and
            # every measurement in artifact_limits.py are bytes, and these
            # files carry enough box-drawing and em-dashes for the two to
            # differ by ~2 KB on run-all. Round 64 站1: printing one under the
            # other's name is how a ratchet entry ends up quoting a size the
            # tree never had.
            size = len(text.encode("utf-8"))
            print(f"[workflowgen] wrote {target.relative_to(REPO_ROOT)} ({size} bytes)")
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
