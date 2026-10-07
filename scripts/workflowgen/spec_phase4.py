"""Phase 4 (Testing) workflow assembly — Round 15 station3 extraction from
the former monolithic phase_specs.py. See scripts/workflowgen/spec_shared.py
for the cross-phase _render_meta.
"""
from __future__ import annotations

from . import js_blocks as B
from . import spec_shared as S
from .spec_shared import _render_meta

_HEADER_4 = f"""\
// Phase 4 — Testing (faithful to .methodology/phase4_plan.md v2.12.0)
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/phase_specs.py::generate_phase4() (+ js_blocks.py for
// the blocks shared across phase workflow files). Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write --phase 4
//
// Structure: FR-loop型 + adversarial bug hunt + Gate 3 ({S.gate_dim_count(3)} dims) exit.
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
"""

_META_PHASES_4 = [
    "Entry & Preflight", "Test Plan", "Env Check",
    "Load FRs", "Per-FR Delta", "Declared Tests", "Coverage", "Bug Hunt", "Artifacts Commit",
    "Gate 3", "Preview Next-Phase", "Advance", "Sync",
]


def _render_test_plan() -> str:
    return (
        B.render_phase_header("Test Plan")
        + "log('Generate 04-testing/TEST_PLAN.md from SRS FR acceptance criteria')\n"
        + "const testPlanReport = await agent(\n"
        + "  'YOU ARE THE P4 TEST PLAN AUTHOR. Generate TEST_PLAN.md (runs once before per-FR testing).\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "  + 'Steps (create 04-testing/ if missing):\\n'\n"
        + "  + '1. Read 01-requirements/SRS.md FR acceptance criteria + .methodology/quality_manifest.json FR list.\\n'\n"
        + "  + '2. Write ' + REPO + '/04-testing/TEST_PLAN.md. For each FR: test case ID, description, input, expected output, priority. Include positive, negative, boundary, and edge-case categories. Cover ALL FRs + NFRs.\\n'\n"
        + "  + '3. Verify TEST_PLAN.md covers every FR from the manifest.\\n\\n'\n"
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if TEST_PLAN.md was written and covers every FR; reason = one-line summary.\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT run TDD/run-gate/bug-hunt/advance.\\n- DO NOT modify harness/.\\n- ONLY author TEST_PLAN.md.',\n"
        + "  { label: 'test-plan', phase: 'Test Plan', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(testPlanReport && testPlanReport.pass === true)) {\n"
        + "  return halt('test-plan', { error: 'Phase 4 TEST_PLAN did not PASS', reason: testPlanReport ? String(testPlanReport.reason ?? '').slice(-500) : 'agent returned null' })\n"
        + "}\n"
    )


def _render_declared_tests() -> str:
    """Round 114 站5 — the tests TEST_SPEC declares outside every FR's rows.

    FR rows are written by the P3 per-FR loop and judged by Gate 1. NFR
    sections, the deferred table and smoke rows had no step and no deadline:
    all 89 undelivered declared tests in the corpus are such rows. This step
    writes them before Coverage and Gate 3 measure the suite; the deadline is
    `advance-phase --completed 4` (exit 52), which re-asks the same command.
    """
    return (
        B.render_phase_header("Declared Tests")
        + "log('Declared tests no FR owns (NFR sections, the deferred table): write them before Gate 3')\n"
        + "const declaredCmd = PY + ' ' + REPO + '/harness_cli.py undelivered-tests --project ' + REPO + ' --non-fr'\n"
        + "let declaredDone = false\n"
        + "for (let round = 1; round <= 3; round++) {\n"
        + "  const chk = await agent(\n"
        + "    'Run EXACTLY this via the Bash tool:\\n`' + declaredCmd + '; echo RC=$?`\\n'\n"
        + "    + 'Report via the StructuredOutput tool: rc = the exact number on the final RC= line.',\n"
        + "    { label: 'declared-check-r' + round, phase: 'Declared Tests', agentType: 'general-purpose', schema: RC_SCHEMA },\n"
        + "  )\n"
        + "  if (chk && chk.rc === 0) { declaredDone = true; break }\n"
        + "  if (round === 3) break\n"
        + "  await agent(\n"
        + "    'YOU ARE THE P4 TEST AUTHOR for the tests TEST_SPEC.md declares outside every FR\\'s rows.\\n'\n"
        + "    + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "    + '1. `' + declaredCmd + '` lists each one and its section.\\n'\n"
        + "    + '2. Write each, EXACTLY that name, from its row in 02-architecture/TEST_SPEC.md (Inputs, precondition, sub-assertions), in an NFR test file. Assert what the row says about the product; no `assert True`, no skip.\\n'\n"
        + "    + '3. A suite-level criterion READS the harness evidence (.methodology/gate_evidence/, the coverage report); it never runs pytest over its own directory or `make verify-system`.\\n'\n"
        + "    + '4. Run them, then commit only the test files: `git -C ' + REPO + ' add <files> && git -C ' + REPO + ' commit -m \"test(P4): declared tests no FR owns\"`.\\n\\n'\n"
        + "    + 'SCOPE RULES:\\n- ONLY test files; DO NOT edit source or any phase deliverable, DO NOT rename a declared test, DO NOT run run-gate / advance-phase.',\n"
        + "    { label: 'declared-write-r' + round, phase: 'Declared Tests', agentType: 'general-purpose' },\n"
        + "  )\n"
        + "}\n"
        + "if (!declaredDone) {\n"
        + "  return halt('declared-tests', { error: 'declared tests outside every FR\\'s rows are still undelivered after 2 writing rounds — `harness_cli.py undelivered-tests --non-fr` lists them', owner: 'project' })\n"
        + "}\n"
    )


