// CRG-guided bug hunt across harness-methodology codebase.
// Excludes repo-root loose files and tests/.
// Output: concise MD report with confirmed bugs.

export const meta = {
  name: 'bug-hunt-crg',
  description: 'CRG-guided multi-lens bug hunt with adversarial verification',
  phases: [
    { title: 'CRG Recon' },
    { title: 'Risk Triage' },
    { title: 'Multi-lens Hunt' },
    { title: 'Adversarial Verify' },
    { title: 'Synthesize Report' },
  ],
}

// ---------- Schemas ----------

const RECON_SCHEMA = {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    top_hubs: { type: 'array', items: { type: 'object' } },
    top_bridges: { type: 'array', items: { type: 'object' } },
    knowledge_gaps: { type: 'array', items: { type: 'object' } },
    large_functions: { type: 'array', items: { type: 'object' } },
    surprising_connections: { type: 'array', items: { type: 'object' } },
    risky_files: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          path: { type: 'string' },
          risk_signals: { type: 'array', items: { type: 'string' } },
        },
        required: ['path', 'risk_signals'],
      },
    },
  },
  required: ['risky_files', 'summary'],
}

const TARGETS_SCHEMA = {
  type: 'object',
  properties: {
    targets: {
      type: 'array',
      maxItems: 6,
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          functions_to_inspect: { type: 'array', items: { type: 'string' } },
          priority: { type: 'number' },
          reason: { type: 'string' },
        },
        required: ['file', 'reason', 'priority'],
      },
    },
  },
  required: ['targets'],
}

const BUG_SCHEMA = {
  type: 'object',
  properties: {
    bugs: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          line: { type: 'number' },
          function: { type: 'string' },
          title: { type: 'string' },
          description: { type: 'string' },
          evidence: { type: 'string' },
          severity: { type: 'string', enum: ['low', 'medium', 'high', 'critical'] },
        },
        required: ['file', 'line', 'title', 'description', 'evidence', 'severity'],
      },
    },
  },
  required: ['bugs'],
}

const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    real: { type: 'boolean' },
    confidence: { type: 'number' },
    reason: { type: 'string' },
  },
  required: ['real', 'reason'],
}

// ---------- Scope guard ----------
// Excluded paths: tests/ and any loose file at the repo root (no subdirectory).
// Source dirs: harness/, agent_personas/, 03-development/, 04-testing/,
//              05-verification/, 06-quality/, 07-risk/, 08-config/.
const SCOPE_RULE = `
SCOPE (strict):
  INCLUDE only files under these directories:
    harness/**, agent_personas/**, 03-development/**, 04-testing/**,
    05-verification/**, 06-quality/**, 07-risk/**, 08-config/**
  EXCLUDE: tests/**, repo-root loose files, .venv/**, __pycache__/**,
           .git/**, .claude/**, .code-review-graph/**, node_modules/**.
  If a finding's file is outside the include list, drop it.
`

// ============================================================
// Phase 1: CRG Recon — four parallel recon agents cover different signals
// ============================================================
phase('CRG Recon')
const recon = await parallel([
  () => agent(
    `Use CRG tools to map architectural risk in /Users/johnny/projects/harness-methodology.

Call (in order):
  1. get_architecture_overview (detail_level=minimal)
  2. get_hub_nodes (top_n=15)
  3. get_bridge_nodes (top_n=10)

For each hub/bridge node, note: name, file, degree/centrality, why it matters.

${SCOPE_RULE}

Output: structured JSON matching schema.`,
    { label: 'recon:arch', phase: 'CRG Recon', schema: RECON_SCHEMA },
  ),
  () => agent(
    `Use CRG tools to surface structural weaknesses in /Users/johnny/projects/harness-methodology.

Call (in order):
  1. get_knowledge_gaps
  2. find_large_functions (min_lines=80, limit=20)
  3. get_surprising_connections (top_n=15)

For each gap/large-fn/surprise, note: identifier, file, why it might harbor bugs.

${SCOPE_RULE}

Output: structured JSON matching schema.`,
    { label: 'recon:gaps', phase: 'CRG Recon', schema: RECON_SCHEMA },
  ),
  () => agent(
    `Use CRG tools to surface test-coverage weaknesses in /Users/johnny/projects/harness-methodology.

Call (in order):
  1. get_suggested_questions
  2. traverse_graph_tool (query='high-degree function with no test', depth=2)
  3. query_graph_tool (pattern='tests_for', target='harness', detail_level=minimal)

Identify files where complex logic exists but no TESTED_BY edges back to it.

${SCOPE_RULE}

Output: structured JSON matching schema.`,
    { label: 'recon:tests', phase: 'CRG Recon', schema: RECON_SCHEMA },
  ),
  () => agent(
    `Use CRG tools to surface change-risk and flow vulnerabilities in /Users/johnny/projects/harness-methodology.

Call (in order):
  1. detect_changes_tool (base=HEAD, detail_level=minimal)
  2. list_flows_tool (sort_by=criticality, limit=10, detail_level=minimal)
  3. get_affected_flows_tool

Cross-reference recent git changes (last 5 commits) with high-criticality flows.
Note any function in a high-criticality flow that has been recently modified.

${SCOPE_RULE}

Output: structured JSON matching schema.`,
    { label: 'recon:flows', phase: 'CRG Recon', schema: RECON_SCHEMA },
  ),
])

