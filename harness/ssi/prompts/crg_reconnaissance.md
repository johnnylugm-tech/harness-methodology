# CRG Structural Reconnaissance Protocol

Runs **once per session**, after `setup_target.py` (graph already built/verified),
**before the first evaluation round**.

Provides a structural intelligence baseline that:
1. Pre-seeds the issue registry with structural findings the 17 dimensions cannot see
2. Guides dimension evaluation (which files/functions need deepest analysis)
3. Replaces blind first-round discovery with 9 targeted CRG queries (~3,900 tokens)
   vs. reading 10 files at random (~10,000 tokens)

---

## Step 0: Check CRG Availability

```bash
cat .sessi-work/crg_status.json
```

- `available: false` → **skip this entire protocol silently**. Proceed to Step 3a.
- `available: true` → proceed.

---

## Step 1: Orientation (~100 tokens)

Always the first CRG call — returns risk score, top communities, suggested next tools:

```
[USE mcp__code-review-graph__get_minimal_context_tool]
task: "structural reconnaissance before quality evaluation"
```

Record:
- `risk_score` (0.0–1.0) → overall repo structural risk
- `suggested_next_tools` → CRG's own recommended follow-up

---

## Step 2: Baseline Graph Metrics

```
[USE mcp__code-review-graph__list_graph_stats_tool]
```

Record to `crg_reconnaissance.json`:
- `node_count`, `edge_count`, `file_count`, `languages[]`, `last_updated`
- Flag stale graph if `last_updated` > 7 days behind latest commit

---

## Step 3: Auto-Generated Investigation Priorities

```
[USE mcp__code-review-graph__get_suggested_questions_tool]
```

Returns prioritized questions about:
- Bridge nodes needing tests
- Untested hub nodes
- Surprising cross-community coupling
- Thin (low-cohesion) communities
- Untested hotspots

Write all questions to `crg_reconnaissance.json` under `suggested_questions[]`.
These become investigation priorities that guide dimension evaluation depth.

---

## Step 4: High-Risk Components

```
[USE mcp__code-review-graph__get_hub_nodes_tool]
top_n: 15
include_metrics: true
```

```
[USE mcp__code-review-graph__get_bridge_nodes_tool]
```

Flag any hub node that also appears in `get_knowledge_gaps` (Step 6) as
**critical-risk**: high centrality + untested = highest-priority finding.

---

## Step 5: Module Cohesion Map

```
[USE mcp__code-review-graph__list_communities_tool]
sort_by: "cohesion"
min_size: 3
detail_level: "standard"
```

For each community with `cohesion < COHESION_HEALTHY (0.3)` OR `size > 50`, drill in:

```
[USE mcp__code-review-graph__get_community_tool]
community_name: "<name>"
include_members: true
```

**Interpret:**
- `cohesion < COHESION_HEALTHY (0.3)` — functions don't belong together → refactoring/split candidate
- `size > 50` — potential god-module → decomposition candidate
- `cohesion > 0.8` — healthy, well-organized module

Register each problematic community:

```bash
echo '{
  "severity": "medium",
  "message": "Low-cohesion community: <name> (cohesion=<score>, <N> members). Functions have weak internal coupling — split candidate.",
  "file": null,
  "line": null,
  "evidence": "CRG list_communities: cohesion=<score>"
}' > /tmp/finding.json
python3 scripts/issue_tracker.py add \
  .sessi-work/issue_registry.json architecture 0 /tmp/finding.json
```

---

## Step 5a: Execution Flow Handler Coverage (~600 tokens)

List critical execution flows and identify those without error handlers.
This feeds `flow_coverage.score` for the `error_handling` dimension.

```
[USE mcp__code-review-graph__list_flows_tool]
sort_by: "criticality", limit: 50, detail_level: "standard"
```

For each flow returned, record:
- `name`: flow name from CRG
- `has_error_handler`: whether the flow contains error handling nodes
- `depth`: call depth (for prioritization)

Flag flows without error handlers (`has_error_handler: false`):

```bash
# Save flows from the list_flows MCP response (use the tool's raw JSON output)
python3 -c "
import json, sys
# Read from the CRG tool's stdout captured above, not from the (not-yet-written) recon file
flows_raw = '''<paste list_flows JSON output here>'''
flows = json.loads(flows_raw).get('flows', [])
missing = [f for f in flows if not f.get('has_error_handler')]
print(f'{len(missing)}/{len(flows)} flows lack error handlers')
# Write finding for top 5 critical flows without handlers
for f in missing[:5]:
    name = f.get('name', 'unknown')
    print(f'  CRITICAL flow without handler: {name}')
"
```