def _render_coverage() -> str:
    return (
        B.render_phase_header("Coverage")
        + "log('Generate TEST_RESULTS.md + COVERAGE_REPORT.md (cross-artifact validated at Gate 3)')\n"
        + "const coverageReport = await agent(\n"
        + "  'YOU ARE THE P4 COVERAGE AUTHOR. Generate the test-results + coverage deliverables.\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "  + 'Steps:\\n'\n"
        + "  + '1. TEST_RESULTS: write ' + REPO + '/04-testing/TEST_RESULTS.md — summarise test execution: cases run, pass/fail, deferred issues. Include the VERBATIM pytest summary line of the run you are describing (the `N passed, M skipped … in T s` line pytest prints); `cross_artifact.check_test_count_reconciliation` compares its counts against the framework own run_suite measurement and reports a mismatch as CRITICAL, so this document cannot record a run over a tree the project does not deliver. Scope the run to the `test_target` step 2 reads, NOT to the repository root — the root also holds the vendored harness copy, and a run from there collects thousands of the framework own tests. Measured: one project recorded `4 failed, 7563 passed, 3 skipped` for a 349-test tree, and that number then travelled into BASELINE.md and VERIFICATION_REPORT.md unchallenged.\\n'\n"
        + "  + '2. COVERAGE: read TESTS=`test_target` and SRC=`cov_target` (project-relative) from ' + REPO + '/.sessi-work/phase4_ctx.json — load-context writes them from the resolver Gate 3 re-measures with. Do NOT substitute your own: the layout differs between projects, and .coveragerc may scope SRC. Run `' + PY + ' -m pytest ' + REPO + '/<TESTS> --cov=<SRC> --cov-report=term-missing -q | tee ' + REPO + '/04-testing/coverage_raw.txt` then `' + PY + ' -m coverage report --format=total`. Write ' + REPO + '/04-testing/COVERAGE_REPORT.md with overall coverage % (≥80% for Gate 3), per-module breakdown, uncovered lines.\\n'\n"
        + "  + '   WARNING: cross_artifact.py validates these numbers against live pytest --cov at Gate 3 — fabricated numbers are caught. Use REAL numbers.\\n\\n'\n"
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if both docs were written from real pytest output; reason = one-line summary.\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT run run-gate / bug-hunt / advance.\\n- DO NOT modify harness/.\\n- DO NOT fabricate coverage numbers.\\n- ONLY generate the 2 docs from real pytest output.',\n"
        + "  { label: 'coverage', phase: 'Coverage', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(coverageReport && coverageReport.pass === true)) {\n"
        + "  return halt('coverage-docs', { error: 'Phase 4 coverage docs did not PASS', reason: coverageReport ? String(coverageReport.reason ?? '').slice(-500) : 'agent returned null' })\n"
        + "}\n"
    )


