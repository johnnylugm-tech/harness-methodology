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


# The D4 spec-coverage floor each exit gate verifies against, keyed by the
# phase that closes that gate (core.phase_topology.EXIT_GATE_MAP's key set).
#
# Round 69 站1. spec-coverage-check's floor is not a gate dimension, so the
# gate config has nothing to read and each phase named its own constant:
# _D4_THRESHOLD_P3/P4/P6, three literals whose comments pointed at each other
# ("See spec_phase4._D4_THRESHOLD_P4"). That was survivable while exactly one
# renderer per phase used the number. `render_advance_loop` now needs the same
# number to re-verify the exit gate against the tree it is about to advance,
# and a fourth hand-copy is how a threshold starts drifting from the gate it
# claims to describe (Round 33's shape, four rounds running).
D4_THRESHOLDS: "dict[int, float]" = {3: 60.0, 4: 80.0, 6: 90.0}


# Poll-loop budgets for the G*c/D4/mutation "run BACKGROUNDED" steps inside
# render_gate_loop (nohup ... & echo $!, then `kill -0 <PID>` every
# POLL_INTERVAL_S seconds until DONE or the cap is hit — see _GATE2_STEPS'
# G2c for the canonical wording this mirrors). Unlike D4_THRESHOLDS above
# (a hand-copied literal only because spec-coverage-check's floor has no
# YAML source to read), core/harness_config.py::STALL_TIMEOUTS DOES exist as
# a real importable table — so this reads it live, the same lazy-import-in-
# function convention _gate_dimension_tokens/render_framework_owned_note use
# a few lines down, rather than hand-copying 3600/1200 a second time.
POLL_INTERVAL_S = 15


def _poll_cap(stall_timeout_key: str) -> int:
    """Poll-loop iteration cap for a STALL_TIMEOUTS budget, at POLL_INTERVAL_S cadence."""
    from core.harness_config import STALL_TIMEOUTS

    return STALL_TIMEOUTS[stall_timeout_key] // POLL_INTERVAL_S


def mutation_poll_cap() -> int:
    return _poll_cap("mutation")


def d4_poll_cap() -> int:
    return _poll_cap("spec_coverage")


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
# are all read from the YAML at generation time. Round 60 站2 retired the three
# per-project feature flags that a regeneration could not have accounted for,
# so the YAML is now the whole answer; the rendered line still points at the
# list run-gate printed, because that list is rendered from the same YAML.
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
    `await dispatch(` and places this block after the meta object, so all 118
    call sites are decided in one place rather than in nine spec modules.

    Round 64 站1 — restored. 6e7942e emptied this body to a single
    pass-through line under a commit message that says the wrapper's COMMENT
    was trimmed; the comment above survived and the mechanism did not, which
    is Round 39's shape with the two halves swapped. Three guards were then
    rewritten to endorse the removal (the unit test, the sim test, and the
    registry entry, in 020695e and 54daf48). Measured on the corpus before
    restoring: the last row this wrapper wrote is 4.5 hours older than the
    commit that deleted it, and the 11 EMPTY/ERROR rows it caught across six
    projects — the exact signature 9fd9a12's classifier was built to read —
    appear in no other record.

    The preamble's wording changed with the restore. It used to say "ignore
    its output, and do NOT mention it in your reply", which is the same
    suppress-verification clause 6e7942e removed from recordBlock's prompt
    and was right to remove. A failed bookkeeping write is now reportable in
    one line; it is still not the agent's task.

    **Round 79 站1 — the env-fp cache-buster is removed, and must not come
    back in that shape.** `4c24cf37` added `ENV_FP` / `ENV_FP_SCHEMA` /
    `getEnvFingerprint()` and tagged every prompt with
    `[env-fp SAB=… HEAD=…]`, to force a cache miss after `amend-sab` repaired
    the project state an RC=25 halt had complained about. It could not work,
    for two independent reasons, both measured on the shipped tree:

      * **The key travelled through the cache it was busting.** The
        fingerprint was fetched by `dispatch()` — the very call being cached.
        Its prompt was a pure function of `REPO` and its opts a fixed
        literal, so on the second launch `env-fp-init` was itself a cache hit
        returning launch one's fingerprint, every downstream tag was
        identical, and the replay happened anyway. The mechanism could fire
        exactly once: the first launch after it shipped, which is the launch
        its commit message cites as proof.
      * **On the documented launch it never fired at all.**
        `getEnvFingerprint()` read `REPO` from inside `let REPO = await
        resolveRepo()`'s own initializer, and `resolveRepo()` dispatches when
        `args.repo` is absent — which is CLAUDE.md's documented form and
        playbook §7's first-class walk-up path. The TDZ `ReferenceError` was
        swallowed by the helper's own `catch`, pinning the tag to a constant
        `none/none` for the whole run. Driven through `sim_runner.mjs` with
        `args: {}`: `env-fp-init` never dispatched, 4/4 prompts carried one
        constant tag.

    **Round 79 站2 — `__RUN_TAG`, the replacement.** The constraint the
    mechanism above failed is that the busting key may not travel through
    `agent()`. `args` is the only value this sandbox receives that does not,
    so the key is `args.run_tag`: an operator-supplied string, folded into
    every prompt, evaluated once at script start from a parameter that cannot
    be in a TDZ. No dispatch, no schema, no `catch`, and an absent or blank
    value renders `''` — prompts byte-identical to a run without it.

    That it is the operator who supplies it is the point, not a compromise:
    the operator is who knows the project state changed. `js_blocks`'
    RC=25 halt names the relaunch form so it is discoverable at the moment it
    is needed.
    """
    return """