// ============================================================
// Phase 2: Risk Triage — synthesize recon into 4-6 priority targets
// ============================================================
phase('Risk Triage')
const reconText = recon.filter(Boolean).map(r => JSON.stringify(r, null, 2)).join('\n\n---\n\n')
const triage = await agent(
  `You are triaging CRG recon output to pick 4-6 highest-risk TARGETS for a bug hunt.

CRG RECON OUTPUT (4 parallel recon passes):
${reconText}

Pick targets that:
  - appear in 2+ recon passes (cross-confirmed risk)
  - are complex (large functions, hubs, bridges, knowledge gaps)
  - are in high-criticality execution flows
  - have weak test coverage

${SCOPE_RULE}

For each target, give: file path, list of specific functions/methods to inspect (1-5), priority 1-10, and a one-sentence reason citing the CRG signals.

Output: structured JSON matching schema.`,
  { label: 'triage', phase: 'Risk Triage', schema: TARGETS_SCHEMA },
)

if (!triage || !triage.targets || triage.targets.length === 0) {
  log('No targets identified — aborting.')
  return { report: '# Bug Hunt\n\nNo targets identified from CRG recon.' }
}
log(`Triage selected ${triage.targets.length} targets`)

// ============================================================
// Phase 3: Multi-lens Hunt — pipeline per target, parallel lenses inside
// ============================================================
phase('Multi-lens Hunt')

const LENSES = [
  {
    key: 'correctness',
    prompt: 'Find LOGIC bugs: wrong conditions, off-by-one, wrong operator, state mutations, missing returns, broken invariants. Read the code carefully; do not invent issues.',
  },
  {
    key: 'error_handling',
    prompt: 'Find ERROR-HANDLING bugs: swallowed exceptions, missing raises, broad except, leaked resources on error path, missing None checks, wrong error type, missing cleanup (try/finally, context manager).',
  },
  {
    key: 'input_validation',
    prompt: 'Find INPUT-VALIDATION bugs: missing boundary checks, type confusion, untrusted input reaching a dangerous sink, path traversal, injection, missing length checks, dict-key KeyError paths.',
  },
  {
    key: 'concurrency_async',
    prompt: 'Find CONCURRENCY/ASYNC bugs: shared mutable state, race conditions, missing locks, async functions blocking on sync I/O, unawaited coroutines, deadlocks, ordering assumptions.',
  },
]

const huntResults = await pipeline(
  triage.targets,
  target => parallel(LENSES.map(lens => () =>
    agent(
      `You are hunting for "${lens.key}" bugs in a single file.

TARGET FILE: ${target.file}
FUNCTIONS TO INSPECT: ${target.functions_to_inspect.join(', ')}
WHY THIS TARGET: ${target.reason}

${SCOPE_RULE}

INSTRUCTIONS:
  1. Use CRG to navigate: query_graph_tool, traverse_graph_tool, get_impact_radius
     to understand call sites and dependencies. Do not stop at the surface.
  2. Read the actual code (use Read tool) — every finding must cite a real file:line.
  3. ${lens.prompt}
  4. For each finding, give: file, line, function, title, description, evidence (quote code), severity.
  5. If you find nothing real, return {"bugs": []} — do not pad.

Output: structured JSON matching schema.`,
      { label: `hunt:${lens.key}:${target.file.split('/').pop()}`, phase: 'Multi-lens Hunt', schema: BUG_SCHEMA },
    ),
  )).then(lensResults => {
    const allBugs = lensResults.filter(Boolean).flatMap(r => r.bugs || [])
    return { target, bugs: allBugs }
  }),
)

// ============================================================
// Phase 4: Adversarial Verify — 3 skeptical verifiers per finding
// ============================================================
phase('Adversarial Verify')

// Flatten all findings into a single list for verification
const allFindings = huntResults.flatMap(hr =>
  (hr.bugs || []).map(b => ({ ...b, target_file: hr.target.file }))
)

if (allFindings.length === 0) {
  log('No findings to verify — going straight to synthesis.')
}

