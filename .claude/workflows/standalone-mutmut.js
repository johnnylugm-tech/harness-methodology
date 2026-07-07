// Standalone Mutation Testing — independent mutmut validation.
//
// Purpose: Run harness-methodology's mutation_testing dimension in
// isolation, without driving the full 8-phase pipeline. Target raw kill
// rate ≥ 70% (per evaluation_dimension.md §mutation_testing Tier 1
// threshold). Equivalent mutants (per the same §Equivalent mutants
// block) are excluded from the denominator for an adjusted score.
//
// Source-of-truth for protocol:
//   harness/harness/ssi/prompts/evaluate_dimension.md §mutation_testing
//
// Hard rules (playbook §4):
// - NO fs.* / path.* / process.* / require() / import() / Date.now() /
//   Math.random() — runtime throws.
// - All file I/O goes through agent() with Bash tool.
// - REPO comes from args.repo or DEFAULT_REPO (playbook §5.7 fallback).
// - No `schema:` on agent() — use balanced-brace JSON parser (playbook
//   §5.2 / §8.4).
// - meta MUST be first statement, pure literal (playbook §3).

export const meta = {
  name: 'standalone-mutmut',
  description: 'Independent mutmut validation — run mutation-test-score, parse 🎉/🙁 counts, apply allowed exclusions, verify kill rate ≥ threshold',
  phases: [
    { title: 'Preflight' },
    { title: 'Mutation Testing' },
    { title: 'Report' },
  ],
}

// ---- args / REPO / PY ----
// REPO precedence: args.repo override wins, then DEFAULT_REPO canonical
// path. process.env.HARNESS_REPO cannot be read here — playbook §4 forbids
// process.* in workflow JS. Caller scripts inject via args.repo.
const DEFAULT_REPO = '.'
let REPO = DEFAULT_REPO
if (typeof args === 'string') { try { args = JSON.parse(args) } catch {} }
if (args && typeof args === 'object' && typeof args.repo === 'string' && args.repo.length > 0) REPO = args.repo
const PY = REPO + '/.venv/bin/python'

// ---- Defaults ----
const DEFAULT_THRESHOLD = 70.0
const MUTMUT_MAJOR_MAX = 2            // mutmut 3.x has trampoline bug — reject
const DEFAULT_EXCLUDE_PATHS = [       // pre-applied per evaluation_dimension.md
  '*/__init__.py',
  '*/__main__.py',
]
let threshold = DEFAULT_THRESHOLD
let mutmutTarget = null               // override setup.cfg paths_to_mutate
let excludeMutants = []               // mutant IDs to exclude (from args)
let excludePaths = DEFAULT_EXCLUDE_PATHS
let fast = false                       // shrink scope + 5min timeout
if (args && typeof args === 'object') {
  if (typeof args.threshold === 'number' && args.threshold > 0 && args.threshold <= 100) threshold = args.threshold
  if (typeof args.mutmut_target === 'string' && args.mutmut_target.length > 0) mutmutTarget = args.mutmut_target
  if (Array.isArray(args.exclude_mutants)) excludeMutants = args.exclude_mutants.filter(s => typeof s === 'string')
  if (Array.isArray(args.exclude_paths)) excludePaths = args.exclude_paths.filter(s => typeof s === 'string')
  if (args.fast === true) fast = true
}
log('REPO         = ' + REPO)
log('PY           = ' + PY)
log('threshold    = ' + threshold + '%')
log('mutmutTarget = ' + (mutmutTarget || '<from setup.cfg>'))
log('fast mode    = ' + fast)

// ---- J: WRITE SCOPE convention for LLM agent debug artifacts ----
const WRITE_SCOPE_TMP = REPO + '/.sessi-work/tmp'
log('WRITE SCOPE: debug artifacts → ' + WRITE_SCOPE_TMP)

