"""`run-all.js` assembly — Phases 1-8 inlined into one workflow file.

WHY THIS IS A COMPOSER, NOT A NINTH HAND-WRITTEN SPEC
-----------------------------------------------------
The hard requirement is that run-all's final artifacts match running the
eight phase workflows in sequence. The only way to guarantee that without
a live E2E run is to make run-all *literally the same generated text* —
so this module calls `generate(N)` for N in 1..8 and mechanically
re-hosts each result inside an `async function runPhase<N>()`.

Three mechanical problems and their solutions:

  1. Name collisions. Every phase file declares `ctx`, `frIds`,
     `gate1Pass`, `advancePass`, `ADVANCE_MAX_ROUNDS`, ... at top level,
     and phase1/phase2/phase6 each declare their OWN `buildBPrompt` with
     different constants (phase1 requires reason >= 40 chars, phase2
     >= 100). Wrapping each body in a function gives every phase its own
     scope, so none of that has to be reconciled.

  2. Top-level `return`. Each phase body ends with `return {...}` and
     early-returns on failure. Inside a function those become the
     function's return value; the driver checks it and stops the run.

  3. Duplicate `phase()` titles. Eight files declare "Entry & Preflight",
     "Advance", "Sync" ... A progress view with eight identical boxes is
     unreadable, so every title is prefixed with `P<N> · `. The titles are
     read out of each file's own meta block (not guessed), and each
     rewrite is asserted to have hit at least one call site.

WHAT IS HOISTED, AND WHY ONLY THAT
-----------------------------------
`_SHARED_HOISTS` is deliberately short. Two things force an item onto it:

  - `resolveRepo` / `REPO` / `PY`: hoisting is the whole of the "one
    resolve instead of eight" saving (playbook §5.7 — resolveRepo costs a
    real sub-agent dispatch whenever args.repo is absent).
  - the verdict schemas: playbook §5.3 is a hard runtime constraint —
    a `schema:` value must be a top-level const. Nesting them inside
    `runPhase<N>()` would break the script parser.

`WRITE_SCOPE_BLOCK` and `BUDGET_GUARD_BLOCK` follow only because they are
byte-identical across the files that carry them and would otherwise log
the same line eight times. Everything else — the A/B review machine, the
JSON helpers, `checkManifestIntegrity`, every phase-specific const —
stays nested, where it cannot collide and needs no reconciliation.

SIZE
----
Playbook §4: a workflow file over 524288 bytes is rejected by the runtime
outright. Eight bodies inlined verbatim come to ~410 KB (78% of the cap)
with no room for the files to keep growing, so pure-comment lines are
stripped from the inlined bodies (~110 KB). The WHY those comments carry
is not lost: the eight sibling files under `.claude/workflows/` and the
generators under `scripts/workflowgen/` keep every word.
"""
from __future__ import annotations

import re

from scripts.workflow_audit.js_lint import comment_line_numbers

from . import js_blocks as B
from .spec_shared import PHASE_COMPLETE_KEY

PHASES = (1, 2, 3, 4, 5, 6, 7, 8)

# Phases whose Sync box is folded into `advance-phase --push` (Round 23 站3).
#
# Those six end with the SAME shared Sync block (js_blocks.render_sync_verified
# with no extra lines): one dispatch running `git push origin main`, existing
# only because advance-phase committed the handover without publishing it.
# With --push the command publishes its own commit and the box has nothing
# left to do. Six dispatches, and one fewer way to leave a commit stranded.
#
# Phase 3 and Phase 8 are NOT in this list and keep their Sync boxes: phase3's
# is bespoke (retry, then a MANUAL_REQUIRED handover note on second failure)
# and phase8's additionally verifies the release tag reached origin. Folding
# either would drop behaviour, not duplicate it.
_SYNC_FOLDED_INTO_ADVANCE = (1, 2, 4, 5, 6, 7)

