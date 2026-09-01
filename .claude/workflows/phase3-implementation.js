// Phase 3 — Implementation (faithful to .methodology/phase3_plan.md v2.12.0)
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/phase_specs.py::generate_phase3() (+ js_blocks.py for
// the blocks shared across phase workflow files). Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write --phase 3
//
// Structure: FR-loop型 + Gate 2 exit. Script holds the per-FR loop (playbook
// "plan as code"): load fr_ids via an agent, then for each FR dispatch a
// narrow agent that runs the TDD chain (RED→MIRROR→GREEN→IMPROVE→GATE1).
// Milestone pushes are script-driven (≥1/3 → p3-mid; all done → p3-pre-gate2).
// Gate 2 is one orchestrator agent (run-gate → eval → finalize → D4 60%).
//
// Playbook lessons: NO import/fs/process, Bash for all harness CLI,
// SCOPE RULES per agent, PY = .venv/bin/python, scriptPath launch.
// v4 (2026-07-02): gate verdicts use FLAT schema: (playbook §5.2 rev) — the
// v2 blanket schema ban was itself a workaround that forced every gate onto
// regex-over-LLM-prose, the root cause of bugs #126/#134/#135/#136 and the
// ENV_CHECK_RC paraphrase false-negative. Flat 2-3 field schemas on bash-proxy
// agents are runtime-validated (AJV + 2 retries); complex nested schemas on
// heavy-cognition agents remain forbidden (that was the real v2 lesson).


export const meta = {
  name: 'phase3-implementation',
  description: 'Phase 3 Implementation — per-FR TDD (RED/GREEN/IMPROVE/GATE1) + milestones + Gate 2 exit (phase3_plan.md v2.12.0)',
  phases: [
    { title: 'Entry & Preflight' },
    { title: 'Env Check' },
    { title: 'Load FRs' },
    { title: 'Per-FR TDD' },
    { title: 'Milestones' },
    { title: 'Gate 2' },
    { title: 'Preview Next-Phase' },
    { title: 'Advance' },
    { title: 'Sync' },
  ],
}

// ── Round 28: top-level crash boundary ─────────────────────────────────
// The runtime does not catch anything; an uncaught throw ends the run with
// no result at all. Everything below runs inside this try so a failed
// dispatch becomes a structured return the operator can act on. Body is
// spliced verbatim (not re-indented) to keep it byte-identical to the
// generator output run-all inlines.
try {

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
  return '[BOOKKEEPING — not part of your task]\n'
    + 'Run this once via Bash, then continue with the task below:\n'
    + '  ' + PY + ' ' + REPO + '/harness_cli.py log-dispatch --project ' + REPO
    + ' --batch ' + JSON.stringify(batch) + '\n'
    + 'It records earlier dispatches in this run. If it fails, say so in one line and carry on.\n\n'
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
  const r = await dispatch(
    'You are the REPO RESOLVER. Find the project root by walking up from your current CWD until a directory contains BOTH `harness_cli.py` AND `.methodology/` AND is NOT a git submodule working tree.\n'
    + 'A git submodule working tree is detected by `[ -f .git ] && head -1 .git 2>/dev/null | grep -q "^gitdir: "` (the top-level `.git` is a FILE whose first line starts with `gitdir:`, pointing to `<parent>/.git/modules/<name>`). This is critical when the harness framework is checked out as a git submodule — the harness/ dir itself contains harness_cli.py AND .methodology/, so naive walk-up would stop there instead of the real project root.\n'
    + 'Run EXACTLY this command via Bash (single line, copy-paste verbatim):\n'
    + 'cd "$(pwd)"; while [ "$(pwd)" != "/" ] && ! { [ -f harness_cli.py ] && [ -d .methodology ] && ! { [ -f .git ] && head -1 .git 2>/dev/null | grep -q "^gitdir: "; }; }; do cd ..; done; '
    + 'if [ -f harness_cli.py ] && [ -d .methodology ] && ! { [ -f .git ] && head -1 .git 2>/dev/null | grep -q "^gitdir: "; }; then echo "REPO=$(pwd)"; else echo "REPO_NOT_FOUND cwd=$(pwd)"; fi\n'
    + 'Report the literal stdout as your final message (no commentary, no transformation).',
    { label: 'resolve-repo', agentType: 'general-purpose' }
  )
  const text = String(r ?? '').trim()
  const match = text.match(/REPO=(\/[A-Za-z0-9_.\/-]+)/)
  if (match && match[1].startsWith('/')) {
    log('  REPO: auto-detected via walk-up = ' + match[1])
    return match[1]
  }
  throw new Error('[workflow] REPO not auto-detected (resolver returned: "' + text.slice(0, 200) + '"). Pass args.repo = absolute path or run from inside the project repo.')
}
let REPO = await resolveRepo()
const PY = REPO + '/.venv/bin/python'
log('REPO = ' + REPO + ' | PY = ' + PY)
// v15: budget guard (Bug #3 — port from phase2-architecture)
if (typeof budget !== 'undefined' && budget.remaining && budget.remaining() < 200000) {
  log('WARNING: budget low (' + Math.round((budget.remaining() || 0) / 1000) + 'k remaining) — workflow may not complete')
}


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


// ---- Gate verdict schemas (flat, top-level consts — playbook §5.2/§5.3) ----
// Verdict authority rule: heavy orchestrator agents keep prose narrative;
// their PASS/FAIL is NEVER parsed from that prose. A separate bash-proxy
// agent reads the harness's own artifact (manifest quality_complete,
// state.json/git log, CLI exit code) and reports through the schema.
const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    pass: { type: 'boolean', description: 'true only if the command output proves PASS' },
    reason: { type: 'string', description: 'verbatim command output tail (or failure reason)' },
  },
  required: ['pass', 'reason'],
}
const RC_SCHEMA = {
  type: 'object',
  properties: { rc: { type: 'integer', description: 'exact numeric exit code of the command' } },
  required: ['rc'],
}
// Round 70 站3: a per-FR GATE1 / GATE1-DELTA report. Routing reads `rc`, never
// the prose; `final_line` is for the operator's log and nothing branches on it.
const FR_STEP_SCHEMA = {
  type: 'object',
  properties: {
    rc: { type: 'integer', description: 'exact exit code of run-fr-step, read off the last RC= line (-1 if it never finished)' },
    final_line: { type: 'string', description: 'one-line human summary of the outcome' },
  },
  required: ['rc'],
}
const ENV_CHECK_SCHEMA = {
  type: 'object',
  properties: {
    rc: { type: 'integer', description: 'exact numeric exit code parsed from the final RC= line in the envcheck log' },
    ready: { type: 'boolean', description: 'env_check_result.json ready flag cross-check (Bug #127 anti-fabrication)' },
  },
  required: ['rc', 'ready'],
}
const CTX_SCHEMA = {
  type: 'object',
  properties: {
    fr_ids: { type: 'array', items: { type: 'string' } },
    fr_count: { type: 'integer' },
    fr_titles: { type: 'object', additionalProperties: { type: 'string' } },
  },
  required: ['fr_ids', 'fr_count'],
}
const FR_LIST_SCHEMA = {
  type: 'object',
  properties: { fr_ids_done: { type: 'array', items: { type: 'string' } } },
  required: ['fr_ids_done'],
}
const GATE_VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    verify_rc: { type: 'integer', description: 'exit code of `verify-gate` — 0 means all three of the gate\'s checks passed AND the PASS verdict was recorded with the digest of the tree it was measured on' },
    detail: { type: 'string' },
  },
  required: ['verify_rc'],
}
const PHASE_SCHEMA = {
  type: 'object',
  properties: { current_phase: { type: 'integer', description: 'current_phase value read from state.json' } },
  required: ['current_phase'],
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Entry & Preflight
// ══════════════════════════════════════════════════════════════════════════

phase('Entry & Preflight')
log('ENTRY-CHECK + P2-ARTIFACTS + run-phase 3 + validate-handoff + CI')
const preflightReport = await dispatch(
  'YOU ARE THE PHASE-3 PREFLIGHT ORCHESTRATOR. Run bash in order; report.\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
  + 'Steps:\n'
  + '1. ENTRY-CHECK: `git -C ' + REPO + ' log --oneline --grep="phase2(review-complete)" -1` OR confirm P2 artifacts exist.\n'
  + '2. P2-ARTIFACTS: `ls ' + REPO + '/02-architecture/SAD.md ' + REPO + '/02-architecture/adr/ADR.md ' + REPO + '/02-architecture/TEST_SPEC.md ' + REPO + '/.methodology/quality_manifest.json ' + REPO + '/.methodology/SAB.json`. ALL must exist (else FAIL → return to Phase 2).\n'
  + '3. PREFLIGHT: `' + PY + ' ' + REPO + '/harness_cli.py run-phase --phase 3 --project ' + REPO + '`. FAIL → fix FSM/Constitution/Drift, re-run (max 3).\n'
  + '4. HANDOFF: `' + PY + ' ' + REPO + '/harness_cli.py validate-handoff --from-phase 2 --project ' + REPO + '`. Must exit 0.\n'
  + '5. PREFLIGHT-CI: (a) confirm `' + REPO + '/.github/workflows/harness_quality_gate.yml` (CI workflow) + `' + REPO + '/.git/hooks/prepare-commit-msg` (git hook) both exist; if either is missing, run `init-project --phase 3 --project ' + REPO + ' --overwrite` and re-check. (b) Confirm state.json current_phase=3. If current_phase != 3: FAIL — init-project never changes an existing FSM state. If lower, the Phase 2 workflow must complete the enforced transition with `advance-phase --completed 2 --project ' + REPO + '`; if higher, run the workflow matching current_phase. Do NOT run advance-phase from this preflight.\n\n'
  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if ALL 5 steps succeeded; reason = one-line summary (on FAIL: which step + verbatim error tail).\n\n'
  + 'SCOPE RULES:\n- DO NOT implement any FR or run TDD steps.\n- DO NOT run advance-phase/push-milestone/run-gate.\n- DO NOT modify harness/.\n- ONLY preflight commands + fixes.',
  { label: 'preflight', phase: 'Entry & Preflight', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
)
if (!(preflightReport && preflightReport.pass === true)) {
  return halt('preflight', { error: 'Phase 3 preflight did not PASS', reason: preflightReport ? String(preflightReport.reason ?? '').slice(-600) : 'agent returned null (skipped or terminal API error)' })
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Env Check
// ══════════════════════════════════════════════════════════════════════════

phase('Env Check')
log('run-env-check + finalize-env-check (Bug #127 root-cause + bash-timeout-aware background poll)')
// Bug #127 root-cause fix (2026-06-27): `cmd_run_env_check` now returns
// exit 0 when ready=true and 1 when ready=false (previously always 0).
// Workflows check `$?` directly with no LLM orchestrator agent in the loop.
// 2026-07-02 paraphrase incident (phase3): the agent rewrote ENV_CHECK_RC=0
// as "RC=0" and the regex gate false-negatived a READY environment. Schema
// transport is paraphrase-proof.
// Round 11 station2b (plan ENV-CHECK marker): run-env-check's exit code
// (Bug #127) only reflects the agent's self-reported `ready` boolean, not
// result-schema completeness — a `{"ready": true}` response missing
// checked_at / env_vars.required / cli_tools.required /
// infra_services.required would pass run-env-check alone but fail
// finalize-env-check's schema check (HarnessBridge.finalize_env_check,
// cli/gate_cmds.py) — a real anti-fabrication gap, not redundant with
// Bug #127's fix. Chain both: `&&` runs finalize only after run-env-check
// succeeds; the trailing `; echo RC=$?` captures whichever of the two is
// authoritative (run-env-check's own failure code if it failed first,
// otherwise finalize-env-check's).
// Round 23 (2026-07-26, observed on a downstream project's phase5 workflow run):
// the chained command legitimately runs past the Claude Code Bash tool's
// 10-min default timeout — run-env-check spawns an LLM sub-agent with
// STALL_TIMEOUT=900s (core/harness_config.py::STALL_TIMEOUTS). The Bash
// tool's response to a timeout hit is "moved to the background" + return
// rc=124 to the caller, which the sub-agent then mis-reports as the
// run-env-check exit code (it isn't — the actual sub-process is still
// running). Symptom: ~10 min elapsed, rc=124, no env_check_result.json
// cross-check. Fix: launch the chain via Bash with run_in_background:true
// and a foreground `kill -0 PID` poll loop (same idiom as GATE1-DELTA
// background dispatch in phase3-8). The Bash tool returns immediately
// with a task_id; the agent then polls via `kill -0` / log tail and
// reports the FINAL `RC=` line from the chained command's own stdout
// (which IS the run-env-check/finalize-env-check exit code — the
// `; echo "RC=$?"` appended at the end of the chain).
const envCheckLog = '/tmp/envcheck_phase3.log'
const envCheckChain = PY + ' ' + REPO + '/harness_cli.py run-env-check --phase 3 --project ' + REPO + ' && ' + PY + ' ' + REPO + '/harness_cli.py finalize-env-check --phase 3 --project ' + REPO + '; echo "RC=$?"'
const envReport = await dispatch(
  'YOU ARE THE PHASE-3 ENV-CHECK ORCHESTRATOR (Bash-timeout-aware, background poll).\n'
  + 'REPO: ' + REPO + '\n'
  + 'PYTHON: ' + PY + '\n'
  + 'LOG PATH: /tmp/envcheck_phase3.log\n\n'
  + 'run-env-check spawns a full LLM sub-agent (max-turns 70) with STALL_TIMEOUT=900s in core/harness_config.py::STALL_TIMEOUTS. A bare synchronous Bash invocation gets auto-moved to background by the Bash tool at its 10-min default timeout and the Bash call returns rc=124 immediately while the actual sub-process keeps running — the rc=124 is NOT the run-env-check exit code. Launch the chain with run_in_background:true so it runs to completion; then poll.\n\n'
  + '1. Launch (Bash with `run_in_background: true`, `timeout: 1500000` (25 min) — covers 900s stall + 600s finalize buffer):\n'
  + '   command: `nohup bash -c \'' + envCheckChain + '\' > ' + envCheckLog + ' 2>&1 & echo $!`\n'
  + '   The Bash tool returns immediately with a task_id AND a shell PID printed in stdout (the `echo $!`). Capture the PID.\n\n'
  + '2. Poll loop — BACKOFF intervals, in seconds: 5, 10, 20, 30, then 60 for every\n'
  + '   further iteration. Cap 22 polls (5+10+20+30 + 18x60 ≈ 19 min — still covers\n'
  + '   the 900s stall plus a finalize buffer).\n'
  + '   Round 22 站4: the first interval used to be a flat 60s. Since Round 20\n'
  + '   run-env-check returns in about a second whenever env_contract.json is\n'
  + '   current (source docs unchanged -> deterministic verification, no sub-agent,\n'
  + '   see cli/gate_cmds.py), so a fixed first sleep spent a full minute per phase\n'
  + '   waiting on a command that had already finished. Backoff keeps the long tail\n'
  + '   cheap while making the common case fast.\n'
  + '   Each iteration Bash call (`run_in_background: false`, `timeout: 90000`):\n'
  + '   `sleep <interval> && kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`\n'
  + '   When DONE → break out of the loop.\n'
  + '   If still RUNNING past 22 polls (~19 min) → `pkill -TERM -P <PID>; kill <PID>` (this PID is bash; its harness child reaps its own tree), then report "ENV_CHECK: TIMEOUT" via StructuredOutput.\n\n'
  + '3. Authoritative read: `tail -100 ' + envCheckLog + '`; parse the LAST line matching `RC=<integer>`. That integer is the run-env-check/finalize-env-check chain exit code (NOT the Bash tool rc).\n\n'
  + '4. Cross-check (Bug #127 anti-fabrication): `cat ' + REPO + '/.sessi-work/env_check_result.json` MUST show `\"ready\": true`. If file missing or ready=false → ready=false in the StructuredOutput regardless of RC (the LLM may have self-reported ready=true while the result JSON says otherwise).\n\n'
  + 'Report via the StructuredOutput tool: { rc: <int from final RC= line>, ready: <bool from env_check_result.json> }.\n\n'
  + 'SCOPE RULES:\n'
  + '- ONLY run-env-check + finalize-env-check + read their log + result artifacts.\n'
  + '- DO NOT modify harness/ (HR-17).',
  { label: 'env-check', phase: 'Env Check', agentType: 'general-purpose', schema: ENV_CHECK_SCHEMA },
)
if (!(envReport && envReport.rc === 0 && envReport.ready === true)) {
  const _envCheckResult = `${REPO}/.sessi-work/env_check_result.json`
  return halt('env-check', { error: 'Phase 3 env-check did not PASS', rc: envReport ? envReport.rc : null, ready: envReport ? envReport.ready : null, note: envReport ? ('run-env-check/finalize-env-check rc=' + envReport.rc + ' ready=' + envReport.ready + ' — read ' + _envCheckResult) : 'agent returned null (skipped or terminal API error)' })
}

// (ported from phase3, 155ec07 + 286ccca)
// 2026-07-02 incident class: a sub-agent action (bare pytest → harness test
// CWD leak) can corrupt quality_manifest.json MID-RUN, not just before entry.
// Detect the three known corruption patterns (fr_ids truncated, traceability
// cleared, gate1 wiped) before anything commits .methodology/ wholesale.
// T1-A (8-phase audit remediation): the previous inline Python one-liner
// had the truncation-comparison direction inverted (`fr_trace >= fr_ids`
// instead of the harness's actual `fr_ids >= fr_trace`) plus an unfounded
// `fr_ids >= 2` floor. `check-manifest-integrity` wraps the harness's own
// (correct, tested) PhaseHooks.preflight_manifest_integrity() instead.
const integrityCmd = PY + ' ' + REPO + '/harness_cli.py check-manifest-integrity --project ' + REPO + ' --phase 3'
async function checkManifestIntegrity(phaseLabel, agentLabel) {
  const verdict = await dispatch(
    'Run EXACTLY this command via the Bash tool:\n`' + integrityCmd + '; echo RC=$?`\n'
    + 'Then report via the StructuredOutput tool: pass = true ONLY if the output ends with `RC=0`; reason = the JSON the command printed (verbatim, excluding the RC= line).',
    { label: agentLabel, phase: phaseLabel, agentType: 'general-purpose', schema: VERDICT_SCHEMA },
  )
  const ok = !!(verdict && verdict.pass === true)
  const raw = verdict ? String(verdict.reason ?? '').trim() : 'agent returned null'
  if (!ok) log('  manifest integrity FAIL [' + agentLabel + ']: ' + raw)
  return { ok, raw }
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Load FRs
// ══════════════════════════════════════════════════════════════════════════

phase('Load FRs')
log('load-context --phase 3 → fr_ids (script holds the loop)')
// v15: retry loop — agent() wrapped (Bug #2); v4: schema transport, no prose parsing
// v2.13.1: hardened against agent hallucination — verify .sessi-work/phase3_ctx.json
// actually exists and contains non-empty fr_ids before accepting (Bug #122).
// Fix D (2026-07-18): this file also carries the `lessons` field (recall_lessons()
// snapshot from .methodology/lessons/*.md, injected into each per-FR TDD prompt
// below). .sessi-work/ is gitignored, so a `git reset --hard` back to a phase-3
// entry commit never clears a stale phase3_ctx.json from a PRIOR (pre-fix) run —
// the old "exists and parses" check alone happily reused it, leaking a dead run's
// failure lessons into a fresh one and anchoring the TDD sub-agent on a
// already-fixed problem. Force a fresh load-context on the first attempt of every
// workflow invocation; the "reuse if valid" fallback still applies on attempts
// 2/3, which only run if regeneration itself failed (Bug #122's original intent).
let ctx = null
const ctxFile = REPO + '/.sessi-work/phase3_ctx.json'
// Round 22 站3: the separate ctx-check dispatch is gone. On attempt 1 its
// verdict was never even read — Fix D's `attempt === 1 ||` short-circuits
// ahead of it, so the probe ran, answered, and was discarded. On attempts
// 2/3 it ran `json.load(ctxFile)` to prove the file parses, which is the
// same thing the read below does and fails on. Both cases it distinguished
// (missing vs. unparseable) had identical handling: regenerate.
let needRegen = true  // Fix D: attempt 1 ALWAYS regenerates
for (let attempt = 1; attempt <= 3; attempt++) {
  if (needRegen) {
    log('  ' + (attempt === 1 ? 'forcing fresh load-context (attempt 1 — avoid stale lessons)' : 'ctx unreadable (attempt ' + attempt + ') — regenerating'))
    const ctxRegenCmd = `${PY} ${REPO}/harness_cli.py load-context --phase 3 --project ${REPO} --json > ${ctxFile} && ${PY} -c "import json,os; json.load(open('${ctxFile}')); print('REGEN_OK_'+str(os.path.getsize('${ctxFile}')))"`
    try {
      await dispatch(
        `You MUST use the Bash tool. Run exactly:\n${ctxRegenCmd}\nReturn the raw stdout as your final message.`,
        { label: 'ctx-regen-' + attempt, phase: 'Load FRs', agentType: 'general-purpose' },
      )
    } catch (e) { log('  ctx-regen agent failed: ' + String(e.message ?? e).slice(0, 80)) }
  }
  needRegen = true

  try {
    // J1 fix (2026-06-29): forward fr_titles too. load-context emits fr_details as a
    // DICT keyed by FR id ({"FR-01":{"title":...}}). The previous parse only forwarded
    // fr_details_keys (no titles), and the consumer (frTitle below) read it as an Array
    // — so titles silently never populated. Emit an {id:title} map the consumer uses
    // directly.
    const ctxParseCmd = `${PY} -c "import json; d=json.load(open('${ctxFile}')); fd=d.get('fr_details') or {}; print(json.dumps({'fr_ids':d.get('fr_ids',[]),'fr_count':len(d.get('fr_ids',[])),'fr_titles':{k:(v.get('title','') if isinstance(v,dict) else '') for k,v in fd.items()}}))"`
    const ctxResult = await dispatch(
      `You MUST use the Bash tool. Run exactly:\n${ctxParseCmd}\nThe command FAILS (nonzero exit, Python traceback) when the file is missing or not valid JSON — report that verbatim rather than inventing values. On success stdout is a single JSON line: report via the StructuredOutput tool fr_ids, fr_count, fr_titles = the EXACT values from that line (transcribe, do not recompute).`,
      { label: 'load-ctx-a' + attempt, phase: 'Load FRs', agentType: 'general-purpose', schema: CTX_SCHEMA },
    )
    if (ctxResult && Array.isArray(ctxResult.fr_ids) && ctxResult.fr_ids.length > 0) {
      ctx = ctxResult
      log('  load-ctx OK (schema-validated, ' + ctx.fr_ids.length + ' FRs)')
      break
    }
    log('  load-ctx returned no fr_ids (attempt ' + attempt + '): keys=' + Object.keys(ctxResult ?? {}).join(',') + ' — regenerating ctx file')
  } catch (e) { log('  load-ctx agent failed: ' + String(e.message ?? e).slice(0, 80) + ' — regenerating ctx file') }
}
if (!ctx) return halt('load-frs', { error: 'Load FRs: ctx failed after 3 attempts', ctxFile })
let frIds = Array.isArray(ctx.fr_ids) ? ctx.fr_ids : []
if (!frIds.length) return halt('load-frs', { error: 'Load FRs: no fr_ids found in ctx', ctxKeys: Object.keys(ctx) })
// J1: fr_titles is the {id:title} map emitted by ctxParseCmd above.
const frTitle = (ctx.fr_titles && typeof ctx.fr_titles === 'object') ? ctx.fr_titles : {}
log('  fr_ids = ' + JSON.stringify(frIds))

// Gate 1 pre-check: identify FRs that ALREADY passed Gate 1 (skip TDD on resume/re-run).
// AUTHORITATIVE source = quality_manifest.gate_results.gate1[fr].quality_complete, which
// harness_bridge writes on EVERY finalize-gate (pass OR fail). NOT the g1_p3_*.flag
// sentinel: that flag is written by run-gate (it only proves run-gate executed), so a
// finalize-gate that raised GateBlockedError on a failing dimension still leaves the
// sentinel behind — using it as a PASS signal misreports blocked FRs as done.
const precheckCmd = PY + ' -c "import json; g=(json.load(open(\'' + REPO + '/.methodology/quality_manifest.json\')).get(\'gate_results\',{}) or {}).get(\'gate1\',{}) or {}; print(chr(10).join(fr for fr,v in g.items() if isinstance(v,dict) and v.get(\'quality_complete\') is True))"'
const precheckResult = await dispatch(
  'Run EXACTLY this command via the Bash tool (stdout is a newline-separated list of FR ids, possibly empty):\n`' + precheckCmd + '`\n'
  + 'Then report via the StructuredOutput tool: fr_ids_done = the EXACT FR ids from stdout as an array (empty array if stdout is empty).',
  { label: 'gate1-precheck', phase: 'Load FRs', agentType: 'general-purpose', schema: FR_LIST_SCHEMA }
)
const alreadyDone = new Set()
for (const id of (precheckResult && Array.isArray(precheckResult.fr_ids_done) ? precheckResult.fr_ids_done : [])) {
  if (/^FR-\d+$/.test(String(id).trim())) alreadyDone.add(String(id).trim())
}
if (alreadyDone.size > 0) log('  sentinel pre-check: Gate 1 (Phase 3) already PASS for ' + [...alreadyDone].join(', ') + ' — skipping TDD agents')


// ══════════════════════════════════════════════════════════════════════════
// Phase: Per-FR TDD
// ══════════════════════════════════════════════════════════════════════════

phase('Per-FR TDD')
const gate1Pass = []
const gate1Fail = []
let p3MidPushed = false
const p3MidThreshold = Math.max(1, Math.floor(frIds.length / 2))  // PUSH ③ trigger: ≥50% FRs Gate 1 PASS (phase3_plan.md, harness push_cmds.py)
for (const frId of frIds) {
  if (alreadyDone.has(frId)) {
    log('  ' + frId + ' — sentinel exists, Gate 1 PASS (skip TDD)')
    gate1Pass.push(frId)
  } else {
        log('  === ' + frId + ' (' + (frTitle[frId] || '') + ') — TDD chain ===')
    const frNum = frId.match(/\d+/)[0].padStart(2, '0')
    let passed = false
    for (let frAttempt = 1; frAttempt <= 2; frAttempt++) {
    if (frAttempt > 1) log('  ' + frId + ' — Gate 1 FAILED on attempt 1, retrying this FR once before moving on (dispatch prompt is resume-aware: re-checks git log, skips already-landed RED/MIRROR/GREEN/IMPROVE commits)')
    const frReport = await dispatch(
      'YOU ARE THE IMPLEMENTER for ' + frId + ' (' + (frTitle[frId] || '') + '). Run the full TDD chain for THIS ONE FR.\n'
      + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
      + 'FIRST — this dispatch may be a retry of a prior attempt on ' + frId + ' that was interrupted mid-chain (this is common; do not assume you are starting from zero). Run `git -C ' + REPO + ' log --oneline -8` and check for existing commits matching RED (`test(RED):`) / GREEN (`feat(' + frId + '):`) / MIRROR (`test(' + frId + '):`) / IMPROVE (`refactor(' + frId + '):`) for this FR. Skip any step below whose commit already exists — jump straight to the first step that is missing. `run-fr-step` itself is idempotent (it skips a step whose commit already landed), so this only saves you the time of re-deciding what to do; it never causes a step to be silently skipped that actually still needs doing.\n\n'
      + 'Direction C (past lessons): Bash `cat ' + REPO + '/.sessi-work/phase3_ctx.json` and READ the `lessons` field (compact markdown, "" if none). DO NOT repeat those past failure modes in this FR\'s TDD chain (implementation / tests / GATE1 fixes).\n\n'
      + 'Run these harness steps IN ORDER (each is a bash command; read its output before the next):\n'
      + 'IMPORTANT for steps 1/3/4 below (TDD-RED/TDD-GREEN/TDD-IMPROVE): these usually finish well under 120s — run them as plain synchronous Bash commands, do NOT pre-emptively background them. But they occasionally exceed the Bash tool\'s own 120s default and get auto-backgrounded ("...moved to the background (ID: <id>)... Output is being written to: <path>... You will be notified when it completes."). If that happens: do NOT call the Monitor tool on it — Monitor\'s async notification will not arrive within this single dispatch, and you will be left waiting with nothing to report. Instead recover synchronously: run `sleep 30 && tail -100 <path>` (repeat, cap 20 polls / ~10min).\n'
      + '   A poll that shows NO NEW content since the previous poll means the command is still running normally — this is the expected steady state while a subprocess/test-suite/coverage run works, NOT a sign of a problem. Sleep 30 and tail again; do NOT investigate `ps`, do NOT hunt for PIDs, do NOT run any other diagnostic command while waiting — you were never given a PID for this command (unlike GATE1\'s step 6 below, which captures its own via `nohup ... & echo $!`), so any PID found by other means belongs to something else and inspecting it wastes turns without telling you anything.\n'
      + '   Stop polling ONLY when: (a) the tail output shows the command has returned control (an explicit PASS/FAIL/error the CLI itself prints, or a new `git log`-visible commit for this step), or (b) you reach the 20-poll cap — report "' + frId + ' <step-name>: TIMEOUT" (not FAIL, not silence) and move on, exactly as GATE1\'s own timeout contract in step 6 does.\n'
      + '1. TDD-RED:    `' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step TDD-RED --project ' + REPO + ' --srs 01-requirements/SRS.md`\n'
  + '   AFTER RED writes the test file: open `tests/test_fr' + frNum + '.py` and ensure EVERY NFR associated with ' + frId + ' in the traceability table (TRACEABILITY_MATRIX.md §5 is the canonical listing) has a `# NFR-XX` comment on at least one test function. Without these annotations, `compute_trace_dimension` 4c = 0% and Gate 2 blocks (HR-16). Use `grep -n "# NFR-" tests/test_fr' + frNum + '.py` and check against the NFR list for ' + frId + ' — document every association.\n'
      + '2. MIRROR:     `' + PY + ' ' + REPO + '/harness_cli.py check-test-mirrors-spec --fr-id ' + frId + ' --test-file tests/test_fr' + frNum + '.py --project ' + REPO + '`\n'
      + '   On MIRROR FAIL: fix the TEST to match TEST_SPEC.md — do NOT edit TEST_SPEC.md (correctness was locked in Phase 2; P3 only implements). Re-run.\n'
      + '3. TDD-GREEN:  `' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step TDD-GREEN --project ' + REPO + ' --srs 01-requirements/SRS.md`\n'
      + '4. TDD-IMPROVE:`' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step TDD-IMPROVE --project ' + REPO + '`\n'
      + '   Coverage-filling tests that exercise ANOTHER FR\'s not-yet-implemented stub (the `_err(f"\'<name>\' is not yet implemented...")` pattern) MUST NOT assert on that stub\'s specific message text — it is temporary and will be replaced when the owning FR lands, breaking your test. Either skip that branch (it will be covered when the owning FR implements it and re-runs GATE1/DELTA) or assert only an invariant guaranteed stable across the stub-to-real transition (e.g. non-zero exit code), never the stub\'s literal text.\n'
      + '5. amend-sab (proactive, BEFORE GATE1): `' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step amend-sab --project ' + REPO + '` (first-class dispatch, idempotent, deterministic — does NOT spawn a sub-agent). If new modules are registered to .methodology/SAB.json: commit them (`git -C ' + REPO + ' add .methodology/SAB.json && git -C ' + REPO + ' commit -m "amend: register SAB modules (' + frId + ')"`) before proceeding to GATE1. This FR\'s GREEN/IMPROVE steps may have added modules GATE1\'s Architecture Amendment Protocol would otherwise BLOCK on — registering them now avoids a wasted GATE1 round.\n'
      + '6. GATE1 — long-running (every internal fix round spawns a fixer AND re-dispatches a full GATE1, so the wall time is the budget the cap in step b encodes). Run it BACKGROUNDED — do NOT invoke it as a plain synchronous command:\n'
      + '   GATE1 invocation procedure (a/b/c):\n'
      + '   a. Launch: `nohup bash -c \'' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step GATE1 --project ' + REPO + '; echo "RC=$?"\' > /tmp/gate1_' + frId + '.log 2>&1 & echo $!` — note the printed PID.\n'
      + '   b. Poll with BACKOFF intervals, in seconds: 5, 10, 20, 30, 60, then `fr_step_poll_interval_s` for every further iteration — `sleep <interval> && kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Cap `fr_step_poll_cap` polls; both from phase3_ctx.json (absent ⇒ re-run load-context). Still RUNNING past the cap → `kill <PID>` (reaps the whole tree), report rc -1 (TIMEOUT, not a gate verdict).\n'
      + '   c. Once DONE: `tail -200 /tmp/gate1_' + frId + '.log`. The LAST line matching `RC=<integer>` is run-fr-step\'s own exit code — that integer, verbatim, is what you report. It is NOT the Bash tool\'s rc, and you must not compute, infer or round it: read it off the line.\n'
      + '   Gate 1 per-dimension thresholds are printed in the log itself (dynamic — read from quality_manifest gate_score_overrides, do not assume fixed numbers).\n'
      + '   - PASS → done.\n'
      + '   - FAIL → fix failing dims (ruff check . --fix; add tests for coverage; fix pyright errors), repeat the GATE1 invocation procedure (a/b/c). Max 3 rounds.\n'
      + '   - Still failing after 3 → report FAIL.\n'
      + '   The RC decides what happens next, and the workflow reads the integer — not your wording, so paraphrase the reason however you like:\n'
      + '   - RC=23 (dispatch structurally broken, claude.ai connectors disabled): STOP. Do NOT unset env vars, do NOT retry — every retry fails identically.\n'
      + '   - RC=70 (harness-methodology itself crashed): NOT your code or tests. STOP, do not retry, do not modify project code. The log names a crash bundle path; quote it in your reason.\n'
      + '   - RC=25 (INFRA precondition block): project state, not code. STOP this chain; the workflow routes it to `amend-sab`.\n'
      + '   - Any other nonzero RC = an ordinary Gate 1 FAIL → fix failing dims and repeat the a/b/c procedure (max 3 rounds, as above).\n'
      + '   - AAP block: log contains "Unregistered modules detected: {…}" — step 5 amend-sab didn\'t run. Verify .methodology/SAB.json committed; else run `' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step amend-sab --project ' + REPO + '` + manual `git add ... && git commit`, repeat GATE1. Max 1 amend round per FR.\n'
      + '   run-fr-step auto-pushes on completion (idempotent). Crash recovery: `resume-fr-phase --phase 3 --project ' + REPO + '`.\n'
      + '7. ORCH-POST (after GATE1 PASS, per phase3_plan.md [ORCH-POST]):\n'
      + '   a. `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 40.0 --fr-id ' + frId + '` (per-FR D4 ≥40%). FAIL → add the missing test implementations for ' + frId + ', re-run.\n'
      + '   b. SAB.json is kept in sync by amend-sab (step 5 above). Do NOT run generate_sab.py --overwrite here.\n'
      + '      (generate_sab.py --overwrite rebuilds SAB purely from SAD.md §5, which was locked in Phase 2\n'
      + '       and may not reflect modules added during Phase 3 implementation. Only run generate_sab.py --overwrite\n'
      + '       manually AFTER updating SAD.md §5 to include all Phase 3 modules.)\n\n'
      + 'Implement the module per SPEC.md (read ' + REPO + '/SPEC.md for ' + frId + ') + SAD.md module mapping. Write source under the package directory layout your project uses: if `03-development/src/<package>/` is a FLAT PACKAGE (one `<module>.py` per file, e.g. `03-development/src/<package>/<module>.py`), write `src/<package>/<module>.py`; if it is MODULE-PER-DIR (one `<module>/__init__.py` per directory), write `src/<package>/<module>/__init__.py`. The init-project directory scaffold shows which layout your project uses. Do NOT place this FR\'s implementation inside a file another FR already owns or a shared/global file (e.g. `cli.py`) used by multiple FRs — each FR\'s logic belongs in its own module per SAD.md/quality_manifest.json\'s fr_module_traceability mapping. Tests for ' + frId + ' MUST be placed at the path(s) declared in TEST_SPEC.md §FR-' + frNum + ' (test file list) — TEST_SPEC.md is the canonical source of truth for test placement. If TEST_SPEC lists multiple test files (e.g. unit + integration variants), you MUST create all of them; pass `--test-file <path1> --test-file <path2> ...` to MIRROR and related tooling. The legacy single-file convention (`tests/test_fr' + frNum + '.py` only) is no longer required when TEST_SPEC specifies otherwise. Docstrings must include [' + frId + '] reference (NFR-05).\n\n'
      + 'Report via the StructuredOutput tool: { rc: <the integer from step 6c\'s last RC= line>, final_line: "' + frId + ' GATE1: PASS" or "' + frId + ' GATE1: FAIL — <reason>" }. If GATE1 never ran to completion (timeout at the poll cap), rc is -1.\n\n'
      + 'SCOPE RULES:\n- DO NOT implement any FR OTHER than ' + frId + '.\n- DO NOT run run-gate (Gate 2), advance-phase, or push-milestone.\n- DO NOT edit .methodology/quality_manifest.json or .sessi-work/gate1_result.json to fake/reset scores — fix the underlying code/tests instead.\n- DO NOT modify harness/ (HR-17).\n- ONLY the 7 steps above for ' + frId + ' (amend-sab in step 5, spec-coverage-check in step 7a is allowed).',
      { label: 'tdd-' + frId, phase: 'Per-FR TDD', agentType: 'general-purpose', schema: FR_STEP_SCHEMA },
    )
    // L1: distinguish a session/rate-limit block (null/empty agent return) from a real
    // Gate 1 FAIL — mirror the Gate 2 detection (below). Without this, a rate-limit mid-
    // TDD is misreported as a code-quality Gate 1 failure. Sentinel GUARD skips completed
    // FRs on resume, so aborting here is safe.
    if (frReport === null || frReport === undefined || typeof frReport !== 'object') {
      log('  ' + frId + ' agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
      return { session_limit_blocked: true, phase: 3, step: frId, fr_id: frId, gate1Pass, message: 'Agent hit session/rate limit during ' + frId + ' TDD. Resume after quota reset — sentinel GUARD will skip completed FRs.' }
    }
    // L1.5-L1.7: the three terminal aborts, read from run-fr-step's own exit code
    // (launch line's `; echo "RC=$?"`, carried by FR_STEP_SCHEMA). Prose is not
    // load-bearing — see render_terminal_abort_detectors' docstring (Round 70 站3).
    const frRc = (frReport && typeof frReport.rc === 'number') ? frReport.rc : null
    // 23 — dispatch structurally broken; every retry fails identically.
    if (frRc === 23) {
      log('  ' + frId + ' exited 23 — dispatch is structurally broken (claude.ai connectors disabled), aborting remaining FRs')
      return { dispatch_structurally_broken: true, phase: 3, fr_id: frId, gate1Pass, gate1Fail: [...gate1Fail, frId], message: frId + ' GATE1: dispatch is structurally broken (env: ANTHROPIC_API_KEY overrides claude.ai login). Human must unset ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN/ANTHROPIC_BASE_URL/ANTHROPIC_DEFAULT_HAIKU_MODEL in the shell that launches this process, then re-run via Workflow({scriptPath, resumeFromRunId}).' }
    }
    // 70 — harness crashed. Not a project defect; no re-run clears it.
    if (frRc === 70) {
      log('  ' + frId + ' exited 70 — harness-methodology crashed, aborting remaining FRs')
      return { harness_bug_detected: true, phase: 3, fr_id: frId, gate1Pass, gate1Fail: [...gate1Fail, frId], message: frId + ' GATE1: harness-methodology itself crashed (exit 70 — see the crash bundle path in the log). This is not a project quality issue; a human must diagnose and fix the harness bug before this FR can proceed.' }
    }
    // 25 — INFRA precondition block: project state, repairable, but not by a fix
    // agent aimed at code. Separate from 70 since 站2, because the remedy is.
    if (frRc === 25) {
      log('  ' + frId + ' exited 25 — INFRA precondition block, aborting remaining FRs')
      return { infra_abort: true, phase: 3, fr_id: frId, gate1Pass, gate1Fail: [...gate1Fail, frId], message: frId + ' GATE1: an INFRA precondition failed (exit 25 — modules missing from SAB.json, or a tool that never ran). Repair project state with `harness_cli.py amend-sab`, then re-run with a NEW run_tag: Workflow({scriptPath, args: {repo, run_tag}}). amend-sab changes no prompt, so without one the cache can replay this halt.' }
    }
    // AUTHORITATIVE Gate 1 verdict: read the harness quality_manifest (bridge writes
    // gate_results.gate1[fr].quality_complete on every finalize-gate, pass OR fail) —
    // NOT the sub-agent's self-reported "GATE1: PASS" string. A sub-agent can report
    // PASS from its own gate1_result.json overall score even when finalize-gate raised
    // GateBlockedError (e.g. spec-coverage short, or a dimension below threshold), which
    // silently advances a FR that the harness actually blocked. Verify against the
    // harness's own record so a blocked gate is never counted as passed.
    // v2.13.3 (P3 2026-07-16 follow-up to v2.13.2): the v2.13.2 design used
    // `spawnSync` to invoke a deterministic Python verifier synchronously
    // from the workflow. The intent was right (avoid LLM-hallucinated
    // verify verdicts — see wf_53d055ce-d0b where the LLM reported
    // `pass:false, reason=GATE1_VERIFIED_FAIL score=91.81` despite
    // `quality_complete=True`), but the dynamic-workflow runtime sandbox
    // does not expose Node.js `child_process.spawnSync` — the call
    // ReferenceErrors at this site (`spawnSync is not defined`).
    //
    // The deterministic manifest read is moved to a stand-alone script
    // (`harness/scripts/verify_gate1_qc.py`) which the Bash sub-agent
    // invokes. The LLM is now a string carrier: the prompt requires it to
    // echo the literal Python stdout (Python's print is deterministic —
    // same input, same bytes), and workflow JS regex-parses the echoed
    // string to derive `passed`. The LLM's own `pass` field is ignored.
    // Same AUTHORITATIVE manifest read (the whole point of this verify
    // step) is preserved; only the execution substrate changes.
    const verifyResult = await dispatch(
      'You MUST use the Bash tool. Run EXACTLY this single command (single line):\n'
      + PY + ' ' + REPO + '/harness/scripts/verify_gate1_qc.py --fr-id ' + frId + ' --project ' + REPO + '\n'
      + 'Then report via the StructuredOutput tool: pass = true ONLY if the FIRST line of stdout is exactly "GATE1_VERIFIED_PASS"; reason = the verbatim stdout (do NOT paraphrase, summarize, or prepend commentary).',
      { label: 'gate1-verify-' + frId, phase: 'Per-FR TDD', agentType: 'general-purpose', schema: VERDICT_SCHEMA }
    )
    // Round 85 站2: a quota cap here returns null, whose empty reason does
    // not start with GATE1_VERIFIED_PASS — a rate limit read as a Gate 1 FAIL.
    if (verifyResult === null || verifyResult === undefined || typeof verifyResult !== 'object') {
      log('  ' + frId + ' agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
      return { session_limit_blocked: true, phase: 3, step: frId, fr_id: frId, gate1Pass, message: 'Agent hit session/rate limit verifying ' + frId + ' Gate 1. Resume after quota reset — the manifest read is idempotent.' }
    }
    const verifyOut = String((verifyResult && verifyResult.reason) || '').trim()
    // Round 12 站2a: verdict from the deterministic stdout ONLY — the AND
    // on verifyResult.pass contradicted the comment above (v2.13.3 shipped
    // "the LLM's pass field is ignored" in prose while the code still let a
    // hallucinated pass:false veto a PASS manifest, the exact
    // wf_53d055ce-d0b incident this step exists to prevent).
    passed = verifyOut.startsWith('GATE1_VERIFIED_PASS')
    // rc -1 is the wrapper saying it killed the step, not a verdict — after
    // the manifest read (see render_fr_step_timeout_exit, Round 85 站2).
    if (!passed && frRc === -1) {
      log('  ' + frId + ' — GATE1 killed at the poll cap; no manifest verdict')
      return { fr_step_timeout: true, halt_step: 'fr-step-timeout', phase: 3, fr_id: frId, gate1Pass, gate1Fail, message: frId + ' GATE1: killed at the poll cap with run-fr-step still running, so no gate verdict was reached — this is NOT a code-quality failure and no fix agent should be sent at it. Re-run with a NEW run_tag: Workflow({scriptPath, args: {repo, run_tag}}); a recurrence means the step is hung past the budget computed from fr_step timeout and max_fix_rounds.' }
    }
    if (passed) break
    }
    if (passed) { gate1Pass.push(frId); log('  ' + frId + ' Gate 1 PASS (' + gate1Pass.length + '/' + frIds.length + ') [harness-verified]') }
    else { gate1Fail.push(frId); log('  ' + frId + ' Gate 1 FAIL [harness manifest qc != true; sub-agent self-report ignored]') }
  }

  // PUSH ③ p3-mid — fire once when ≥1/3 FRs have Gate 1 PASS (but not yet all done).
  if (!p3MidPushed && gate1Pass.length >= p3MidThreshold && gate1Pass.length < frIds.length) {
    p3MidPushed = true
    log('  ≥1/3 FRs Gate 1 PASS (' + gate1Pass.length + '/' + frIds.length + ') — pushing p3-mid milestone')
    await dispatch(
      'YOU ARE THE P3 MID-MILESTONE PUSHER (≥1/3 FRs Gate 1 PASS).\n'
      + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
      + '0. GUARD: `git -C ' + REPO + ' log --oneline --grep="P3-mid)" -1`. If a p3-mid commit already exists, report "MILESTONE: PASS (already pushed)" and stop — do NOT push again.\n'
      + '1. Command: `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p3-mid --project ' + REPO
      + ' --fr-done ' + gate1Pass.length + ' --fr-total ' + frIds.length + ' --fr-ids ' + gate1Pass.join(',') + '`\n'
      + '   Writes HANDOVER.md + commits + pushes. If a hook blocks, reword commit to start with `chore(harness):` (NOT --no-verify), retry.\n\n'
      + 'Report: "MILESTONE: PASS|FAIL — <details>".\n\n'
      + 'SCOPE RULES:\n- DO NOT run run-gate / advance-phase / implement FRs.\n- ONLY push-milestone p3-mid.',
      { label: 'milestone-p3-mid', phase: 'Per-FR TDD', agentType: 'general-purpose' },
    )
  }
}
if (gate1Fail.length) {
  return halt('gate1', { error: 'Phase 3: Gate 1 FAILED for FR(s): ' + gate1Fail.join(', ') + ' (escalate — fix code/tests, resume-fr-phase)', owner: 'project', gate1Pass, gate1Fail })
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Milestones
// ══════════════════════════════════════════════════════════════════════════

phase('Milestones')
log('All ' + frIds.length + ' FRs Gate 1 PASS — push p3-pre-gate2 (last stable snapshot before Gate 2)')
const preGate2Report = await dispatch(
  'YOU ARE THE P3 MILESTONE PUSHER. Push the pre-Gate-2 milestone.\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
  + '0. GUARD: `git -C ' + REPO + ' log --oneline --grep="P3-pre-gate2)" -1`. If a p3-pre-gate2 commit already exists, report "MILESTONE: PASS (already pushed)" and stop.\n'
  + '1. Command: `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p3-pre-gate2 --project ' + REPO + ' --fr-ids ' + gate1Pass.join(',') + '`\n'
  + '   Writes HANDOVER.md + commits + pushes. If a hook blocks, reword commit to start with `chore(harness):` (NOT --no-verify), retry.\n\n'
  + 'Verdict: report via the StructuredOutput tool — pass=true if the milestone commit exists or was pushed; reason = one-line detail.\n\n'
  + 'SCOPE RULES:\n- DO NOT run run-gate or advance-phase.\n- ONLY push-milestone p3-pre-gate2.',
  { label: 'milestone-pre-gate2', phase: 'Milestones', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
)
if (!(preGate2Report && preGate2Report.pass === true)) {
  log('  WARNING: p3-pre-gate2 milestone push did not confirm PASS — continuing to Gate 2 (milestone is a snapshot, not a hard gate)')
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Gate 2
// ══════════════════════════════════════════════════════════════════════════

phase('Gate 2')
log('Gate 2 exit (composite ≥75, 12 dims: 10 self-scored + traceability/architecture framework-owned)')
let gate2Pass = false, gate2Report = '', gate2Blocked = false
// Gate 2 pre-flight GUARD: only state.json.last_gate >= 2 proves this gate was
// truly finalized (SSI dims passed AND Phase Truth passed) — see harness_cli.py finalize-gate.
{
  const _precheckCmd = `${PY} -c "import json; lg=json.load(open('${REPO}/.methodology/state.json')).get('last_gate'); print(json.dumps({'qc': isinstance(lg,int) and lg >= 2, 'last_gate': lg}))"`
  try {
    const _preVerdict = await dispatch(
      'Run EXACTLY this command via the Bash tool:\n`' + _precheckCmd + '; echo RC=$?`\n'
      + 'Then report via the StructuredOutput tool: pass = true ONLY if the output line starts with `{"qc": true`; reason = the verbatim JSON line (excluding the RC= line).',
      { label: 'gate2-precheck', phase: 'Gate 2', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
    )
    if (_preVerdict && _preVerdict.pass === true) {
      gate2Pass = true
      log('  Gate 2 PRE-FLIGHT PASS — state.json last_gate >= 2 (gate truly finalized); skipping round loop')
    } else {
      log('  Gate 2 pre-flight: not yet finalized — proceeding to round loop')
    }
  } catch (e) {
    log('  Gate 2 pre-flight threw: ' + String(e.message ?? e).slice(0, 120) + ' — proceeding to round loop')
  }
}
if (!gate2Pass) for (let round = 1; round <= 3; round++) {
  log('  Gate 2 round ' + round + '/3')
  const g2Integrity = await checkManifestIntegrity('Gate 2', 'g2-integrity-r' + round)
  if (!g2Integrity.ok) {
    return halt('manifest-corrupt', { error: 'Gate 2 round ' + round + ': quality_manifest.json corrupted mid-run', detail: g2Integrity.raw, recovery: 'git checkout HEAD -- .methodology/quality_manifest.json (verify HEAD is healthy first — a corrupted manifest may already be committed)', note: 'Corruption appeared AFTER the entry integrity check. Inspect the previous round\'s agent transcript for the writer before restoring.' })
  }
  gate2Report = await dispatch(
    'YOU ARE THE GATE-2 ORCHESTRATOR (Phase 3 exit). ROUND ' + round + '.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Steps:\n'
    + '1. G2a: `' + PY + ' ' + REPO + '/harness_cli.py run-gate --gate 2 --phase 3 --project ' + REPO + '` — read the printed evaluation prompt.\n'
    + '2. G2b: Evaluate ALL Gate 2 dimensions inline per ' + REPO + '/harness/harness/ssi/prompts/evaluate_dimension.md. Write ' + REPO + '/.sessi-work/gate2_result.json.\n   Dims: use the exact `dimensions:` list G2a just printed (it is computed from gate2_p3_exit.yaml, filtered by enabled feature flags — always current, do NOT hand-copy a dim list here).\n   FRAMEWORK-OWNED (do NOT self-score — finalize-gate computes these and overwrites what you write): traceability (harness-trace), architecture (code-review-graph).\n   For any failing dim: fix the ROOT CAUSE in code (ruff/pyright/add tests/bandit/mutation), re-run the tool, update the score. (No auto-fix engine.)\n'
    + '3. G2c — run BACKGROUNDED (finalize-gate\'s own git push triggers the local pre-push hook, plus CRG refresh: bounded on this project today, but a single opaque Bash call with no visible output until it returns is exactly the shape the 180s stall watchdog kills — same class of risk as GATE1, same fix):\n   a. Launch: `nohup ' + PY + ' ' + REPO + '/harness_cli.py finalize-gate --gate 2 --phase 3 --project ' + REPO + ' > /tmp/gate2_finalize_r' + round + '.log 2>&1 & echo $!` — note the printed PID.\n   b. Poll: every 15s run `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Repeat until DONE (cap 40 polls / ~10min). Still RUNNING past the cap → `kill <PID>` (reaps the whole tree), report "GATE2: TIMEOUT".\n   c. Once DONE: `cat /tmp/gate2_finalize_r' + round + '.log` for the full output — identical to what a synchronous run would have printed.\n\n'
    + '4. D4: `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 60.0`. FAIL → add missing test implementations, re-run.\n'
    + '5. CRG-ARCH: `BASELINE=""; [ -f ' + REPO + '/.methodology/crg_baseline_p4.json ] && BASELINE="--baseline ' + REPO + '/.methodology/crg_baseline_p4.json"; ' + PY + ' ' + REPO + '/harness_cli.py crg-arch-check --project ' + REPO + ' $BASELINE`. CI enforces this as an absolute floor on every push from Phase 3 onward, independent of the Gate 2/3/4 composite score — a low architecture sub-score can still let the composite pass, but this check will not. FAIL → the crg-arch-check output lists the low-cohesion communities / oversized functions; fix the underlying architecture issue, re-run.\n'
    + 'finalize-gate (G2c) writes HANDOVER.md + pushes on PASS. Report final line: "GATE2: PASS" (composite ≥75 AND all dims ≥ threshold AND D4 ≥60% AND CRG architecture ≥80) or "GATE2: FAIL — <failing dims>".\n\n'
    + 'SCOPE RULES:\n- DO NOT run advance-phase or push-milestone p3-post-gate2 (next phase does that).\n- DO NOT edit .sessi-work/gate2_result.json to fake scores — fix the code.\n- DO NOT modify harness/ (HR-17).\n- ONLY run-gate/eval/finalize/spec-coverage/crg-arch-check + code fixes.',
    { label: 'gate2-r' + round, phase: 'Gate 2', agentType: 'general-purpose' },
  )
  if (gate2Report === null || gate2Report === undefined || gate2Report === '' || typeof gate2Report !== 'string') {
    gate2Blocked = true
    log('  Gate 2 agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
    break
  }
  const g2v = await dispatch(
    'Run this ONE command via the Bash tool:\n'
    + '`pip install -q code-review-graph==2.3.6 igraph==1.0.0 >/dev/null 2>&1; ' + PY + ' ' + REPO + '/harness_cli.py verify-gate --project ' + REPO + ' --gate 2 --phase 3 --spec-threshold 60.0; echo "RC=$?"`\n'
    + 'It runs all three of Gate 2\'s checks — state.json last_gate >= 2, spec-coverage, and the CRG architecture floor — and appends the verdict, with a digest of the tree it measured, to .methodology/gate_verify.jsonl. advance-phase re-derives that digest and refuses a phase whose exit gate has no matching PASS, so a verdict you did not actually produce cannot carry the phase.\n'
    + 'Then report via the StructuredOutput tool: verify_rc = the exact numeric exit code echoed on the final RC= line; detail = the command\'s last [verify-gate] line.',
    { label: 'gate2-verify-r' + round, phase: 'Gate 2', agentType: 'general-purpose', schema: GATE_VERIFY_SCHEMA },
  )
  gate2Pass = !!(g2v && g2v.verify_rc === 0)
  if (gate2Pass) { log('  Gate 2 PASS [harness-verified: verify-gate rc=0, verdict recorded in gate_verify.jsonl]'); break }
  log('  Gate 2 not yet PASS [' + (g2v ? String(g2v.detail ?? '') : 'verify agent null') + '] — retry round ' + (round + 1))
}
if (gate2Blocked) {
  return { session_limit_blocked: true, gate: 2, message: 'Agent hit session/rate limit during Gate 2 evaluation. Resume after quota reset — GUARD checks will skip completed FRs.' }
}
if (!gate2Pass) {
  log('  Gate 2 exhausted 3 rounds — generating deferred_fixes.md')
  const gate2StateCmd = PY + ' -c "import json; g=(json.load(open(\'' + REPO + '/.methodology/quality_manifest.json\')).get(\'gate_results\',{}) or {}).get(\'gate2\') or {}; print(json.dumps({\'score\': g.get(\'score\'), \'qc\': g.get(\'quality_complete\'), \'dims\': g.get(\'dimensions\',{})}))"'
  await dispatch(
    'YOU ARE THE DEFERRED-FIX RECORDER. Gate 2 failed to reach PASS in 3 rounds.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + '1. Get the last-known Gate 2 state:\n`' + gate2StateCmd + '`\n'
    + '2. Run `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 60.0; echo "RC=$?"` for the D4 status.\n'
    + '3. Run `' + PY + ' ' + REPO + '/harness_cli.py crg-arch-check --project ' + REPO + '; echo "RC=$?"` for the CRG architecture status.\n'
    + '4. Write `' + REPO + '/.methodology/deferred_fixes.md` with:\n'
    + '   - A brief header: "Gate 2 — deferred fixes" + date + last-known composite score\n'
    + '   - Each failing dimension (score below its threshold) as a `- [ ]` checkbox item\n'
    + '   - D4 as a `- [ ]` checkbox item (spec-coverage < 80%)\n'
    + '   - CRG architecture as a `- [ ]` checkbox item if RC != 0 (architecture score < 80%)\n'
    + '   - Each item MUST cite the current score AND the required threshold\n'
    + '   - A final "Next step:" line: "Resolve every item → re-run Phase 3 Gate 2 → advance-phase"',
    { label: 'deferred-fixes-g2', phase: 'Gate 2', agentType: 'general-purpose' },
  )
  return halt('gate2', { error: 'Gate 2 did not PASS in 3 rounds (HR-08; write deferred_fixes.md + escalate to human)', owner: 'project', raw: String(gate2Report ?? '').slice(-600) })
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Preview Next-Phase
// ══════════════════════════════════════════════════════════════════════════

phase('Preview Next-Phase')
log('preview-next-phase --phase 3 (predict Phase 4 entry-blocking findings before Push)')
const MAX_PREVIEW_FIX_ROUNDS = 3
let previewClean = false, previewReport = null, previewReason = ''
for (let round = 1; round <= MAX_PREVIEW_FIX_ROUNDS; round++) {
  previewReport = await dispatch(
    'YOU ARE THE PHASE-3 PRE-PUSH OBLIGATION CHECKER. Round ' + round + '/' + MAX_PREVIEW_FIX_ROUNDS + '.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Run EXACTLY: `' + PY + ' ' + REPO + '/harness_cli.py preview-next-phase --phase 3 --project ' + REPO + '`\n'
    + 'READ-ONLY — no state/HANDOVER/commit writes.\n\n'
    + 'Report via the StructuredOutput tool: pass = true ONLY if the output says "clean — no blocking obligations predicted"; reason = the verbatim output (or its obligation lines if long).',
    { label: 'preview-next-phase-r' + round, phase: 'Preview Next-Phase', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
  )
  if (previewReport === null || previewReport === undefined) {
    return halt('preview-next-phase-unmeasured', { error: 'preview-next-phase was never read, so Phase 4 entry is unknown, not blocked', reason: 'agent returned null (skipped or terminal API error)' })
  }
  previewClean = previewReport.pass === true
  if (previewClean) { log('  → Preview Next-Phase: clean'); break }
  previewReason = String(previewReport.reason ?? '').trim()
  if (previewReason === '') {
    return halt('preview-next-phase-unmeasured', { error: 'checker reported not-clean and named no obligation, so no fixer has anything to open', reason: 'pass=false with an empty reason' })
  }
  log('  → obligation(s) found (round ' + round + '/' + MAX_PREVIEW_FIX_ROUNDS + ')')
  if (round < MAX_PREVIEW_FIX_ROUNDS) {
    const fixReport = await dispatch(
      'YOU ARE THE PHASE-3 PRE-PUSH OBLIGATION FIXER. Round ' + round + '.\n'
      + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
      + 'The following obligations were predicted to block Phase 4 entry:\n\n'
      + previewReason + '\n\n'
      + 'Each names a file/rule_id — open it, close the gap surgically. Never fabricate a case to force a citation.\n\n'
      + 'SCOPE:\n- ONLY what is named.\n- NOT harness/ (HR-17) — a framework bug: STOP, report, don\'t route around it.\n- NOT phase-transition/push/advance-phase.',
      { label: 'preview-fix-r' + round, phase: 'Preview Next-Phase', agentType: 'general-purpose' },
    )
    if (fixReport === null || fixReport === undefined || fixReport === '' || typeof fixReport !== 'string') {
      log('  preview-next-phase-fix agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
      return { session_limit_blocked: true, phase: 3, step: 'preview-next-phase-fix', message: 'Agent hit session/rate limit during the pre-push obligation fixer. Resume after quota reset — state.json is untouched.' }
    }
  }
}
if (!previewClean) {
  return halt('preview-next-phase', { error: 'Phase 4 entry obligations still present after ' + MAX_PREVIEW_FIX_ROUNDS + ' round(s) — escalate to human', raw: previewReason.slice(-1200) })
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Advance
// ══════════════════════════════════════════════════════════════════════════

phase('Advance')
log('p3-post-gate2 milestone + advance-phase --completed 3 (TDD-PRECHECK enforced)')
// Round loop (2026-07-02 audit finding, ported from phase3): advance-phase
// enforces more independent checks than any single prompt can safely
// enumerate, and a static checklist goes stale the moment harness adds or
// changes one. advance-phase is idempotent (preflight runs before any
// FSM/state write), so the robust fix is an outer retry loop where the
// agent reads advance-phase's own [BLOCKED] output each round instead of
// guessing in advance.
let advancePass = false, advanceReport = ''
const ADVANCE_MAX_ROUNDS = 5
for (let round = 1; round <= ADVANCE_MAX_ROUNDS; round++) {
  log('  Advance round ' + round + '/' + ADVANCE_MAX_ROUNDS)
  // Manifest integrity: enforced by advance-phase itself since Round 22 站2
  // (cli/phase_cmds.py::_advance_prechecks, exit 27 with the restore command
  // in its [BLOCKED] message). It runs first, before any other precheck, and
  // on every round because advance-phase is idempotent — same guarantee the
  // per-round dispatch here used to buy, minus the dispatch, and now covering
  // the human/CI callers this loop never could.
  advanceReport = await dispatch(
    'YOU ARE THE PHASE-3 EXIT ORCHESTRATOR. Advance to Phase 4. ROUND ' + round + '.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Steps:\n'
    + '0. GUARD — already advanced? `PHASE=$(jq -r .current_phase ' + REPO + '/.methodology/state.json 2>/dev/null); echo "current_phase=$PHASE"; [ "$PHASE" -ge 4 ]`. If Phase 4 is confirmed, report "ADVANCE: PASS (already advanced)" and stop.\n'
    + '1. RE-VERIFY GATE 2 (do this FIRST): `' + PY + ' ' + REPO + '/harness_cli.py verify-gate --project ' + REPO + ' --gate 2 --phase 3 --spec-threshold 60.0`\n   The earlier Gate 2 PASS was measured on the tree as it stood THEN; every step since has written the delivered tree, and advance-phase compares that verdict\'s digest against the tree it is about to record. This is what makes the verdict describe the tree being advanced. Non-zero exit: its [BLOCKED] line names which check regressed — fix it and re-run this step.\n'
    + '2. GUARD + PUSH ⑤ p3-post-gate2: `git -C ' + REPO + ' log --oneline --grep="P3-post-gate2)" -1`. If a commit exists, skip the push. Else: `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p3-post-gate2 --project ' + REPO + ' --fr-ids ' + gate1Pass.join(',') + '`\n   Pre-flight (enforced): gate2_result.json composite ≥75 + per-FR Gate 1 sentinel .sessi-work/sentinels/g1_p3_<fr>.flag exists for every FR. If BLOCKED, read the error list and fix.\n'
    + '3. advance-phase — run BACKGROUNDED (internally runs `ruff check .` + `mypy .` + `pytest --cov-fail-under=100` over the WHOLE project as sequential subprocess calls inside one opaque Bash call; harmless today at this project\'s size (~25s measured) but this cost only grows as more FRs/tests land, and a single opaque long Bash call is exactly what the 180s stall watchdog kills — same class of risk as GATE1, same fix):\n   a. Launch: `nohup ' + PY + ' ' + REPO + '/harness_cli.py advance-phase --completed 3 --project ' + REPO + ' > /tmp/advance_r' + round + '.log 2>&1 & echo $!` — note the printed PID.\n   b. Poll: every 15s run `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Repeat until DONE (cap 40 polls / ~10min). Still RUNNING past the cap → `kill <PID>` (reaps the whole tree), report "ADVANCE: TIMEOUT".\n   c. Once DONE: `cat /tmp/advance_r' + round + '.log` for the full output — identical to what a synchronous run would have printed.\n   advance-phase independently re-verifies EVERYTHING before it will advance (lint, types, coverage, document quality, reliability lint, architecture drift, Phase Truth, and more) — its own output tells you exactly what is missing. If it prints "[BLOCKED] ...", that message IS the fix instruction: read it verbatim and do exactly what it says (it often includes the precise command to run), then repeat the advance-phase backgrounded procedure (a/b/c). Do NOT guess what might be wrong — trust only what advance-phase itself reports.\n   advance-phase is safe to re-run: it re-checks and re-reports without side effects until every check passes, so iterate within this round as many times as needed.\n'
    + '4. Read ' + REPO + '/.methodology/state.json; confirm current_phase = 4 (advance-phase atomically writes state.json when complete).\n\n'
    + 'Report final line: "ADVANCE: PASS|FAIL — <details>". If still FAIL after exhausting this round\'s turn, report the LAST [BLOCKED] message verbatim so the next round starts from where this one left off. PHASE_4_PLAN: ' + REPO + '/.methodology/phase4_plan.md\n\n'
    + 'SCOPE RULES:\n- DO NOT re-implement FRs.\n- DO NOT use --no-verify.\n- DO NOT modify harness/ (HR-17).\n- ONLY verify-gate + push-milestone p3-post-gate2 + advance-phase + verify HANDOVER.md + the specific fixes advance-phase\'s own output asked for.\n- Any diagnostic/debug script MUST be written under .sessi-work/tmp/ (never repo root or source dirs) and self-cleaned before you exit.',
    { label: 'advance-r' + round, phase: 'Advance', agentType: 'general-purpose' },
  )
  if (advanceReport === null || advanceReport === undefined || advanceReport === '' || typeof advanceReport !== 'string') {
    log('  advance agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
    return { session_limit_blocked: true, phase: 3, step: 'advance', message: 'Agent hit session/rate limit during Advance. Resume after quota reset — the GUARD step skips if already advanced.' }
  }
  // AUTHORITATIVE Advance verdict: advance-phase atomically writes
  // state.json current_phase=4 on success. Read it via a schema proxy —
  // the orchestrator's prose "ADVANCE: PASS" is narrative only.
  const advVerifyCmd = PY + ' -c "import json; print(json.dumps({\'current_phase\': int(json.load(open(\'' + REPO + '/.methodology/state.json\')).get(\'current_phase\') or 0)}))"'
  const advV = await dispatch(
    'Run EXACTLY this command via the Bash tool (stdout is a single JSON line):\n`' + advVerifyCmd + '`\n'
    + 'Then report via the StructuredOutput tool: current_phase = the exact integer from that JSON.',
    { label: 'advance-verify-r' + round, phase: 'Advance', agentType: 'general-purpose', schema: PHASE_SCHEMA },
  )
  advancePass = !!(advV && advV.current_phase >= 4)
  if (advancePass) {
    log('  Advance PASS [harness-verified: state.json current_phase=' + advV.current_phase + ']')
    break
  }
  log('  Advance not yet PASS [state.json current_phase=' + (advV ? advV.current_phase : '?') + '] — retry round ' + (round + 1))
}

if (!advancePass) {
  return halt('advance', { error: 'Advance did not PASS in ' + ADVANCE_MAX_ROUNDS + ' rounds — check HANDOVER.md + state.json + the last [BLOCKED] message below. If Phase 4 is confirmed, resume workflow to verify.', raw: String(advanceReport ?? '').slice(-600) })
}

// Bug A fix (2026-07-07): advance-phase intentionally commits the handover
// locally without pushing (harness/cli/phase_cmds.py: "next milestone push
// publishes to origin"). This workflow ends right after Advance with no
// next-phase push queued, so the handover commit was left stranded on
// local until whatever runs next happened to push it. Publish it now.
phase('Sync')
log('git push origin main (publish advance handover commit)')
const SYNC_MAX_ATTEMPTS = 3
const SYNC_PROMPT = 'Run this command via Bash:\n'
  + 'git -C ' + REPO + ' push origin main\n\n'
  + 'If the push is REJECTED, the pre-push hook has already printed why: it runs the full phase preflight, so the blocker is almost always project CONTENT (a `# pragma: no cover`, a missing artifact block, an unregistered SAB module), not the network. Read the blocker list, fix exactly what it names, and push again. Do NOT use --no-verify. If the output contains [HARNESS-BUG], stop — harness-methodology crashed and there is nothing in this project to fix.\n\n'
  + 'Report final outcome as plain text: "SYNC: PASS" or "SYNC: FAIL — <one-line reason>"'
  + ' (if the pre-push hook printed a blocker list, include it verbatim).'
let syncReport = ''
let syncPass = false
for (let sAttempt = 1; sAttempt <= SYNC_MAX_ATTEMPTS; sAttempt++) {
  syncReport = await dispatch(SYNC_PROMPT, { label: 'sync-' + sAttempt, phase: 'Sync', agentType: 'general-purpose' })
  const syncText = String(syncReport ?? '')
  syncPass = /SYNC:\s*PASS/.test(syncText)
  if (syncPass) break
  if (/\[HARNESS-BUG\][^\n]*\n {2}This is a bug in harness-methodology itself/i.test(syncText)) {
    log('  Sync reports [HARNESS-BUG] — harness-methodology crashed; not a project blocker and not something a retry can clear')
    return { harness_bug_detected: true, step: 'sync', message: 'git push was rejected by a harness-methodology crash ([HARNESS-BUG] — see the crash bundle path in the log), not by a project quality failure. A human must fix the harness bug.', raw: syncText.slice(-600) }
  }
  log('  Sync attempt ' + sAttempt + '/' + SYNC_MAX_ATTEMPTS + ' did not PASS — read the pre-push blocker list, fix what it names, retry')
}
if (!syncPass) {
  // Do NOT auto `--no-verify` (HR-17 forbids bypassing the gate without a
  // human decision). Surface the blocker instead of terminating with a bare
  // error: state.json current_phase is already authoritative for Phase 4
  // (Advance PASS'd above), the handover commit just hasn't reached origin
  // yet — a human resolves the printed blocker(s) and pushes manually.
  const blockers = String(syncReport ?? '').slice(-600)
  await dispatch(
    'Append this section to the END of ' + REPO + '/HANDOVER.md (append — do not overwrite '
    + 'existing content; create the file only if it truly does not exist):\n\n'
    + '## Sync Blocked — manual push required\n\n'
    + 'The Phase 3 advance handover commit landed locally but `git push origin main` '
    + 'did not pass the pre-push hook after ' + SYNC_MAX_ATTEMPTS + ' attempts, the last of which was allowed to fix what the hook named:\n\n'
    + '```\n' + blockers + '\n```\n\n'
    + 'Resolve the blocker(s) above, then run `git push origin main` manually. '
    + 'Do NOT use `--no-verify` without explicit human sign-off.\n',
    { label: 'sync-handover-note', phase: 'Sync', agentType: 'general-purpose' },
  )
  log('Phase 3 workflow ends with Sync unresolved — see HANDOVER.md "Sync Blocked" section.')
  return {
    phase: 3,
    fr_count: frIds.length,
    gate1_pass: gate1Pass,
    gate2_status: gate2Pass ? 'PASS' : 'unknown',
    advance_status: 'PASS',
    sync_status: 'MANUAL_REQUIRED',
    blockers,
    artifacts: ['03-development/src/', 'tests/', '.methodology/gate2_result.json', 'HANDOVER.md'],
    notes: 'Phase 3 complete (Advance PASS) but the handover commit could not be auto-pushed — see HANDOVER.md "Sync Blocked" section for the pre-push blocker list.',
  }
}


log('Phase 3 workflow complete. Open .methodology/phase4_plan.md to continue.')
return {
  phase_complete: true,
  phase: 3,
  fr_count: frIds.length,
  gate1_pass: gate1Pass,
  gate2_status: gate2Pass ? 'PASS' : 'unknown',
  advance_status: 'PASS',
  sync_status: 'PASS',
  artifacts: ['03-development/src/', 'tests/', '.methodology/gate2_result.json', 'HANDOVER.md'],
  notes: 'Phase 3 complete per phase3_plan.md v2.12.0. All FRs Gate 1 PASS + Gate 2 PASS. Phase 4 (Testing) ready.',
}
} catch (err) {
  const msg = (err && err.message) ? err.message : String(err)
  return {
    error: 'workflow crashed: ' + msg.slice(0, 300),
    workflow: meta.name,
    crashed: true,
    note: 'An agent dispatch threw instead of returning a result — most often a transient transport error, which the Workflow runtime does not retry or catch. Nothing was skipped silently: relaunch this workflow and its GUARD/sentinel checks short-circuit the work that already completed.',
  }
}
