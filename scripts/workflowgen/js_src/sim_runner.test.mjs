// sim_runner.test.mjs — executes all 9 generated workflow files (the 8 phases
// and run-all.js) under the simulation testbed (Round 12 站1; run-all joined in
// Round 27 站6). First run of this testbed caught two
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

import { runWorkflow, makeHappyResponder, nullResponder, throwingResponder } from './sim_runner.mjs'

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

// Round 27 站6: run-all.js joins the scenarios that sweep every workflow. It was
// absent for its whole existence, which is backwards — it is the one file an
// unattended run actually executes, and the eight above are its inputs. The
// 2026-07-30 incident (a transient transport error 83 dispatches / 3h into a
// run, taking the whole thing down) happened inside a file no scenario here had
// ever loaded; the fix commit's own words were "Confirmed sim_runner.test.mjs
// never exercised run-all.js at all".
//
// Kept separate from PHASE_FILES rather than appended to it: the dispatch-count
// spec below compares run-all's total against the sum of the EIGHT, and folding
// run-all into that list would have it compared against itself.
const ALL_WORKFLOW_FILES = [...PHASE_FILES, 'run-all.js']

// run-all reads state.json's cursor before it does anything else, so a sweep
// that wants it to actually execute phases has to answer that read.
const sweepOverrides = (name) =>
  name === 'run-all.js' ? [cursorAt(1), ...happyOverrides()] : happyOverrides()

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
    // persistApproval/writeApprovalJson are schema calls on all three callers
    // (phase1/phase2/phase6) now — a plain-string respond is silently
    // skipped by the schema-call guard above (line ~110 in sim_runner.mjs),
    // which used to fall through to the generic VERDICT_SCHEMA synthesizer's
    // `{ pass: true, reason: 'GATE1_VERIFIED_PASS' }`. That was invisible
    // while the workflow trusted `res.pass` (also true), but the fix for
    // the wf_53d055ce-d0b-class bug (2026-08-01) checks `res.reason` for
    // the literal `[write-approval] OK` string instead, so the mock must
    // actually carry it there.
    { match: /^persist|^write-approval/, respond: { pass: true, reason: '[write-approval] OK — wrote approval JSON (137 bytes)' } },
  ]
}

/** meta.phases titles declared at the top of a generated file. */
async function declaredPhases(file) {
  const src = await readFile(file, 'utf8')
  const metaBlock = src.slice(0, src.indexOf('\n}\n') + 3)
  return [...metaBlock.matchAll(/title:\s*'([^']+)'/g)].map((m) => m[1])
}

// ---- 1. happy path: every declared phase is reached, no error return ------
for (const name of ALL_WORKFLOW_FILES) {
  test(`happy path — ${name} runs to completion through every declared phase`, async () => {
    const file = WF(name)
    const { result, events } = await runWorkflow(file, makeHappyResponder(sweepOverrides(name)))
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
for (const name of ALL_WORKFLOW_FILES) {
  test(`null-agent — ${name} degrades to a structured error return`, async () => {
    const { result } = await runWorkflow(WF(name), nullResponder)
    assert.ok(result && typeof result === 'object')
    assert.ok(result.error || result.session_limit_blocked,
      `expected error/session_limit_blocked shape, got: ${JSON.stringify(result).slice(0, 200)}`)
  })
}

// ---- 2b. run-all.js top-level boundary — a thrown agent() must degrade to a
// structured error, never an unhandled rejection. loadFileViaPython's retry
// loop already caught this class of failure in one call site (the 2026-07-30
// incident: 83 dispatches / 3h in, a transient "Connection closed mid-response"
// crashed the whole unattended run). run-all.js's OWN phase-driver loop
// (_render_driver() in spec_runall.py) had no equivalent boundary — any
// uncaught throw from ANY call site inside a phase, known or future, still
// crashed the entire script. When this test was written it was the ONLY thing
// here that loaded run-all.js; Round 27 站6 added the file to the happy-path and
// null-agent sweeps as well, so this case is now the specific one, not the only
// one.
test('run-all: phase-cursor agent() throwing degrades to a structured error, not a rejected promise', async () => {
  const overrides = [
    { match: /^phase-cursor$/, respond: () => { throw new Error('API Error: Connection closed mid-response.') } },
  ]
  const { result } = await runWorkflow(WF('run-all.js'), makeHappyResponder(overrides))
  assert.ok(result && typeof result === 'object', 'must resolve, not reject')
  assert.ok(result.error, `expected a structured error return, got: ${JSON.stringify(result).slice(0, 200)}`)
})

test('run-all: an uncaught throw from inside a phase degrades to a structured error, not a crash', async () => {
  // Targets phase1's one-shot ENTRY-CHECK/PREFLIGHT dispatch (label 'preflight')
  // rather than the A/B loop's 'a-1-r1' — that loop already has its own
  // try/catch (runSubTask, js_blocks.py) and absorbs a single-round throw by
  // retrying, so it never reaches the boundary this test is pinning. The
  // one-shot preflight call has no such protection.
  const overrides = [
    { match: /^phase-cursor$/, respond: { current_phase: 1 } },
    { match: /^preflight/, respond: () => { throw new Error('API Error: Connection closed mid-response.') } },
    ...happyOverrides(),
  ]
  const { result } = await runWorkflow(WF('run-all.js'), makeHappyResponder(overrides))
  assert.ok(result && typeof result === 'object', 'must resolve, not reject')
  assert.ok(result.error, `expected a structured error return, got: ${JSON.stringify(result).slice(0, 200)}`)
})

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
    { match: /^tdd-/, respond: 'FR-01 GATE1: FAIL — harness-methodology itself crashed\n[HARNESS-BUG] ValueError: foo\n  This is a bug in harness-methodology itself\n  Crash bundle: .methodology/crash/x.json' },
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
    { match: /^sync-\d+$/, respond: 'SYNC: FAIL — simulated push rejection' },
    ...happyOverrides(),
  ]
  const { result, events } = await runWorkflow(WF('phase7-risk.js'), makeHappyResponder(overrides))
  assert.ok(result.error, 'a Sync FAIL must produce a structured error, not a silent pass-through')
  assert.match(result.error, /post-advance push did not PASS/)
  // Round 43 站3: the step now retries WITH repair authority before giving up.
  // A content blocker needs a second attempt after the fix; an unbounded loop
  // would spin on a blocker no agent can clear.
  const attempts = events.agents.filter((a) => a.label.startsWith('sync-')).length
  assert.equal(attempts, 3, 'Sync must exhaust its bounded retry, not stop at one')
})

