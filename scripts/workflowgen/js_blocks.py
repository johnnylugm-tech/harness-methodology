"""Shared JS block renderers for the workflow generator.

Mirrors `scripts/plangen/blocks.py`'s shape (functions return strings/line
lists; assembly logic lives in the phase-spec + facade layer), applied to
`.claude/workflows/*.js` instead of `.methodology/phaseN_plan.md`.

Runtime constraint this whole package exists because of (see
docs/WORKFLOW_PLAYBOOK.md once station5 adopts it): a workflow script may
not `import`/`require`/touch `fs`/`process` at runtime, so 8 self-contained
`.js` files cannot literally `import` a shared module the way 8 Python
files can. The de-duplication instead happens at GENERATION time — these
functions render the *identical* text into each phase's output, so the
duplication that exists in the final `.js` files is a build artifact, not
8 hand-maintained copies. Fixing a bug here and re-running
`generate_workflows.py --write` is the same "fix once" property `import`
would have given, just resolved before the runtime sees it instead of at
runtime.
"""
from __future__ import annotations

import re
from pathlib import Path

_JS_SRC_DIR = Path(__file__).resolve().parent / "js_src"
_EXPORT_RE = re.compile(r"^export\s+(?=function\b)", re.MULTILINE)


def _crg_standalone_specs() -> str:
    """`code-review-graph==X igraph==Y`, with both versions read from the SSOT.

    The gate-verify dispatch cannot assume an earlier step in THIS session
    installed CRG, so it installs it inline — which means the workflow files
    carry a pin. Rendering it keeps that pin equal to the one bootstrap.py
    states, instead of being a fourth hand-typed copy.
    """
    from harness.toolchains import bootstrap

    return " ".join(bootstrap.pinned_spec(p) for p in ("code-review-graph", "igraph"))

# One statement of the crash-banner shape, shared by every site that routes on
# it (Round 13 站2: HARNESS_BUG and INFRA must not be sent to CODE-FIX).
HARNESS_BUG_RE_JS = r"/\[HARNESS-BUG\]/"


def render_rule_prose(rule_id: str) -> str:
    """Canonical @rule prose from harness/prompts/rules/<id>.md, JS-escaped
    for embedding in a single-quoted JS string literal.

    Reuses plangen/blocks.py's own SSOT loader (`_load_rule`) rather than a
    second copy — tests/test_prompt_rules.py's no-fork check scans EVERY .py
    file, not just plangen's own module, so a hardcoded duplicate of this
    prose inside workflowgen (phase1's original JS embedded these rules
    verbatim, its only option since workflow JS can't `import` a .md at
    runtime) would trip the same guard the moment it's copied into a scanned
    .py string constant. Loading at generation time is exactly this whole
    package's reason to exist (see module docstring) and simultaneously
    fixes a second, independent bug: phase1's original inline text for
    R-CANONICAL-INTERP-001 pre-dates the rule's extraction and still says
    "python -m taskq" (a past E2E target project's name) where the
    canonical .md was already genericized to "python -m <pkg>".
    """
    from scripts.plangen.blocks import _load_rule

    return _load_rule(rule_id).replace("'", "\\'")


def render_json_utils() -> str:
    """`js_src/json_utils.mjs` with `export` stripped, for inlining into a
    self-contained phase workflow file (the runtime forbids `export`/
    `import`). The SAME source file is exercised directly (with `export`
    intact) by `node --test scripts/workflowgen/js_src/` — one file, two
    consumers, so a fix or a new test case never has to be duplicated into
    a second copy the way balancedJsonAt/extractLastJson/parseAgentJson
    were duplicated across phase1/phase2/phase6 before this module existed.
    """
    src = (_JS_SRC_DIR / "json_utils.mjs").read_text(encoding="utf-8")
    # Drop the leading `// ...` module comment block (js_src-only context;
    # the generated file gets its own header) — keep everything from the
    # first `export function` onward.
    first_export = _EXPORT_RE.search(src)
    body = src[first_export.start():] if first_export else src
    return _EXPORT_RE.sub("", body)


RESOLVE_REPO_FN_BLOCK = """\
// ---- args / REPO / PY ----
// REPO precedence: args.repo override wins, then DEFAULT_REPO canonical path.
// process.env.HARNESS_REPO cannot be read here — playbook §4 forbids process.*
// in workflow JS. Caller scripts (run-e2e.mjs / harness-e2e.js /
// phase1-workflow.mjs) read HARNESS_REPO and inject it via args.repo.
async function resolveRepo() {
  if (typeof args === 'string') { try { args = JSON.parse(args) } catch {} }
  let argRepo = ''
  if (args && typeof args === 'object' && typeof args.repo === 'string' && args.repo.length > 0) argRepo = args.repo
  if (argRepo) {
    if (!argRepo.startsWith('/')) {
      throw new Error('[workflow] args.repo must be an absolute path; got "' + argRepo + '"')
    }
    log('  REPO: from args.repo override = ' + argRepo)
    return argRepo
  }
  const r = await agent(
    'You are the REPO RESOLVER. Find the project root by walking up from your current CWD until a directory contains BOTH `harness_cli.py` AND `.methodology/` AND is NOT a git submodule working tree.\\n'
    + 'A git submodule working tree is detected by `[ -f .git ] && head -1 .git 2>/dev/null | grep -q "^gitdir: "` (the top-level `.git` is a FILE whose first line starts with `gitdir:`, pointing to `<parent>/.git/modules/<name>`). This is critical when the harness framework is checked out as a git submodule — the harness/ dir itself contains harness_cli.py AND .methodology/, so naive walk-up would stop there instead of the real project root.\\n'
    + 'Run EXACTLY this command via Bash (single line, copy-paste verbatim):\\n'
    + 'cd "$(pwd)"; while [ "$(pwd)" != "/" ] && ! { [ -f harness_cli.py ] && [ -d .methodology ] && ! { [ -f .git ] && head -1 .git 2>/dev/null | grep -q "^gitdir: "; }; }; do cd ..; done; '
    + 'if [ -f harness_cli.py ] && [ -d .methodology ] && ! { [ -f .git ] && head -1 .git 2>/dev/null | grep -q "^gitdir: "; }; then echo "REPO=$(pwd)"; else echo "REPO_NOT_FOUND cwd=$(pwd)"; fi\\n'
    + 'Report the literal stdout as your final message (no commentary, no transformation).',
    { label: 'resolve-repo', agentType: 'general-purpose' }
  )
  const text = String(r ?? '').trim()
  const match = text.match(/REPO=(\\/[A-Za-z0-9_.\\/-]+)/)
  if (match && match[1].startsWith('/')) {
    log('  REPO: auto-detected via walk-up = ' + match[1])
    return match[1]
  }
  throw new Error('[workflow] REPO not auto-detected (resolver returned: "' + text.slice(0, 200) + '"). Pass args.repo = absolute path or run from inside the project repo.')
}
"""

RESOLVE_REPO_BLOCK = RESOLVE_REPO_FN_BLOCK + (
    "let REPO = await resolveRepo()\n"
    "const PY = REPO + '/.venv/bin/python'\n"
)

REPO_LOG_LINE = "log('REPO = ' + REPO + ' | PY = ' + PY)\n"

BUDGET_GUARD_BLOCK = """\
// v15: budget guard (Bug #3 — port from phase2-architecture)
if (typeof budget !== 'undefined' && budget.remaining && budget.remaining() < 200000) {
  log('WARNING: budget low (' + Math.round((budget.remaining() || 0) / 1000) + 'k remaining) — workflow may not complete')
}
"""

WRITE_SCOPE_BLOCK = """\
// ---- J: WRITE SCOPE convention for LLM agent debug artifacts ----
// All agent-generated debug scripts, coverage reports, and exploration
// artifacts MUST go under ${REPO}/.sessi-work/tmp/<random_id>/. This
// directory is gitignored and gets cleaned automatically. Direct writes
// to 03-development/, scripts/, .claude/, harness/, .methodology/, or
// .github/ require explicit user approval per agent scope rules.
//
// Why this matters: debug_* scripts (fr04_cov.py, show_cov.py, etc.)
// otherwise pollute the source tree and require manual cleanup before
// commit. Sandboxing them keeps the working tree clean by default.
//
// Self-audit (add to agent prompt end): "List every Write/Edit file
// path used in this task; confirm all paths start with .sessi-work/tmp/."
const WRITE_SCOPE_TMP = REPO + '/.sessi-work/tmp'
log('WRITE SCOPE: debug artifacts → ' + WRITE_SCOPE_TMP)
"""

RECORD_BLOCK_FN_BLOCK = """\
// ---- Round 48 站2: where the run stopped, written down ----
// Every terminal exit below funnels through this. Before it, a halt existed
// only as this script's return value: run-report reads sessions_spawn.log,
// degradations.jsonl and the gate result files, and a workflow halt is in
// none of them. The one event nobody recorded was where the pipeline stopped.
//
// It SPENDS a dispatch, unlike __dispatchFlushPreamble's ride-along. That is
// unavoidable: this sandbox has no filesystem and no shell, and a halt is
// terminal, so there is no next dispatch to ride on. One dispatch, once per
// aborted run.
//
// Classification is NOT done here. harness_cli.py record-block calls
// core/fault_owner.py and prints the owner; a copy of that table inside a
// workflow string literal would be one fact in two places, on a surface no
// unit test can reach.
async function recordBlock(phaseNo, step, message) {
  const clean = (s) => String(s == null ? '' : s).replace(/'/g, '').replace(/\\s+/g, ' ').slice(0, 800)
  const cmd = PY + ' ' + REPO + '/harness_cli.py record-block --project ' + REPO
    + ' --phase ' + phaseNo + " --step '" + clean(step) + "' --message '" + clean(message) + "'"
  try {
    await agent(
      'Run EXACTLY this command via the Bash tool, then report its stdout verbatim as your final message. Do nothing else.\\n`'
      + cmd + '`\\n'
      + 'It records where this run stopped and who owns the failure. It always exits 0; if it does not, report that verbatim rather than retrying.',
      { label: 'record-block', phase: 'Phase Cursor', agentType: 'general-purpose' },
    )
  } catch (e) {
    log('record-block dispatch failed (the halt below is still the real result): ' + String((e && e.message) || e).slice(0, 160))
  }
}
"""

HALT_FN_BLOCK = """\
// ---- Round 50 站3: a halt carries the step it happened at ----
// Round 48 站2 gave run-all six recordBlock sites on the phase loop's
// boundary. Measured across the shipped workflows: the eight phase files
// return `{ error: ... }` from 55 distinct top-level sites, and all 55 arrive
// at one of those six under the single step name `phase-error`. A full
// P1-P8 run produced one workflow_blocks.jsonl row, and that row names the
// phase and nothing about which of its halts fired.
//
// The event was never lost; its coordinate was. This helper is where the
// coordinate is attached, at the site that knows it — the same rule Round 24
// applied to block_reason and Round 48 站1 wrote down for fault ownership:
// the answer is written where it is known, not reconstructed later from
// prose.
//
// It costs NOTHING at runtime: no dispatch, no await. The recording still
// happens once, at the driver's boundary, which now reads halt_step instead
// of hardcoding a name. The shape passed through is each site's own — error,
// reason, detail, raw, peerVerdict — so every existing caller of these
// workflows sees what it saw before, plus one field.
//
// No phase argument: several halt sites live in blocks shared across phases
// (js_blocks.LOAD_FRS_BLOCK, the post-advance push), where the phase is not
// something the site knows. The driver's loop already has it.
function halt(step, shape) {
  return Object.assign({ halt_step: step }, shape)
}
"""

HUNT_MODEL_BLOCK = """\
// Bug hunt should use a DIFFERENT model from the developer (minimise same-source bias).
const HUNT_MODEL = (args && typeof args === 'object' && typeof args.huntModel === 'string') ? args.huntModel : 'claude-opus-4-8'
log('HUNT_MODEL = ' + HUNT_MODEL)
"""

_SCHEMA_DEFS: dict[str, str] = {
    "VERDICT_SCHEMA": """\
const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    pass: { type: 'boolean', description: 'true only if the command output proves PASS' },
    reason: { type: 'string', description: 'verbatim command output tail (or failure reason)' },
  },
  required: ['pass', 'reason'],
}""",
    "RC_SCHEMA": """\
const RC_SCHEMA = {
  type: 'object',
  properties: { rc: { type: 'integer', description: 'exact numeric exit code of the command' } },
  required: ['rc'],
}""",
    "CTX_SCHEMA": """\
const CTX_SCHEMA = {
  type: 'object',
  properties: {
    fr_ids: { type: 'array', items: { type: 'string' } },
    fr_count: { type: 'integer' },
  },
  required: ['fr_ids', 'fr_count'],
}""",
    "CTX_SCHEMA_WITH_TITLES": """\
const CTX_SCHEMA = {
  type: 'object',
  properties: {
    fr_ids: { type: 'array', items: { type: 'string' } },
    fr_count: { type: 'integer' },
    fr_titles: { type: 'object', additionalProperties: { type: 'string' } },
  },
  required: ['fr_ids', 'fr_count'],
}""",
    "DELTA_FAST_SCHEMA": """\
const DELTA_FAST_SCHEMA = {
  type: 'object',
  properties: {
    pass_fr_ids: { type: 'array', items: { type: 'string' }, description: 'FRs whose manifest gate1 quality_complete printed True after GATE1-DELTA' },
    fail_fr_ids: { type: 'array', items: { type: 'string' }, description: 'FRs that did not print True (False/None/timeout/error)' },
  },
  required: ['pass_fr_ids', 'fail_fr_ids'],
}""",
    "PHASE_SCHEMA": """\
const PHASE_SCHEMA = {
  type: 'object',
  properties: { current_phase: { type: 'integer', description: 'current_phase value read from state.json' } },
  required: ['current_phase'],
}""",
    "ENV_CHECK_SCHEMA": """\
const ENV_CHECK_SCHEMA = {
  type: 'object',
  properties: {
    rc: { type: 'integer', description: 'exact numeric exit code parsed from the final RC= line in the envcheck log' },
    ready: { type: 'boolean', description: 'env_check_result.json ready flag cross-check (Bug #127 anti-fabrication)' },
  },
  required: ['rc', 'ready'],
}""",
    "GATE_VERIFY_SCHEMA": """\
const GATE_VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    verify_rc: { type: 'integer', description: 'exit code of `verify-gate` — 0 means all three of the gate\\'s checks passed AND the PASS verdict was recorded with the digest of the tree it was measured on' },
    detail: { type: 'string' },
  },
  required: ['verify_rc'],
}""",
    "FR_LIST_SCHEMA": """\
const FR_LIST_SCHEMA = {
  type: 'object',
  properties: { fr_ids_done: { type: 'array', items: { type: 'string' } } },
  required: ['fr_ids_done'],
}""",
}


def render_schemas(names: list[str]) -> str:
    """Selectable subset of the flat gate-verdict schemas (playbook §5.2/§5.3
    — a heavy orchestrator's prose is never parsed for PASS/FAIL; a separate
    flat-schema proxy agent reads the harness's own artifact instead)."""
    header = (
        "// ---- Gate verdict schemas (flat, top-level consts — playbook §5.2/§5.3) ----\n"
        "// Verdict authority rule: heavy orchestrator agents keep prose narrative;\n"
        "// their PASS/FAIL is NEVER parsed from that prose. A separate bash-proxy\n"
        "// agent reads the harness's own artifact (manifest quality_complete,\n"
        "// state.json/git log, CLI exit code) and reports through the schema.\n"
    )
    body = "\n".join(_SCHEMA_DEFS[n] for n in names)
    return header + body + "\n"


