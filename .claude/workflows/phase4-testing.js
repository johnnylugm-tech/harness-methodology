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
    { title: 'Declared Tests' },
    { title: 'Coverage' },
    { title: 'Bug Hunt' },
    { title: 'Artifacts Commit' },
    { title: 'Gate 3' },
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
    'You are the REPO RESOLVER. Find the project root by walking up from CWD until a directory contains `harness_cli.py` or `harness/harness_cli.py` and is NOT a submodule working tree.\n'
    + 'A submodule tree has a `.git` FILE whose first line starts with `gitdir:`. This ensures walk-up does not stop at harness/.\n'
    + 'Run EXACTLY this command via Bash (single line, copy-paste verbatim):\n'
    + 'cd "$(pwd)"; while [ "$(pwd)" != "/" ] && ! { { [ -f harness_cli.py ] || [ -f harness/harness_cli.py ]; } && ! { [ -f .git ] && head -1 .git 2>/dev/null | grep -q "^gitdir: "; }; }; do cd ..; done; '
    + 'if { [ -f harness_cli.py ] || [ -f harness/harness_cli.py ]; } && ! { [ -f .git ] && head -1 .git 2>/dev/null | grep -q "^gitdir: "; }; then echo "REPO=$(pwd)"; else echo "REPO_NOT_FOUND cwd=$(pwd)"; fi\n'
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
const HUNT_FINDING_PROPS = {
  module: { type: 'string' }, lens: { type: 'string' },
  severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
  title: { type: 'string' }, description: { type: 'string' }, file: { type: 'string' },
  line_start: { type: 'integer' }, line_end: { type: 'integer' }, code_snippet: { type: 'string' },
  reasoning: { type: 'string' }, suggested_fix: { type: 'string' },
  confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
}
const HUNT_FINDING_REQUIRED = ['module', 'severity', 'title', 'file', 'line_start', 'reasoning', 'confidence']
const HUNT_RESULT_SCHEMA = {
  type: 'object',
  properties: { findings: { type: 'array', items: { type: 'object', properties: HUNT_FINDING_PROPS, required: HUNT_FINDING_REQUIRED } } },
  required: ['findings'],
}
const THREAT_HUNT_SCHEMA = {
  type: 'object',
  properties: Object.assign({
    attack_vector: { type: 'string' }, attempted_exploit: { type: 'string' },
    mitigation_effective: { type: 'boolean' }, evidence: { type: 'string', description: 'file:line the verdict rests on' },
  }, HUNT_FINDING_PROPS),
  required: ['attack_vector', 'attempted_exploit', 'mitigation_effective', 'evidence'].concat(HUNT_FINDING_REQUIRED),
}
const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    is_real: { type: 'boolean' }, refutation_attempt: { type: 'string' },
    evidence: { type: 'string' }, severity_agrees: { type: 'boolean' },
  },
  required: ['is_real', 'refutation_attempt', 'evidence', 'severity_agrees'],
}
const HUNT_RECORD_SCHEMA = {
  type: 'object',
  properties: {
    rc: { type: 'integer', description: 'exit code on the RC= line' },
    findings: { type: 'integer', description: 'findings= from the RECORDED or PART line' },
    confirmed: { type: 'integer', description: 'confirmed= from the RECORDED or PART line' },
    first: { type: 'string', description: 'first= from the PART line' },
    last: { type: 'string', description: 'last= from the PART line' },
  },
  required: ['rc', 'findings', 'confirmed'],
}

function firstLineHasAnchor(text, expectPrefix) {
  // An empty anchor means the CALLER decided one applies but supplied nothing.
  // file_loader treats "" as "no anchor configured" and skips its check; here
  // that would turn a caller bug into "accept anything", so it is a failure.
  if (!expectPrefix) return false
  const nl = text.indexOf('\n')
  const firstLine = nl === -1 ? text : text.slice(0, nl)
  return firstLine.startsWith(expectPrefix)
}

// ---- relay frame (Round 86 站2): read-file's receipt, checked here ----
const RELAY_MAX_BYTES = 24576
function parseRelayFrame(text) {
  const nl = text.indexOf('\n')
  if (nl === -1) return null
  const m = text.slice(0, nl).match(/^<<<HARNESS-RELAY v1 mode=(content|index) sha256=([0-9a-f]{64}) bytes=(\d+) lines=(\d+)>>>$/)
  if (!m) return null
  const end = '<<<HARNESS-RELAY-END sha256=' + m[2] + '>>>'
  const body = text.slice(nl + 1).replace(/\s+$/, '')
  if (!body.endsWith(end)) return null
  // A relay claiming whole content for a file read-file refuses to send
  // whole contradicts the framework's own rule. Reject, do not believe.
  if (m[1] === 'content' && Number(m[3]) > RELAY_MAX_BYTES) return null
  return { mode: m[1], sha: m[2], bytes: Number(m[3]), lines: Number(m[4]),
    payload: body.slice(0, body.length - end.length).replace(/\n$/, '') }
}
// An index payload names the file and its first line; that second field
// is what keeps the anchor check alive for files too large to send.
function isFileIndex(s) {
  return typeof s === 'string' && /^FILE: .*\nFIRST-LINE: /.test(s)
}
function relayAnchorTarget(frame) {
  if (frame.mode === 'content') return frame.payload
  const m = frame.payload.match(/^FIRST-LINE: (.*)$/m)
  return m ? m[1] : ''
}

