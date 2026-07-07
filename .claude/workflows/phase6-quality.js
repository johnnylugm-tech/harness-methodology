// Phase 6 — Quality Assurance (faithful to .methodology/phase6_plan.md v2.12.0)
//
// Structure: NO FR loop. Gate 4 (14 dims, tool-scored + artifact-backed DA
// challenge for Tier 3 dims) PLUS Agent B peer review of the QA deliverables
// (both required to exit). Then release notes + final sign-off + git tag + advance.
//
// Playbook lessons: NO import/fs/process, Bash CLI, SCOPE RULES,
// PY = .venv/bin/python, scriptPath launch.
// v4 (2026-07-02): gate verdicts use FLAT schema: (playbook §5.2 rev) — regex
// over LLM prose was the root cause of the #126/#134/#135/#136/ENV_CHECK_RC
// bug class. Heavy orchestrators keep prose narrative; verdicts come from
// schema proxy agents reading harness artifacts (manifest qc, state.json).
// EXCEPTION kept as prose+parser: Peer Review's complex nested verdicts JSON
// (the original v2 schema failure case — heavy-cognition agent + big schema).

export const meta = {
  name: 'phase6-quality',
  description: 'Phase 6 Quality — Gate 4 (14 dims + DA challenge) + Agent B peer review + release notes/sign-off + git tag (phase6_plan.md v2.12.0)',
  phases: [
    { title: 'Entry & Preflight' },
    { title: 'Manifest Integrity' },
    { title: 'Gate 4' },
    { title: 'Release Docs' },
    { title: 'Peer Review' },
    { title: 'Tag & Advance' },
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

// ---- writeApprovalJson: workflow-side approval writer (Class C) ----
//
// Mirror of phase1+phase2 persistApproval (v33b 6/6 advance-phase PASS).
// Used by Phase 6 Peer Review to write 4 approval JSON files
// (QUALITY_REPORT.md.json, RELEASE_NOTES.md.json, FINAL_SIGN_OFF.md.json,
// quality_manifest.json) without depending on the sub-agent's byte-level
// file write — that path produced the v33b-class "double-encoded string"
// bug where the sub-agent emitted a JSON-quoted string and the harness
// _verify_agent_b_approvals_core rejected it as `data.get("review_status")`
// on a string. Building the JSON in the workflow and routing it through the
// deterministic `harness_cli.py write-approval` CLI delegates the
// byte-accurate contract to Python, where the v22-era 6/6 PASS lives.
//
// Returns when the CLI returns "OK" with size >= 10 bytes; throws on
// MAX_OUTER_ATTEMPTS exhaustion so the caller surfaces a real error.
async function writeApprovalJson(deliverableId, obj) {
  const approvalPayload = JSON.stringify({
    fr: deliverableId,
    review_status: obj.review_status ?? 'APPROVE',
    reason: (obj.reason ?? ('Approved ' + deliverableId + ' (reason omitted)')).slice(0, 800),
    citations: Array.isArray(obj.citations) ? obj.citations.slice(0, 20) : [],
    docs_embedded: Array.isArray(obj.docs_embedded) ? obj.docs_embedded : [],
    confidence: typeof obj.confidence === 'number' ? obj.confidence : 0.9,
  })
  const cliPath = REPO + '/harness/harness_cli.py'
  const escapedPayload = approvalPayload.replace(/'/g, "'\\''")
  const cmd = PY + ' ' + cliPath + ' write-approval --project ' + REPO +
    ' --fr-id ' + JSON.stringify(deliverableId) + " --json '" + escapedPayload + "'"
  const MAX = 3
  let lastErr = null
  for (let attempt = 1; attempt <= MAX; attempt++) {
    let res
    try {
      res = await agent(
        'You are a SHELL WRAPPER AGENT. Run EXACTLY this Bash command:\n\n' + cmd + '\n\nThen report via the StructuredOutput tool: pass = true ONLY if stdout contains `[write-approval] OK`; reason = the verbatim stdout tail. No other tool calls.',
        { label: 'write-approval-' + deliverableId + '-try' + attempt, phase: 'Peer Review', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
      )
    } catch (e) {
      lastErr = 'agent() threw: ' + (e && e.message ? e.message : String(e))
      log('  writeApprovalJson ' + deliverableId + ' attempt ' + attempt + '/' + MAX + ': ' + lastErr.slice(0, 200))
      continue
    }
    if (res && res.pass === true) {
      log('  wrote approval: ' + deliverableId + ' (attempt ' + attempt + '/' + MAX + ')')
      return
    }
    lastErr = 'CLI did not return OK; got: ' + (res ? String(res.reason ?? '').slice(0, 400) : 'agent returned null')
    log('  writeApprovalJson ' + deliverableId + ' attempt ' + attempt + '/' + MAX + ': ' + lastErr)
  }
  throw new Error('writeApprovalJson FAILED for ' + deliverableId + ' after ' + MAX + ' attempts. Last error: ' + lastErr)
}
const MAX_OUTER_ATTEMPTS_PEER = 3  // peer-review dispatch retry at orchestrator level
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
const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    pass: { type: 'boolean', description: 'true only if the command output proves PASS' },
    reason: { type: 'string', description: 'verbatim command output tail (or failure reason)' },
  },
  required: ['pass', 'reason'],
}
const GATE_VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    manifest_qc: { type: 'boolean', description: 'gate_results.gate4.quality_complete is exactly true' },
    d4_rc: { type: 'integer', description: 'exit code of spec-coverage-check --threshold 90.0' },
    detail: { type: 'string' },
  },
  required: ['manifest_qc', 'd4_rc'],
}
const PHASE_SCHEMA = {
  type: 'object',
  properties: { current_phase: { type: 'integer', description: 'current_phase value read from state.json' } },
  required: ['current_phase'],
}