// ---- JSON parsing (balanced-brace; playbook §5.2) ----
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
// Phase: Preflight — verify harness + mutmut + scope
// ════════════════════════════════════════════════════════════════════════
phase('Preflight')
log('Toolchain check: venv, mutmut 2.x, setup.cfg [mutmut], scope resolution')
const preflightReport = await agent(
  'YOU ARE THE MUTMUT PREFLIGHT CHECKER. Verify the toolchain; do NOT run mutation testing.\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\n\n'
  + 'Steps (use ONLY Bash tool; cat/grep/test):\n'
  + '1. VENV: `test -f ' + PY + ' && echo VENV_OK || echo VENV_MISSING`. If MISSING, FAIL.\n'
  + '2. MUTMUT-INSTALL: `' + PY + ' -m mutmut --version 2>&1`. Parse the version string. REQUIRE major version < ' + MUTMUT_MAJOR_MAX + ' (mutmut 3.x has trampoline bug per evaluation_dimension.md). If missing or >= 3.x: print "INSTALL: pip install \\"mutmut==2.5.1\\"" and FAIL.\n'
  + '3. SETUP-CFG: `grep -A 5 "^\\[mutmut\\]" ' + REPO + '/setup.cfg`. If missing, FAIL (no paths_to_mutate configured).\n'
  + '4. SCOPE-RESOLVE: extract paths_to_mutate= line. If `args.mutmut_target` was provided via prompt, use it instead; else use setup.cfg value. Print "SCOPE: <comma-separated paths>".\n'
  + '5. FILES-EXIST: for each path in resolved scope, run `test -f <REPO-relative path> && echo OK || echo MISSING:<path>`. Any MISSING → FAIL.\n'
  + '6. SOURCE-PRESENT (only if scope includes any path under `03-development/src/`): `test -d ' + REPO + '/03-development/src && echo SOURCE_OK || echo SOURCE_MISSING`. SOURCE_MISSING is FAIL for default scope (workflow needs FR implementations under `03-development/src/`).\n'
  + '7. PRIOR-RUN: if `find ' + REPO + ' -maxdepth 3 -name ".mutmut-cache" -type d 2>/dev/null | head -3` shows an existing cache, print "PRIOR-RUN: <path>" (informational; framework handles cache reuse).\n\n'
  + 'Return a JSON object on the LAST line of your output (no other JSON elsewhere):\n'
  + '{"preflight":"PASS","mutmut_version":"<x.y.z>","scope":["<path1>","<path2>"],"venv":"<path>","mutmut_executable":"<path>","prior_cache":"<path|null>","warnings":["<optional>"]}\n'
  + 'OR (on failure):\n'
  + '{"preflight":"FAIL","reason":"<short reason>","missing":["<items>"],"fix":"<one-line remediation>"}\n\n'
  + 'SCOPE RULES:\n- DO NOT run mutmut run / mutmut results / mutation-test-score.\n- DO NOT modify any files.\n- DO NOT modify harness/.\n- ONLY verify the toolchain + resolve scope.',
  { label: 'preflight', phase: 'Preflight', agentType: 'general-purpose' },
)

let preflight
try {
  preflight = parseAgentJson(preflightReport, 'preflight')
} catch (e) {
  return { error: 'Preflight parse failed', detail: e.message, raw: String(preflightReport ?? '').slice(-600) }
}
if (!preflight || preflight.preflight !== 'PASS') {
  return {
    error: 'Preflight FAILED',
    reason: (preflight && preflight.reason) || 'unknown',
    missing: (preflight && preflight.missing) || [],
    fix: (preflight && preflight.fix) || 'see preflight output',
    raw: String(preflightReport ?? '').slice(-400),
  }
}
log('Preflight PASS — mutmut ' + (preflight.mutmut_version || 'unknown') + ', scope: ' + JSON.stringify(preflight.scope))