# hunt_bugs.md run by the workflow itself. A workflow agent() child has no
# Agent tool, so the scout, every hunter and both verifiers of each finding
# are dispatched here; JS applies the strict confirmation rule and hands the
# judged findings to `record-bug-hunt` in parts small enough for one Write.
_BUG_HUNT_JS = r'''log('Adversarial bug hunt (hunt_bugs.md): targets → scout → hunters → refute+confirm → record → resolve')
// A workflow agent() child has no Agent tool, so one agent told to "spawn hunters" hunted and
// verified alone (measured: 0 sub-agents in a real P4). The fan-out lives here instead.
const HUNT_LENSES = {"correctness": "Business logic errors, boundary conditions, null/empty handling, off-by-one, type mismatches, incorrect assumptions about input data.", "concurrency": "Race conditions, thread safety, async/await issues, shared mutable state, lock ordering, ordering of side effects, lifecycle of long-lived objects across awaits.", "resilience": "Error handling gaps, missing timeouts, broken fallbacks, resource leaks (files/sockets/connections/child procs), partial-failure handling, error swallowing, NFR compliance for degraded modes.", "general": "Any concrete, reachable bug — wrong return type, broken validation, dead branch, leaked resource, missing rollback, incorrect status code, log/PII leak, input size limit (DoS), wrong default. Skip stylistic nits and hypotheticals."}
const htRc = await agent(
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
const huntScout = await agent(
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
const huntHunt = async (p, i, sfx) => await agent(huntPrompt(p), { label: 'hunt-' + i + '-' + p.lens + sfx, phase: 'Bug Hunt', agentType: 'Explore', model: HUNT_MODEL, schema: p.kind === 'threat' ? THREAT_HUNT_SCHEMA : HUNT_RESULT_SCHEMA })
const huntVerify = async (f, i, j, role, sfx) => await agent(
  (role === 'refute'
    ? 'Try to REFUTE this bug finding (hunt_bugs.md Phase 3). Default is_real=false unless undeniable. Is the cited code at the cited line? Does surrounding code already guard it? Is the scenario reachable? Cite line numbers.\n'
    : 'Independently CONFIRM this bug finding (hunt_bugs.md Phase 3). Default is_real=false unless provable: trace the data flow to the line, check tests_for (a passing test on this path suggests it is handled), and confirm only with a concrete trigger + expected vs actual, citing line numbers.\n')
  + 'REPO: ' + REPO + '\nFINDING:\n' + JSON.stringify(f) + '\nREAD ONLY.',
  { label: 'hunt-' + i + '-' + j + '-' + role + (sfx || ''), phase: 'Bug Hunt', agentType: 'Explore', model: HUNT_MODEL, schema: VERIFY_SCHEMA })
const huntCited = (v) => /(:\d+|line\s*\d+|L\d+)/i.test(String(v.evidence) + ' ' + String(v.refutation_attempt))
// A verifier that returned nothing gave no verdict: it is re-dispatched once, and a finding
// still missing either verdict is UNJUDGED — never "refuted" (Round 115 站1: `.filter(Boolean)`
// recorded absence of evidence as a refutation, the one state Gate 3 does not block on).
const huntJudge = async (res, p, i) => {
  if (!res) return null
  const raw = p.kind === 'threat' ? [res] : (res.findings || [])
  const out = []
  for (let j = 0; j < raw.length; j++) {
    const f = Object.assign({}, raw[j], { lens: p.lens, module: raw[j].module || p.name })
    if (p.kind === 'threat' && f.mitigation_effective === true) { out.push({ f: f, confirmed: false, refute: String(f.evidence || '') }); continue }
    const roles = ['refute', 'confirm']
    const vs = await parallel(roles.map((role) => () => huntVerify(f, i, j, role, '')))
    for (let k = 0; k < roles.length; k++) if (!vs[k]) vs[k] = await huntVerify(f, i, j, roles[k], '-retry')
    if (!vs[0] || !vs[1]) { out.push({ f: f, unjudged: true }); continue }
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
const huntUnjudged = huntRows.flat().filter((r) => r.unjudged).map((r) => r.f.lens + ':' + r.f.file + ':' + r.f.line_start)
if (huntUnjudged.length) return halt('bug-hunt', { error: huntUnjudged.length + ' finding(s) got no verdict from a verifier after a retry — ' + huntUnjudged.slice(0, 5).join(', '), owner: 'infra' })
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
const huntWritePart = async (k, sfx) => await agent(
  'Write the JSON between the markers to ' + REPO + '/.sessi-work/bug_hunt/part-' + (k + 1) + '.json with the Write tool, byte for byte (do not edit, reformat or summarise it). Then run via Bash: `' + PY + ' ' + REPO + '/harness_cli.py record-bug-hunt --project ' + REPO + ' --part ' + (k + 1) + '; echo RC=$?`\n'
  + 'Report via the StructuredOutput tool: rc from the RC= line; findings, confirmed, first, last from its PART line.\n<<<JSON\n' + JSON.stringify({ findings: huntParts[k] }) + '\nJSON>>>',
  { label: 'hunt-record-' + (k + 1) + sfx, phase: 'Bug Hunt', agentType: 'general-purpose', schema: HUNT_RECORD_SCHEMA })
const huntPartRes = await parallel(huntParts.map((_, k) => () => huntWritePart(k, '')))
for (let k = 0; k < huntParts.length; k++) if (!huntPartOk(k, huntPartRes[k])) huntPartRes[k] = await huntWritePart(k, '-retry')
const huntBadParts = huntParts.map((_, k) => k + 1).filter((n) => !huntPartOk(n - 1, huntPartRes[n - 1]))
if (huntBadParts.length) return halt('bug-hunt', { error: 'bug-hunt part(s) ' + huntBadParts.join(', ') + ' of ' + huntParts.length + ' were not recorded as dispatched', owner: 'infra' })
const huntRec = await agent(
  'Run EXACTLY this via the Bash tool:\n`' + PY + ' ' + REPO + '/harness_cli.py record-bug-hunt --project ' + REPO + ' --assemble ' + huntParts.length + ' --lenses threat-model,correctness,concurrency,resilience,general; echo RC=$?`\n'
  + 'Report via the StructuredOutput tool: rc from the RC= line; findings and confirmed from its RECORDED line (0 and 0 if there is none).',
  { label: 'hunt-record-assemble', phase: 'Bug Hunt', agentType: 'general-purpose', schema: HUNT_RECORD_SCHEMA },
)
if (!(huntRec && huntRec.rc === 0 && huntRec.findings === huntFindings.length && huntRec.confirmed === huntConfirmed)) {
  return halt('bug-hunt', { error: 'record-bug-hunt did not assemble the hunt as dispatched (expected ' + huntFindings.length + ' findings / ' + huntConfirmed + ' confirmed)', owner: 'infra', got: huntRec })
}
await agent(
  'Write a concise markdown bug report in Traditional Chinese at ' + REPO + '/03-development/.audit/bug-report-hunt.md from ' + REPO + '/.methodology/bug_hunt_report.json (read it): summary table (module x severity), confirmed bugs by severity (location, problem, evidence, fix), refuted findings (one line each), fix priority, method. Cite file:line; at most 2000 words. Edit nothing else.',
  { label: 'hunt-report-md', phase: 'Bug Hunt', agentType: 'general-purpose' },
)
const huntBlocking = huntFindings.filter((f) => f.confirmed && (f.severity === 'critical' || f.severity === 'high'))
if (huntBlocking.length === 0) {
  log('  no confirmed critical/high finding — nothing to resolve before Gate 3')
} else {
  const huntReport = await agent(
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
'''


