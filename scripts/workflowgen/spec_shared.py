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


# Round 39 站3 — the dimension list a prompt states is the one the gate scores.
#
# Round 18 站2 made the gate_configs YAML the only authority on a *threshold*,
# and Round 38 站2 finished the job for the architecture floor. The *set* and
# the *count* were left behind (R38-DEFER-2), and drifted exactly as a copy
# does. Measured at the start of this station:
#
#   generator        prose claimed   prose listed   the YAML scored
#   spec_phase3.py   9 dims          —              12
#   spec_phase4.py   15 dims         13             16
#   spec_phase6.py   14 dims         13             15
#
# All 13 listed thresholds were right; the numbers were never the problem. The
# three omitted dimensions were traceability, mutation_testing and
# adversarial_review — every one of them framework-owned or framework-blocking,
# i.e. precisely the ones an agent cannot discover by doing the work. Gate 2's
# list was also missing the architecture dimension Round 38 站1 had added to it
# a day earlier: a copy is stale the moment the source moves.
#
# So the list, the count, the framework-owned grouping and the composite floor
# are all read from the YAML at generation time. What a regeneration cannot fix
# is a per-project feature flag (mutation_testing / crg_architecture /
# phase4_llm_review), which is why the rendered line still points at the list
# run-gate printed as the authoritative one — the same instruction spec_phase3
# has always given in place of an enumeration.
def _gate_dimension_tokens(gate: int) -> list[str]:
    from core.quality_gate.gate_thresholds import load_gate_dimensions

    return [
        f"{d['name']}({float(d['threshold']):g})"
        for d in load_gate_dimensions(gate)
    ]


def gate_dim_count(gate: int) -> int:
    """How many dimensions gate *gate* scores — for the prose that says so."""
    return len(_gate_dimension_tokens(gate))


def render_framework_owned_note(gate: int) -> str:
    """The FRAMEWORK-OWNED line — which dims the agent must not score itself."""
    from core.quality_gate.gate_thresholds import framework_owned_dimensions

    owned = ", ".join(
        f"{name} ({tool})"
        for name, tool in framework_owned_dimensions(gate).items()
    )
    return (
        "   FRAMEWORK-OWNED (do NOT self-score — finalize-gate computes these "
        f"and overwrites what you write): {owned}.\\n"
    )


# Round 58 — the dim list is authoritative; off-list dims are off-limits this round.
#
# run-gate --gate N prints the dim list at evaluation prompt. Each generator
# already teaches the agent to read the list verbatim ("Dims: use the exact
# `dimensions:` list … — do NOT hand-copy a dim list here"). That phrasing is
# about *what to evaluate*, not *what NOT to*. Measured on taskq-cc Gate 2
# round-1: an LLM agent saw the list excluded mutation_testing (features flag
# false), confirmed the exclusion in its own thinking, then spent the rest of
# the round (~50min / 333 entries) chasing a test isolation bug in test_fr06
# because the dim's `mutation_enforcer` baseline happens to fail on it. The
# dim was disabled on purpose (Round 50 站3 commit 1b4c3d8); the gate scoring
# excludes it; G2's responsibility is to score what the list contains.
#
# Anything off the list — feature-flag-disabled, framework-blocked at compile
# time, or simply not in gate_N_exit.yaml — stays out for this round. The
# only ways to bring a dim into the round are: flip the feature flag in
# .methodology/harness_config.json and re-run run-gate, or wait for the next
# round. Within a round, off-list dim work is wasted dispatch.
#
# The rule does not name any specific dim — a flag flip from mutation_testing
# to a different one moves with the rule instead of leaving a stale name in
# the prompt, same principle as `render_mutation_flag_note` (Round 36).
def render_excluded_dims_rule() -> str:
    """The EXCLUDED DIMS line — anything not on the G2/G3/G4-printed list.

    Returns a multi-line note (each line ends in an escaped newline) for
    inlining into a generated JS string literal — same convention as
    `render_framework_owned_note` and `render_mutation_flag_note`.
    """
    return (
        "   EXCLUDED DIMS: a feature-flagged dim disabled in "
        ".methodology/harness_config.json (or otherwise absent from the run-"
        "gate --gate N printed dim list) is OUT OF SCOPE for this round. "
        "Do NOT evaluate it, run its scoring tools, or fix code issues you "
        "discover while evaluating OTHER dims — even if you find a bug that "
        "would be caught by the disabled dim. The flag was flipped on "
        "purpose (e.g. to sidestep a wall-time budget), the gate scoring "
        "excludes it, and your responsibility this round is the dims ON the "
        "list. Re-enabling a dim is harness_config.json + restart-from-run-"
        "gate, not inline scope expansion.\\n"
    )


def render_dimension_table(gate: int) -> str:
    """The `N dims: …` enumeration + the FRAMEWORK-OWNED line, for gate *gate*.

    Lines end in an escaped newline for inlining into a generated JS string
    literal — same convention as `render_mutation_flag_note`.
    """
    from core.quality_gate.gate_thresholds import GATE_CONFIG_NAMES

    tokens = _gate_dimension_tokens(gate)
    return (
        f"   {len(tokens)} dims per {GATE_CONFIG_NAMES[gate]}: {' '.join(tokens)}.\\n"
        # Escaped: this lands inside a single-quoted JS string literal.
        "   (A project\\'s feature flags can remove dims; the `dimensions:` "
        "list run-gate just printed is the authoritative one.)\\n"
        + render_framework_owned_note(gate)
    )


def render_gate_dims_summary(gate: int) -> str:
    """The parenthetical a gate's log line carries: composite floor + dim census."""
    from core.quality_gate.gate_thresholds import (
        framework_owned_dimensions,
        load_score_gate,
    )

    total = len(_gate_dimension_tokens(gate))
    owned = framework_owned_dimensions(gate)
    return (
        f"composite ≥{load_score_gate(gate):g}, {total} dims: "
        f"{total - len(owned)} self-scored + {'/'.join(owned)} framework-owned"
    )


def render_gate_pass_line(gate: int, *, d4_threshold: float, extra: str = "") -> str:
    """The `pass_line_desc` a gate loop prints — every number read, none typed.

    `extra` is the one clause that is not derivable (gate 4's DA artifacts).
    """
    from core.quality_gate.gate_thresholds import (
        load_gate_thresholds,
        load_score_gate,
    )

    arch = load_gate_thresholds(gate)["architecture"]
    return (
        f"composite ≥{load_score_gate(gate):g} AND all dims ≥ threshold{extra} "
        f"AND D4 ≥{d4_threshold:g}% AND CRG architecture ≥{arch:g}"
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
