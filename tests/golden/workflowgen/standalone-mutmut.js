// standalone-mutmut — the mutation_testing dimension, run on its own
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/spec_mutmut.py. Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write
//
// The framework computes the score (harness_cli.py mutation-test-score writes
// .methodology/mutation_score.json); this workflow relays that file and
// compares it with the gate threshold. It never computes a kill rate itself.


export const meta = {
  name: 'standalone-mutmut',
  description: 'Run the mutation_testing dimension on its own: mutation-test-score, then the score the framework recorded against the gate threshold',
  phases: [
    { title: 'Mutation Testing' },
    { title: 'Report' },
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


// ---- Gate verdict schemas (flat, top-level consts — playbook §5.2/§5.3) ----
// Verdict authority rule: heavy orchestrator agents keep prose narrative;
// their PASS/FAIL is NEVER parsed from that prose. A separate bash-proxy
// agent reads the harness's own artifact (manifest quality_complete,
// state.json/git log, CLI exit code) and reports through the schema.
const RC_SCHEMA = {
  type: 'object',
  properties: { rc: { type: 'integer', description: 'exact numeric exit code of the command' } },
  required: ['rc'],
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
// Phase: Mutation Testing
// ══════════════════════════════════════════════════════════════════════════

phase('Mutation Testing')
const THRESHOLD = 70.0  // harness/gate_configs mutation_testing
const mutCmd = PY + ' ' + REPO + '/harness_cli.py mutation-test-score --project ' + REPO
const runRc = await dispatch(
  'You MUST use the Bash tool. mutmut can take up to 3600s, and a synchronous call is cut off at ~10 min, so run it BACKGROUNDED:\n'
  + '  a. Launch: `nohup sh -c ' + JSON.stringify(mutCmd + '; echo RC=$?') + ' > /tmp/standalone_mutmut.log 2>&1 & echo $!` — note the PID.\n'
  + '  b. Poll every 15s: `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE` (cap 240 polls). Past the cap: `kill <PID>` and report rc -1.\n'
  + '  c. DONE: `tail -1 /tmp/standalone_mutmut.log` prints RC=<n>.\n'
  + 'Report via the StructuredOutput tool: rc = the EXACT integer after RC= (-1 if it never finished). Do not run mutmut yourself, do not compute any rate, do not edit any file.',
  { label: 'mutation-run', phase: 'Mutation Testing', agentType: 'general-purpose', schema: RC_SCHEMA },
)
if (!(runRc && runRc.rc === 0)) {
  return { error: 'mutation-test-score did not produce a score', rc: runRc ? runRc.rc : null, note: 'Read /tmp/standalone_mutmut.log: the command prints why (no [mutmut] in setup.cfg, mutmut missing, a crash). Nothing here substitutes a number for it.' }
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Report
// ══════════════════════════════════════════════════════════════════════════

phase('Report')
const scoreText = await loadFileViaPython('.methodology/mutation_score.json', '', 'Report')
let recorded = null
try { recorded = JSON.parse(scoreText) } catch (e) { recorded = null }
if (!recorded || typeof recorded.score !== 'number') {
  return { error: 'mutation_score.json was not relayed intact', detail: String(scoreText).slice(0, 200) }
}
const survivorsText = await loadFileViaPython('.methodology/mutation_survivors.json', '', 'Report')
let survivors = null
try { survivors = JSON.parse(survivorsText) } catch (e) { survivors = null }
const report = {
  score: recorded.score, killed: recorded.killed, survived: recorded.survived,
  threshold: THRESHOLD, paths_to_mutate: recorded.paths_to_mutate,
  survivor_count: survivors ? survivors.survivor_count : null,
  top_survivors: survivors && Array.isArray(survivors.survivors) ? survivors.survivors.slice(0, 5) : [],
  source: '.methodology/mutation_score.json (written by mutation-test-score)',
}
log('mutation score ' + recorded.score + '% (threshold ' + THRESHOLD + '%)')
if (recorded.score < THRESHOLD) {
  return Object.assign({ error: 'mutation score below the gate threshold' }, report)
}
return Object.assign({ status: 'PASS' }, report)
} catch (err) {
  const msg = (err && err.message) ? err.message : String(err)
  return {
    error: 'workflow crashed: ' + msg.slice(0, 300),
    workflow: meta.name,
    crashed: true,
    note: 'An agent dispatch threw instead of returning a result — most often a transient transport error, which the Workflow runtime does not retry or catch. Nothing was skipped silently: relaunch this workflow and its GUARD/sentinel checks short-circuit the work that already completed.',
  }
}