// ── Round 26: workflow-substrate dispatch observability ────────────────────
// Buffered because this sandbox has no filesystem, no shell and no clock; the
// records ride along on the NEXT dispatch's prompt, so no agent reports its own
// outcome and no extra dispatch is spent. See docs/OBSERVABILITY.md.
const __dispatchLog = []

// Round 79 站2: cache-buster key. The runtime caches agent() on (prompt, opts),
// so a relaunch after an SAB repair can replay a stale RC=25. `args` is the only
// value here that does not travel through agent(), so the key comes from it —
// operator-supplied, evaluated at script start (a parameter cannot be in TDZ),
// no dispatch. Blank/absent => '' => prompts byte-identical to no mechanism.
// See render_dispatch_wrapper's docstring for why a fingerprint cannot work.
if (typeof args === 'string') { try { args = JSON.parse(args) } catch {} }
const __RUN_TAG = (args && typeof args === 'object'
  && typeof args.run_tag === 'string' && args.run_tag.trim())
  ? '[run ' + args.run_tag.trim().slice(0, 32) + '] ' : ''

function __dispatchFlushPreamble() {
  if (__dispatchLog.length === 0) return ''
  const batch = JSON.stringify(__dispatchLog.splice(0, __dispatchLog.length))
  return '[BOOKKEEPING — not part of your task]\\n'
    + 'Run this once via Bash, then continue with the task below:\\n'
    + '  ' + PY + ' ' + REPO + '/harness_cli.py log-dispatch --project ' + REPO
    + ' --batch ' + JSON.stringify(batch) + '\\n'
    + 'It records earlier dispatches in this run. If it fails, say so in one line and carry on.\\n\\n'
}

