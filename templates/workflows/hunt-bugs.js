// Workflow: hunt-bugs — Gate-3 adversarial bug hunt (reference implementation).
//
// AUTHORITATIVE PROTOCOL: harness/ssi/prompts/hunt_bugs.md — this file is a
// Claude Code dynamic-workflow rendering of it (canonicalized from the
// tts-new hunt that found 50 confirmed bugs after a near-perfect Gate 4).
// Runtime-specific; portable installs follow hunt_bugs.md manually.
//
// Prerequisites:
//   1. CRG graph built:  code-review-graph build
//   2. Targeting manifest: python harness_cli.py bug-hunt-targets --project .
//      → .methodology/bug_hunt_targets.json  (high_risk ×3 lenses, standard ×1)
//   3. Run hunt/verify with a model DIFFERENT from the one that wrote the code.
//
// Usage:
//   Workflow({ scriptPath: '.claude/workflows/hunt-bugs.js',
//              args: { repo: '/abs/path/to/project', timestamp: '2026-06-11' } })
//
// Outputs:
//   .methodology/bug_hunt_report.json   (gate input — schemas/bug_hunt_report.schema.json)
//   03-development/.audit/bug-report-<timestamp>.md  (human report, 繁體中文)

export const meta = {
  name: 'hunt-bugs',
  description: 'CRG-guided adversarial bug hunt (manifest-driven targets, refute+confirm verify, JSON+markdown report)',
  phases: [
    { title: 'Gather context' },
    { title: 'Hunt' },
    { title: 'Verify' },
    { title: 'Synthesize' },
  ],
}

const REPO = (args && args.repo) || process.cwd()
const TIMESTAMP = (args && args.timestamp) || new Date().toISOString().slice(0, 10)

// === Targets from the manifest (replaces hand-maintained module lists) ===
const fs = await import('node:fs')
const manifestPath = `${REPO}/.methodology/bug_hunt_targets.json`
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf-8'))
const HIGH_RISK = manifest.high_risk.map(m => ({
  name: m.name, path: m.path, absPath: `${REPO}/${m.path}`,
  risk: (m.reasons || []).join('; '),
}))
const STANDARD = manifest.standard.map(m => ({
  name: m.name, path: m.path, absPath: `${REPO}/${m.path}`,
  survivors: m.survivors || 0,
}))
const SURVIVORS = manifest.mutation_survivors || []

// Lens definitions — verbatim from hunt_bugs.md (do not dilute).
const LENSES = [
  { key: 'correctness',
    focus: 'Business logic errors, boundary conditions, null/empty handling, off-by-one, type mismatches, incorrect assumptions about input data.' },
  { key: 'concurrency',
    focus: 'Race conditions, thread safety, async/await issues, shared mutable state, lock ordering, ordering of side effects, lifecycle of long-lived objects across awaits.' },
  { key: 'resilience',
    focus: 'Error handling gaps, missing timeouts, broken fallbacks, resource leaks (files/sockets/connections/child procs), partial-failure handling, error swallowing, NFR compliance for degraded modes.' },
  { key: 'general',
    focus: 'Any concrete, reachable bug — wrong return type, broken validation, dead branch, leaked resource, missing rollback, incorrect status code, log/PII leak, input size limit (DoS), wrong default. Skip stylistic nits and hypotheticals.' },
]

const FINDING_SCHEMA = {
  type: 'object',
  properties: {
    module: { type: 'string' },
    lens: { type: 'string' },
    severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
    title: { type: 'string' },
    description: { type: 'string' },
    file: { type: 'string' },
    line_start: { type: 'number' },
    line_end: { type: 'number' },
    code_snippet: { type: 'string' },
    reasoning: { type: 'string' },
    suggested_fix: { type: 'string' },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
  },
  required: ['module', 'lens', 'severity', 'title', 'description', 'file',
             'line_start', 'line_end', 'reasoning', 'suggested_fix', 'confidence'],
  additionalProperties: false,
}
const HUNT_RESULT_SCHEMA = {
  type: 'object',
  properties: { findings: { type: 'array', items: FINDING_SCHEMA } },
  required: ['findings'],
  additionalProperties: false,
}
const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    is_real: { type: 'boolean' },
    refutation_attempt: { type: 'string' },
    evidence: { type: 'string' },
    severity_agrees: { type: 'boolean' },
  },
  required: ['is_real', 'refutation_attempt', 'evidence', 'severity_agrees'],
  additionalProperties: false,
}

// === Phase 1: Gather context ===
phase('Gather context')