// ---- JSON parsing (balanced-brace; playbook §5.2) ----
// KEPT deliberately for Peer Review ONLY: its verdicts payload is a complex
// nested array on a heavy-cognition agent — the exact shape that broke schema
// compliance in v2. Flat gate verdicts use schema: (above) instead.
function balancedJsonAt(text, start) {
  if (text[start] !== '{' && text[start] !== '[') return null
  let depth = 0, inStr = false, esc = false
  for (let i = start; i < text.length; i++) {
    const c = text[i]
    if (esc) { esc = false; continue }
    if (c === '\\') { esc = true; continue }
    if (c === '"') { inStr = !inStr; continue }
    if (inStr) continue
    if (c === '{' || c === '[') depth++
    else if (c === '}' || c === ']') { depth--; if (depth === 0) return text.slice(start, i + 1) }
  }
  return null
}
function extractLastJson(text) {
  if (typeof text !== 'string') return null
  let last = null
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '{' || text[i] === '[') {
      const block = balancedJsonAt(text, i)
      if (block) { try { last = JSON.parse(block); i += block.length - 1 } catch {} }
    }
  }
  return last
}
function parseAgentJson(text, label) {
  const parsed = extractLastJson(text)
  if (parsed !== null) return parsed
  throw new Error('PARSE_FAIL [' + label + ']: no balanced JSON. tail=' + (text ?? '').toString().slice(-200))
}

// ════════════════════════════════════════════════════════════════════════
// Phase: Entry & Preflight (incl. D4-precheck 90% — Gate 4 blocks at 90% not 80%)
// ════════════════════════════════════════════════════════════════════════

