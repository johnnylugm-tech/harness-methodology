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

// ---- 3. hallucinated schema verdict (GATE1 verify) --------------------------
// Round 12 站2a: the verdict is derived from the echoed deterministic stdout
// ONLY. A hallucinated schema pass:false can no longer veto a PASS manifest
// (wf_53d055ce-d0b incident shape — v2.13.3's prose claimed this but its code
// still ANDed the boolean; the sim pinned that contradiction and 站2a fixed
// it). Covers phase3 (TDD loop) and phase5 (render_per_fr_delta family, the
// P4/P5/P7/P8 shared renderer).
for (const name of ['phase3-implementation.js', 'phase5-verification.js']) {
  test(`${name} GATE1 verify: hallucinated pass:false is ignored — deterministic PASS stdout wins`, async () => {
    const overrides = [
      { match: /^gate1-verify-/, respond: { pass: false, reason: 'GATE1_VERIFIED_PASS' } },
      ...happyOverrides(),
    ]
    const { result } = await runWorkflow(WF(name), makeHappyResponder(overrides))
    assert.equal(result.error, undefined,
      `FR must pass on the deterministic stdout (got: ${JSON.stringify(result).slice(0, 200)})`)
  })
}

// Inverse guard: a hallucinated pass:true cannot rescue a FAIL stdout.
test('phase3 GATE1 verify: hallucinated pass:true cannot rescue a deterministic FAIL stdout', async () => {
  const overrides = [
    { match: /^gate1-verify-/, respond: { pass: true, reason: 'GATE1_VERIFIED_FAIL score=71.4' } },
    ...happyOverrides(),
  ]
  const { result } = await runWorkflow(WF('phase3-implementation.js'), makeHappyResponder(overrides))
  assert.ok(result.error, 'FR must fail on the deterministic FAIL stdout')
  assert.match(result.error, /Gate 1/i)
})

// ---- 3b. [HARNESS-BUG] banner (Round 13 站0 crash boundary) ----------------
// A sub-agent whose GATE1 log contains harness_cli.py's new [HARNESS-BUG]
// banner (core/errors.py — an uncaught exception in harness code, NOT a
// project quality problem) must abort the FR loop with harness_bug_detected,
// not fall through to the deterministic-verdict read as an ordinary FAIL.
test('phase3 TDD loop: [HARNESS-BUG] banner in the GATE1 log aborts the FR loop (not a code-quality FAIL)', async () => {
  const overrides = [
    { match: /^tdd-/, respond: 'FR-01 GATE1: FAIL — harness-methodology bug detected, escalate to human (see [HARNESS-BUG] message and its crash bundle path)' },
    ...happyOverrides(),
  ]
  const { result } = await runWorkflow(WF('phase3-implementation.js'), makeHappyResponder(overrides))
  assert.equal(result.harness_bug_detected, true, JSON.stringify(result).slice(0, 200))
  assert.match(result.message, /harness-methodology itself crashed/)
})