const ALL_FILES = [...HIGH_RISK, ...STANDARD]
const survivorHints = SURVIVORS.length
  ? `\nMUTATION SURVIVORS (behaviors NO test asserts — mark their functions PRIORITY):\n${
      SURVIVORS.slice(0, 40).map(s => `- ${s.file}${s.line ? ':' + s.line : ''} (${s.mutator || 'mutant ' + s.mutant_id})`).join('\n')}`
  : ''

const context = await agent(
  `You are a CRG scout gathering scan context for an adversarial bug hunt.

REPO: ${REPO}

FILES (${HIGH_RISK.length} high-risk + ${STANDARD.length} standard):
${ALL_FILES.map(m => `- ${m.name}: ${m.path}`).join('\n')}

HIGH-RISK (3-lens deep scan — include test coverage + caller graph):
${HIGH_RISK.map(m => `- ${m.name} → ${m.path}\n  RISK: ${m.risk}`).join('\n')}
${survivorHints}

For EACH file: mcp__code-review-graph__get_review_context (include_source=true, max_depth=2, max_lines_per_file=250).
For HIGH-RISK files ALSO: query_graph_tool pattern="tests_for" / "callers_of" on key functions.
Then list_flows limit=10.

OUTPUT (markdown, ≤5000 words): per-module key functions @line, public callers,
test coverage, suspicious patterns, PRIORITY survivor functions; top flows.
Optimize for scan density — this feeds ${HIGH_RISK.length * 3 + STANDARD.length} parallel hunters.`,
  { label: 'crg-scout', phase: 'Gather context', agentType: 'Explore' },
)

// === Phase 2: Hunt ===
phase('Hunt')

const PAIRS = [
  ...HIGH_RISK.flatMap(m => LENSES.filter(l => l.key !== 'general').map(l => ({ module: m, lens: l }))),
  ...STANDARD.map(m => ({ module: m, lens: LENSES.find(l => l.key === 'general') })),
]

const huntResults = await parallel(PAIRS.map(p => () => agent(
  `You are a bug-hunter with LENS="${p.lens.key}".
LENS FOCUS: ${p.lens.focus}

TARGET MODULE: ${p.module.name}
FILE: ${p.module.absPath}
${p.module.risk ? `DECLARED RISK: ${p.module.risk}` : ''}
${p.module.survivors ? `MUTATION SURVIVORS IN THIS FILE: ${p.module.survivors} — prioritize their functions.` : ''}

SHARED CRG CONTEXT:
---
${context}
---

RULES (hunt_bugs.md Phase 2):
1. Read the target file FULLY (absolute path above). Use CRG query_graph_tool for callers/callees/tests_for.
2. Bug must be REACHABLE in the current code path with a concrete failure scenario.
3. Do not re-report what static preflight already blocks (subprocess timeout, TOCTOU, except BaseException, dead config keys).
4. No stylistic nits, no hypotheticals, no invented bugs. Empty findings is a valid result.

OUTPUT strict JSON {"findings": [...]} — each finding: module, lens, severity, title,
description (1-2 sentences), file ("${p.module.path}"), line_start, line_end,
code_snippet (≤8 verbatim lines), reasoning (cite the proving line + trigger scenario),
suggested_fix (≤5 lines), confidence.`,
  { label: `hunt:${p.module.name}/${p.lens.key}`, phase: 'Hunt',
    schema: HUNT_RESULT_SCHEMA, agentType: 'Explore' },
)))

const rawFindings = huntResults.filter(Boolean).flatMap(r => (r && r.findings) || [])
log(`Hunt produced ${rawFindings.length} raw findings across ${PAIRS.length} (module,lens) pairs`)

// === Phase 3: Adversarial verify (refute + confirm per finding) ===
phase('Verify')

const verified = await parallel(rawFindings.map(f => () => parallel([
  () => agent(
    `Try to REFUTE this bug finding. Default is_real=false unless undeniable.

FINDING:
${JSON.stringify(f, null, 2)}

Read ${REPO}/${f.file}. Check: is the cited code at the cited line? Does surrounding
code already guard this case? Is the failure scenario reachable? Cite line numbers.

OUTPUT strict JSON: {"is_real", "refutation_attempt", "evidence", "severity_agrees"}`,
    { label: `refute:${f.module}:${String(f.line_start)}`, phase: 'Verify',
      schema: VERIFY_SCHEMA, agentType: 'Explore' },
  ),
  () => agent(
    `Independently CONFIRM this bug finding. Default is_real=false unless provable.

FINDING:
${JSON.stringify(f, null, 2)}

Read ${REPO}/${f.file}; trace the data flow (does input X reach the buggy line?);
check query_graph_tool tests_for — a passing test on this path suggests it's handled.
Confirm ONLY with a concrete trigger + observed-vs-expected, citing line numbers.

OUTPUT strict JSON: {"is_real", "refutation_attempt", "evidence", "severity_agrees"}`,
    { label: `confirm:${f.module}:${String(f.line_start)}`, phase: 'Verify',
      schema: VERIFY_SCHEMA, agentType: 'Explore' },
  ),
])))