def _render_bug_hunt() -> str:
    return B.render_phase_header("Bug Hunt") + _BUG_HUNT_JS


# The D4 spec-coverage floor for this phase. It is NOT a gate dimension —
# spec-coverage-check owns it — so it has no entry in the gate config the rest
# of this block reads from. Round 69 站1 moved the literal to
# spec_shared.D4_THRESHOLDS: render_advance_loop's exit-gate re-verify needs
# the same number, and this alias keeps the prose, the `--threshold` argument
# and the pass line reading from one place.
_D4_THRESHOLD_P4 = S.D4_THRESHOLDS[4]

_GATE3_STEPS = [
    "1. G3a: `' + PY + ' ' + REPO + '/harness_cli.py run-gate --gate 3 --phase 4 --project ' + REPO + '` (CRG recon runs inside automatically). Read the printed evaluation prompt.",
    (
        "2. G3b: Evaluate ALL Gate 3 dimensions inline per ' + REPO + '/harness/harness/ssi/prompts/evaluate_dimension.md. Write ' + REPO + '/.sessi-work/gate3_result.json.\\n"
        f"{S.render_dimension_table(3)}"
        "   For any failing dim: fix ROOT CAUSE in code (ruff/pyright/tests/bandit/readability_v2/ast-error-handling/pytest-benchmark), re-run the tool, update score. (readability tool is `python3 -m harness.toolchains.readability_v2` — NOT `radon mi` — per phase3/4/6_plan.md v2.12.0.) A low architecture score has no waiver route (Round 38): fix the structure, or — only for a genuine CRG false positive — calibrate `crg_excludes` / `crg_cohesion_healthy` in .methodology/harness_config.json, which is committed and therefore applies to CI too.\\n"
        "   mutation_testing re-run MUST be BACKGROUNDED (mutmut can take up to 3600s; a synchronous call is silently truncated at ~10min, which is exactly how a fabricated score happens):\\n"
        "   a. Launch: `nohup ' + PY + ' ' + REPO + '/harness_cli.py mutation-test-score --project ' + REPO + ' > /tmp/mutation_g3_r' + round + '.log 2>&1 & echo $!` — note the PID.\\n"
        f"   b. Poll every {S.POLL_INTERVAL_S}s: `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE` (cap {S.mutation_poll_cap()} polls / ~60min). Past cap → `kill <PID>`, record TIMEOUT for mutation_testing — never hand-write a score.\\n"
        "   c. DONE → `cat /tmp/mutation_g3_r' + round + '.log`; this already wrote ' + REPO + '/.methodology/mutation_score.json — read it back, never author it."
    ),
    (
        "3. G3c — run BACKGROUNDED (same class of risk as GATE2\\'s G2c — a single opaque Bash call with no visible output until it returns is exactly the shape the 180s stall watchdog kills):\\n"
        "   a. Launch: `nohup ' + PY + ' ' + REPO + '/harness_cli.py finalize-gate --gate 3 --phase 4 --project ' + REPO + ' > /tmp/gate3_finalize_r' + round + '.log 2>&1 & echo $!` — note the printed PID.\\n"
        "   b. Poll: every 15s run `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Repeat until DONE (cap 40 polls / ~10min). Still RUNNING past the cap → `kill <PID>` (reaps the whole tree), report \"GATE3: TIMEOUT\".\\n"
        "   c. Once DONE: `cat /tmp/gate3_finalize_r' + round + '.log` for the full output — identical to what a synchronous run would have printed.\\n"
    ),
    (
        "4. D4 — run BACKGROUNDED (this check can exceed the Bash tool\\'s ~10-min synchronous default; a truncated call must not be read as passing/excluded):\\n"
        f"   a. Launch: `nohup ' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold {_D4_THRESHOLD_P4} > /tmp/d4_g3_r' + round + '.log 2>&1 & echo $!` — note the PID.\\n"
        f"   b. Poll every {S.POLL_INTERVAL_S}s: `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE` (cap {S.d4_poll_cap()} polls / ~20min). Past cap → `kill <PID>`, report D4 as TIMEOUT — never invent a test\\'s delivered/excluded status.\\n"
        "   c. DONE → `cat /tmp/d4_g3_r' + round + '.log`. FAIL → add missing tests, re-run this backgrounded step."
    ),
    "5. CRG-ARCH: `' + PY + ' ' + REPO + '/harness_cli.py crg-arch-check --project ' + REPO + '`. CI enforces this as an absolute floor on every push, independent of the Gate 3 composite score. FAIL → the crg-arch-check output lists the low-cohesion communities / oversized functions; fix the underlying architecture issue, re-run.",
]