// ---- 3c. Sync verdict FAIL branch (Round 28) -------------------------------
// render_sync_verified()'s regex early-return was zero-covered: every happy
// pack answers 'SYNC: PASS' (line 62 above), so no test ever exercised the
// FAIL path — for any of the 6 phases that already used it, and now P8
// (Round 28 migrated P8 off the unconditional render_sync(), which had no
// verdict check at all — the one phase-exit push in the pipeline with no
// safety net). phase7 is the simplest consumer (Sync is its last phase box).
test('phase7 Sync: a FAIL verdict early-returns an error (post-advance push did not PASS)', async () => {
  const overrides = [
    { match: /^sync$/, respond: 'SYNC: FAIL — simulated push rejection' },
    ...happyOverrides(),
  ]
  const { result } = await runWorkflow(WF('phase7-risk.js'), makeHappyResponder(overrides))
  assert.ok(result.error, 'a Sync FAIL must produce a structured error, not a silent pass-through')
  assert.match(result.error, /post-advance push did not PASS/)
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

// ---- 6. Gate 2/3/4 PASS semantics (Round 18 站4) ---------------------------
// render_gate_loop's verdict was zero-covered here: the precheck's
// VERDICT_SCHEMA call falls through to the happy synthesizer's {pass:true},
// so every prior test skipped the round loop entirely and never exercised
// what makes a phase-exit gate PASS. That blind spot is exactly where
// 9b5f7cf shipped a precheck keyed on manifest.quality_complete — a flag
// written from the SSI dimension score BEFORE finalize-gate's Phase Truth
// check runs, and never reverted — which 64d8ea9 had to replace two commits
// later with state.json.last_gate. --check, the golden fixtures, and the
// 52-test workflowgen suite were all green for both versions: they compare
// generator output to generator output and cannot see a wrong premise.
const EXIT_GATES = [
  ['phase3-implementation.js', 2],
  ['phase4-testing.js', 3],
  ['phase6-quality.js', 4],
]

for (const [name, gate] of EXIT_GATES) {
  const precheck = new RegExp(`^gate${gate}-precheck$`)
  const verify = new RegExp(`^gate${gate}-verify-r`)
  const roundAgent = new RegExp(`^gate${gate}-r\\d`)

  test(`${name} Gate ${gate}: pre-flight PASS skips the round loop`, async () => {
    const overrides = [
      { match: precheck, respond: { pass: true, reason: 'last_gate >= N' } },
      ...happyOverrides(),
    ]
    const { events } = await runWorkflow(WF(name), makeHappyResponder(overrides))
    assert.equal(events.agents.filter((a) => roundAgent.test(a.label)).length, 0,
      `the round loop re-dispatched despite a finalized gate — the repeated `
      + `round-1 respawn 9b5f7cf exists to prevent`)
  })

  test(`${name} Gate ${gate}: pre-flight FAIL enters the round loop`, async () => {
    const overrides = [
      { match: precheck, respond: { pass: false, reason: 'not finalized' } },
      { match: verify, respond: { last_gate_ok: true, d4_rc: 0, detail: 'sim' } },
      ...happyOverrides(),
    ]
    const { result, events } = await runWorkflow(WF(name), makeHappyResponder(overrides))
    assert.ok(events.agents.some((a) => roundAgent.test(a.label)),
      'an unfinalized gate must still run its orchestrator round')
    assert.equal(result.error, undefined, JSON.stringify(result).slice(0, 200))
    assert.ok(events.logs.some((l) => l.includes(`Gate ${gate} PASS`)),
      'last_gate_ok + d4_rc=0 is the PASS condition')
  })

  test(`${name} Gate ${gate}: last_gate_ok=false cannot PASS (the 9b5f7cf premise)`, async () => {
    const overrides = [
      { match: precheck, respond: { pass: false, reason: 'not finalized' } },
      // Phase Truth still blocking: the SSI dims may well have scored, which
      // is precisely the state manifest.quality_complete reported as done.
      { match: verify, respond: { last_gate_ok: false, d4_rc: 0, detail: 'phase truth 69%' } },
      ...happyOverrides(),
    ]
    const { result, events } = await runWorkflow(WF(name), makeHappyResponder(overrides))
    assert.ok(!events.logs.some((l) => l.includes(`Gate ${gate} PASS`)),
      `Gate ${gate} reported PASS while state.json.last_gate says it was never `
      + `finalized — advance-phase would then hit the same block downstream`)
    assert.ok(result.error, 'a gate that never passes must return a structured error')
  })

  test(`${name} Gate ${gate}: a nonzero D4 exit code cannot PASS`, async () => {
    const overrides = [
      { match: precheck, respond: { pass: false, reason: 'not finalized' } },
      { match: verify, respond: { last_gate_ok: true, d4_rc: 1, detail: 'spec-coverage below threshold' } },
      ...happyOverrides(),
    ]
    const { result, events } = await runWorkflow(WF(name), makeHappyResponder(overrides))
    assert.ok(!events.logs.some((l) => l.includes(`Gate ${gate} PASS`)),
      'spec-coverage-check failing must veto the gate even when last_gate is set')
    assert.ok(result.error)
  })
}

// ---- 7. P2 peer-review approval is reported (Round 18 站4) -----------------
// 83ed438: the APPROVE branch broke out of the round loop without ever
// assigning peerReviewPassed, so the post-loop "Peer Review PASS (APPROVE)"
// line never printed even on a real approval. Gating was unaffected (callers
// branch on peerB2.review_status), which is why nothing caught it — the only
// symptom was a run log that misreported its own outcome.
test('phase2 peer review: an approved review actually logs PASS', async () => {
  const { result, events } = await runWorkflow(
    WF('phase2-architecture.js'), makeHappyResponder(happyOverrides()))
  assert.equal(result.error, undefined, JSON.stringify(result).slice(0, 200))
  assert.ok(events.logs.some((l) => l.includes('Peer Review PASS')),
    'escalation_action=approve must set peerReviewPassed — otherwise the run '
    + 'log claims no approval happened while the workflow proceeds as if it did')
})