# Exact text excised from each phase's generated output and re-emitted once
# at the top of run-all. `required` items must appear exactly once in every
# phase; the optional one is absent from phase1/phase2 (verified, not
# assumed — those two predate the budget guard).
_SHARED_HOISTS: tuple[tuple[str, str, bool], ...] = (
    ("resolveRepo()", B.RESOLVE_REPO_FN_BLOCK[B.RESOLVE_REPO_FN_BLOCK.index("async function"):], True),
    ("let REPO", "let REPO = await resolveRepo()\n", True),
    ("const PY", "const PY = REPO + '/.venv/bin/python'\n", True),
    ("WRITE_SCOPE", B.WRITE_SCOPE_BLOCK, True),
    ("budget guard", B.BUDGET_GUARD_BLOCK, False),
)

# CTX_SCHEMA is declared by two entries in js_blocks._SCHEMA_DEFS: the plain
# one (phases 4/5/7/8) and the WITH_TITLES one (phase3). Both emit `const
# CTX_SCHEMA` and both declare required = ['fr_ids', 'fr_count']; the second
# adds an OPTIONAL `fr_titles` property. The union therefore keeps only the
# WITH_TITLES variant — a superset that every consumer validates against
# identically. (Checked field by field, not inferred from the names.)
_SCHEMA_UNION = (
    "VERDICT_SCHEMA", "RC_SCHEMA", "FR_STEP_SCHEMA", "CTX_SCHEMA_WITH_TITLES", "DELTA_FAST_SCHEMA",
    "PHASE_SCHEMA", "ENV_CHECK_SCHEMA", "GATE_VERIFY_SCHEMA", "FR_LIST_SCHEMA",
    "RECORD_BLOCK_SCHEMA",
)

_HEADER = """\
// run-all — Phases 1 through 8 in a single workflow
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/spec_runall.py, which INLINES the output of
// generate_phase1()..generate_phase8() — the same text shipped as the eight
// .claude/workflows/phase*.js files. Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write
//
// Pure-comment lines are stripped from the eight inlined phase bodies to stay
// clear of the runtime's 512 KB parse limit (playbook §4). Read the sibling
// phase*.js files for the commentary — they are byte-identical otherwise, and
// scripts/workflowgen/js_src/sim_runner.test.mjs pins that equivalence by
// comparing the agent-dispatch sequence of this file against theirs.
//
// Entry point: reads .methodology/state.json's current_phase and runs from
// there to Phase 8. A run that dies mid-way is resumed by launching this same
// workflow again — every phase's own GUARD steps short-circuit work that
// already landed.
"""

# NOTE: no apostrophes. This lands inside a single-quoted JS string literal in
# the meta object, and meta must stay a pure literal (playbook §3), so there is
# nowhere to escape one. The first draft said "state.json's current_phase" and
# broke the whole file at parse time.
_DESCRIPTION = (
    "Run Phases 1-8 end to end starting from the current_phase in state.json — "
    "the eight per-phase workflows inlined, one launch instead of eight"
)

_CURSOR_TITLE = "Phase Cursor"


def _meta_titles(text: str) -> list[str]:
    """Titles declared in a generated file's own meta block."""
    start = text.index("export const meta = {")
    end = text.index("\n}\n", start) + 3
    return re.findall(r"title: '([^']+)'", text[start:end])


def _runall_titles(phase: int, titles: list[str]) -> list[str]:
    """The boxes run-all actually opens for this phase.

    A title only belongs in run-all's meta if the run reaches it — the sim
    testbed asserts exactly that. The six phases whose Sync folds into
    `advance-phase --push` no longer open a Sync box, so declaring one would
    promise a box that never arrives.
    """
    if phase in _SYNC_FOLDED_INTO_ADVANCE:
        return [t for t in titles if t != "Sync"]
    return titles


def _strip_header_and_meta(text: str) -> str:
    """Everything after the file's header comment and meta object."""
    start = text.index("export const meta = {")
    return text[text.index("\n}\n", start) + 3:]


def _drop_comment_lines(text: str) -> str:
    drop = comment_line_numbers(text)
    return "".join(
        line for n, line in enumerate(text.splitlines(keepends=True), start=1)
        if n not in drop
    )


