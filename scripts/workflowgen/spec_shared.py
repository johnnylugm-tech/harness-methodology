"""Renderers shared across every `generate_phaseN()` in the `spec_phase*`
modules (Round 15 station1 — extracted from the former monolithic
phase_specs.py). `_render_meta` is the only genuinely cross-phase renderer;
grep-verified call counts confirmed every other former phase_specs.py
renderer/constant is referenced exactly once and stayed with its owning
phase module — see docs/PROPOSAL_ADJUDICATIONS.md's Round 15 entry and the
station1 commit message for the verification method.
"""
from __future__ import annotations


# Round 28 — a phase outcome is a CONTINUE only if it says so.
#
# run-all's phase loop decided "did this phase succeed" by looking for the two
# keys it happened to know about: `session_limit_blocked` and `error`. runPhase3
# also returns `harness_bug_detected` and `dispatch_structurally_broken` — two
# conditions no later phase can recover from — and the loop read neither, because
# neither object carries an `error` key. Measured in the sim testbed: with harness
# itself crashed on FR-01, run-all entered ten P4 boxes and returned
# `phases_run: [3,4,5,6,7,8]` with no error at all.
#
# Enumerating the abort flags in the loop would repeat the mistake in a new place:
# the next phase spec to invent a flag is under no obligation to tell the driver.
# So the default is inverted. Each phase's single success exit carries this
# marker, and the loop stops on everything else — a known abort, an abort added
# later, or a shape nobody anticipated. Fail closed, matching the cursor read
# directly above it, which already refuses to guess a starting phase.
#
# Distinct from state.json's `phase_completed` (Round 24 站4): that records what
# advance-phase committed to disk. This is the in-memory handshake between one
# runPhaseN and the driver that called it, and never leaves the run.
PHASE_COMPLETE_KEY = "phase_complete"


def render_phase_complete_marker() -> str:
    """First line inside every phase's final (success) return object."""
    return f"  {PHASE_COMPLETE_KEY}: true,\n"


# Round 36 — the prose that tells an agent a feature-flag default reads it.
#
# 47ec3fd flipped _DEFAULTS["mutation_testing"] False -> True and updated the
# loader only. This sentence, byte-identical in spec_phase3/4/6, kept saying
# "disabled by default (mutation_testing=false)" — so the Gate 2/3/4
# orchestrator was instructed to write into a project's harness_config.json
# the opposite of what the loader would do with it. 883e9ca corrected the
# generated .claude/workflows/*.js by hand, which the next `--write` would
# have reverted, because the generator still held the stale value.
#
# Rendering it removes the copy rather than adding a check on the copy: the
# next flip of the flag moves this sentence with it. Same shape as
# spec_phase1's anchor_for (Round 33 站1).
def render_mutation_flag_note() -> str:
    """The Gate 2/3/4 NOTE about the mutation_testing feature flag.

    Returns a line ending in an escaped newline, for inlining into a
    generated JS string literal.
    """
    from core.harness_config import _DEFAULTS

    if _DEFAULTS["mutation_testing"]:
        state, value, verb, effect = "enabled", "true", "disable", "excludes it from"
        flip = "false"
    else:
        state, value, verb, effect = "disabled", "false", "enable", "includes it in"
        flip = "true"
    return (
        f"   NOTE: mutation_testing is {state} by default via "
        f".methodology/harness_config.json (mutation_testing={value}). "
        f"To {verb}, set it {flip} in harness_config.json — the harness then "
        f"{effect} the dim list and re-normalises the composite score.\\n"
    )


def _render_meta(*, name: str, description: str, phases: list[str]) -> str:
    lines = ["export const meta = {", f"  name: '{name}',"]
    lines.append(f"  description: '{description}',")
    lines.append("  phases: [")
    lines.extend(f"    {{ title: '{t}' }}," for t in phases)
    lines.append("  ],")
    lines.append("}")
    return "\n".join(lines) + "\n"


