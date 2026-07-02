export const meta = {
  name: 'bug-hunt-crg-non-test',
  description: 'CRG-guided multi-lens bug hunt across non-test source',
  phases: [
    { title: 'CRG Priority Map' },
    { title: 'Multi-Lens Hunt' },
    { title: 'Adversarial Verify' },
    { title: 'Synthesize Report' },
  ],
}

const PROJECT = '/Users/johnny/projects/harness-methodology'

// ── Schemas ───────────────────────────────────────────────────────────────
const PRIORITY_SCHEMA = {
  type: 'object',
  properties: {
    scope: {
      type: 'object',
      properties: { files: { type: 'number' }, languages: { type: 'array', items: { type: 'string' } } },
      required: ['files', 'languages'],
    },
    communities_at_risk: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string' },
          size: { type: 'number' },
          cohesion: { type: 'number' },
          reason: { type: 'string' },
          members_qualified: { type: 'array', items: { type: 'string' } },
        },
        required: ['name', 'size', 'cohesion', 'reason', 'members_qualified'],
      },
    },
    untested_functions: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          qualified_name: { type: 'string' },
          file: { type: 'string' },
          line_start: { type: 'number' },
          line_end: { type: 'number' },
        },
        required: ['qualified_name', 'file', 'line_start', 'line_end'],
      },
    },
    large_functions: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          qualified_name: { type: 'string' },
          file: { type: 'string' },
          lines: { type: 'number' },
        },
        required: ['qualified_name', 'file', 'lines'],
      },
    },
  },
  required: ['scope', 'communities_at_risk', 'untested_functions', 'large_functions'],
}

const FINDING_SCHEMA = {
  type: 'object',
  properties: {
    file: { type: 'string' },
    line: { type: 'number' },
    function: { type: 'string' },
    lens: { type: 'string', enum: ['correctness', 'error_handling', 'security', 'concurrency', 'type_boundary'] },
    defect: { type: 'string' },
    failure_scenario: { type: 'string' },
    suggested_fix: { type: 'string' },
    severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
  },
  required: ['file', 'line', 'function', 'lens', 'defect', 'failure_scenario', 'severity'],
}

const HUNT_SCHEMA = {
  type: 'object',
  properties: {
    cluster: { type: 'string' },
    findings: { type: 'array', items: FINDING_SCHEMA },
  },
  required: ['cluster', 'findings'],
}

const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['confirmed', 'refuted', 'inconclusive'] },
    reason: { type: 'string' },
    reproduction: { type: ['string', 'null'] },
  },
  required: ['verdict', 'reason'],
}

const REPORT_SCHEMA = {
  type: 'object',
  properties: {
    markdown: { type: 'string' },
    summary: { type: 'string' },
    confirmed_count: { type: 'number' },
  },
  required: ['markdown', 'summary', 'confirmed_count'],
}

// ── Phase 1: CRG Priority Map ─────────────────────────────────────────────
phase('CRG Priority Map')
const priority = await agent(
  `You are a discovery agent. Use the code-review-graph MCP tools (call them in parallel when independent) to enumerate priority bug-hunt targets in NON-TEST source code at ${PROJECT}.

EXCLUDE any node whose file path contains: /tests/, /test/, _test.py, .test., /spec/, /__tests__/, .spec.,/conftest.py. Only consider non-test source.

RUN THESE TOOLS:
1. list_graph_stats → scope summary
2. list_communities (sort_by "cohesion", ascending, detail_level "minimal") → identify LOW-cohesion communities (loose = higher bug risk). Filter out name "tests-no" (test community).
3. find_large_functions (min_lines=80, kind="Function", limit=30) → flag oversized functions
4. For each large function, query_graph pattern "tests_for" target=<qualified_name> → if no callers classified as Test, it's effectively untested for its size
5. detect_changes (base="HEAD~3") → identify files changed recently that have NO test coverage (TESTED_BY edges = 0)
6. get_community on the top 2-3 risky communities with include_members=true (use members_qualified, do NOT paste whole tool output which exceeds token limits — fetch only the names list)

OUTPUT (return as a single JSON object, ≤ 700 tokens, qualified names that next phase can use directly):
{
  "scope": {"files": <int>, "languages": [<str>]},
  "communities_at_risk": [{"name","size","cohesion","reason","members_qualified":[<qualified_name>, ...]}],
  "untested_functions": [{"qualified_name","file","line_start","line_end"}],
  "large_functions": [{"qualified_name","file","lines"}]
}

CONSTRAINTS:
- Top 8 communities_at_risk sorted by (size desc, cohesion asc), drop "tests-no"
- Top 20 untested_functions by line count
- Top 20 large_functions by line count
- No prose, just the JSON.`,
  { label: 'crg-priority-map', phase: 'CRG Priority Map', schema: PRIORITY_SCHEMA }
)