def render_phase_header(title: str) -> str:
    return (
        "\n"
        "// " + "═" * 74 + "\n"
        f"// Phase: {title}\n"
        "// " + "═" * 74 + "\n"
        f"\nphase('{title}')\n"
    )


def render_entry_preflight(
    *,
    phase: int,
    gate_num: int,
    gate_owner_phase: int,
    prev_phase: int,
    extra_note: str = "",
) -> str:
    """Entry & Preflight: verify the previous gate landed, run-phase preflight
    (with the standard reliability/config-liveness/attestation fix menu),
    validate-handoff from the previous phase, confirm CI wiring."""
    return (
        render_phase_header("Entry & Preflight")
        + f"log('ENTRY-CHECK Gate{gate_num} + run-phase {phase} (reliability/config/attestation fixes) + handoff + CI')\n"
        + "const preflightReport = await agent(\n"
        + f"  'YOU ARE THE PHASE-{phase} PREFLIGHT ORCHESTRATOR. Run bash in order; report.\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "  + 'Steps:\\n'\n"
        + f"  + '1. ENTRY-CHECK: run EXACTLY this bash command to verify Gate {gate_num} status (do NOT rely on reading the file yourself — use the command output):\\n`' + PY + ' -c \"import json; m=json.load(open(\\'' + REPO + '/.methodology/quality_manifest.json\\')); g{gate_num}=(m.get(\\'gate_results\\',{{}}) or {{}}).get(\\'gate{gate_num}\\',{{}}) or {{}}; print(\\'GATE_VERIFIED\\' if isinstance(g{gate_num},dict) and g{gate_num}.get(\\'quality_complete\\') is True else \\'GATE_MISSING\\')\"`\\nIf GATE_MISSING → FAIL (return to Phase {gate_owner_phase}).\\n'\n"
        + f"  + '2. PREFLIGHT: `' + PY + ' ' + REPO + '/harness_cli.py run-phase --phase {phase} --project ' + REPO + '`. FAIL → fix, re-run (max 3). Also fix if reported: reliability lint (subprocess timeout / mkstemp / TOCTOU / sleep-in-async), config liveness (env keys absent from .env.example), attestation missing/mismatch (build-trace-attestation --write + commit; re-run until \"Attestation: clean\"), property_spec (an FR declares a Properties invariant in TEST_SPEC.md but no test executes it — write a hypothesis @given (Python) / fast-check (JS/TS) test exercising the declared invariant for that FR, then re-run).\\n'\n"
        + f"  + '3. HANDOFF: `' + PY + ' ' + REPO + '/harness_cli.py validate-handoff --from-phase {prev_phase} --project ' + REPO + '`. Must exit 0.\\n'\n"
        + f"  + '4. PREFLIGHT-CI: confirm `' + REPO + '/.github/workflows/harness_quality_gate.yml` (CI workflow) + `' + REPO + '/.git/hooks/prepare-commit-msg` (git hook) both exist; confirm state.json current_phase={phase}. If stale: `init-project --phase {phase} --project ' + REPO + ' --overwrite`.\\n\\n'\n"
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if ALL 4 steps succeeded; reason = one-line summary (on FAIL: which step + verbatim error tail).\\n\\n'\n"
        + f"  + 'SCOPE RULES:\\n{extra_note}- DO NOT modify harness/.\\n- ONLY preflight commands + fixes.',\n"
        + "  { label: 'preflight', phase: 'Entry & Preflight', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(preflightReport && preflightReport.pass === true)) {\n"
        + f"  return halt('preflight', {{ error: 'Phase {phase} preflight did not PASS', reason: preflightReport ? String(preflightReport.reason ?? '').slice(-600) : 'agent returned null (skipped or terminal API error)' }})\n"
        + "}\n"
    )


def render_env_check(phase: int) -> str:
    envcheck_log = f"/tmp/envcheck_phase{phase}.log"
    return (
        render_phase_header("Env Check")
        + "log('run-env-check + finalize-env-check (Bug #127 root-cause + bash-timeout-aware background poll)')\n"
        + "// Bug #127 root-cause fix (2026-06-27): `cmd_run_env_check` now returns\n"
        + "// exit 0 when ready=true and 1 when ready=false (previously always 0).\n"
        + "// Workflows check `$?` directly with no LLM orchestrator agent in the loop.\n"
        + "// 2026-07-02 paraphrase incident (phase3): the agent rewrote ENV_CHECK_RC=0\n"
        + "// as \"RC=0\" and the regex gate false-negatived a READY environment. Schema\n"
        + "// transport is paraphrase-proof.\n"
        + "// Round 11 station2b (plan ENV-CHECK marker): run-env-check's exit code\n"
        + "// (Bug #127) only reflects the agent's self-reported `ready` boolean, not\n"
        + "// result-schema completeness — a `{\"ready\": true}` response missing\n"
        + "// checked_at / env_vars.required / cli_tools.required /\n"
        + "// infra_services.required would pass run-env-check alone but fail\n"
        + "// finalize-env-check's schema check (HarnessBridge.finalize_env_check,\n"
        + "// cli/gate_cmds.py) — a real anti-fabrication gap, not redundant with\n"
        + "// Bug #127's fix. Chain both: `&&` runs finalize only after run-env-check\n"
        + "// succeeds; the trailing `; echo RC=$?` captures whichever of the two is\n"
        + "// authoritative (run-env-check's own failure code if it failed first,\n"
        + "// otherwise finalize-env-check's).\n"
        + "// Round 23 (2026-07-26, observed on a downstream project's phase5 workflow run):\n"
        + "// the chained command legitimately runs past the Claude Code Bash tool's\n"
        + "// 10-min default timeout — run-env-check spawns an LLM sub-agent with\n"
        + "// STALL_TIMEOUT=900s (core/harness_config.py::STALL_TIMEOUTS). The Bash\n"
        + "// tool's response to a timeout hit is \"moved to the background\" + return\n"
        + "// rc=124 to the caller, which the sub-agent then mis-reports as the\n"
        + "// run-env-check exit code (it isn't — the actual sub-process is still\n"
        + "// running). Symptom: ~10 min elapsed, rc=124, no env_check_result.json\n"
        + "// cross-check. Fix: launch the chain via Bash with run_in_background:true\n"
        + "// and a foreground `kill -0 PID` poll loop (same idiom as GATE1-DELTA\n"
        + "// background dispatch in phase3-8). The Bash tool returns immediately\n"
        + "// with a task_id; the agent then polls via `kill -0` / log tail and\n"
        + "// reports the FINAL `RC=` line from the chained command's own stdout\n"
        + "// (which IS the run-env-check/finalize-env-check exit code — the\n"
        + "// `; echo \"RC=$?\"` appended at the end of the chain).\n"
        + f"const envCheckLog = '{envcheck_log}'\n"
        + f"const envCheckChain = PY + ' ' + REPO + '/harness_cli.py run-env-check --phase {phase} --project ' + REPO + ' && ' + PY + ' ' + REPO + '/harness_cli.py finalize-env-check --phase {phase} --project ' + REPO + '; echo \"RC=$?\"'\n"
        + "const envReport = await agent(\n"
        + "  'YOU ARE THE PHASE-" + str(phase) + " ENV-CHECK ORCHESTRATOR (Bash-timeout-aware, background poll).\\n'\n"
        + "  + 'REPO: ' + REPO + '\\n'\n"
        + "  + 'PYTHON: ' + PY + '\\n'\n"
        + f"  + 'LOG PATH: {envcheck_log}\\n\\n'\n"
        + "  + 'run-env-check spawns a full LLM sub-agent (max-turns 70) with STALL_TIMEOUT=900s in core/harness_config.py::STALL_TIMEOUTS. A bare synchronous Bash invocation gets auto-moved to background by the Bash tool at its 10-min default timeout and the Bash call returns rc=124 immediately while the actual sub-process keeps running — the rc=124 is NOT the run-env-check exit code. Launch the chain with run_in_background:true so it runs to completion; then poll.\\n\\n'\n"
        + "  + '1. Launch (Bash with `run_in_background: true`, `timeout: 1500000` (25 min) — covers 900s stall + 600s finalize buffer):\\n'\n"
        + "  + '   command: `nohup bash -c \\'' + envCheckChain + '\\' > ' + envCheckLog + ' 2>&1 & echo $!`\\n'\n"
        + "  + '   The Bash tool returns immediately with a task_id AND a shell PID printed in stdout (the `echo $!`). Capture the PID.\\n\\n'\n"
        + "  + '2. Poll loop — BACKOFF intervals, in seconds: 5, 10, 20, 30, then 60 for every\\n'\n"
        + "  + '   further iteration. Cap 22 polls (5+10+20+30 + 18x60 ≈ 19 min — still covers\\n'\n"
        + "  + '   the 900s stall plus a finalize buffer).\\n'\n"
        + "  + '   Round 22 站4: the first interval used to be a flat 60s. Since Round 20\\n'\n"
        + "  + '   run-env-check returns in about a second whenever env_contract.json is\\n'\n"
        + "  + '   current (source docs unchanged -> deterministic verification, no sub-agent,\\n'\n"
        + "  + '   see cli/gate_cmds.py), so a fixed first sleep spent a full minute per phase\\n'\n"
        + "  + '   waiting on a command that had already finished. Backoff keeps the long tail\\n'\n"
        + "  + '   cheap while making the common case fast.\\n'\n"
        + "  + '   Each iteration Bash call (`run_in_background: false`, `timeout: 90000`):\\n'\n"
        + "  + '   `sleep <interval> && kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`\\n'\n"
        + "  + '   When DONE → break out of the loop.\\n'\n"
        + "  + '   If still RUNNING past 22 polls (~19 min) → report \"ENV_CHECK: TIMEOUT\" via StructuredOutput and stop (do NOT kill the PID — it is still legitimately running; resume by re-running this same step).\\n\\n'\n"
        + "  + '3. Authoritative read: `tail -100 ' + envCheckLog + '`; parse the LAST line matching `RC=<integer>`. That integer is the run-env-check/finalize-env-check chain exit code (NOT the Bash tool rc).\\n\\n'\n"
        + "  + '4. Cross-check (Bug #127 anti-fabrication): `cat ' + REPO + '/.sessi-work/env_check_result.json` MUST show `\\\"ready\\\": true`. If file missing or ready=false → ready=false in the StructuredOutput regardless of RC (the LLM may have self-reported ready=true while the result JSON says otherwise).\\n\\n'\n"
        + "  + 'Report via the StructuredOutput tool: { rc: <int from final RC= line>, ready: <bool from env_check_result.json> }.\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n'\n"
        + "  + '- ONLY run-env-check + finalize-env-check + read their log + result artifacts.\\n'\n"
        + "  + '- DO NOT modify harness/ (HR-17).',\n"
        + "  { label: 'env-check', phase: 'Env Check', agentType: 'general-purpose', schema: ENV_CHECK_SCHEMA },\n"
        + ")\n"
        + "if (!(envReport && envReport.rc === 0 && envReport.ready === true)) {\n"
        + "  const _envCheckResult = `${REPO}/.sessi-work/env_check_result.json`\n"
        + f"  return halt('env-check', {{ error: 'Phase {phase} env-check did not PASS', rc: envReport ? envReport.rc : null, ready: envReport ? envReport.ready : null, note: envReport ? ('run-env-check/finalize-env-check rc=' + envReport.rc + ' ready=' + envReport.ready + ' — read ' + _envCheckResult) : 'agent returned null (skipped or terminal API error)' }})\n"
        + "}\n"
    )


def render_manifest_integrity_fn(phase: int) -> str:
    """The checkManifestIntegrity() helper definition — no phase box, no call.

    Round 22 站2: this used to also render an entry-point "Manifest Integrity"
    phase box that ran the check immediately. Two of the three call sites it
    served are gone:

      - The entry call was redundant. Entry & Preflight runs `run-phase
        --phase N` immediately before it, and PREFLIGHT_CHECKS[0] in
        core/phase_hooks.py IS preflight_manifest_integrity — the exact method
        `check-manifest-integrity` wraps (cli/check_cmds.py).
      - The Advance-loop call moved into `advance-phase` itself
        (cli/phase_cmds.py::_advance_prechecks, exit 27), where it also
        protects every non-workflow caller: a human running advance-phase by
        hand, a resumed session, CI.

    Only two call sites still need this helper, and both are checks the CLI
    does not perform for them: phase3's Gate-2 round loop (a fix attempt
    mid-loop can reintroduce corruption before finalize-gate commits) and
    phase8's Final Push (push-milestone, not advance-phase).
    """
    return (
        "// (ported from phase3, 155ec07 + 286ccca)\n"
        "// 2026-07-02 incident class: a sub-agent action (bare pytest → harness test\n"
        "// CWD leak) can corrupt quality_manifest.json MID-RUN, not just before entry.\n"
        "// Detect the three known corruption patterns (fr_ids truncated, traceability\n"
        "// cleared, gate1 wiped) before anything commits .methodology/ wholesale.\n"
        "// T1-A (8-phase audit remediation): the previous inline Python one-liner\n"
        "// had the truncation-comparison direction inverted (`fr_trace >= fr_ids`\n"
        "// instead of the harness's actual `fr_ids >= fr_trace`) plus an unfounded\n"
        "// `fr_ids >= 2` floor. `check-manifest-integrity` wraps the harness's own\n"
        "// (correct, tested) PhaseHooks.preflight_manifest_integrity() instead.\n"
        f"const integrityCmd = PY + ' ' + REPO + '/harness_cli.py check-manifest-integrity --project ' + REPO + ' --phase {phase}'\n"
        "async function checkManifestIntegrity(phaseLabel, agentLabel) {\n"
        "  const verdict = await agent(\n"
        "    'Run EXACTLY this command via the Bash tool:\\n`' + integrityCmd + '; echo RC=$?`\\n'\n"
        "    + 'Then report via the StructuredOutput tool: pass = true ONLY if the output ends with `RC=0`; reason = the JSON the command printed (verbatim, excluding the RC= line).',\n"
        "    { label: agentLabel, phase: phaseLabel, agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        "  )\n"
        "  const ok = !!(verdict && verdict.pass === true)\n"
        "  const raw = verdict ? String(verdict.reason ?? '').trim() : 'agent returned null'\n"
        "  if (!ok) log('  manifest integrity FAIL [' + agentLabel + ']: ' + raw)\n"
        "  return { ok, raw }\n"
        "}\n"
    )


