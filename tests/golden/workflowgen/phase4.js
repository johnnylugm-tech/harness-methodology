// Phase 4 — Testing (faithful to .methodology/phase4_plan.md v2.12.0)
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/phase_specs.py::generate_phase4() (+ js_blocks.py for
// the blocks shared across phase workflow files). Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write --phase 4
//
// Structure: FR-loop型 + adversarial bug hunt + Gate 3 (17 dims) exit.
// CHECKPOINT-0 TEST_PLAN → per-FR GATE1-DELTA → TEST_RESULTS/COVERAGE →
// Step 4b bug hunt (adversarial_review is a Gate 3 dim, needs bug_hunt_report.json)
// → Gate 3 → p4-pre-gate3 milestone + advance.
//
// Playbook lessons: NO import/fs/process, Bash CLI, SCOPE RULES,
// PY = .venv/bin/python, scriptPath launch.
// v4 (2026-07-02): gate verdicts use FLAT schema: (playbook §5.2 rev) — regex
// over LLM prose was the root cause of the #126/#134/#135/#136/ENV_CHECK_RC
// bug class. Heavy orchestrators keep prose narrative; verdicts come from
// schema proxy agents reading harness artifacts (manifest qc, state.json, rc).


export const meta = {
  name: 'phase4-testing',
  description: 'Phase 4 Testing — TEST_PLAN + per-FR GATE1-DELTA + adversarial bug hunt + Gate 3 (17 dims) exit (phase4_plan.md v2.12.0)',
  phases: [
    { title: 'Entry & Preflight' },
    { title: 'Test Plan' },
    { title: 'Env Check' },
    { title: 'Load FRs' },
    { title: 'Per-FR Delta' },
    { title: 'Coverage' },
    { title: 'Bug Hunt' },
    { title: 'Artifacts Commit' },
    { title: 'Gate 3' },
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

// Bug hunt should use a DIFFERENT model from the developer (minimise same-source bias).
const HUNT_MODEL = (args && typeof args === 'object' && typeof args.huntModel === 'string') ? args.huntModel : 'claude-opus-4-8'
log('HUNT_MODEL = ' + HUNT_MODEL)


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
  },
  required: ['fr_ids', 'fr_count'],
}
const DELTA_FAST_SCHEMA = {
  type: 'object',
  properties: {
    pass_fr_ids: { type: 'array', items: { type: 'string' }, description: 'FRs whose manifest gate1 quality_complete printed True after GATE1-DELTA' },
    fail_fr_ids: { type: 'array', items: { type: 'string' }, description: 'FRs that did not print True (False/None/timeout/error)' },
  },
  required: ['pass_fr_ids', 'fail_fr_ids'],
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
log('ENTRY-CHECK Gate2 + run-phase 4 (reliability/config/attestation fixes) + handoff + CI')
const preflightReport = await dispatch(
  'YOU ARE THE PHASE-4 PREFLIGHT ORCHESTRATOR. Run bash in order; report.\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
  + 'Steps:\n'
  + '1. ENTRY-CHECK: run EXACTLY this bash command to verify Gate 2 status (do NOT rely on reading the file yourself — use the command output):\n`' + PY + ' -c "import json; m=json.load(open(\'' + REPO + '/.methodology/quality_manifest.json\')); g2=(m.get(\'gate_results\',{}) or {}).get(\'gate2\',{}) or {}; print(\'GATE_VERIFIED\' if isinstance(g2,dict) and g2.get(\'quality_complete\') is True else \'GATE_MISSING\')"`\nIf GATE_MISSING → FAIL (return to Phase 3).\n'
  + '2. PREFLIGHT: `' + PY + ' ' + REPO + '/harness_cli.py run-phase --phase 4 --project ' + REPO + '`. FAIL → fix, re-run (max 3). Also fix if reported: reliability lint (subprocess timeout / mkstemp / TOCTOU / sleep-in-async), config liveness (env keys absent from .env.example), attestation missing/mismatch (build-trace-attestation --write + commit; re-run until "Attestation: clean"), property_spec (an FR declares a Properties invariant in TEST_SPEC.md but no test executes it — write a hypothesis @given (Python) / fast-check (JS/TS) test exercising the declared invariant for that FR, then re-run).\n'
  + '3. HANDOFF: `' + PY + ' ' + REPO + '/harness_cli.py validate-handoff --from-phase 3 --project ' + REPO + '`. Must exit 0.\n'
  + '4. PREFLIGHT-CI: confirm `' + REPO + '/.github/workflows/harness_quality_gate.yml` (CI workflow) + `' + REPO + '/.git/hooks/prepare-commit-msg` (git hook) both exist; confirm state.json current_phase=4. If stale: `init-project --phase 4 --project ' + REPO + ' --overwrite`.\n\n'
  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if ALL 4 steps succeeded; reason = one-line summary (on FAIL: which step + verbatim error tail).\n\n'
  + 'SCOPE RULES:\n- DO NOT generate TEST_PLAN / run TDD / run-gate / bug hunt.\n- DO NOT run advance-phase/push-milestone.\n- DO NOT modify harness/.\n- ONLY preflight commands + fixes.',
  { label: 'preflight', phase: 'Entry & Preflight', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
)
if (!(preflightReport && preflightReport.pass === true)) {
  return halt('preflight', { error: 'Phase 4 preflight did not PASS', reason: preflightReport ? String(preflightReport.reason ?? '').slice(-600) : 'agent returned null (skipped or terminal API error)' })
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Test Plan
// ══════════════════════════════════════════════════════════════════════════

phase('Test Plan')
log('Generate 04-testing/TEST_PLAN.md from SRS FR acceptance criteria')
const testPlanReport = await dispatch(
  'YOU ARE THE P4 TEST PLAN AUTHOR. Generate TEST_PLAN.md (runs once before per-FR testing).\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
  + 'Steps (create 04-testing/ if missing):\n'
  + '1. Read 01-requirements/SRS.md FR acceptance criteria + .methodology/quality_manifest.json FR list.\n'
  + '2. Write ' + REPO + '/04-testing/TEST_PLAN.md. For each FR: test case ID, description, input, expected output, priority. Include positive, negative, boundary, and edge-case categories. Cover ALL FRs + NFRs.\n'
  + '3. Verify TEST_PLAN.md covers every FR from the manifest.\n\n'
  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if TEST_PLAN.md was written and covers every FR; reason = one-line summary.\n\n'
  + 'SCOPE RULES:\n- DO NOT run TDD/run-gate/bug-hunt/advance.\n- DO NOT modify harness/.\n- ONLY author TEST_PLAN.md.',
  { label: 'test-plan', phase: 'Test Plan', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
)
if (!(testPlanReport && testPlanReport.pass === true)) {
  return halt('test-plan', { error: 'Phase 4 TEST_PLAN did not PASS', reason: testPlanReport ? String(testPlanReport.reason ?? '').slice(-500) : 'agent returned null' })
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
const envCheckLog = '/tmp/envcheck_phase4.log'
const envCheckChain = PY + ' ' + REPO + '/harness_cli.py run-env-check --phase 4 --project ' + REPO + ' && ' + PY + ' ' + REPO + '/harness_cli.py finalize-env-check --phase 4 --project ' + REPO + '; echo "RC=$?"'
const envReport = await dispatch(
  'YOU ARE THE PHASE-4 ENV-CHECK ORCHESTRATOR (Bash-timeout-aware, background poll).\n'
  + 'REPO: ' + REPO + '\n'
  + 'PYTHON: ' + PY + '\n'
  + 'LOG PATH: /tmp/envcheck_phase4.log\n\n'
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
  + '   If still RUNNING past 22 polls (~19 min) → report "ENV_CHECK: TIMEOUT" via StructuredOutput and stop (do NOT kill the PID — it is still legitimately running; resume by re-running this same step).\n\n'
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
  return halt('env-check', { error: 'Phase 4 env-check did not PASS', rc: envReport ? envReport.rc : null, ready: envReport ? envReport.ready : null, note: envReport ? ('run-env-check/finalize-env-check rc=' + envReport.rc + ' ready=' + envReport.ready + ' — read ' + _envCheckResult) : 'agent returned null (skipped or terminal API error)' })
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Load FRs
// ══════════════════════════════════════════════════════════════════════════

phase('Load FRs')
log('load-context --phase 4 → fr_ids')
// v15: retry loop — agent() wrapped (Bug #2); v4: schema transport, no prose parsing
// v2.13.1: hardened against agent hallucination (Bug #122).
let ctx = null
const ctxFile = REPO + '/.sessi-work/phase4_ctx.json'
// Round 22 站3: the read used to be preceded by a separate ctx-check
// dispatch that ran `json.load(ctxFile)` purely to prove the file was
// parseable. The read below runs `json.load(ctxFile)` too — its failure
// condition is a superset of the probe's — so the probe could only ever
// confirm what the next command was about to establish, at the cost of a
// full sub-agent dispatch per phase. Bug #134's actual fix (parse the
// JSON rather than stat the file, so a partial write cannot pass) lives
// in the command below and is unaffected; Bug #136's template-literal
// quoting likewise. A failed read now routes to the same regen path the
// probe used to trigger — the two cases it distinguished (file missing
// vs. file unparseable) had identical handling anyway.
for (let attempt = 1; attempt <= 3; attempt++) {
  // Bug #135 fix (2026-06-28) + v4 schema transport: emit parseable JSON via
  // Python; the agent transcribes the fields into StructuredOutput (AJV-
  // validated, retries on mismatch). No prose parsing left on this path.
  try {
    const ctxParseCmd = `${PY} -c "import json; d=json.load(open('${ctxFile}')); print(json.dumps({'fr_ids':d.get('fr_ids',[]),'fr_count':len(d.get('fr_ids',[]))}))"`
    const ctxResult = await dispatch(
      `You MUST use the Bash tool. Run exactly:\n${ctxParseCmd}\nThe command FAILS (nonzero exit, Python traceback) when the file is missing or not valid JSON — report that verbatim rather than inventing values. On success stdout is a single JSON line: report via the StructuredOutput tool fr_ids, fr_count = the EXACT values from that line (transcribe, do not recompute).`,
      { label: 'load-ctx-a' + attempt, phase: 'Load FRs', agentType: 'general-purpose', schema: CTX_SCHEMA },
    )
    if (ctxResult && Array.isArray(ctxResult.fr_ids) && ctxResult.fr_ids.length > 0) {
      ctx = ctxResult
      log('  load-ctx OK (schema-validated, ' + ctx.fr_ids.length + ' FRs)')
      break
    }
    log('  load-ctx returned no fr_ids (attempt ' + attempt + '): keys=' + Object.keys(ctxResult ?? {}).join(',') + ' — regenerating ctx file')
  } catch (e) { log('  load-ctx agent failed: ' + String(e.message ?? e).slice(0, 80) + ' — regenerating ctx file') }

  const ctxRegenCmd = `${PY} ${REPO}/harness_cli.py load-context --phase 4 --project ${REPO} --json > ${ctxFile} && ${PY} -c "import json,os; json.load(open('${ctxFile}')); print('REGEN_OK_'+str(os.path.getsize('${ctxFile}')))"`
  try {
    await dispatch(
      `You MUST use the Bash tool. Run exactly:\n${ctxRegenCmd}\nReturn the raw stdout as your final message.`,
      { label: 'ctx-regen-' + attempt, phase: 'Load FRs', agentType: 'general-purpose' },
    )
  } catch (e) { log('  ctx-regen agent failed: ' + String(e.message ?? e).slice(0, 80)) }
}
if (!ctx) return halt('load-frs', { error: 'Load FRs: ctx failed after 3 attempts', ctxFile })
let frIds = Array.isArray(ctx.fr_ids) ? ctx.fr_ids
  : (Array.isArray(ctx.fr_details) ? ctx.fr_details.map(f => f.id || f.fr_id || f.fr).filter(Boolean) : [])
if (!frIds.length) return halt('load-frs', { error: 'Load FRs: no fr_ids found in ctx', ctxKeys: Object.keys(ctx) })
const frTitle = {}
if (Array.isArray(ctx.fr_details)) for (const f of ctx.fr_details) frTitle[f.id || f.fr_id] = f.title || f.name || ''
log('  fr_ids = ' + JSON.stringify(frIds))


// ══════════════════════════════════════════════════════════════════════════
// Phase: Per-FR Delta
// ══════════════════════════════════════════════════════════════════════════

phase('Per-FR Delta')
const gate1Pass = []
const gate1Fail = []
let p4MidPushed = false
const p4MidThreshold = Math.ceil(frIds.length / 2)  // PUSH ⑤ trigger: ≥50% FRs Gate 1 PASS
// DELTA fast-path: probe every FR's GATE1-DELTA through the harness CLI in ONE
// agent — unchanged-code FRs pass immediately inside the CLI, so N already-PASS
// FRs cost 1 spawn instead of 2N (delta + verify). Verdict authority is manifest
// qc AND a phase-scoped gate_timestamps.jsonl entry (NOT the agent's self-report).
// The timestamp is required because manifest qc is not phase-scoped: a stale
// `true` from an earlier phase would mask a timed-out/failed run-fr-step this
// phase. run-fr-step writes the {phase, gate:1, fr_id} timestamp only on
// successful completion (both the unchanged-skip and full-dispatch paths); a
// killed dispatch writes nothing, so absence ⇒ fail ⇒ full per-FR loop.
let deltaTodo = frIds
const fastProbe = await dispatch(
  'YOU ARE THE GATE1-DELTA FAST-PATH PROBE. Classify each FR — fix NOTHING.\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\nFRs: ' + JSON.stringify(frIds) + '\n\n'
  + 'Direction C (past lessons): BEFORE classifying, Bash `cat ' + REPO + '/.sessi-work/phase4_ctx.json` and READ the `lessons` field (compact markdown, "" if none). DO NOT repeat those past failure modes in your pass/fail classification or any follow-up P4 work.\n\n'
  + 'For EACH FR in order, substituting <FR> with the FR id:\n'
  + '1. GATE1-DELTA is long-running for any FR whose code actually changed (harness runs up to 3 internal CODE-FIX rounds, each up to ~600s — can silently block ~2400s worst case even though this step is a "probe"). Run it BACKGROUNDED for every FR, not just slow ones — unchanged FRs still hit the fast in-CLI short-circuit almost instantly so this costs nothing extra:\n'
  + '   a. `nohup ' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 4 --fr-id <FR> --step GATE1-DELTA --project ' + REPO + ' > /tmp/gate1delta_<FR>.log 2>&1 & echo $!` — note the PID.\n'
  + '   b. Poll with BACKOFF intervals, in seconds: 5, 10, then 30 for every further iteration — `sleep <interval> && kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Cap 42 polls (5+10 + 40x30 ≈ 20min). Still RUNNING past the cap → classify <FR> as fail_fr_ids (the full loop below will retry it) and move to the next FR — do not kill the PID.\n'
  + '      (Round 22 站4: the first interval used to be a flat 30s. An unchanged FR hits the in-CLI short-circuit almost instantly, and this probe walks the FRs one at a time, so a fixed first sleep cost 30s x N — ten minutes on a 20-FR project spent waiting on commands that had already returned.)\n'
  + '   c. DONE → proceed to step 2 (the log itself is not needed — the authoritative verdict is the manifest read below).\n'
  + '2. Authoritative verdict (manifest qc AND a phase-4 gate-1 timestamp for <FR>): `' + PY + ' -c "import json; g=(json.load(open(\'' + REPO + '/.methodology/quality_manifest.json\')).get(\'gate_results\',{}) or {}).get(\'gate1\',{}).get(\'<FR>\',{}) or {}; ts=any(e.get(\'phase\')==4 and e.get(\'gate\')==1 and e.get(\'fr_id\')==\'<FR>\' for e in (json.loads(l) for l in open(\'' + REPO + '/.methodology/gate_timestamps.jsonl\') if l.strip())); print(bool(g.get(\'quality_complete\')) and ts)"`\n'
  + '   stdout `True` → pass_fr_ids; anything else (False/None/timeout/error/missing file) → fail_fr_ids.\n\n'
  + 'HARD RULES:\n- DO NOT fix code, edit files, or run TDD steps.\n- DO NOT retry a failing FR — classify it and move on (the full loop handles it).\n- DO NOT run run-gate / bug-hunt / advance-phase / push-milestone.\n- DO NOT modify harness/.\n\n'
  + 'Report via the StructuredOutput tool: pass_fr_ids + fail_fr_ids (every FR in exactly one list).',
  { label: 'delta-fastpath', phase: 'Per-FR Delta', agentType: 'general-purpose', schema: DELTA_FAST_SCHEMA },
)
if (fastProbe && Array.isArray(fastProbe.pass_fr_ids)) {
  const fastPassed = fastProbe.pass_fr_ids.filter((f) => frIds.includes(f))
  for (const fr of fastPassed) {
    gate1Pass.push(fr)
    log('  ' + fr + ' GATE1-DELTA fast-path PASS [manifest qc + p4 timestamp] — full DELTA skipped')
  }
  deltaTodo = frIds.filter((f) => !fastPassed.includes(f))
} else {
  log('  delta-fastpath unavailable — falling back to full per-FR loop')
}
for (const frId of deltaTodo) {
  log('  === ' + frId + ' — GATE1-DELTA ===')
  const frReport = await dispatch(
    'YOU ARE THE TEST VERIFIER for ' + frId + ' (' + (frTitle[frId] || '') + '). Re-evaluate Gate 1 for THIS ONE FR.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Steps:\n'
    + '1. GATE1-DELTA — long-running when code changed (harness runs up to 3 internal CODE-FIX rounds plus, on FAIL, a full TDD-RED→GREEN→IMPROVE→GATE1 chain — can silently block well past 180s). Run it BACKGROUNDED, do NOT invoke it as a plain synchronous command:\n'
    + '   a. `nohup ' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 4 --fr-id ' + frId + ' --step GATE1-DELTA --project ' + REPO + ' > /tmp/gate1delta_' + frId + '.log 2>&1 & echo $!` — note the PID.\n'
    + '   b. Poll every 30s: `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Cap 60 polls (~30min — this path can chain a full TDD cycle on top of GATE1-DELTA\'s own retries). Still RUNNING past the cap → report "' + frId + ' GATE1: TIMEOUT" (not FAIL) and stop — do not kill the PID.\n'
    + '   c. DONE → `cat /tmp/gate1delta_' + frId + '.log` for the full output, identical to a synchronous run. Parse PASS/FAIL from it.\n'
    + '   - PASS → done.\n'
    + '   - FAIL → full TDD auto-triggered: TDD-RED → TDD-GREEN → TDD-IMPROVE → GATE1 (each for ' + frId + '). Max 3 rounds. Still failing → report FAIL.\n'
    + '   If ' + frId + '’s code is unchanged since last Gate 1 PASS, this passes immediately.\n\n'
    + 'Report final line: "' + frId + ' GATE1: PASS" or "' + frId + ' GATE1: FAIL — <reason>".\n\n'
    + 'SCOPE RULES:\n- DO NOT touch any FR OTHER than ' + frId + '.\n- DO NOT run run-gate / bug-hunt / advance-phase / push-milestone.\n- DO NOT edit .methodology/quality_manifest.json or .sessi-work/gate1_result.json to fake/reset scores — fix the underlying code/tests instead.\n- DO NOT modify harness/.\n- ONLY GATE1-DELTA (+ full TDD if needed) for ' + frId + '.',
    { label: 'delta-' + frId, phase: 'Per-FR Delta', agentType: 'general-purpose' },
  )
  // L1 (ported from phase3): distinguish a session/rate-limit block (null/empty
  // agent return) from a real Gate 1 FAIL — a rate-limit mid-DELTA must not be
  // misreported as a code-quality failure. DELTA auto-skip makes resume safe.
  if (frReport === null || frReport === undefined || frReport === '' || typeof frReport !== 'string') {
    log('  ' + frId + ' agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
    return { session_limit_blocked: true, phase: 4, step: frId, fr_id: frId, gate1Pass, message: 'Agent hit session/rate limit during ' + frId + ' GATE1-DELTA. Resume after quota reset — completed FRs skip via DELTA auto-satisfy.' }
  }
  // L1.5: detect a structurally-broken dispatch [FATAL] surfaced via the sub-agent
  // (harness/cli/fr_cmds.py:_abort_dispatch_structurally_broken prints "[FATAL] <fr> <step>:
  // sub-agent dispatch is structurally broken — claude.ai connectors are disabled" to
  // stderr and returns exit code 23). A sub-agent reading its own GATE1-DELTA log and seeing
  // that banner will escalate to human with "FAIL — structurally broken dispatch" even
  // when the gate has not yet run a single evaluation round. The harness-side
  // _is_connector_disabled_failure guard already catches this AT the fr_cmds.py layer
  // for LINT-FIX / COVERAGE-FIX / GATE1-final-dispatch, but the TDD dispatches AND the
  // first-round prompt path do NOT have it. Continuing to dispatch the remaining FRs in
  // that state burns ~5min and ~50K tokens per FR on identically-broken dispatches.
  // Abort once the structural signal is observed.
  const frReportText = (typeof frReport === 'string') ? frReport : JSON.stringify(frReport)
  if (/structurally broken dispatch environment/i.test(frReportText) || /\[FATAL\][^\n]*dispatch is structurally broken/i.test(frReportText)) {
    log('  ' + frId + ' reports [FATAL] structurally broken dispatch (claude.ai connectors disabled) — aborting remaining FRs')
    return { dispatch_structurally_broken: true, phase: 4, fr_id: frId, gate1Pass, gate1Fail: [...gate1Fail, frId], message: frId + ' GATE1-DELTA: dispatch is structurally broken (env: ANTHROPIC_API_KEY overrides claude.ai login). Human must unset ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN/ANTHROPIC_BASE_URL/ANTHROPIC_DEFAULT_HAIKU_MODEL in the shell that launches this process, then re-run via Workflow({scriptPath, resumeFromRunId}).' }
  }
  // L1.6 (Round 13 站0): detect a [HARNESS-BUG] banner (core/errors.py's crash
  // boundary — harness_cli.py's main() converting an uncaught exception into this
  // signal instead of a bare traceback) surfaced via the sub-agent reading its own
  // GATE1-DELTA log. Unlike the structurally-broken-dispatch signature above (a known,
  // human-actionable env-var cause), this means harness-methodology itself crashed —
  // the FR loop cannot proceed until a human fixes the harness bug, and treating it
  // as a code-quality FAIL would send CODE-FIX at a defect that isn't there.
  if (/\[HARNESS-BUG\]/.test(frReportText)) {
    log('  ' + frId + ' reports [HARNESS-BUG] — harness-methodology crashed, aborting remaining FRs')
    return { harness_bug_detected: true, phase: 4, fr_id: frId, gate1Pass, gate1Fail: [...gate1Fail, frId], message: frId + ' GATE1-DELTA: harness-methodology itself crashed ([HARNESS-BUG] — see the crash bundle path in the log). This is not a project quality issue; a human must diagnose and fix the harness bug before this FR can proceed.' }
  }
  // AUTHORITATIVE Gate 1 verdict (ported from phase3, 9fe2036): read the harness
  // quality_manifest — NOT the sub-agent's self-reported "GATE1: PASS" string. A
  // sub-agent can report PASS even when finalize-gate raised GateBlockedError,
  // silently advancing a FR the harness actually blocked (2026-06-30 incident).
  // Round 12 站2a: the deterministic read lives in the standalone helper
  // (`harness/scripts/verify_gate1_qc.py`, v2.13.3 pattern — cef32c4 deferred
  // this exact P4/P5/P7/P8 migration). The LLM is a string carrier only:
  // the verdict is derived from the echoed deterministic stdout, and the
  // LLM's own `pass` boolean is IGNORED — wf_53d055ce-d0b showed an agent
  // hallucinating pass:false against a PASS manifest; Python's printed
  // bytes cannot be flipped by a wrong boolean.
  const verdict = await dispatch(
    'You MUST use the Bash tool. Run EXACTLY this single command (single line):\n'
    + PY + ' ' + REPO + '/harness/scripts/verify_gate1_qc.py --fr-id ' + frId + ' --project ' + REPO + '\n'
    + 'Then report via the StructuredOutput tool: pass = true ONLY if the FIRST line of stdout is exactly "GATE1_VERIFIED_PASS"; reason = the verbatim stdout (do NOT paraphrase, summarize, or prepend commentary).',
    { label: 'gate1-verify-' + frId, phase: 'Per-FR Delta', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
  )
  const passed = String((verdict && verdict.reason) || '').trim().startsWith('GATE1_VERIFIED_PASS')
  if (passed) {
    gate1Pass.push(frId); log('  ' + frId + ' Gate 1 PASS [harness-verified]')
  } else { gate1Fail.push(frId); log('  ' + frId + ' Gate 1 FAIL [harness manifest qc != true; sub-agent self-report ignored]') }

  // PUSH ⑤ p4-mid — fire once when ≥50% FRs have Gate 1 PASS (but not yet all done).
  if (!p4MidPushed && gate1Pass.length >= p4MidThreshold && gate1Pass.length < frIds.length) {
    p4MidPushed = true
    log('  ≥50% FRs Gate 1 PASS (' + gate1Pass.length + '/' + frIds.length + ') — pushing p4-mid milestone')
    await dispatch(
      'YOU ARE THE P4 MID-MILESTONE PUSHER (≥50% FRs Gate 1 PASS).\n'
      + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
      + '0. GUARD: `git -C ' + REPO + ' log --oneline --grep="P4-mid)" -1`. If exists, report "MILESTONE: PASS (already pushed)" and stop.\n'
      + '1. Command: `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p4-mid --project ' + REPO
      + ' --fr-done ' + gate1Pass.length + ' --fr-total ' + frIds.length + ' --fr-ids ' + gate1Pass.join(',') + '`\n'
      + 'Writes HANDOVER.md + commits + pushes. If a hook blocks, reword commit to start with `chore(harness):` (NOT --no-verify), retry.\n\n'
      + 'Report: "MILESTONE: PASS|FAIL — <details>".\n\n'
      + 'SCOPE RULES:\n- DO NOT run run-gate / bug-hunt / advance-phase.\n- ONLY push-milestone p4-mid.',
      { label: 'milestone-p4-mid', phase: 'Per-FR Delta', agentType: 'general-purpose' },
    )
  }
}
if (gate1Fail.length) {
  return halt('gate1', { error: 'Phase 4: Gate 1 FAILED for FR(s): ' + gate1Fail.join(', ') + ' (escalate)', gate1Pass, gate1Fail })
}
if (gate1Pass.length) {
  await dispatch(
    'Run these commands via the Bash tool, in order. Report the verbatim stdout/stderr of ALL of them.\n'
    + '1. Per-FR spec coverage — run for EVERY id in the list, and do NOT stop early on a nonzero exit (each `|| true` keeps the loop going; a below-threshold FR is an early warning to report, not a reason to abort):\n'
    + '`for FR in ' + gate1Pass.join(' ') + '; do ' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 40.0 --fr-id $FR || true; done`\n'
    + '2. `' + PY + ' ' + REPO + '/harness_cli.py amend-sab --project ' + REPO + '` (project-wide, runs ONCE — it takes no --fr-id)\n\n'
    + 'SCOPE RULES:\n- ONLY the two commands above.\n- DO NOT modify harness/.',
    { label: 'orch-post', phase: 'Per-FR Delta', agentType: 'general-purpose' },
  )
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Coverage
// ══════════════════════════════════════════════════════════════════════════

phase('Coverage')
log('Generate TEST_RESULTS.md + COVERAGE_REPORT.md (cross-artifact validated at Gate 3)')
const coverageReport = await dispatch(
  'YOU ARE THE P4 COVERAGE AUTHOR. Generate the test-results + coverage deliverables.\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
  + 'Steps:\n'
  + '1. TEST_RESULTS: write ' + REPO + '/04-testing/TEST_RESULTS.md — summarise test execution: cases run, pass/fail, deferred issues. Include the VERBATIM pytest summary line of the run you are describing (the `N passed, M skipped … in T s` line pytest prints); `cross_artifact.check_test_count_reconciliation` compares its counts against the framework own run_suite measurement and reports a mismatch as CRITICAL, so this document cannot record a run over a tree the project does not deliver. Scope the run to the project test directory, NOT to the repository root — the root also holds the vendored copy of the harness, and a run from there collects thousands of the framework own tests. Measured: one project recorded `4 failed, 7563 passed, 3 skipped` for a 349-test tree, and that number then travelled into BASELINE.md and VERIFICATION_REPORT.md unchallenged.\n'
  + '2. COVERAGE: run `' + PY + ' -m pytest ' + REPO + '/03-development/tests/ --cov=03-development/src --cov-report=term-missing -q | tee ' + REPO + '/04-testing/coverage_raw.txt` then `' + PY + ' -m coverage report --format=total`. Write ' + REPO + '/04-testing/COVERAGE_REPORT.md with overall coverage % (≥80% for Gate 3), per-module breakdown, uncovered lines.\n'
  + '   WARNING: cross_artifact.py validates these numbers against live pytest --cov at Gate 3 — fabricated numbers are caught. Use REAL numbers.\n\n'
  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if both docs were written from real pytest output; reason = one-line summary.\n\n'
  + 'SCOPE RULES:\n- DO NOT run run-gate / bug-hunt / advance.\n- DO NOT modify harness/.\n- DO NOT fabricate coverage numbers.\n- ONLY generate the 2 docs from real pytest output.',
  { label: 'coverage', phase: 'Coverage', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
)
if (!(coverageReport && coverageReport.pass === true)) {
  return halt('coverage-docs', { error: 'Phase 4 coverage docs did not PASS', reason: coverageReport ? String(coverageReport.reason ?? '').slice(-500) : 'agent returned null' })
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Bug Hunt
// ══════════════════════════════════════════════════════════════════════════

phase('Bug Hunt')
log('Adversarial bug hunt (targets → scout → hunters → verify → synthesize → resolve)')
const huntReport = await dispatch(
  'YOU ARE THE ADVERSARIAL BUG HUNT ORCHESTRATOR (Step 4b, before Gate 3).\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
  + 'The Gate 3 dimension adversarial_review (threshold 100) BLOCKS the gate if .methodology/bug_hunt_report.json is absent or any confirmed critical/high finding is still "open". Run the hunt NOW.\n\n'
  + 'Steps:\n'
  + '1. HUNT-TARGETS: `' + PY + ' ' + REPO + '/harness_cli.py bug-hunt-targets --project ' + REPO + '` → .methodology/bug_hunt_targets.json (CRG hubs + mutation survivors + integration gaps). **IMPORTANT**: the file\'s `threat_model` entries (SAD.md §6 declared threats via `core.quality_gate.security_design:extract_security_block()`) are forced high-risk attack-vector seeds — INDEPENDENT of CRG/mutation signals — and MUST be present in the target list.\n'
  + '2. HUNT-RUN: execute the 4-phase protocol in ' + REPO + '/harness/harness/ssi/prompts/hunt_bugs.md (scout → lens hunters → adversarial verify → synthesize). Reference workflow: ' + REPO + '/harness/templates/workflows/hunt-bugs.js. Spawn hunters/verifiers as sub-agents (you have the Agent tool); use model ' + HUNT_MODEL + ' (DIFFERENT from the developer model to minimise same-source bias). Build the CRG graph first if needed. **`threat_model` targets**: verify the declared `mitigation` actually blocks the attack (not just that defensive-looking code exists). For each `threat_model` entry, produce a finding row in `.methodology/bug_hunt_report.json` with `attack_vector`, `attempted_exploit`, and `mitigation_effective: true|false`.\n'
  + '   Output: .methodology/bug_hunt_report.json (schema: harness/schemas/bug_hunt_report.schema.json) + human markdown under 03-development/.audit/.\n'
  + '3. HUNT-RESOLVE: for EACH confirmed critical/high finding set resolution.status:\n'
  + '   - resolved: include fix_commit (SHA) or repro_test (path in tests/).\n'
  + '   - refuted: include refute_evidence (explanation + line citation).\n'
  + '   Medium/low: record only (not required to resolve before Gate 3).\n\n'
  + 'PERMITTED actions to populate `resolved` (the `DO NOT modify harness/` scope rule covers ONLY the `harness/` submodule — project source IS in scope for HUNT-RESOLVE):\n'
  + '- Write a repro test under 03-development/tests/ that RED-fails on the bug. Apply the minimal source fix in 03-development/src/<module>.py. Confirm the repro test now PASSES (RED→GREEN anti-fabrication gate per hunt_bugs.md).\n'
  + '- Commit the repro test + source fix with `fix(<module>): <title>` prefix.\n'
  + '- Update .methodology/bug_hunt_report.json resolution.status to `resolved` with the fix_commit SHA + repro_test path.\n'
  + 'Refuted is ALSO permitted: read the offending code, find a guard/fallback the finding missed, cite the exact line numbers as refute_evidence.\n\n'
  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if bug_hunt_report.json was written AND all confirmed critical/high findings are resolved-or-refuted; reason = one-line summary. (Truth is enforced downstream: Gate 3\'s framework-owned adversarial_review dim re-reads the report itself.)\n\n'
  + 'SCOPE RULES:\n- DO NOT run run-gate (Gate 3) / advance-phase / push-milestone.\n- DO NOT modify harness/ (running its scripts/prompts is fine; editing is NOT — HR-17).\n- ONLY targets + hunt + resolve + write bug_hunt_report.json.',
  { label: 'bug-hunt', phase: 'Bug Hunt', agentType: 'general-purpose', model: HUNT_MODEL, schema: VERDICT_SCHEMA },
)
if (!(huntReport && huntReport.pass === true)) {
  return halt('bug-hunt', { error: 'Phase 4 bug hunt did not PASS (Gate 3 adversarial_review will block)', reason: huntReport ? String(huntReport.reason ?? '').slice(-600) : 'agent returned null' })
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Artifacts Commit
// ══════════════════════════════════════════════════════════════════════════

phase('Artifacts Commit')
log('Committing phase-4 artifacts (explicit paths) so a verify-handoff FAIL exit leaves a clean tree')
await dispatch(
  'Run ONE bash command and report its stdout/stderr:\n'
  + '`git -C ' + REPO + ' add 04-testing .methodology/bug_hunt_report.json .methodology/bug_hunt_targets.json .methodology/decision_logs && git -C ' + REPO + ' commit -m "chore(p4): test-plan + coverage + bug-hunt artifacts" || true`\n\n'
  + 'Report: the verbatim stdout/stderr of that command. "nothing to commit" is a valid outcome.\n\n'
  + 'SCOPE RULES:\n- DO NOT run any code, tests, gates, or phase transitions.\n- DO NOT stage any path other than the 4 listed above.\n- ONLY the git command above.',
  { label: 'artifacts-commit', phase: 'Artifacts Commit', agentType: 'general-purpose' },
)


// ══════════════════════════════════════════════════════════════════════════
// Phase: Gate 3
// ══════════════════════════════════════════════════════════════════════════

phase('Gate 3')
log('Gate 3 exit (composite ≥80, 17 dims: 14 self-scored + architecture/traceability/adversarial_review framework-owned)')
let gate3Pass = false, gate3Report = '', gate3Blocked = false
// Gate 3 pre-flight GUARD: only state.json.last_gate >= 3 proves this gate was
// truly finalized (SSI dims passed AND Phase Truth passed) — see harness_cli.py finalize-gate.
{
  const _precheckCmd = `${PY} -c "import json; lg=json.load(open('${REPO}/.methodology/state.json')).get('last_gate'); print(json.dumps({'qc': isinstance(lg,int) and lg >= 3, 'last_gate': lg}))"`
  try {
    const _preVerdict = await dispatch(
      'Run EXACTLY this command via the Bash tool:\n`' + _precheckCmd + '; echo RC=$?`\n'
      + 'Then report via the StructuredOutput tool: pass = true ONLY if the output line starts with `{"qc": true`; reason = the verbatim JSON line (excluding the RC= line).',
      { label: 'gate3-precheck', phase: 'Gate 3', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
    )
    if (_preVerdict && _preVerdict.pass === true) {
      gate3Pass = true
      log('  Gate 3 PRE-FLIGHT PASS — state.json last_gate >= 3 (gate truly finalized); skipping round loop')
    } else {
      log('  Gate 3 pre-flight: not yet finalized — proceeding to round loop')
    }
  } catch (e) {
    log('  Gate 3 pre-flight threw: ' + String(e.message ?? e).slice(0, 120) + ' — proceeding to round loop')
  }
}
if (!gate3Pass) for (let round = 1; round <= 3; round++) {
  log('  Gate 3 round ' + round + '/3')
  gate3Report = await dispatch(
    'YOU ARE THE GATE-3 ORCHESTRATOR (Phase 4 exit). ROUND ' + round + '.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Steps:\n'
    + '1. G3a: `' + PY + ' ' + REPO + '/harness_cli.py run-gate --gate 3 --phase 4 --project ' + REPO + '` (CRG recon runs inside automatically). Read the printed evaluation prompt.\n'
    + '2. G3b: Evaluate ALL Gate 3 dimensions inline per ' + REPO + '/harness/harness/ssi/prompts/evaluate_dimension.md. Write ' + REPO + '/.sessi-work/gate3_result.json.\n   17 dims per gate3_p4_exit.yaml: linting(90) type_safety(85) test_coverage(80) security(80) secrets_scanning(100) license_compliance(100) mutation_testing(70) integration_coverage(60) architecture(80) readability(80) error_handling(80) documentation(75) test_assertion_quality(60) performance(75) execute_verification_target(100) traceability(100) adversarial_review(100).\n   (A project\'s feature flags can remove dims; the `dimensions:` list run-gate just printed is the authoritative one.)\n   FRAMEWORK-OWNED (do NOT self-score — finalize-gate computes these and overwrites what you write): architecture (code-review-graph), traceability (harness-trace), adversarial_review (bug-hunt-report).\n   For any failing dim: fix ROOT CAUSE in code (ruff/pyright/tests/bandit/readability_v2/ast-error-handling/pytest-benchmark), re-run the tool, update score. (readability tool is `python3 -m harness.toolchains.readability_v2` — NOT `radon mi` — per phase3/4/6_plan.md v2.12.0.) A low architecture score has no waiver route (Round 38): fix the structure, or — only for a genuine CRG false positive — calibrate `crg_excludes` / `crg_cohesion_healthy` in .methodology/harness_config.json, which is committed and therefore applies to CI too.\n'
    + '3. G3c: `' + PY + ' ' + REPO + '/harness_cli.py finalize-gate --gate 3 --phase 4 --project ' + REPO + '`.\n\n'
    + '4. D4: `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 80.0`. FAIL → add missing tests, re-run.\n'
    + '5. CRG-ARCH: `BASELINE=""; [ -f ' + REPO + '/.methodology/crg_baseline_p4.json ] && BASELINE="--baseline ' + REPO + '/.methodology/crg_baseline_p4.json"; ' + PY + ' ' + REPO + '/harness_cli.py crg-arch-check --project ' + REPO + ' $BASELINE`. CI enforces this as an absolute floor on every push, independent of the Gate 3 composite score. FAIL → the crg-arch-check output lists the low-cohesion communities / oversized functions; fix the underlying architecture issue, re-run.\n'
    + 'finalize-gate (G3c) writes HANDOVER.md + pushes on PASS. Report final line: "GATE3: PASS" (composite ≥80 AND all dims ≥ threshold AND D4 ≥80% AND CRG architecture ≥80) or "GATE3: FAIL — <failing dims>".\n\n'
    + 'SCOPE RULES:\n- DO NOT run advance-phase.\n- DO NOT edit gate3_result.json to fake scores — fix the code.\n- DO NOT modify harness/ (HR-17).\n- ONLY run-gate/eval/finalize/spec-coverage/crg-arch-check + code fixes.',
    { label: 'gate3-r' + round, phase: 'Gate 3', agentType: 'general-purpose' },
  )
  if (gate3Report === null || gate3Report === undefined || gate3Report === '' || typeof gate3Report !== 'string') {
    gate3Blocked = true
    log('  Gate 3 agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
    break
  }
  const g3v = await dispatch(
    'Run this ONE command via the Bash tool:\n'
    + '`pip install -q code-review-graph==2.3.6 igraph==1.0.0 >/dev/null 2>&1; ' + PY + ' ' + REPO + '/harness_cli.py verify-gate --project ' + REPO + ' --gate 3 --phase 4 --spec-threshold 80.0; echo "RC=$?"`\n'
    + 'It runs all three of Gate 3\'s checks — state.json last_gate >= 3, spec-coverage, and the CRG architecture floor — and appends the verdict, with a digest of the tree it measured, to .methodology/gate_verify.jsonl. advance-phase re-derives that digest and refuses a phase whose exit gate has no matching PASS, so a verdict you did not actually produce cannot carry the phase.\n'
    + 'Then report via the StructuredOutput tool: verify_rc = the exact numeric exit code echoed on the final RC= line; detail = the command\'s last [verify-gate] line.',
    { label: 'gate3-verify-r' + round, phase: 'Gate 3', agentType: 'general-purpose', schema: GATE_VERIFY_SCHEMA },
  )
  gate3Pass = !!(g3v && g3v.verify_rc === 0)
  if (gate3Pass) { log('  Gate 3 PASS [harness-verified: verify-gate rc=0, verdict recorded in gate_verify.jsonl]'); break }
  log('  Gate 3 not yet PASS [' + (g3v ? String(g3v.detail ?? '') : 'verify agent null') + '] — retry round ' + (round + 1))
}
if (gate3Blocked) {
  return { session_limit_blocked: true, gate: 3, message: 'Agent hit session/rate limit during Gate 3 evaluation. Resume after quota reset — GUARD checks will skip completed FRs.' }
}
if (!gate3Pass) {
  log('  Gate 3 exhausted 3 rounds — generating deferred_fixes.md')
  const gate3StateCmd = PY + ' -c "import json; g=(json.load(open(\'' + REPO + '/.methodology/quality_manifest.json\')).get(\'gate_results\',{}) or {}).get(\'gate3\') or {}; print(json.dumps({\'score\': g.get(\'score\'), \'qc\': g.get(\'quality_complete\'), \'dims\': g.get(\'dimensions\',{})}))"'
  await dispatch(
    'YOU ARE THE DEFERRED-FIX RECORDER. Gate 3 failed to reach PASS in 3 rounds.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + '1. Get the last-known Gate 3 state:\n`' + gate3StateCmd + '`\n'
    + '2. Run `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 80.0; echo "RC=$?"` for the D4 status.\n'
    + '3. Run `' + PY + ' ' + REPO + '/harness_cli.py crg-arch-check --project ' + REPO + '; echo "RC=$?"` for the CRG architecture status.\n'
    + '4. Write `' + REPO + '/.methodology/deferred_fixes.md` with:\n'
    + '   - A brief header: "Gate 3 — deferred fixes" + date + last-known composite score\n'
    + '   - Each failing dimension (score below its threshold) as a `- [ ]` checkbox item\n'
    + '   - D4 as a `- [ ]` checkbox item (spec-coverage < 80%)\n'
    + '   - CRG architecture as a `- [ ]` checkbox item if RC != 0 (architecture score < 80%)\n'
    + '   - Each item MUST cite the current score AND the required threshold\n'
    + '   - A final "Next step:" line: "Resolve every item → re-run Phase 4 Gate 3 → advance-phase"',
    { label: 'deferred-fixes', phase: 'Gate 3', agentType: 'general-purpose' },
  )
  return halt('gate3', { error: 'Gate 3 did not PASS in 3 rounds (HR-08); deferred_fixes.md written to .methodology/ (advance-phase exit 17 until resolved)', raw: String(gate3Report ?? '').slice(-600) })
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Advance
// ══════════════════════════════════════════════════════════════════════════

phase('Advance')
log('p4-pre-gate3 milestone + advance-phase --completed 4 (TDD-PRECHECK enforced)')
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
    'YOU ARE THE PHASE-4 EXIT ORCHESTRATOR. Advance to Phase 5. ROUND ' + round + '.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Steps:\n'
    + '0. GUARD — already advanced? `PHASE=$(jq -r .current_phase ' + REPO + '/.methodology/state.json 2>/dev/null); echo "current_phase=$PHASE"; [ "$PHASE" -ge 5 ]`. If Phase 5 is confirmed, report "ADVANCE: PASS (already advanced)" and stop.\n'
    + '1. PUSH ⑥ p4-pre-gate3 (if not already pushed): `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p4-pre-gate3 --project ' + REPO + ' --fr-ids ' + gate1Pass.join(',') + '`. (Idempotent; skip if already snapshotted.)\n'
    + '2. advance-phase: `' + PY + ' ' + REPO + '/harness_cli.py advance-phase --completed 4 --project ' + REPO + '`\n'
    + '   advance-phase independently re-verifies EVERYTHING before it will advance — its own output tells you exactly what is missing. If it prints "[BLOCKED] ...", that message IS the fix instruction: read it verbatim and do exactly what it says, then re-run this same advance-phase command. Do NOT guess what might be wrong — trust only what advance-phase itself reports. It is safe to re-run repeatedly within this round.\n'
    + '3. Read ' + REPO + '/.methodology/state.json; confirm current_phase = 5 (advance-phase atomically writes state.json when complete).\n\n'
    + 'Report final line: "ADVANCE: PASS|FAIL — <details>". If still FAIL after exhausting this round\'s turn, report the LAST [BLOCKED] message verbatim so the next round starts from where this one left off. PHASE_5_PLAN: ' + REPO + '/.methodology/phase5_plan.md\n\n'
    + 'SCOPE RULES:\n- DO NOT re-do P4 testing.\n- DO NOT use --no-verify.\n- DO NOT modify harness/ (HR-17).\n- ONLY push-milestone p4-pre-gate3 + advance-phase + verify HANDOVER.md + the specific fixes advance-phase\'s own output asked for.\n- Any diagnostic/debug script MUST be written under .sessi-work/tmp/ (never repo root or source dirs) and self-cleaned before you exit.',
    { label: 'advance-r' + round, phase: 'Advance', agentType: 'general-purpose' },
  )
  if (advanceReport === null || advanceReport === undefined || advanceReport === '' || typeof advanceReport !== 'string') {
    log('  advance agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
    return { session_limit_blocked: true, phase: 4, step: 'advance', message: 'Agent hit session/rate limit during Advance. Resume after quota reset — the GUARD step skips if already advanced.' }
  }
  // AUTHORITATIVE Advance verdict: advance-phase atomically writes
  // state.json current_phase=5 on success. Read it via a schema proxy —
  // the orchestrator's prose "ADVANCE: PASS" is narrative only.
  const advVerifyCmd = PY + ' -c "import json; print(json.dumps({\'current_phase\': int(json.load(open(\'' + REPO + '/.methodology/state.json\')).get(\'current_phase\') or 0)}))"'
  const advV = await dispatch(
    'Run EXACTLY this command via the Bash tool (stdout is a single JSON line):\n`' + advVerifyCmd + '`\n'
    + 'Then report via the StructuredOutput tool: current_phase = the exact integer from that JSON.',
    { label: 'advance-verify-r' + round, phase: 'Advance', agentType: 'general-purpose', schema: PHASE_SCHEMA },
  )
  advancePass = !!(advV && advV.current_phase >= 5)
  if (advancePass) {
    log('  Advance PASS [harness-verified: state.json current_phase=' + advV.current_phase + ']')
    // [Phase close cleanup] advance-phase only commits its own target paths
    // (state.json, HANDOVER.md, CLAUDE.md, phase plan). Post-advance edits
    // (pragma annotations, style fixes, test additions, deleted scaffolding)
    // remain uncommitted, leaving a dirty tree for the next phase. Commit
    // everything advance-phase didn't include. This agent is SCOPED to git
    // housekeeping only — no code, no phase transitions.
    await dispatch(
      'Run ONE bash command and report its stdout/stderr:\n'
      + '`git -C ' + REPO + ' add -A && git -C ' + REPO + ' commit -m "chore: phase 4 clean-up" || true`\n\n'
      + 'Report: the verbatim stdout/stderr of that command.\n\n'
      + 'SCOPE RULES:\n- DO NOT run any code, tests, or phase transitions.\n- ONLY the git commit above.',
      { label: 'cleanup-r' + round, phase: 'Advance', agentType: 'general-purpose' },
    )
    break
  }
  log('  Advance not yet PASS [state.json current_phase=' + (advV ? advV.current_phase : '?') + '] — retry round ' + (round + 1))
}

if (!advancePass) {
  return halt('advance', { error: 'Advance did not PASS in ' + ADVANCE_MAX_ROUNDS + ' rounds — check HANDOVER.md + state.json + the last [BLOCKED] message below. If Phase 5 is confirmed, resume workflow to verify.', raw: String(advanceReport ?? '').slice(-600) })
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
  if (/\[HARNESS-BUG\]/.test(syncText)) {
    log('  Sync reports [HARNESS-BUG] — harness-methodology crashed; not a project blocker and not something a retry can clear')
    return { harness_bug_detected: true, step: 'sync', message: 'git push was rejected by a harness-methodology crash ([HARNESS-BUG] — see the crash bundle path in the log), not by a project quality failure. A human must fix the harness bug.', raw: syncText.slice(-600) }
  }
  log('  Sync attempt ' + sAttempt + '/' + SYNC_MAX_ATTEMPTS + ' did not PASS — read the pre-push blocker list, fix what it names, retry')
}
if (!syncPass) {
  return halt('post-advance-push', { error: 'post-advance push did not PASS', raw: String(syncReport ?? '').slice(-500) })
}


log('Phase 4 workflow complete. Open .methodology/phase5_plan.md to continue.')
return {
  phase_complete: true,
  phase: 4,
  fr_count: frIds.length,
  gate1_pass: gate1Pass,
  gate3_status: gate3Pass ? 'PASS' : 'unknown',
  advance_status: 'PASS',
  artifacts: ['04-testing/TEST_PLAN.md', '04-testing/TEST_RESULTS.md', '04-testing/COVERAGE_REPORT.md', '.methodology/bug_hunt_report.json', '.methodology/gate3_result.json', 'HANDOVER.md'],
  notes: 'Phase 4 complete per phase4_plan.md v2.12.0. All FRs Gate 1 PASS + bug hunt done + Gate 3 PASS. Phase 5 (Verification) ready.',
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
