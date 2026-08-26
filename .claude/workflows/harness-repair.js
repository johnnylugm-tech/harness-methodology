// harness-repair — fix a defect in harness-methodology itself
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/spec_repair.py. Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write
//
// Launched when `harness_cli.py record-block` classifies a halt as
// owner=harness (core/fault_owner.py). It is NOT launched for owner=unknown:
// "could not prove the project's fault, so go edit the framework" would hand a
// repair agent a standing motive to change the judge.
//
// Every verdict here comes from a harness CLI exit code, never from the
// agent's prose — same rule the eight phase workflows follow, and it matters
// more here because the agent is editing the thing that produces the verdict.


export const meta = {
  name: 'harness-repair',
  description: 'Repair a harness-methodology defect: reproduce, root-cause, adversarially review, fix, counter-prove, self-gate, push',
  phases: [
    { title: 'Ticket' },
    { title: 'Reproduce' },
    { title: 'Root Cause' },
    { title: 'Adversarial Review' },
    { title: 'Fix' },
    { title: 'Land' },
    { title: 'Handover' },
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

// Round 79: env-fp cache state. Declared ABOVE dispatch() so the first call from
// resolveRepo() sees an initialised binding; `let` does not hoist, and `var` is
// not allowed in this module's strict-ish lint profile.
let ENV_FP = null

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
  // Round 79: env-fp cache-buster — fold the live project fingerprint into the
  // prompt so a fresh run after AAP amendment / git commit forces cache invalidation
  // for every agent that consults project state (delta-*, gate1-verify-*, …).
  // The tag is prepended OUTSIDE the bookkeeping preamble so the bookkeeping
  // command itself stays at line 1 and the agent sees a one-line header it can
  // safely ignore; env-fp-init (the only label that does not depend on the
  // fingerprint) skips the tag to avoid a chicken-and-egg loop.
  let fpTag = ''
  if (label !== 'env-fp-init') {
    const fp = await getEnvFingerprint()
    const sab = (fp && fp.sab_sha) || 'none'
    const head = (fp && fp.git_head) || 'none'
    fpTag = '[env-fp SAB=' + sab.slice(0, 12) + ' HEAD=' + head.slice(0, 12) + '] '
  }
  let res
  try {
    res = await agent(fpTag + __dispatchFlushPreamble() + prompt, opts)
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
// Round 79: env-fp — workflow-level cache-buster. The runtime cache keys on
// (prompt, opts) and persists across `Workflow({scriptPath, resumeFromRunId})`
// calls, so an `infra_abort` followed by AAP amendment (which only mutates the
// project tree, never the prompt text) replays the stale RC=25 from cache and
// loops the same halt. Tagging every dispatch with `[env-fp SAB=… HEAD=…]`
// folds the live environment state into the cache key without changing any
// phase template, INFRA-abort behaviour, or scope rule — the tag is invisible
// to the agent (it lives in the bookkeeping preamble) and to the workflow
// routing (it doesn't match any VERDICT_SCHEMA field). One env-fp-init
// dispatch computes the fingerprint on first use and caches it module-local,
// so subsequent dispatches in the same run take no extra agent call.
const ENV_FP_SCHEMA = {
  type: 'object',
  properties: {
    sab_sha: { type: 'string', description: 'git hash-object of .methodology/SAB.json, or "none" if missing/unreadable' },
    git_head: { type: 'string', description: 'git rev-parse HEAD short sha, or "none" if missing' },
  },
  required: ['sab_sha', 'git_head'],
}
// `let ENV_FP = null` is declared above dispatch() (search `let ENV_FP`) so the
// first call from resolveRepo() sees an initialised binding instead of TDZ.
async function getEnvFingerprint() {
  if (ENV_FP !== null) return ENV_FP
  try {
    const r = await dispatch(
      'You MUST use the Bash tool. Run EXACTLY these two commands in order, each on its own line:\n'
      + '  git -C ' + REPO + ' hash-object ' + REPO + '/.methodology/SAB.json 2>/dev/null || echo none\n'
      + '  git -C ' + REPO + ' rev-parse HEAD 2>/dev/null || echo none\n'
      + 'Report via the StructuredOutput tool exactly: { sab_sha: <line1 stripped>, git_head: <line2 stripped> }. Use "none" verbatim if a command printed "none".',
      { label: 'env-fp-init', phase: 'Phase Cursor', agentType: 'general-purpose', schema: ENV_FP_SCHEMA }
    )
    ENV_FP = {
      sab_sha: (r && typeof r.sab_sha === 'string' && r.sab_sha.length > 0) ? r.sab_sha : 'none',
      git_head: (r && typeof r.git_head === 'string' && r.git_head.length > 0) ? r.git_head : 'none',
    }
  } catch (e) {
    ENV_FP = { sab_sha: 'none', git_head: 'none' }
  }
  return ENV_FP
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


// ---- ticket path (args.ticket) ----
// No default. A repair with no ticket has no reproduction command and no block
// signature, so it has nothing to verify and nothing to close.
if (typeof args === 'string') { try { args = JSON.parse(args) } catch {} }
const TICKET = (args && typeof args === 'object' && typeof args.ticket === 'string') ? args.ticket : ''
if (!TICKET) {
  return { error: 'harness-repair: args.ticket is required (the path harness_cli.py record-block wrote)', note: 'Launch with Workflow({ scriptPath, args: { repo, ticket } }).' }
}
// The harness checkout being repaired. Submodule layout in every live project;
// the repo itself when harness is dogfooding on itself.
const HROOT = REPO + '/harness'
log('ticket = ' + TICKET)


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


// ══════════════════════════════════════════════════════════════════════════
// Phase: Ticket
// ══════════════════════════════════════════════════════════════════════════

phase('Ticket')
log('Read the repair ticket and confirm it names harness as the owner')
const ticketReport = await dispatch(
  'You MUST use the Bash tool. Run exactly:\n'
  + '`cat ' + TICKET + '`\n'
  + 'Then report via the StructuredOutput tool: pass = true ONLY if the JSON has an \"owner\" of \"harness\" AND a non-empty \"repro\" string; reason = the ticket\u2019s signature, phase, step and repro command, transcribed verbatim.',
  { label: 'repair-ticket', phase: 'Ticket', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
)
if (!(ticketReport && ticketReport.pass === true)) {
  return { error: 'harness-repair: the ticket does not name harness as the owner, or carries no repro command', reason: ticketReport ? String(ticketReport.reason ?? '').slice(-400) : 'agent returned null', note: 'Only owner=harness blocks route here. A project-owned block belongs in the ordinary fix loop; an unknown-owner block stops and is recorded, not repaired.' }
}
log('  ticket: ' + String(ticketReport.reason ?? '').slice(0, 200))


// ══════════════════════════════════════════════════════════════════════════
// Phase: Reproduce
// ══════════════════════════════════════════════════════════════════════════

phase('Reproduce')
log('Reality first: the reported failure must reproduce on this tree')
const reproCmd = PY + ' ' + HROOT + '/harness_cli.py repair-harness --project ' + REPO + ' --ticket ' + TICKET + ' --check-repro'
const reproRc = await dispatch(
  'You MUST use the Bash tool. Run exactly:\n`' + reproCmd + '; echo RC=$?`\n'
  + 'Report via the StructuredOutput tool: rc = the EXACT integer after RC=. Do not interpret the output, do not fix anything, do not retry.',
  { label: 'repair-repro', phase: 'Reproduce', agentType: 'general-purpose', schema: RC_SCHEMA },
)
if (!(reproRc && reproRc.rc === 0)) {
  return { error: 'harness-repair: the reported failure did not reproduce, or the submodule could not be prepared', rc: reproRc ? reproRc.rc : null, note: 'A report is a claim; the reproduction is the evidence. Nothing in harness is edited on the strength of a report alone. This step also returns the submodule to main while the tree is still clean, and refuses if it is off main WITH uncommitted edits — read the printed refusal: it says which of the two happened.' }
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Root Cause
// ══════════════════════════════════════════════════════════════════════════

phase('Root Cause')
log('Locate the defect in harness — hypothesis only, no edits yet')
const hypothesis = await dispatch(
  'YOU ARE THE HARNESS ROOT-CAUSE ANALYST. A block was attributed to harness-methodology itself.\n'
  + 'HARNESS CHECKOUT: ' + HROOT + '\nTICKET: ' + TICKET + '\nPYTHON: ' + PY + '\n\n'
  + 'Read the ticket, then read the harness code the failing command runs. Reproduce it yourself if that helps you localise it.\n'
  + 'Produce, in prose:\n'
  + '  1. The exact file and line where harness does the wrong thing.\n'
  + '  2. WHY it is wrong — the property that is violated, not the symptom.\n'
  + '  3. The smallest change that would make it right, and which existing test would have caught it.\n'
  + '  4. Whether the defect is in a GENERATED file (.claude/workflows/*.js). If so, name the scripts/workflowgen/ source that produces it — that is what gets edited.\n\n'
  + 'DO NOT EDIT ANYTHING in this step. A hypothesis that has not been challenged is not a diagnosis.\n'
  + 'STANDING RULES (the land step enforces all four; breaking one wastes the round):\n'
  + '  R1. NEVER edit .claude/workflows/*.js. They are generated. Edit the matching\n'
  + '      scripts/workflowgen/spec_*.py or js_blocks.py, then run\n'
  + '      `python3 scripts/workflowgen/generate_workflows.py --write`.\n'
  + '  R2. NEVER edit harness/gate_configs/*.yaml. Those are thresholds every\n'
  + '      enforcer shares, including CI. Lowering one is not a fix.\n'
  + '  R3. NEVER remove an entry from tests/REGRESSION_GUARDS.yaml. Guards only grow.\n'
  + '  R4. The fix must make the reproduction pass AND stay necessary: reverting it\n'
  + '      must turn the reproduction red again. A change that is not load-bearing\n'
  + '      did not fix anything.\n'
  ,
  { label: 'repair-hypothesis', phase: 'Root Cause', agentType: 'general-purpose' },
)
const hypothesisText = String(hypothesis ?? '').slice(0, 6000)
if (!hypothesisText.trim()) {
  return { error: 'harness-repair: the root-cause step produced nothing', note: 'No diagnosis means no fix. Re-launch, or escalate the ticket to a human.' }
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Adversarial Review
// ══════════════════════════════════════════════════════════════════════════

phase('Adversarial Review')
log('Challenge the hypothesis before any edit lands')
const review = await dispatch(
  'YOU ARE THE ADVERSARIAL REVIEWER. Another agent diagnosed a harness-methodology defect. Your job is to try to falsify the diagnosis, not to confirm it.\n'
  + 'HARNESS CHECKOUT: ' + HROOT + '\n\nTHE DIAGNOSIS:\n' + hypothesisText + '\n\n'
  + 'Check, by reading the code and running commands yourself:\n'
  + '  - Is the named line actually reached by the failing command? If not, the diagnosis is wrong.\n'
  + '  - Does the proposed change fix the CAUSE, or only silence the symptom the ticket reported?\n'
  + '  - Would the change alter a verdict for projects that are NOT failing? Name them if so.\n'
  + '  - Is the defect really in harness, or is the project at fault after all?\n\n'
  + 'Report via the StructuredOutput tool: pass = true ONLY if the diagnosis survives all four questions; reason = your findings, and when pass=false, what the diagnosis got wrong.',
  { label: 'repair-review', phase: 'Adversarial Review', agentType: 'general-purpose', schema: VERDICT_SCHEMA },
)
if (!(review && review.pass === true)) {
  return { error: 'harness-repair: the diagnosis did not survive adversarial review', reason: review ? String(review.reason ?? '').slice(-800) : 'reviewer returned null', note: 'Stopping here is the cheap outcome. A rejected diagnosis costs one round; an unchallenged one that lands on main costs every project that tracks it.' }
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Fix
// ══════════════════════════════════════════════════════════════════════════

phase('Fix')
phase('Land')
phase('Fix')
const MAX_REPAIR_ROUNDS = 3
let landed = false
let lastLand = null
for (let round = 1; round <= MAX_REPAIR_ROUNDS && !landed; round++) {
  log('repair round ' + round + '/' + MAX_REPAIR_ROUNDS)
  await dispatch(
    'YOU ARE THE HARNESS FIXER. Apply the reviewed diagnosis to the harness checkout.\n'
    + 'HARNESS CHECKOUT: ' + HROOT + '\nPYTHON: ' + PY + '\n\n'
    + 'THE DIAGNOSIS (already survived adversarial review):\n' + hypothesisText + '\n\n'
    + (lastLand ? 'THE PREVIOUS LAND ATTEMPT WAS REFUSED. Its output is below — read it as the fix instruction and address exactly what it names:\n' + lastLand + '\n\n' : '')
    + 'Make the change. Add or extend a test that fails without it. Do not commit — the next step commits, and only if it can prove the fix is load-bearing and the self-gate is green.\n\n'
  + 'STANDING RULES (the land step enforces all four; breaking one wastes the round):\n'
  + '  R1. NEVER edit .claude/workflows/*.js. They are generated. Edit the matching\n'
  + '      scripts/workflowgen/spec_*.py or js_blocks.py, then run\n'
  + '      `python3 scripts/workflowgen/generate_workflows.py --write`.\n'
  + '  R2. NEVER edit harness/gate_configs/*.yaml. Those are thresholds every\n'
  + '      enforcer shares, including CI. Lowering one is not a fix.\n'
  + '  R3. NEVER remove an entry from tests/REGRESSION_GUARDS.yaml. Guards only grow.\n'
  + '  R4. The fix must make the reproduction pass AND stay necessary: reverting it\n'
  + '      must turn the reproduction red again. A change that is not load-bearing\n'
  + '      did not fix anything.\n'
    ,
    { label: 'repair-fix-r' + round, phase: 'Fix', agentType: 'general-purpose' },
  )
  const landCmd = PY + ' ' + HROOT + '/harness_cli.py repair-harness --project ' + REPO + ' --ticket ' + TICKET + ' --land --push'
  const landRc = await dispatch(
    'You MUST use the Bash tool. Run exactly:\n`' + landCmd + ' 2>&1; echo RC=$?`\n'
    + 'This command does the verifying: it stashes the fix, re-runs the reproduction (which must fail without the fix), restores it, checks the repair policy, runs the six-check self-gate, then commits and pushes.\n'
    + 'Report via the StructuredOutput tool: rc = the EXACT integer after RC=. Do NOT edit anything in this step, do NOT retry, and do NOT interpret a nonzero rc as success.',
    { label: 'repair-land-r' + round, phase: 'Land', agentType: 'general-purpose', schema: RC_SCHEMA },
  )
  if (landRc && landRc.rc === 0) { landed = true; break }
  lastLand = 'rc=' + (landRc ? landRc.rc : 'null')
  log('  land refused (rc=' + (landRc ? landRc.rc : 'null') + ') — feeding the refusal back into the next round')
}
if (!landed) {
  return { error: 'harness-repair: the fix did not land in ' + MAX_REPAIR_ROUNDS + ' rounds', last: lastLand, note: 'Nothing was pushed. The harness checkout still holds the attempted fix — inspect it, or `git -C <repo>/harness checkout -- .` to discard. A human decides from here; the loop deliberately does not spend a fourth round on a refusal it has already failed to clear three times (Round 41 站3).' }
}


// ══════════════════════════════════════════════════════════════════════════
// Phase: Handover
// ══════════════════════════════════════════════════════════════════════════

phase('Handover')
log('Fix pushed to harness-methodology main — the project must now move its submodule pointer')
return {
  workflow: 'harness-repair',
  repaired: true,
  ticket: TICKET,
  next: [
    'git -C ' + REPO + '/harness pull --ff-only origin main',
    'git -C ' + REPO + ' add harness && git -C ' + REPO + ' commit -m "chore(harness): bump submodule past repair"',
    'relaunch run-all — the block that stopped it is recorded as resolved, and run-all re-checks it rather than trusting this claim',
  ],
  note: 'The repair marked its block resolved in .methodology/workflow_blocks.jsonl. If the same coordinate blocks again on the next run, the fix did not hold — that is checked, not assumed.',
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