def render_load_frs(phase: int, *, include_fr_titles: bool = False) -> str:
    fr_title_block = (
        "const frTitle = {}\n"
        "if (Array.isArray(ctx.fr_details)) for (const f of ctx.fr_details) frTitle[f.id || f.fr_id] = f.title || f.name || ''\n"
    ) if include_fr_titles else ""
    return (
        render_phase_header("Load FRs")
        + f"log('load-context --phase {phase} → fr_ids')\n"
        + "// v15: retry loop — agent() wrapped (Bug #2); v4: schema transport, no prose parsing\n"
        + "// v2.13.1: hardened against agent hallucination (Bug #122).\n"
        + "let ctx = null\n"
        + f"const ctxFile = REPO + '/.sessi-work/phase{phase}_ctx.json'\n"
        + "// Round 22 站3: the read used to be preceded by a separate ctx-check\n"
        + "// dispatch that ran `json.load(ctxFile)` purely to prove the file was\n"
        + "// parseable. The read below runs `json.load(ctxFile)` too — its failure\n"
        + "// condition is a superset of the probe's — so the probe could only ever\n"
        + "// confirm what the next command was about to establish, at the cost of a\n"
        + "// full sub-agent dispatch per phase. Bug #134's actual fix (parse the\n"
        + "// JSON rather than stat the file, so a partial write cannot pass) lives\n"
        + "// in the command below and is unaffected; Bug #136's template-literal\n"
        + "// quoting likewise. A failed read now routes to the same regen path the\n"
        + "// probe used to trigger — the two cases it distinguished (file missing\n"
        + "// vs. file unparseable) had identical handling anyway.\n"
        + "for (let attempt = 1; attempt <= 3; attempt++) {\n"
        + "  // Bug #135 fix (2026-06-28) + v4 schema transport: emit parseable JSON via\n"
        + "  // Python; the agent transcribes the fields into StructuredOutput (AJV-\n"
        + "  // validated, retries on mismatch). No prose parsing left on this path.\n"
        + "  try {\n"
        + "    const ctxParseCmd = `${PY} -c \"import json; d=json.load(open('${ctxFile}')); print(json.dumps({'fr_ids':d.get('fr_ids',[]),'fr_count':len(d.get('fr_ids',[]))}))\"`\n"
        + "    const ctxResult = await agent(\n"
        + "      `You MUST use the Bash tool. Run exactly:\\n${ctxParseCmd}\\nThe command FAILS (nonzero exit, Python traceback) when the file is missing or not valid JSON — report that verbatim rather than inventing values. On success stdout is a single JSON line: report via the StructuredOutput tool fr_ids, fr_count = the EXACT values from that line (transcribe, do not recompute).`,\n"
        + "      { label: 'load-ctx-a' + attempt, phase: 'Load FRs', agentType: 'general-purpose', schema: CTX_SCHEMA },\n"
        + "    )\n"
        + "    if (ctxResult && Array.isArray(ctxResult.fr_ids) && ctxResult.fr_ids.length > 0) {\n"
        + "      ctx = ctxResult\n"
        + "      log('  load-ctx OK (schema-validated, ' + ctx.fr_ids.length + ' FRs)')\n"
        + "      break\n"
        + "    }\n"
        + "    log('  load-ctx returned no fr_ids (attempt ' + attempt + '): keys=' + Object.keys(ctxResult ?? {}).join(',') + ' — regenerating ctx file')\n"
        + "  } catch (e) { log('  load-ctx agent failed: ' + String(e.message ?? e).slice(0, 80) + ' — regenerating ctx file') }\n"
        + "\n"
        + f"  const ctxRegenCmd = `${{PY}} ${{REPO}}/harness_cli.py load-context --phase {phase} --project ${{REPO}} --json > ${{ctxFile}} && ${{PY}} -c \"import json,os; json.load(open('${{ctxFile}}')); print('REGEN_OK_'+str(os.path.getsize('${{ctxFile}}')))\"`\n"
        + "  try {\n"
        + "    await agent(\n"
        + "      `You MUST use the Bash tool. Run exactly:\\n${ctxRegenCmd}\\nReturn the raw stdout as your final message.`,\n"
        + "      { label: 'ctx-regen-' + attempt, phase: 'Load FRs', agentType: 'general-purpose' },\n"
        + "    )\n"
        + "  } catch (e) { log('  ctx-regen agent failed: ' + String(e.message ?? e).slice(0, 80)) }\n"
        + "}\n"
        + "if (!ctx) return halt('load-frs', { error: 'Load FRs: ctx failed after 3 attempts', ctxFile })\n"
        + "let frIds = Array.isArray(ctx.fr_ids) ? ctx.fr_ids\n"
        + "  : (Array.isArray(ctx.fr_details) ? ctx.fr_details.map(f => f.id || f.fr_id || f.fr).filter(Boolean) : [])\n"
        + "if (!frIds.length) return halt('load-frs', { error: 'Load FRs: no fr_ids found in ctx', ctxKeys: Object.keys(ctx) })\n"
        + fr_title_block
        + "log('  fr_ids = ' + JSON.stringify(frIds))\n"
    )


def render_terminal_abort_detectors(*, phase: int, indent: str, step: str) -> str:
    """The two per-FR conditions no fix agent can resolve (Round 13 站0/站2).

    Both were written for Phase 3's TDD loop and stayed there. P4, P5, P7 and P8
    run their own per-FR Gate 1 loops through `render_per_fr_delta` and had
    neither: harness crashing during any of those four was read as an ordinary
    GATE1 FAIL, which routes to CODE-FIX — a fix agent sent at a defect that is
    not in the project's code. Round 28 moves the pair here so every per-FR loop
    gets the same two exits from the same source.

    `indent` differs by host (P3's loop body sits one level deeper than the
    delta loop's); `step` names the step in the operator-facing message, so a
    P5 abort does not claim to have happened in GATE1.
    """
    i = indent
    return (
        f"{i}// L1.5: detect a structurally-broken dispatch [FATAL] surfaced via the sub-agent\n"
        f"{i}// (harness/cli/fr_cmds.py:_abort_dispatch_structurally_broken prints \"[FATAL] <fr> <step>:\n"
        f"{i}// sub-agent dispatch is structurally broken — claude.ai connectors are disabled\" to\n"
        f"{i}// stderr and returns exit code 23). A sub-agent reading its own {step} log and seeing\n"
        f"{i}// that banner will escalate to human with \"FAIL — structurally broken dispatch\" even\n"
        f"{i}// when the gate has not yet run a single evaluation round. The harness-side\n"
        f"{i}// _is_connector_disabled_failure guard already catches this AT the fr_cmds.py layer\n"
        f"{i}// for LINT-FIX / COVERAGE-FIX / GATE1-final-dispatch, but the TDD dispatches AND the\n"
        f"{i}// first-round prompt path do NOT have it. Continuing to dispatch the remaining FRs in\n"
        f"{i}// that state burns ~5min and ~50K tokens per FR on identically-broken dispatches.\n"
        f"{i}// Abort once the structural signal is observed.\n"
        f"{i}const frReportText = (typeof frReport === 'string') ? frReport : JSON.stringify(frReport)\n"
        f"{i}if (/structurally broken dispatch environment/i.test(frReportText) || "
        f"/\\[FATAL\\][^\\n]*dispatch is structurally broken/i.test(frReportText)) {{\n"
        f"{i}  log('  ' + frId + ' reports [FATAL] structurally broken dispatch "
        f"(claude.ai connectors disabled) — aborting remaining FRs')\n"
        f"{i}  return {{ dispatch_structurally_broken: true, phase: {phase}, fr_id: frId, gate1Pass, "
        f"gate1Fail: [...gate1Fail, frId], message: frId + ' {step}: dispatch is structurally broken "
        f"(env: ANTHROPIC_API_KEY overrides claude.ai login). Human must unset "
        f"ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN/ANTHROPIC_BASE_URL/ANTHROPIC_DEFAULT_HAIKU_MODEL "
        f"in the shell that launches this process, then re-run via "
        f"Workflow({{scriptPath, resumeFromRunId}}).' }}\n"
        f"{i}}}\n"
        f"{i}// L1.6 (Round 13 站0): detect a [HARNESS-BUG] banner (core/errors.py's crash\n"
        f"{i}// boundary — harness_cli.py's main() converting an uncaught exception into this\n"
        f"{i}// signal instead of a bare traceback) surfaced via the sub-agent reading its own\n"
        f"{i}// {step} log. Unlike the structurally-broken-dispatch signature above (a known,\n"
        f"{i}// human-actionable env-var cause), this means harness-methodology itself crashed —\n"
        f"{i}// the FR loop cannot proceed until a human fixes the harness bug, and treating it\n"
        f"{i}// as a code-quality FAIL would send CODE-FIX at a defect that isn't there.\n"
        f"{i}if ({HARNESS_BUG_RE_JS}.test(frReportText)) {{\n"
        f"{i}  log('  ' + frId + ' reports [HARNESS-BUG] — harness-methodology crashed, "
        f"aborting remaining FRs')\n"
        f"{i}  return {{ harness_bug_detected: true, phase: {phase}, fr_id: frId, gate1Pass, "
        f"gate1Fail: [...gate1Fail, frId], message: frId + ' {step}: harness-methodology itself "
        f"crashed ([HARNESS-BUG] — see the crash bundle path in the log). This is not a project "
        f"quality issue; a human must diagnose and fix the harness bug before this FR can "
        f"proceed.' }}\n"
        f"{i}}}\n"
    )