def _phase_body(phase: int) -> str:
    """One phase's generated code, ready to be a function body."""
    # Round 26: the RAW body — the dispatch wrapper is injected once over the
    # assembled composite, not eight times into its parts.
    from .generate_workflows import generate_raw

    text = generate_raw(phase)
    titles = _meta_titles(text)
    body = _strip_header_and_meta(text)

    for label, snippet, required in _SHARED_HOISTS:
        count = body.count(snippet)
        if required and count != 1:
            raise AssertionError(
                f"phase{phase}: expected exactly one {label} block to hoist, "
                f"found {count} — run-all's excision table has drifted from "
                f"the phase generators; fix scripts/workflowgen/spec_runall.py"
            )
        if count:
            body = body.replace(snippet, "", 1)

    body = body.replace(B.render_schemas([]).rstrip("\n"), "", 1)
    for definition in B._SCHEMA_DEFS.values():
        body = body.replace(definition, "", 1)

    if phase in _SYNC_FOLDED_INTO_ADVANCE:
        sync_block = B.render_sync_verified()
        if body.count(sync_block) != 1:
            raise AssertionError(
                f"phase{phase}: expected exactly one shared Sync block to fold "
                f"into advance-phase --push, found {body.count(sync_block)}. If "
                f"this phase grew a bespoke Sync, drop it from "
                f"_SYNC_FOLDED_INTO_ADVANCE instead of loosening this check."
            )
        body = body.replace(sync_block, "", 1)
        advance_cmd = (
            f"harness_cli.py advance-phase --completed {phase} "
            "--project ' + REPO + '"
        )
        if body.count(advance_cmd) != 1:
            raise AssertionError(
                f"phase{phase}: expected exactly one advance-phase invocation "
                f"to add --push to, found {body.count(advance_cmd)}"
            )
        body = body.replace(advance_cmd, advance_cmd + " --push", 1)

    prefix = f"P{phase} · "
    # Assert first, rewrite second. The blanket rewrites below prefix every
    # box name; this loop exists only to catch the case they cannot see —
    # a title declared in meta that no code path ever opens, which would
    # leave run-all's meta promising a box the run never reaches.
    for title in _runall_titles(phase, titles):
        if f"phase('{title}')" not in body and f"phase: '{title}'" not in body:
            raise AssertionError(
                f"phase{phase}: declared title {title!r} has no phase() call "
                f"or phase: option — the meta block and the code have drifted "
                f"apart"
            )
    # phase1/phase2 also group two agent calls under `phase:` labels
    # ('B Review', 'Persist Approval') that meta does not declare. Prefixing
    # every literal, not just the declared ones, keeps the progress view
    # consistent: every box names the phase it belongs to.
    body = re.sub(r"phase: '(?!P\d+ · )([^']+)'", lambda m: f"phase: '{prefix}{m.group(1)}'", body)
    body = re.sub(r"phase\('(?!P\d+ · )([^']+)'\)", lambda m: f"phase('{prefix}{m.group(1)}')", body)
    # Four agent options take their box name from a VARIABLE, not a literal:
    # loadFileViaPython's `phase: phaseName`, checkManifestIntegrity's
    # `phase: phaseLabel`, and the A/B machine's two `phase: cfg.phaseName`.
    # Prefixing at the option site covers whatever the caller passed, so those
    # dispatches land in the same P<N>-prefixed box as everything else instead
    # of opening an eighth unprefixed "Sub-Task 1/4 — SRS.md". Numeric
    # `phase: 7` in the session-limit return objects cannot match this pattern.
    body = re.sub(
        r"phase: ([A-Za-z_$][\w$]*(?:\.[\w$]+)*)",
        lambda m: f"phase: '{prefix}' + {m.group(1)}", body,
    )

    return _drop_comment_lines(body)


