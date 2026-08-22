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
RUNALL_MAX_BYTES = 370702  # 2026-08-22: +22176 — `preview-next-phase` (Round 15 §2's read-only P(N+1)-entry-blocking preview) was never wired into any phase workflow; the ten `_DELAYED_BLOCKING_PREFLIGHTS` categories (drift_detection/sab/traceability/fr_spec_consistency/property_spec/artifact_consistency/reliability_lint/config_liveness/previous_phase_artifacts/bvs_phase_order) were structurally invisible to a phase's own authoring loop no matter how many rounds it ran, surfacing only at that phase's `advance-phase` exit gate — after the whole phase's authoring/review/push cost was already sunk. Measured on taskq-api P2 2026-08-22: ~40 agent dispatches / ~50 minutes before the exact TEST_SPEC.md AC-coverage gap surfaced this way, a gap `check-artifact-consistency` mid-loop could never have caught (that CLI gates the same checks on `current_phase >= 3`, always false while still inside the phase's own loop). `js_blocks.render_preview_next_phase(phase)` adds one read-only check + a bounded (3-round) fixer step before each phase's own Push/Advance; inlined once per phase × 8 phases is the whole of this growth (trimmed three times before this ceiling moved: the fixer prompt's worked example and restated SCOPE prose were cut, then the checker dispatch was switched from a plain-text regex read to `schema: VERDICT_SCHEMA` — dropping its `render_session_block_guard` copy entirely — because sim_runner's generic happy-path responder synthesizes a passing object for any schema call but only ever returns a fixed narrative string for a schema-less one, and the plain-text regex never matched it; round50's `post-advance-push` sim caught this before it shipped). Measured 370402, ceiling set 300 bytes above it, same headroom convention. Previous: 348526. # 2026-08-21: **LOWERED** -82 — Round 66 站2. Six prompt sites stopped telling the agent to walk away from a background harness run it had just launched ("do not kill the PID", 32 shipped copies) and now tell it to `kill <PID>`, which station 1 made mean "reap the whole tree"; the delta fast-path's launch instruction stops reading as a fan-out ("for every FR" -> "ONE FR AT A TIME"), matching what its own comment eight lines below already claimed. Every replacement was written shorter than what it replaced, so the file lost 159 bytes and the ceiling follows it down — the second time this number has gone down and the second time it was prompts shortening, not a ceiling moving. The first draft was 14 bytes OVER 348608 and `--write` refused; the fan-out sentence was cut rather than the ceiling raised. Measured 348426, ceiling 100 above it (Round 65's 23 was too tight to absorb a typo fix). Previous: 348608. # 2026-08-21: +0 — Round 65 站2. The Phase 4 coverage command stops naming `03-development/{tests,src}` and reads test_target / cov_target out of phase4_ctx.json, which load-context now fills from resolve_targets; step 1 loses its prose copy of the scoping rule at the same time. Net -7 against the entry below, so the ceiling does not move. Measured 348585, ceiling 23 above it — the tightest this has ever been, and deliberately: the first draft of the new prompt was 65 bytes OVER and `--write` refused to write, the prompt was shortened twice instead. # 2026-08-20: +0 — 73be69c, corrected here by Round 65 站3. As written this entry read "+35 ... Previous: 348608" with the constant still at 348608: the ceiling did not move, and +35 was the delta of the FILE underneath it (348301 -> 348336). Every other entry's signed number is what it did to the CEILING and its `Previous:` is the ceiling before that, so this one was in a different vocabulary from the six below it and its arithmetic did not close. Round 64's guard reads `Measured` and passed — 348336 was that tree's size — so the entry was wrong only in the field nobody read; test_the_ratchet_notes_arithmetic_closes now walks the chain instead. Headroom fell 307 -> 272 without being restated as such. Measured 348336, ceiling 272 above it. Previous: 348608. # 2026-08-20: +48 — Round 64 站6 folded three hand-written session guards into render_session_block_guard; the DELTA sites gain a `step: frId` field they never carried (8 copies inlined into run-all), which is the whole of the growth. Measured 348301, ceiling 307 above it. Previous: 348560. # 2026-08-20: -440 — Round 64 站1/站2. Restoring the dispatch wrapper 6e7942e deleted adds it back to each generated file (run-all carries one copy, injected over the composite); Phase 2's three session guards moved inside their retry loops, which is a reordering, not growth. Measured 348253, ceiling set 307 bytes above it. Corrections to the entry below, both found by reading the tree rather than the note: (a) it claims "Measured 348693" — run-all.js was 348457 at 983b46e, the commit that wrote it, and no commit has ever produced 348693. The generator printed `len(text)`, a CHARACTER count, under the label "bytes"; the ~2 KB gap is this file's box-drawing and em-dashes, and 站1 fixed the print. (b) 6e7942e then removed 1733 bytes and left the ceiling alone, so the slack stood at 2276 with nobody having measured it. test_the_ratchet_note_reports_the_size_it_measured now holds the newest entry to the shipped size. Previous: 349000. # 2026-08-19: +4240 — Round 62 (session_limit_blocked parity + SAB type/dim prompt). Eight new `session_limit_blocked` guards inlined into Phase 2's ad-hoc halt sites (preflight, adr-constitution, artifact-consistency×2, sab-generation, constitution, push-checkpoint, advance-phase) and the P2 SAB prompt gains a CRITICAL clause distinguishing the type vocabulary (14 names) from the dimension vocabulary (18+-name disjoint). Run-all grows by the Phase 2 deltas since each phase body is inlined once. Measured 348693; ceiling set 307 bytes above it, same headroom convention. Previous: 344760. # 2026-08-19: **LOWERED** -2540 — Round 60 站3 deleted render_mutation_flag_note and render_excluded_dims_rule with the mechanism they describe (no dimension can be switched off), taking their prose out of the three gate prompts and out of run-all three times over. Measured 344567, down from 347147; ceiling set 193 bytes above it, the same headroom convention as the 2026-08-17 entry. This is the first time this number has gone down, and it is what the note below asks for before a ceiling is raised. Correction: 304b90d's commit message claims 342509 — that reading was taken during a moment when render_framework_owned_note was accidentally absent from spec_shared.py; the renderer was restored before that commit and 344567 is the shipped size. Previous: 347300. # 2026-08-18: +1900 — Round 58 (f4be095/c939bbf): EXCLUDED DIMS rule in spec_shared.render_excluded_dims_rule inlined into phase3/phase4/phase6 gate prompts, and summed 3 times in run-all.js. Measured 347147; ceiling set at 347300 (153 bytes headroom). Previous: 345400. 2026-08-17: +400 — 59579b3's retry hint now names both failure modes of an unresolvable citation instead of only the out-of-range one: (a) the cited file does not exist (the common case — an out-of-tree path like `spec_parser.py` in a project that has none), (b) the line number is past the end. Three prompt lines in js_blocks.render_shell_wrapper_retry, inlined into phase1/phase2/phase6 and summed again in run-all. Measured 345314; the ceiling is set 86 bytes above it rather than exactly at it so the next reader is not forced to re-ratchet for a typo fix. Previous: 345000. 2026-08-14: v33b P2 citation-validator fix (run-all.js halt on taskq-super). Inlines the prompt rule additions (positive + negative citation example + DIGITS-after-colon rule) into every phase's buildBPrompt, plus the abLoop try/catch + reject-block prepend into Phase 2's sub-task A/B loop. Phase 1/2/6 all gain ~50 lines; run-all.js is their sum + the inlined copy. Pure bug-fix growth (mirrors Phase 1's existing pattern); no new agents or dispatches. Previous: 340000.


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