def render_per_fr_delta(
    *,
    phase: int,
    forbidden_note: str,
    verifier_role: str = "VERIFIER",
    use_fr_titles: bool = False,
    mid_milestone_step: str = "",
    pre_loop_state: str = "",
) -> str:
    """GATE1-DELTA fast-path (one batched agent classifies all FRs; unchanged
    FRs short-circuit inside the harness CLI) then a full per-FR loop for the
    FRs that didn't fast-pass, each backed by the harness-verified (not
    agent-self-reported) Gate 1 manifest check. `use_fr_titles` interpolates
    the frTitle lookup built by render_load_frs(include_fr_titles=True) into
    the per-FR verifier's opening line — callers must keep the two flags in
    sync (frTitle is undefined otherwise). `mid_milestone_step` is a raw JS
    block (phase4's p4-mid push at ≥50% FRs Gate 1 PASS) inserted after the
    full-loop's PASS/FAIL branch, mirroring where phase4's own code places
    it — NOT inside the fast-path loop above (phase4's original doesn't
    check it there either, an existing gap preserved as-is, not introduced
    by this migration). `pre_loop_state` is a raw JS block emitted right
    after the gate1Pass/gate1Fail declarations — phase4 uses it for the
    p4MidPushed/p4MidThreshold state that mid_milestone_step reads (Round
    12 站1: the sim testbed's first run caught that the station-3a
    migration dropped these two declarations — the pre-migration file had
    them at Load-FRs exit; a live ReferenceError the equivalence pins
    (phases/labels/CLI-command sets) could not see)."""
    role_line = (
        f"    'YOU ARE THE {verifier_role} for ' + frId + ' (' + (frTitle[frId] || '') + '). Re-evaluate Gate 1 for THIS ONE FR.\\n'\n"
        if use_fr_titles else
        f"    'YOU ARE THE {verifier_role} for ' + frId + '. Re-evaluate Gate 1 for THIS ONE FR.\\n'\n"
    )

    def _orch_post() -> str:
        # ORCH-POST (plan marker, every FR-loop phase — 4/5/7/8): fire-and-
        # report, no verdict gate — mirrors Artifacts Commit's style. The 40%
        # threshold is an early-warning floor, not a blocking gate like Gate
        # 3/4's ≥80/90%, so a low score here doesn't fail the phase.
        #
        # Round 22 站1: this used to render INSIDE both FR loops — one full
        # sub-agent dispatch per FR. Its second command, `amend-sab`, has no
        # --fr-id parameter at all (cli/project_cmds.py::cmd_amend_sab) and is
        # idempotent by construction (core/quality_gate/sab_amender.amend_sab:
        # "Idempotent: running twice adds nothing on the second call"), so N-1
        # of those N dispatches asked the same project-wide question again.
        # taskq's 5-FR run left 35 `tool:amend-sab` rows in sessions_spawn.log.
        # The per-FR INFORMATION is preserved — spec-coverage-check still runs
        # once per FR, in a bash loop inside this single agent; only the
        # dispatch count is decoupled from the FR count.
        return (
            "if (gate1Pass.length) {\n"
            "  await agent(\n"
            "    'Run these commands via the Bash tool, in order. Report the verbatim stdout/stderr of ALL of them.\\n'\n"
            "    + '1. Per-FR spec coverage — run for EVERY id in the list, and do NOT stop early on a nonzero exit (each `|| true` keeps the loop going; a below-threshold FR is an early warning to report, not a reason to abort):\\n'\n"
            "    + '`for FR in ' + gate1Pass.join(' ') + '; do ' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 40.0 --fr-id $FR || true; done`\\n'\n"
            "    + '2. `' + PY + ' ' + REPO + '/harness_cli.py amend-sab --project ' + REPO + '` (project-wide, runs ONCE — it takes no --fr-id)\\n\\n'\n"
            "    + 'SCOPE RULES:\\n- ONLY the two commands above.\\n- DO NOT modify harness/.',\n"
            "    { label: 'orch-post', phase: 'Per-FR Delta', agentType: 'general-purpose' },\n"
            "  )\n"
            "}\n"
        )

    return (
        render_phase_header("Per-FR Delta")
        + "const gate1Pass = []\n"
        + "const gate1Fail = []\n"
        + pre_loop_state
        + "// DELTA fast-path: probe every FR's GATE1-DELTA through the harness CLI in ONE\n"
        + "// agent — unchanged-code FRs pass immediately inside the CLI, so N already-PASS\n"
        + "// FRs cost 1 spawn instead of 2N (delta + verify). Verdict authority is manifest\n"
        + "// qc AND a phase-scoped gate_timestamps.jsonl entry (NOT the agent's self-report).\n"
        + "// The timestamp is required because manifest qc is not phase-scoped: a stale\n"
        + "// `true` from an earlier phase would mask a timed-out/failed run-fr-step this\n"
        + "// phase. run-fr-step writes the {phase, gate:1, fr_id} timestamp only on\n"
        + "// successful completion (both the unchanged-skip and full-dispatch paths); a\n"
        + "// killed dispatch writes nothing, so absence ⇒ fail ⇒ full per-FR loop.\n"
        + "let deltaTodo = frIds\n"
        + "const fastProbe = await agent(\n"
        + "  'YOU ARE THE GATE1-DELTA FAST-PATH PROBE. Classify each FR — fix NOTHING.\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\nFRs: ' + JSON.stringify(frIds) + '\\n\\n'\n"
        + f"  + 'Direction C (past lessons): BEFORE classifying, Bash `cat ' + REPO + '/.sessi-work/phase{phase}_ctx.json` and READ the `lessons` field (compact markdown, \"\" if none). DO NOT repeat those past failure modes in your pass/fail classification or any follow-up P{phase} work.\\n\\n'\n"
        + "  + 'For EACH FR in order, substituting <FR> with the FR id:\\n'\n"
        + "  + '1. GATE1-DELTA is long-running for any FR whose code actually changed (harness runs up to 3 internal CODE-FIX rounds, each up to ~600s — can silently block ~2400s worst case even though this step is a \"probe\"). Run it BACKGROUNDED for every FR, not just slow ones — unchanged FRs still hit the fast in-CLI short-circuit almost instantly so this costs nothing extra:\\n'\n"
        + f"  + '   a. `nohup ' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase {phase} --fr-id <FR> --step GATE1-DELTA --project ' + REPO + ' > /tmp/gate1delta_<FR>.log 2>&1 & echo $!` — note the PID.\\n'\n"
        + "  + '   b. Poll with BACKOFF intervals, in seconds: 5, 10, then 30 for every further iteration — `sleep <interval> && kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Cap 42 polls (5+10 + 40x30 ≈ 20min). Still RUNNING past the cap → classify <FR> as fail_fr_ids (the full loop below will retry it) and move to the next FR — do not kill the PID.\\n'\n"
        + "  + '      (Round 22 站4: the first interval used to be a flat 30s. An unchanged FR hits the in-CLI short-circuit almost instantly, and this probe walks the FRs one at a time, so a fixed first sleep cost 30s x N — ten minutes on a 20-FR project spent waiting on commands that had already returned.)\\n'\n"
        + "  + '   c. DONE → proceed to step 2 (the log itself is not needed — the authoritative verdict is the manifest read below).\\n'\n"
        + f"  + '2. Authoritative verdict (manifest qc AND a phase-{phase} gate-1 timestamp for <FR>): `' + PY + ' -c \"import json; g=(json.load(open(\\'' + REPO + '/.methodology/quality_manifest.json\\')).get(\\'gate_results\\',{{}}) or {{}}).get(\\'gate1\\',{{}}).get(\\'<FR>\\',{{}}) or {{}}; ts=any(e.get(\\'phase\\')=={phase} and e.get(\\'gate\\')==1 and e.get(\\'fr_id\\')==\\'<FR>\\' for e in (json.loads(l) for l in open(\\'' + REPO + '/.methodology/gate_timestamps.jsonl\\') if l.strip())); print(bool(g.get(\\'quality_complete\\')) and ts)\"`\\n'\n"
        + "  + '   stdout `True` → pass_fr_ids; anything else (False/None/timeout/error/missing file) → fail_fr_ids.\\n\\n'\n"
        + f"  + 'HARD RULES:\\n- DO NOT fix code, edit files, or run TDD steps.\\n- DO NOT retry a failing FR — classify it and move on (the full loop handles it).\\n{forbidden_note}- DO NOT modify harness/.\\n\\n'\n"
        + "  + 'Report via the StructuredOutput tool: pass_fr_ids + fail_fr_ids (every FR in exactly one list).',\n"
        + "  { label: 'delta-fastpath', phase: 'Per-FR Delta', agentType: 'general-purpose', schema: DELTA_FAST_SCHEMA },\n"
        + ")\n"
        + "if (fastProbe && Array.isArray(fastProbe.pass_fr_ids)) {\n"
        + "  const fastPassed = fastProbe.pass_fr_ids.filter((f) => frIds.includes(f))\n"
        + "  for (const fr of fastPassed) {\n"
        + "    gate1Pass.push(fr)\n"
        + f"    log('  ' + fr + ' GATE1-DELTA fast-path PASS [manifest qc + p{phase} timestamp] — full DELTA skipped')\n"
        + "  }\n"
        + "  deltaTodo = frIds.filter((f) => !fastPassed.includes(f))\n"
        + "} else {\n"
        + "  log('  delta-fastpath unavailable — falling back to full per-FR loop')\n"
        + "}\n"
        + "for (const frId of deltaTodo) {\n"
        + "  log('  === ' + frId + ' — GATE1-DELTA ===')\n"
        + "  const frReport = await agent(\n"
        + role_line
        + "    + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "    + 'Steps:\\n'\n"
        + "    + '1. GATE1-DELTA — long-running when code changed (harness runs up to 3 internal CODE-FIX rounds plus, on FAIL, a full TDD-RED→GREEN→IMPROVE→GATE1 chain — can silently block well past 180s). Run it BACKGROUNDED, do NOT invoke it as a plain synchronous command:\\n'\n"
        + f"    + '   a. `nohup ' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase {phase} --fr-id ' + frId + ' --step GATE1-DELTA --project ' + REPO + ' > /tmp/gate1delta_' + frId + '.log 2>&1 & echo $!` — note the PID.\\n'\n"
        + "    + '   b. Poll every 30s: `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Cap 60 polls (~30min — this path can chain a full TDD cycle on top of GATE1-DELTA\\'s own retries). Still RUNNING past the cap → report \"' + frId + ' GATE1: TIMEOUT\" (not FAIL) and stop — do not kill the PID.\\n'\n"
        + "    + '   c. DONE → `cat /tmp/gate1delta_' + frId + '.log` for the full output, identical to a synchronous run. Parse PASS/FAIL from it.\\n'\n"
        + "    + '   - PASS → done.\\n'\n"
        + "    + '   - FAIL → full TDD auto-triggered: TDD-RED → TDD-GREEN → TDD-IMPROVE → GATE1 (each for ' + frId + '). Max 3 rounds. Still failing → report FAIL.\\n'\n"
        + "    + '   If ' + frId + '’s code is unchanged since last Gate 1 PASS, this passes immediately.\\n\\n'\n"
        + "    + 'Report final line: \"' + frId + ' GATE1: PASS\" or \"' + frId + ' GATE1: FAIL — <reason>\".\\n\\n'\n"
        + f"    + 'SCOPE RULES:\\n- DO NOT touch any FR OTHER than ' + frId + '.\\n{forbidden_note}- DO NOT edit .methodology/quality_manifest.json or .sessi-work/gate1_result.json to fake/reset scores — fix the underlying code/tests instead.\\n- DO NOT modify harness/.\\n- ONLY GATE1-DELTA (+ full TDD if needed) for ' + frId + '.',\n"
        + "    { label: 'delta-' + frId, phase: 'Per-FR Delta', agentType: 'general-purpose' },\n"
        + "  )\n"
        + "  // L1 (ported from phase3): distinguish a session/rate-limit block (null/empty\n"
        + "  // agent return) from a real Gate 1 FAIL — a rate-limit mid-DELTA must not be\n"
        + "  // misreported as a code-quality failure. DELTA auto-skip makes resume safe.\n"
        + "  if (frReport === null || frReport === undefined || (typeof frReport === 'string' && frReport.length < 10)) {\n"
        + "    log('  ' + frId + ' agent blocked (session limit / rate limit) — aborting, resume after quota reset')\n"
        + f"    return {{ session_limit_blocked: true, phase: {phase}, fr_id: frId, gate1Pass, message: 'Agent hit session/rate limit during ' + frId + ' GATE1-DELTA. Resume after quota reset — completed FRs skip via DELTA auto-satisfy.' }}\n"
        + "  }\n"
        + render_terminal_abort_detectors(phase=phase, indent="  ", step="GATE1-DELTA")
        + "  // AUTHORITATIVE Gate 1 verdict (ported from phase3, 9fe2036): read the harness\n"
        + "  // quality_manifest — NOT the sub-agent's self-reported \"GATE1: PASS\" string. A\n"
        + "  // sub-agent can report PASS even when finalize-gate raised GateBlockedError,\n"
        + "  // silently advancing a FR the harness actually blocked (2026-06-30 incident).\n"
        + "  // Round 12 站2a: the deterministic read lives in the standalone helper\n"
        + "  // (`harness/scripts/verify_gate1_qc.py`, v2.13.3 pattern — cef32c4 deferred\n"
        + "  // this exact P4/P5/P7/P8 migration). The LLM is a string carrier only:\n"
        + "  // the verdict is derived from the echoed deterministic stdout, and the\n"
        + "  // LLM's own `pass` boolean is IGNORED — wf_53d055ce-d0b showed an agent\n"
        + "  // hallucinating pass:false against a PASS manifest; Python's printed\n"
        + "  // bytes cannot be flipped by a wrong boolean.\n"
        + "  const verdict = await agent(\n"
        + "    'You MUST use the Bash tool. Run EXACTLY this single command (single line):\\n'\n"
        + "    + PY + ' ' + REPO + '/harness/scripts/verify_gate1_qc.py --fr-id ' + frId + ' --project ' + REPO + '\\n'\n"
        + "    + 'Then report via the StructuredOutput tool: pass = true ONLY if the FIRST line of stdout is exactly \"GATE1_VERIFIED_PASS\"; reason = the verbatim stdout (do NOT paraphrase, summarize, or prepend commentary).',\n"
        + "    { label: 'gate1-verify-' + frId, phase: 'Per-FR Delta', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + "  )\n"
        + "  const passed = String((verdict && verdict.reason) || '').trim().startsWith('GATE1_VERIFIED_PASS')\n"
        + "  if (passed) {\n"
        + "    gate1Pass.push(frId); log('  ' + frId + ' Gate 1 PASS [harness-verified]')\n"
        + "  } else { gate1Fail.push(frId); log('  ' + frId + ' Gate 1 FAIL [harness manifest qc != true; sub-agent self-report ignored]') }\n"
        + mid_milestone_step
        + "}\n"
        + "if (gate1Fail.length) {\n"
        + f"  return halt('gate1', {{ error: 'Phase {phase}: Gate 1 FAILED for FR(s): ' + gate1Fail.join(', ') + ' (escalate)', gate1Pass, gate1Fail }})\n"
        + "}\n"
        + _orch_post()
    )


def render_artifacts_commit(*, paths: list[str], commit_msg: str, phase: int) -> str:
    path_args = " ".join(paths)
    return (
        render_phase_header("Artifacts Commit")
        + f"log('Committing phase-{phase} artifacts (explicit paths) so a verify-handoff FAIL exit leaves a clean tree')\n"
        + "await agent(\n"
        + "  'Run ONE bash command and report its stdout/stderr:\\n'\n"
        + f"  + '`git -C ' + REPO + ' add {path_args} && git -C ' + REPO + ' commit -m \"{commit_msg}\" || true`\\n\\n'\n"
        + "  + 'Report: the verbatim stdout/stderr of that command. \"nothing to commit\" is a valid outcome.\\n\\n'\n"
        + f"  + 'SCOPE RULES:\\n- DO NOT run any code, tests, gates, or phase transitions.\\n- DO NOT stage any path other than the {len(paths)} listed above.\\n- ONLY the git command above.',\n"
        + "  { label: 'artifacts-commit', phase: 'Artifacts Commit', agentType: 'general-purpose' },\n"
        + ")\n"
    )


def render_milestone(
    *,
    phase: int,
    milestone_type: str,
    guard_grep: str,
    label: str,
    extra_note: str = "",
) -> str:
    """A single push-milestone call, guarded so a re-run after an already-
    pushed milestone short-circuits instead of re-pushing."""
    return (
        render_phase_header("Milestone")
        + f"log('push-milestone {milestone_type}{extra_note}')\n"
        + "const milestoneReport = await agent(\n"
        + f"  'YOU ARE THE P{phase} MILESTONE PUSHER.\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + f"  + '0. GUARD: `git -C ' + REPO + ' log --oneline --grep=\"{guard_grep}\" -1`. If exists, report \"MILESTONE: PASS (already pushed)\" and stop.\\n'\n"
        + f"  + '1. Command: `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type {milestone_type} --project ' + REPO + '`\\n'\n"
        + "  + 'Writes HANDOVER.md + commits + pushes. If a hook blocks, reword commit to start with `chore(harness):` (NOT --no-verify), retry.\\n\\n'\n"
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true if the milestone commit exists or was pushed; reason = one-line detail.\\n\\n'\n"
        + f"  + 'SCOPE RULES:\\n- DO NOT run advance-phase.\\n- ONLY push-milestone {milestone_type}.',\n"
        + f"  {{ label: '{label}', phase: 'Milestone', agentType: 'general-purpose', schema: VERDICT_SCHEMA }},\n"
        + ")\n"
        + "if (!(milestoneReport && milestoneReport.pass === true)) {\n"
        + f"  return halt('milestone', {{ error: 'Phase {phase} {milestone_type} milestone did not PASS', reason: milestoneReport ? String(milestoneReport.reason ?? '').slice(-500) : 'agent returned null' }})\n"
        + "}\n"
    )


def render_advance_loop(
    *,
    phase: int,
    next_phase: int,
    precheck_steps: list[str] | None = None,
    scope_extra: str = "",
    only_extra: str = "",
    log_msg: str | None = None,
    on_pass_extra: str = "",
    advance_step_override: str | None = None,
) -> str:
    """The advance-phase retry loop (round-based, manifest-integrity-guarded
    each round — 2026-07-02 audit finding: advance-phase enforces more
    independent checks than any prompt can safely enumerate ahead of time,
    so the robust design reads advance-phase's own [BLOCKED] output each
    round rather than guessing). `precheck_steps` are optional proactive
    checks (e.g. P5's D4-GAP spec-coverage warning) that run BEFORE
    advance-phase within the same numbered step list; `only_extra` names
    those same precheck commands in the closing "ONLY ..." scope line (kept
    as a separate param rather than derived from precheck_steps — the two
    texts don't share a machine-extractable command token). `on_pass_extra`
    is a raw JS block inserted right after the PASS log line, before break —
    phase4 uses this for its post-advance `git add -A` clean-up commit
    (advance-phase only commits its own target paths, leaving other
    post-advance edits uncommitted); no other migrated phase needs it.
    `advance_step_override` replaces the auto-appended synchronous
    advance-phase step text with a caller-supplied alternative — phase3
    backgrounds advance-phase via nohup/poll (its own `ruff+mypy+pytest
    --cov-fail-under=100` internal cost is non-trivial at project scale),
    unlike every other migrated phase's plain synchronous call.
    """
    steps = list(precheck_steps or [])
    steps.append(
        advance_step_override if advance_step_override is not None else
        f"advance-phase: `' + PY + ' ' + REPO + '/harness_cli.py advance-phase --completed {phase} --project ' + REPO + '`\\n'\n"
        + "    + '   advance-phase independently re-verifies EVERYTHING before it will advance — its own output tells you exactly what is missing. If it prints \"[BLOCKED] ...\", that message IS the fix instruction: read it verbatim and do exactly what it says, then re-run this same advance-phase command. Do NOT guess what might be wrong — trust only what advance-phase itself reports. It is safe to re-run repeatedly within this round."
    )
    steps.append(
        f"Read ' + REPO + '/.methodology/state.json; confirm current_phase = {next_phase} (advance-phase atomically writes state.json when complete)."
    )
    last = len(steps)
    newline = "\\n"
    numbered = "".join(
        f"    + '{i}. {s}\\n{newline if i == last else ''}'\n"
        for i, s in enumerate(steps, start=1)
    )
    log_line = log_msg if log_msg is not None else f"advance-phase --completed {phase}"
    return (
        render_phase_header("Advance")
        + f"log('{log_line}')\n"
        + "// Round loop (2026-07-02 audit finding, ported from phase3): advance-phase\n"
        + "// enforces more independent checks than any single prompt can safely\n"
        + "// enumerate, and a static checklist goes stale the moment harness adds or\n"
        + "// changes one. advance-phase is idempotent (preflight runs before any\n"
        + "// FSM/state write), so the robust fix is an outer retry loop where the\n"
        + "// agent reads advance-phase's own [BLOCKED] output each round instead of\n"
        + "// guessing in advance.\n"
        + "let advancePass = false, advanceReport = ''\n"
        + "const ADVANCE_MAX_ROUNDS = 5\n"
        + "for (let round = 1; round <= ADVANCE_MAX_ROUNDS; round++) {\n"
        + "  log('  Advance round ' + round + '/' + ADVANCE_MAX_ROUNDS)\n"
        + "  // Manifest integrity: enforced by advance-phase itself since Round 22 站2\n"
        + "  // (cli/phase_cmds.py::_advance_prechecks, exit 27 with the restore command\n"
        + "  // in its [BLOCKED] message). It runs first, before any other precheck, and\n"
        + "  // on every round because advance-phase is idempotent — same guarantee the\n"
        + "  // per-round dispatch here used to buy, minus the dispatch, and now covering\n"
        + "  // the human/CI callers this loop never could.\n"
        + "  advanceReport = await agent(\n"
        + f"    'YOU ARE THE PHASE-{phase} EXIT ORCHESTRATOR. Advance to Phase {next_phase}. ROUND ' + round + '.\\n'\n"
        + "    + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "    + 'Steps:\\n'\n"
        + f"    + '0. GUARD — already advanced? `PHASE=$(jq -r .current_phase ' + REPO + '/.methodology/state.json 2>/dev/null); echo \"current_phase=$PHASE\"; [ \"$PHASE\" -ge {next_phase} ]`. If Phase {next_phase} is confirmed, report \"ADVANCE: PASS (already advanced)\" and stop.\\n'\n"
        + numbered
        + f"    + 'Report final line: \"ADVANCE: PASS|FAIL — <details>\". If still FAIL after exhausting this round\\'s turn, report the LAST [BLOCKED] message verbatim so the next round starts from where this one left off. PHASE_{next_phase}_PLAN: ' + REPO + '/.methodology/phase{next_phase}_plan.md\\n\\n'\n"
        + f"    + 'SCOPE RULES:\\n{scope_extra}- DO NOT use --no-verify.\\n- DO NOT modify harness/ (HR-17).\\n- ONLY {only_extra}advance-phase + verify HANDOVER.md + the specific fixes advance-phase\\'s own output asked for.\\n- Any diagnostic/debug script MUST be written under .sessi-work/tmp/ (never repo root or source dirs) and self-cleaned before you exit.',\n"
        + "    { label: 'advance-r' + round, phase: 'Advance', agentType: 'general-purpose' },\n"
        + "  )\n"
        + "  if (advanceReport === null || advanceReport === undefined || (typeof advanceReport === 'string' && advanceReport.length < 10)) {\n"
        + "    log('  Advance agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')\n"
        + f"    return {{ session_limit_blocked: true, phase: {phase}, step: 'advance', message: 'Agent hit session/rate limit during Advance. Resume after quota reset — the GUARD step skips if already advanced.' }}\n"
        + "  }\n"
        + "  // AUTHORITATIVE Advance verdict: advance-phase atomically writes\n"
        + f"  // state.json current_phase={next_phase} on success. Read it via a schema proxy —\n"
        + "  // the orchestrator's prose \"ADVANCE: PASS\" is narrative only.\n"
        + "  const advVerifyCmd = PY + ' -c \"import json; print(json.dumps({\\'current_phase\\': int(json.load(open(\\'' + REPO + '/.methodology/state.json\\')).get(\\'current_phase\\') or 0)}))\"'\n"
        + "  const advV = await agent(\n"
        + "    'Run EXACTLY this command via the Bash tool (stdout is a single JSON line):\\n`' + advVerifyCmd + '`\\n'\n"
        + "    + 'Then report via the StructuredOutput tool: current_phase = the exact integer from that JSON.',\n"
        + "    { label: 'advance-verify-r' + round, phase: 'Advance', agentType: 'general-purpose', schema: PHASE_SCHEMA },\n"
        + "  )\n"
        + f"  advancePass = !!(advV && advV.current_phase >= {next_phase})\n"
        + "  if (advancePass) {\n"
        + "    log('  Advance PASS [harness-verified: state.json current_phase=' + advV.current_phase + ']')\n"
        + on_pass_extra
        + "    break\n"
        + "  }\n"
        + "  log('  Advance not yet PASS [state.json current_phase=' + (advV ? advV.current_phase : '?') + '] — retry round ' + (round + 1))\n"
        + "}\n"
        + "\n"
        + "if (!advancePass) {\n"
        + f"  return halt('advance', {{ error: 'Advance did not PASS in ' + ADVANCE_MAX_ROUNDS + ' rounds — check HANDOVER.md + state.json + the last [BLOCKED] message below. If Phase {next_phase} is confirmed, resume workflow to verify.', raw: String(advanceReport ?? '').slice(-600) }})\n"
        + "}\n"
    )


