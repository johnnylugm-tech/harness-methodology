// sim_runner.test.mjs — executes all 8 generated workflow files under the
// simulation testbed (Round 12 站1). First run of this testbed caught two
// LIVE migration regressions the Round-11 equivalence pins (meta.phases /
// agent labels / CLI-command sets) could not see:
//   - phase4: p4MidPushed/p4MidThreshold declarations dropped by station-3a
//     (ReferenceError the moment ≥50% FRs pass Gate 1);
//   - phase6: MAX_OUTER_ATTEMPTS declaration missing after station-4's A/B
//     unification injected the shared persistApproval (ReferenceError at the
//     first approval write — after peer review already passed).
// Both are pinned by dedicated regression tests below.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

import { runWorkflow, makeHappyResponder, nullResponder } from './sim_runner.mjs'

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..')
const WF = (name) => join(REPO, '.claude', 'workflows', name)

const PHASE_FILES = [
  'phase1-requirements.js',
  'phase2-architecture.js',
  'phase3-implementation.js',
  'phase4-testing.js',
  'phase5-verification.js',
  'phase6-quality.js',
  'phase7-risk.js',
  'phase8-config.js',
]

// ---- happy-path scenario pack ---------------------------------------------
// Text-verdict protocol tokens measured from the generated files (grep
// /[A-Z_]+:\s*PASS/): each override answers the exact string its call site
// regex-tests. Schema calls are answered by makeHappyResponder's synthesizer;
// the string overrides below are skipped for schema calls by the runner's
// foot-gun guard.
const LONG_REASON = 'Substantive peer-review justification for the simulated '
  + 'deliverable, well over one hundred characters long so the length gate '
  + 'passes cleanly.'
const PEER_JSON = JSON.stringify({
  verdicts: ['QUALITY_REPORT.md', 'RELEASE_NOTES.md', 'FINAL_SIGN_OFF.md', 'quality_manifest']
    .map((d) => ({ deliverable: d, review_status: 'APPROVE', reason: LONG_REASON, citations: [d + ':1'], gaps: [] })),
})
const B_JSON = JSON.stringify({
  review_status: 'APPROVE',
  reason: 'Substantive justification exceeding forty characters for the simulated review.',
  citations: ['SRS.md:1'],
  docs_embedded: ['SRS.md'],
  gaps: [],
})
const SBR_JSON = JSON.stringify({
  status: 'ok', review_status: 'APPROVE', gaps: [],
  escalation_action: 'approve', escalation_reason: '', b2_verification: 'pass',
})

function happyOverrides() {
  return [
    { match: /^preflight/, respond: 'PREFLIGHT: PASS' },
    { match: /^sync/, respond: 'SYNC: PASS' },
    // 'ADR-CONSTITUTION: PASS' also satisfies the unanchored
    // /CONSTITUTION:\s*PASS/ used by phase1 — one string, both call sites.
    { match: /^constitution/, respond: 'ADR-CONSTITUTION: PASS' },
    { match: /^advance/, respond: 'ADVANCE: PASS' },
    { match: /^push|^milestone|final-push/, respond: 'PUSH: PASS — MILESTONE: PASS' },
    { match: /fwdref|forward/, respond: 'FWDREF: PASS' },
    { match: /aci/, respond: '[check-artifact-consistency] OK — ACI: PASS' },
    { match: /sab/, respond: 'SAB: PASS' },
    { match: /^peer-review/, respond: 'Peer review complete.\n' + PEER_JSON },
    { match: /^sbr-/, respond: 'Validation done.\n' + SBR_JSON },
    { match: /^b-\d+-r/, respond: 'B review done.\n' + B_JSON },
    { match: /^persist|^write-approval/, respond: '[write-approval] OK — wrote approval JSON (137 bytes)' },
  ]
}

/** meta.phases titles declared at the top of a generated file. */
async function declaredPhases(file) {
  const src = await readFile(file, 'utf8')
  const metaBlock = src.slice(0, src.indexOf('\n}\n') + 3)
  return [...metaBlock.matchAll(/title:\s*'([^']+)'/g)].map((m) => m[1])
}

// ---- 1. happy path: every declared phase is reached, no error return ------
for (const name of PHASE_FILES) {
  test(`happy path — ${name} runs to completion through every declared phase`, async () => {
    const file = WF(name)
    const { result, events } = await runWorkflow(file, makeHappyResponder(happyOverrides()))
    assert.ok(result && typeof result === 'object', 'workflow must return an object')
    assert.equal(result.error, undefined,
      `no error on the happy path (got: ${JSON.stringify(result).slice(0, 200)})`)
    assert.equal(result.session_limit_blocked, undefined)
    assert.equal(result.__fell_through, undefined,
      'workflow must end via its own return statement, not fall off the end')
    const declared = await declaredPhases(file)
    assert.ok(declared.length >= 5, `meta.phases parse sanity (${declared.length})`)
    for (const title of declared) {
      assert.ok(events.phases.includes(title),
        `declared phase "${title}" was never reached (reached: ${events.phases.join(' → ')})`)
    }
    assert.equal(events.phases[0], declared[0], 'first phase reached = first declared')
  })
}