_GATE3_SCOPE_RULES = (
    "- DO NOT run advance-phase.\\n"
    "- DO NOT edit gate3_result.json, mutation_score.json, or any evidence file to "
    "fake/reconstruct a score — fix the code, or record TIMEOUT if a backgrounded "
    "call genuinely times out.\\n"
    "- DO NOT cite a framework exclusion/deferral rule you cannot point to in "
    "harness source — an uncited shortfall is real.\\n"
    "- DO NOT modify harness/ (HR-17).\\n"
    "- ONLY run-gate/eval/finalize/spec-coverage/crg-arch-check + code fixes."
)

def generate_phase4() -> str:
    parts = [
        _HEADER_4,
        "",
        _render_meta(
            name="phase4-testing",
            description=(
                "Phase 4 Testing — TEST_PLAN + per-FR GATE1-DELTA + adversarial "
                f"bug hunt + Gate 3 ({S.gate_dim_count(3)} dims) exit (phase4_plan.md v2.12.0)"
            ),
            phases=_META_PHASES_4,
        ),
        "",
        B.RESOLVE_REPO_BLOCK + B.REPO_LOG_LINE + B.BUDGET_GUARD_BLOCK,
        B.HUNT_MODEL_BLOCK,
        "",
        B.WRITE_SCOPE_BLOCK,
        "",
        B.render_schemas(["VERDICT_SCHEMA", "RC_SCHEMA", "FR_STEP_SCHEMA", "ENV_CHECK_SCHEMA", "CTX_SCHEMA", "DELTA_FAST_SCHEMA", "GATE_VERIFY_SCHEMA", "PHASE_SCHEMA", "HUNT_RESULT_SCHEMA", "THREAT_HUNT_SCHEMA", "VERIFY_SCHEMA", "HUNT_RECORD_SCHEMA"]),
        B.render_change_review(),  # Round 114 站6
        B.render_load_file_via_python(),
        B.render_entry_preflight(
            phase=4, gate_num=2, gate_owner_phase=3, prev_phase=3,
            extra_note=(
                "- DO NOT generate TEST_PLAN / run TDD / run-gate / bug hunt.\\n"
                "- DO NOT run advance-phase/push-milestone.\\n"
            ),
        ),
        _render_test_plan(),
        B.render_env_check(phase=4),
        B.render_load_frs(phase=4, include_fr_titles=True),
        B.render_per_fr_delta(
            phase=4,
            forbidden_note="- DO NOT run run-gate / bug-hunt / advance-phase / push-milestone.\\n",
            verifier_role="TEST VERIFIER",
            use_fr_titles=True,
            # Round 12 站1: restored verbatim from the pre-migration file
            # (840d637^ lines 303-304) — the station-3a migration dropped
            # these declarations while keeping the mid_milestone_step that
            # reads them; first sim-testbed run caught the ReferenceError.
            pre_loop_state=(
                "let p4MidPushed = false\n"
                "const p4MidThreshold = Math.ceil(frIds.length / 2)  // PUSH ⑤ trigger: ≥50% FRs Gate 1 PASS\n"
            ),
            mid_milestone_step=(
                "\n"
                "  // PUSH ⑤ p4-mid — fire once when ≥50% FRs have Gate 1 PASS (but not yet all done).\n"
                "  if (!p4MidPushed && gate1Pass.length >= p4MidThreshold && gate1Pass.length < frIds.length) {\n"
                "    p4MidPushed = true\n"
                "    log('  ≥50% FRs Gate 1 PASS (' + gate1Pass.length + '/' + frIds.length + ') — pushing p4-mid milestone')\n"
                "    await agent(\n"
                "      'YOU ARE THE P4 MID-MILESTONE PUSHER (≥50% FRs Gate 1 PASS).\\n'\n"
                "      + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
                "      + '0. GUARD: `git -C ' + REPO + ' log --oneline --grep=\"P4-mid)\" -1`. If exists, report \"MILESTONE: PASS (already pushed)\" and stop.\\n'\n"
                "      + '1. Command: `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p4-mid --project ' + REPO\n"
                "      + ' --fr-done ' + gate1Pass.length + ' --fr-total ' + frIds.length + ' --fr-ids ' + gate1Pass.join(',') + '`\\n'\n"
                "      + 'Writes HANDOVER.md + commits + pushes. If a hook blocks, reword commit to start with `chore(harness):` (NOT --no-verify), retry.\\n\\n'\n"
                "      + 'Report: \"MILESTONE: PASS|FAIL — <details>\".\\n\\n'\n"
                "      + 'SCOPE RULES:\\n- DO NOT run run-gate / bug-hunt / advance-phase.\\n- ONLY push-milestone p4-mid.',\n"
                "      { label: 'milestone-p4-mid', phase: 'Per-FR Delta', agentType: 'general-purpose' },\n"
                "    )\n"
                "  }\n"
            ),
        ),
        _render_declared_tests(),
        _render_coverage(),
        _render_bug_hunt(),
        B.render_artifacts_commit(
            paths=["04-testing", ".methodology/bug_hunt_report.json", ".methodology/bug_hunt_targets.json", ".methodology/decision_logs"],
            commit_msg="chore(p4): test-plan + coverage + bug-hunt artifacts",
            phase=4,
        ),
        B.render_gate_loop(
            gate_num=3, phase=4,
            log_msg=f"Gate 3 exit ({S.render_gate_dims_summary(3)})",
            prompt_steps=_GATE3_STEPS,
            pass_line_desc=S.render_gate_pass_line(3, d4_threshold=_D4_THRESHOLD_P4),
            scope_rules=_GATE3_SCOPE_RULES,
            d4_threshold=_D4_THRESHOLD_P4,
            on_fail_error_msg="Gate 3 did not PASS in 3 rounds (HR-08); deferred_fixes.md written to .methodology/ (advance-phase exit 17 until resolved)",
            include_manifest_integrity=False,
            deferred_fixes_step=B.render_deferred_fixes_step(
                gate_num=3, phase=4, d4_threshold=_D4_THRESHOLD_P4,
            ),
        ),
        B.render_preview_next_phase(4),
        B.render_advance_loop(
            phase=4, next_phase=5,
            precheck_steps=[
                "PUSH ⑥ p4-pre-gate3 (skip if `jq -r --arg t p4-pre-gate3 \\'.last_milestone_head[$t] // empty\\' ' + REPO + '/.methodology/state.json` prints a sha): `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p4-pre-gate3 --project ' + REPO + ' --fr-ids ' + gate1Pass.join(',') + '`.",
            ],
            scope_extra="- DO NOT re-do P4 testing.\\n",
            only_extra="push-milestone p4-pre-gate3 + ",
            log_msg="p4-pre-gate3 milestone + advance-phase --completed 4 (TDD-PRECHECK enforced)",
            on_pass_extra=(
                "    // [Phase close cleanup] advance-phase only commits its own target paths\n"
                "    // (state.json, HANDOVER.md, CLAUDE.md, phase plan). Post-advance edits\n"
                "    // (pragma annotations, style fixes, test additions, deleted scaffolding)\n"
                "    // remain uncommitted, leaving a dirty tree for the next phase. Commit\n"
                "    // everything advance-phase didn't include. This agent is SCOPED to git\n"
                "    // housekeeping only — no code, no phase transitions.\n"
                "    await agent(\n"
                "      'Run ONE bash command and report its stdout/stderr:\\n'\n"
                "      + '`git -C ' + REPO + ' add -A && git -C ' + REPO + ' commit -m \"chore: phase 4 clean-up\" || true`\\n\\n'\n"
                "      + 'Report: the verbatim stdout/stderr of that command.\\n\\n'\n"
                "      + 'SCOPE RULES:\\n- DO NOT run any code, tests, or phase transitions.\\n- ONLY the git commit above.',\n"
                "      { label: 'cleanup-r' + round, phase: 'Advance', agentType: 'general-purpose' },\n"
                "    )\n"
            ),
        ),
        B.render_sync_verified(),
        (
            "\nlog('Phase 4 workflow complete. Open .methodology/phase5_plan.md to continue.')\n"
            "return {\n"
            + S.render_phase_complete_marker()
            +
            "  phase: 4,\n"
            "  fr_count: frIds.length,\n"
            "  gate1_pass: gate1Pass,\n"
            "  gate3_status: gate3Pass ? 'PASS' : 'unknown',\n"
            "  advance_status: 'PASS',\n"
            "  artifacts: ['04-testing/TEST_PLAN.md', '04-testing/TEST_RESULTS.md', '04-testing/COVERAGE_REPORT.md', '.methodology/bug_hunt_report.json', '.methodology/gate3_result.json', 'HANDOVER.md'],\n"
            "  notes: 'Phase 4 complete per phase4_plan.md v2.12.0. All FRs Gate 1 PASS + bug hunt done + Gate 3 PASS. Phase 5 (Verification) ready.',\n"
            "}\n"
        ),
    ]
    return "\n".join(p for p in parts if p is not None)