// ════════════════════════════════════════════════════════════════════════
// Phase: Mutation Testing — run + parse + exclude + verify
// ════════════════════════════════════════════════════════════════════════
phase('Mutation Testing')
log('Run mutation-test-score (framework-owned) + parse 🎉/🙁 + apply exclusions + verify ≥ ' + threshold + '%')
const timeoutSec = fast ? 300 : 3600
const scopeOverride = mutmutTarget ? (' --paths-to-mutate ' + mutmutTarget) : ''
const mutReport = await agent(
  'YOU ARE THE MUTATION-TESTING ORCHESTRATOR. Run mutmut via the framework command and parse results.\n'
  + 'REPO: ' + REPO + '\nPYTHON: ' + PY + '\nSCOPE (resolved by preflight): ' + JSON.stringify(preflight.scope) + '\n'
  + 'TIMEOUT: ' + timeoutSec + 's\n'
  + (scopeOverride ? 'SCOPE OVERRIDE (from args.mutmut_target): ' + scopeOverride + '\n' : '')
  + 'EXCLUDE_MUTANTS: ' + JSON.stringify(excludeMutants) + '\n'
  + 'EXCLUDE_PATHS: ' + JSON.stringify(excludePaths) + '\n'
  + 'FAST MODE: ' + fast + '\n\n'
  + 'Steps:\n'
  + '1. RUN: `' + PY + ' ' + REPO + '/harness_cli.py mutation-test-score --project ' + REPO + (scopeOverride ? ' --paths-to-mutate ' + mutmutTarget : '') + ' --timeout ' + timeoutSec + ' 2>&1 | tee /tmp/mutation_score.json`. This is the framework-owned command — DO NOT run `mutmut run` directly (Bug #91, #105).\n'
  + '2. PARSE FRAMEWORK RESULT: read /tmp/mutation_score.json. Extract success (bool), score (number), message (string), cache_path (string).\n'
  + '3. IF success=false: print "MUTATION-RUN: FAIL — <message>" and return the JSON with status=BLOCKED. Do not continue to exclusions.\n'
  + '4. RUN `mutmut results` from cache_path. Parse the emoji-coded summary line(s):\n'
  + '   - 🎉 → killed (counter += 1)\n'
  + '   - 🙁 → survived (counter += 1)\n'
  + '   - ⏰ → timeout (counts as survived per evaluation_dimension.md)\n'
  + '   - 🤔 → suspicious (counts as survived per evaluation_dimension.md)\n'
  + '   Tolerate BOTH the emoji output AND legacy text output ("Killed:", "Survived:", "Timeout:").\n'
  + '5. If `mutmut results` is empty / no per-file summary, fall back to parsing the framework result: `killed = score × (killed + survived) / 100`, set survived to a marker `EXTRACT_FROM_CACHE` and continue (this signals "raw extraction was partial").\n'
  + '6. TOP SURVIVORS: for up to 5 surviving mutants, capture `file:line mutator_name`. This is what the developer will read first when fixing.\n'
  + '7. COMPUTE:\n'
  + '   raw_kill_rate = round(killed / max(killed + survived, 1) × 100, 1)\n'
  + '   For exclusions:\n'
  + '     a) Path-based: from (killed + survived), subtract any mutant whose file matches an EXCLUDE_PATHS glob. (If you cannot identify which mutants came from which paths, return raw_kill_rate = adjusted_kill_rate.)\n'
  + '     b) Mutant-ID-based: from survived, subtract any mutant in EXCLUDE_MUTANTS list.\n'
  + '     c) Logger-skip is handled by mutmut_config.py:pre_mutation — already excluded before mutmut counts them. No action here.\n'
  + '   adjusted_denominator = killed + survived − path_excluded − mutant_id_excluded\n'
  + '   adjusted_kill_rate = round(killed / max(adjusted_denominator, 1) × 100, 1)\n'
  + '8. VERIFY against threshold (' + threshold + '%):\n'
  + '   - If raw_kill_rate ≥ threshold: status = "PASS"\n'
  + '   - Else: status = "FAIL"\n'
  + '9. WRITE report to ' + REPO + '/.sessi-work/standalone_mutmut_report.json (overwrite if exists). Use Write tool. The JSON schema:\n'
  + '   {\n'
  + '     "timestamp": "<ISO8601>",\n'
  + '     "tool": "mutmut==<version>",\n'
  + '     "scope": {"configured": "<from setup.cfg or args>", "actual": ["<path1>", ...]},\n'
  + '     "raw": {"killed": <int>, "survived": <int>, "timeout": <int>, "suspicious": <int>, "kill_rate": <float>},\n'
  + '     "exclusions": {\n'
  + '       "logger_calls_skipped": "framework-auto (mutmut_config.py:pre_mutation)",\n'
  + '       "path_excluded": [{"pattern":"<glob>","count":<int>}],\n'
  + '       "mutant_id_excluded": [{"id":"<id>","reason":"<string|null>"}]\n'
  + '     },\n'
  + '     "adjusted": {"killed": <int>, "survived": <int>, "excluded": <int>, "kill_rate": <float>},\n'
  + '     "threshold": <float>,\n'
  + '     "status": "PASS"|"FAIL",\n'
  + '     "top_survivors": [{"file":"<rel>","line":<int>,"mutator":"<name>"}],\n'
  + '     "details": "<1-2 line summary>"\n'
  + '   }\n'
  + '10. Return on the LAST line of output:\n'
  + '   {"status":"PASS","raw_kill_rate":<float>,"adjusted_kill_rate":<float>,"killed":<int>,"survived":<int>,"excluded":<int>,"report_path":"<abs path>","top_survivor_count":<int>}\n'
  + '   OR on failure:\n'
  + '   {"status":"FAIL","reason":"<short>","raw_kill_rate":<float>,"adjusted_kill_rate":<float>,"report_path":"<abs path>"}\n\n'
  + 'SCOPE RULES:\n- DO NOT run Gate 1/2/3/4 — this workflow is mutation-only.\n- DO NOT modify setup.cfg (mutmut scope comes from pre-resolved value).\n- DO NOT modify harness/ (HR-17).\n- DO NOT run advance-phase / git tag / run-phase.\n- ONLY run mutation-test-score, parse results, write the report JSON, return summary.',
  { label: 'mutation-run', phase: 'Mutation Testing', agentType: 'general-purpose' },
)