// Round 43 站3: [HARNESS-BUG] is not a project blocker. Retrying it buys the
// same crash, and "fixing" the project aims CODE-FIX at a defect that is not
// there (Round 13 站2's routing rule).
test('phase7 Sync: [HARNESS-BUG] stops the loop instead of retrying', async () => {
  const overrides = [
    { match: /^sync-\d+$/, respond: 'SYNC: FAIL — harness-methodology itself crashed\n[HARNESS-BUG] ValueError: foo\n  This is a bug in harness-methodology itself\n  Crash bundle: .methodology/crash/x.json' },
    ...happyOverrides(),
  ]
  const { result, events } = await runWorkflow(WF('phase7-risk.js'), makeHappyResponder(overrides))
  assert.equal(result.harness_bug_detected, true, JSON.stringify(result).slice(0, 200))
  assert.equal(result.step, 'sync')
  const attempts = events.agents.filter((a) => a.label.startsWith('sync-')).length
  assert.equal(attempts, 1, 'a harness crash must stop the loop on the first sighting')
})

// ---- 4. garbage B response no longer crashes the A/B machine ---------------
// parseAgentJson THROWS on JSON-less text; runSubTask's fallback
// `sbrResult.b2 || parseAgentJson(bResult, ...)` had no catch, so when BOTH
// the B agent and the structured_b_review validator returned prose without
// JSON, the throw escaped and killed the run. This test used to pin that as
// current behaviour, with the note that the graceful-degrade fix was a Round
// 12 station-2 / audit-doc item rather than a sim change.
//
// Round 28's top-level boundary is that fix, arriving from the layer where it
// belongs: the failure is not specific to the A/B machine — any throw anywhere
// in a standalone phase file ended the run the same way. The diagnosis (the
// throw is real and is not caught locally) still holds; what changed is that
// PARSE_FAIL now leaves through a structured return the operator can read.
test('phase1 A/B machine: JSON-less B + JSON-less validator degrade to a structured error', async () => {
  // first-match-wins: these two shadow the happy pack's b-/sbr- entries.
  const overrides = [
    { match: /^b-\d+-r/, respond: 'I looked at the document and it seems fine to me.' },
    { match: /^sbr-/, respond: 'The validator could not produce output today.' },
    ...happyOverrides(),
  ]
  const { result } = await runWorkflow(WF('phase1-requirements.js'), makeHappyResponder(overrides))
  assert.ok(result.error, `expected a structured error, got: ${JSON.stringify(result).slice(0, 200)}`)
  assert.match(result.error, /PARSE_FAIL/,
    'the boundary must carry the original failure through, not replace it with a generic message')
  assert.equal(result.crashed, true, 'a throw is reported as a crash, not as an ordinary verdict')
})

// ---- 5. schema response missing required fields ----------------------------
test('phase8 preflight: schema reply missing `pass` degrades to a structured error return', async () => {
  const overrides = [{ match: /^preflight/, respond: {} }]
  const { result } = await runWorkflow(WF('phase8-config.js'), makeHappyResponder(overrides))
  assert.ok(result.error)
  assert.match(result.error, /preflight/i)
})

