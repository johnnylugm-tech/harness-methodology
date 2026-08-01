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
    // The `sync` dispatch is the other, and it IS visible: six phases fold it
    // into `advance-phase --push` (Round 23 站3). SYNC_FOLDED names them, and
    // the assertion below is exact in both directions — run-all may drop that
    // one label from those six and NOTHING else, anywhere.
    const expected = solo.events.agents
      .map((a) => a.label)
      .filter((label) => !(SYNC_FOLDED.includes(n) && label === 'sync'))
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
    const syncCalls = phaseLabels(events, n).filter((l) => l === 'sync').length
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
  assert.equal(events.agents.length, 1, 'nothing beyond the cursor read may run')
})

test('run-all does nothing when the project is past Phase 8', async () => {
  const { result } = await runWorkflow(RUNALL, makeHappyResponder([cursorAt(9), ...happyOverrides()]))
  assert.equal(result.error, undefined)
  assert.deepEqual(result.phases_run, [])
})

// ---- 13. Round 26 站5: the dispatch wrapper's bookkeeping actually rides along
// The wrapper buffers a record per dispatch and hands the buffer to the NEXT
// dispatch as a preamble — the only way to write anything from a sandbox with no
// filesystem, no shell and no clock. Inside the sim there is no shell either, so
// the preamble's PRESENCE in the next prompt is the observable. Without this, the
// wrapper could be silently dropped and every workflow test would still pass.
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

test('round26: every workflow routes its dispatches through the wrapper', async () => {
  for (const f of ['phase1-requirements.js', 'phase3-implementation.js', 'run-all.js']) {
    const src = await readFile(WF(f), 'utf8')
    const raw = (src.match(/await agent\(/g) || []).length
    assert.equal(raw, 1, `${f}: expected only the wrapper's own agent() call, found ${raw}`)
    assert.equal((src.match(/async function dispatch\(/g) || []).length, 1,
                 `${f}: the wrapper must be declared exactly once`)
  }
})
