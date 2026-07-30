"""Renderers shared across every `generate_phaseN()` in the `spec_phase*`
modules (Round 15 station1 — extracted from the former monolithic
phase_specs.py). `_render_meta` is the only genuinely cross-phase renderer;
grep-verified call counts confirmed every other former phase_specs.py
renderer/constant is referenced exactly once and stayed with its owning
phase module — see docs/PROPOSAL_ADJUDICATIONS.md's Round 15 entry and the
station1 commit message for the verification method.
"""
from __future__ import annotations


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
