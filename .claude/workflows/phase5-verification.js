// Phase 5 — Verification & Delivery (faithful to .methodology/phase5_plan.md v2.12.0)
//
// Structure: FR-loop型, NO harness run-gate (P5 cleared by Gate 3 at P4 exit).
// Per-FR GATE1-DELTA re-eval (auto-triggers full TDD on code change), then
// generate BASELINE.md + VERIFICATION_REPORT.md (BASELINE.md is a blocking
// audit-phase C1 deliverable with a 7-H2-section depth check; the earlier
// "merged into VERIFICATION_REPORT" note came from a generate_full_plan.py
// copy-paste error, fixed harness-side), p5-baseline milestone push, advance
// (advance-phase still enforces TDD-PRECHECK + D4 ≥80%, and Gate 4 next phase
// needs ≥90% so we warn-check here).
//
// Playbook lessons: NO import/fs/process, Bash CLI, SCOPE RULES,
// PY = .venv/bin/python, scriptPath launch.
// v4 (2026-07-02): gate verdicts use FLAT schema: (playbook §5.2 rev) — regex
// over LLM prose was the root cause of the #126/#134/#135/#136/ENV_CHECK_RC
// bug class. Heavy orchestrators keep prose narrative; verdicts come from
// schema proxy agents reading harness artifacts (manifest qc, state.json, rc).

export const meta = {
  name: 'phase5-verification',
  description: 'Phase 5 Verification — per-FR GATE1-DELTA + BASELINE/VERIFICATION_REPORT + p5-baseline push (phase5_plan.md v2.12.0)',
  phases: [
    { title: 'Entry & Preflight' },
    { title: 'Env Check' },
    { title: 'Manifest Integrity' },
    { title: 'Load FRs' },
    { title: 'Per-FR Delta' },
    { title: 'Verification Docs' },
    { title: 'Artifacts Commit' },
    { title: 'Milestone' },
    { title: 'Advance' },
  ],
}

// ---- args / REPO / PY ----
// REPO precedence: args.repo override wins, then DEFAULT_REPO canonical path.
// process.env.HARNESS_REPO cannot be read here — playbook §4 forbids process.*
// in workflow JS. Caller scripts (run-e2e.mjs / harness-e2e.js /
// phase1-workflow.mjs) read HARNESS_REPO and inject it via args.repo.
const DEFAULT_REPO = '.'
let REPO = DEFAULT_REPO
if (typeof args === 'string') { try { args = JSON.parse(args) } catch {} }
if (args && typeof args === 'object' && typeof args.repo === 'string' && args.repo.length > 0) REPO = args.repo
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
// state.json current_phase, CLI exit code) and reports through the schema.
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
  },
  required: ['fr_ids', 'fr_count'],
}
const PHASE_SCHEMA = {
  type: 'object',
  properties: { current_phase: { type: 'integer', description: 'current_phase value read from state.json' } },
  required: ['current_phase'],
}
const DELTA_FAST_SCHEMA = {
  type: 'object',
  properties: {
    pass_fr_ids: { type: 'array', items: { type: 'string' }, description: 'FRs whose manifest gate1 quality_complete printed True after GATE1-DELTA' },
    fail_fr_ids: { type: 'array', items: { type: 'string' }, description: 'FRs that did not print True (False/None/timeout/error)' },
  },
  required: ['pass_fr_ids', 'fail_fr_ids'],
}

// ════════════════════════════════════════════════════════════════════════
// Phase: Entry & Preflight (incl. attestation fix per P5+ ASPICE)
// ════════════════════════════════════════════════════════════════════════