// ---- 5b. loadFileViaPython: agent() throws mid-retry (transport error) -----
// Bug: js_blocks.py's render_load_file_via_python() called agent() with no
// try/catch, so a thrown error (API disconnect, rate limit) escaped the
// retry loop and crashed the whole workflow instead of retrying — even
// though the loop already has maxAttempts machinery for exactly this class
// of bad outcome. Every sibling retry loop in this codebase (persistApproval,
// abLoop, runSubTask, gate loops) already wraps agent()/dispatch() in
// try/catch (the "Bug #2" convention); loadFileViaPython was the outlier.
// Targets P2 Peer Review's SAD.md reload (run-all.js: peerReload defaults to
// all three P2 deliverables when peerModified is unset), the exact call site
// that crashed in production (label loadpy-02-architecture-SAD-md-a1).
test('phase2 loadFileViaPython: agent() throwing on attempt 1 retries instead of crashing the workflow', async () => {
  let loadpyCalls = 0
  const overrides = [
    { match: /^loadpy-/, respond: (call) => {
        loadpyCalls++
        if (loadpyCalls === 1) throw new Error('API Error: Connection closed mid-response.')
        const m = call.prompt.match(/--expect-prefix\s+"([^"]+)"/)
        const heading = m ? m[1].replace(/^#\s*/, '') : 'Simulated Document'
        return `# ${heading}\n\n` + 'simulated document body for the workflow logic testbed. '.repeat(4)
      } },
    ...happyOverrides(),
  ]
  const { result } = await runWorkflow(WF('phase2-architecture.js'), makeHappyResponder(overrides))
  assert.equal(result.error, undefined, `expected the throw to be retried, got: ${JSON.stringify(result).slice(0, 200)}`)
  assert.ok(loadpyCalls >= 2, 'the throwing first attempt must be retried, not fatal')
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
      { match: verify, respond: { verify_rc: 0, detail: 'sim' } },
      ...happyOverrides(),
    ]
    const { result, events } = await runWorkflow(WF(name), makeHappyResponder(overrides))
    assert.ok(events.agents.some((a) => roundAgent.test(a.label)),
      'an unfinalized gate must still run its orchestrator round')
    assert.equal(result.error, undefined, JSON.stringify(result).slice(0, 200))
    assert.ok(events.logs.some((l) => l.includes(`Gate ${gate} PASS`)),
      'verify-gate rc=0 is the PASS condition (Round 38: one command, one number,\n'
      + 'and a verdict on disk carrying the tree it was measured on)')
  })

  test(`${name} Gate ${gate}: a non-zero verify_rc cannot PASS (the 9b5f7cf premise)`, async () => {
    const overrides = [
      { match: precheck, respond: { pass: false, reason: 'not finalized' } },
      // Phase Truth still blocking: the SSI dims may well have scored, which
      // is precisely the state manifest.quality_complete reported as done.
      { match: verify, respond: { verify_rc: 12, detail: 'phase truth 69%' } },
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
      { match: verify, respond: { last_gate_ok: true, d4_rc: 1, crg_rc: 0, detail: 'spec-coverage below threshold' } },
      ...happyOverrides(),
    ]
    const { result, events } = await runWorkflow(WF(name), makeHappyResponder(overrides))
    assert.ok(!events.logs.some((l) => l.includes(`Gate ${gate} PASS`)),
      'spec-coverage-check failing must veto the gate even when last_gate is set')
    assert.ok(result.error)
  })

  test(`${name} Gate ${gate}: a nonzero CRG exit code cannot PASS`, async () => {
    // Same shape as the D4 test above — CI's standalone "CRG Architecture
    // Gate (P3+)" job enforces architecture score >= threshold as an
    // absolute floor on every push; the local gate must veto on it too, not
    // just fold it into the (dilutable) composite score.
    const overrides = [
      { match: precheck, respond: { pass: false, reason: 'not finalized' } },
      { match: verify, respond: { last_gate_ok: true, d4_rc: 0, crg_rc: 1, detail: 'architecture score below threshold' } },
      ...happyOverrides(),
    ]
    const { result, events } = await runWorkflow(WF(name), makeHappyResponder(overrides))
    assert.ok(!events.logs.some((l) => l.includes(`Gate ${gate} PASS`)),
      'crg-arch-check failing must veto the gate even when last_gate and D4 are set')
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

// ---- 8. dispatch cost is decoupled from FR count (Round 22 站1) ------------
// ORCH-POST used to render INSIDE both FR loops, so an N-FR phase spent N
// sub-agent dispatches on a step whose second command (`amend-sab`) takes no
// --fr-id and is idempotent by construction. Measured before the fix, on the
// same testbed: P4 21 dispatches at 5 FRs vs 36 at 20; P5/P7/P8 18 vs 33 —
// i.e. every extra FR cost exactly one extra dispatch. The per-FR INFORMATION
// is unchanged (spec-coverage-check still runs once per FR, in a bash loop
// inside the single agent); only the dispatch count moved.
const FR_LOOP_PHASES = [
  'phase4-testing.js',
  'phase5-verification.js',
  'phase7-risk.js',
  'phase8-config.js',
]

function frIds(n) {
  return Array.from({ length: n }, (_, i) => `FR-${String(i + 1).padStart(2, '0')}`)
}

/** All FRs fast-path PASS — the branch an established project actually takes. */
function fastPassOverrides(n) {
  const ids = frIds(n)
  return [
    { match: /^load-ctx/, respond: { fr_ids: ids, fr_count: n } },
    { match: /^delta-fastpath$/, respond: { pass_fr_ids: ids, fail_fr_ids: [] } },
    ...happyOverrides(),
  ]
}

for (const name of FR_LOOP_PHASES) {
  test(`${name}: ORCH-POST dispatches once per phase, not once per FR`, async () => {
    for (const n of [5, 20]) {
      const { result, events } = await runWorkflow(WF(name), makeHappyResponder(fastPassOverrides(n)))
      assert.equal(result.error, undefined, JSON.stringify(result).slice(0, 200))
      const orch = events.agents.filter((a) => a.label === 'orch-post')
      assert.equal(orch.length, 1,
        `${n} FRs must still cost exactly ONE orch-post dispatch (got ${orch.length})`)
    }
  })

  test(`${name}: total dispatch count does not grow with the FR count`, async () => {
    const counts = []
    for (const n of [5, 20]) {
      const { result, events } = await runWorkflow(WF(name), makeHappyResponder(fastPassOverrides(n)))
      assert.equal(result.error, undefined, JSON.stringify(result).slice(0, 200))
      counts.push(events.agents.length)
    }
    assert.equal(counts[0], counts[1],
      `5 FRs and 20 FRs must cost the same number of dispatches on the fast path `
      + `(got ${counts[0]} vs ${counts[1]}) — a difference means something is back inside the FR loop`)
  })
}

// ---- 9. manifest integrity lives in advance-phase now (Round 22 站2) -------
// The check used to run in three places per workflow: an entry-point phase()
// box (redundant — the run-phase in the box immediately before it executes
// PREFLIGHT_CHECKS[0], which IS preflight_manifest_integrity), and once per
// round inside the Advance / Tag & Advance loops. The loop calls moved into
// advance-phase itself (cli/phase_cmds.py::_advance_prechecks, exit 27),
// which also covers the callers a workflow loop never could: a human running
// advance-phase by hand, a resumed session, CI. Two call sites remain because
// advance-phase does not cover them — phase3's Gate-2 round loop and phase8's
// Final Push (push-milestone, not advance-phase).
test('manifest-integrity dispatches survive only where advance-phase cannot cover them', async () => {
  const expected = {
    'phase3-implementation.js': false, // Gate 2 precheck PASSes -> round loop skipped
    'phase4-testing.js': false,
    'phase5-verification.js': false,
    'phase6-quality.js': false,
    'phase7-risk.js': false,
    'phase8-config.js': true, // Final Push
  }
  for (const [name, wantIntegrity] of Object.entries(expected)) {
    const { result, events } = await runWorkflow(WF(name), makeHappyResponder(happyOverrides()))
    assert.equal(result.error, undefined, JSON.stringify(result).slice(0, 200))
    const integrity = events.agents.filter((a) => /integrity/.test(a.label))
    assert.equal(integrity.length > 0, wantIntegrity,
      `${name} integrity dispatches: ${JSON.stringify(integrity.map((a) => a.label))}`)
    assert.ok(!events.agents.some((a) => a.label === 'manifest-integrity'),
      `${name} still runs the entry-point integrity check, which run-phase just performed`)
    assert.ok(!events.agents.some((a) => /^advance-integrity-r/.test(a.label)),
      `${name} still spends a dispatch on what advance-phase now enforces itself`)
  }
})

test('phase3 Gate 2 round loop still re-checks integrity every round', async () => {
  // A fix attempt in an earlier round can reintroduce corruption before
  // finalize-gate commits, and run-gate does not check for it.
  const overrides = [
    { match: /^gate2-precheck$/, respond: { pass: false, reason: 'not finalized' } },
    ...happyOverrides(),
  ]
  const { events } = await runWorkflow(WF('phase3-implementation.js'), makeHappyResponder(overrides))
  assert.ok(events.agents.some((a) => /^g2-integrity-r/.test(a.label)),
    'entering the Gate 2 round loop must still dispatch its per-round integrity check')
})

// ---- 10. ctx load is one dispatch on the happy path (Round 22 站3) ---------
// A ctx-check probe used to precede the read purely to prove the ctx file was
// parseable — running `json.load(ctxFile)`, which is exactly what the read
// itself runs and fails on. On phase3 it was worse than redundant: Fix D's
// `attempt === 1 ||` short-circuited ahead of the verdict, so the probe ran,
// answered, and was discarded outright.
test('Load FRs costs one dispatch when the ctx file reads cleanly', async () => {
  for (const name of FR_LOOP_PHASES) {
    const { result, events } = await runWorkflow(WF(name), makeHappyResponder(fastPassOverrides(2)))
    assert.equal(result.error, undefined, JSON.stringify(result).slice(0, 200))
    const loadFrs = events.agents.filter((a) => a.phase === 'Load FRs')
    assert.deepEqual(loadFrs.map((a) => a.label), ['load-ctx-a1'],
      `${name} Load FRs dispatches: ${JSON.stringify(loadFrs.map((a) => a.label))}`)
  }
})

test('phase3 Load FRs keeps Fix D: attempt 1 always regenerates first', async () => {
  // .sessi-work/ is gitignored, so a `git reset --hard` back to a phase-3
  // entry commit leaves a prior run's phase3_ctx.json — and its `lessons`
  // field — in place. Attempt 1 must overwrite it before reading.
  const { events } = await runWorkflow(
    WF('phase3-implementation.js'), makeHappyResponder(fastPassOverrides(2)))
  // phase3's gate1-precheck also reports phase 'Load FRs'; filter to the ctx
  // dispatches, which are what this test is about.
  const ctxCalls = events.agents
    .filter((a) => a.phase === 'Load FRs' && /^(ctx-|load-ctx-)/.test(a.label))
    .map((a) => a.label)
  assert.deepEqual(ctxCalls, ['ctx-regen-1', 'load-ctx-a1'], JSON.stringify(ctxCalls))
})

test('a failed ctx read still routes to regeneration', async () => {
  // The removed probe was the only thing that used to trigger regen. If a
  // read failure no longer reached that path, a missing ctx file would burn
  // all three attempts on the same unreadable file.
  let reads = 0
  const overrides = [
    { match: /^load-ctx-a/, respond: () => { reads += 1; return reads === 1 ? {} : { fr_ids: ['FR-01'], fr_count: 1 } } },
    ...happyOverrides(),
  ]
  const { result, events } = await runWorkflow(WF('phase7-risk.js'), makeHappyResponder(overrides))
  const loadFrs = events.agents.filter((a) => a.phase === 'Load FRs').map((a) => a.label)
  assert.deepEqual(loadFrs, ['load-ctx-a1', 'ctx-regen-1', 'load-ctx-a2'], JSON.stringify(loadFrs))
  assert.equal(result.error, undefined, 'the second attempt must recover after regeneration')
})

// ---- 11. run-all inlines the eight phase files verbatim (Round 23 站2) -----
// run-all.js is generated by calling the SAME generate_phaseN() functions and
// re-hosting each result inside `async function runPhaseN()`. That makes the
// two paths textually identical by construction; these tests make it
// observable — for every phase, the sequence of agent dispatches run-all
// produces under its `P<N> · ` boxes must match what the standalone file
// produces on its own.
//
// HONEST BOUNDARY: this proves the DISPATCH sequence is the same, which is as
// far as a logic testbed can see. It does not prove the final artifacts are
// byte-identical — that depends on the same prompts producing the same work
// from a real LLM, which only a live E2E run can show.
const RUNALL = WF('run-all.js')

/** Pin the state.json cursor read so the run starts at a known phase. */
const cursorAt = (n) => ({ match: /^phase-cursor$/, respond: { current_phase: n } })

let _runAllFrom1 = null
async function runAllFrom1() {
  if (!_runAllFrom1) {
    _runAllFrom1 = await runWorkflow(RUNALL, makeHappyResponder([cursorAt(1), ...happyOverrides()]))
  }
  return _runAllFrom1
}

const phaseLabels = (events, n) => events.agents
  .filter((a) => a.phase.startsWith(`P${n} · `))
  .map((a) => a.label)

test('run-all runs to completion through all eight phases', async () => {
  const { result, events } = await runAllFrom1()
  assert.equal(result.error, undefined, JSON.stringify(result).slice(0, 300))
  assert.equal(result.session_limit_blocked, undefined)
  assert.equal(result.__fell_through, undefined)
  assert.deepEqual(result.phases_run, [1, 2, 3, 4, 5, 6, 7, 8])
  const declared = await declaredPhases(RUNALL)
  for (const title of declared) {
    assert.ok(events.phases.includes(title), `declared phase "${title}" was never reached`)
  }
  assert.equal(events.phases[0], declared[0], 'the cursor box must come first')
})

for (let n = 1; n <= 8; n += 1) {
  test(`run-all's P${n} section dispatches exactly what phase${n} dispatches standalone`, async () => {
    const solo = await runWorkflow(WF(PHASE_FILES[n - 1]), makeHappyResponder(happyOverrides()))
    assert.equal(solo.result.error, undefined, JSON.stringify(solo.result).slice(0, 200))
    const { events } = await runAllFrom1()
    // `resolve-repo` is one dispatch that legitimately differs: run-all
    // resolves REPO once at top level instead of once per phase. It does not
    // appear here because the sim supplies args.repo, so neither path spawns
    // it — the saving is real but conditional, and invisible to this testbed.
    //
    // The Sync dispatch is the other, and it IS visible: six phases fold it
    // into `advance-phase --push` (Round 23 站3). SYNC_FOLDED names them, and
    // the assertion below is exact in both directions — run-all may drop that
    // one label from those six and NOTHING else, anywhere.
    const expected = solo.events.agents
      .map((a) => a.label)
      .filter((label) => !(SYNC_FOLDED.includes(n) && label.startsWith('sync-')))
    assert.deepEqual(phaseLabels(events, n), expected,
      `run-all's P${n} dispatch sequence drifted from phase${n}'s`)
  })
}

// ---- 12. the C-level subtraction, stated as a spec (Round 23 站3) ---------
// advance-phase --push publishes the handover commit the command itself made,
// so the six phases that ended with the SHARED Sync block (one `git push`,
// nothing else) no longer need it. phase3 keeps its bespoke Sync (retry, then
// a MANUAL_REQUIRED handover note) and phase8 keeps its own (it also verifies
// the release tag reached origin) — folding either would drop behaviour.
const SYNC_FOLDED = [1, 2, 4, 5, 6, 7]

test('run-all drops exactly the six foldable Sync dispatches, and nothing else', async () => {
  const { events } = await runAllFrom1()
  const solo = []
  for (const name of PHASE_FILES) {
    solo.push(await runWorkflow(WF(name), makeHappyResponder(happyOverrides())))
  }
  const soloTotal = solo.reduce((n, r) => n + r.events.agents.length, 0)
  const runAllTotal = events.agents.length
  // -6 sync dispatches, +1 phase-cursor read.
  assert.equal(runAllTotal, soloTotal - SYNC_FOLDED.length + 1,
    `expected ${soloTotal} - 6 + 1 dispatches, got ${runAllTotal}`)

  for (let n = 1; n <= 8; n += 1) {
    const syncCalls = phaseLabels(events, n).filter((l) => l.startsWith('sync-')).length
    assert.equal(syncCalls, SYNC_FOLDED.includes(n) ? 0 : 1,
      `P${n} sync dispatch count is wrong for the fold policy`)
  }
})

test('every folded phase asks advance-phase to publish its own commit', async () => {
  const src = await readFile(RUNALL, 'utf8')
  for (const n of SYNC_FOLDED) {
    assert.ok(src.includes(`advance-phase --completed ${n} --project ' + REPO + ' --push`),
      `P${n} folds away its Sync box but never passes --push — its handover commit would never reach origin`)
  }
  for (const n of [3, 8]) {
    assert.ok(!src.includes(`advance-phase --completed ${n} --project ' + REPO + ' --push`),
      `P${n} keeps its own Sync box, so --push would push twice`)
  }
})

test('run-all starts at the state.json cursor, not always at Phase 1', async () => {
  const { result, events } = await runWorkflow(RUNALL, makeHappyResponder([cursorAt(7), ...happyOverrides()]))
  assert.equal(result.error, undefined, JSON.stringify(result).slice(0, 300))
  assert.deepEqual(result.phases_run, [7, 8])
  for (const n of [1, 2, 3, 4, 5, 6]) {
    assert.equal(phaseLabels(events, n).length, 0, `Phase ${n} must not re-run when the cursor says 7`)
  }
  assert.ok(phaseLabels(events, 7).length > 0)
})

test('run-all fails closed when the cursor cannot be read', async () => {
  // Defaulting to Phase 1 here would re-run the whole requirements phase on an
  // established project — strictly worse than stopping and reporting.
  const { result, events } = await runWorkflow(
    RUNALL, makeHappyResponder([{ match: /^phase-cursor$/, respond: null }, ...happyOverrides()]),
  )
  assert.match(String(result.error ?? ''), /current_phase/)
  // Round 48 站2: the cursor read, then ONE dispatch to write the halt down,
  // and nothing else. Before that second dispatch existed this asserted a bare
  // length of 1; the run still stopped in the same place, it just left no
  // record that it had.
  assert.deepEqual(
    events.agents.map((a) => a.label), ['phase-cursor', 'record-block'],
    'nothing beyond the cursor read and its halt record may run',
  )
})

// ---- Round 48 站2: a halt is written down, once ----------------------------
// The 125 terminal halt sites across the eight phase files all funnel through
// the four driver branches below, so one recording point covers every one of
// them — but only when the driver actually reaches it. Without this scenario
// the recordBlock calls could be dropped and every other test would stay green,
// which is the exact blind spot Round 27 站6 opened this file for run-all to fix.
test('round48: a phase that halts records where it stopped, exactly once', async () => {
  // Only the cursor is pinned: with no happy-path overrides the run reaches
  // Phase 8's post-advance push and stops there, which is the driver's
  // `outcome.error` branch — the one 93 of the 125 halt sites arrive through.
  const { result, events } = await runWorkflow(
    RUNALL, makeHappyResponder([cursorAt(8)]),
  )
  assert.match(String(result.error ?? ''), /Phase 8/)
  const recorded = events.agents.filter((a) => a.label === 'record-block')
  assert.equal(recorded.length, 1, 'one dispatch, once per aborted run — not once per retry')
  assert.match(recorded[0].prompt, /harness_cli\.py record-block --project/)
  assert.match(recorded[0].prompt, /--phase 8 /, 'the record must name the phase it stopped in')
})

// ---- Round 48 站4: harness-repair, the workflow whose subject is harness ----
// It is the only workflow that can edit the framework, so what matters is not
// that it succeeds but that each refusal actually stops it. Four scenarios,
// one per exit the design promises.
const REPAIR = WF('harness-repair.js')
const REPAIR_ARGS = { repo: '/sim/project', ticket: '/sim/project/.methodology/t.json' }

test('round48: harness-repair refuses to start without a ticket', async () => {
  const { result, events } = await runWorkflow(
    REPAIR, makeHappyResponder([]), { args: { repo: '/sim/project' } },
  )
  assert.match(String(result.error ?? ''), /args\.ticket/)
  assert.equal(events.agents.length, 0, 'not one dispatch may be spent without a ticket')
})

test('round48: harness-repair stops when the failure does not reproduce', async () => {
  const { result, events } = await runWorkflow(
    REPAIR,
    makeHappyResponder([{ match: /^repair-repro$/, respond: { rc: 1 } }]),
    { args: REPAIR_ARGS },
  )
  assert.match(String(result.error ?? ''), /did not reproduce/)
  const labels = events.agents.map((a) => a.label)
  assert.ok(!labels.some((l) => l.startsWith('repair-fix')),
            'nothing may be edited before the failure is shown to be real')
})

test('round48: a rejected diagnosis stops the run before any edit', async () => {
  const { result, events } = await runWorkflow(
    REPAIR,
    makeHappyResponder([
      { match: /^repair-review$/, respond: { pass: false, reason: 'the named line is never reached' } },
    ]),
    { args: REPAIR_ARGS },
  )
  assert.match(String(result.error ?? ''), /adversarial review/)
  const labels = events.agents.map((a) => a.label)
  assert.ok(!labels.some((l) => l.startsWith('repair-fix')),
            'a diagnosis that failed review must not reach the fixer')
})

test('round48: a land that keeps being refused never reports success', async () => {
  const { result, events } = await runWorkflow(
    REPAIR,
    makeHappyResponder([{ match: /^repair-land-r/, respond: { rc: 1 } }]),
    { args: REPAIR_ARGS },
  )
  assert.equal(result.repaired, undefined, 'a refused land is not a repair')
  assert.match(String(result.error ?? ''), /did not land/)
  assert.equal(events.agents.filter((a) => a.label.startsWith('repair-land-r')).length, 3,
               'three rounds, then stop — not a fourth spent on a refusal it has failed to clear')
})

test('round48: a clean repair reports the submodule bump the project must make', async () => {
  const { result } = await runWorkflow(REPAIR, makeHappyResponder([]), { args: REPAIR_ARGS })
  assert.equal(result.repaired, true, JSON.stringify(result).slice(0, 300))
  assert.ok(String(result.next.join(' ')).includes('harness pull'))
})

test('round48: a run that completes records no block at all', async () => {
  const { events } = await runAllFrom1()
  assert.equal(
    events.agents.filter((a) => a.label === 'record-block').length, 0,
    'a successful run has no halt to record — an unconditional call would make '
    + 'the ledger a log of runs rather than a log of blocks',
  )
})

test('run-all does nothing when the project is past Phase 8', async () => {
  const { result } = await runWorkflow(RUNALL, makeHappyResponder([cursorAt(9), ...happyOverrides()]))
  assert.equal(result.error, undefined)
  assert.deepEqual(result.phases_run, [])
})

// recordBlock's dispatch is schema'd and its result is used — matching every
// other verified dispatch in run-all.js — rather than fired and discarded
// with an instruction to not retry or escalate on the one path whose job is
// reporting why the pipeline failed.
test('round48: recordBlock is schema-verified, not fire-and-forget', async () => {
  const { events } = await runWorkflow(RUNALL, makeHappyResponder([cursorAt(8)]))
  const recorded = events.agents.filter((a) => a.label === 'record-block')
  assert.equal(recorded.length, 1)
  assert.ok(!recorded[0].prompt.includes('Do nothing else'))
  assert.ok(!recorded[0].prompt.includes('rather than retrying'))
  assert.match(recorded[0].prompt, /repair_workflow/)
})

// ---- 13. Round 64 站0: the bookkeeping preamble actually rides along -------
// The wrapper buffers a record per dispatch and hands the buffer to the NEXT
// dispatch as a preamble — the only way to write anything from a sandbox with
// no filesystem, no shell and no clock. Inside the sim there is no shell
// either, so the preamble's PRESENCE in the next prompt is the observable.
// Without this, the wrapper can be silently emptied and every other workflow
// test still passes — which is what happened in 6e7942e.
test('round26: dispatch records ride along on the next prompt', async () => {
  const { events } = await runWorkflow(WF('phase1-requirements.js'),
                                       makeHappyResponder(happyOverrides()))
  assert.ok(events.agents.length >= 2, 'need at least two dispatches to observe a flush')
  assert.ok(
    !events.agents[0].prompt.includes('[BOOKKEEPING'),
    'the FIRST dispatch has nothing to flush — an empty buffer must add nothing',
  )
  const carriers = events.agents.filter((a) => a.prompt.includes('[BOOKKEEPING'))
  assert.ok(
    carriers.length > 0,
    'no dispatch carried the buffered records — the workflow substrate is invisible '
    + 'to sessions_spawn.log again (Round 26 站5)',
  )
  assert.match(carriers[0].prompt, /harness_cli\.py log-dispatch --project/)
  assert.match(carriers[0].prompt, /--batch/)
})

// ---- 13c. Round 64 站0: a blocked reply ends the retry loop -----------------
// Phase 2's preflight, constitution and push-checkpoint sites each dispatch
// inside a retry loop, and their session-limit guards sit AFTER the loop's
// closing brace — unlike phase6/phase8/the gate loop, whose guards return or
// break from inside. Two consequences, both observable here: the run keeps
// dispatching into a wall it has already hit, and the verdict is read from
// the LAST attempt, so real FAILs followed by one empty reply are reported as
// a quota block (relaunchable, project not at fault) instead of a quality
// failure.
test('round64: a blocked preflight reply does not burn the remaining attempts', async () => {
  const { result, events } = await runWorkflow(
    WF('phase2-architecture.js'),
    makeHappyResponder([{ match: /^preflight-/, respond: null }, ...happyOverrides()]),
  )
  assert.equal(result.session_limit_blocked, true)
  const attempts = events.agents.filter((a) => a.label.startsWith('preflight-'))
  assert.equal(attempts.length, 1,
               `a blocked first attempt dispatched ${attempts.length} times before aborting`)
})

// The push-checkpoint loop retries five times, so it is the most expensive
// of the three: a quota cap hit on the first attempt costs four more
// dispatches against a wall the run has already reported as final.
test('round64: a blocked push-checkpoint reply aborts on the first attempt', async () => {
  const { result, events } = await runWorkflow(
    WF('phase2-architecture.js'),
    makeHappyResponder([{ match: /^push-/, respond: null }, ...happyOverrides()]),
  )
  assert.equal(result.session_limit_blocked, true)
  assert.equal(result.step, 'push-checkpoint')
  const attempts = events.agents.filter((a) => a.label.startsWith('push-'))
  assert.equal(attempts.length, 1,
               `a blocked first attempt dispatched ${attempts.length} times before aborting`)
})

test('round26: every workflow routes its dispatches through the wrapper', async () => {
  for (const f of ['phase1-requirements.js', 'phase3-implementation.js', 'run-all.js']) {
    const src = await readFile(WF(f), 'utf8')
    const raw = (src.match(/await agent\(/g) || []).length
    assert.equal(raw, 1, `${f}: expected only the wrapper's own agent() call, found ${raw}`)
    assert.equal((src.match(/async function dispatch\(/g) || []).length, 1,
                 `${f}: the wrapper must be declared exactly once`)
  }
})

// ---- 14. Round 28: the workflow runtime handles NOTHING ---------------------
// Measured against the runtime, not assumed: its entire error behaviour is
// "terminate the run". It does not retry, isolate, degrade, or resume across
// sessions; a throw the script does not catch produces no result at all
// (docs/WORKFLOW_PLAYBOOK.md §4/§6.3). Every bit of fault tolerance therefore
// has to be IN the generated JS, which makes these three assertions the spec
// for what "the workflow survived" means.

// 14a. A terminal abort flag must actually stop the pipeline.
// runPhase3 detects two conditions no downstream phase can recover from and
// returns a flag for each. run-all's phase loop read neither: both objects
// carry no `error` key, so the loop pushed the phase onto phases_run and went
// on to Phase 4. A run in which harness itself crashed on FR-01 reported
// `phases_run: [3,4,5,6,7,8]` and no error — the sixth appearance of "the
// detector was built, the consumer never read it".
const TERMINAL_FLAG_CASES = [
  {
    flag: 'harness_bug_detected',
    reply: 'FR-01 GATE1: FAIL — harness-methodology itself crashed\n'
      + '[HARNESS-BUG] ValueError: foo\n'
      + '  This is a bug in harness-methodology itself\n'
      + '  Crash bundle: .methodology/crash/x.json',
    why: 'harness-methodology itself crashed — no later phase can be trusted',
  },
  {
    flag: 'dispatch_structurally_broken',
    reply: 'FR-01 GATE1: FAIL — [FATAL] FR-01 GATE1: sub-agent dispatch is structurally '
      + 'broken — Claude Code reports claude.ai connectors are disabled',
    why: 'no sub-agent can be dispatched at all — every later phase would fail identically',
  },
]

for (const { flag, reply, why } of TERMINAL_FLAG_CASES) {
  test(`round28: run-all stops when Phase 3 returns ${flag}`, async () => {
    const { result, events } = await runWorkflow(RUNALL, makeHappyResponder(
      [cursorAt(3), { match: /^tdd-/, respond: reply }, ...happyOverrides()]))
    const laterBoxes = events.phases.filter((p) => /^P[4-8] · /.test(p))
    assert.deepEqual(laterBoxes, [],
      `${flag}: ${why}, yet run-all entered ${laterBoxes.length} later-phase box(es) `
      + `(${laterBoxes.slice(0, 3).join(', ')})`)
    assert.deepEqual(result.phases_run, [],
      `${flag}: Phase 3 aborted, so it must not be recorded as run — got `
      + JSON.stringify(result.phases_run))
    assert.ok(result.error || result[flag],
      `${flag}: run-all returned a success shape after a terminal abort — `
      + JSON.stringify(result).slice(0, 200))
  })
}

// 14c. The two unfixable conditions are detected in EVERY per-FR loop.
// [HARNESS-BUG] (harness crashed) and [FATAL] structurally-broken dispatch (no
// sub-agent can be spawned) were written for Phase 3's TDD loop and stayed
// there for their whole existence. P4, P5, P7 and P8 run their own per-FR
// Gate 1 loops and had neither: harness crashing during any of those four read
// as an ordinary GATE1 FAIL, which routes to CODE-FIX — a fix agent dispatched
// at a defect that is not in the project's code.
const DELTA_PHASE_FILES = {
  4: 'phase4-testing.js',
  5: 'phase5-verification.js',
  7: 'phase7-risk.js',
  8: 'phase8-config.js',
}

for (const [n, file] of Object.entries(DELTA_PHASE_FILES)) {
  for (const { flag, reply } of TERMINAL_FLAG_CASES) {
    test(`round28: ${file} per-FR loop aborts on ${flag}`, async () => {
      const { result } = await runWorkflow(WF(file), makeHappyResponder(
        [{ match: /^delta-FR-/, respond: reply.replace('GATE1', 'GATE1-DELTA') }, ...happyOverrides()]))
      assert.equal(result[flag], true,
        `${file}: ${flag} not raised — the failure would be routed to CODE-FIX as a `
        + `code-quality FAIL. Got: ${JSON.stringify(result).slice(0, 200)}`)
      assert.equal(result.phase, Number(n), 'the abort must name the phase it happened in')
      assert.match(result.message, /GATE1-DELTA/,
        'the message must name the step that actually ran, not P3\'s GATE1')
    })
  }
}

// 14b. No dispatch may kill the run.
// A rejecting `agent()` is not exotic: it is what a transient transport error
// looks like. run-all wraps each phase call in try/catch (Round 23), so it
// survives all 85 of its labels; the eight standalone files it was generated
// from have no boundary at all, so 84 of their 217 labels take the whole run
// down with no result. The boundary belongs to every file, not just the one
// that happened to get it.
for (const name of ALL_WORKFLOW_FILES) {
  test(`round28: ${name} — no dispatch failure escapes as an unhandled throw`, async () => {
    const base = () => makeHappyResponder(sweepOverrides(name))
    const { events } = await runWorkflow(WF(name), base())
    const labels = [...new Set(events.agents.map((a) => a.label))]
    assert.ok(labels.length > 0, `${name}: no dispatches observed — scenario pack is broken`)
    const escaped = []
    for (const label of labels) {
      try {
        await runWorkflow(WF(name), throwingResponder(label, base()))
      } catch {
        escaped.push(label)
      }
    }
    assert.deepEqual(escaped, [],
      `${name}: ${escaped.length}/${labels.length} dispatch label(s) kill the run with no `
      + `structured result — the operator gets no phase, no reason, no resume point. `
      + `First few: ${escaped.slice(0, 5).join(', ')}`)
  })
}

// ---- Round 50 站3: the halt carries its step to the recording -------------
// Round 48 站2's recording point hardcoded `phase-error` for every one of the
// 55 top-level halts across the eight phase files, so the one row a real
// P1-P8 run produced named a phase and nothing else. `halt()` attaches the
// step at the site that knows it and the driver reads it back.
test('round50: the recorded step is the one the halt site named', async () => {
  const { result, events } = await runWorkflow(
    RUNALL, makeHappyResponder([cursorAt(8)]),
  )
  assert.equal(result.halt_step, 'post-advance-push',
    'the driver returns the step the phase halted at, not a generic name')
  const recorded = events.agents.filter((a) => a.label === 'record-block')
  assert.equal(recorded.length, 1)
  assert.match(recorded[0].prompt, /--step 'post-advance-push'/,
    'the ledger row must name the step, not `phase-error` for all 55 sites')
  assert.doesNotMatch(recorded[0].prompt, /--step 'phase-error'/)
})