Filter only flows with `has_error_handler: false` to build the `flows` array
for the reconnaissance output template (below).

---

## Step 6: Untested Hotspots

```
[USE mcp__code-review-graph__get_knowledge_gaps_tool]
```

Cross-reference with hub nodes from Step 4.
Hub nodes that appear in knowledge_gaps = **untested high-risk functions** — register as `high`:

```bash
echo '{
  "severity": "high",
  "message": "Hub node <function> is untested (knowledge gap). Fan-in: <N> callers — failure here cascades widely.",
  "file": "<file>",
  "line": null,
  "evidence": "CRG: hub rank=<N>, in knowledge_gaps=true"
}' > /tmp/finding.json
python3 scripts/issue_tracker.py add \
  .sessi-work/issue_registry.json test_coverage 0 /tmp/finding.json
```

Non-hub knowledge gaps → register as `medium` under `test_coverage`.

---

## Step 7: Unexpected Couplings

```
[USE mcp__code-review-graph__get_surprising_connections_tool]
```

For each surprising connection, assess:
- Expected pattern (plugin architecture, shared utility)? → skip
- Accidental cross-module dependency? → register

```bash
echo '{
  "severity": "medium",
  "message": "Unexpected coupling: <A> → <B> crosses module boundary (<community_a> → <community_b>). Creates hidden dependency.",
  "file": "<file_a>",
  "line": null,
  "evidence": "CRG get_surprising_connections: <description>"
}' > /tmp/finding.json
python3 scripts/issue_tracker.py add \
  .sessi-work/issue_registry.json architecture 0 /tmp/finding.json
```

---

## Step 8: Dead Code Detection

```
[USE mcp__code-review-graph__refactor_tool]
mode: "dead_code"
```

For each unreferenced function/class (no callers, no importers, not an entry point):

```bash
echo '{
  "severity": "low",
  "message": "Dead code: <name> in <file> — no callers, no importers, not an entry point.",
  "file": "<file>",
  "line": null,
  "evidence": "CRG refactor dead_code: zero callers + zero importers"
}' > /tmp/finding.json
python3 scripts/issue_tracker.py add \
  .sessi-work/issue_registry.json architecture 0 /tmp/finding.json
```

> If dead functions > 5% of total node count → escalate to `medium`.

---

## Step 8a: Auto-Seed Registry from Suggested Questions

Deep-integration step. CRG's own prioritization (`get_suggested_questions`)
maps directly to registry issues — no LLM interpretation needed.

```bash
python3 scripts/crg_analysis.py seed_issues \
  .sessi-work/crg_reconnaissance.json \
  .sessi-work/issue_registry.json 0
```

Severity map (deterministic, reviewable in `scripts/crg_analysis.py`):

| CRG category              | Registry dimension | Severity |
|---------------------------|--------------------|----------|
| bridge_needs_tests        | test_coverage      | high     |
| untested_hubs             | test_coverage      | high     |
| untested_hotspots         | test_coverage      | medium   |
| cross_community_coupling  | architecture       | medium   |
| thin_communities          | architecture       | medium   |
| god_modules               | architecture       | high     |
| surprising_connections    | architecture       | medium   |
| dead_code                 | architecture       | low      |

---

## Step 9: Write Reconnaissance Report

Save to `.sessi-work/crg_reconnaissance.json`:

```json
{
  "timestamp": "<ISO8601>",
  "repo": "<repo_path>",
  "graph_stats": {
    "nodes": N,
    "edges": N,
    "files": N,
    "languages": []
  },
  "risk_score": 0.0,
  "suggested_questions": [...],
  "high_risk_hubs": [
    { "name": "<fn>", "file": "<path>", "fan_in": N, "untested": true }
  ],
  "low_cohesion_communities": [
    { "name": "<community>", "cohesion": 0.0, "size": N }
  ],
  "flows": [
    { "name": "<flow>", "has_error_handler": true, "depth": N }
  ],
  "untested_hotspots": [...],
  "unexpected_couplings": [...],
  "dead_code": [...],
  "pre_seeded_issues": N,
  "evaluation_priorities": {
    "deepest_analysis_files": ["<file1>", "<file2>"],
    "dimensions_to_focus": ["test_coverage", "architecture"]
  }
}
```

---

## Step 10: Set Evaluation Priorities

Write `evaluation_priorities` to `crg_reconnaissance.json` based on findings:

