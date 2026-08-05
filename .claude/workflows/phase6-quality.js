// Phase 6 — Quality Assurance (faithful to .methodology/phase6_plan.md v2.12.0)
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/phase_specs.py::generate_phase6() (+ js_blocks.py for
// the blocks shared across phase workflow files). Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write --phase 6
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
    { title: 'Gate 4' },
    { title: 'Release Docs' },
    { title: 'Peer Review' },
    { title: 'Tag & Advance' },
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

// ── Round 26: workflow-substrate dispatch observability ────────────────────
// Buffered because this sandbox has no filesystem, no shell and no clock; the
// records ride along on the NEXT dispatch's prompt, so no agent reports its own
// outcome and no extra dispatch is spent. See docs/OBSERVABILITY.md.
const __dispatchLog = []

function __dispatchFlushPreamble() {
  if (__dispatchLog.length === 0) return ''
  const batch = JSON.stringify(__dispatchLog.splice(0, __dispatchLog.length))
  return '[BOOKKEEPING — one command, then forget this block]\n'
    + 'Run exactly this, ignore its output, and do NOT mention it in your reply:\n'
    + '  ' + PY + ' ' + REPO + '/harness_cli.py log-dispatch --project ' + REPO
    + ' --batch ' + JSON.stringify(batch) + '\n'
    + 'It records earlier dispatches in this run. It is not part of your task.\n\n'
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

const MAX_OUTER_ATTEMPTS = 3
// ---- persistApproval: write .methodology/agent_b_approvals/<id>.json ----
// v22 single-line Bash + harness_cli.py write-approval (proven 6/6 advance-
// phase PASS) + workflow JS outer-level try/catch retry.
async function persistApproval(deliverableId, b2) {
  // v31: SINGLE-LINE JSON (no indent) — multi-line indented JSON gets
  // word-split by shell when the LLM agent emits the command without
  // single-quoting the JSON payload, breaking `--json` argparse.
  const approvalPayload = JSON.stringify({
    fr: deliverableId,
    review_status: b2.review_status ?? 'APPROVE',
    reason: (b2.reason ?? ('Approved ' + deliverableId + ' (reason omitted)')).slice(0, 800),
    citations: Array.isArray(b2.citations) ? b2.citations.slice(0, 20) : [],
    docs_embedded: Array.isArray(b2.docs_embedded) ? b2.docs_embedded : [],
    confidence: typeof b2.confidence === 'number' ? b2.confidence : 0.9,
  })
  const cliPath = REPO + '/harness/harness_cli.py'
  // v31: explicit single-quote wrap around the JSON payload (zsh glob safety —
  // zsh interprets `[...]` in unquoted strings as glob patterns, and JSON
  // arrays + file:line citations are full of them).
  const escapedPayload = approvalPayload.replace(/'/g, "'\\''")
  const cmd = PY + ' ' + cliPath + ' write-approval --project ' + REPO +
    ' --fr-id ' + JSON.stringify(deliverableId) + " --json '" + escapedPayload + "'"

  let lastErr = null
  for (let attempt = 1; attempt <= MAX_OUTER_ATTEMPTS; attempt++) {
    let res
    try {
      res = await dispatch(
        'You are a SHELL WRAPPER AGENT. Run EXACTLY this Bash command:\n\n' + cmd + '\n\nThen report via the StructuredOutput tool: pass = true ONLY if stdout contains `[write-approval] OK`; reason = the verbatim stdout tail. No other tool calls.',
        { label: 'write-approval-' + deliverableId + '-try' + attempt, phase: 'Peer Review', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
      )
    } catch (e) {
      lastErr = 'agent() threw: ' + (e && e.message ? e.message : String(e))
      log('  persistApproval ' + deliverableId + ' attempt ' + attempt + '/' + MAX_OUTER_ATTEMPTS + ': ' + lastErr.slice(0, 200))
      continue
    }
    if (res && /\[write-approval\]\s*OK/.test(String(res.reason || ''))) {
      log('  persisted approval: ' + deliverableId + ' (attempt ' + attempt + '/' + MAX_OUTER_ATTEMPTS + ')')
      return
    }
    lastErr = 'CLI did not return OK; got: ' + (res ? String(res.reason ?? '').slice(0, 400) : 'agent returned null')
    log('  persistApproval ' + deliverableId + ' attempt ' + attempt + '/' + MAX_OUTER_ATTEMPTS + ': ' + lastErr)
  }
  throw new Error('persistApproval FAILED for ' + deliverableId + ' after ' + MAX_OUTER_ATTEMPTS + ' attempts. Last error: ' + lastErr)
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


// ══════════════════════════════════════════════════════════════════════════
// Phase: Entry & Preflight
// ══════════════════════════════════════════════════════════════════════════

phase('Entry & Preflight')
log('ENTRY-CHECK Gate3 + P5 artifacts + D4-precheck 90% + run-phase 6 + handoff + CI')
const preflightReport = await dispatch(
  'YOU ARE THE PHASE-6 PREFLIGHT ORCHESTRATOR. Run bash in order; report.\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
  + 'Steps:\n'
  + '1. ENTRY-CHECK: run EXACTLY this bash command to verify Gate 3 status (do NOT rely on reading the file yourself — use the command output):\n`' + PY + ' -c "import json; m=json.load(open(\'' + REPO + '/.methodology/quality_manifest.json\')); g3=(m.get(\'gate_results\',{}) or {}).get(\'gate3\',{}) or {}; print(\'GATE_VERIFIED\' if isinstance(g3,dict) and g3.get(\'quality_complete\') is True else \'GATE_MISSING\')"`\nIf GATE_MISSING → FAIL (return to Phase 4).\n'
  + '2. D4-PRECHECK: `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 90.0`. Gate 4 blocks at 90% — if below, ADD missing test implementations NOW. Do NOT proceed until this passes.\n'
  + '3. CRG-PRECHECK: `BASELINE=""; [ -f ' + REPO + '/.methodology/crg_baseline_p4.json ] && BASELINE="--baseline ' + REPO + '/.methodology/crg_baseline_p4.json"; ' + PY + ' ' + REPO + '/harness_cli.py crg-arch-check --project ' + REPO + ' $BASELINE`. CI enforces this as an absolute floor independent of the Gate 4 composite — if it FAILs, FIX the underlying architecture issue NOW (the command prints the floor it applied). Do NOT proceed until this passes.\n'
  + '4. PREFLIGHT: `' + PY + ' ' + REPO + '/harness_cli.py run-phase --phase 6 --project ' + REPO + '`. FAIL → fix, re-run (max 3). Also fix if reported: reliability lint (subprocess timeout / mkstemp / TOCTOU / sleep-in-async), config liveness (env keys absent from .env.example), attestation missing/mismatch (build-trace-attestation --write + commit; re-run until "Attestation: clean").\n'
  + '5. HANDOFF: `' + PY + ' ' + REPO + '/harness_cli.py validate-handoff --from-phase 5 --project ' + REPO + '`. Must exit 0.\n'
  + '6. PREFLIGHT-CI: confirm `' + REPO + '/.github/workflows/harness_quality_gate.yml` (CI workflow) + `' + REPO + '/.git/hooks/prepare-commit-msg` (git hook) both exist; confirm state.json current_phase=6. If stale: `init-project --phase 6 --project ' + REPO + ' --overwrite`.\n'
  + '7. PHASE-CONTEXT (load-context): `mkdir -p ' + REPO + '/.sessi-work && ' + PY + ' ' + REPO + '/harness_cli.py load-context --phase 6 --project ' + REPO + ' --json > ' + REPO + '/.sessi-work/phase6_ctx.json`.\n\n'
  + '8. READ THE LESSONS BLOCK (advisory, not a gate): Bash `cat ' + REPO + '/.sessi-work/phase6_ctx.json` and READ the `lessons` field (compact markdown, "" if none). DO NOT repeat those past failure modes in this preflight or any follow-up P6 work. (Direction C — past lessons injection)\n\n'
  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if ALL 7 must-succeed steps succeeded; step 8 is read-only advisory. reason = one-line summary (on FAIL: which step + verbatim error tail).\n\n'
  + 'SCOPE RULES:\n- DO NOT run run-gate / generate release docs / peer review.\n- DO NOT run advance-phase / git tag.\n- DO NOT modify harness/.\n- ONLY preflight commands + load-context + spec-coverage/crg-arch-check fixes.',
  { label: 'preflight', phase: 'Entry & Preflight', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
)
if (!(preflightReport && preflightReport.pass === true)) {
  return { error: 'Phase 6 preflight did not PASS', reason: preflightReport ? String(preflightReport.reason ?? '').slice(-600) : 'agent returned null (skipped or terminal API error)' }
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Gate 4
// ══════════════════════════════════════════════════════════════════════════

phase('Gate 4')
log('Gate 4 full-project eval (composite ≥85, 14 dims: 12 self-scored + traceability + architecture framework-owned; mutation_testing disabled by default)')
let gate4Pass = false, gate4Report = '', gate4Blocked = false
// Gate 4 pre-flight GUARD: only state.json.last_gate >= 4 proves this gate was
// truly finalized (SSI dims passed AND Phase Truth passed) — see harness_cli.py finalize-gate.
{
  const _precheckCmd = `${PY} -c "import json; lg=json.load(open('${REPO}/.methodology/state.json')).get('last_gate'); print(json.dumps({'qc': isinstance(lg,int) and lg >= 4, 'last_gate': lg}))"`
  try {
    const _preVerdict = await dispatch(
      'Run EXACTLY this command via the Bash tool:\n`' + _precheckCmd + '; echo RC=$?`\n'
      + 'Then report via the StructuredOutput tool: pass = true ONLY if the output line starts with `{"qc": true`; reason = the verbatim JSON line (excluding the RC= line).',
      { label: 'gate4-precheck', phase: 'Gate 4', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
    )
    if (_preVerdict && _preVerdict.pass === true) {
      gate4Pass = true
      log('  Gate 4 PRE-FLIGHT PASS — state.json last_gate >= 4 (gate truly finalized); skipping round loop')
    } else {
      log('  Gate 4 pre-flight: not yet finalized — proceeding to round loop')
    }
  } catch (e) {
    log('  Gate 4 pre-flight threw: ' + String(e.message ?? e).slice(0, 120) + ' — proceeding to round loop')
  }
}
if (!gate4Pass) for (let round = 1; round <= 3; round++) {
  log('  Gate 4 round ' + round + '/3')
  // v15: wrap agent() in try/catch (Bug #2)
  try { gate4Report = await dispatch(
    'YOU ARE THE GATE-4 ORCHESTRATOR (Phase 6 — full project quality). ROUND ' + round + '.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Pre-Gate: confirm all FRs merged to main + no open critical/high from Gate 3.\n\n'
    + 'Steps:\n'
    + '1. G4a: `' + PY + ' ' + REPO + '/harness_cli.py run-gate --gate 4 --phase 6 --project ' + REPO + '` (CRG recon runs inside). Read the printed prompt.\n'
    + '2. A3 DA CHALLENGE (artifact-backed — finalize-gate validates this BEFORE scoring): for EACH Tier 3 dim (architecture, readability, error_handling, documentation, performance), dispatch a Claude sub-agent (you have the Agent tool) with a CHALLENGER persona that critiques the design/score, then record its critique + your defence. Dispatch each challenger SYNCHRONOUSLY — call the Agent tool and wait for its return before the next; do NOT run challengers in the background and busy-poll with `sleep`/`cat *.output` (that blows the per-agent wall-clock budget and stalls the round). Write into .sessi-work/gate4_result.json:\n   "devil_advocate": {"architecture":true,"readability":true,"error_handling":true,"documentation":true,"performance":true},\n   "devil_advocate_evidence": {"<dim>": {"challenger_model":"claude","challenge":"<≥120 chars actual critique>","response":"<≥120 chars defence>"}, ...}.\n   A bare boolean is NOT accepted. If architecture/error_handling score 0 due to Orchestrator hub-and-spoke: also add "da_waiver": {"architecture": true} (requires the matching evidence artifact).\n'
    + '3. G4b: Evaluate all 14 dims inline per ' + REPO + '/harness/harness/ssi/prompts/evaluate_dimension.md → .sessi-work/gate4_result.json.\n   Dims: linting(90) type_safety(85) test_coverage(80) security(80) secrets_scanning(100) license_compliance(100) architecture(80) readability(80) error_handling(80) documentation(75) performance(75) integration_coverage(75) test_assertion_quality(70).\n   NOTE: mutation_testing is enabled by default via .methodology/harness_config.json (mutation_testing=true). To disable, set it false in harness_config.json — the harness then excludes it from the dim list and re-normalises the composite score.\n   FRAMEWORK-OWNED (do NOT self-score): traceability + architecture (CRG override). Fix failing dims at ROOT CAUSE in code.\n   CITATION REQUIRED: any tool_evidence sentence that names a specific NFR/FR as the CAUSE of a skip or failure (e.g. "N skipped for feature-flagged NFR-08") must be verified per-skip against the actual docstring/name tag of that test before being written — do NOT attribute a whole skip count to one NFR without checking each skipped test individually; a wrong blanket attribution is a fabrication, not a summary.\n'
    + '4. D4: `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 90.0`. FAIL → add tests, re-run. Runs BEFORE G4c so any fix here is captured by the G4c commit (Round 26: a D4 fix landing AFTER finalize-gate committed had no downstream commit step and was left uncommitted).\n'
    + '5. CRG-ARCH: `BASELINE=""; [ -f ' + REPO + '/.methodology/crg_baseline_p4.json ] && BASELINE="--baseline ' + REPO + '/.methodology/crg_baseline_p4.json"; ' + PY + ' ' + REPO + '/harness_cli.py crg-arch-check --project ' + REPO + ' $BASELINE`. CI enforces this as an absolute floor on every push, independent of the Gate 4 composite score. FAIL → the crg-arch-check output lists the low-cohesion communities / oversized functions; fix the underlying architecture issue, re-run. Also runs BEFORE G4c so any fix lands in the G4c commit.\n'
    + '6. G4c: `' + PY + ' ' + REPO + '/harness_cli.py finalize-gate --gate 4 --phase 6 --project ' + REPO + '` (writes QUALITY_REPORT.md + HANDOVER.md + pushes on PASS; also the commit point for any code/test fixes from steps 3-5 above).\n\n'
    + 'Report final line: "GATE4: PASS" (composite ≥85 AND all dims ≥ threshold AND DA artifacts present AND D4 ≥90% AND CRG architecture ≥80) or "GATE4: FAIL — <failing dims>".\n\n'
    + 'SCOPE RULES:\n- DO NOT generate RELEASE_NOTES/FINAL_SIGN_OFF (next phase) or run advance-phase / git tag.\n- DO NOT edit gate4_result.json scores to fake them — fix code (DA evidence is the only hand-authored part).\n- DO NOT hand-write or rewrite 06-quality/QUALITY_REPORT.md — finalize-gate is its sole author and now renders DA-waiver dimensions correctly (raw score + PASS (DA-waiver)); a hand-edited copy only creates an uncommitted second source.\n- DO NOT run scripts/build_traceability.py directly against the project root, and DO NOT hand-author TRACEABILITY_MATRIX.overlay.yaml overrides — the canonical matrix is 01-requirements/TRACEABILITY_MATRIX.md, auto-refreshed by advance-phase; a root-level copy or a hand-written overlay only creates an untracked duplicate with no effect on this gate.\n- DO NOT modify harness/ (HR-17).\n- ONLY run-gate/DA-challenge/eval/finalize/spec-coverage/crg-arch-check + code fixes.',
    { label: 'gate4-r' + round, phase: 'Gate 4', agentType: 'general-purpose' },
  ) } catch (e) {
    log('  Gate 4 agent threw: ' + String(e.message ?? e).slice(0, 80) + ' -- retrying')
    gate4Report = ''
    if (round < 3) continue
  }
  if (gate4Report === null || gate4Report === undefined || (typeof gate4Report === 'string' && gate4Report.length < 10)) {
    gate4Blocked = true
    log('  Gate 4 agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
    break
  }
  const g4v = await dispatch(
    'Run this ONE command via the Bash tool:\n'
    + '`pip install -q code-review-graph==2.3.6 igraph==1.0.0 >/dev/null 2>&1; ' + PY + ' ' + REPO + '/harness_cli.py verify-gate --project ' + REPO + ' --gate 4 --phase 6 --spec-threshold 90.0; echo "RC=$?"`\n'
    + 'It runs all three of Gate 4\'s checks — state.json last_gate >= 4, spec-coverage, and the CRG architecture floor — and appends the verdict, with a digest of the tree it measured, to .methodology/gate_verify.jsonl. advance-phase re-derives that digest and refuses a phase whose exit gate has no matching PASS, so a verdict you did not actually produce cannot carry the phase.\n'
    + 'Then report via the StructuredOutput tool: verify_rc = the exact numeric exit code echoed on the final RC= line; detail = the command\'s last [verify-gate] line.',
    { label: 'gate4-verify-r' + round, phase: 'Gate 4', agentType: 'general-purpose', schema: GATE_VERIFY_SCHEMA },
  )
  gate4Pass = !!(g4v && g4v.verify_rc === 0)
  if (gate4Pass) { log('  Gate 4 PASS [harness-verified: verify-gate rc=0, verdict recorded in gate_verify.jsonl]'); break }
  log('  Gate 4 not yet PASS [' + (g4v ? String(g4v.detail ?? '') : 'verify agent null') + '] — retry round ' + (round + 1))
}
if (gate4Blocked) {
  return { session_limit_blocked: true, gate: 4, message: 'Agent hit session/rate limit during Gate 4 evaluation. Resume after quota reset — GUARD checks will skip completed FRs.' }
}
if (!gate4Pass) {
  return { error: 'Gate 4 did not PASS in 3 rounds (HR-08; write deferred_fixes.md + escalate to human)', raw: String(gate4Report ?? '').slice(-600) }
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Release Docs
// ══════════════════════════════════════════════════════════════════════════

phase('Release Docs')
log('Generate RELEASE_NOTES.md + FINAL_SIGN_OFF.md (reference Gate 4 score + provenance)')
const releaseReport = await dispatch(
  'YOU ARE THE P6 RELEASE AUTHOR. Generate the release deliverables (after Gate 4 PASS).\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
  + 'Steps:\n'
  + '1. G4e RELEASE_NOTES: write ' + REPO + '/RELEASE_NOTES.md (project root). Summarise changes since the prior release. Include: version, date, FR list, Gate 4 composite score (read from .methodology/quality_manifest.json — persistent SoT, per phase6_plan.md v2.12.0), known limitations. Reference 06-quality/QUALITY_REPORT.md (auto-generated by G4c).\n'
  + '2. G4f FINAL_SIGN_OFF: write ' + REPO + '/FINAL_SIGN_OFF.md (project root). Include: project name, completion date, Gate 4 composite score, sign-off statement. MUST reference 05-verification/VERIFICATION_REPORT.md (verification provenance) and 05-verification/BASELINE.md (P5 system baseline).\n\n'
  + 'CITATION REQUIRED: any commit you cite by hash/label (e.g. "Gate 3 release") must be verified against the actual `git log --format=%H %h %s` subject line for that hash BEFORE writing it — do NOT infer or assume which Gate a commit belongs to from its position in history. Any claim about mutation-testing scores or coverage MUST point to a real artifact you actually read (e.g. a specific gate_results/*.json field, or `.mutmut-cache`) — if no such artifact exists, say so explicitly (e.g. "not run") instead of writing an unverifiable claim.\n\n'
  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if both docs were written with the required references; reason = one-line summary.\n\n'
  + 'SCOPE RULES:\n- DO NOT run advance-phase / git tag / peer review dispatch.\n- DO NOT modify harness/.\n- DO NOT re-run Gate 4.\n- ONLY generate RELEASE_NOTES.md + FINAL_SIGN_OFF.md.',
  { label: 'release-docs', phase: 'Release Docs', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
)
if (!(releaseReport && releaseReport.pass === true)) {
  return { error: 'Phase 6 release docs did not PASS', reason: releaseReport ? String(releaseReport.reason ?? '').slice(-500) : 'agent returned null' }
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Peer Review
// ══════════════════════════════════════════════════════════════════════════

phase('Peer Review')
log('Agent B reviews 4 deliverables; workflow writes 4 approval JSON via persistApproval (Class C)')

// v22-era 4 deliverables advanced-phase expects (harness_cli.py:_PHASE_DELIVERABLES[6]).
const peerDeliverables = ['QUALITY_REPORT.md', 'RELEASE_NOTES.md', 'FINAL_SIGN_OFF.md', 'quality_manifest']

let peerVerdict = null
for (let attempt = 1; attempt <= MAX_OUTER_ATTEMPTS_PEER; attempt++) {
  const peerReport = await dispatch(
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
      if (!Array.isArray(v.citations) || v.citations.length < 1) {
        throw new Error('verdict for ' + v.deliverable + ' has empty citations[] — agent_b_approvals.py hard-blocks this at advance-phase')
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

// T1-B: check whether ALL verdicts are APPROVE (no REJECT, no medium/high gaps).
// Previously the workflow wrote all 4 approvals unconditionally regardless of
// review_status — a REJECT verdict would be committed to disk with no escalation.
const allApproved = peerVerdict.verdicts.every(function (v) {
  if (v.review_status !== 'APPROVE') return false
  return !(v.gaps || []).some(function (g) { return g.severity === 'medium' || g.severity === 'high' })
})
if (!allApproved) {
  return { error: 'HR-08: Phase 6 Peer Review had REJECT or unresolved medium/high gaps — escalate to human (previously this was silently ignored; T1-B adds the check)', peerVerdict: peerVerdict }
}

// Workflow writes 4 approval JSON files via persistApproval (Class C).
// This avoids the v33b-class double-encode bug where a sub-agent emitting a
// JSON string-of-string was accepted by `size >= 10 bytes` verify but later
// failed at advance-phase _verify_agent_b_approvals_core (data.get on str).
for (const v of peerVerdict.verdicts) {
  await persistApproval(v.deliverable, v)
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Tag & Advance
// ══════════════════════════════════════════════════════════════════════════

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
  // Manifest integrity: enforced by advance-phase itself since Round 22 站2
  // (cli/phase_cmds.py::_advance_prechecks, exit 27 with the restore command
  // in its [BLOCKED] message). It runs first, before any other precheck, and
  // on every round because advance-phase is idempotent — same guarantee the
  // per-round dispatch here used to buy, minus the dispatch, and now covering
  // the human/CI callers this loop never could.
  advanceReport = await dispatch(
    'YOU ARE THE PHASE-6 EXIT ORCHESTRATOR. Tag the Gate 4 release + advance to Phase 7. ROUND ' + round + '.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Steps:\n'
    + '0. GUARD — already advanced? `PHASE=$(jq -r .current_phase ' + REPO + '/.methodology/state.json 2>/dev/null); echo "current_phase=$PHASE"; [ "$PHASE" -ge 7 ]`. Also check: `git -C ' + REPO + ' tag -l "harness-v4-*" | head -1`. If Phase 7 is confirmed OR tag already exists, report "ADVANCE: PASS (already advanced)" and stop.\n'
    + '1. GIT-TAG (skip if step 0 found an existing tag): `' + PY + ' ' + REPO + '/harness_cli.py gate4-tag --project ' + REPO + '` then `git -C ' + REPO + ' push origin --tags`. gate4-tag reads composite_score from gate4_result.json (the same score finalize-gate computed and persisted), formats the tag, and creates it. Do NOT hand-build the tag command — gate4-tag is the single source of truth for tag naming and score extraction.\n'
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
  const advV = await dispatch(
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

// Bug A fix (2026-07-07): advance-phase intentionally commits the handover
// locally without pushing (harness/cli/phase_cmds.py: "next milestone push
// publishes to origin"). This workflow ends right after Advance with no
// next-phase push queued, so the handover commit was left stranded on
// local until whatever runs next happened to push it. Publish it now.
phase('Sync')
log('git push origin main (publish advance handover commit)')
const syncReport = await dispatch(
  'Run EXACTLY this command via Bash:\n'
  + 'git -C ' + REPO + ' push origin main\n\n'
  + 'Report final outcome as plain text: "SYNC: PASS" or "SYNC: FAIL — <one-line reason>".',
  { label: 'sync', phase: 'Sync', agentType: 'general-purpose' },
)
if (!/SYNC:\s*PASS/.test(String(syncReport ?? ''))) {
  return { error: 'post-advance push did not PASS', raw: String(syncReport ?? '').slice(-500) }
}


log('Phase 6 workflow complete. Open .methodology/phase7_plan.md to continue.')
return {
  phase_complete: true,
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
} catch (err) {
  const msg = (err && err.message) ? err.message : String(err)
  return {
    error: 'workflow crashed: ' + msg.slice(0, 300),
    workflow: meta.name,
    crashed: true,
    note: 'An agent dispatch threw instead of returning a result — most often a transient transport error, which the Workflow runtime does not retry or catch. Nothing was skipped silently: relaunch this workflow and its GUARD/sentinel checks short-circuit the work that already completed.',
  }
}