async function dispatch(prompt, opts) {
  const label = (opts && opts.label) || 'agent'
  const phaseLabel = (opts && opts.phase) || ''
  let res
  try {
    // __RUN_TAG is at line 1, before the preamble and outside it.
    res = await agent(__RUN_TAG + __dispatchFlushPreamble() + prompt, opts)
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


# Round 63 — `length < 10` magic number misclassified 9-char `"SAB: PASS"` as
# a session-limit block (measured on taskq-cc 2026-08-19, workflow
# wf_018138d9-78c): the sab-generation agent returned exactly `SAB: PASS`,
# the prior `< 10` check fired first, the run aborted relaunchable with
# state.json untouched, and `.methodology/SAB.json` was already on disk.
#
# The fix separates the two checks the prior pattern fused. The runtime
# safety classifier (and a quota cap) signals a blocked dispatch by
# resolving to `null` / `undefined` / empty string / non-string — there
# is no signal inside a "short but non-empty" string. The PASS regex below
# this guard is the only correct reader of a 9-or-10-character reply; the
# magic number was the wrong layer to ask.
#
# 11 sites across four generators previously inlined this pattern
# (spec_phase2.py: 112, 237, 256, 343, 371, 390, 524, 550;
# spec_phase3.py: 238; spec_phase6.py: 247; spec_phase8.py: 127).
#
# Round 64 站6: that list was wrong in both directions. Ten of the eleven
# called the helper, not eleven — spec_phase3.py was named and still
# hand-wrote it — and three more copies in js_blocks.py (the per-FR DELTA
# loop, the advance loop, the gate loop) were not mentioned at all, so an
# edit to the wording would have reached ten of thirteen sites. The DELTA,
# advance and TDD copies now call this helper too, via `indent` and
# `step_js`. The gate loop is the one site that legitimately does not: it
# sets a flag and `break`s out of its retry loop rather than returning from
# the dispatch, and its payload is keyed by `gate` instead of `phase`.
# tests/test_workflowgen.py::TestSessionBlockGuard pins that inventory, so
# a fourteenth hand-written copy is a failure rather than a comment to
# update.
#
# The driver at run-all.js:4218-4221 already recognizes
# `outcome.session_limit_blocked` and routes through `recordBlock(n,
# 'session-limit', …)` so the run aborts relaunchable (state.json untouched,
# completed phases skipped on resume). The return shape this helper
# produces is the same one the driver reads — `phase`, `step`, optional
# `fr_id` / `gate1Pass`, and a human-readable `message`.
def render_session_block_guard(
    var_name: str,
    step_name: str,
    phase_no: int,
    *,
    extra_fields: str = '',
    message: str,
    indent: str = '',
    step_js: "str | None" = None,
    payload: str = 'string',
) -> str:
    """Emit the JS guard that distinguishes a session/rate-limit block from a
    hard PASS/FAIL failure.

    Fires ONLY on a truly empty payload — `null`, `undefined`, `''`, or a
    non-string — the runtime safety classifier's signature for
    blocked-by-classifier, and a quota cap's signature too. A short but
    non-empty string (e.g. `SAB: PASS`, 9 chars) falls through to the next
    halt() check, which reads the sub-agent's PASS/FAIL verdict via regex.

    `payload='object'` is the same guard for a dispatch that carries a schema:
    the blocked shapes are identical (`null` / `undefined`), but the "wrong
    type" half has to test for an object rather than a string, and there is no
    empty-string case. Round 70 站3 moved the per-FR GATE1 / GATE1-DELTA
    dispatches onto RC_SCHEMA, and a guard still asking `typeof x !== 'string'`
    would have called every successful one of them a rate limit.

    Place it immediately after the dispatch it guards — INSIDE any retry
    loop, not after the loop's closing brace. Round 64 站2: Phase 2's
    preflight (3 attempts), constitution (5) and push-checkpoint (5) had it
    outside, so a quota cap on the first attempt kept dispatching into the
    wall for 2 and 4 more turns while the log line it prints already said
    "aborting retries". phase6, phase8 and the gate loop return or break
    from inside, which is the shape this helper assumes.

    `extra_fields` is rendered inside the returned object literal so sites
    that need to carry more (Phase 3's `fr_id` + `gate1Pass`) keep their
    payload shape. `message` is the human-readable text shown by the
    record-block CLI; each site passes its own to keep the wording
    precise (Phase 6/8 mention the GUARD step's skip-on-resume behaviour;
    Phase 3 mentions sentinel GUARD). Both are emitted inside the JS, and
    `message` inside single quotes, so a site whose text names a runtime
    value passes the concatenation itself (`… during ' + frId + ' TDD…`).

    `indent` prefixes every emitted line, for sites nested inside a loop
    body. `step_js` names the step with a JS expression instead of a
    literal — the per-FR sites report the FR id they were working on, which
    is not known until the loop runs. It lands in a `+`-concatenation, so
    an expression looser than that needs its own parentheses.
    """
    extra = (', ' + extra_fields) if extra_fields else ''
    log_subject = f"' + {step_js} + '" if step_js else step_name
    step_value = step_js if step_js else f"'{step_name}'"
    if payload not in ('string', 'object'):
        raise ValueError(f"payload must be 'string' or 'object', not {payload!r}")
    empty = (
        f"|| {var_name} === '' || typeof {var_name} !== 'string'"
        if payload == 'string'
        else f"|| typeof {var_name} !== 'object'"
    )
    return (
        f"{indent}if ({var_name} === null || {var_name} === undefined "
        f"{empty}) {{\n"
        f"{indent}  log('  {log_subject} agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')\n"
        f"{indent}  return {{ session_limit_blocked: true, phase: {phase_no}, step: {step_value}{extra}, message: '{message}' }}\n"
        f"{indent}}}\n"
    )