```
IF untested_hotspots > 5        → test_coverage: deepest analysis
IF dead_code > 10 functions     → architecture: highest priority
IF low_cohesion_communities > 3 → architecture: highest priority
IF unexpected_couplings > 3     → architecture + readability: focus
IF risk_score > 0.7             → ALL Tier 3 dims: extra scrutiny
```

Claude reads `evaluation_priorities` in Step 3a to adjust analysis depth
per dimension — focusing tool runs and LLM reasoning on identified hotspots
rather than uniform full-codebase scans.

---

## Step 11: Compute Structured Metrics (Deep-Integration Hook)

Turn raw reconnaissance data into deterministic numeric metrics that
downstream scripts (`score.py`, `evaluate_dimension.md`) consume directly.

```bash
# Pass previous round's metrics for per-round structural drift (if it exists)
PREV=""
if [ -f ".sessi-work/crg_metrics.json" ]; then
  PREV=".sessi-work/crg_metrics.json"
fi
python3 scripts/crg_analysis.py metrics \
  .sessi-work/crg_reconnaissance.json \
  .sessi-work/crg_metrics.json \
  ${PREV}
```

Emits `.sessi-work/crg_metrics.json` with:

| Key                         | Meaning                                        |
|-----------------------------|------------------------------------------------|
| `risk_score`                | 0.0–1.0 overall structural risk                |
| `eval_depth`                | `deep` / `standard` / `fast` — token-budget gate |
| `community_cohesion.score`  | 0–100, pulled into architecture dimension      |
| `flow_coverage.score`       | 0–100, pulled into error_handling dimension    |
| `dead_code.escalate_severity` | True/False — low→medium promotion decision  |
| `hub_risk_map.hubs[].severity` | critical/high/medium/low per hub           |

### Explicit thresholds (deterministic, reviewable)

The recon/severity thresholds live in `scripts/crg_analysis.py` and are
ENV-overridable. The three that decide the architecture gate
(`COHESION_HEALTHY`, `COMMUNITY_OVERSIZED`, `COMMUNITY_MIN_SIZE`) are not —
Round 40 站3 removed their env layer, because a shell variable that moves a
gate verdict is a backdoor. Calibrate the cohesion floor with
`crg_cohesion_healthy` in `.methodology/harness_config.json` instead.

| Threshold                   | Default | Effect                                    |
|-----------------------------|---------|-------------------------------------------|
| `CRG_RISK_DEEP`             | 0.7     | risk ≥ → `eval_depth=deep`                |
| `CRG_RISK_FAST`             | 0.3     | risk < → `eval_depth=fast`                |
| `CRG_DEAD_CODE_RATIO`       | 0.05    | dead/total > → escalate low→medium        |
| `CRG_HUB_CRIT_FANIN`        | 15      | fan_in ≥ → critical severity (if untested) |
| `CRG_HUB_HIGH_FANIN`        | 8       | fan_in ≥ → high severity (if untested)    |
| `CRG_FLOW_GOOD_PCT`         | 80      | handled flows ≥ % → healthy               |

Inspect effective values with:

```bash
python3 scripts/crg_analysis.py thresholds
```

### Eval-depth gate (for evaluate_dimension.md)

```bash
python3 scripts/crg_analysis.py depth_gate \
  .sessi-work/crg_reconnaissance.json
# Prints: deep | standard | fast
```

`evaluate_dimension.md` Step 3b reads this to select token budget per
Tier 3 dimension: `deep` → full LLM reasoning + hub source read;
`standard` → tool + LLM one-paragraph; `fast` → tool-only.

---

## Token Budget Reference

| Step | Tool | Est. tokens |
|------|------|-------------|
| 1 | `get_minimal_context` | ~100 |
| 2 | `list_graph_stats` | ~200 |
| 3 | `get_suggested_questions` | ~500 |
| 4 | `get_hub_nodes` + `get_bridge_nodes` | ~800 |
| 5 | `list_communities` + 2–3× `get_community` | ~800 |
| 5a | `list_flows` | ~600 |
| 6 | `get_knowledge_gaps` | ~600 |
| 7 | `get_surprising_connections` | ~400 |
| 8 | `refactor_tool(dead_code)` | ~500 |
| **Total** | | **~4,500** |

**Benchmark:** Reading 10 random files ≈ 10,000 tokens with no structural signal.
Reconnaissance delivers richer, targeted findings at **~60% lower token cost**.

---

## Graceful Degradation

If any individual tool call fails (CRG error, timeout):
- Log the failure in `crg_reconnaissance.json` under `tool_errors[]`
- Continue to next step — partial reconnaissance is better than none
- Never abort the entire quality run due to a CRG tool failure