// ── Phase 2: parallel multi-lens hunt per priority cluster ────────────────
phase('Multi-Lens Hunt')

const HUNT_PROMPT = (clusterName, targets) => `
You are a multi-lens bug hunter. Find REAL bugs (not style/lint) in this cluster.

CLUSTER: ${clusterName}
TARGETS (qualified names from CRG, possibly mixed with communities):
${JSON.stringify(targets, null, 2)}

LENSES — scan each function/file with ALL of these:
1. CORRECTNESS: off-by-one, wrong default, mutable default arg, wrong comparison (== vs is), exception swallowing, None/NaN propagation, division-by-zero, wrong base case in recursion
2. ERROR HANDLING: bare except/except Exception, missing return on error path, swallowed errors, wrong exit codes, missing finally, OSError vs IOError confusion
3. SECURITY: path traversal (../), injection (regex/shell/sql/format string), unsafe deserialization (yaml.load / pickle / marshal), hardcoded secrets, weak hash, SSRF on user URL, open redirect
4. CONCURRENCY/RESOURCE: file handles not closed, missing context manager, leaked temp files, double-close, threading race (TOCTOU), fsync missing before atomic-rename
5. TYPE/BOUNDARY: wrong type coercion, JSON shape mismatch, dict KeyError on user input, NoneType crashes, list index out of range on empty input

RULES:
- DO NOT review test files (skip if path contains /test/, /tests/, _test.py, .test., /spec/, conftest.py)
- Use Read tool to actually open files; do NOT guess from names
- Each finding MUST be CONCRETE: file:line + the exact input that triggers it
- Skip TODO/FIXME comments unless they reveal an obvious bug
- If you find nothing in this cluster, return {"cluster":"${clusterName}","findings":[]}

RETURN JSON:
{"cluster":"${clusterName}","findings":[{"file","line","function","lens","defect","failure_scenario","suggested_fix","severity"}]}

Max 8 findings per cluster. Focus on severity ≥ medium.
`

const clusters = [
  { name: 'risk-community-1', targets: (priority.communities_at_risk[0]?.members_qualified || []).slice(0, 25) },
  { name: 'risk-community-2', targets: (priority.communities_at_risk[1]?.members_qualified || []).slice(0, 25) },
  { name: 'risk-community-3', targets: (priority.communities_at_risk[2]?.members_qualified || []).slice(0, 25) },
  { name: 'large-functions', targets: priority.large_functions.slice(0, 25) },
  { name: 'untested-functions', targets: priority.untested_functions.slice(0, 25) },
].filter(c => Array.isArray(c.targets) ? c.targets.length > 0 : false)

const hunts = await parallel(clusters.map(c => () =>
  agent(HUNT_PROMPT(c.name, c.targets), {
    label: `hunt:${c.name}`,
    phase: 'Multi-Lens Hunt',
    schema: HUNT_SCHEMA,
  })
))

// ── Phase 3: adversarial verify (refute-by-default) ────────────────────────
phase('Adversarial Verify')