# Round 43 站3. `scripts/hooks/pre-push` runs `harness_cli.py run-phase
# --phase N` — the full fifteen-check preflight — on every push. What it
# rejects is almost never the network; it is a pragma, a missing FR Block, an
# unregistered SAB module. Until this round the Sync step that performs that
# push was the one blocking step in the pipeline with no authority to fix
# anything: a bare `git push`, and in P3's case the SAME instruction dispatched
# a second time under a comment about transient network failures. Round 41 站3's
# rule — diagnose the transport from the transport's own text, and stop buying
# the same failure twice — is what re-sending an identical prompt at a
# deterministic content failure violates.
#
# The correct shape was already in the same generated file: render_push_loop's
# P1 Push step runs push-checkpoint up to five times with "Read the error and
# fix if FAIL", through the identical hook.
#
# The clause is a module constant, not inline prose, because
# tests/test_sync_may_repair.py asserts it appears in every shipped Sync block.
# A test that restated the wording would drift from it (Round 33 站1 /
# Round 36 站1: render from one source, assert against that source).
SYNC_ATTEMPTS_CONST = "SYNC_MAX_ATTEMPTS"
SYNC_MAX_ATTEMPTS = 3

SYNC_REPAIR_CLAUSE = (
    "If the push is REJECTED, the pre-push hook has already printed why: it "
    "runs the full phase preflight, so the blocker is almost always project "
    "CONTENT (a `# pragma: no cover`, a missing artifact block, an "
    "unregistered SAB module), not the network. Read the blocker list, fix "
    "exactly what it names, and push again. Do NOT use --no-verify. If the "
    "output contains [HARNESS-BUG], stop — harness-methodology crashed and "
    "there is nothing in this project to fix."
)


def render_sync_verified(
    *,
    extra_lines: list[str] | None = None,
    on_blocked: str | None = None,
) -> str:
    """The phase-exit Sync step: publish the advance handover commit.

    Bounded retry WITH repair authority — see SYNC_REPAIR_CLAUSE above for why
    a bare re-send was wrong. `[HARNESS-BUG]` in the output stops the loop
    immediately: the harness crashed, the project has no defect to fix, and
    another attempt buys the same failure.

    Used by every phase-exit Sync step. Round 28 migrated P8 onto it from the
    old unconditional render_sync(); Round 43 站3 migrated P3, whose bespoke
    renderer was the second implementation of this step.

    `extra_lines` appends phase-specific confirmation steps (P8's tag-push
    check) into the same verdict-checked report. `on_blocked` replaces the
    terminal branch for a phase that has something specific to say when the
    push cannot be made — P3 writes a "Sync Blocked" section into HANDOVER.md
    and returns a structured MANUAL_REQUIRED result. Default is a bare error
    return.

    Unlike every other phase box this one has no boxed `// Phase: Sync`
    divider comment in the original files, so it does not call
    render_phase_header().
    """
    extra = "".join(f"  + '{line}\\n'\n" for line in (extra_lines or []))
    terminal = on_blocked if on_blocked is not None else (
        "  return halt('post-advance-push', { error: 'post-advance push did not PASS', "
        "raw: String(syncReport ?? '').slice(-500) })\n"
    )
    return (
        "// Bug A fix (2026-07-07): advance-phase intentionally commits the handover\n"
        "// locally without pushing (harness/cli/phase_cmds.py: \"next milestone push\n"
        "// publishes to origin\"). This workflow ends right after Advance with no\n"
        "// next-phase push queued, so the handover commit was left stranded on\n"
        "// local until whatever runs next happened to push it. Publish it now.\n"
        "phase('Sync')\n"
        "log('git push origin main (publish advance handover commit)')\n"
        f"const {SYNC_ATTEMPTS_CONST} = {SYNC_MAX_ATTEMPTS}\n"
        "const SYNC_PROMPT = 'Run this command via Bash:\\n'\n"
        "  + 'git -C ' + REPO + ' push origin main\\n\\n'\n"
        + extra
        + f"  + '{SYNC_REPAIR_CLAUSE}\\n\\n'\n"
        "  + 'Report final outcome as plain text: \"SYNC: PASS\" or \"SYNC: FAIL — <one-line reason>\"'\n"
        "  + ' (if the pre-push hook printed a blocker list, include it verbatim).'\n"
        "let syncReport = ''\n"
        "let syncPass = false\n"
        f"for (let sAttempt = 1; sAttempt <= {SYNC_ATTEMPTS_CONST}; sAttempt++) {{\n"
        "  syncReport = await agent(SYNC_PROMPT, "
        "{ label: 'sync-' + sAttempt, phase: 'Sync', agentType: 'general-purpose' })\n"
        "  const syncText = String(syncReport ?? '')\n"
        "  syncPass = /SYNC:\\s*PASS/.test(syncText)\n"
        "  if (syncPass) break\n"
        f"  if ({HARNESS_BUG_RE_JS}.test(syncText)) {{\n"
        "    log('  Sync reports [HARNESS-BUG] — harness-methodology crashed; "
        "not a project blocker and not something a retry can clear')\n"
        "    return { harness_bug_detected: true, step: 'sync', "
        "message: 'git push was rejected by a harness-methodology crash "
        "([HARNESS-BUG] — see the crash bundle path in the log), not by a "
        "project quality failure. A human must fix the harness bug.', "
        "raw: syncText.slice(-600) }\n"
        "  }\n"
        f"  log('  Sync attempt ' + sAttempt + '/' + {SYNC_ATTEMPTS_CONST} + "
        "' did not PASS — read the pre-push blocker list, fix what it names, retry')\n"
        "}\n"
        "if (!syncPass) {\n"
        + terminal
        + "}\n"
    )


