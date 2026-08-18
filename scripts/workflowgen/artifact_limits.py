"""Size limits the generated workflow files must satisfy — and who reads them.

Round 60 站1. Both numbers were constants inside
``tests/test_workflow_js_conventions.py``, which meant only a pytest run
enforced them. `scripts/workflowgen/generate_workflows.py --write` printed the
byte count of what it had just written and checked nothing, so `f4be095`
shipped a `run-all.js` 1,747 bytes over the ratchet and CI was the first thing
to notice. The producer holds them now; the conventions test keeps reading the
same constants from here, so there is one number, not two.

``RUNTIME_PARSE_LIMIT_BYTES`` is the cliff (the Workflow validator and the
runtime both refuse to parse past it). ``RUNALL_MAX_BYTES`` is the guard rail
in front of it, and only ``run-all.js`` carries one — it inlines all eight
phase bodies, so it absorbs eight files worth of growth.
"""

from __future__ import annotations

RUNALL_FILE = "run-all.js"

MAX_BYTES = 524288  # 512 KiB — playbook §4 hard error (validator + runtime)

# Headroom ratchet, separate from the runtime's hard cap. run-all grows at
# roughly eight times the rate of any single phase file, and the failure mode
# at 512 KB is the runtime refusing to parse — not a warning. Raising this
# number is a deliberate act: the right first response to hitting it is to
# shorten prompts in scripts/workflowgen/, not to move the ceiling.
RUNALL_MAX_BYTES = 347300  # 2026-08-18: +1900 — Round 58 (f4be095/c939bbf): EXCLUDED DIMS rule in spec_shared.render_excluded_dims_rule inlined into phase3/phase4/phase6 gate prompts, and summed 3 times in run-all.js. Measured 347147; ceiling set at 347300 (153 bytes headroom). Previous: 345400. 2026-08-17: +400 — 59579b3's retry hint now names both failure modes of an unresolvable citation instead of only the out-of-range one: (a) the cited file does not exist (the common case — an out-of-tree path like `spec_parser.py` in a project that has none), (b) the line number is past the end. Three prompt lines in js_blocks.render_shell_wrapper_retry, inlined into phase1/phase2/phase6 and summed again in run-all. Measured 345314; the ceiling is set 86 bytes above it rather than exactly at it so the next reader is not forced to re-ratchet for a typo fix. Previous: 345000. 2026-08-14: v33b P2 citation-validator fix (run-all.js halt on taskq-super). Inlines the prompt rule additions (positive + negative citation example + DIGITS-after-colon rule) into every phase's buildBPrompt, plus the abLoop try/catch + reject-block prepend into Phase 2's sub-task A/B loop. Phase 1/2/6 all gain ~50 lines; run-all.js is their sum + the inlined copy. Pure bug-fix growth (mirrors Phase 1's existing pattern); no new agents or dispatches. Previous: 340000.


def size_problems(filename: str, text: str) -> "list[str]":
    """Every size rule *filename* breaks, named with the measured value.

    Returns an empty list for a file that fits. Pure and public so the
    generator's pre-write check and the conventions test ask the same
    question of the same bytes.
    """
    size = len(text.encode("utf-8"))
    problems = []
    if size > MAX_BYTES:
        problems.append(
            f"{filename}: {size} bytes exceeds the {MAX_BYTES}-byte runtime "
            f"parse limit"
        )
    if filename == RUNALL_FILE and size > RUNALL_MAX_BYTES:
        problems.append(
            f"{filename}: {size} bytes over the {RUNALL_MAX_BYTES}-byte "
            f"headroom ratchet — shorten prompts in scripts/workflowgen/ "
            f"before raising it (see RUNALL_MAX_BYTES)"
        )
    return problems
