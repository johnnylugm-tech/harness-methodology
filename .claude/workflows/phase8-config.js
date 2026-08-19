// Phase 8 — Configuration Management (faithful to .methodology/phase8_plan.md v2.12.0)
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/phase_specs.py::generate_phase8() (+ js_blocks.py for
// the blocks shared across phase workflow files). Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write --phase 8
//
// Structure: FR-loop型 → push-milestone p8 → advance-phase --completed 8 → Sync → Phase 9 (Maintenance).
// Per-FR GATE1-DELTA re-eval, then REVIEW+APPEND the config baseline (the
// framework deterministically generated CONFIG_RECORDS.md + RELEASE_CHECKLIST.md
// via `scripts/phase8_doc_gen.py` during P7→P8 advance-phase (cli/phase_cmds.py
// advance-phase hook, harness commits 4738542 + 3f1fd73), create .methodology-archive/
// (cp -r .methodology/ — NOT .sessi-work/, per harness commit 3f1fd73), verify
// no Phase 9 refs, p8 push.
//
// Playbook lessons: NO import/fs/process, Bash CLI, SCOPE RULES,
// PY = .venv/bin/python, scriptPath launch.
// v4 (2026-07-02): gate verdicts use FLAT schema (playbook §5.2 rev) — regex
// over LLM prose was the root cause of the #126/#134/#135/#136/ENV_CHECK_RC
// bug class. Heavy orchestrators keep prose narrative; verdicts come from
// schema proxy agents reading harness artifacts (manifest qc, git log, rc).


export const meta = {
  name: 'phase8-config',
  description: 'Phase 8 Config — per-FR GATE1-DELTA + CONFIG_RECORDS/RELEASE_CHECKLIST + archive + p8 push (phase8_plan.md v2.12.0)',
  phases: [
    { title: 'Entry & Preflight' },
    { title: 'Env Check' },
    { title: 'Load FRs' },
    { title: 'Per-FR Delta' },
    { title: 'Config Docs' },
    { title: 'Artifacts Commit' },
    { title: 'Archive' },
    { title: 'Final Push' },
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

async function dispatch(prompt, opts) {
  return await agent(prompt, opts)
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


// ══════════════════════════════════════════════════════════════════════════
// Phase: Entry & Preflight
// ══════════════════════════════════════════════════════════════════════════

phase('Entry & Preflight')
log('ENTRY-CHECK Gate4 + run-phase 8 (reliability/config/attestation fixes) + handoff + CI')
const preflightReport = await dispatch(
  'YOU ARE THE PHASE-8 PREFLIGHT ORCHESTRATOR. Run bash in order; report.\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
  + 'Steps:\n'
  + '1. ENTRY-CHECK: run EXACTLY this bash command to verify Gate 4 status (do NOT rely on reading the file yourself — use the command output):\n`' + PY + ' -c "import json; m=json.load(open(\'' + REPO + '/.methodology/quality_manifest.json\')); g4=(m.get(\'gate_results\',{}) or {}).get(\'gate4\',{}) or {}; print(\'GATE_VERIFIED\' if isinstance(g4,dict) and g4.get(\'quality_complete\') is True else \'GATE_MISSING\')"`\nIf GATE_MISSING → FAIL (return to Phase 6).\n'
  + '2. PREFLIGHT: `' + PY + ' ' + REPO + '/harness_cli.py run-phase --phase 8 --project ' + REPO + '`. FAIL → fix, re-run (max 3). Also fix if reported: reliability lint (subprocess timeout / mkstemp / TOCTOU / sleep-in-async), config liveness (env keys absent from .env.example), attestation missing/mismatch (build-trace-attestation --write + commit; re-run until "Attestation: clean"), property_spec (an FR declares a Properties invariant in TEST_SPEC.md but no test executes it — write a hypothesis @given (Python) / fast-check (JS/TS) test exercising the declared invariant for that FR, then re-run).\n'
  + '3. HANDOFF: `' + PY + ' ' + REPO + '/harness_cli.py validate-handoff --from-phase 7 --project ' + REPO + '`. Must exit 0.\n'
  + '4. PREFLIGHT-CI: confirm `' + REPO + '/.github/workflows/harness_quality_gate.yml` (CI workflow) + `' + REPO + '/.git/hooks/prepare-commit-msg` (git hook) both exist; confirm state.json current_phase=8. If stale: `init-project --phase 8 --project ' + REPO + ' --overwrite`.\n\n'
  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if ALL 4 steps succeeded; reason = one-line summary (on FAIL: which step + verbatim error tail).\n\n'
  + 'SCOPE RULES:\n- DO NOT generate config docs / run TDD steps / create archive.\n- DO NOT run push-milestone.\n- DO NOT modify harness/.\n- ONLY preflight commands + fixes.',
  { label: 'preflight', phase: 'Entry & Preflight', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
)
if (!(preflightReport && preflightReport.pass === true)) {
  return halt('preflight', { error: 'Phase 8 preflight did not PASS', reason: preflightReport ? String(preflightReport.reason ?? '').slice(-600) : 'agent returned null (skipped or terminal API error)' })
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
const envCheckLog = '/tmp/envcheck_phase8.log'
const envCheckChain = PY + ' ' + REPO + '/harness_cli.py run-env-check --phase 8 --project ' + REPO + ' && ' + PY + ' ' + REPO + '/harness_cli.py finalize-env-check --phase 8 --project ' + REPO + '; echo "RC=$?"'
const envReport = await dispatch(
  'YOU ARE THE PHASE-8 ENV-CHECK ORCHESTRATOR (Bash-timeout-aware, background poll).\n'
  + 'REPO: ' + REPO + '\n'
  + 'PYTHON: ' + PY + '\n'
  + 'LOG PATH: /tmp/envcheck_phase8.log\n\n'
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
  return halt('env-check', { error: 'Phase 8 env-check did not PASS', rc: envReport ? envReport.rc : null, ready: envReport ? envReport.ready : null, note: envReport ? ('run-env-check/finalize-env-check rc=' + envReport.rc + ' ready=' + envReport.ready + ' — read ' + _envCheckResult) : 'agent returned null (skipped or terminal API error)' })
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
const integrityCmd = PY + ' ' + REPO + '/harness_cli.py check-manifest-integrity --project ' + REPO + ' --phase 8'
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
log('load-context --phase 8 → fr_ids')
// v15: retry loop — agent() wrapped (Bug #2); v4: schema transport, no prose parsing
// v2.13.1: hardened against agent hallucination (Bug #122).
let ctx = null
const ctxFile = REPO + '/.sessi-work/phase8_ctx.json'
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

  const ctxRegenCmd = `${PY} ${REPO}/harness_cli.py load-context --phase 8 --project ${REPO} --json > ${ctxFile} && ${PY} -c "import json,os; json.load(open('${ctxFile}')); print('REGEN_OK_'+str(os.path.getsize('${ctxFile}')))"`
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
log('  fr_ids = ' + JSON.stringify(frIds))


// ══════════════════════════════════════════════════════════════════════════
// Phase: Per-FR Delta
// ══════════════════════════════════════════════════════════════════════════

phase('Per-FR Delta')
const gate1Pass = []
const gate1Fail = []
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
  + 'Direction C (past lessons): BEFORE classifying, Bash `cat ' + REPO + '/.sessi-work/phase8_ctx.json` and READ the `lessons` field (compact markdown, "" if none). DO NOT repeat those past failure modes in your pass/fail classification or any follow-up P8 work.\n\n'
  + 'For EACH FR in order, substituting <FR> with the FR id:\n'
  + '1. GATE1-DELTA is long-running for any FR whose code actually changed (harness runs up to 3 internal CODE-FIX rounds, each up to ~600s — can silently block ~2400s worst case even though this step is a "probe"). Run it BACKGROUNDED for every FR, not just slow ones — unchanged FRs still hit the fast in-CLI short-circuit almost instantly so this costs nothing extra:\n'
  + '   a. `nohup ' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 8 --fr-id <FR> --step GATE1-DELTA --project ' + REPO + ' > /tmp/gate1delta_<FR>.log 2>&1 & echo $!` — note the PID.\n'
  + '   b. Poll with BACKOFF intervals, in seconds: 5, 10, then 30 for every further iteration — `sleep <interval> && kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Cap 42 polls (5+10 + 40x30 ≈ 20min). Still RUNNING past the cap → classify <FR> as fail_fr_ids (the full loop below will retry it) and move to the next FR — do not kill the PID.\n'
  + '      (Round 22 站4: the first interval used to be a flat 30s. An unchanged FR hits the in-CLI short-circuit almost instantly, and this probe walks the FRs one at a time, so a fixed first sleep cost 30s x N — ten minutes on a 20-FR project spent waiting on commands that had already returned.)\n'
  + '   c. DONE → proceed to step 2 (the log itself is not needed — the authoritative verdict is the manifest read below).\n'
  + '2. Authoritative verdict (manifest qc AND a phase-8 gate-1 timestamp for <FR>): `' + PY + ' -c "import json; g=(json.load(open(\'' + REPO + '/.methodology/quality_manifest.json\')).get(\'gate_results\',{}) or {}).get(\'gate1\',{}).get(\'<FR>\',{}) or {}; ts=any(e.get(\'phase\')==8 and e.get(\'gate\')==1 and e.get(\'fr_id\')==\'<FR>\' for e in (json.loads(l) for l in open(\'' + REPO + '/.methodology/gate_timestamps.jsonl\') if l.strip())); print(bool(g.get(\'quality_complete\')) and ts)"`\n'
  + '   stdout `True` → pass_fr_ids; anything else (False/None/timeout/error/missing file) → fail_fr_ids.\n\n'
  + 'HARD RULES:\n- DO NOT fix code, edit files, or run TDD steps.\n- DO NOT retry a failing FR — classify it and move on (the full loop handles it).\n- DO NOT run push-milestone / generate config docs / create archive.\n- DO NOT modify harness/.\n\n'
  + 'Report via the StructuredOutput tool: pass_fr_ids + fail_fr_ids (every FR in exactly one list).',
  { label: 'delta-fastpath', phase: 'Per-FR Delta', agentType: 'general-purpose', schema: DELTA_FAST_SCHEMA },
)
if (fastProbe && Array.isArray(fastProbe.pass_fr_ids)) {
  const fastPassed = fastProbe.pass_fr_ids.filter((f) => frIds.includes(f))
  for (const fr of fastPassed) {
    gate1Pass.push(fr)
    log('  ' + fr + ' GATE1-DELTA fast-path PASS [manifest qc + p8 timestamp] — full DELTA skipped')
  }
  deltaTodo = frIds.filter((f) => !fastPassed.includes(f))
} else {
  log('  delta-fastpath unavailable — falling back to full per-FR loop')
}
for (const frId of deltaTodo) {
  log('  === ' + frId + ' — GATE1-DELTA ===')
  const frReport = await dispatch(
    'YOU ARE THE CONFIG-AWARE VERIFIER for ' + frId + '. Re-evaluate Gate 1 for THIS ONE FR.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Steps:\n'
    + '1. GATE1-DELTA — long-running when code changed (harness runs up to 3 internal CODE-FIX rounds plus, on FAIL, a full TDD-RED→GREEN→IMPROVE→GATE1 chain — can silently block well past 180s). Run it BACKGROUNDED, do NOT invoke it as a plain synchronous command:\n'
    + '   a. `nohup ' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 8 --fr-id ' + frId + ' --step GATE1-DELTA --project ' + REPO + ' > /tmp/gate1delta_' + frId + '.log 2>&1 & echo $!` — note the PID.\n'
    + '   b. Poll every 30s: `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Cap 60 polls (~30min — this path can chain a full TDD cycle on top of GATE1-DELTA\'s own retries). Still RUNNING past the cap → report "' + frId + ' GATE1: TIMEOUT" (not FAIL) and stop — do not kill the PID.\n'
    + '   c. DONE → `cat /tmp/gate1delta_' + frId + '.log` for the full output, identical to a synchronous run. Parse PASS/FAIL from it.\n'
    + '   - PASS → done.\n'
    + '   - FAIL → full TDD auto-triggered: TDD-RED → TDD-GREEN → TDD-IMPROVE → GATE1 (each for ' + frId + '). Max 3 rounds. Still failing → report FAIL.\n'
    + '   If ' + frId + '’s code is unchanged since last Gate 1 PASS, this passes immediately.\n\n'
    + 'Report final line: "' + frId + ' GATE1: PASS" or "' + frId + ' GATE1: FAIL — <reason>".\n\n'
    + 'SCOPE RULES:\n- DO NOT touch any FR OTHER than ' + frId + '.\n- DO NOT run push-milestone / generate config docs / create archive.\n- DO NOT edit .methodology/quality_manifest.json or .sessi-work/gate1_result.json to fake/reset scores — fix the underlying code/tests instead.\n- DO NOT modify harness/.\n- ONLY GATE1-DELTA (+ full TDD if needed) for ' + frId + '.',
    { label: 'delta-' + frId, phase: 'Per-FR Delta', agentType: 'general-purpose' },
  )
  // L1 (ported from phase3): distinguish a session/rate-limit block (null/empty
  // agent return) from a real Gate 1 FAIL — a rate-limit mid-DELTA must not be
  // misreported as a code-quality failure. DELTA auto-skip makes resume safe.
  if (frReport === null || frReport === undefined || frReport === '' || typeof frReport !== 'string') {
    log('  ' + frId + ' agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
    return { session_limit_blocked: true, phase: 8, fr_id: frId, gate1Pass, message: 'Agent hit session/rate limit during ' + frId + ' GATE1-DELTA. Resume after quota reset — completed FRs skip via DELTA auto-satisfy.' }
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
    return { dispatch_structurally_broken: true, phase: 8, fr_id: frId, gate1Pass, gate1Fail: [...gate1Fail, frId], message: frId + ' GATE1-DELTA: dispatch is structurally broken (env: ANTHROPIC_API_KEY overrides claude.ai login). Human must unset ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN/ANTHROPIC_BASE_URL/ANTHROPIC_DEFAULT_HAIKU_MODEL in the shell that launches this process, then re-run via Workflow({scriptPath, resumeFromRunId}).' }
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
    return { harness_bug_detected: true, phase: 8, fr_id: frId, gate1Pass, gate1Fail: [...gate1Fail, frId], message: frId + ' GATE1-DELTA: harness-methodology itself crashed ([HARNESS-BUG] — see the crash bundle path in the log). This is not a project quality issue; a human must diagnose and fix the harness bug before this FR can proceed.' }
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
}
if (gate1Fail.length) {
  return halt('gate1', { error: 'Phase 8: Gate 1 FAILED for FR(s): ' + gate1Fail.join(', ') + ' (escalate)', gate1Pass, gate1Fail })
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
// Phase: Config Docs
// ══════════════════════════════════════════════════════════════════════════

phase('Config Docs')
// Per phase8_plan.md + harness commit 4738542: CONFIG_RECORDS.md and
// RELEASE_CHECKLIST.md are DETERMINISTICALLY generated by scripts/phase8_doc_gen.py
// during P7→P8 advance-phase (cli/phase_cmds.py:554, P7→P8 advance-phase hook).
// P8 work is therefore REVIEW + APPEND human-only context, NOT regenerate from
// scratch. Do NOT overwrite the deterministic baseline (it breaks byte-equality
// for downstream consumers); use Edit/append to add the human-only sections.
log('Review deterministic baseline (phase8_doc_gen.py output) + append human-only context')
const docsReport = await dispatch(
  'YOU ARE THE P8 CONFIG REVIEWER. The framework has ALREADY deterministically generated\n'
  + 'the config baseline during P7→P8 advance-phase. Your job: REVIEW + APPEND.\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
  + 'Steps (Bash for read-only checks; Edit for human-only append):\n'
  + '0. VERIFY BASELINE EXISTS: `test -f ' + REPO + '/08-config/CONFIG_RECORDS.md && test -f ' + REPO + '/08-config/RELEASE_CHECKLIST.md && echo BASELINE_OK || echo BASELINE_MISSING`. If MISSING, regenerate via `' + PY + ' ' + REPO + '/harness/scripts/phase8_doc_gen.py --project ' + REPO + '` (fallback per harness advance-phase behavior; should not normally fire).\n'
  + '1. CONFIG_RECORDS APPEND: Edit ' + REPO + '/08-config/CONFIG_RECORDS.md and APPEND a `## Human Context (P8 append)` section with: ownership per config item, secret rotation cadence, access audit log reference. KEEP all existing framework-generated sections (env var inventory, source-of-truth module refs, feature flags) intact. Do NOT overwrite the framework version.\n'
  + '1b. CONFIG_RECORDS FILL: the baseline is copied from a template and still carries the placeholders of that template — `{config}` for both environments, `{VAR}` / `{description}` in the environment-variable table, `{method}` / `{name}` in the deployment log, `{change}` / `{reason}` in the change log, and `{condition}` / `{rollback commands}` for the entire Rollback SOP. Replace EACH of them in place with the real value for this release; where a section genuinely does not apply, say so in words and delete the placeholder. Round 55 站2: `cross_artifact.check_unfilled_placeholders` now reports every surviving one as CRITICAL, and it is a CRITICAL that fails Phase Truth. Measured before it existed: all seven projects built by this framework shipped the same nine placeholders, rollback SOP included.\n'
  + '2. RELEASE_CHECKLIST APPEND: Edit ' + REPO + '/08-config/RELEASE_CHECKLIST.md and APPEND a `## Human Context (P8 append)` section with: deployment runbook URL, rollback owner + on-call, post-release monitoring dashboard, customer comms template. KEEP the framework-generated Gate 4 PASS proof, quality_manifest composite_score, FR coverage, git tag/hash intact.\n'
  + '3. SANITY: `grep -c "^## " ' + REPO + '/08-config/CONFIG_RECORDS.md && grep -c "^## " ' + REPO + '/08-config/RELEASE_CHECKLIST.md` — confirm both files still have the framework sections (count >= baseline).\n\n'
  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if the baseline was verified AND human context appended; reason = one-line summary.\n\n'
  + 'SCOPE RULES:\n- DO NOT regenerate CONFIG_RECORDS.md / RELEASE_CHECKLIST.md from scratch.\n- DO NOT use Write tool to overwrite either file — Edit/append only.\n- DO NOT run push-milestone / create archive (next phases do that).\n- DO NOT modify harness/.\n- DO NOT re-implement FRs.\n- ONLY verify baseline + append human context.',
  { label: 'config-docs', phase: 'Config Docs', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
)
if (!(docsReport && docsReport.pass === true)) {
  return halt('config-docs', { error: 'Phase 8 config docs did not PASS', reason: docsReport ? String(docsReport.reason ?? '').slice(-500) : 'agent returned null' })
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Artifacts Commit
// ══════════════════════════════════════════════════════════════════════════

phase('Artifacts Commit')
log('Committing phase-8 artifacts (explicit paths) so a verify-handoff FAIL exit leaves a clean tree')
await dispatch(
  'Run ONE bash command and report its stdout/stderr:\n'
  + '`git -C ' + REPO + ' add 08-config/CONFIG_RECORDS.md 08-config/RELEASE_CHECKLIST.md .methodology && git -C ' + REPO + ' commit -m "chore(p8): config-records + release-checklist artifacts" || true`\n\n'
  + 'Report: the verbatim stdout/stderr of that command. "nothing to commit" is a valid outcome.\n\n'
  + 'SCOPE RULES:\n- DO NOT run any code, tests, gates, or phase transitions.\n- DO NOT stage any path other than the 3 listed above.\n- ONLY the git command above.',
  { label: 'artifacts-commit', phase: 'Artifacts Commit', agentType: 'general-purpose' },
)


// ══════════════════════════════════════════════════════════════════════════
// Phase: Archive
// ══════════════════════════════════════════════════════════════════════════

phase('Archive')
// P8-ARCHIVE + P8-HANDOVER-CHECK — required by CI p8-archive-check.
log('Create .methodology-archive/ + verify HANDOVER.md has no Phase 9 refs')
const archiveReport = await dispatch(
  'YOU ARE THE P8 ARCHIVE ORCHESTRATOR. Prepare the archive (REQUIRED before p8 push).\n'
  + 'REPO: ' + REPO + '\n\n'
  + 'Steps (Bash):\n'
  + '1. P8-ARCHIVE: `mkdir -p ' + REPO + '/.methodology-archive && cp -r ' + REPO + '/.methodology/ ' + REPO + '/.methodology-archive/`. (push-milestone _validate_p8_completion + CI p8-archive-check both verify this dir. Source MUST be `.methodology/` — NOT `.sessi-work/` per harness commit 3f1fd73 which fixed the wrong-source silent bug.)\n'
  + '2. P8-HANDOVER-CHECK: `grep -qi "phase 9\\|phase9\\|phase9_plan" ' + REPO + '/HANDOVER.md && echo "HAS_P9" || echo "NO_P9"`. Phase 8 is final — if HAS_P9, remove the Phase 9 references from HANDOVER.md (Edit).\n\n'
  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if the archive dir was created AND HANDOVER.md has no Phase 9 refs; reason = one-line summary.\n\n'
  + 'SCOPE RULES:\n- DO NOT run push-milestone yet.\n- DO NOT modify harness/.\n- ONLY create .methodology-archive/ + clean HANDOVER.md Phase 9 refs.',
  { label: 'archive', phase: 'Archive', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
)
if (!(archiveReport && archiveReport.pass === true)) {
  return halt('archive-prep', { error: 'Phase 8 archive prep did not PASS', reason: archiveReport ? String(archiveReport.reason ?? '').slice(-500) : 'agent returned null' })
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Final Push
// ══════════════════════════════════════════════════════════════════════════

phase('Final Push')
log('push-milestone p8 (final — pipeline complete)')
// Round loop (2026-07-02 audit finding, ported from phase3): this round's
// two steps (push-milestone p8, then advance-phase) each run their own
// independent completion checks that are more than any single prompt can
// safely enumerate, and a static checklist goes stale the moment harness
// adds or changes one. The GUARD at step 0 makes this safe to re-run: an
// already-pushed p8 commit short-circuits immediately.
let p8Ok = false, pushReport = ''
const ADVANCE_MAX_ROUNDS = 5
for (let round = 1; round <= ADVANCE_MAX_ROUNDS; round++) {
  log('  Final Push round ' + round + '/' + ADVANCE_MAX_ROUNDS)
  // Last-line integrity guard: the phase-exit push commits .methodology/
  // wholesale — block here so mid-run corruption never reaches git history
  // (2026-07-02: commit 3198402 baked a corrupted manifest into main).
  // Re-check every round — a fix attempt in a prior round could reintroduce it.
  const pushIntegrity = await checkManifestIntegrity('Final Push', 'finalpush-integrity-r' + round)
  if (!pushIntegrity.ok) {
    return halt('final-push', { error: 'Final Push round ' + round + ': quality_manifest.json corrupted — refusing to commit it', detail: pushIntegrity.raw, recovery: 'git checkout HEAD -- .methodology/quality_manifest.json (verify HEAD is healthy first), merge the latest gate result back into gate_results, then resume', note: 'Blocking prevents the corruption from being committed by the p8 final push.' })
  }
  pushReport = await dispatch(
    'YOU ARE THE P8 FINAL PUSHER. This is the LAST step of the 8-phase pipeline. ROUND ' + round + '.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Steps:\n'
    + '0. GUARD: `git -C ' + REPO + ' log --oneline --grep="P8" -1`. If exists, report "P8-PUSH: PASS (already pushed)" and stop.\n'
    + '1. PUSH ⑩: `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p8 --project ' + REPO + '`. _validate_p8_completion checks the `.methodology-archive/` presence + contents (its output tells you exactly what is missing — lint/types/coverage/Phase Truth are advance-phase\'s job, step 2 below, not this step\'s). If it prints "[BLOCKED] ..." or "[ERROR] P8 push blocked ...", that message IS the fix instruction: read it verbatim and do exactly what it says, then re-run this same push-milestone command. Do NOT guess what might be wrong — trust only what push-milestone itself reports. It is safe to re-run repeatedly within this round. On success it writes HANDOVER.md + commits + pushes. If a hook blocks, reword commit to start with `chore(harness):` (NOT --no-verify), retry.\n'
    + '2. ADVANCE: `' + PY + ' ' + REPO + '/harness_cli.py advance-phase --completed 8 --project ' + REPO + '`. This transitions into Phase 9 (Maintenance — steady-state, CR-driven). advance-phase independently re-verifies EVERYTHING (TDD-PRECHECK, HR-11 Phase Truth, HR-17 submodule guard, etc.) — its own output tells you exactly what is missing. If it prints "[BLOCKED] ...", that message IS the fix instruction. It is safe to re-run repeatedly within this round.\n'
    + '3. Read ' + REPO + '/.methodology/state.json; confirm current_phase >= 8.\n\n'
    + 'Report final line: "P8-PUSH: PASS|FAIL — <details>". If still FAIL after exhausting this round\'s turn, report the LAST [BLOCKED] message verbatim so the next round starts from where this one left off. PHASE_9_PLAN: ' + REPO + '/.methodology/phase9_plan.md\n\n'
    + 'SCOPE RULES:\n- DO NOT use --no-verify.\n- DO NOT modify harness/ (HR-17).\n- ONLY push-milestone p8 + advance-phase --completed 8 + the specific fixes their own output asked for.\n- Any diagnostic/debug script MUST be written under .sessi-work/tmp/ (never repo root or source dirs) and self-cleaned before you exit.',
    { label: 'final-push-r' + round, phase: 'Final Push', agentType: 'general-purpose' },
  )
if (pushReport === null || pushReport === undefined || pushReport === '' || typeof pushReport !== 'string') {
  log('  final-push agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
  return { session_limit_blocked: true, phase: 8, step: 'final-push', message: 'Agent hit session/rate limit during Final Push. Resume after quota reset — the GUARD step skips if already pushed.' }
}
  // AUTHORITATIVE Final Push verdict: push-milestone p8 creates a milestone
  // commit — the same artifact the step-0 GUARD checks. Read git log via a
  // schema proxy; the pusher's prose "P8-PUSH: PASS" is narrative only.
  // Round 28: query origin/main, not local HEAD — _commit_and_push commits
  // locally before attempting the push and does not revert the commit if the
  // push itself fails, so a local-only grep matched even when nothing reached
  // origin (retry loop then broke early on a push that never landed).
  const p8VerifyCmd = 'git -C ' + REPO + ' fetch origin main --quiet && git -C ' + REPO + ' log origin/main --oneline --grep="P8" -1'
  const p8v = await dispatch(
    'Run EXACTLY this command via the Bash tool:\n`' + p8VerifyCmd + '`\n'
    + 'Then report via the StructuredOutput tool: pass = true ONLY if stdout contains a commit line (non-empty) — this confirms the P8 commit reached origin, not merely local HEAD; reason = the verbatim stdout (or "empty").',
    { label: 'p8-verify-r' + round, phase: 'Final Push', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
  )
  p8Ok = !!(p8v && p8v.pass === true)
  if (p8Ok) { log('  Final Push PASS [git-verified: ' + String(p8v.reason ?? '').slice(0, 80) + ']'); break }
  log('  Final Push not yet PASS [' + (p8v ? String(p8v.reason ?? '').slice(0, 80) : 'verify agent null') + '] — retry round ' + (round + 1))
}
if (!p8Ok) return halt('p8-push', { error: 'Phase 8 p8 push did not PASS in ' + ADVANCE_MAX_ROUNDS + ' rounds — check the last [BLOCKED] message below', raw: String(pushReport ?? '').slice(-600) })

log('Phase 8 push-milestone + advance-phase complete. 🎉 Pipeline complete — Phase 9 (Maintenance) begins.')

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
  + '3. `git -C ' + REPO + ' tag -l \"harness-v*\" | head -3` — confirm any Phase 6 gate4 tag is pushed; if there is a P6 tag but `git push origin --tags` hasn\'t run yet, push tags.\n'
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


return {
  phase_complete: true,
  phase: 8,
  fr_count: frIds.length,
  gate1_pass: gate1Pass,
  p8_push_status: p8Ok ? 'PASS' : 'unknown',
  artifacts: ['08-config/CONFIG_RECORDS.md', '08-config/RELEASE_CHECKLIST.md', '.methodology-archive/', 'HANDOVER.md'],
  notes: 'Phase 8 complete per phase8_plan.md v2.12.0. Full P1→P8 pipeline complete → Phase 9 (Maintenance, CR-driven steady state).',
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