phase('Entry & Preflight')
log('ENTRY-CHECK Gate3 + P5 artifacts + D4-precheck 90% + run-phase 6 + handoff + CI')
const preflightReport = await agent(
  'YOU ARE THE PHASE-6 PREFLIGHT ORCHESTRATOR. Run bash in order; report.\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
  + 'Steps:\n'
  + '1. ENTRY-CHECK: run EXACTLY this bash command to verify Gate 3 status (do NOT rely on reading the file yourself — use the command output):\n`' + PY + ' -c "import json; m=json.load(open(\'' + REPO + '/.methodology/quality_manifest.json\')); g3=(m.get(\'gate_results\',{}) or {}).get(\'gate3\',{}) or {}; print(\'GATE_VERIFIED\' if isinstance(g3,dict) and g3.get(\'quality_complete\') is True else \'GATE_MISSING\')"`\nIf GATE_MISSING → FAIL (return to Phase 4).\n'
  + '2. D4-PRECHECK: `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 90.0`. Gate 4 blocks at 90% — if below, ADD missing test implementations NOW. Do NOT proceed until this passes.\n'
  + '3. PREFLIGHT: `' + PY + ' ' + REPO + '/harness_cli.py run-phase --phase 6 --project ' + REPO + '`. FAIL → fix (reliability lint / config liveness / attestation), re-run (max 3).\n'
  + '4. HANDOFF: `' + PY + ' ' + REPO + '/harness_cli.py validate-handoff --from-phase 5 --project ' + REPO + '`. Must exit 0.\n'
  + '5. PREFLIGHT-CI: confirm `' + REPO + '/.github/workflows/harness_quality_gate.yml` (CI workflow) + `' + REPO + '/.git/hooks/prepare-commit-msg` (git hook) both exist; confirm state.json current_phase=6. If stale: `init-project --phase 6 --project ' + REPO + ' --overwrite`.\n'
  + '6. PHASE-CONTEXT (load-context): `mkdir -p ' + REPO + '/.sessi-work && ' + PY + ' ' + REPO + '/harness_cli.py load-context --phase 6 --project ' + REPO + ' --json > ' + REPO + '/.sessi-work/phase6_ctx.json`.\n\n'
  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if ALL 6 steps succeeded; reason = one-line summary (on FAIL: which step + verbatim error tail).\n\n'
  + 'SCOPE RULES:\n- DO NOT run run-gate / generate release docs / peer review.\n- DO NOT run advance-phase / git tag.\n- DO NOT modify harness/.\n- ONLY preflight commands + load-context + spec-coverage fixes.',
  { label: 'preflight', phase: 'Entry & Preflight', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
)
if (!(preflightReport && preflightReport.pass === true)) {
  return { error: 'Phase 6 preflight did not PASS', reason: preflightReport ? String(preflightReport.reason ?? '').slice(-600) : 'agent returned null (skipped or terminal API error)' }
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
// Phase: Gate 4 (run-gate → DA challenge A3 → eval 14 dims → finalize → D4 90%; HR-08)
// ════════════════════════════════════════════════════════════════════════
phase('Gate 4')
log('Gate 4 full-project eval (composite ≥85, 14 dims: 13 self-scored + traceability/architecture framework-owned)')
let gate4Pass = false, gate4Report = '', gate4Blocked = false
for (let round = 1; round <= 3; round++) {
  log('  Gate 4 round ' + round + '/3')
  // v15: wrap agent() in try/catch (Bug #2)
  try { gate4Report = await agent(
    'YOU ARE THE GATE-4 ORCHESTRATOR (Phase 6 — full project quality). ROUND ' + round + '.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Pre-Gate: confirm all FRs merged to main + no open critical/high from Gate 3.\n\n'
    + 'Steps:\n'
    + '0. TRACE-PRECHECK: `' + PY + ' ' + REPO + '/harness_cli.py build-trace-attestation --project ' + REPO + ' --write 2>&1 | tail -4`. If output contains "wrote canonical", commit immediately: `git -C ' + REPO + ' add .methodology/trace/attestation.json && git -C ' + REPO + ' commit -m "trace: regen attestation before Gate 4"`. Prevents trace_dirt from blocking finalize-gate.\n'
    + '1. G4a: `' + PY + ' ' + REPO + '/harness_cli.py run-gate --gate 4 --phase 6 --project ' + REPO + '` (CRG recon runs inside). Read the printed prompt.\n'
    + '2. A3 DA CHALLENGE (artifact-backed — finalize-gate validates this BEFORE scoring): for EACH Tier 3 dim (architecture, readability, error_handling, documentation, performance), dispatch a Claude sub-agent (you have the Agent tool) with a CHALLENGER persona that critiques the design/score, then record its critique + your defence. Dispatch each challenger SYNCHRONOUSLY — call the Agent tool and wait for its return before the next; do NOT run challengers in the background and busy-poll with `sleep`/`cat *.output` (that blows the per-agent wall-clock budget and stalls the round). Write into .sessi-work/gate4_result.json:\n'
    + '   "devil_advocate": {"architecture":true,"readability":true,"error_handling":true,"documentation":true,"performance":true},\n'
    + '   "devil_advocate_evidence": {"<dim>": {"challenger_model":"claude","challenge":"<≥120 chars actual critique>","response":"<≥120 chars defence>"}, ...}.\n'
    + '   A bare boolean is NOT accepted. If architecture/error_handling score 0 due to Orchestrator hub-and-spoke: also add "da_waiver": {"architecture": true} (requires the matching evidence artifact).\n'
    + '3. G4b: Evaluate all 14 dims inline per ' + REPO + '/harness/harness/ssi/prompts/evaluate_dimension.md → .sessi-work/gate4_result.json.\n'
    + '   Dims: linting(90) type_safety(85) test_coverage(80) security(80) secrets_scanning(100) license_compliance(100) architecture(80) readability(80) error_handling(80) documentation(75) performance(75) integration_coverage(75) test_assertion_quality(70).\n'
    + '   NOTE: mutation_testing is disabled by default via .methodology/harness_config.json (mutation_testing=false). If enabled, the harness auto-includes it and re-normalises the composite score.\n'
    + '   FRAMEWORK-OWNED (do NOT self-score): traceability + architecture (CRG override). Fix failing dims at ROOT CAUSE in code.\n'
    + '4. G4c: `' + PY + ' ' + REPO + '/harness_cli.py finalize-gate --gate 4 --phase 6 --project ' + REPO + '` (writes QUALITY_REPORT.md + HANDOVER.md + pushes on PASS).\n'
    + '   - If blocked by traceability: build-trace-attestation --write + commit, re-run finalize.\n'
    + '5. D4: `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 90.0`. FAIL → add tests, re-run.\n\n'
    + 'Report final line: "GATE4: PASS" (composite ≥85 AND all dims ≥ threshold AND DA artifacts present AND D4 ≥90%) or "GATE4: FAIL — <failing dims>".\n\n'
    + 'SCOPE RULES:\n- DO NOT generate RELEASE_NOTES/FINAL_SIGN_OFF (next phase) or run advance-phase / git tag.\n- DO NOT edit gate4_result.json scores to fake them — fix code (DA evidence is the only hand-authored part).\n- DO NOT hand-write or rewrite 06-quality/QUALITY_REPORT.md — finalize-gate is its sole author and now renders DA-waiver dimensions correctly (raw score + PASS (DA-waiver)); a hand-edited copy only creates an uncommitted second source.\n- DO NOT modify harness/ (HR-17).\n- ONLY run-gate/DA-challenge/eval/finalize/spec-coverage + code fixes.',
    { label: 'gate4-r' + round, phase: 'Gate 4', agentType: 'general-purpose' },
  ) } catch (e) {
    log('  Gate 4 agent threw: ' + String(e.message ?? e).slice(0, 80) + ' -- retrying')
    gate4Report = ''
    if (round < 3) continue
  }
  // Detect session-limit / rate-limit failures: agent returns null or empty when blocked.
  if (gate4Report === null || gate4Report === undefined || (typeof gate4Report === 'string' && gate4Report.length < 10)) {
    gate4Blocked = true
    log('  Gate 4 agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
    break
  }
  // AUTHORITATIVE Gate 4 verdict (verdict-authority rule, same as Gate 1):
  // finalize-gate writes gate_results.gate4.{score,quality_complete} to the
  // manifest as an aggregate payload. The orchestrator's prose "GATE4: PASS"
  // is narrative only — never parsed. D4 (spec-coverage ≥90%) is not in the
  // manifest, so the verify agent re-runs spec-coverage-check (exit code
  // reflects pass/fail).
  const gate4VerifyCmd = PY + ' -c "import json; g=(json.load(open(\'' + REPO + '/.methodology/quality_manifest.json\')).get(\'gate_results\',{}) or {}).get(\'gate4\') or {}; print(json.dumps({\'qc\': (isinstance(g,dict) and g.get(\'quality_complete\') is True), \'score\': (g.get(\'score\') if isinstance(g,dict) else None)}))"'
  const g4v = await agent(
    'Run these TWO commands via the Bash tool, in order:\n'
    + '1. `' + gate4VerifyCmd + '` — stdout is a single JSON line with qc + score.\n'
    + '2. `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 90.0; echo "RC=$?"`\n'
    + 'Then report via the StructuredOutput tool: manifest_qc = the exact qc boolean from command 1; d4_rc = the exact numeric exit code echoed on command 2\'s final RC= line; detail = qc/score/RC in one line.',
    { label: 'gate4-verify-r' + round, phase: 'Gate 4', agentType: 'general-purpose', schema: GATE_VERIFY_SCHEMA },
  )
  gate4Pass = !!(g4v && g4v.manifest_qc === true && g4v.d4_rc === 0)
  if (gate4Pass) { log('  Gate 4 PASS [harness-verified: manifest qc=true, D4 rc=0]'); break }
  log('  Gate 4 not yet PASS [' + (g4v ? String(g4v.detail ?? '') : 'verify agent null') + '] — retry round ' + (round + 1))
}
if (gate4Blocked) {
  return { session_limit_blocked: true, gate: 4, message: 'Agent hit session/rate limit during Gate 4 evaluation. Resume after quota reset — GUARD checks will skip completed FRs.' }
}
if (!gate4Pass) {
  return { error: 'Gate 4 did not PASS in 3 rounds (HR-08; write deferred_fixes.md + escalate to human)', raw: String(gate4Report ?? '').slice(-600) }
}

// ════════════════════════════════════════════════════════════════════════
// Phase: Release Docs (G4e RELEASE_NOTES + G4f FINAL_SIGN_OFF)
// ════════════════════════════════════════════════════════════════════════
phase('Release Docs')
log('Generate RELEASE_NOTES.md + FINAL_SIGN_OFF.md (reference Gate 4 score + provenance)')
const releaseReport = await agent(
  'YOU ARE THE P6 RELEASE AUTHOR. Generate the release deliverables (after Gate 4 PASS).\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
  + 'Steps:\n'
  + '1. G4e RELEASE_NOTES: write ' + REPO + '/RELEASE_NOTES.md (project root). Summarise changes since Gate 3. Include: version, date, FR list, Gate 4 composite score (read from .methodology/quality_manifest.json — persistent SoT, per phase6_plan.md v2.12.0), known limitations. Reference 06-quality/QUALITY_REPORT.md (auto-generated by G4c).\n'
  + '2. G4f FINAL_SIGN_OFF: write ' + REPO + '/FINAL_SIGN_OFF.md (project root). Include: project name, completion date, Gate 4 composite score, sign-off statement. MUST reference 05-verification/VERIFICATION_REPORT.md (verification provenance) and 05-verification/BASELINE.md (P5 system baseline).\n\n'
  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if both docs were written with the required references; reason = one-line summary.\n\n'
  + 'SCOPE RULES:\n- DO NOT run advance-phase / git tag / peer review dispatch.\n- DO NOT modify harness/.\n- DO NOT re-run Gate 4.\n- ONLY generate RELEASE_NOTES.md + FINAL_SIGN_OFF.md.',
  { label: 'release-docs', phase: 'Release Docs', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
)
if (!(releaseReport && releaseReport.pass === true)) {
  return { error: 'Phase 6 release docs did not PASS', reason: releaseReport ? String(releaseReport.reason ?? '').slice(-500) : 'agent returned null' }
}

// ════════════════════════════════════════════════════════════════════════
// Phase: Peer Review (G4g — Agent B reviews; workflow writes 4 approval JSON)
// ════════════════════════════════════════════════════════════════════════
phase('Peer Review')
log('Agent B reviews 4 deliverables; workflow writes 4 approval JSON via writeApprovalJson (Class C)')

// v22-era 4 deliverables advanced-phase expects (harness_cli.py:_PHASE_DELIVERABLES[6]).
const peerDeliverables = ['QUALITY_REPORT.md', 'RELEASE_NOTES.md', 'FINAL_SIGN_OFF.md', 'quality_manifest']

let peerVerdict = null
for (let attempt = 1; attempt <= MAX_OUTER_ATTEMPTS_PEER; attempt++) {
  const peerReport = await agent(
    'YOU ARE AGENT B (TECH_LEAD reviewer) for the Phase 6 Gate 4 deliverables (HR-01).\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Steps:\n'
    + '1. Review 06-quality/QUALITY_REPORT.md, RELEASE_NOTES.md, FINAL_SIGN_OFF.md (read them via Bash cat for exact content).\n'
    + '2. Cross-check .methodology/quality_manifest.json Gate 4 scoring logic. Reference 05-verification/VERIFICATION_REPORT.md and 05-verification/BASELINE.md for historical traceability.\n'
    + '3. If any deliverable warrants REJECT or has medium/high gaps: fix the deliverable (or escalate), then re-review.\n\n'
    + 'Output ONLY a single JSON object (no other text, no markdown fences) in your final message:\n'
    + '{"verdicts": [\n'
    + '  {"deliverable":"QUALITY_REPORT.md","review_status":"APPROVE","reason":"<concise>","citations":["file:line"],"docs_embedded":["QUALITY_REPORT.md","RELEASE_NOTES.md","FINAL_SIGN_OFF.md","VERIFICATION_REPORT.md"],"gaps":[]},\n'
    + '  {"deliverable":"RELEASE_NOTES.md","review_status":"APPROVE","reason":"<concise>","citations":["file:line"],"docs_embedded":["QUALITY_REPORT.md","RELEASE_NOTES.md","FINAL_SIGN_OFF.md","VERIFICATION_REPORT.md"],"gaps":[]},\n'
    + '  {"deliverable":"FINAL_SIGN_OFF.md","review_status":"APPROVE","reason":"<concise>","citations":["file:line"],"docs_embedded":["QUALITY_REPORT.md","RELEASE_NOTES.md","FINAL_SIGN_OFF.md","VERIFICATION_REPORT.md"],"gaps":[]},\n'
    + '  {"deliverable":"quality_manifest","review_status":"APPROVE","reason":"<concise>","citations":["file:line"],"docs_embedded":["QUALITY_REPORT.md","RELEASE_NOTES.md","FINAL_SIGN_OFF.md","VERIFICATION_REPORT.md"],"gaps":[]}\n'
    + ']}\n'
    + 'CRITICAL: "docs_embedded" must list ALL 4 required embedded docs (QUALITY_REPORT.md, RELEASE_NOTES.md, FINAL_SIGN_OFF.md, VERIFICATION_REPORT.md) — NOT just the deliverable being reviewed. The harness _verify_agent_b_approvals_core checks every verdict includes every required doc (Bug v26 basename-match contract).\n'
    + 'Each "reason" must be ≥100 chars of substantive justification (not "APPROVE" or one-word). Each "gaps" array is empty when review_status is APPROVE. Each "citations" must include ≥1 file:line you actually cat-ed.\n\n'
    + 'SCOPE RULES:\n- DO NOT run advance-phase / git tag / run-gate.\n- DO NOT modify harness/ (HR-17).\n- DO NOT write any files (workflow writes approval JSON; you only review content).',
    { label: 'peer-review-r' + attempt, phase: 'Peer Review', agentType: 'general-purpose' },
  )
  // parseAgentJson lives at top of file (same pattern as phase1+phase2)
  try {
    const parsed = parseAgentJson(peerReport, 'PeerB-r' + attempt)
    if (!parsed || !Array.isArray(parsed.verdicts) || parsed.verdicts.length !== peerDeliverables.length) {
      throw new Error('verdicts[] missing or wrong length (expected ' + peerDeliverables.length + ')')
    }
    // Sanity: each verdict must be for one of our 4 deliverables
    for (const v of parsed.verdicts) {
      if (!peerDeliverables.includes(v.deliverable)) {
        throw new Error('unknown deliverable in verdict: ' + v.deliverable)
      }
      if (!v.reason || String(v.reason).trim().length < 100) {
        throw new Error('verdict for ' + v.deliverable + ' has reason < 100 chars')
      }
    }
    peerVerdict = parsed
    log('  peer review verdict parsed (round ' + attempt + '/' + MAX_OUTER_ATTEMPTS_PEER + ')')
    break
  } catch (e) {
    log('  Peer B parse failed: ' + String(e.message ?? e).slice(0, 120) + ' — retrying')
    if (attempt === MAX_OUTER_ATTEMPTS_PEER) {
      return { error: 'Peer B parse failed after ' + MAX_OUTER_ATTEMPTS_PEER + ' rounds', detail: String(e.message ?? e).slice(0, 400) }
    }
  }
}
if (!peerVerdict) {
  return { error: 'Peer B did not produce valid verdict' }
}

// Workflow writes 4 approval JSON files via writeApprovalJson (Class C).
// This avoids the v33b-class double-encode bug where a sub-agent emitting a
// JSON string-of-string was accepted by `size >= 10 bytes` verify but later
// failed at advance-phase _verify_agent_b_approvals_core (data.get on str).
for (const v of peerVerdict.verdicts) {
  await writeApprovalJson(v.deliverable, v)
}

// ════════════════════════════════════════════════════════════════════════
// Phase: Tag & Advance (git tag harness-v4 + advance-phase --completed 6)
// ════════════════════════════════════════════════════════════════════════
phase('Tag & Advance')
log('git tag (Gate 4 score) + advance-phase --completed 6')
// Round loop (2026-07-02 audit finding, ported from phase3): advance-phase
// enforces more independent checks than any single prompt can safely
// enumerate, and a static checklist goes stale the moment harness adds or
// changes one. advance-phase is idempotent (preflight runs before any
// FSM/state write), so the robust fix is an outer retry loop where the
// agent reads advance-phase's own [BLOCKED] output each round instead of
// guessing in advance. The git-tag step is separately GUARDed (step 0
// checks for an existing tag), so it stays safe to repeat across rounds.
let advancePass = false, advanceReport = ''
const ADVANCE_MAX_ROUNDS = 5
for (let round = 1; round <= ADVANCE_MAX_ROUNDS; round++) {
  log('  Tag & Advance round ' + round + '/' + ADVANCE_MAX_ROUNDS)
  // Last-line integrity guard: the phase-exit push commits .methodology/
  // wholesale — block here so mid-run corruption never reaches git history
  // (2026-07-02: commit 3198402 baked a corrupted manifest into main).
  // Re-check every round — a fix attempt in a prior round could reintroduce it.
  const advIntegrity = await checkManifestIntegrity('Tag & Advance', 'advance-integrity-r' + round)
  if (!advIntegrity.ok) {
    return { error: 'Tag & Advance round ' + round + ': quality_manifest.json corrupted — refusing to commit it', detail: advIntegrity.raw, recovery: 'git checkout HEAD -- .methodology/quality_manifest.json (verify HEAD is healthy first), merge the latest gate result back into gate_results, then resume', note: 'Blocking prevents the corruption from being committed by the phase-exit push.' }
  }
  advanceReport = await agent(
    'YOU ARE THE PHASE-6 EXIT ORCHESTRATOR. Tag the Gate 4 release + advance to Phase 7. ROUND ' + round + '.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Steps:\n'
    + '0. GUARD — already advanced? `PHASE=$(jq -r .current_phase ' + REPO + '/.methodology/state.json 2>/dev/null); echo "current_phase=$PHASE"; [ "$PHASE" -ge 7 ]`. Also check: `git -C ' + REPO + ' tag -l "harness-v4-*" | head -1`. If Phase 7 is confirmed OR tag already exists, report "ADVANCE: PASS (already advanced)" and stop.\n'
    + '1. GIT-TAG (skip if step 0 found an existing tag): read composite_score from .methodology/quality_manifest.json (persistent SoT per phase6_plan.md v2.12.0; gate4_result.json is ephemeral), then:\n'
    + '   `git -C ' + REPO + ' tag -a "harness-v4-$(date +%Y%m%d)-score<SCORE>" -m "Gate 4 PASS (score <SCORE>)" && git -C ' + REPO + ' push origin --tags`\n'
    + '2. advance-phase: `' + PY + ' ' + REPO + '/harness_cli.py advance-phase --completed 6 --project ' + REPO + '`\n'
    + '   advance-phase independently re-verifies EVERYTHING before it will advance — its own output tells you exactly what is missing. If it prints "[BLOCKED] ...", that message IS the fix instruction: read it verbatim and do exactly what it says, then re-run this same advance-phase command. Do NOT guess what might be wrong — trust only what advance-phase itself reports. It is safe to re-run repeatedly within this round.\n'
    + '3. Read ' + REPO + '/.methodology/state.json; confirm current_phase = 7 (advance-phase atomically writes state.json when complete).\n\n'
    + 'Report final line: "ADVANCE: PASS|FAIL — <details>". If still FAIL after exhausting this round\'s turn, report the LAST [BLOCKED] message verbatim so the next round starts from where this one left off. PHASE_7_PLAN: ' + REPO + '/.methodology/phase7_plan.md\n\n'
    + 'SCOPE RULES:\n- DO NOT re-do Gate 4 / release docs.\n- DO NOT use --no-verify.\n- DO NOT modify harness/ (HR-17).\n- ONLY git tag + advance-phase + verify HANDOVER.md + the specific fixes advance-phase\'s own output asked for.\n- Any diagnostic/debug script MUST be written under .sessi-work/tmp/ (never repo root or source dirs) and self-cleaned before you exit.',
    { label: 'tag-advance-r' + round, phase: 'Tag & Advance', agentType: 'general-purpose' },
  )
  if (advanceReport === null || advanceReport === undefined || (typeof advanceReport === 'string' && advanceReport.length < 10)) {
    log('  Tag & Advance agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
    return { session_limit_blocked: true, phase: 6, step: 'tag-advance', message: 'Agent hit session/rate limit during Tag & Advance. Resume after quota reset — the GUARD step skips if already advanced/tagged.' }
  }
  // AUTHORITATIVE Advance verdict: advance-phase atomically writes
  // state.json current_phase=7 on success. Read it via a schema proxy —
  // the orchestrator's prose "ADVANCE: PASS" is narrative only.
  const advVerifyCmd = PY + ' -c "import json; print(json.dumps({\'current_phase\': int(json.load(open(\'' + REPO + '/.methodology/state.json\')).get(\'current_phase\') or 0)}))"'
  const advV = await agent(
    'Run EXACTLY this command via the Bash tool (stdout is a single JSON line):\n`' + advVerifyCmd + '`\n'
    + 'Then report via the StructuredOutput tool: current_phase = the exact integer from that JSON.',
    { label: 'advance-verify-r' + round, phase: 'Tag & Advance', agentType: 'general-purpose', schema: PHASE_SCHEMA },
  )
  advancePass = !!(advV && advV.current_phase >= 7)
  if (advancePass) { log('  Tag & Advance PASS [harness-verified: state.json current_phase=' + advV.current_phase + ']'); break }
  log('  Tag & Advance not yet PASS [state.json current_phase=' + (advV ? advV.current_phase : '?') + '] — retry round ' + (round + 1))
}

if (!advancePass) {
  return { error: 'Tag & Advance did not PASS in ' + ADVANCE_MAX_ROUNDS + ' rounds — check HANDOVER.md + state.json + the last [BLOCKED] message below. If Phase 7 is confirmed, resume workflow to verify.', raw: String(advanceReport ?? '').slice(-600) }
}
log('Phase 6 workflow complete. Open .methodology/phase7_plan.md to continue.')
return {
  phase: 6,
  gate4_status: gate4Pass ? 'PASS' : 'unknown',
  // Pre-existing latent bug fixed 2026-07-02: this line referenced `peerReport`,
  // a for-block const out of scope here — the final return would have thrown
  // ReferenceError after everything passed. peerVerdict is the in-scope truth.
  peer_review_status: (peerVerdict && Array.isArray(peerVerdict.verdicts) && peerVerdict.verdicts.every(v => v.review_status === 'APPROVE')) ? 'APPROVE' : 'unknown',
  advance_status: 'PASS',
  artifacts: ['06-quality/QUALITY_REPORT.md', 'RELEASE_NOTES.md', 'FINAL_SIGN_OFF.md', '.methodology/agent_b_approvals/', '.sessi-work/gate4_result.json', '.methodology/quality_manifest.json', 'HANDOVER.md'],
  notes: 'Phase 6 complete per phase6_plan.md v2.12.0. Gate 4 PASS + Agent B peer review APPROVE. Phase 7 (Risk Management) ready.',
}