const raw = hunts.filter(Boolean).flatMap(h => h.findings.map(f => ({ ...f, cluster: h.cluster })))
// Cap to top 25 by severity for token budget
const SEV_RANK = { critical: 4, high: 3, medium: 2, low: 1 }
const ranked = [...raw].sort((a, b) => (SEV_RANK[b.severity] || 0) - (SEV_RANK[a.severity] || 0)).slice(0, 25)

const verified = await parallel(ranked.map(f => () =>
  agent(
    `You are an adversarial verifier. Your job is to REFUTE this claimed bug.

FINDING:
- File: ${f.file}
- Line: ${f.line}
- Function: ${f.function}
- Defect: ${f.defect}
- Failure scenario: ${f.failure_scenario}

INSTRUCTIONS:
- DEFAULT to verdict="refuted" unless the bug is REAL and reproducible from the scenario as stated
- Use Read to actually OPEN ${f.file} around line ${f.line}. Verify the exact code path. Quote the relevant lines.
- If failure_scenario requires unstated assumptions (specific input shape, prior state), mark "inconclusive"
- If the code has a guard, precondition, or upstream check that prevents the scenario, mark "refuted" with the guarding line cited
- If you can construct a concrete failing input matching the stated scenario, mark "confirmed" and paste the input

DO NOT mark "confirmed" based on plausibility alone. Only confirmed when you can trace the actual buggy path.

RETURN JSON: {"verdict":"confirmed"|"refuted"|"inconclusive","reason":"<cited lines>","reproduction":<string|null>}`,
    {
      label: `verify:${f.file.split('/').pop()}:${f.line}`,
      phase: 'Adversarial Verify',
      schema: VERIFY_SCHEMA,
    }
  )
))

// ── Phase 4: synthesize final report ──────────────────────────────────────
phase('Synthesize Report')

const annotated = ranked.map((f, i) => ({ ...f, verify: verified[i] }))
const confirmed = annotated.filter(f => f.verify?.verdict === 'confirmed')

const final = await agent(
  `Write a concise Markdown bug report in 繁體中文/English mix as natural.

USE ONLY these findings (each independently verified by adversarial referee):
${JSON.stringify(confirmed, null, 2)}

METHOD CONTEXT:
- Scope: ${JSON.stringify(priority.scope)} files in non-test source
- Communities scanned: ${priority.communities_at_risk.map(c => c.name).join(', ')}
- Hunt lenses: correctness / error_handling / security / concurrency / type_boundary
- Verification: refute-by-default, read actual code at file:line

REQUIRED FORMAT (≤ 900 tokens total):

# Bug Hunt Report — Non-test Source
**Date**: 2026-07-02
**Scope**: <files> files | ${priority.scope.languages.join(', ')}
**Verified findings**: ${confirmed.length} confirmed / ${ranked.length} reviewed / ${raw.length} raw

## TL;DR
[2-3 sentences: where bugs cluster, dominant pattern]

## Confirmed Bugs (by severity desc)

### [<SEVERITY>] <one-line title>
- **File**: path:line
- **Function**: name
- **Defect**: 一句話
- **Failure scenario**: 具體輸入/序列
- **Referee reason**: cited guard/precondition/missing
- **Fix**: 建議

[max 12 bugs in body; if more, summarize "另有 N 個已驗證次要 bug 略"]

## Bug Density Map
[top 2-3 communities/modules with most confirmed bugs — one line each]

## Recommended Next Steps
[2-3 actionable items]

CONSTRAINTS:
- NO markdown formatting beyond what's shown
- NO "might be", "could possibly" hedging in confirmed section
- Each finding cites the file path exactly
- Write as engineer-to-engineer`,
  { label: 'write-report', phase: 'Synthesize Report', schema: REPORT_SCHEMA }
)

return { markdown: final.markdown, confirmed_count: final.confirmed_count }