def _render_driver(all_titles: list[str]) -> str:
    return (
        "const PHASE_RUNNERS = {\n"
        + "".join(f"  {p}: runPhase{p},\n" for p in PHASES)
        + "}\n"
        "\n"
        f"phase('{_CURSOR_TITLE}')\n"
        "log('run-all: reading .methodology/state.json to find the starting phase')\n"
        "// Fail CLOSED. Defaulting to Phase 1 when the read fails would re-run the\n"
        "// whole requirements phase on an established project — far worse than\n"
        "// stopping and asking. state.json is the same authority advance-phase\n"
        "// writes and every phase's Advance box verifies.\n"
        "const cursorCmd = PY + ' -c \"import json; print(json.dumps({\\'current_phase\\': int(json.load(open(\\'' + REPO + '/.methodology/state.json\\')).get(\\'current_phase\\') or 0)}))\"'\n"
        "let cursor\n"
        "try {\n"
        "  cursor = await agent(\n"
        "    'Run EXACTLY this command via the Bash tool (stdout is a single JSON line):\\n`' + cursorCmd + '`\\n'\n"
        "    + 'Then report via the StructuredOutput tool: current_phase = the exact integer from that JSON. Do NOT guess a value if the command fails — report the failure.',\n"
        f"    {{ label: 'phase-cursor', phase: '{_CURSOR_TITLE}', agentType: 'general-purpose', schema: PHASE_SCHEMA }},\n"
        "  )\n"
        "} catch (e) {\n"
        "  const cursorErr = 'run-all: phase-cursor dispatch threw: ' + (e && e.message ? e.message : String(e)).slice(0, 200)\n"
        "  await recordBlock(0, 'phase-cursor', cursorErr)\n"
        "  return halt('phase-cursor', { error: cursorErr, "
        "note: 'Transient API error reading state.json cursor — nothing changed on disk, relaunch run-all.' })\n"
        "}\n"
        "if (!(cursor && Number.isInteger(cursor.current_phase))) {\n"
        "  await recordBlock(0, 'phase-cursor', 'run-all: could not read current_phase from .methodology/state.json')\n"
        "  return halt('phase-cursor', { error: 'run-all: could not read current_phase from .methodology/state.json', "
        "note: 'Refusing to guess a starting phase. Check the file, then relaunch.' })\n"
        "}\n"
        "const startPhase = cursor.current_phase\n"
        "if (startPhase < 1 || startPhase > 8) {\n"
        "  return { workflow: 'run-all', start_phase: startPhase, phases_run: [], "
        "note: 'state.json current_phase is outside 1-8 — nothing for run-all to do "
        "(Phase 9 maintenance is ticket-driven, not a phase workflow).' }\n"
        "}\n"
        "log('run-all: starting at Phase ' + startPhase + ' — phases before it already advanced')\n"
        "\n"
        "const phasesRun = []\n"
        "for (let n = startPhase; n <= 8; n++) {\n"
        "  log('run-all: ===== Phase ' + n + ' =====')\n"
        "  let outcome\n"
        "  try {\n"
        "    outcome = await PHASE_RUNNERS[n]()\n"
        "  } catch (e) {\n"
        "    const crashMsg = 'run-all crashed in Phase ' + n + ': ' + (e && e.message ? e.message : String(e)).slice(0, 300)\n"
        "    await recordBlock(n, 'workflow-crash', crashMsg)\n"
        "    return halt('workflow-crash', { error: crashMsg, "
        "phase: n, phases_run: phasesRun, note: 'An agent dispatch inside this phase threw instead of returning a result. "
        "Relaunch run-all — it resumes from state.json (this phase restarts from its current sub-task, per existing resumability).' })\n"
        "  }\n"
        "  if (outcome && outcome.session_limit_blocked) {\n"
        # Round 79 站3: pre-classify INFRA at the call site.
        "    await recordBlock(n, 'session-limit', String(outcome.message || 'agent hit a session/rate limit'), 'infra')\n"
        "    return { session_limit_blocked: true, phase: n, phases_run: phasesRun, "
        "detail: outcome, message: 'Agent hit a session/rate limit. Relaunch run-all after the quota resets — "
        "it resumes from state.json and every completed phase short-circuits.' }\n"
        "  }\n"
        "  if (outcome && outcome.error) {\n"
        "    await recordBlock(n, String(outcome.halt_step || 'phase-error'), String(outcome.error))\n"
        "    return halt(String(outcome.halt_step || 'phase-error'), { error: 'run-all stopped in Phase ' + n + ': ' + outcome.error, "
        "phase: n, phases_run: phasesRun, detail: outcome })\n"
        "  }\n"
        "  // Round 28 — fail CLOSED, like the cursor read above. The two branches\n"
        "  // above name the outcomes this driver recognises; this one covers every\n"
        "  // outcome it does not. runPhase3 returns `harness_bug_detected` and\n"
        "  // `dispatch_structurally_broken` for conditions no later phase can\n"
        "  // recover from, and neither carries an `error` key — so before this,\n"
        "  // a run in which harness itself crashed on FR-01 walked on through P4-P8\n"
        "  // and reported `phases_run: [3,4,5,6,7,8]` with no error at all.\n"
        f"  if (!outcome || outcome.{PHASE_COMPLETE_KEY} !== true) {{\n"
        "    await recordBlock(n, 'phase-incomplete', String((outcome && (outcome.message || outcome.error)) || 'no message'))\n"
        "    return halt(String((outcome && outcome.halt_step) || 'phase-incomplete'), { error: 'run-all stopped in Phase ' + n + ': the phase returned without "
        "reporting completion — ' + String((outcome && (outcome.message || outcome.error)) || "
        "'no message'), "
        "phase: n, phases_run: phasesRun, detail: outcome, "
        f"note: 'A phase sets {PHASE_COMPLETE_KEY} only on its single success exit. Anything else "
        "— a terminal abort such as a harness crash or a broken dispatch environment, or a shape "
        "this driver does not recognise — stops the run rather than advancing on an unfinished "
        "phase.' })\n"
        "  }\n"
        "  phasesRun.push(n)\n"
        "}\n"
        "\n"
        "log('run-all complete — Phase ' + startPhase + ' through Phase 8.')\n"
        "return { workflow: 'run-all', start_phase: startPhase, phases_run: phasesRun, "
        f"phase_boxes: {len(all_titles)}, "
        "notes: 'All phases from the state.json cursor through Phase 8 completed.' }\n"
    )