// ---- 2. null agent (session-limit / terminal API error shape) --------------
// The runtime returns null from agent() on session limits. Every file must
// degrade to a structured return — never a throw, never a fall-through.
for (const name of PHASE_FILES) {
  test(`null-agent — ${name} degrades to a structured error return`, async () => {
    const { result } = await runWorkflow(WF(name), nullResponder)
    assert.ok(result && typeof result === 'object')
    assert.ok(result.error || result.session_limit_blocked,
      `expected error/session_limit_blocked shape, got: ${JSON.stringify(result).slice(0, 200)}`)
  })
}

// ---- 3. hallucinated schema verdict (P3 GATE1 verify) ----------------------
// Pins CURRENT behavior after v2.13.3 (cef32c4): the deterministic helper
// prints GATE1_VERIFIED_PASS, but the workflow still ANDs the LLM's schema
// `pass` boolean — a hallucinated pass:false therefore still blocks the FR
// (the exact wf_53d055ce-d0b incident shape). Residual hallucination surface,
// recorded in docs/CONVERGENCE_AUDIT (Round 12 站2 candidate: derive the
// verdict from the echoed stdout alone).
test('phase3 GATE1 verify: hallucinated pass:false still blocks despite deterministic PASS stdout (pinned current behavior)', async () => {
  const overrides = [
    { match: /^gate1-verify-/, respond: { pass: false, reason: 'GATE1_VERIFIED_PASS' } },
    ...happyOverrides(),
  ]
  const { result } = await runWorkflow(WF('phase3-implementation.js'), makeHappyResponder(overrides))
  assert.ok(result.error, 'FR must be treated as failed under the AND-gate')
  assert.match(result.error, /Gate 1/i)
})

// ---- 4. garbage B response crashes the A/B machine (pinned weakness) -------
// parseAgentJson THROWS on JSON-less text; runSubTask's fallback
// `sbrResult.b2 || parseAgentJson(bResult, ...)` therefore escapes as a
// top-level crash instead of a graceful {error} return when BOTH the B agent
// and the structured_b_review validator return prose without JSON. Pinned
// as-is (behavior-equivalence discipline); the graceful-degrade fix is a
// Round 12 station-2 / audit-doc item, not a sim-testbed change.
test('phase1 A/B machine: JSON-less B + JSON-less validator crash the workflow (pinned current behavior)', async () => {
  // first-match-wins: these two shadow the happy pack's b-/sbr- entries.
  const overrides = [
    { match: /^b-\d+-r/, respond: 'I looked at the document and it seems fine to me.' },
    { match: /^sbr-/, respond: 'The validator could not produce output today.' },
    ...happyOverrides(),
  ]
  await assert.rejects(
    runWorkflow(WF('phase1-requirements.js'), makeHappyResponder(overrides)),
    /PARSE_FAIL/,
  )
})

// ---- 5. schema response missing required fields ----------------------------
test('phase8 preflight: schema reply missing `pass` degrades to a structured error return', async () => {
  const overrides = [{ match: /^preflight/, respond: {} }]
  const { result } = await runWorkflow(WF('phase8-config.js'), makeHappyResponder(overrides))
  assert.ok(result.error)
  assert.match(result.error, /preflight/i)
})

// ---- 6. regression pins for the two testbed-caught migration bugs ----------
test('phase4 regression pin: p4Mid declarations exist — mid-milestone branch executes without ReferenceError', async () => {
  // 3 FRs, fast-path passes none -> the full loop passes them one by one;
  // after FR-02 gate1Pass.length=2 ≥ ceil(3/2)=2 AND < 3, so the
  // p4MidPushed branch fires mid-loop (the branch only exists in the full
  // loop — the fast-path loop deliberately has no mid-milestone check).
  const frs = ['FR-01', 'FR-02', 'FR-03']
  const overrides = [
    { match: /^delta-fastpath$/, respond: { pass_fr_ids: [], fail_fr_ids: frs } },
    // CTX_SCHEMA only — ctx-check's VERDICT call falls through to the
    // synthesizer.
    { match: /^load-ctx/, respond: { fr_ids: frs, fr_count: frs.length } },
    ...happyOverrides(),
  ]
  const { result, events } = await runWorkflow(WF('phase4-testing.js'), makeHappyResponder(overrides))
  assert.equal(result.error, undefined, JSON.stringify(result).slice(0, 200))
  assert.ok(events.agents.some((a) => a.label === 'milestone-p4-mid'),
    'the ≥50% mid-milestone branch must actually dispatch (proves the declarations exist)')
})

test('phase6 regression pin: persistApproval executes without ReferenceError (MAX_OUTER_ATTEMPTS declared)', async () => {
  const { result, events } = await runWorkflow(WF('phase6-quality.js'), makeHappyResponder(happyOverrides()))
  assert.equal(result.error, undefined, JSON.stringify(result).slice(0, 200))
  assert.ok(events.agents.some((a) => a.label.startsWith('write-approval')),
    'approval writes must have been dispatched (the persistApproval body ran)')
})
