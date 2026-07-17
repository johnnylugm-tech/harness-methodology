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
    { title: 'Manifest Integrity' },
    { title: 'Load FRs' },
    { title: 'Per-FR TDD' },
    { title: 'Milestones' },
    { title: 'Gate 2' },
    { title: 'Advance' },
    { title: 'Sync' },
  ],
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
  const r = await agent(
    'You are the REPO RESOLVER. Find the project root by walking up from your current CWD until a directory contains BOTH `harness_cli.py` AND `.methodology/` AND is NOT a git submodule working tree.\n'
    + 'A git submodule working tree is detected by `[ -f .git ] && head -1 .git 2>/dev/null | grep -q "^gitdir: "` (the top-level `.git` is a FILE whose first line starts with `gitdir:`, pointing to `<parent>/.git/modules/<name>`). This is critical when the harness framework is checked out as a git submodule — the harness/ dir itself contains harness_cli.py AND .methodology/, so naive walk-up would stop there instead of the real project root.\n'
    + 'Run EXACTLY this command via Bash (single line, copy-paste verbatim):\n'
    + 'cd "$(pwd)"; while [ "$(pwd)" != "/" ] && ! { [ -f harness_cli.py ] && [ -d .methodology ] && ! { [ -f .git ] && head -1 .git 2>/dev/null | grep -q "^gitdir: "; }; }; do cd ..; done; '
    + 'if [ -f harness_cli.py ] && [ -d .methodology ] && ! { [ -f .git ] && head -1 .git 2>/dev/null | grep -q "^gitdir: "; }; then echo "REPO=$(pwd)"; else echo "REPO_NOT_FOUND cwd=$(pwd)"; fi\n'
    + 'Report the literal stdout as your final message (no commentary, no transformation).',
    { label: 'resolve-repo', agentType: 'general-purpose' }
  )
  const text = String(r ?? '').trim()
  const match = text.match(/REPO=(\S+)/)
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
    manifest_qc: { type: 'boolean', description: 'gate_results.<gate>.quality_complete is exactly true' },
    d4_rc: { type: 'integer', description: 'exit code of spec-coverage-check' },
    detail: { type: 'string' },
  },
  required: ['manifest_qc', 'd4_rc'],
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
const preflightReport = await agent(
  'YOU ARE THE PHASE-3 PREFLIGHT ORCHESTRATOR. Run bash in order; report.\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
  + 'Steps:\n'
  + '1. ENTRY-CHECK: `git -C ' + REPO + ' log --oneline --grep="phase2(review-complete)" -1` OR confirm P2 artifacts exist.\n'
  + '2. P2-ARTIFACTS: `ls ' + REPO + '/02-architecture/SAD.md ' + REPO + '/02-architecture/adr/ADR.md ' + REPO + '/02-architecture/TEST_SPEC.md ' + REPO + '/.methodology/quality_manifest.json ' + REPO + '/.methodology/SAB.json`. ALL must exist (else FAIL → return to Phase 2).\n'
  + '3. PREFLIGHT: `' + PY + ' ' + REPO + '/harness_cli.py run-phase --phase 3 --project ' + REPO + '`. FAIL → fix FSM/Constitution/Drift, re-run (max 3).\n'
  + '4. HANDOFF: `' + PY + ' ' + REPO + '/harness_cli.py validate-handoff --from-phase 2 --project ' + REPO + '`. Must exit 0.\n'
  + '5. PREFLIGHT-CI: confirm `' + REPO + '/.github/workflows/harness_quality_gate.yml` (CI workflow) + `' + REPO + '/.git/hooks/prepare-commit-msg` (git hook) both exist; confirm state.json current_phase=3. If stale: `init-project --phase 3 --project ' + REPO + ' --overwrite`.\n\n'
  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if ALL 5 steps succeeded; reason = one-line summary (on FAIL: which step + verbatim error tail).\n\n'
  + 'SCOPE RULES:\n- DO NOT implement any FR or run TDD steps.\n- DO NOT run advance-phase/push-milestone/run-gate.\n- DO NOT modify harness/.\n- ONLY preflight commands + fixes.',
  { label: 'preflight', phase: 'Entry & Preflight', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
)
if (!(preflightReport && preflightReport.pass === true)) {
  return { error: 'Phase 3 preflight did not PASS', reason: preflightReport ? String(preflightReport.reason ?? '').slice(-600) : 'agent returned null (skipped or terminal API error)' }
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Env Check
// ══════════════════════════════════════════════════════════════════════════

phase('Env Check')
log('run-env-check + finalize-env-check (root-cause fix: CLI exit code reflects ready flag)')
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
const envReport = await agent(
  'You MUST use the Bash tool. Run exactly this ONE command (single line):\n'
  + PY + ' ' + REPO + '/harness_cli.py run-env-check --phase 3 --project ' + REPO + ' && ' + PY + ' ' + REPO + '/harness_cli.py finalize-env-check --phase 3 --project ' + REPO + '; echo "RC=$?"\n'
  + 'Then report via the StructuredOutput tool: rc = the exact numeric exit code echoed on the final RC= line.',
  { label: 'env-check', phase: 'Env Check', agentType: 'general-purpose', schema: RC_SCHEMA },
)
if (!(envReport && envReport.rc === 0)) {
  return { error: 'Phase 3 env-check did not PASS', rc: envReport ? envReport.rc : null, note: envReport ? 'run-env-check/finalize-env-check exit ' + envReport.rc + ' — read .sessi-work/env_check_result.json' : 'agent returned null (skipped or terminal API error)' }
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Manifest Integrity
// ══════════════════════════════════════════════════════════════════════════

phase('Manifest Integrity')
// (ported from phase3, 155ec07 + 286ccca)
// 2026-07-02 incident class: a sub-agent action (bare pytest → harness test
// CWD leak) can corrupt quality_manifest.json MID-RUN, not just before entry.
// Detect the three known corruption patterns (fr_ids truncated, traceability
// cleared, gate1 wiped) at entry AND re-check before the phase-exit push so
// corruption is never baked into a milestone commit.
// T1-A (8-phase audit remediation): the previous inline Python one-liner
// had the truncation-comparison direction inverted (`fr_trace >= fr_ids`
// instead of the harness's actual `fr_ids >= fr_trace`) plus an unfounded
// `fr_ids >= 2` floor. `check-manifest-integrity` wraps the harness's own
// (correct, tested) PhaseHooks.preflight_manifest_integrity() instead.
const integrityCmd = PY + ' ' + REPO + '/harness_cli.py check-manifest-integrity --project ' + REPO + ' --phase 3'
async function checkManifestIntegrity(phaseLabel, agentLabel) {
  const verdict = await agent(
    'Run EXACTLY this command via the Bash tool:\n`' + integrityCmd + '; echo RC=$?`\n'
    + 'Then report via the StructuredOutput tool: pass = true ONLY if the output ends with `RC=0`; reason = the JSON the command printed (verbatim, excluding the RC= line).',
    { label: agentLabel, phase: phaseLabel, agentType: 'general-purpose', schema: VERDICT_SCHEMA },
  )
  const ok = !!(verdict && verdict.pass === true)
  const raw = verdict ? String(verdict.reason ?? '').trim() : 'agent returned null'
  if (!ok) log('  manifest integrity FAIL [' + agentLabel + ']: ' + raw)
  return { ok, raw }
}
const integrity0 = await checkManifestIntegrity('Manifest Integrity', 'manifest-integrity')
if (!integrity0.ok) {
  return { error: 'Manifest Integrity: quality_manifest.json appears corrupted', detail: integrity0.raw, recovery: 'git checkout HEAD -- .methodology/quality_manifest.json (verify HEAD is healthy first)', note: 'Working-tree manifest fails the P4+ shape check (fr_ids/traceability/gate1 per-FR records). A sub-agent likely wrote to it directly. Restore a healthy copy and re-run.' }
}
log('  manifest integrity OK')


// ══════════════════════════════════════════════════════════════════════════
// Phase: Load FRs
// ══════════════════════════════════════════════════════════════════════════

phase('Load FRs')
log('load-context --phase 3 → fr_ids (script holds the loop)')
// v15: retry loop — agent() wrapped (Bug #2); v4: schema transport, no prose parsing
// v2.13.1: hardened against agent hallucination — verify .sessi-work/phase3_ctx.json
// actually exists and contains non-empty fr_ids before accepting (Bug #122).
let ctx = null
const ctxFile = REPO + '/.sessi-work/phase3_ctx.json'
for (let attempt = 1; attempt <= 3; attempt++) {
  try {
    const ctxCheckCmd = `${PY} -c "import json,os,sys; json.load(open('${ctxFile}')); print('FILE_OK_'+str(os.path.getsize('${ctxFile}')))" || echo FILE_MISSING`
    const existsVerdict = await agent(
      `You MUST use the Bash tool. Run exactly:\n${ctxCheckCmd}\nThen report via the StructuredOutput tool: pass = true ONLY if stdout starts with FILE_OK_; reason = the verbatim stdout.`,
      { label: 'ctx-check-' + attempt, phase: 'Load FRs', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
    )
    if (!(existsVerdict && existsVerdict.pass === true)) {
      log('  ctx file missing/invalid (attempt ' + attempt + ') — regenerating')
      const ctxRegenCmd = `${PY} ${REPO}/harness_cli.py load-context --phase 3 --project ${REPO} --json > ${ctxFile} && ${PY} -c "import json,os; json.load(open('${ctxFile}')); print('REGEN_OK_'+str(os.path.getsize('${ctxFile}')))"`
      await agent(
        `You MUST use the Bash tool. Run exactly:\n${ctxRegenCmd}\nReturn the raw stdout as your final message.`,
        { label: 'ctx-regen-' + attempt, phase: 'Load FRs', agentType: 'general-purpose' },
      )
      continue
    }
  } catch (e) { log('  ctx-check agent failed: ' + String(e.message ?? e).slice(0, 80)); continue }

  try {
    // J1 fix (2026-06-29): forward fr_titles too. load-context emits fr_details as a
    // DICT keyed by FR id ({"FR-01":{"title":...}}). The previous parse only forwarded
    // fr_details_keys (no titles), and the consumer (frTitle below) read it as an Array
    // — so titles silently never populated. Emit an {id:title} map the consumer uses
    // directly.
    const ctxParseCmd = `${PY} -c "import json; d=json.load(open('${ctxFile}')); fd=d.get('fr_details') or {}; print(json.dumps({'fr_ids':d.get('fr_ids',[]),'fr_count':len(d.get('fr_ids',[])),'fr_titles':{k:(v.get('title','') if isinstance(v,dict) else '') for k,v in fd.items()}}))"`
    const ctxResult = await agent(
      `You MUST use the Bash tool. Run exactly:\n${ctxParseCmd}\nStdout is a single JSON line. Report via the StructuredOutput tool: fr_ids, fr_count, fr_titles = the EXACT values from that JSON line (transcribe, do not recompute).`,
      { label: 'load-ctx-a' + attempt, phase: 'Load FRs', agentType: 'general-purpose', schema: CTX_SCHEMA },
    )
    if (ctxResult && Array.isArray(ctxResult.fr_ids) && ctxResult.fr_ids.length > 0) {
      ctx = ctxResult
      log('  load-ctx OK (schema-validated, ' + ctx.fr_ids.length + ' FRs)')
      break
    }
    log('  load-ctx returned empty fr_ids (attempt ' + attempt + '): keys=' + Object.keys(ctxResult ?? {}).join(','))
  } catch (e) { log('  load-ctx agent failed: ' + String(e.message ?? e).slice(0, 80)); continue }
}
if (!ctx) return { error: 'Load FRs: ctx failed after 3 attempts', ctxFile }
let frIds = Array.isArray(ctx.fr_ids) ? ctx.fr_ids : []
if (!frIds.length) return { error: 'Load FRs: no fr_ids found in ctx', ctxKeys: Object.keys(ctx) }
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
const precheckResult = await agent(
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
    const frReport = await agent(
      'YOU ARE THE IMPLEMENTER for ' + frId + ' (' + (frTitle[frId] || '') + '). Run the full TDD chain for THIS ONE FR.\n'
      + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
      + 'Direction C (past lessons): FIRST, Bash `cat ' + REPO + '/.sessi-work/phase3_ctx.json` and READ the `lessons` field (compact markdown, "" if none). DO NOT repeat those past failure modes in this FR\'s TDD chain (implementation / tests / GATE1 fixes).\n\n'
      + 'Run these harness steps IN ORDER (each is a bash command; read its output before the next):\n'
      + '1. TDD-RED:    `' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step TDD-RED --project ' + REPO + ' --srs 01-requirements/SRS.md`\n'
  + '   AFTER RED writes the test file: open `tests/test_fr' + frNum + '.py` and ensure EVERY NFR associated with ' + frId + ' in the traceability table (TRACEABILITY_MATRIX.md §5 is the canonical listing) has a `# NFR-XX` comment on at least one test function. Without these annotations, `compute_trace_dimension` 4c = 0% and Gate 2 blocks (HR-16). Use `grep -n "# NFR-" tests/test_fr' + frNum + '.py` and check against the NFR list for ' + frId + ' — document every association.\n'
      + '2. MIRROR:     `' + PY + ' ' + REPO + '/harness_cli.py check-test-mirrors-spec --fr-id ' + frId + ' --test-file tests/test_fr' + frNum + '.py --project ' + REPO + '`\n'
      + '   On MIRROR FAIL: fix the TEST to match TEST_SPEC.md — do NOT edit TEST_SPEC.md (correctness was locked in Phase 2; P3 only implements). Re-run.\n'
      + '3. TDD-GREEN:  `' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step TDD-GREEN --project ' + REPO + ' --srs 01-requirements/SRS.md`\n'
      + '4. TDD-IMPROVE:`' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step TDD-IMPROVE --project ' + REPO + '`\n'
      + '   Coverage-filling tests that exercise ANOTHER FR\'s not-yet-implemented stub (the `_err(f"\'<name>\' is not yet implemented...")` pattern) MUST NOT assert on that stub\'s specific message text — it is temporary and will be replaced when the owning FR lands, breaking your test. Either skip that branch (it will be covered when the owning FR implements it and re-runs GATE1/DELTA) or assert only an invariant guaranteed stable across the stub-to-real transition (e.g. non-zero exit code), never the stub\'s literal text.\n'
      + '5. amend-sab (proactive, BEFORE GATE1): `' + PY + ' ' + REPO + '/harness_cli.py amend-sab --project ' + REPO + '` (idempotent, scans 03-development/src/). If it registers any new modules: `git -C ' + REPO + ' add .methodology/SAB.json && git -C ' + REPO + ' commit -m "amend: register SAB modules (' + frId + ')"`. This FR\'s GREEN/IMPROVE steps may have added modules GATE1\'s Architecture Amendment Protocol would otherwise BLOCK on — registering them now avoids a wasted GATE1 round.\n'
      + '6. GATE1 — long-running (harness runs up to 3 internal CODE-FIX rounds, each up to ~600s: can silently block ~2400s worst case). Run it BACKGROUNDED — do NOT invoke it as a plain synchronous command:\n'
      + '   GATE1 invocation procedure (a/b/c):\n'
      + '   a. Launch: `nohup ' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step GATE1 --project ' + REPO + ' > /tmp/gate1_' + frId + '.log 2>&1 & echo $!` — note the printed PID.\n'
      + '   b. Poll: every 30s run `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Repeat until DONE (cap 40 polls / ~20min, comfortably above the ~2400s worst case). Still RUNNING past the cap → report "' + frId + ' GATE1: TIMEOUT" (not FAIL) and stop — do not kill the PID.\n'
      + '   c. Once DONE: `cat /tmp/gate1_' + frId + '.log` for the full output — identical to what a synchronous run would have printed. Parse PASS/FAIL from it exactly as before.\n'
      + '   Gate 1 thresholds: linting(90) type_safety(85) test_coverage(80).\n'
      + '   - PASS → done.\n'
      + '   - FAIL → fix failing dims (ruff check . --fix; add tests for coverage; fix pyright errors), repeat the GATE1 invocation procedure (a/b/c). Max 3 rounds.\n'
      + '   - Still failing after 3 → report FAIL.\n'
      + '   - Structurally-broken dispatch [FATAL]: if the log contains "[FATAL]" (e.g. "claude.ai connectors are disabled" or any other structurally-broken-dispatch signature), STOP IMMEDIATELY — do NOT unset/modify any environment variables yourself, do NOT retry the GATE1 invocation procedure. The message\'s suggested fix is for a human operator to run OUTSIDE this session, not something you can act on. Report "' + frId + ' GATE1: FAIL — structurally broken dispatch environment, escalate to human (see [FATAL] message)" and stop this FR\'s TDD chain.\n'
      + '   - Harness bug [HARNESS-BUG]: if the log contains "[HARNESS-BUG]", harness-methodology itself crashed — this is NOT a problem with your code or tests. STOP IMMEDIATELY, do NOT retry, do NOT modify any project code to work around it. Report "' + frId + ' GATE1: FAIL — harness-methodology bug detected, escalate to human (see [HARNESS-BUG] message and its crash bundle path)" and stop this FR\'s TDD chain.\n'
      + '   - Architecture Amendment Protocol [BLOCKED]: if the log contains "Unregistered modules detected: {…}", DO NOT hand-edit SAB.json by hand. Run `' + PY + ' ' + REPO + '/harness_cli.py amend-sab --project ' + REPO + '` to register the new modules (idempotent, scans 03-development/src/), `git -C ' + REPO + ' add .methodology/SAB.json && git -C ' + REPO + ' commit -m "amend: register SAB modules (' + frId + ')"`, then repeat the GATE1 invocation procedure (a/b/c). Max 1 amend round per FR.\n'
      + '   run-fr-step auto-pushes on completion (idempotent). Crash recovery: `resume-fr-phase --phase 3 --project ' + REPO + '`.\n'
      + '7. ORCH-POST (after GATE1 PASS, per phase3_plan.md [ORCH-POST]):\n'
      + '   a. `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 40.0 --fr-id ' + frId + '` (per-FR D4 ≥40%). FAIL → add the missing test implementations for ' + frId + ', re-run.\n'
      + '   b. SAB.json is kept in sync by amend-sab (step 5 above). Do NOT run generate_sab.py --overwrite here.\n'
      + '      (generate_sab.py --overwrite rebuilds SAB purely from SAD.md §5, which was locked in Phase 2\n'
      + '       and may not reflect modules added during Phase 3 implementation. Only run generate_sab.py --overwrite\n'
      + '       manually AFTER updating SAD.md §5 to include all Phase 3 modules.)\n\n'
      + 'Implement the module per SPEC.md (read ' + REPO + '/SPEC.md for ' + frId + ') + SAD.md module mapping. Write source under the package directory layout your project uses: if `03-development/src/<package>/` is a FLAT PACKAGE (one `<module>.py` per file, e.g. `03-development/src/<package>/<module>.py`), write `src/<package>/<module>.py`; if it is MODULE-PER-DIR (one `<module>/__init__.py` per directory), write `src/<package>/<module>/__init__.py`. The init-project directory scaffold shows which layout your project uses. Do NOT place this FR\'s implementation inside a file another FR already owns or a shared/global file (e.g. `cli.py`) used by multiple FRs — each FR\'s logic belongs in its own module per SAD.md/quality_manifest.json\'s fr_module_traceability mapping. Tests for ' + frId + ' MUST be placed at the path(s) declared in TEST_SPEC.md §FR-' + frNum + ' (test file list) — TEST_SPEC.md is the canonical source of truth for test placement. If TEST_SPEC lists multiple test files (e.g. unit + integration variants), you MUST create all of them; pass `--test-file <path1> --test-file <path2> ...` to MIRROR and related tooling. The legacy single-file convention (`tests/test_fr' + frNum + '.py` only) is no longer required when TEST_SPEC specifies otherwise. Docstrings must include [' + frId + '] reference (NFR-05).\n\n'
      + 'Report final line: "' + frId + ' GATE1: PASS" or "' + frId + ' GATE1: FAIL — <reason>".\n\n'
      + 'SCOPE RULES:\n- DO NOT implement any FR OTHER than ' + frId + '.\n- DO NOT run run-gate (Gate 2), advance-phase, or push-milestone.\n- DO NOT edit .methodology/quality_manifest.json or .sessi-work/gate1_result.json to fake/reset scores — fix the underlying code/tests instead.\n- DO NOT modify harness/ (HR-17).\n- ONLY the 7 steps above for ' + frId + ' (amend-sab in step 5, spec-coverage-check in step 7a is allowed).',
      { label: 'tdd-' + frId, phase: 'Per-FR TDD', agentType: 'general-purpose' },
    )
    // L1: distinguish a session/rate-limit block (null/empty agent return) from a real
    // Gate 1 FAIL — mirror the Gate 2 detection (below). Without this, a rate-limit mid-
    // TDD is misreported as a code-quality Gate 1 failure. Sentinel GUARD skips completed
    // FRs on resume, so aborting here is safe.
    if (frReport === null || frReport === undefined || (typeof frReport === 'string' && frReport.length < 10)) {
      log('  ' + frId + ' agent blocked (session limit / rate limit) — aborting, resume after quota reset')
      return { session_limit_blocked: true, phase: 3, fr_id: frId, gate1Pass, message: 'Agent hit session/rate limit during ' + frId + ' TDD. Resume after quota reset — sentinel GUARD will skip completed FRs.' }
    }
    // L1.5: detect a structurally-broken dispatch [FATAL] surfaced via the sub-agent
    // (harness/cli/fr_cmds.py:_abort_dispatch_structurally_broken prints "[FATAL] <fr> <step>:
    // sub-agent dispatch is structurally broken — claude.ai connectors are disabled" to
    // stderr and returns exit code 23). A sub-agent reading its own GATE1 log and seeing
    // that banner will escalate to human with "FAIL — structurally broken dispatch" even
    // when the gate has not yet run a single evaluation round. The harness-side
    // _is_connector_disabled_failure guard already catches this AT the fr_cmds.py layer
    // for LINT-FIX / COVERAGE-FIX / GATE1-final-dispatch, but TDD-RED/GREEN/IMPROVE
    // dispatches AND the GATE1 first-round prompt path do NOT have it. Continuing to
    // dispatch FR-02..FR-05 in that state burns ~5min and ~50K tokens per FR on
    // identically-broken dispatches. Abort once the structural signal is observed.
    // FIX-N: workflow JS L1 [FATAL] detection — abort loop on connector-disabled signature.
    const frReportText = (typeof frReport === 'string') ? frReport : JSON.stringify(frReport)
    if (/structurally broken dispatch environment/i.test(frReportText) || /\[FATAL\][^\n]*dispatch is structurally broken/i.test(frReportText)) {
      log('  ' + frId + ' reports [FATAL] structurally broken dispatch (claude.ai connectors disabled) — aborting remaining FRs')
      return { dispatch_structurally_broken: true, phase: 3, fr_id: frId, gate1Pass, gate1Fail: [...gate1Fail, frId], message: frId + ' GATE1: dispatch is structurally broken (env: ANTHROPIC_API_KEY overrides claude.ai login). Human must unset ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN/ANTHROPIC_BASE_URL/ANTHROPIC_DEFAULT_HAIKU_MODEL in the shell that launches this process, then re-run via Workflow({scriptPath, resumeFromRunId}).' }
    }
    // L1.6 (Round 13 站0): detect a [HARNESS-BUG] banner (core/errors.py's crash
    // boundary — harness_cli.py's main() converting an uncaught exception into this
    // signal instead of a bare traceback) surfaced via the sub-agent reading its own
    // GATE1 log. Unlike the structurally-broken-dispatch signature above (a known,
    // human-actionable env-var cause), this means harness-methodology itself crashed —
    // the FR loop cannot proceed until a human fixes the harness bug, and treating it
    // as a code-quality GATE1 FAIL would send CODE-FIX at a defect that isn't there.
    if (/\[HARNESS-BUG\]/.test(frReportText)) {
      log('  ' + frId + ' reports [HARNESS-BUG] — harness-methodology crashed, aborting remaining FRs')
      return { harness_bug_detected: true, phase: 3, fr_id: frId, gate1Pass, gate1Fail: [...gate1Fail, frId], message: frId + ' GATE1: harness-methodology itself crashed ([HARNESS-BUG] — see the crash bundle path in the log). This is not a project quality issue; a human must diagnose and fix the harness bug before this FR can proceed.' }
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
    const verifyResult = await agent(
      'You MUST use the Bash tool. Run EXACTLY this single command (single line):\n'
      + PY + ' ' + REPO + '/harness/scripts/verify_gate1_qc.py --fr-id ' + frId + ' --project ' + REPO + '\n'
      + 'Then report via the StructuredOutput tool: pass = true ONLY if the FIRST line of stdout is exactly "GATE1_VERIFIED_PASS"; reason = the verbatim stdout (do NOT paraphrase, summarize, or prepend commentary).',
      { label: 'gate1-verify-' + frId, phase: 'Per-FR TDD', agentType: 'general-purpose', schema: VERDICT_SCHEMA }
    )
    const verifyOut = String((verifyResult && verifyResult.reason) || '').trim()
    // Round 12 站2a: verdict from the deterministic stdout ONLY — the AND
    // on verifyResult.pass contradicted the comment above (v2.13.3 shipped
    // "the LLM's pass field is ignored" in prose while the code still let a
    // hallucinated pass:false veto a PASS manifest, the exact
    // wf_53d055ce-d0b incident this step exists to prevent).
    const passed = verifyOut.startsWith('GATE1_VERIFIED_PASS')
    if (passed) { gate1Pass.push(frId); log('  ' + frId + ' Gate 1 PASS (' + gate1Pass.length + '/' + frIds.length + ') [harness-verified]') }
    else { gate1Fail.push(frId); log('  ' + frId + ' Gate 1 FAIL [harness manifest qc != true; sub-agent self-report ignored]') }
  }

  // PUSH ③ p3-mid — fire once when ≥1/3 FRs have Gate 1 PASS (but not yet all done).
  if (!p3MidPushed && gate1Pass.length >= p3MidThreshold && gate1Pass.length < frIds.length) {
    p3MidPushed = true
    log('  ≥1/3 FRs Gate 1 PASS (' + gate1Pass.length + '/' + frIds.length + ') — pushing p3-mid milestone')
    await agent(
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
  return { error: 'Phase 3: Gate 1 FAILED for FR(s): ' + gate1Fail.join(', ') + ' (escalate — fix code/tests, resume-fr-phase)', gate1Pass, gate1Fail }
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Milestones
// ══════════════════════════════════════════════════════════════════════════

phase('Milestones')
log('All ' + frIds.length + ' FRs Gate 1 PASS — push p3-pre-gate2 (last stable snapshot before Gate 2)')
const preGate2Report = await agent(
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
log('Gate 2 exit (composite ≥75, 9 dims: 8 self-scored + traceability framework-owned)')
let gate2Pass = false, gate2Report = '', gate2Blocked = false
for (let round = 1; round <= 3; round++) {
  log('  Gate 2 round ' + round + '/3')
  const g2Integrity = await checkManifestIntegrity('Gate 2', 'g2-integrity-r' + round)
  if (!g2Integrity.ok) {
    return { error: 'Gate 2 round ' + round + ': quality_manifest.json corrupted mid-run', detail: g2Integrity.raw, recovery: 'git checkout HEAD -- .methodology/quality_manifest.json (verify HEAD is healthy first — a corrupted manifest may already be committed)', note: 'Corruption appeared AFTER the entry integrity check. Inspect the previous round\'s agent transcript for the writer before restoring.' }
  }
  gate2Report = await agent(
    'YOU ARE THE GATE-2 ORCHESTRATOR (Phase 3 exit). ROUND ' + round + '.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Steps:\n'
    + '1. G2a: `' + PY + ' ' + REPO + '/harness_cli.py run-gate --gate 2 --phase 3 --project ' + REPO + '` — read the printed evaluation prompt.\n'
    + '2. G2b: Evaluate ALL Gate 2 dimensions inline per ' + REPO + '/harness/harness/ssi/prompts/evaluate_dimension.md. Write ' + REPO + '/.sessi-work/gate2_result.json.\n   Dims: use the exact `dimensions:` list G2a just printed (it is computed from gate2_p3_exit.yaml, filtered by enabled feature flags — always current, do NOT hand-copy a dim list here).\n   NOTE: mutation_testing is disabled by default via .methodology/harness_config.json (mutation_testing=false). If enabled, the harness auto-includes it and re-normalises the composite score.\n   NOTE: traceability is FRAMEWORK-OWNED — do NOT score it; the harness injects it in finalize-gate.\n   For any failing dim: fix the ROOT CAUSE in code (ruff/pyright/add tests/bandit/mutation), re-run the tool, update the score. (No auto-fix engine.)\n'
    + '3. G2c — run BACKGROUNDED (finalize-gate\'s own git push triggers the local pre-push hook, plus CRG refresh: bounded on this project today, but a single opaque Bash call with no visible output until it returns is exactly the shape the 180s stall watchdog kills — same class of risk as GATE1, same fix):\n   a. Launch: `nohup ' + PY + ' ' + REPO + '/harness_cli.py finalize-gate --gate 2 --phase 3 --project ' + REPO + ' > /tmp/gate2_finalize_r' + round + '.log 2>&1 & echo $!` — note the printed PID.\n   b. Poll: every 15s run `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Repeat until DONE (cap 40 polls / ~10min). Still RUNNING past the cap → report "GATE2: TIMEOUT" and stop — do not kill the PID.\n   c. Once DONE: `cat /tmp/gate2_finalize_r' + round + '.log` for the full output — identical to what a synchronous run would have printed.\n\n'
    + '4. D4: `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 60.0`. FAIL → add missing test implementations, re-run.\n'
    + 'finalize-gate (G2c) writes HANDOVER.md + pushes on PASS. Report final line: "GATE2: PASS" (composite ≥75 AND all dims ≥ threshold AND D4 ≥60%) or "GATE2: FAIL — <failing dims>".\n\n'
    + 'SCOPE RULES:\n- DO NOT run advance-phase or push-milestone p3-post-gate2 (next phase does that).\n- DO NOT edit .sessi-work/gate2_result.json to fake scores — fix the code.\n- DO NOT modify harness/ (HR-17).\n- ONLY run-gate/eval/finalize/spec-coverage + code fixes.',
    { label: 'gate2-r' + round, phase: 'Gate 2', agentType: 'general-purpose' },
  )
  if (gate2Report === null || gate2Report === undefined || (typeof gate2Report === 'string' && gate2Report.length < 10)) {
    gate2Blocked = true
    log('  Gate 2 agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
    break
  }
  const gate2VerifyCmd = PY + ' -c "import json; g=(json.load(open(\'' + REPO + '/.methodology/quality_manifest.json\')).get(\'gate_results\',{}) or {}).get(\'gate2\') or {}; print(json.dumps({\'qc\': (isinstance(g,dict) and g.get(\'quality_complete\') is True), \'score\': (g.get(\'score\') if isinstance(g,dict) else None)}))"'
  const g2v = await agent(
    'Run these TWO commands via the Bash tool, in order:\n'
    + '1. `' + gate2VerifyCmd + '` — stdout is a single JSON line with qc + score.\n'
    + '2. `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 60.0; echo "RC=$?"`\n'
    + 'Then report via the StructuredOutput tool: manifest_qc = the exact qc boolean from command 1; d4_rc = the exact numeric exit code echoed on command 2\'s final RC= line; detail = qc/score/RC in one line.',
    { label: 'gate2-verify-r' + round, phase: 'Gate 2', agentType: 'general-purpose', schema: GATE_VERIFY_SCHEMA },
  )
  gate2Pass = !!(g2v && g2v.manifest_qc === true && g2v.d4_rc === 0)
  if (gate2Pass) { log('  Gate 2 PASS [harness-verified: manifest qc=true, D4 rc=0]'); break }
  log('  Gate 2 not yet PASS [' + (g2v ? String(g2v.detail ?? '') : 'verify agent null') + '] — retry round ' + (round + 1))
}
if (gate2Blocked) {
  return { session_limit_blocked: true, gate: 2, message: 'Agent hit session/rate limit during Gate 2 evaluation. Resume after quota reset — GUARD checks will skip completed FRs.' }
}
if (!gate2Pass) {
  return { error: 'Gate 2 did not PASS in 3 rounds (HR-08; write deferred_fixes.md + escalate to human)', raw: String(gate2Report ?? '').slice(-600) }
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
  // Last-line integrity guard: the phase-exit push commits .methodology/
  // wholesale — block here so mid-run corruption never reaches git history
  // (2026-07-02: commit 3198402 baked a corrupted manifest into main).
  // Re-check every round — a fix attempt in a prior round could reintroduce it.
  const advIntegrity = await checkManifestIntegrity('Advance', 'advance-integrity-r' + round)
  if (!advIntegrity.ok) {
    return { error: 'Advance round ' + round + ': quality_manifest.json corrupted — refusing to commit it', detail: advIntegrity.raw, recovery: 'git checkout HEAD -- .methodology/quality_manifest.json (verify HEAD is healthy first), merge the latest gate result back into gate_results, then resume', note: 'Blocking prevents the corruption from being committed by the phase-exit push.' }
  }
  advanceReport = await agent(
    'YOU ARE THE PHASE-3 EXIT ORCHESTRATOR. Advance to Phase 4. ROUND ' + round + '.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Steps:\n'
    + '0. GUARD — already advanced? `PHASE=$(jq -r .current_phase ' + REPO + '/.methodology/state.json 2>/dev/null); echo "current_phase=$PHASE"; [ "$PHASE" -ge 4 ]`. If Phase 4 is confirmed, report "ADVANCE: PASS (already advanced)" and stop.\n'
    + '1. GUARD + PUSH ⑤ p3-post-gate2: `git -C ' + REPO + ' log --oneline --grep="P3-post-gate2)" -1`. If a commit exists, skip the push. Else: `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p3-post-gate2 --project ' + REPO + ' --fr-ids ' + gate1Pass.join(',') + '`\n   Pre-flight (enforced): gate2_result.json composite ≥75 + per-FR Gate 1 sentinel .sessi-work/sentinels/g1_p3_<fr>.flag exists for every FR. If BLOCKED, read the error list and fix.\n'
    + '2. advance-phase — run BACKGROUNDED (internally runs `ruff check .` + `mypy .` + `pytest --cov-fail-under=100` over the WHOLE project as sequential subprocess calls inside one opaque Bash call; harmless today at this project\'s size (~25s measured) but this cost only grows as more FRs/tests land, and a single opaque long Bash call is exactly what the 180s stall watchdog kills — same class of risk as GATE1, same fix):\n   a. Launch: `nohup ' + PY + ' ' + REPO + '/harness_cli.py advance-phase --completed 3 --project ' + REPO + ' > /tmp/advance_r' + round + '.log 2>&1 & echo $!` — note the printed PID.\n   b. Poll: every 15s run `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Repeat until DONE (cap 40 polls / ~10min). Still RUNNING past the cap → report "ADVANCE: TIMEOUT" and stop — do not kill the PID.\n   c. Once DONE: `cat /tmp/advance_r' + round + '.log` for the full output — identical to what a synchronous run would have printed.\n   advance-phase independently re-verifies EVERYTHING before it will advance (lint, types, coverage, document quality, reliability lint, architecture drift, Phase Truth, and more) — its own output tells you exactly what is missing. If it prints "[BLOCKED] ...", that message IS the fix instruction: read it verbatim and do exactly what it says (it often includes the precise command to run), then repeat the advance-phase backgrounded procedure (a/b/c). Do NOT guess what might be wrong — trust only what advance-phase itself reports.\n   advance-phase is safe to re-run: it re-checks and re-reports without side effects until every check passes, so iterate within this round as many times as needed.\n'
    + '3. Read ' + REPO + '/.methodology/state.json; confirm current_phase = 4 (advance-phase atomically writes state.json when complete).\n\n'
    + 'Report final line: "ADVANCE: PASS|FAIL — <details>". If still FAIL after exhausting this round\'s turn, report the LAST [BLOCKED] message verbatim so the next round starts from where this one left off. PHASE_4_PLAN: ' + REPO + '/.methodology/phase4_plan.md\n\n'
    + 'SCOPE RULES:\n- DO NOT re-implement FRs.\n- DO NOT use --no-verify.\n- DO NOT modify harness/ (HR-17).\n- ONLY push-milestone p3-post-gate2 + advance-phase + verify HANDOVER.md + the specific fixes advance-phase\'s own output asked for.\n- Any diagnostic/debug script MUST be written under .sessi-work/tmp/ (never repo root or source dirs) and self-cleaned before you exit.',
    { label: 'advance-r' + round, phase: 'Advance', agentType: 'general-purpose' },
  )
  if (advanceReport === null || advanceReport === undefined || (typeof advanceReport === 'string' && advanceReport.length < 10)) {
    log('  Advance agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
    return { session_limit_blocked: true, phase: 3, step: 'advance', message: 'Agent hit session/rate limit during Advance. Resume after quota reset — the GUARD step skips if already advanced.' }
  }
  // AUTHORITATIVE Advance verdict: advance-phase atomically writes
  // state.json current_phase=4 on success. Read it via a schema proxy —
  // the orchestrator's prose "ADVANCE: PASS" is narrative only.
  const advVerifyCmd = PY + ' -c "import json; print(json.dumps({\'current_phase\': int(json.load(open(\'' + REPO + '/.methodology/state.json\')).get(\'current_phase\') or 0)}))"'
  const advV = await agent(
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
  return { error: 'Advance did not PASS in ' + ADVANCE_MAX_ROUNDS + ' rounds — check HANDOVER.md + state.json + the last [BLOCKED] message below. If Phase 4 is confirmed, resume workflow to verify.', raw: String(advanceReport ?? '').slice(-600) }
}

// Bug A fix (2026-07-07): advance-phase intentionally commits the handover
// locally without pushing (harness/cli/phase_cmds.py: "next milestone push
// publishes to origin"). This workflow ends right after Advance with no
// next-phase push queued, so the handover commit was left stranded on
// local until whatever runs next happened to push it. Publish it now.
phase('Sync')
log('git push origin main (publish advance handover commit)')
const SYNC_PROMPT = 'Run EXACTLY this command via Bash:\n'
  + 'git -C ' + REPO + ' push origin main\n\n'
  + 'Report final outcome as plain text: "SYNC: PASS" or "SYNC: FAIL — <one-line reason>"'
  + ' (if a pre-push hook printed a blocker list, include it verbatim).'
let syncReport = await agent(SYNC_PROMPT, { label: 'sync', phase: 'Sync', agentType: 'general-purpose' })
let syncPass = /SYNC:\s*PASS/.test(String(syncReport ?? ''))
if (!syncPass) {
  // One retry only — covers transient failures (DNS/auth-token blips), not
  // a real pre-push gate block, which is deterministic and won't clear on
  // its own.
  log('  Sync FAIL on first attempt — retrying once (covers transient network failures)')
  syncReport = await agent(SYNC_PROMPT, { label: 'sync-retry', phase: 'Sync', agentType: 'general-purpose' })
  syncPass = /SYNC:\s*PASS/.test(String(syncReport ?? ''))
}

if (!syncPass) {
  // Do NOT auto `--no-verify` (HR-17 forbids bypassing the gate without a
  // human decision). Surface the blocker instead of terminating with a bare
  // error: state.json current_phase is already authoritative for Phase 4
  // (Advance PASS'd above), the handover commit just hasn't reached origin
  // yet — a human resolves the printed blocker(s) and pushes manually.
  const blockers = String(syncReport ?? '').slice(-600)
  await agent(
    'Append this section to the END of ' + REPO + '/HANDOVER.md (append — do not overwrite '
    + 'existing content; create the file only if it truly does not exist):\n\n'
    + '## Sync Blocked — manual push required\n\n'
    + 'The Phase 3 advance handover commit landed locally but `git push origin main` '
    + 'did not pass the pre-push hook:\n\n'
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
  phase: 3,
  fr_count: frIds.length,
  gate1_pass: gate1Pass,
  gate2_status: gate2Pass ? 'PASS' : 'unknown',
  advance_status: 'PASS',
  sync_status: 'PASS',
  artifacts: ['03-development/src/', 'tests/', '.methodology/gate2_result.json', 'HANDOVER.md'],
  notes: 'Phase 3 complete per phase3_plan.md v2.12.0. All FRs Gate 1 PASS + Gate 2 PASS. Phase 4 (Testing) ready.',
}