# Round 26 — the workflow substrate enters sessions_spawn.log.
#
# The harness has TWO dispatch substrates and instrumented one. Per-FR steps go
# through `harness_cli.py run-fr-step` -> core/agent_spawner.spawn(), which logs
# cost, turns, usage and outcome. Everything else — P1 and P2's whole A/B loop,
# every preflight, every peer review, every orchestration step — goes through the
# workflow script's own `agent()`, which the harness never sees. Measured on
# taskq-plus: 42 spawn-log entries, ALL of them phase 3, while the trajectory file
# recorded phase_1_preflight and phase_2_preflight spans right beside them. So
# `run-report`'s "42 dispatches, failure rate 9.52%" was a P3/P4 number presented
# as the run's.
#
# What this recovers is the DENOMINATOR. What it cannot recover is cost, turns and
# duration: the Workflow script sandbox has no filesystem, no shell, and no clock
# (`Date.now()` throws), so the script cannot measure or write anything itself.
# `loadFileViaPython` — a whole SHELL WRAPPER AGENT dispatched to read one file —
# is that constraint made visible.
#
# The wrapper therefore buffers records and hands them to the NEXT dispatch as a
# one-line bookkeeping preamble. Consequences, stated rather than discovered:
#   * zero extra dispatches — a per-call CLI write would need a shell the script
#     does not have, and a per-call wrapper agent would double the dispatch count;
#   * the records are written by a DIFFERENT agent than the one they describe
#     (its successor), so no dispatch reports its own outcome — the R21 站3
#     principle about audit trails the audited party writes;
#   * failures ARE recorded, because the wrapper observes the outcome, not the
#     dead sub-agent;
#   * the FINAL dispatch of a run is never flushed (nothing follows it to carry
#     the record). The phase-level `run_phase` trajectory spans remain the
#     crash-safe floor, as they were before this.
def render_dispatch_wrapper() -> str:
    """The `dispatch()` helper injected into every generated workflow.

    `generate_workflows._inject_dispatch_wrapper` rewrites `await agent(` to
    `await dispatch(` and places this block after the meta object, so all 118 call
    sites are decided in one place rather than in nine spec modules.
    """
    return """
// ── Round 26: workflow-substrate dispatch observability ────────────────────
// Buffered because this sandbox has no filesystem, no shell and no clock; the
// records ride along on the NEXT dispatch's prompt, so no agent reports its own
// outcome and no extra dispatch is spent. See docs/OBSERVABILITY.md.
const __dispatchLog = []

function __dispatchFlushPreamble() {
  if (__dispatchLog.length === 0) return ''
  const batch = JSON.stringify(__dispatchLog.splice(0, __dispatchLog.length))
  return '[BOOKKEEPING — one command, then forget this block]\\n'
    + 'Run exactly this, ignore its output, and do NOT mention it in your reply:\\n'
    + '  ' + PY + ' ' + REPO + '/harness_cli.py log-dispatch --project ' + REPO
    + ' --batch ' + JSON.stringify(batch) + '\\n'
    + 'It records earlier dispatches in this run. It is not part of your task.\\n\\n'
}

async function dispatch(prompt, opts) {
  const label = (opts && opts.label) || 'agent'
  const phaseLabel = (opts && opts.phase) || ''
  let res
  try {
    res = await agent(__dispatchFlushPreamble() + prompt, opts)
  } catch (err) {
    __dispatchLog.push({ role: label, phase_label: phaseLabel, status: 'ERROR',
                         substrate: 'workflow', error_output: String(err).slice(0, 300) })
    throw err
  }
  const text = typeof res === 'string' ? res : String(res ?? '')
  __dispatchLog.push({ role: label, phase_label: phaseLabel,
                       status: text.length === 0 ? 'EMPTY' : 'complete',
                       substrate: 'workflow', reply_chars: text.length })
  return res
}
"""