def render_gate_loop(
    *,
    gate_num: int,
    phase: int,
    log_msg: str,
    prompt_steps: list[str],
    pass_line_desc: str,
    scope_rules: str,
    d4_threshold: float,
    on_fail_error_msg: str,
    include_manifest_integrity: bool = True,
    deferred_fixes_step: str = "",
    wrap_try_catch: bool = False,
    orchestrator_desc: str | None = None,
    pre_gate_note: str = "",
    include_finalize_note: bool = True,
) -> str:
    """The Gate-2 (phase3) / Gate-3 (phase4) / Gate-4 (phase6) evaluation
    round loop — all three share this skeleton (round loop, orchestrator
    agent, session-limit detection, harness-verified manifest-qc + D4
    verdict check, retry) but differ enough in real content
    (dims/thresholds/fix-hints — supplied via `prompt_steps`/`scope_rules`/
    `pass_line_desc` since that prose doesn't repeat between gates;
    `include_manifest_integrity` — gate2 re-checks each round, gate3/gate4
    don't; `deferred_fixes_step` — gate3 writes deferred_fixes.md on
    exhausted-retries FAIL, gate2/gate4 don't; `wrap_try_catch` — gate4's
    orchestrator call is wrapped in try/catch, ported from phase3 in a
    historical "Bug #2" fix, gate2/gate3 aren't; `orchestrator_desc` —
    gate2/gate3 say "(Phase N exit)", gate4 says "(Phase 6 — full project
    quality)" since Gate 4 isn't a hand-off checkpoint into another phase;
    `pre_gate_note` — gate4 has an extra "Pre-Gate: confirm ..." sentence
    gate2/gate3 don't; `include_finalize_note` — gate4's final-line summary
    omits the "finalize-gate writes HANDOVER.md..." clause because its own
    numbered step 4 (G4c) already states that, making the clause redundant
    there only) that this function takes them as explicit parameters rather
    than guessing a false common prose.

    The round-final verify step runs `verify-gate`, one framework command that
    performs all three checks (state.json's last_gate, spec-coverage, and the
    CRG architecture floor) and appends the verdict — with a digest of the tree
    it measured — to .methodology/gate_verify.jsonl. The agent transcribes one
    exit code instead of three, and `advance-phase` re-derives the digest before
    letting the phase through.

    Round 38: this used to be three commands and three transcribed numbers, none
    of which was written down anywhere. `crg_rc` appears zero times in
    taskq-renew's entire .methodology/ after a complete P1-P8 run, which is why
    the contradiction between its P6 baseline (77.8, below its own floor of 80)
    and its first-round gate4-verify PASS cannot be adjudicated. A `crg_check`
    parameter also went: every call site passed True, so it stated nothing.
    """
    # crg_threshold wiring: same "harness-verified, not self-reported" shape as
    # D4 above — a 3rd command in the SAME verify-agent dispatch (not the main
    # gate-orchestrator agent's self-remediation round), ANDed into gate{N}Pass
    # by the orchestrator JS itself, not trusted from the subagent's judgment.

    steps_text = "".join(f"    + '{s}\\n'\n" for s in prompt_steps)
    integrity_block = (
        f"  const g{gate_num}Integrity = await checkManifestIntegrity('Gate {gate_num}', 'g{gate_num}-integrity-r' + round)\n"
        f"  if (!g{gate_num}Integrity.ok) {{\n"
        f"    return halt('manifest-corrupt', {{ error: 'Gate {gate_num} round ' + round + ': quality_manifest.json corrupted mid-run', detail: g{gate_num}Integrity.raw, recovery: 'git checkout HEAD -- .methodology/quality_manifest.json (verify HEAD is healthy first — a corrupted manifest may already be committed)', note: 'Corruption appeared AFTER the entry integrity check. Inspect the previous round\\'s agent transcript for the writer before restoring.' }})\n"
        f"  }}\n"
    ) if include_manifest_integrity else ""
    agent_open = (
        f"  // v15: wrap agent() in try/catch (Bug #2)\n"
        f"  try {{ gate{gate_num}Report = await agent(\n"
    ) if wrap_try_catch else f"  gate{gate_num}Report = await agent(\n"
    agent_close = (
        f"  ) }} catch (e) {{\n"
        f"    log('  Gate {gate_num} agent threw: ' + String(e.message ?? e).slice(0, 80) + ' -- retrying')\n"
        f"    gate{gate_num}Report = ''\n"
        f"    if (round < 3) continue\n"
        f"  }}\n"
    ) if wrap_try_catch else "  )\n"
    desc = orchestrator_desc if orchestrator_desc is not None else f"Phase {phase} exit"
    pre_gate_block = f"    + '{pre_gate_note}\\n\\n'\n" if pre_gate_note else ""
    finalize_note = f"finalize-gate (G{gate_num}c) writes HANDOVER.md + pushes on PASS. " if include_finalize_note else ""
    # Pre-flight: skip dispatch entirely if a prior run already fully finalized
    # this gate. The authoritative signal is state.json.last_gate >= gate_num,
    # which harness_cli.py's finalize-gate only writes (_update_state_checkpoint)
    # AFTER PhaseTruthVerifier.verify() passes for a phase-exit gate — see
    # cli/gate_cmds.py cmd_finalize_gate: the Phase-Truth block does `return 11`
    # BEFORE reaching _update_state_checkpoint. manifest.gate_results.gate{N}.
    # quality_complete is NOT sufficient on its own: it is set from the SSI
    # dimension score alone, BEFORE the Phase Truth check runs, and never
    # reverts — so it can read true forever even while the gate stays blocked.
    # We piggyback on checkManifestIntegrity's pattern (delegate the shell read
    # to a tiny agent so the workflow runtime's hermeticity invariant holds).
    gate_precheck_block = (
        f"// Gate {gate_num} pre-flight GUARD: only state.json.last_gate >= {gate_num} proves this gate was\n"
        f"// truly finalized (SSI dims passed AND Phase Truth passed) — see harness_cli.py finalize-gate.\n"
        f"{{\n"
        f"  const _precheckCmd = `${{PY}} -c \"import json; lg=json.load(open('${{REPO}}/.methodology/state.json')).get('last_gate'); print(json.dumps({{'qc': isinstance(lg,int) and lg >= {gate_num}, 'last_gate': lg}}))\"`\n"
        f"  try {{\n"
        f"    const _preVerdict = await agent(\n"
        f"      'Run EXACTLY this command via the Bash tool:\\n`' + _precheckCmd + '; echo RC=$?`\\n'\n"
        f"      + 'Then report via the StructuredOutput tool: pass = true ONLY if the output line starts with `{{\"qc\": true`; reason = the verbatim JSON line (excluding the RC= line).',\n"
        f"      {{ label: 'gate{gate_num}-precheck', phase: 'Gate {gate_num}', agentType: 'general-purpose', schema: VERDICT_SCHEMA }},\n"
        f"    )\n"
        f"    if (_preVerdict && _preVerdict.pass === true) {{\n"
        f"      gate{gate_num}Pass = true\n"
        f"      log('  Gate {gate_num} PRE-FLIGHT PASS — state.json last_gate >= {gate_num} (gate truly finalized); skipping round loop')\n"
        f"    }} else {{\n"
        f"      log('  Gate {gate_num} pre-flight: not yet finalized — proceeding to round loop')\n"
        f"    }}\n"
        f"  }} catch (e) {{\n"
        f"    log('  Gate {gate_num} pre-flight threw: ' + String(e.message ?? e).slice(0, 120) + ' — proceeding to round loop')\n"
        f"  }}\n"
        f"}}\n"
    )
    return (
        render_phase_header(f"Gate {gate_num}")
        + f"log('{log_msg}')\n"
        + f"let gate{gate_num}Pass = false, gate{gate_num}Report = '', gate{gate_num}Blocked = false\n"
        + gate_precheck_block
        + f"if (!gate{gate_num}Pass) for (let round = 1; round <= 3; round++) {{\n"
        + f"  log('  Gate {gate_num} round ' + round + '/3')\n"
        + integrity_block
        + agent_open
        + f"    'YOU ARE THE GATE-{gate_num} ORCHESTRATOR ({desc}). ROUND ' + round + '.\\n'\n"
        + "    + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + pre_gate_block
        + "    + 'Steps:\\n'\n"
        + steps_text
        + f"    + '{finalize_note}Report final line: \"GATE{gate_num}: PASS\" ({pass_line_desc}) or \"GATE{gate_num}: FAIL — <failing dims>\".\\n\\n'\n"
        + f"    + 'SCOPE RULES:\\n{scope_rules}',\n"
        + f"    {{ label: 'gate{gate_num}-r' + round, phase: 'Gate {gate_num}', agentType: 'general-purpose' }},\n"
        + agent_close
        + f"  if (gate{gate_num}Report === null || gate{gate_num}Report === undefined || (typeof gate{gate_num}Report === 'string' && gate{gate_num}Report.length < 10)) {{\n"
        + f"    gate{gate_num}Blocked = true\n"
        + f"    log('  Gate {gate_num} agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')\n"
        + "    break\n"
        + "  }\n"
        + f"  const g{gate_num}v = await agent(\n"
        + "    'Run this ONE command via the Bash tool:\\n'\n"
        # code-review-graph is deliberately NOT in requirements.txt (conflicts
        # with semgrep's exceptiongroup pin under joint resolution — see
        # requirements.txt's comment); this is a standalone dispatch that
        # can't assume an earlier step in THIS session already installed it.
        # igraph must be installed alongside it — code-review-graph doesn't
        # declare igraph as a pip dependency, so without it CRG silently
        # degrades to a coarse directory-based grouping that scores
        # differently from CI (which always has igraph present first).
        # Round 47 站1: both pins are RENDERED, not typed. They used to be a
        # literal in this string, a second literal in the CI template and an
        # unpinned third in cli/project_cmds.py — and the CI template's own
        # :610-624 note records what a version disagreement costs (architecture
        # 66.7 in CI vs a committed baseline of 100.0, same commit).
        + f"    + '`pip install -q {_crg_standalone_specs()} >/dev/null 2>&1; ' + PY + ' ' + REPO + '/harness_cli.py verify-gate --project ' + REPO + ' --gate {gate_num} --phase {phase} --spec-threshold {d4_threshold}; echo \"RC=$?\"`\\n'\n"
        + f"    + 'It runs all three of Gate {gate_num}\\'s checks — state.json last_gate >= {gate_num}, spec-coverage, and the CRG architecture floor — and appends the verdict, with a digest of the tree it measured, to .methodology/gate_verify.jsonl. advance-phase re-derives that digest and refuses a phase whose exit gate has no matching PASS, so a verdict you did not actually produce cannot carry the phase.\\n'\n"
        + "    + 'Then report via the StructuredOutput tool: verify_rc = the exact numeric exit code echoed on the final RC= line; detail = the command\\'s last [verify-gate] line.',\n"
        + f"    {{ label: 'gate{gate_num}-verify-r' + round, phase: 'Gate {gate_num}', agentType: 'general-purpose', schema: GATE_VERIFY_SCHEMA }},\n"
        + "  )\n"
        + f"  gate{gate_num}Pass = !!(g{gate_num}v && g{gate_num}v.verify_rc === 0)\n"
        + f"  if (gate{gate_num}Pass) {{ log('  Gate {gate_num} PASS [harness-verified: verify-gate rc=0, verdict recorded in gate_verify.jsonl]'); break }}\n"
        + f"  log('  Gate {gate_num} not yet PASS [' + (g{gate_num}v ? String(g{gate_num}v.detail ?? '') : 'verify agent null') + '] — retry round ' + (round + 1))\n"
        + "}\n"
        + f"if (gate{gate_num}Blocked) {{\n"
        + f"  return {{ session_limit_blocked: true, gate: {gate_num}, message: 'Agent hit session/rate limit during Gate {gate_num} evaluation. Resume after quota reset — GUARD checks will skip completed FRs.' }}\n"
        + "}\n"
        + f"if (!gate{gate_num}Pass) {{\n"
        + deferred_fixes_step
        + f"  return halt('gate{gate_num}', {{ error: '{on_fail_error_msg}', raw: String(gate{gate_num}Report ?? '').slice(-600) }})\n"
        + "}\n"
    )


# ---------------------------------------------------------------------------
# A/B review machine (phase1/phase2/phase6 shared helpers — Round 11 station4)
#
# Confirmed by direct diff (not assumption) across all three hand-maintained
# files: balancedJsonAt/extractLastJson/parseAgentJson are already covered by
# render_json_utils() (js_src/json_utils.mjs, built station1 but unused until
# this station). safePrevB2 is byte-identical. makeDocSummary/loadFileViaPython/
# structuredBReview differ only in comments/defaults (cosmetic, canonicalized
# below). buildBPrompt and persistApproval have REAL differences, preserved as
# explicit parameters:
#   - phase1's buildBPrompt requires reason >=40 chars; phase2's requires
#     >=100 — verified against the actual harness code
#     (core/quality_gate/agent_b_approvals.py:21, MIN_REVIEW_REASON_CHARS=40).
#     phase2's "100" is a stale, stricter-than-necessary requirement that was
#     apparently never corrected after the harness's real minimum was set to
#     40 — flagged, not silently fixed (this station migrates, it doesn't
#     rewrite prompt policy).
#   - phase1's persistApproval uses a plain fallback reason; phase2's
#     synthesizes a >=100-char justification when B returns none (matching
#     phase2's own, stricter, self-imposed 100-char belief). phase6's
#     writeApprovalJson matches phase1's plain fallback, not phase2's.
#   - phase1 has an extra scopeRules() text-builder helper phase2 doesn't
#     (phase2 inlines its SCOPE RULES text per-prompt) — rendered only where
#     the phase's original file actually defines it.
#
# hasHighGap() and summarizeVerify() are dropped: both are confirmed dead in
# every hand-maintained file (defined, zero callers) — hasHighGap's entire
# body is an unconditional throw whose own comment says it exists only "so
# the git diff clearly shows what was replaced"; that diff already happened
# in this repo's history and doesn't need perpetuating into every future
# regeneration.
# ---------------------------------------------------------------------------


def render_safe_prev_b2() -> str:
    return (
        "// ---- safePrevB2: strip prev-round `reason` to defeat premise persistence ----\n"
        "function safePrevB2(prevB2) {\n"
        "  if (!prevB2) return null\n"
        "  return {\n"
        "    review_status: prevB2.review_status,\n"
        "    gaps: Array.isArray(prevB2.gaps) ? prevB2.gaps : [],\n"
        "  }\n"
        "}\n"
    )


def render_make_doc_summary() -> str:
    return (
        "// ---- makeDocSummary: collapse full content → headings + counts ----\n"
        "// Used for APPROVED upstream docs (B does not re-review them; they're context\n"
        "// for the deliverable under review). Trims ~95% of token volume while\n"
        "// preserving the structural skeleton B needs to orient.\n"
        "function makeDocSummary(content, opts) {\n"
        "  opts = opts || {}\n"
        "  const lines = content.split('\\n')\n"
        "  const headings = []\n"
        "  for (const ln of lines) {\n"
        "    const m = ln.match(/^(#{1,6})\\s+(.+?)\\s*$/)\n"
        "    if (m) headings.push(m[2].slice(0, 80))\n"
        "  }\n"
        "  const summary = {\n"
        "    line_count: lines.length,\n"
        "    char_count: content.length,\n"
        "    headings: headings.slice(0, 40),\n"
        "  }\n"
        "  if (opts.includeFirstLines) {\n"
        "    summary.first_3_lines = lines.slice(0, 3).map(l => l.slice(0, 120))\n"
        "  }\n"
        "  return JSON.stringify(summary, null, 2)\n"
        "}\n"
    )


def render_scope_rules() -> str:
    """phase1-only text-builder (phase2 inlines its SCOPE RULES text
    per-prompt instead) — only injected into phase1's generated file."""
    return (
        "// ---- SCOPE RULES template (playbook §7.3) ----\n"
        "function scopeRules(singleDeliverable, prevDeliverables) {\n"
        "  let p = '\\n\\nSCOPE RULES (you MUST obey):\\n'\n"
        "  p += '- DO NOT write any deliverable OTHER than ' + singleDeliverable + '.\\n'\n"
        "  if (prevDeliverables && prevDeliverables.length > 0) {\n"
        "    p += '- DO NOT modify ' + prevDeliverables.join(', ') + ' (already APPROVED).\\n'\n"
        "  }\n"
        "  p += '- DO NOT run git commit, git push, advance-phase, push-checkpoint, or any phase-transition command.\\n'\n"
        "  p += '- DO NOT run constitution-check, peer-review, or any quality-gate command.\\n'\n"
        "  p += '- DO NOT spawn other agents or do the work of downstream sub-tasks.\\n'\n"
        "  p += '- ONLY do the steps above. Return the compact JSON when done.'\n"
        "  return p\n"
        "}\n"
    )


def render_citation_contract_line() -> str:
    """Citation format rule shared by render_build_b_prompt (P1/P2) and
    Phase 6's inline peer-review verdicts. Single source of truth so a
    future format change touches one place.

    The rule has three parts:
      1. Positive example (so Agent B has a concrete shape to copy).
      2. Negative example + "must be DIGITS after `:`" rule (so Agent B
         does not write prose-shaped citations like
         `taskq_api.app:app aligns with §X.Y` — observed 2026-08-14
         P2 ADR.md crash; the harness validator correctly rejected it).
      3. wc -l reminder so the cited line is in range.

    Returns a single JS source-code line shaped like the surrounding
    `    + '...\\n'\\n` entries — drop-in replacement for the inline
    citation rule string in render_build_b_prompt.
    """
    return (
        "    + '  - `citations`: array of \"file:line\" strings. CITATION FORMAT — each entry MUST be exactly `<rel_path>:<digits>` (a line number you verified by `Read` or `cat <path> | head -N | tail -1`), with an optional trailing `(parenthesised annotation)`. Positive examples: `\"SRS.md:42\"` or `\"02-architecture/SAD.md:118\"`. Negative example — DO NOT WRITE: `\"taskq_api.app:app aligns with §X.Y\"` (or any string where the part after `:` is prose): the validator regex (harness `core/quality_gate/agent_b_approvals.py` `_CITATION`) requires DIGITS after `:`; prose is rejected with `(unparseable citation format)`. Always run `wc -l <path>` first so the line number is in range.\\n'\n"
    )


def render_build_b_prompt(
    *,
    min_reason_chars: int,
    docs_embedded_note: str,
    critical_docs_note: str,
    evidence_type_note: str,
) -> str:
    """buildBPrompt: the 3-layer B-review defense prompt builder. Real,
    verified differences between phase1/phase2 preserved as parameters (see
    module-level comment above) rather than guessed at — NOT the phase-
    specific per-sub-task docs/checklist content, which stays in phase_specs.py.
    `docs_embedded_note` was caught by a direct diff AFTER this function was
    first written (its initial version silently dropped the phase-specific
    basename examples — "SAD.md/ADR.md/TEST_SPEC.md" for phase2, "SRS.md/
    TEST_INVENTORY.yaml" + an extra "NOT descriptive strings like ..." clause
    for phase1); re-verified against both real files before wiring any spec.
    """
    return (
        "// ---- B prompt builder (3-layer B-review defense, T1-B) ----\n"
        "//\n"
        "// Correct B-review architecture has three layers:\n"
        "//   Layer 1 — B agent orientation: SUMMARY via makeDocSummary(); B Bash-cats\n"
        "//   full file for any citation (playbook §8.2).\n"
        "//   Layer 2 — Deterministic gap verification (harness): structured_b_review.py\n"
        "//   --doc-content reads the deliverable file directly; b_gap_validator checks\n"
        "//   each gap's terms against actual file content — no LLM involved.\n"
        "//   Layer 3 — Escalation (harness): enforce_escalation computes the round-loop\n"
        "//   verdict AFTER Layer 2 has corrected gap severities.\n"
        "function buildBPrompt(role, deliverable, docs, checklist) {\n"
        "  let p = 'You are ' + role + '. Your task: review the following deliverable (' + deliverable + ').\\n'\n"
        "    + 'You have FULL access to Bash and Read tools — USE THEM to cat/Read the\\n'\n"
        "    + 'freshest version of every file you cite. The DOC blocks below are a SUMMARY\\n'\n"
        "    + 'snapshot for orientation; for any citation file:line, you MUST re-read that\\n'\n"
        "    + 'file via Read/Bash first. Do NOT extend any prior round\\'s `reason` verbatim\\n'\n"
        "    + 'into your own reasoning — read disk, then judge.\\n\\n'\n"
        "  for (let i = 0; i < docs.length; i++) p += '=== [' + docs[i][0] + '] ===\\n' + docs[i][1] + '\\n\\n'\n"
        "  p += 'Review checklist:\\n' + checklist + '\\n\\n'\n"
        "    + 'SCHEMA REQUIREMENTS (advance-phase `harness_cli.py _verify_agent_b_approvals_core` REJECTS the approval if any of these fail — observed 2026-06-29 wf_3a9377cb):\\n'\n"
        f"    + '  - `reason`: ≥ {min_reason_chars} characters of substantive justification. NOT \"APPROVE\", \"OK\", or other one-word response.\\n'\n"
        + render_citation_contract_line()
        + "    + '  - For range citations `path:N-M`, the end line M MUST NOT exceed the file\\'s actual line count (verify via `wc -l <path>` before writing). Off-by-one errors in range citations are a known failure mode that blocks advance-phase with no automated remediation.\\n'\n"
        f"    + '  - `docs_embedded`: array of file paths/identifiers you actually read during this review. CRITICAL — the harness basename-matcher (advance-phase `_norm()`) {docs_embedded_note}\\n'\n"
        f"    + '  - CRITICAL: {critical_docs_note}\\n\\n'\n"
        "    + 'Return JSON only (no markdown fences, no commentary). Schema (harness b_review.schema.json):\\n'\n"
        f"    + '{{\"review_status\":\"APPROVE\"|\"REJECT\"|\"CANCELLED\",\"reason\":\"<≥{min_reason_chars} chars>\",\"citations\":[\"file:line\"],\"docs_embedded\":[\"...\"],\"gaps\":[{{\"severity\":\"low|medium|high\",\"evidence_type\":\"real_invention|over_interpretation|methodology_artifact\",\"canonical_ref\":\"<file:line or section ID>\",\"message\":\"...\",\"fr_id\":\"<FR-XX or null>\"}}]}}\\n'\n"
        f"    + 'evidence_type tells the framework which fix strategy to dispatch. {evidence_type_note}\\n\\n'\n"
        "    + 'IMPORTANT: Return ONLY the JSON object as your final message. No prose before or after.'\n"
        "  return p\n"
        "}\n"
    )