const verified = await pipeline(
  allFindings,
  finding => parallel([
    () => agent(
      `Adversarially verify this potential bug. Default to refuted=true if uncertain.

FINDING:
  File: ${finding.file}
  Line: ${finding.line}
  Function: ${finding.function}
  Title: ${finding.title}
  Description: ${finding.description}
  Evidence: ${finding.evidence}
  Severity claimed: ${finding.severity}

LENS: correctness
  Is this actually a bug? Could the "wrong" code be intentional? Is the cited line really the cause, or is it a downstream effect?

Use Read tool to inspect the actual code. Output JSON: {real, confidence 0-1, reason}.`,
      { label: `verify:correctness:${finding.file}:${finding.line}`, phase: 'Adversarial Verify', schema: VERIFY_SCHEMA },
    ),
    () => agent(
      `Adversarially verify this potential bug. Default to refuted=true if uncertain.

FINDING:
  File: ${finding.file}
  Line: ${finding.line}
  Function: ${finding.function}
  Title: ${finding.title}
  Description: ${finding.description}
  Evidence: ${finding.evidence}

LENS: reproduction
  Can this bug actually be triggered by a realistic input/call path? Trace the call chain (use CRG query_graph_tool callers_of). If it requires constructing a contrived scenario that no caller would produce, refuted=true.

Output JSON: {real, confidence 0-1, reason}.`,
      { label: `verify:repro:${finding.file}:${finding.line}`, phase: 'Adversarial Verify', schema: VERIFY_SCHEMA },
    ),
    () => agent(
      `Adversarially verify this potential bug. Default to refuted=true if uncertain.

FINDING:
  File: ${finding.file}
  Line: ${finding.line}
  Function: ${finding.function}
  Title: ${finding.title}
  Description: ${finding.description}
  Evidence: ${finding.evidence}
  Severity claimed: ${finding.severity}

LENS: severity
  Even if real, is the severity accurate? Distinguish: critical (data loss, security, crash) > high (incorrect output, broken workflow) > medium (edge case, degraded behavior) > low (style, theoretical). If the bug is real but severity is overstated, mark real=true but state the actual severity in the reason.

Output JSON: {real, confidence 0-1, reason}.`,
      { label: `verify:severity:${finding.file}:${finding.line}`, phase: 'Adversarial Verify', schema: VERIFY_SCHEMA },
    ),
  ]).then(votes => {
    const realVotes = votes.filter(Boolean).filter(v => v.real).length
    const survived = realVotes >= 2
    // Pick the highest-confidence real verdict for severity rationale, else refuted reason
    const realReasons = votes.filter(Boolean).filter(v => v.real)
    const bestReal = realReasons.sort((a, b) => (b.confidence || 0) - (a.confidence || 0))[0]
    return {
      finding,
      survived,
      votes: realVotes,
      total_voters: votes.length,
      verdict_reason: bestReal ? bestReal.reason : (votes.filter(Boolean)[0]?.reason || 'no verdict'),
      actual_severity: finding.severity, // may be corrected in synthesis
    }
  }),
)

const confirmed = verified.filter(v => v && v.survived)
log(`${confirmed.length}/${verified.length} findings survived adversarial verify`)

// ============================================================
// Phase 5: Synthesize Report — concise MD
// ============================================================
phase('Synthesize Report')
const report = await agent(
  `Produce a CONCISE Markdown bug-hunt report.

INPUT:
${JSON.stringify(confirmed, null, 2)}

REPORT STRUCTURE (keep it tight — no fluff):

# Bug Hunt Report (CRG-guided)
- Date: 2026-06-11
- Codebase: harness-methodology
- Scope: harness/, agent_personas/, 03-08* (excluded: tests/, repo-root files)
- Method: CRG recon → risk triage → multi-lens hunt (4 lenses) → 3-vote adversarial verify

## Summary
- N findings confirmed out of M total candidates.
- 1-2 sentence takeaway.

## Confirmed Bugs
For each confirmed bug, in this exact compact format:

### [SEVERITY] file:line — title
- **Function**: name
- **Bug**: 1-2 sentence description
- **Evidence**: 1-3 line code quote
- **Why real**: 1 sentence (best verifier reason)

Order: critical → high → medium → low.

## Investigated but Not Confirmed
- file:line — title (1 line, why refuted)

## CRG Signals That Surfaced the Most
- 2-4 bullets naming specific CRG tools/signals and the modules they flagged.

RULES:
  - No fabricated findings. If confirmed list is empty, say so plainly.
  - Cite real file paths and line numbers from the input.
  - Total report under 200 lines.`,
  { label: 'synth', phase: 'Synthesize Report' },
)

return { report }