// Strict confirmation rule (hunt_bugs.md Phase 3): 2/2 is_real, or 1/2 whose
// evidence carries a concrete line citation.
const hasLineCitation = (v) => /(:\d+|line\s*\d+|L\d+)/i.test(`${v.evidence} ${v.refutation_attempt}`)
const judged = rawFindings.map((f, i) => {
  const vs = (verified[i] || []).filter(Boolean)
  const real = vs.filter(v => v.is_real)
  const confirmedFlag =
    real.length === 2 || (real.length === 1 && hasLineCitation(real[0]))
  return { ...f, _confirmed: confirmedFlag, _verifiers: vs }
})
const confirmed = judged.filter(f => f._confirmed)
const refuted = judged.filter(f => !f._confirmed)
log(`Adversarial verify: ${confirmed.length} confirmed / ${refuted.length} refuted of ${rawFindings.length}`)

// === Phase 4: Synthesize (JSON gate artifact + human markdown) ===
phase('Synthesize')

const mdPath = `03-development/.audit/bug-report-${TIMESTAMP}.md`
const perModuleSeq = {}
const toEntry = (f) => {
  perModuleSeq[f.module] = (perModuleSeq[f.module] || 0) + 1
  const confirmer = (f._verifiers || []).find(v => v.is_real)
  const refuter = (f._verifiers || []).find(v => !v.is_real)
  return {
    id: `${f.module}#${perModuleSeq[f.module]}`,
    module: f.module, lens: f.lens, severity: f.severity, title: f.title,
    description: f.description, file: f.file,
    line_start: f.line_start, line_end: f.line_end,
    code_snippet: f.code_snippet || '', reasoning: f.reasoning,
    suggested_fix: f.suggested_fix, confidence: f.confidence,
    confirmed: f._confirmed,
    verify_evidence: (confirmer && confirmer.evidence) || '',
    resolution: f._confirmed
      ? { status: 'open' }
      : { status: 'refuted',
          refute_evidence: (refuter && (refuter.refutation_attempt || refuter.evidence)) || 'no verifier confirmed' },
  }
}
const findingsOut = [...confirmed, ...refuted].map(toEntry)

const { execSync } = await import('node:child_process')
const gitSha = execSync('git rev-parse HEAD', { cwd: REPO }).toString().trim()
const report = {
  generated_at: new Date().toISOString(),
  git_sha: gitSha,
  targets_manifest: '.methodology/bug_hunt_targets.json',
  lenses: LENSES.map(l => l.key),
  raw_count: rawFindings.length,
  confirmed_count: confirmed.length,
  refuted_count: refuted.length,
  findings: findingsOut,
}
fs.writeFileSync(`${REPO}/.methodology/bug_hunt_report.json`,
                 JSON.stringify(report, null, 2) + '\n')
log(`Wrote .methodology/bug_hunt_report.json (gate input for adversarial_review)`)

await agent(
  `Write a concise markdown bug report in Traditional Chinese (繁體中文,稱呼讀者「老闆」).

REPO: ${REPO}
REPORT PATH: ${REPO}/${mdPath}
RAW: ${rawFindings.length}  CONFIRMED: ${confirmed.length}  REFUTED: ${refuted.length}

FINDINGS (already verified; read cited files only when elaborating):
${JSON.stringify(findingsOut.map(({ code_snippet, ...rest }) => rest), null, 2)}

STRUCTURE (≤2000 words): # 漏洞掃描報告 / ## 1. 掃描摘要 (module×severity 表) /
## 2. 確認的 Bugs (severity 降序: 模組/位置、問題、證據、修復) /
## 3. 被反駁的 Findings (一句理由) / ## 4. 修復優先順序 / ## 5. 掃描方法。
語氣客觀;引 file:line;不貼 >6 行代碼。用 Write 寫入 REPORT PATH。

最後提醒老闆:confirmed critical/high 需逐條 resolved(fix_commit 或 repro_test)
或 refuted(附證據)後,Gate 3 的 adversarial_review 才會放行
(python harness_cli.py finalize-gate --gate 3 --phase 4 --project .)。`,
  { label: 'synthesize', phase: 'Synthesize', agentType: 'general-purpose' },
)

return {
  rawCount: rawFindings.length,
  confirmedCount: confirmed.length,
  refutedCount: refuted.length,
  reportJson: '.methodology/bug_hunt_report.json',
  reportMd: mdPath,
}