def render_structured_b_review(*, default_phase_num: int) -> str:
    return (
        "// ---- structuredBReview (T1-B) — harness-owned B-2 round-loop control ----\n"
        "//\n"
        "// Replaces hasHighGap() (hand-rolled gap-severity gating), runBSelfVerify()\n"
        "// (second LLM re-checking the first LLM's claims), and the VETO guard (that\n"
        "// let the second LLM's self-reported confidence silently flip REJECT→APPROVE).\n"
        "//\n"
        "// Calls structured_b_review.py, which:\n"
        "//   1. Extracts JSON from B's raw free-text output\n"
        "//   2. Validates against b_review.schema.json\n"
        "//   3. Applies downgrade rules (_downgrade_over_interpretation)\n"
        "//   4. Runs deterministic gap/reason/citation verification (b_gap_validator)\n"
        "//   5. Computes EscalationAction (approve|retry|escalate_human) via enforce_escalation\n"
        "//\n"
        "// Returns { b2, escalation_action, escalation_reason, review_out } where b2\n"
        "// is the normalized (gap-severity-verified) B-2 dict — workflow JS branches\n"
        "// on escalation_action.\n"
        "async function structuredBReview(bRawText, round, maxRounds, delivPath, phaseNum) {\n"
        f"  const rawFile = '/tmp/sbr_raw_' + (phaseNum || {default_phase_num}) + '_r' + round + '.txt'\n"
        f"  const jsonFile = '/tmp/sbr_out_' + (phaseNum || {default_phase_num}) + '_r' + round + '.json'\n"
        "  const delivFlag = delivPath ? ' --doc-content ' + REPO + '/' + delivPath : ''\n"
        f"  const phaseFlag = ' --phase ' + (phaseNum || {default_phase_num})\n"
        "  const sbrCmd = PY + ' ' + REPO + '/harness/scripts/structured_b_review.py'\n"
        "    + ' --raw-text ' + rawFile\n"
        "    + ' --round ' + round\n"
        "    + ' --max-rounds ' + maxRounds\n"
        "    + ' --json-out ' + jsonFile\n"
        "    + phaseFlag\n"
        "    + delivFlag\n"
        "    + ' --quiet'\n"
        "\n"
        "  const reviewAgent = await agent(\n"
        "    'YOU ARE A DETERMINISTIC B-REVIEW VALIDATOR. Run these steps in order via Bash.\\n'\n"
        "    + '1. Write the raw B-review text to a file (heredoc — verbatim, no modification):\\n'\n"
        "    + '   cat > ' + rawFile + \" <<'HEREDOC_END'\\n\" + bRawText + '\\nHEREDOC_END\\n'\n"
        "    + '2. Run: `' + sbrCmd + '`\\n'\n"
        "    + '3. Read the output: `cat ' + jsonFile + '`\\n'\n"
        "    + 'Return the verbatim cat output as your final message — no commentary.',\n"
        f"    {{ label: 'sbr-' + (phaseNum || {default_phase_num}) + '-r' + round, phase: 'B Review', agentType: 'general-purpose' }},\n"
        "  )\n"
        "  let reviewOut = null\n"
        "  try {\n"
        "    reviewOut = extractLastJson(reviewAgent)\n"
        "  } catch (_) { /* fall through — escalate if unparseable */ }\n"
        "  if (!reviewOut) {\n"
        "    return { b2: null, escalation_action: 'retry', escalation_reason: 'structured_b_review.py output unparseable', review_out: String(reviewAgent ?? '').slice(0, 200) }\n"
        "  }\n"
        "\n"
        "  // structured_b_review.py's own `out` dict does NOT forward reason/citations/\n"
        "  // docs_embedded (only status/review_status/gaps/diagnostic/b2_verification/\n"
        "  // escalation_*) — re-extract them from B's raw text directly, or every\n"
        "  // approval persisted from this object would carry empty citations/\n"
        "  // docs_embedded and advance-phase's _verify_agent_b_approvals_core would\n"
        "  // reject it unconditionally (confirmed 2026-07-14 2nd-round audit).\n"
        "  let b2 = null\n"
        "  try {\n"
        "    const rawB = extractLastJson(bRawText) || {}\n"
        "    b2 = {\n"
        "      review_status: reviewOut.review_status,\n"
        "      gaps: reviewOut.gaps || [],\n"
        "      reason: reviewOut.review_status === 'CANCELLED' ? (reviewOut.diagnostic || '') : (rawB.reason || ''),\n"
        "      citations: Array.isArray(rawB.citations) ? rawB.citations : [],\n"
        "      docs_embedded: Array.isArray(rawB.docs_embedded) ? rawB.docs_embedded : [],\n"
        "      verify: reviewOut.b2_verification || null,\n"
        "    }\n"
        "  } catch (_) { b2 = null }\n"
        "\n"
        "  return {\n"
        "    b2: b2,\n"
        "    escalation_action: reviewOut.escalation_action || 'retry',\n"
        "    escalation_reason: reviewOut.escalation_reason || '',\n"
        "    review_out: reviewOut,\n"
        "  }\n"
        "}\n"
    )


def render_persist_approval(
    *,
    synthesize_reason: bool,
    use_schema_verdict: bool = False,
    label_prefix: str = "persist",
    phase_label: str = "Persist Approval",
) -> str:
    """persistApproval (phase1/phase2 name) / writeApprovalJson (phase6 name,
    same body shape) — writes .methodology/agent_b_approvals/<id>.json via
    harness_cli.py write-approval. `synthesize_reason=True` reproduces
    phase2's exact >=100-char-padding fallback (verified NOT required by the
    actual harness minimum of 40 — see module docstring — but preserved
    verbatim since equivalence migration doesn't rewrite prompt/logic policy);
    phase1 and phase6 both use the plain fallback (synthesize_reason=False).

    `use_schema_verdict=True` forces the sub-agent through the
    StructuredOutput tool (schema: VERDICT_SCHEMA) instead of accepting a
    free-text reply — this is what stops a distracted sub-agent (e.g. one
    whose context got an injected MCP-server system-reminder) from just
    replying "Acknowledged, no further action" and having that count as its
    final turn. It does NOT trust the schema's self-reported `pass` boolean:
    success is decided by regex-matching the literal `[write-approval] OK`
    string inside `res.reason` (which the prompt requires to be the verbatim
    CLI stdout tail). Trusting a bare self-reported `pass` was tried and
    reverted elsewhere in this module after it proved unsafe — see
    `gate1-verify-`'s registry note in test_workflow_dispatch_registry.py
    ("schema pass ignored — wf_53d055ce-d0b class closed"); this function
    applies that same already-hardened pattern rather than reintroducing the
    closed bug class. All three callers (phase1, phase2, phase6) now share
    this one verdict path via use_schema_verdict=True — run-all.js crash
    2026-08-01 (persistApproval regex-on-free-text false negative against a
    real write-approval success) was phase1/phase2's `False` branch; fixed
    by moving them onto phase6's already-schema'd transport instead of
    inventing a new one. `label_prefix`/`phase_label` default to phase1/2's
    own values; phase6 passes its own (`write-approval` / `Peer Review` — it
    has no dedicated "Persist Approval" phase box, calling this only from
    within Peer Review) so no agent() label changes and no rename entry is
    needed for phase6's migration."""
    reason_block = (
        "  const rawReason = String(b2.reason ?? '').trim()\n"
        "  const synthReason = 'Agent B approved ' + deliverableId + ' (review_status=' + (b2.review_status ?? 'APPROVE')\n"
        "    + '); the reviewer returned no substantive reason text, so the workflow synthesized this justification to satisfy the harness _verify_agent_b_approvals_core minimum-length (100 char) contract.'\n"
        "  const reason = (rawReason.length >= 100 ? rawReason : (rawReason ? rawReason + ' — ' + synthReason : synthReason)).slice(0, 800)\n"
        if synthesize_reason else ""
    )
    reason_field = (
        "reason: reason,"
        if synthesize_reason else
        "reason: (b2.reason ?? ('Approved ' + deliverableId + ' (reason omitted)')).slice(0, 800),"
    )
    if use_schema_verdict:
        agent_call = (
            "      res = await agent(\n"
            "        (attempt === 1\n"
            "          ? 'You are a SHELL WRAPPER AGENT. Run EXACTLY this Bash command:\\n\\n' + cmd + '\\n\\nThen report via the StructuredOutput tool: pass = true ONLY if stdout contains `[write-approval] OK`; reason = the verbatim stdout tail. No other tool calls.'\n"
            "          : 'You are a SHELL WRAPPER AGENT (retry ' + attempt + '/' + MAX_OUTER_ATTEMPTS + '). Previous attempt stderr:\\n' + (lastErr ?? '(none)') + '\\n\\nIf stderr contains `BLOCKED: citation(s) do not resolve`, the cited range end exceeds the file length; the orchestrator must re-dispatch Agent B with the cited file path and a reminder to run `wc -l <path>` before writing the citation. Report stderr verbatim via StructuredOutput reason. Then run:\\n\\n' + cmd + '\\n\\nReport via StructuredOutput: pass = true ONLY if stdout contains `[write-approval] OK`.'\n"
            "        ),\n"
            f"        {{ label: '{label_prefix}-' + deliverableId + '-try' + attempt, phase: '{phase_label}', agentType: 'general-purpose', schema: VERDICT_SCHEMA }},\n"
            "      )\n"
        )
        success_check = (
            # v33: verdict comes from the canonical `[write-approval] OK`
            # string transcribed into res.reason, not the self-reported
            # res.pass boolean — same class of bug as gate1-verify- closed
            # (wf_53d055ce-d0b): a schema-forced tool call still lets the
            # LLM mis-set a bare boolean, but it cannot fabricate the exact
            # bytes the CLI printed without actually transcribing them.
            "    if (res && /\\[write-approval\\]\\s*OK/.test(String(res.reason || ''))) {\n"
            "      log('  persisted approval: ' + deliverableId + ' (attempt ' + attempt + '/' + MAX_OUTER_ATTEMPTS + ')')\n"
            "      return\n"
            "    }\n"
            "    lastErr = 'CLI did not return OK; got: ' + (res ? String(res.reason ?? '').slice(0, 400) : 'agent returned null')\n"
        )
    else:
        agent_call = (
            "      res = await agent(\n"
            "        'You are a SHELL WRAPPER AGENT. Run EXACTLY this Bash command and emit stdout + exit code verbatim:\\n\\n' + cmd + '\\n\\nNo commentary, no preamble, no other tool calls.',\n"
            f"        {{ label: '{label_prefix}-' + deliverableId + '-try' + attempt, phase: '{phase_label}', agentType: 'general-purpose' }},\n"
            "      )\n"
        )
        success_check = (
            "    if (typeof res === 'string' && /\\[write-approval\\]\\s*OK/.test(res)) {\n"
            "      log('  persisted approval: ' + deliverableId + ' (attempt ' + attempt + '/' + MAX_OUTER_ATTEMPTS + ')')\n"
            "      return\n"
            "    }\n"
            "    lastErr = 'CLI did not return OK; got: ' + String(res).slice(0, 400)\n"
        )
    return (
        "// ---- persistApproval: write .methodology/agent_b_approvals/<id>.json ----\n"
        "// v22 single-line Bash + harness_cli.py write-approval (proven 6/6 advance-\n"
        "// phase PASS) + workflow JS outer-level try/catch retry.\n"
        "async function persistApproval(deliverableId, b2) {\n"
        "  // v31: SINGLE-LINE JSON (no indent) — multi-line indented JSON gets\n"
        "  // word-split by shell when the LLM agent emits the command without\n"
        "  // single-quoting the JSON payload, breaking `--json` argparse.\n"
        + reason_block
        + "  const approvalPayload = JSON.stringify({\n"
        "    fr: deliverableId,\n"
        "    review_status: b2.review_status ?? 'APPROVE',\n"
        f"    {reason_field}\n"
        "    citations: Array.isArray(b2.citations) ? b2.citations.slice(0, 20) : [],\n"
        "    docs_embedded: Array.isArray(b2.docs_embedded) ? b2.docs_embedded : [],\n"
        "    confidence: typeof b2.confidence === 'number' ? b2.confidence : 0.9,\n"
        "  })\n"
        "  const cliPath = REPO + '/harness/harness_cli.py'\n"
        "  // v31: explicit single-quote wrap around the JSON payload (zsh glob safety —\n"
        "  // zsh interprets `[...]` in unquoted strings as glob patterns, and JSON\n"
        "  // arrays + file:line citations are full of them).\n"
        "  const escapedPayload = approvalPayload.replace(/'/g, \"'\\\\''\")\n"
        "  const cmd = PY + ' ' + cliPath + ' write-approval --project ' + REPO +\n"
        "    ' --fr-id ' + JSON.stringify(deliverableId) + \" --json '\" + escapedPayload + \"'\"\n"
        "\n"
        "  let lastErr = null\n"
        "  for (let attempt = 1; attempt <= MAX_OUTER_ATTEMPTS; attempt++) {\n"
        "    let res\n"
        "    try {\n"
        + agent_call
        + "    } catch (e) {\n"
        "      lastErr = 'agent() threw: ' + (e && e.message ? e.message : String(e))\n"
        "      log('  persistApproval ' + deliverableId + ' attempt ' + attempt + '/' + MAX_OUTER_ATTEMPTS + ': ' + lastErr.slice(0, 200))\n"
        "      continue\n"
        "    }\n"
        + success_check
        + "    log('  persistApproval ' + deliverableId + ' attempt ' + attempt + '/' + MAX_OUTER_ATTEMPTS + ': ' + lastErr)\n"
        "  }\n"
        "  throw new Error('persistApproval FAILED for ' + deliverableId + ' after ' + MAX_OUTER_ATTEMPTS + ' attempts. Last error: ' + lastErr)\n"
        "}\n"
    )