phase('Entry & Preflight')
log('ENTRY-CHECK Gate3 + run-phase 5 (reliability/config/attestation fixes) + handoff + CI')
const preflightReport = await agent(
  'YOU ARE THE PHASE-5 PREFLIGHT ORCHESTRATOR. Run bash in order; report.\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
  + 'Steps:\n'
  + '1. ENTRY-CHECK: run EXACTLY this bash command to verify Gate 3 status (do NOT rely on reading the file yourself — use the command output):\n`' + PY + ' -c "import json; m=json.load(open(\'' + REPO + '/.methodology/quality_manifest.json\')); g3=(m.get(\'gate_results\',{}) or {}).get(\'gate3\',{}) or {}; print(\'GATE_VERIFIED\' if isinstance(g3,dict) and g3.get(\'quality_complete\') is True else \'GATE_MISSING\')"`\nIf GATE_MISSING → FAIL (return to Phase 4).\n'
  + '2. PREFLIGHT: `' + PY + ' ' + REPO + '/harness_cli.py run-phase --phase 5 --project ' + REPO + '`. FAIL → fix, re-run (max 3). Also fix if reported:\n'
  + '   - reliability lint: subprocess without timeout=, mkstemp outside try/finally, os.path.exists-before-open (TOCTOU), time.sleep in async def.\n'
  + '   - config liveness: env keys read in code but absent from .env.example.\n'
  + '   - attestation missing/mismatch: run `' + PY + ' ' + REPO + '/harness_cli.py build-trace-attestation --project ' + REPO + ' --write`, then `git -C ' + REPO + ' add .methodology/trace/attestation.json && git -C ' + REPO + ' commit -m "trace: regen attestation"`, then re-run run-phase until "Attestation: clean".\n'
  + '3. HANDOFF: `' + PY + ' ' + REPO + '/harness_cli.py validate-handoff --from-phase 4 --project ' + REPO + '`. Must exit 0.\n'
  + '4. PREFLIGHT-CI: confirm `' + REPO + '/.github/workflows/harness_quality_gate.yml` (CI workflow) + `' + REPO + '/.git/hooks/prepare-commit-msg` (git hook) both exist; confirm state.json current_phase=5. If stale: `init-project --phase 5 --project ' + REPO + ' --overwrite`.\n\n'
  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if ALL 4 steps succeeded; reason = one-line summary (on FAIL: which step + verbatim error tail).\n\n'
  + 'SCOPE RULES:\n- DO NOT generate BASELINE/VERIFICATION docs or run TDD steps.\n- DO NOT run advance-phase/push-milestone.\n- DO NOT modify harness/.\n- ONLY preflight commands + fixes.',
  { label: 'preflight', phase: 'Entry & Preflight', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
)
if (!(preflightReport && preflightReport.pass === true)) {
  return { error: 'Phase 5 preflight did not PASS', reason: preflightReport ? String(preflightReport.reason ?? '').slice(-600) : 'agent returned null (skipped or terminal API error)' }
}

// ════════════════════════════════════════════════════════════════════════
// Phase: Env Check
// ════════════════════════════════════════════════════════════════════════
phase('Env Check')
log('run-env-check (root-cause fix: CLI exit code reflects ready flag)')
// Bug #127 root-cause fix (2026-06-27): `cmd_run_env_check` now returns
// exit 0 when ready=true and 1 when ready=false (previously always 0).
// Workflows check `$?` directly with no LLM orchestrator agent in the loop.
// 2026-07-02 paraphrase incident (phase3): the agent rewrote ENV_CHECK_RC=0
// as "RC=0" and the regex gate false-negatived a READY environment. Schema
// transport is paraphrase-proof.
const envReport = await agent(
  'You MUST use the Bash tool. Run exactly this ONE command (single line, the `;` keeps $? bound to run-env-check):\n'
  + PY + ' ' + REPO + '/harness_cli.py run-env-check --phase 5 --project ' + REPO + '; echo "RC=$?"\n'
  + 'Then report via the StructuredOutput tool: rc = the exact numeric exit code echoed on the final RC= line.',
  { label: 'env-check', phase: 'Env Check', agentType: 'general-purpose', schema: RC_SCHEMA },
)
if (!(envReport && envReport.rc === 0)) {
  return { error: 'Phase 5 env-check did not PASS', rc: envReport ? envReport.rc : null, note: envReport ? 'run-env-check exit ' + envReport.rc + ' — read .sessi-work/env_check_result.json' : 'agent returned null (skipped or terminal API error)' }
}

// ════════════════════════════════════════════════════════════════════════
// Phase: Manifest Integrity (ported from phase3, 155ec07 + 286ccca)
// ════════════════════════════════════════════════════════════════════════
phase('Manifest Integrity')
// 2026-07-02 incident class: a sub-agent action (bare pytest → harness test
// CWD leak) can corrupt quality_manifest.json MID-RUN, not just before entry.
// Detect the three known corruption patterns (fr_ids truncated, traceability
// cleared, gate1 wiped) at entry AND re-check before the phase-exit push so
// corruption is never baked into a milestone commit.
const integrityCmd = PY + ' -c "import json, sys; m = json.load(open(\'' + REPO + '/.methodology/quality_manifest.json\')); ids = m.get(\'fr_ids\') or []; mt = m.get(\'fr_module_traceability\') or {}; g1 = (m.get(\'gate_results\',{}) or {}).get(\'gate1\',{}) or {}; ok_ids = len(ids) >= 2; ok_trace = len(mt) >= len(ids); ok_g1 = isinstance(g1, dict) and len(g1) >= len(ids); print(\'OK\' if (ok_ids and ok_trace and ok_g1) else json.dumps({\'BROKEN\': True, \'fr_ids_count\': len(ids), \'traceability_count\': len(mt), \'gate1_keys\': len(g1), \'recovery\': \'git checkout HEAD -- .methodology/quality_manifest.json\'}))"'
async function checkManifestIntegrity(phaseLabel, agentLabel) {
  const verdict = await agent(
    'Run EXACTLY this command via the Bash tool:\n`' + integrityCmd + '`\n'
    + 'Then report via the StructuredOutput tool: pass = true ONLY if stdout is exactly `OK`; reason = the verbatim stdout.',
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

// ════════════════════════════════════════════════════════════════════════
// Phase: Load FRs
// ════════════════════════════════════════════════════════════════════════
phase('Load FRs')
log('load-context --phase 5 → fr_ids')
// v15: retry loop — agent() wrapped (Bug #2); v4: schema transport, no prose parsing
// v2.13.1: hardened against agent hallucination (Bug #122).
let ctx = null
const ctxFile = REPO + '/.sessi-work/phase5_ctx.json'
for (let attempt = 1; attempt <= 3; attempt++) {
  try {
    // Bug #134 fix (2026-06-28): validate JSON-parseable, not just non-zero size.
    // Previous `test -s FILE && echo FILE_OK_<size>` passed for partial writes.
    // Root-cause: use `python3 -c 'json.load(...)'` so incomplete JSON raises
    // mid-write → no FILE_OK marker → regen path triggered.
    // Bug #136 sibling: bash built via template literal (single quotes safe).
    const ctxCheckCmd = `${PY} -c "import json,os,sys; json.load(open('${ctxFile}')); print('FILE_OK_'+str(os.path.getsize('${ctxFile}')))" || echo FILE_MISSING`
    const existsVerdict = await agent(
      `You MUST use the Bash tool. Run exactly:\n${ctxCheckCmd}\nThen report via the StructuredOutput tool: pass = true ONLY if stdout starts with FILE_OK_; reason = the verbatim stdout.`,
      { label: 'ctx-check-' + attempt, phase: 'Load FRs', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
    )
    if (!(existsVerdict && existsVerdict.pass === true)) {
      log('  ctx file missing/invalid (attempt ' + attempt + ') — regenerating')
      const ctxRegenCmd = `${PY} ${REPO}/harness_cli.py load-context --phase 5 --project ${REPO} --json > ${ctxFile} && ${PY} -c "import json,os; json.load(open('${ctxFile}')); print('REGEN_OK_'+str(os.path.getsize('${ctxFile}')))"`
      await agent(
        `You MUST use the Bash tool. Run exactly:\n${ctxRegenCmd}\nReturn the raw stdout as your final message.`,
        { label: 'ctx-regen-' + attempt, phase: 'Load FRs', agentType: 'general-purpose' },
      )
      continue
    }
  } catch (e) { log('  ctx-check agent failed: ' + String(e.message ?? e).slice(0, 80)); continue }

  // Bug #135 fix (2026-06-28) + v4 schema transport: emit parseable JSON via
  // Python; the agent transcribes the fields into StructuredOutput (AJV-
  // validated, retries on mismatch). No prose parsing left on this path.
  try {
    const ctxParseCmd = `${PY} -c "import json; d=json.load(open('${ctxFile}')); print(json.dumps({'fr_ids':d.get('fr_ids',[]),'fr_count':len(d.get('fr_ids',[]))}))"`
    const ctxResult = await agent(
      `You MUST use the Bash tool. Run exactly:\n${ctxParseCmd}\nStdout is a single JSON line. Report via the StructuredOutput tool: fr_ids, fr_count = the EXACT values from that JSON line (transcribe, do not recompute).`,
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
let frIds = Array.isArray(ctx.fr_ids) ? ctx.fr_ids
  : (Array.isArray(ctx.fr_details) ? ctx.fr_details.map(f => f.id || f.fr_id || f.fr).filter(Boolean) : [])
if (!frIds.length) return { error: 'Load FRs: no fr_ids found in ctx', ctxKeys: Object.keys(ctx) }
const frTitle = {}
if (Array.isArray(ctx.fr_details)) for (const f of ctx.fr_details) frTitle[f.id || f.fr_id] = f.title || f.name || ''
log('  fr_ids = ' + JSON.stringify(frIds))

// ════════════════════════════════════════════════════════════════════════
// Phase: Per-FR Delta (GATE1-DELTA; auto-triggers full TDD on code change)
// ════════════════════════════════════════════════════════════════════════
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
const fastProbe = await agent(
  'YOU ARE THE GATE1-DELTA FAST-PATH PROBE. Classify each FR — fix NOTHING.\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\nFRs: ' + JSON.stringify(frIds) + '\n\n'
  + 'For EACH FR in order, substituting <FR> with the FR id:\n'
  + '1. `' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 5 --fr-id <FR> --step GATE1-DELTA --project ' + REPO + ' 2>&1 | tail -5`\n'
  + '2. Authoritative verdict (manifest qc AND a phase-5 gate-1 timestamp for <FR>): `' + PY + ' -c "import json; g=(json.load(open(\'' + REPO + '/.methodology/quality_manifest.json\')).get(\'gate_results\',{}) or {}).get(\'gate1\',{}).get(\'<FR>\',{}) or {}; ts=any(e.get(\'phase\')==5 and e.get(\'gate\')==1 and e.get(\'fr_id\')==\'<FR>\' for e in (json.loads(l) for l in open(\'' + REPO + '/.methodology/gate_timestamps.jsonl\') if l.strip())); print(bool(g.get(\'quality_complete\')) and ts)"`\n'
  + '   stdout `True` → pass_fr_ids; anything else (False/None/timeout/error/missing file) → fail_fr_ids.\n\n'
  + 'HARD RULES:\n- DO NOT fix code, edit files, or run TDD steps.\n- DO NOT retry a failing FR — classify it and move on (the full loop handles it).\n- DO NOT run advance-phase / push-milestone / generate BASELINE docs.\n- DO NOT modify harness/.\n\n'
  + 'Report via the StructuredOutput tool: pass_fr_ids + fail_fr_ids (every FR in exactly one list).',
  { label: 'delta-fastpath', phase: 'Per-FR Delta', agentType: 'general-purpose', schema: DELTA_FAST_SCHEMA },
)
if (fastProbe && Array.isArray(fastProbe.pass_fr_ids)) {
  const fastPassed = fastProbe.pass_fr_ids.filter((f) => frIds.includes(f))
  for (const fr of fastPassed) {
    gate1Pass.push(fr)
    log('  ' + fr + ' GATE1-DELTA fast-path PASS [manifest qc + p5 timestamp] — full DELTA skipped')
  }
  deltaTodo = frIds.filter((f) => !fastPassed.includes(f))
} else {
  log('  delta-fastpath unavailable — falling back to full per-FR loop')
}
for (const frId of deltaTodo) {
  log('  === ' + frId + ' — GATE1-DELTA ===')
  const frReport = await agent(
    'YOU ARE THE VERIFIER for ' + frId + ' (' + (frTitle[frId] || '') + '). Re-evaluate Gate 1 for THIS ONE FR.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Steps:\n'
    + '1. `' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 5 --fr-id ' + frId + ' --step GATE1-DELTA --project ' + REPO + '`\n'
    + '   - PASS → done.\n'
    + '   - FAIL → full TDD auto-triggered by crash recovery: run-fr-step --step TDD-RED → TDD-GREEN → TDD-IMPROVE → GATE1 (each for ' + frId + '). Max 3 TDD rounds.\n'
    + '   - Still failing after 3 → report FAIL.\n'
    + '   Note: if ' + frId + '’s code has not changed since its last Gate 1 PASS, GATE1-DELTA passes immediately (advance-phase auto-skip will also honour this).\n\n'
    + 'Report final line: "' + frId + ' GATE1: PASS" or "' + frId + ' GATE1: FAIL — <reason>".\n\n'
    + 'SCOPE RULES:\n- DO NOT touch any FR OTHER than ' + frId + '.\n- DO NOT run advance-phase / push-milestone / generate BASELINE docs.\n- DO NOT modify harness/.\n- ONLY GATE1-DELTA (+ full TDD if needed) for ' + frId + '.',
    { label: 'delta-' + frId, phase: 'Per-FR Delta', agentType: 'general-purpose' },
  )
  // L1 (ported from phase3): distinguish a session/rate-limit block (null/empty
  // agent return) from a real Gate 1 FAIL — a rate-limit mid-DELTA must not be
  // misreported as a code-quality failure. DELTA auto-skip makes resume safe.
  if (frReport === null || frReport === undefined || (typeof frReport === 'string' && frReport.length < 10)) {
    log('  ' + frId + ' agent blocked (session limit / rate limit) — aborting, resume after quota reset')
    return { session_limit_blocked: true, phase: 5, fr_id: frId, gate1Pass, message: 'Agent hit session/rate limit during ' + frId + ' GATE1-DELTA. Resume after quota reset — completed FRs skip via DELTA auto-satisfy.' }
  }
  // AUTHORITATIVE Gate 1 verdict (ported from phase3, 9fe2036): read the harness
  // quality_manifest — NOT the sub-agent's self-reported "GATE1: PASS" string. A
  // sub-agent can report PASS even when finalize-gate raised GateBlockedError,
  // silently advancing a FR the harness actually blocked (2026-06-30 incident).
  const verifyCmd = PY + ' -c "import json; g=(json.load(open(\'' + REPO + '/.methodology/quality_manifest.json\')).get(\'gate_results\',{}) or {}).get(\'gate1\',{}).get(\'' + frId + '\',{}) or {}; print(\'GATE1_VERIFIED_PASS\' if g.get(\'quality_complete\') is True else \'GATE1_VERIFIED_FAIL score=\'+str(g.get(\'score\')))"'
  const verdict = await agent(
    'Run EXACTLY this command via the Bash tool:\n`' + verifyCmd + '`\n'
    + 'Then report via the StructuredOutput tool: pass = true ONLY if stdout is GATE1_VERIFIED_PASS; reason = the verbatim stdout.',
    { label: 'gate1-verify-' + frId, phase: 'Per-FR Delta', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
  )
  const passed = !!(verdict && verdict.pass === true)
  if (passed) { gate1Pass.push(frId); log('  ' + frId + ' Gate 1 PASS [harness-verified]') }
  else { gate1Fail.push(frId); log('  ' + frId + ' Gate 1 FAIL [harness manifest qc != true; sub-agent self-report ignored]') }
}
if (gate1Fail.length) {
  return { error: 'Phase 5: Gate 1 FAILED for FR(s): ' + gate1Fail.join(', ') + ' (escalate)', gate1Pass, gate1Fail }
}

// ════════════════════════════════════════════════════════════════════════
// Phase: Verification Docs (BASELINE.md + VERIFICATION_REPORT.md + re-checks)
// ════════════════════════════════════════════════════════════════════════
// BASELINE.md is a blocking audit-phase C1 deliverable (advance-phase runs the
// audit) and its depth check (C5) counts H2 sections — 7 required per
// harness/templates/BASELINE.md. VERIFICATION_REPORT.md is asserted by
// validate-handoff on the P5→P6 edge.
phase('Verification Docs')
log('Generate BASELINE.md + VERIFICATION_REPORT.md; re-run integration + security')
const docsReport = await agent(
  'YOU ARE THE P5 VERIFICATION AUTHOR. Generate the verification deliverables.\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
  + 'Steps:\n'
  + '1. BASELINE: write ' + REPO + '/05-verification/BASELINE.md (system state snapshot). Follow ' + REPO + '/harness/templates/BASELINE.md — EXACTLY 7 `## ` sections (Baseline Overview, Functional Baseline, Quality Baseline, Performance Baseline, Known Issues, Change Log, Acceptance Sign-off); audit-phase counts H2 headings and warns below 7. Fill with real data: current version, test results summary, coverage %, Gate 3 composite score, the 03-development/src/ module list; Change Log from `git -C ' + REPO + ' log --oneline -10`.\n'
  + '2. VERIFICATION_REPORT: write ' + REPO + '/05-verification/VERIFICATION_REPORT.md. For each FR in `' + ctxFile + '`: verification status, acceptance-criteria result (PASS/FAIL), evidence. Enumerate FRs with one Bash call: `' + PY + ' -c "import json,sys; d=json.load(open(\'' + ctxFile + '\')); [print(fr) for fr in d.get(\'fr_ids\',[])]"`. Include coverage %, mutation score, deferred Gate 3 issues. Certify all Gate 3 open issues addressed or deferred-with-justification. Must be NON-trivial (validate-handoff checks this). Reference 04-testing/TEST_RESULTS.md.\n'
  + '3. Re-run integration tests: `' + PY + ' -m pytest ' + REPO + '/tests/integration/ -q` (skip gracefully if dir absent).\n'
  + '4. Confirm performance NFRs: review benchmark entries in 04-testing/TEST_RESULTS.md.\n'
  + '5. Security clean: `bandit -r ' + REPO + '/03-development/src/ -ll` + `gitleaks detect --source ' + REPO + '`.\n\n'
  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if BOTH BASELINE.md (7 H2 sections) and VERIFICATION_REPORT.md were written and all re-run checks succeeded; reason = one-line summary.\n\n'
  + 'SCOPE RULES:\n- DO NOT run advance-phase / push-milestone.\n- DO NOT modify harness/.\n- DO NOT re-implement FRs (only document verification + re-run existing checks).\n- ONLY generate BASELINE.md + VERIFICATION_REPORT.md + re-run checks.',
  { label: 'verification-docs', phase: 'Verification Docs', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
)
if (!(docsReport && docsReport.pass === true)) {
  return { error: 'Phase 5 verification docs did not PASS', reason: docsReport ? String(docsReport.reason ?? '').slice(-500) : 'agent returned null' }
}

// ════════════════════════════════════════════════════════════════════════
// Phase: Artifacts Commit (commit verification artifacts BEFORE p5-baseline push)
// ════════════════════════════════════════════════════════════════════════
// Milestone push sweeps the tree via commit_and_push_gate (git add -A), but
// verify-handoff FAIL exits this workflow early with the verification
// artifacts still dirty. Commit the already-final deterministic artifacts NOW
// with an explicit path list (allowlist philosophy, mirrors phase4 d4f4724 —
// never `git add -A` here, mid-workflow trees can carry unrelated noise).
phase('Artifacts Commit')
log('Committing phase-5 artifacts (explicit paths) so a verify-handoff FAIL exit leaves a clean tree')
await agent(
  'Run ONE bash command and report its stdout/stderr:\n'
  + '`git -C ' + REPO + ' add 05-verification .methodology && git -C ' + REPO + ' commit -m "chore(p5): baseline + verification-report artifacts" || true`\n\n'
  + 'Report: the verbatim stdout/stderr of that command. "nothing to commit" is a valid outcome.\n\n'
  + 'SCOPE RULES:\n- DO NOT run any code, tests, gates, or phase transitions.\n- DO NOT stage any path other than the two listed above.\n- ONLY the git command above.',
  { label: 'artifacts-commit', phase: 'Artifacts Commit', agentType: 'general-purpose' },
)

// ════════════════════════════════════════════════════════════════════════
// Phase: Milestone (p5-baseline push)
// ════════════════════════════════════════════════════════════════════════
phase('Milestone')
log('push-milestone p5-baseline (after VERIFICATION_REPORT.md generated)')
const milestoneReport = await agent(
  'YOU ARE THE P5 MILESTONE PUSHER.\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
  + '0. GUARD: `git -C ' + REPO + ' log --oneline --grep="p5-baseline" -1`. If exists, report "MILESTONE: PASS (already pushed)" and stop.\n'
  + '1. Command: `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p5-baseline --project ' + REPO + '`\n'
  + 'Writes HANDOVER.md + commits + pushes. If a hook blocks, reword commit to start with `chore(harness):` (NOT --no-verify), retry.\n\n'
  + 'Verdict: report via the StructuredOutput tool — pass=true if the milestone commit exists or was pushed; reason = one-line detail.\n\n'
  + 'SCOPE RULES:\n- DO NOT run advance-phase.\n- ONLY push-milestone p5-baseline.',
  { label: 'milestone-baseline', phase: 'Milestone', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
)
if (!(milestoneReport && milestoneReport.pass === true)) {
  return { error: 'Phase 5 p5-baseline milestone did not PASS', reason: milestoneReport ? String(milestoneReport.reason ?? '').slice(-500) : 'agent returned null' }
}

// ════════════════════════════════════════════════════════════════════════
// Phase: Advance (D4 90% gap check + advance-phase --completed 5)
// ════════════════════════════════════════════════════════════════════════
phase('Advance')
log('D4 90% gap warning + advance-phase --completed 5 (TDD-PRECHECK enforced)')
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
    'YOU ARE THE PHASE-5 EXIT ORCHESTRATOR. Advance to Phase 6. ROUND ' + round + '.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Steps:\n'
    + '0. GUARD — already advanced? `PHASE=$(jq -r .current_phase ' + REPO + '/.methodology/state.json 2>/dev/null); echo "current_phase=$PHASE"; [ "$PHASE" -ge 6 ]`. If Phase 6 is confirmed, report "ADVANCE: PASS (already advanced)" and stop.\n'
    + '1. D4-GAP: `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 90.0`. Gate 4 (next phase) needs ≥90% but advance only needs 80% — if below 90%, ADD missing test implementations NOW to avoid a Gate 4 surprise.\n'
    + '2. advance-phase: `' + PY + ' ' + REPO + '/harness_cli.py advance-phase --completed 5 --project ' + REPO + '`\n'
    + '   advance-phase independently re-verifies EVERYTHING before it will advance — its own output tells you exactly what is missing. If it prints "[BLOCKED] ...", that message IS the fix instruction: read it verbatim and do exactly what it says, then re-run this same advance-phase command. Do NOT guess what might be wrong — trust only what advance-phase itself reports. It is safe to re-run repeatedly within this round.\n'
    + '3. Read ' + REPO + '/.methodology/state.json; confirm current_phase = 6 (advance-phase atomically writes state.json when complete).\n\n'
    + 'Report final line: "ADVANCE: PASS|FAIL — <details>". If still FAIL after exhausting this round\'s turn, report the LAST [BLOCKED] message verbatim so the next round starts from where this one left off. PHASE_6_PLAN: ' + REPO + '/.methodology/phase6_plan.md\n\n'
    + 'SCOPE RULES:\n- DO NOT re-do P5 docs.\n- DO NOT use --no-verify.\n- DO NOT modify harness/ (HR-17).\n- ONLY spec-coverage-check + advance-phase + verify HANDOVER.md + the specific fixes advance-phase\'s own output asked for.\n- Any diagnostic/debug script MUST be written under .sessi-work/tmp/ (never repo root or source dirs) and self-cleaned before you exit.',
    { label: 'advance-r' + round, phase: 'Advance', agentType: 'general-purpose' },
  )
  if (advanceReport === null || advanceReport === undefined || (typeof advanceReport === 'string' && advanceReport.length < 10)) {
    log('  Advance agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
    return { session_limit_blocked: true, phase: 5, step: 'advance', message: 'Agent hit session/rate limit during Advance. Resume after quota reset — the GUARD step skips if already advanced.' }
  }
  // AUTHORITATIVE Advance verdict: advance-phase atomically writes
  // state.json current_phase=6 on success. Read it via a schema proxy —
  // the orchestrator's prose "ADVANCE: PASS" is narrative only.
  const advVerifyCmd = PY + ' -c "import json; print(json.dumps({\'current_phase\': int(json.load(open(\'' + REPO + '/.methodology/state.json\')).get(\'current_phase\') or 0)}))"'
  const advV = await agent(
    'Run EXACTLY this command via the Bash tool (stdout is a single JSON line):\n`' + advVerifyCmd + '`\n'
    + 'Then report via the StructuredOutput tool: current_phase = the exact integer from that JSON.',
    { label: 'advance-verify-r' + round, phase: 'Advance', agentType: 'general-purpose', schema: PHASE_SCHEMA },
  )
  advancePass = !!(advV && advV.current_phase >= 6)
  if (advancePass) { log('  Advance PASS [harness-verified: state.json current_phase=' + advV.current_phase + ']'); break }
  log('  Advance not yet PASS [state.json current_phase=' + (advV ? advV.current_phase : '?') + '] — retry round ' + (round + 1))
}

if (!advancePass) {
  return { error: 'Advance did not PASS in ' + ADVANCE_MAX_ROUNDS + ' rounds — check HANDOVER.md + state.json + the last [BLOCKED] message below. If Phase 6 is confirmed, resume workflow to verify.', raw: String(advanceReport ?? '').slice(-600) }
}
log('Phase 5 workflow complete. Open .methodology/phase6_plan.md to continue.')
return {
  phase: 5,
  fr_count: frIds.length,
  gate1_pass: gate1Pass,
  advance_status: 'PASS',
  artifacts: ['05-verification/BASELINE.md', '05-verification/VERIFICATION_REPORT.md', 'HANDOVER.md'],
  notes: 'Phase 5 complete per phase5_plan.md v2.12.0. Phase 6 (Quality Assurance) ready. Reminder: Gate 4 needs spec-coverage ≥90%.',
}