let mutResult
try {
  mutResult = parseAgentJson(mutReport, 'mutation-run')
} catch (e) {
  return { error: 'Mutation run parse failed', detail: e.message, raw: String(mutReport ?? '').slice(-600) }
}
if (!mutResult || (mutResult.status !== 'PASS' && mutResult.status !== 'FAIL')) {
  return {
    error: 'Mutation run did not return a valid status',
    raw: String(mutReport ?? '').slice(-600),
  }
}
log('  raw=' + mutResult.raw_kill_rate + '%  adjusted=' + mutResult.adjusted_kill_rate + '%  killed=' + mutResult.killed + '  survived=' + mutResult.survived + '  excluded=' + mutResult.excluded)
log('  status: ' + mutResult.status)

// ════════════════════════════════════════════════════════════════════════
// Phase: Report — return consolidated result
// ════════════════════════════════════════════════════════════════════════
phase('Report')

const finalReport = {
  tool: 'mutmut 2.x (framework-owned path: mutation-test-score)',
  repo: REPO,
  scope: preflight.scope,
  threshold: threshold,
  raw: {
    killed: mutResult.killed,
    survived: mutResult.survived,
    kill_rate: mutResult.raw_kill_rate,
  },
  adjusted: {
    killed: mutResult.killed,
    survived: mutResult.survived,
    excluded: mutResult.excluded,
    kill_rate: mutResult.adjusted_kill_rate,
  },
  report_path: mutResult.report_path,
  status: mutResult.status,
  top_survivor_count: (typeof mutResult.top_survivor_count === 'number') ? mutResult.top_survivor_count : 0,
}

if (mutResult.status !== 'PASS') {
  log('MUTATION: FAIL — raw=' + mutResult.raw_kill_rate + '% < threshold=' + threshold + '%')
  return {
    error: 'Mutation kill rate below threshold',
    ...finalReport,
    detail: 'raw_kill_rate ' + mutResult.raw_kill_rate + '% < threshold ' + threshold + '%. See ' + mutResult.report_path + ' for top survivors.',
  }
}

log('MUTATION: PASS — raw=' + mutResult.raw_kill_rate + '% adjusted=' + mutResult.adjusted_kill_rate + '%')
return finalReport