// ---- loadFileViaPython: deterministic Bash + harness_cli.py read-file (v33) ----
// Drops the v29 MCP read path (failed at large-context stages) in favour of a
// single-step Bash tool-call running the deterministic `harness_cli.py
// read-file` + `cat` relay, which does not depend on an MCP server in a
// headless run. read-file's prefix check is a first-line startswith() (file_
// loader Bug v8 guard), so all expectPrefix values passed in must lead with "#".
const RELAYED_SHA = {}
async function loadFileViaPython(relPath, expectPrefix, phaseName, opts) {
  opts = opts || {}
  const maxAttempts = opts.maxAttempts || 3
  const filePath = REPO + '/' + relPath
  const expectPrefixArg = expectPrefix ? ' --expect-prefix ' + JSON.stringify(expectPrefix) : ''
  const safeName = relPath.replace(/[\/.]/g, '_')
  const contentOut = '/tmp/load_' + safeName + '.txt'
  const jsonOut = '/tmp/load_' + safeName + '.json'
  // rm -f first: contentOut is a fixed path, and read-file does not write
  // it when the read fails — the agent would then cat a PREVIOUS run's
  // leftover, which carries a valid anchor and now a valid frame too.
  const pythonCmd = 'rm -f ' + contentOut + ' ' + jsonOut + ' && ' + PY
    + ' ' + REPO + '/harness_cli.py read-file --file ' + JSON.stringify(filePath)
    + expectPrefixArg + ' --relay --relay-max-bytes ' + RELAY_MAX_BYTES
    + ' --content-out ' + contentOut + ' --json-out ' + jsonOut + ' --quiet'

  const prompt = 'You are a SHELL WRAPPER AGENT. Your ONLY job is to run ONE shell command and emit ONE file content verbatim.\n\n'
    + 'STEPS (DO NOT DEVIATE):\n'
    + '1. Use the Bash tool to run EXACTLY this command (no modifications):\n'
    + '   ' + pythonCmd + '\n\n'
    + '2. Use the Bash tool to run `cat ' + contentOut + '` — read the content file from disk.\n\n'
    + '3. Your final assistant message = the EXACT output of `cat ' + contentOut + '` (verbatim bytes).\n\n'
    + 'CRITICAL OUTPUT RULES (violations = failure):\n'
    + '- DO NOT generate or paraphrase content based on your memory/inference.\n'
    + '- ALWAYS read the actual file from disk. NEVER hallucinate file content.\n'
    + '- DO NOT echo the JSON file. Only echo the content file.\n'
    + '- DO NOT write any preamble or acknowledgment.\n'
    + '- DO NOT add commentary, summary, or explanation.\n'
    + '- Your final message = the verbatim cat output only.\n'
    + '- If the command fails, return EXACTLY: ERROR_LOAD_FAILED: ' + filePath

  let lastFailReason = 'unknown'
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    let res
    try {
      res = await dispatch(prompt, {
        label: 'loadpy-' + relPath.replace(/[\/.]/g, '-') + '-a' + attempt,
        phase: phaseName,
        agentType: 'general-purpose',
      })
    } catch (e) {
      lastFailReason = 'agent_threw: ' + (e && e.message ? e.message : String(e)).slice(0, 80)
      log('  [' + relPath + '] attempt ' + attempt + '/' + maxAttempts + ' agent() threw: ' + (e && e.message ? e.message : String(e)).slice(0, 200))
      continue
    }
    const rawText = (typeof res === 'string' ? res : String(res ?? '')).trim()
    // sub-agent runtime sometimes emits a literal <think>...</think> preamble
    // merged into the same line as the real content (no newline in between),
    // which pushes the anchor off the start of the first line even though the
    // agent DID read the correct file. Strip it before validating. A <think>
    // block on its own line is NOT stripped and NOT accepted: that is an
    // unfaithful relay, which is what firstLineHasAnchor is here to catch.
    const text = rawText.replace(/^\s*<think>[\s\S]*?<\/think>\s*/, '')
    if (text.startsWith('ERROR_LOAD_FAILED')) {
      lastFailReason = 'ERROR_LOAD_FAILED'
      log('  [' + relPath + '] attempt ' + attempt + '/' + maxAttempts + ' ERROR_LOAD_FAILED')
      continue
    }
    if (text.length < 50) {
      lastFailReason = 'too_short(len=' + text.length + '): ' + text.slice(0, 60)
      log('  [' + relPath + '] attempt ' + attempt + '/' + maxAttempts + ' too short (len=' + text.length + ')')
      continue
    }
    const frame = parseRelayFrame(text)
    if (!frame) {
      lastFailReason = 'relay_frame_broken: got=' + text.slice(0, 60)
      log('  [' + relPath + '] attempt ' + attempt + '/' + maxAttempts + ' relay frame broken (truncated in transit, or not what read-file wrote)')
      continue
    }
    const anchorAt = relayAnchorTarget(frame)
    if (expectPrefix && !firstLineHasAnchor(anchorAt, expectPrefix)) {
      lastFailReason = 'prefix_mismatch: got=' + anchorAt.slice(0, 40)
      log('  [' + relPath + '] attempt ' + attempt + '/' + maxAttempts + ' content-prefix-mismatch (expected first line to start with "' + expectPrefix + '", got: ' + anchorAt.slice(0, 80) + ')')
      continue
    }
    log('  [' + relPath + '] relay ' + frame.mode + ': ' + frame.bytes + ' bytes / ' + frame.lines + ' lines')
    RELAYED_SHA[relPath] = frame.sha
    return frame.payload
  }
  return 'ERROR: LOADER_FAILED_AFTER_' + maxAttempts + '_ATTEMPTS: ' + relPath + ' (last: ' + lastFailReason + ')'
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
  + '1. GATE1-DELTA is long-running for any FR whose code actually changed (every internal fix round spawns a fixer AND re-dispatches a full GATE1, so even this "probe" runs for the budget the cap in step b encodes). Run it BACKGROUNDED, ONE FR AT A TIME — they share one project tree and one lock, so N at once is slower than N in sequence:\n'
  + '   a. `nohup ' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 4 --fr-id <FR> --step GATE1-DELTA --project ' + REPO + ' > /tmp/gate1delta_<FR>.log 2>&1 & echo $!` — note the PID.\n'
  + '   b. Poll with BACKOFF intervals, in seconds: 5, 10, 20, 30, 60, then `fr_step_poll_interval_s` for every further iteration — `sleep <interval> && kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Cap `fr_step_poll_cap` polls; both from the ctx JSON read for Direction C (absent ⇒ re-run load-context). Still RUNNING past the cap → `kill <PID>` (reaps the whole tree), classify <FR> as fail_fr_ids and move on (the full loop retries it).\n'
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
    + '1. GATE1-DELTA — long-running when code changed (every internal fix round spawns a fixer AND re-dispatches a full GATE1, so the wall time is the budget the cap in step b encodes). Run it BACKGROUNDED, do NOT invoke it as a plain synchronous command:\n'
    + '   a. `nohup bash -c \'' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 4 --fr-id ' + frId + ' --step GATE1-DELTA --project ' + REPO + '; echo "RC=$?"\' > /tmp/gate1delta_' + frId + '.log 2>&1 & echo $!` — note the PID.\n'
    + '   b. Poll with BACKOFF intervals, in seconds: 5, 10, 20, 30, 60, then `fr_step_poll_interval_s` for every further iteration — `sleep <interval> && kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Cap `fr_step_poll_cap` polls — both from ' + REPO + '/.sessi-work/phase4_ctx.json (absent ⇒ re-run load-context). Still RUNNING past the cap → `kill <PID>` (reaps the whole tree), report rc -1 (TIMEOUT, not a gate verdict).\n'
    + '   c. DONE → `tail -200 /tmp/gate1delta_' + frId + '.log`. The LAST line matching `RC=<integer>` is run-fr-step\'s own exit code — report that integer verbatim. It is NOT the Bash tool\'s rc; do not compute or infer it.\n'
    + '   - RC=0 → done.\n'
    + '   - RC=23 (dispatch structurally broken) / RC=70 (harness-methodology crashed) / RC=25 (INFRA precondition block) / RC=36 (repeated-failure refusal — an identical failure already seen N times on this tree): STOP — none of the four is a code-quality problem and no retry clears any of them. Report the rc and stop.\n'
    + '   - Any other nonzero RC → full TDD auto-triggered: TDD-RED → TDD-GREEN → TDD-IMPROVE → GATE1 (each for ' + frId + '). Max 3 rounds. Still failing → report the final rc.\n'
    + '   If ' + frId + '’s code is unchanged since last Gate 1 PASS, this passes immediately.\n\n'
    + 'Report via the StructuredOutput tool: { rc: <the integer from step 1c\'s last RC= line>, final_line: "' + frId + ' GATE1: PASS" or "' + frId + ' GATE1: FAIL — <reason>" }.\n\n'
    + 'SCOPE RULES:\n- DO NOT touch any FR OTHER than ' + frId + '.\n- DO NOT run run-gate / bug-hunt / advance-phase / push-milestone.\n- DO NOT edit .methodology/quality_manifest.json or .sessi-work/gate1_result.json to fake/reset scores — fix the underlying code/tests instead.\n- DO NOT modify harness/.\n- ONLY GATE1-DELTA (+ full TDD if needed) for ' + frId + '.',
    { label: 'delta-' + frId, phase: 'Per-FR Delta', agentType: 'general-purpose', schema: FR_STEP_SCHEMA },
  )
  // L1 (ported from phase3): distinguish a session/rate-limit block (null/empty
  // agent return) from a real Gate 1 FAIL — a rate-limit mid-DELTA must not be
  // misreported as a code-quality failure. DELTA auto-skip makes resume safe.
  if (frReport === null || frReport === undefined || typeof frReport !== 'object') {
    log('  ' + frId + ' agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
    return { session_limit_blocked: true, phase: 4, step: frId, fr_id: frId, gate1Pass, message: 'Agent hit session/rate limit during ' + frId + ' GATE1-DELTA. Resume after quota reset — completed FRs skip via DELTA auto-satisfy.' }
  }
  // L1.5-L1.7: the per-FR terminal aborts, read from run-fr-step's own exit code
  // (launch line's `; echo "RC=$?"`, carried by FR_STEP_SCHEMA). Prose is not
  // load-bearing — see render_terminal_abort_detectors' docstring (Round 70 站3).
  // Round 102 站3 added 36 and made 25/36 parkable via `park_rcs`.
  const frRc = (frReport && typeof frReport.rc === 'number') ? frReport.rc : null
  // 23 — dispatch structurally broken; every retry fails identically.
  if (frRc === 23) {
    log('  ' + frId + ' exited 23 — dispatch is structurally broken (claude.ai connectors disabled), aborting remaining FRs')
    return { dispatch_structurally_broken: true, phase: 4, fr_id: frId, gate1Pass, gate1Fail: [...gate1Fail, frId], message: frId + ' GATE1-DELTA: dispatch is structurally broken (env: ANTHROPIC_API_KEY overrides claude.ai login). Human must unset ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN/ANTHROPIC_BASE_URL/ANTHROPIC_DEFAULT_HAIKU_MODEL in the shell that launches this process, then re-run via Workflow({scriptPath, resumeFromRunId}).' }
  }
  // 70 — harness crashed. Not a project defect; no re-run clears it.
  if (frRc === 70) {
    log('  ' + frId + ' exited 70 — harness-methodology crashed, aborting remaining FRs')
    return { harness_bug_detected: true, phase: 4, fr_id: frId, gate1Pass, gate1Fail: [...gate1Fail, frId], message: frId + ' GATE1-DELTA: harness-methodology itself crashed (exit 70 — see the crash bundle path in the log). This is not a project quality issue; a human must diagnose and fix the harness bug before this FR can proceed.' }
  }
  // 25 — INFRA precondition block: project state, repairable, but not by a fix
  // agent aimed at code. Separate from 70 since 站2, because the remedy is.
  if (frRc === 25) {
    log('  ' + frId + ' exited 25 — INFRA precondition block, aborting remaining FRs')
    return { infra_abort: true, phase: 4, fr_id: frId, condition_class: 'UNREGISTERED', gate1Pass, gate1Fail: [...gate1Fail, frId], message: frId + ' GATE1-DELTA: an INFRA precondition failed (exit 25 — modules missing from SAB.json, or a tool that never ran). Repair project state with `harness_cli.py amend-sab`, then re-run with a NEW run_tag: Workflow({scriptPath, args: {repo, run_tag}}). amend-sab changes no prompt, so without one the cache can replay this halt.' }
  }
  // 45 — PHANTOM precondition block: SAB.json declares a module the codebase
  // does not implement. Same halt shape but different owner (project,
  // per fault_owner.py mapping) and a different remediation channel
  // (the resolve-phantom CLI form, not plain append).
  if (frRc === 45) {
    log('  ' + frId + ' exited 45 — PHANTOM precondition block (SAB declares a module that does not exist on disk), aborting remaining FRs')
    return { phantom_abort: true, phase: 4, fr_id: frId, condition_class: 'PHANTOM', gate1Pass, gate1Fail: [...gate1Fail, frId], message: frId + ' GATE1-DELTA: a PHANTOM precondition failed (exit 45 — SAB.json declares a module the codebase does not implement). For each phantom, either implement the module or run `harness_cli.py amend-sab --resolve-phantom <declared> --to <target>|--drop --reason ">=20 chars"`, then re-run with a NEW run_tag: Workflow({scriptPath, args: {repo, run_tag}}). amend-sab changes no prompt, so without one the cache can replay this halt.' }
  }
  // 36 — repeated-failure refusal (exit 36, EX_STEP_REPEATED_FAILURE):
  // run-fr-step refuses to spend another dispatch on an identical failure
  // it has already seen on this exact tree (Round 102 站3). The cause is
  // whatever failed identically N times — measured: budget kills and quota
  // INFRA_ERRORs (owner infra). This driver records the row as infra;
  // fault_owner.py keeps 36 = UNKNOWN by design for the class-derived
  // ledger owners (GATE1_BLOCKED -> project, TURN_BUDGET/TIMEOUT -> infra).
  if (frRc === 36) {
    log('  ' + frId + ' exited 36 — repeated-failure refusal, aborting remaining FRs')
    return { repeated_failure: true, phase: 4, fr_id: frId, gate1Pass, gate1Fail: [...gate1Fail, frId], message: frId + ' GATE1-DELTA: exit 36 — run-fr-step refuses to re-dispatch an identical failure already seen on this exact tree. Read .methodology/degradations.jsonl for the signature; any change to the tree re-opens the step. This is not a code-quality verdict on this FR\'s implementation.' }
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
  // Round 85 站2: a quota cap here returns null, whose empty reason does
  // not start with GATE1_VERIFIED_PASS — a rate limit read as a Gate 1 FAIL.
  if (verdict === null || verdict === undefined || typeof verdict !== 'object') {
    log('  ' + frId + ' agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
    return { session_limit_blocked: true, phase: 4, step: frId, fr_id: frId, gate1Pass, message: 'Agent hit session/rate limit verifying ' + frId + ' Gate 1. Resume after quota reset — the manifest read is idempotent.' }
  }
  const passed = String((verdict && verdict.reason) || '').trim().startsWith('GATE1_VERIFIED_PASS')
  // rc -1 is the wrapper saying it killed the step, not a verdict — after
  // the manifest read (see render_fr_step_timeout_exit, Round 85 站2).
  if (!passed && frRc === -1) {
    log('  ' + frId + ' — GATE1-DELTA killed at the poll cap; no manifest verdict')
    return { fr_step_timeout: true, halt_step: 'fr-step-timeout', phase: 4, fr_id: frId, gate1Pass, gate1Fail, message: frId + ' GATE1-DELTA: killed at the poll cap with run-fr-step still running, so no gate verdict was reached — this is NOT a code-quality failure and no fix agent should be sent at it. Re-run with a NEW run_tag: Workflow({scriptPath, args: {repo, run_tag}}); a recurrence means the step is hung past the budget computed from fr_step timeout and max_fix_rounds.' }
  }
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
  return halt('gate1', { error: 'Phase 4: Gate 1 FAILED for FR(s): ' + gate1Fail.join(', ') + ' (escalate)', owner: 'project', gate1Pass, gate1Fail })
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
// Phase: Declared Tests
// ══════════════════════════════════════════════════════════════════════════

phase('Declared Tests')
log('Declared tests no FR owns (NFR sections, the deferred table): write them before Gate 3')
const declaredCmd = PY + ' ' + REPO + '/harness_cli.py undelivered-tests --project ' + REPO + ' --non-fr'
let declaredDone = false
for (let round = 1; round <= 3; round++) {
  const chk = await dispatch(
    'Run EXACTLY this via the Bash tool:\n`' + declaredCmd + '; echo RC=$?`\n'
    + 'Report via the StructuredOutput tool: rc = the exact number on the final RC= line.',
    { label: 'declared-check-r' + round, phase: 'Declared Tests', agentType: 'general-purpose', schema: RC_SCHEMA },
  )
  if (chk && chk.rc === 0) { declaredDone = true; break }
  if (round === 3) break
  await dispatch(
    'YOU ARE THE P4 TEST AUTHOR for the tests TEST_SPEC.md declares outside every FR\'s rows.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + '1. `' + declaredCmd + '` lists each one and its section.\n'
    + '2. Write each, EXACTLY that name, from its row in 02-architecture/TEST_SPEC.md (Inputs, precondition, sub-assertions), in an NFR test file. Assert what the row says about the product; no `assert True`, no skip.\n'
    + '3. A suite-level criterion READS the harness evidence (.methodology/gate_evidence/, the coverage report); it never runs pytest over its own directory or `make verify-system`.\n'
    + '4. Run them, then commit only the test files: `git -C ' + REPO + ' add <files> && git -C ' + REPO + ' commit -m "test(P4): declared tests no FR owns"`.\n\n'
    + 'SCOPE RULES:\n- ONLY test files; DO NOT edit source or any phase deliverable, DO NOT rename a declared test, DO NOT run run-gate / advance-phase.',
    { label: 'declared-write-r' + round, phase: 'Declared Tests', agentType: 'general-purpose' },
  )
}
if (!declaredDone) {
  return halt('declared-tests', { error: 'declared tests outside every FR\'s rows are still undelivered after 2 writing rounds — `harness_cli.py undelivered-tests --non-fr` lists them', owner: 'project' })
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
  + '1. TEST_RESULTS: write ' + REPO + '/04-testing/TEST_RESULTS.md — summarise test execution: cases run, pass/fail, deferred issues. Include the VERBATIM pytest summary line of the run you are describing (the `N passed, M skipped … in T s` line pytest prints); `cross_artifact.check_test_count_reconciliation` compares its counts against the framework own run_suite measurement and reports a mismatch as CRITICAL, so this document cannot record a run over a tree the project does not deliver. Scope the run to the `test_target` step 2 reads, NOT to the repository root — the root also holds the vendored harness copy, and a run from there collects thousands of the framework own tests. Measured: one project recorded `4 failed, 7563 passed, 3 skipped` for a 349-test tree, and that number then travelled into BASELINE.md and VERIFICATION_REPORT.md unchallenged.\n'
  + '2. COVERAGE: read TESTS=`test_target` and SRC=`cov_target` (project-relative) from ' + REPO + '/.sessi-work/phase4_ctx.json — load-context writes them from the resolver Gate 3 re-measures with. Do NOT substitute your own: the layout differs between projects, and .coveragerc may scope SRC. Run `' + PY + ' -m pytest ' + REPO + '/<TESTS> --cov=<SRC> --cov-report=term-missing -q | tee ' + REPO + '/04-testing/coverage_raw.txt` then `' + PY + ' -m coverage report --format=total`. Write ' + REPO + '/04-testing/COVERAGE_REPORT.md with overall coverage % (≥80% for Gate 3), per-module breakdown, uncovered lines.\n'
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
log('Adversarial bug hunt (hunt_bugs.md): targets → scout → hunters → refute+confirm → record → resolve')
// A workflow agent() child has no Agent tool, so one agent told to "spawn hunters" hunted and
// verified alone (measured: 0 sub-agents in a real P4). The fan-out lives here instead.
const HUNT_LENSES = {"correctness": "Business logic errors, boundary conditions, null/empty handling, off-by-one, type mismatches, incorrect assumptions about input data.", "concurrency": "Race conditions, thread safety, async/await issues, shared mutable state, lock ordering, ordering of side effects, lifecycle of long-lived objects across awaits.", "resilience": "Error handling gaps, missing timeouts, broken fallbacks, resource leaks (files/sockets/connections/child procs), partial-failure handling, error swallowing, NFR compliance for degraded modes.", "general": "Any concrete, reachable bug — wrong return type, broken validation, dead branch, leaked resource, missing rollback, incorrect status code, log/PII leak, input size limit (DoS), wrong default. Skip stylistic nits and hypotheticals."}
const htRc = await dispatch(
  'Run EXACTLY this via the Bash tool:\n`' + PY + ' ' + REPO + '/harness_cli.py bug-hunt-targets --project ' + REPO + '; echo RC=$?`\n'
  + 'Report via the StructuredOutput tool: rc = the exact number on the final RC= line.',
  { label: 'hunt-targets', phase: 'Bug Hunt', agentType: 'general-purpose', schema: RC_SCHEMA },
)
if (!(htRc && htRc.rc === 0)) return halt('bug-hunt', { error: 'bug-hunt-targets did not exit 0', owner: 'infra', rc: htRc ? htRc.rc : null })
const huntManifestText = await loadFileViaPython('.methodology/bug_hunt_targets.json', '', 'Bug Hunt')
let huntTargets = null
try { huntTargets = JSON.parse(huntManifestText) } catch (e) { huntTargets = null }
if (!huntTargets || !Array.isArray(huntTargets.high_risk) || !Array.isArray(huntTargets.standard)) {
  return halt('bug-hunt', { error: 'bug_hunt_targets.json was not relayed intact', owner: 'infra', detail: String(huntManifestText).slice(0, 200) })
}
const huntPairs = []
for (const m of huntTargets.high_risk) for (const k of ['correctness', 'concurrency', 'resilience']) huntPairs.push({ kind: 'lens', lens: k, name: m.name, path: m.path, note: (m.reasons || []).join('; ') })
for (const m of huntTargets.standard) huntPairs.push({ kind: 'lens', lens: 'general', name: m.name, path: m.path, note: m.survivors ? m.survivors + ' mutation survivor(s) — prioritise their functions' : '' })
for (const t of (huntTargets.threat_model || [])) if (t.path) huntPairs.push({ kind: 'threat', lens: 'threat-model', name: t.threat_id, path: t.path, threat: t })
log('  ' + huntPairs.length + ' hunters (' + huntTargets.high_risk.length + ' high-risk x3, ' + huntTargets.standard.length + ' standard x1, ' + (huntTargets.threat_model || []).filter((t) => t.path).length + ' threats)')
const huntScout = await dispatch(
  'YOU ARE THE CRG SCOUT for an adversarial bug hunt (hunt_bugs.md Phase 1). REPO: ' + REPO + '\n'
  + 'Targets: ' + REPO + '/.methodology/bug_hunt_targets.json (read it). For each target call CRG get_review_context (include_source, max_depth=2); for high_risk also tests_for / callers_of on key functions; then list_flows. Mark mutation-survivor functions and each threat owner module PRIORITY.\n'
  + 'Output markdown, at most 5000 words: per module key functions @line, callers, test coverage, suspicious patterns, PRIORITY marks; top flows. READ ONLY.',
  { label: 'hunt-scout', phase: 'Bug Hunt', agentType: 'Explore', model: HUNT_MODEL },
)
if (typeof huntScout !== 'string' || huntScout.length < 50) return halt('bug-hunt', { error: 'CRG scout returned nothing', owner: 'infra' })
const huntPrompt = (p) => (p.kind === 'threat'
  ? 'YOU ARE A THREAT-MODEL HUNTER (hunt_bugs.md). Declared threat ' + p.threat.threat_id + ' (' + p.threat.category + '): ' + p.threat.description + '\nDeclared mitigation: ' + p.threat.mitigation + '\nOwner file: ' + REPO + '/' + p.path + '\n'
    + 'Try to carry out the attack against the code as written. Decide whether the declared mitigation actually blocks it (not merely whether defensive-looking code exists). Report one row: attack_vector, attempted_exploit, mitigation_effective, evidence (file:line), plus the finding fields (severity per the hunt_bugs.md rubric if the mitigation fails, else low).\n'
  : 'YOU ARE A BUG HUNTER, LENS=' + p.lens + ' (hunt_bugs.md Phase 2). LENS FOCUS: ' + HUNT_LENSES[p.lens] + '\nTARGET: ' + REPO + '/' + p.path + (p.note ? '\nNOTE: ' + p.note : '') + '\n'
    + 'Read the target fully; use CRG callers_of/callees_of/tests_for. Report only bugs reachable on the current code path with a concrete failure scenario; no style nits, no hypotheticals, nothing static preflight already blocks. An empty findings list is a valid result.\n')
  + 'Each finding: module, lens, severity, title, description, file (project-relative), line_start, line_end, code_snippet (<=8 verbatim lines), reasoning (cite the proving line + trigger), suggested_fix, confidence. READ ONLY — edit nothing.\n\nSCOUT CONTEXT:\n' + huntScout
const huntHunt = async (p, i, sfx) => await dispatch(huntPrompt(p), { label: 'hunt-' + i + '-' + p.lens + sfx, phase: 'Bug Hunt', agentType: 'Explore', model: HUNT_MODEL, schema: p.kind === 'threat' ? THREAT_HUNT_SCHEMA : HUNT_RESULT_SCHEMA })
const huntVerify = async (f, i, j, role) => await dispatch(
  (role === 'refute'
    ? 'Try to REFUTE this bug finding (hunt_bugs.md Phase 3). Default is_real=false unless undeniable. Is the cited code at the cited line? Does surrounding code already guard it? Is the scenario reachable? Cite line numbers.\n'
    : 'Independently CONFIRM this bug finding (hunt_bugs.md Phase 3). Default is_real=false unless provable: trace the data flow to the line, check tests_for (a passing test on this path suggests it is handled), and confirm only with a concrete trigger + expected vs actual, citing line numbers.\n')
  + 'REPO: ' + REPO + '\nFINDING:\n' + JSON.stringify(f) + '\nREAD ONLY.',
  { label: 'hunt-' + i + '-' + j + '-' + role, phase: 'Bug Hunt', agentType: 'Explore', model: HUNT_MODEL, schema: VERIFY_SCHEMA })
const huntCited = (v) => /(:\d+|line\s*\d+|L\d+)/i.test(String(v.evidence) + ' ' + String(v.refutation_attempt))
const huntJudge = async (res, p, i) => {
  if (!res) return null
  const raw = p.kind === 'threat' ? [res] : (res.findings || [])
  const out = []
  for (let j = 0; j < raw.length; j++) {
    const f = Object.assign({}, raw[j], { lens: p.lens, module: raw[j].module || p.name })
    if (p.kind === 'threat' && f.mitigation_effective === true) { out.push({ f: f, confirmed: false, refute: String(f.evidence || '') }); continue }
    const vs = (await parallel([() => huntVerify(f, i, j, 'refute'), () => huntVerify(f, i, j, 'confirm')])).filter(Boolean)
    const real = vs.filter((v) => v.is_real)
    const confirmed = real.length === 2 || (real.length === 1 && huntCited(real[0]))
    const refuter = vs.find((v) => !v.is_real)
    out.push({ f: f, confirmed: confirmed, evidence: real.length ? String(real[0].evidence) : '', refute: refuter ? String(refuter.refutation_attempt || refuter.evidence) : 'no verifier confirmed' })
  }
  return out
}
const huntRows = await pipeline(huntPairs, (p, _p, i) => huntHunt(p, i, ''), huntJudge)
for (let i = 0; i < huntPairs.length; i++) {
  if (huntRows[i] === null) huntRows[i] = await huntJudge(await huntHunt(huntPairs[i], i, '-retry'), huntPairs[i], i)
}
const huntMissing = huntPairs.filter((p, i) => huntRows[i] === null).map((p) => p.lens + ':' + p.path)
if (huntMissing.length) return halt('bug-hunt', { error: huntMissing.length + ' hunter(s) returned nothing after a retry — the hunt did not cover ' + huntMissing.slice(0, 5).join(', '), owner: 'infra' })
const huntSeq = {}
const huntFindings = []
for (const rows of huntRows) for (const r of rows) {
  const m = String(r.f.module)
  huntSeq[m] = (huntSeq[m] || 0) + 1
  const row = Object.assign({}, r.f, { id: m + '#' + huntSeq[m], confirmed: r.confirmed, verify_evidence: r.evidence || '' })
  row.resolution = r.confirmed ? { status: 'open' } : { status: 'refuted', refute_evidence: r.refute }
  huntFindings.push(row)
}
const huntConfirmed = huntFindings.filter((f) => f.confirmed).length
log('  hunt: ' + huntFindings.length + ' finding(s), ' + huntConfirmed + ' confirmed by adversarial verify')
// One agent per part: a whole report through a single Write call would be one output of
// tens of thousands of tokens on a large hunt. Each part echoes back what it holds.
const huntParts = []
for (const f of huntFindings) {
  const last = huntParts[huntParts.length - 1]
  if (!last || JSON.stringify(last).length + JSON.stringify(f).length > 12000) huntParts.push([f])
  else last.push(f)
}
const huntPartOk = (k, r) => !!(r && r.rc === 0 && r.findings === huntParts[k].length
  && r.confirmed === huntParts[k].filter((f) => f.confirmed).length && r.first === huntParts[k][0].id && r.last === huntParts[k][huntParts[k].length - 1].id)
const huntWritePart = async (k, sfx) => await dispatch(
  'Write the JSON between the markers to ' + REPO + '/.sessi-work/bug_hunt/part-' + (k + 1) + '.json with the Write tool, byte for byte (do not edit, reformat or summarise it). Then run via Bash: `' + PY + ' ' + REPO + '/harness_cli.py record-bug-hunt --project ' + REPO + ' --part ' + (k + 1) + '; echo RC=$?`\n'
  + 'Report via the StructuredOutput tool: rc from the RC= line; findings, confirmed, first, last from its PART line.\n<<<JSON\n' + JSON.stringify({ findings: huntParts[k] }) + '\nJSON>>>',
  { label: 'hunt-record-' + (k + 1) + sfx, phase: 'Bug Hunt', agentType: 'general-purpose', schema: HUNT_RECORD_SCHEMA })
const huntPartRes = await parallel(huntParts.map((_, k) => () => huntWritePart(k, '')))
for (let k = 0; k < huntParts.length; k++) if (!huntPartOk(k, huntPartRes[k])) huntPartRes[k] = await huntWritePart(k, '-retry')
const huntBadParts = huntParts.map((_, k) => k + 1).filter((n) => !huntPartOk(n - 1, huntPartRes[n - 1]))
if (huntBadParts.length) return halt('bug-hunt', { error: 'bug-hunt part(s) ' + huntBadParts.join(', ') + ' of ' + huntParts.length + ' were not recorded as dispatched', owner: 'infra' })
const huntRec = await dispatch(
  'Run EXACTLY this via the Bash tool:\n`' + PY + ' ' + REPO + '/harness_cli.py record-bug-hunt --project ' + REPO + ' --assemble ' + huntParts.length + ' --lenses threat-model,correctness,concurrency,resilience,general; echo RC=$?`\n'
  + 'Report via the StructuredOutput tool: rc from the RC= line; findings and confirmed from its RECORDED line (0 and 0 if there is none).',
  { label: 'hunt-record-assemble', phase: 'Bug Hunt', agentType: 'general-purpose', schema: HUNT_RECORD_SCHEMA },
)
if (!(huntRec && huntRec.rc === 0 && huntRec.findings === huntFindings.length && huntRec.confirmed === huntConfirmed)) {
  return halt('bug-hunt', { error: 'record-bug-hunt did not assemble the hunt as dispatched (expected ' + huntFindings.length + ' findings / ' + huntConfirmed + ' confirmed)', owner: 'infra', got: huntRec })
}
await dispatch(
  'Write a concise markdown bug report in Traditional Chinese at ' + REPO + '/03-development/.audit/bug-report-hunt.md from ' + REPO + '/.methodology/bug_hunt_report.json (read it): summary table (module x severity), confirmed bugs by severity (location, problem, evidence, fix), refuted findings (one line each), fix priority, method. Cite file:line; at most 2000 words. Edit nothing else.',
  { label: 'hunt-report-md', phase: 'Bug Hunt', agentType: 'general-purpose' },
)
const huntBlocking = huntFindings.filter((f) => f.confirmed && (f.severity === 'critical' || f.severity === 'high'))
if (huntBlocking.length === 0) {
  log('  no confirmed critical/high finding — nothing to resolve before Gate 3')
} else {
  const huntReport = await dispatch(
    'YOU ARE THE BUG-HUNT RESOLVER (Step 4b, before Gate 3). REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Gate 3 adversarial_review BLOCKS while any confirmed critical/high finding in .methodology/bug_hunt_report.json is "open". These ' + huntBlocking.length + ' are: ' + huntBlocking.map((f) => f.id).join(', ') + '.\n'
    + 'For EACH set resolution.status:\n- resolved: write a repro test under 03-development/tests/ that RED-fails on the bug, apply the minimal source fix, confirm GREEN, commit `fix(<module>): <title>`, then record fix_commit (SHA) and repro_test (path).\n- refuted: read the code, find the guard the finding missed, record refute_evidence with exact line numbers.\n'
    + 'Edit only those findings\' resolution fields in the report; never change confirmed, severity or the verify evidence.\n\n'
    + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if every one of them is resolved-or-refuted; reason = one-line summary.\n\n'
    + 'SCOPE RULES:\n- DO NOT run run-gate (Gate 3) / advance-phase / push-milestone.\n- DO NOT modify harness/ (HR-17).\n- ONLY the fixes, repro tests and resolution fields for the findings named above.',
    { label: 'hunt-resolve', phase: 'Bug Hunt', agentType: 'general-purpose', model: HUNT_MODEL, schema: VERDICT_SCHEMA },
  )
  if (!(huntReport && huntReport.pass === true)) {
    return halt('bug-hunt', { error: 'confirmed critical/high bug-hunt findings are still open (Gate 3 adversarial_review will block)', reason: huntReport ? String(huntReport.reason ?? '').slice(-600) : 'agent returned null' })
  }
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
log('Gate 3 exit (composite ≥80, 17 dims: 13 self-scored + mutation_testing/architecture/traceability/adversarial_review framework-owned)')
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
    + '2. G3b: Evaluate ALL Gate 3 dimensions inline per ' + REPO + '/harness/harness/ssi/prompts/evaluate_dimension.md. Write ' + REPO + '/.sessi-work/gate3_result.json.\n   17 dims per gate3_p4_exit.yaml: linting(90) type_safety(85) test_coverage(80) security(80) secrets_scanning(100) license_compliance(100) mutation_testing(70) integration_coverage(60) architecture(80) readability(80) error_handling(80) documentation(75) test_assertion_quality(60) performance(75) execute_verification_target(100) traceability(100) adversarial_review(100).\n   (A project\'s feature flags can remove dims; the `dimensions:` list run-gate just printed is the authoritative one.)\n   FRAMEWORK-OWNED (do NOT self-score — finalize-gate computes these and overwrites what you write): mutation_testing (mutmut), architecture (code-review-graph), traceability (harness-trace), adversarial_review (bug-hunt-report).\n   For any failing dim: fix ROOT CAUSE in code (ruff/pyright/tests/bandit/readability_v2/ast-error-handling/pytest-benchmark), re-run the tool, update score. (readability tool is `python3 -m harness.toolchains.readability_v2` — NOT `radon mi` — per phase3/4/6_plan.md v2.12.0.) A low architecture score has no waiver route (Round 38): fix the structure, or — only for a genuine CRG false positive — calibrate `crg_excludes` / `crg_cohesion_healthy` in .methodology/harness_config.json, which is committed and therefore applies to CI too.\n   mutation_testing re-run MUST be BACKGROUNDED (mutmut can take up to 3600s; a synchronous call is silently truncated at ~10min, which is exactly how a fabricated score happens):\n   a. Launch: `nohup ' + PY + ' ' + REPO + '/harness_cli.py mutation-test-score --project ' + REPO + ' > /tmp/mutation_g3_r' + round + '.log 2>&1 & echo $!` — note the PID.\n   b. Poll every 15s: `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE` (cap 240 polls / ~60min). Past cap → `kill <PID>`, record TIMEOUT for mutation_testing — never hand-write a score.\n   c. DONE → `cat /tmp/mutation_g3_r' + round + '.log`; this already wrote ' + REPO + '/.methodology/mutation_score.json — read it back, never author it.\n'
    + '3. G3c — run BACKGROUNDED (same class of risk as GATE2\'s G2c — a single opaque Bash call with no visible output until it returns is exactly the shape the 180s stall watchdog kills):\n   a. Launch: `nohup ' + PY + ' ' + REPO + '/harness_cli.py finalize-gate --gate 3 --phase 4 --project ' + REPO + ' > /tmp/gate3_finalize_r' + round + '.log 2>&1 & echo $!` — note the printed PID.\n   b. Poll: every 15s run `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Repeat until DONE (cap 40 polls / ~10min). Still RUNNING past the cap → `kill <PID>` (reaps the whole tree), report "GATE3: TIMEOUT".\n   c. Once DONE: `cat /tmp/gate3_finalize_r' + round + '.log` for the full output — identical to what a synchronous run would have printed.\n\n'
    + '4. D4 — run BACKGROUNDED (this check can exceed the Bash tool\'s ~10-min synchronous default; a truncated call must not be read as passing/excluded):\n   a. Launch: `nohup ' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 80.0 > /tmp/d4_g3_r' + round + '.log 2>&1 & echo $!` — note the PID.\n   b. Poll every 15s: `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE` (cap 80 polls / ~20min). Past cap → `kill <PID>`, report D4 as TIMEOUT — never invent a test\'s delivered/excluded status.\n   c. DONE → `cat /tmp/d4_g3_r' + round + '.log`. FAIL → add missing tests, re-run this backgrounded step.\n'
    + '5. CRG-ARCH: `' + PY + ' ' + REPO + '/harness_cli.py crg-arch-check --project ' + REPO + '`. CI enforces this as an absolute floor on every push, independent of the Gate 3 composite score. FAIL → the crg-arch-check output lists the low-cohesion communities / oversized functions; fix the underlying architecture issue, re-run.\n'
    + 'finalize-gate (G3c) writes HANDOVER.md + pushes on PASS. Report final line: "GATE3: PASS" (composite ≥80 AND all dims ≥ threshold AND D4 ≥80% AND CRG architecture ≥80) or "GATE3: FAIL — <failing dims>".\n\n'
    + 'SCOPE RULES:\n- DO NOT run advance-phase.\n- DO NOT edit gate3_result.json, mutation_score.json, or any evidence file to fake/reconstruct a score — fix the code, or record TIMEOUT if a backgrounded call genuinely times out.\n- DO NOT cite a framework exclusion/deferral rule you cannot point to in harness source — an uncited shortfall is real.\n- DO NOT modify harness/ (HR-17).\n- ONLY run-gate/eval/finalize/spec-coverage/crg-arch-check + code fixes.',
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
    { label: 'deferred-fixes-g3', phase: 'Gate 3', agentType: 'general-purpose' },
  )
  return halt('gate3', { error: 'Gate 3 did not PASS in 3 rounds (HR-08); deferred_fixes.md written to .methodology/ (advance-phase exit 17 until resolved)', owner: 'project', raw: String(gate3Report ?? '').slice(-600) })
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Preview Next-Phase
// ══════════════════════════════════════════════════════════════════════════

phase('Preview Next-Phase')
log('preview-next-phase --phase 4 (predict Phase 5 entry-blocking findings before Push)')
const MAX_PREVIEW_FIX_ROUNDS = 3
let previewClean = false, previewReport = null, previewReason = ''
for (let round = 1; round <= MAX_PREVIEW_FIX_ROUNDS; round++) {
  previewReport = await dispatch(
    'YOU ARE THE PHASE-4 PRE-PUSH OBLIGATION CHECKER. Round ' + round + '/' + MAX_PREVIEW_FIX_ROUNDS + '.\n'
    + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
    + 'Run EXACTLY: `' + PY + ' ' + REPO + '/harness_cli.py preview-next-phase --phase 4 --project ' + REPO + '`\n'
    + 'READ-ONLY — no state/HANDOVER/commit writes.\n\n'
    + 'Report via the StructuredOutput tool: pass = true ONLY if the output says "clean — no blocking obligations predicted"; reason = the verbatim output (or its obligation lines if long).',
    { label: 'preview-next-phase-r' + round, phase: 'Preview Next-Phase', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
  )
  if (previewReport === null || previewReport === undefined) {
    return halt('preview-next-phase-unmeasured', { error: 'preview-next-phase was never read, so Phase 5 entry is unknown, not blocked', reason: 'agent returned null (skipped or terminal API error)' })
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
      'YOU ARE THE PHASE-4 PRE-PUSH OBLIGATION FIXER. Round ' + round + '.\n'
      + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
      + 'The following obligations were predicted to block Phase 5 entry:\n\n'
      + previewReason + '\n\n'
      + 'Each names a file/rule_id — open it, close the gap surgically. Never fabricate a case to force a citation.\n\n'
      + 'SCOPE:\n- ONLY what is named.\n- NOT harness/ (HR-17) — a framework bug: STOP, report, don\'t route around it.\n- NOT phase-transition/push/advance-phase.',
      { label: 'preview-fix-r' + round, phase: 'Preview Next-Phase', agentType: 'general-purpose' },
    )
    if (fixReport === null || fixReport === undefined || fixReport === '' || typeof fixReport !== 'string') {
      log('  preview-next-phase-fix agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')
      return { session_limit_blocked: true, phase: 4, step: 'preview-next-phase-fix', message: 'Agent hit session/rate limit during the pre-push obligation fixer. Resume after quota reset — state.json is untouched.' }
    }
  }
}
if (!previewClean) {
  return halt('preview-next-phase', { error: 'Phase 5 entry obligations still present after ' + MAX_PREVIEW_FIX_ROUNDS + ' round(s) — escalate to human', raw: previewReason.slice(-1200) })
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
    + '1. RE-VERIFY GATE 3 (do this FIRST): `' + PY + ' ' + REPO + '/harness_cli.py verify-gate --project ' + REPO + ' --gate 3 --phase 4 --spec-threshold 80.0`\n   The earlier Gate 3 PASS was measured on the tree as it stood THEN; every step since has written the delivered tree, and advance-phase compares that verdict\'s digest against the tree it is about to record. This is what makes the verdict describe the tree being advanced. Non-zero exit: its [BLOCKED] line names which check regressed — fix it and re-run this step.\n'
    + '2. PUSH ⑥ p4-pre-gate3 (skip if `jq -r --arg t p4-pre-gate3 \'.last_milestone_head[$t] // empty\' ' + REPO + '/.methodology/state.json` prints a sha): `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p4-pre-gate3 --project ' + REPO + ' --fr-ids ' + gate1Pass.join(',') + '`.\n'
    + '3. advance-phase: `' + PY + ' ' + REPO + '/harness_cli.py advance-phase --completed 4 --project ' + REPO + '`\n'
    + '   advance-phase independently re-verifies EVERYTHING before it will advance — its own output tells you exactly what is missing. If it prints "[BLOCKED] ...", that message IS the fix instruction: read it verbatim and do exactly what it says, then re-run this same advance-phase command. Do NOT guess what might be wrong — trust only what advance-phase itself reports. It is safe to re-run repeatedly within this round.\n'
    + '4. Read ' + REPO + '/.methodology/state.json; confirm current_phase = 5 (advance-phase atomically writes state.json when complete).\n\n'
    + 'Report final line: "ADVANCE: PASS|FAIL — <details>". If still FAIL after exhausting this round\'s turn, report the LAST [BLOCKED] message verbatim so the next round starts from where this one left off. PHASE_5_PLAN: ' + REPO + '/.methodology/phase5_plan.md\n\n'
    + 'SCOPE RULES:\n- DO NOT re-do P4 testing.\n- DO NOT use --no-verify.\n- DO NOT modify harness/ (HR-17).\n- ONLY verify-gate + push-milestone p4-pre-gate3 + advance-phase + verify HANDOVER.md + the specific fixes advance-phase\'s own output asked for.\n- Any diagnostic/debug script MUST be written under .sessi-work/tmp/ (never repo root or source dirs) and self-cleaned before you exit.',
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
  if (/\[HARNESS-BUG\][^\n]*\n {2}This is a bug in harness-methodology itself/i.test(syncText)) {
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