def render_anchor_check() -> str:
    """`js_src/anchor_check.mjs` with `export` stripped, for inlining.

    Same one-file-two-consumers arrangement as render_json_utils(): `node
    --test scripts/workflowgen/js_src/` runs the module directly, and the
    generated workflows carry this exact source. Before Round 34 the rule was
    a regex literal inside the string below, which meant the only way to test
    it was to grep the generated file — and a grep cannot tell a rule from a
    sentence about a rule.
    """
    src = (_JS_SRC_DIR / "anchor_check.mjs").read_text(encoding="utf-8")
    first_export = _EXPORT_RE.search(src)
    body = src[first_export.start():] if first_export else src
    return _EXPORT_RE.sub("", body)


def render_load_file_via_python() -> str:
    return (
        render_anchor_check()
        + "\n"
        "// ---- loadFileViaPython: deterministic Bash + harness_cli.py read-file (v33) ----\n"
        "// Drops the v29 MCP read path (failed at large-context stages) in favour of a\n"
        "// single-step Bash tool-call running the deterministic `harness_cli.py\n"
        "// read-file` + `cat` relay, which does not depend on an MCP server in a\n"
        "// headless run. read-file's prefix check is a first-line startswith() (file_\n"
        "// loader Bug v8 guard), so all expectPrefix values passed in must lead with \"#\".\n"
        "async function loadFileViaPython(relPath, expectPrefix, phaseName, opts) {\n"
        "  opts = opts || {}\n"
        "  const maxAttempts = opts.maxAttempts || 3\n"
        "  const filePath = REPO + '/' + relPath\n"
        "  const expectPrefixArg = expectPrefix ? ' --expect-prefix ' + JSON.stringify(expectPrefix) : ''\n"
        "  const safeName = relPath.replace(/[\\/.]/g, '_')\n"
        "  const contentOut = '/tmp/load_' + safeName + '.txt'\n"
        "  const jsonOut = '/tmp/load_' + safeName + '.json'\n"
        "  const pythonCmd = PY + ' ' + REPO + '/harness_cli.py read-file --file ' + JSON.stringify(filePath)\n"
        "    + expectPrefixArg + ' --content --content-out ' + contentOut + ' --json-out ' + jsonOut + ' --quiet'\n"
        "\n"
        "  const prompt = 'You are a SHELL WRAPPER AGENT. Your ONLY job is to run ONE shell command and emit ONE file content verbatim.\\n\\n'\n"
        "    + 'STEPS (DO NOT DEVIATE):\\n'\n"
        "    + '1. Use the Bash tool to run EXACTLY this command (no modifications):\\n'\n"
        "    + '   ' + pythonCmd + '\\n\\n'\n"
        "    + '2. Use the Bash tool to run `cat ' + contentOut + '` — read the content file from disk.\\n\\n'\n"
        "    + '3. Your final assistant message = the EXACT output of `cat ' + contentOut + '` (verbatim bytes).\\n\\n'\n"
        "    + 'CRITICAL OUTPUT RULES (violations = failure):\\n'\n"
        "    + '- DO NOT generate or paraphrase content based on your memory/inference.\\n'\n"
        "    + '- ALWAYS read the actual file from disk. NEVER hallucinate file content.\\n'\n"
        "    + '- DO NOT echo the JSON file. Only echo the content file.\\n'\n"
        "    + '- DO NOT write any preamble or acknowledgment.\\n'\n"
        "    + '- DO NOT add commentary, summary, or explanation.\\n'\n"
        "    + '- Your final message = the verbatim cat output only.\\n'\n"
        "    + '- If the command fails, return EXACTLY: ERROR_LOAD_FAILED: ' + filePath\n"
        "\n"
        "  let lastFailReason = 'unknown'\n"
        "  for (let attempt = 1; attempt <= maxAttempts; attempt++) {\n"
        "    let res\n"
        "    try {\n"
        "      res = await agent(prompt, {\n"
        "        label: 'loadpy-' + relPath.replace(/[\\/.]/g, '-') + '-a' + attempt,\n"
        "        phase: phaseName,\n"
        "        agentType: 'general-purpose',\n"
        "      })\n"
        "    } catch (e) {\n"
        "      lastFailReason = 'agent_threw: ' + (e && e.message ? e.message : String(e)).slice(0, 80)\n"
        "      log('  [' + relPath + '] attempt ' + attempt + '/' + maxAttempts + ' agent() threw: ' + (e && e.message ? e.message : String(e)).slice(0, 200))\n"
        "      continue\n"
        "    }\n"
        "    const rawText = (typeof res === 'string' ? res : String(res ?? '')).trim()\n"
        "    // sub-agent runtime sometimes emits a literal <think>...</think> preamble\n"
        "    // merged into the same line as the real content (no newline in between),\n"
        "    // which pushes the anchor off the start of the first line even though the\n"
        "    // agent DID read the correct file. Strip it before validating. A <think>\n"
        "    // block on its own line is NOT stripped and NOT accepted: that is an\n"
        "    // unfaithful relay, which is what firstLineHasAnchor is here to catch.\n"
        "    const text = rawText.replace(/^\\s*<think>[\\s\\S]*?<\\/think>\\s*/, '')\n"
        "    if (text.startsWith('ERROR_LOAD_FAILED')) {\n"
        "      lastFailReason = 'ERROR_LOAD_FAILED'\n"
        "      log('  [' + relPath + '] attempt ' + attempt + '/' + maxAttempts + ' ERROR_LOAD_FAILED')\n"
        "      continue\n"
        "    }\n"
        "    if (text.length < 50) {\n"
        "      lastFailReason = 'too_short(len=' + text.length + '): ' + text.slice(0, 60)\n"
        "      log('  [' + relPath + '] attempt ' + attempt + '/' + maxAttempts + ' too short (len=' + text.length + ')')\n"
        "      continue\n"
        "    }\n"
        "    if (expectPrefix && !firstLineHasAnchor(text, expectPrefix)) {\n"
        "      lastFailReason = 'prefix_mismatch: got=' + text.slice(0, 40)\n"
        "      log('  [' + relPath + '] attempt ' + attempt + '/' + maxAttempts + ' content-prefix-mismatch (expected first line to start with \"' + expectPrefix + '\", got: ' + text.slice(0, 80) + ')')\n"
        "      continue\n"
        "    }\n"
        "    return text\n"
        "  }\n"
        "  return 'ERROR: LOADER_FAILED_AFTER_' + maxAttempts + '_ATTEMPTS: ' + relPath + ' (last: ' + lastFailReason + ')'\n"
        "}\n"
    )


def render_generic_ab_loop(*, b_role: str, phase_num: int) -> str:
    """phase2's abLoop — NOT shared with phase1's runSubTask. Initially
    assumed unifiable (docstring said so in an earlier draft), but a full
    line-by-line diff against phase1's real runSubTask (Round 11 station4)
    found more than the two JS-internal conventions first assumed: cfg field
    names differ (idx/name/bChecklist vs key/deliverable/checklist),
    buildBDocs is called `await cfg.buildBDocs(round, content, b2)` in
    phase1 (one implementation, srsBDocs, is genuinely async) vs sync
    `cfg.buildBDocs(content)` in phase2, phase1 has two extra log() calls
    phase2 doesn't, and phase1's round-exhausted return additionally omits
    `ok`. Forcing both into one function would need a field-name-mapping
    parameter plus two more behavior toggles — the wrong abstraction for two
    call sites. phase1's runSubTask is instead rendered as phase1-specific
    verbatim text in phase_specs.py. This function renders ONLY phase2's
    shape. `b_role` is fixed to phase2's 'TECH_LEAD' (never varies per sub-
    task) and `phase_num` selects the structuredBReview phase flag (always 2
    for this function's only caller) — both generator-time parameters.
    cfg = { phaseName, key, deliverable, diskPath, diskPrefix,
            buildAPrompt(round, prevB2), buildBDocs(content), checklist }
    """
    return (
        "// ---- Generic A/B loop (returns {ok,content,b2} or {error,...} — caller propagates) ----\n"
        "async function abLoop(cfg) {\n"
        "  phase(cfg.phaseName)\n"
        "  log(cfg.deliverable + ': A/B loop (max ' + MAX_B_ROUNDS + ' rounds)')\n"
        "  let content = '', b2 = null\n"
        "  for (let round = 1; round <= MAX_B_ROUNDS; round++) {\n"
        "    log('  --- ' + cfg.deliverable + ' round ' + round + '/' + MAX_B_ROUNDS + ' ---')\n"
        "    if (typeof budget !== 'undefined' && budget.remaining && budget.remaining() < 50000) {\n"
        "      const rem = Math.round((budget.remaining() || 0) / 1000)\n"
        "      log('  BUDGET LOW (' + rem + 'k) -- exiting ' + cfg.deliverable)\n"
        "      if (b2 && b2.review_status === 'APPROVE') return { ok: true, content, b2, budget_exhausted: true }\n"
        "      if (b2) return { ok: false, content, b2, budget_exhausted: true }\n"
        "      return halt('budget-exhausted', { error: 'Budget exhausted during ' + cfg.deliverable, budget_exhausted: true })\n"
        "    }\n"
        "    let aResult\n"
        "    try { aResult = await agent(cfg.buildAPrompt(round, b2), {\n"
        "      label: 'a-' + cfg.key + '-r' + round, phase: cfg.phaseName, agentType: 'general-purpose',\n"
        "    }) } catch (e) {\n"
        "      if (round === MAX_B_ROUNDS) return halt('sbr-a-review', { error: cfg.deliverable + ' A agent failed at max rounds', detail: String(e.message ?? e).slice(0, 200) })\n"
        "      log('  A agent failed: ' + String(e.message ?? e).slice(0, 80) + ' -- retrying'); continue\n"
        "    }\n"
        "    let a\n"
        "    try { a = parseAgentJson(aResult, 'A-' + cfg.key + '-r' + round) }\n"
        "    catch (e) { log('  A JSON parse fail (likely truncated): ' + e.message.slice(0, 80)); a = null }\n"
        "    content = await loadFileViaPython(cfg.diskPath, cfg.diskPrefix || '', cfg.phaseName)\n"
        "    if (content.startsWith('ERROR:') || content.length < 50) {\n"
        "      if (round === MAX_B_ROUNDS) return halt('sbr-deliverable-missing', { error: cfg.deliverable + ' not found on disk after A — exhausted ' + MAX_B_ROUNDS + ' rounds', loader_preview: content.slice(0, 200) })\n"
        "      log('  A disk empty (parse-fail + no file) → retrying next round')\n"
        "      continue\n"
        "    }\n"
        "    log('  A status=' + (a && a.status ? a.status : 'assumed-OK') + ' | disk loaded: ' + content.length + ' chars, confidence=' + (a && a.confidence ? a.confidence : '?'))\n"
        "\n"
        "    let bResult\n"
        "    // v33b (parity with spec_phase1.py:245): when the previous round's\n"
        "      // persistApproval threw (validator rejected citations), carry the\n"
        "      // error message into the next round's B prompt so Agent B can\n"
        "      // self-correct instead of looping on identical input.\n"
        f"    const _baseBPrompt = buildBPrompt('{b_role}', cfg.deliverable, cfg.buildBDocs(content), cfg.checklist)\n"
        "      + (b2 && b2.persist_error ? '\\n\\n=== PREVIOUS ROUND CITE REJECT ===\\n' + b2.persist_error + '\\nRe-read each cited file with `wc -l <path>` BEFORE writing citations. Each citation MUST be exactly `<rel_path>:<digits>` (or `path:N-M` where M ≤ `wc -l <path>`). DO NOT cite prose like `taskq_api.app:app aligns with §X.Y` — the validator requires digits after `:`.\\n' : '')\n"
        "    try { bResult = await agent(_baseBPrompt, {\n"
        "      label: 'b-' + cfg.key + '-r' + round, phase: cfg.phaseName, agentType: 'general-purpose',\n"
        "    }) } catch (e) {\n"
        "      if (round === MAX_B_ROUNDS) return halt('sbr-b-review', { error: cfg.deliverable + ' B agent failed at max rounds', detail: String(e.message ?? e).slice(0, 200) })\n"
        "      log('  B agent failed: ' + String(e.message ?? e).slice(0, 80) + ' -- retrying'); continue\n"
        "    }\n"
        "\n"
        "    // --- structured_b_review (T1-B: harness-owned B-2 validation + escalation) ---\n"
        "    const sbrResult = await structuredBReview(\n"
        f"      bResult, round, MAX_B_ROUNDS, cfg.diskPath, {phase_num},\n"
        "    )\n"
        "    b2 = sbrResult.b2 || parseAgentJson(bResult, 'B-' + cfg.key + '-r' + round)\n"
        "    log('  B-2: ' + (b2 ? b2.review_status : '(none)')\n"
        "      + ' | gaps=' + ((b2 ? b2.gaps : []) || []).length\n"
        "      + ' | escalation=' + sbrResult.escalation_action)\n"
        "\n"
        "    if (sbrResult.escalation_action === 'approve') {\n"
        "      log('  APPROVED')\n"
        "      // v33b (parity with spec_phase1.py:285-298): if persistApproval throws\n"
        "      // (cmd_write_approval rejected an unresolvable_citations / off-by-one\n"
        "      // range via 3 outer retries), attach the error to b2.persist_error\n"
        "      // and re-enter the round loop so the next Agent B can self-correct.\n"
        "      // The round-MAX_B_ROUNDS HR-12 escalation below still fires if it\n"
        "      // never converges. Without this, Phase 2 abLoop crashes the whole\n"
        "      // workflow on the first off-shape citation Agent B writes\n"
        "      // (observed 2026-08-14 P2 ADR.md: 'taskq_api.app:app aligns with\n"
        "      // SAD §1.2' — validator rejected, persistApproval 3× retry, throw,\n"
        "      // halt).\n"
        "      let persistErr = null\n"
        "      try {\n"
        "        await persistApproval(cfg.deliverable, b2)\n"
        "      } catch (e) {\n"
        "        persistErr = e\n"
        "      }\n"
        "      if (persistErr) {\n"
        "        b2.persist_error = String(persistErr.message ?? persistErr).slice(0, 400)\n"
        "        log('  Persist failed at round ' + round + ': ' + b2.persist_error)\n"
        "        if (round === MAX_B_ROUNDS) return halt('sbr-persist-rejected', { error: cfg.deliverable + ': persistApproval rejected after ' + MAX_B_ROUNDS + ' rounds (last: ' + b2.persist_error + ')', lastB2: b2 })\n"
        "        continue\n"
        "      }\n"
        "      return { ok: true, content, b2 }\n"
        "    }\n"
        "    if (sbrResult.escalation_action === 'escalate_human') {\n"
        "      log('  ESCALATE TO HUMAN — ' + sbrResult.escalation_reason)\n"
        "      return halt('sbr-escalation', { error: cfg.deliverable + ': ' + sbrResult.escalation_reason, lastB2: b2, escalation_action: 'escalate_human' })\n"
        "    }\n"
        "    if (round === MAX_B_ROUNDS) return halt('sbr-no-convergence', { error: cfg.deliverable + ': B did not converge in ' + MAX_B_ROUNDS + ' rounds (HR-12 escalation)', lastB2: b2 })\n"
        "    // APPROVE+high OR REJECT → A fixes next round\n"
        "  }\n"
        "  return halt('sbr-loop-exhausted', { error: cfg.deliverable + ' loop exhausted unexpectedly' })\n"
        "}\n"
    )