def generate_runall() -> str:
    bodies: list[str] = []
    all_titles: list[str] = [_CURSOR_TITLE]
    for phase in PHASES:
        from .generate_workflows import generate

        all_titles.extend(
            f"P{phase} · {t}"
            for t in _runall_titles(phase, _meta_titles(generate(phase)))
        )
        bodies.append(
            f"async function runPhase{phase}() {{\n{_phase_body(phase)}}}\n"
        )

    meta_lines = ["export const meta = {", "  name: 'run-all',",
                  f"  description: '{_DESCRIPTION}',", "  phases: ["]
    meta_lines.extend(f"    {{ title: '{t}' }}," for t in all_titles)
    meta_lines.extend(["  ],", "}"])

    parts = [
        _HEADER,
        "",
        "\n".join(meta_lines) + "\n",
        "",
        _SHARED_HOISTS[0][1],
        "let REPO = await resolveRepo()\n"
        "const PY = REPO + '/.venv/bin/python'\n"
        "log('REPO = ' + REPO + ' | PY = ' + PY)\n",
        B.BUDGET_GUARD_BLOCK,
        "",
        B.WRITE_SCOPE_BLOCK,
        "",
        B.RECORD_BLOCK_FN_BLOCK,
        "",
        B.render_schemas(list(_SCHEMA_UNION)),
        "",
        "\n".join(bodies),
        _render_driver(all_titles),
    ]
    # Injected HERE, not by generate_composite (Round 48 站4): each composite
    # generator now returns finished text, because harness-repair needs the
    # top-level boundary applied AFTER the injection and a shared "wrap
    # everything" step could only do one order. run-all still injects exactly
    # once, over the assembled file — never per inlined body, which would emit
    # eight `const __dispatchLog` declarations.
    from .generate_workflows import _inject_dispatch_wrapper, _inject_halt_helper

    return _inject_halt_helper(_inject_dispatch_wrapper("\n".join(parts)))
