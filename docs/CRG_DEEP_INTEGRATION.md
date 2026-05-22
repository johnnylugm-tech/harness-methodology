# CRG Deep Integration

Code Review Graph (CRG) is **mandatory** in harness-methodology (same tier as ruff/mypy/pytest).
It provides structural analysis that feeds directly into quality gate scoring — not as advisory
context but as the authoritative scorer for structural dimensions.

## Philosophy

| Aspect | Surface Integration | Deep Integration (this project) |
|--------|-------------------|-------------------------------|
| Scoring | LLM reads CRG → LLM decides | CRG output → formula → score |
| Availability | Optional, graceful degradation | Mandatory, BLOCK if missing |
| Architecture score | LLM + CRG min() pull-down | CRG community_cohesion only |
| Error handling score | LLM + CRG min() pull-down | CRG flow_coverage only |
| Fix safety | Prompt hint ("consider checking") | Deterministic blast radius gate |
| Drift detection | Ad-hoc | Per-round structural comparison |

## Integration Points

### 4 Injection Points (HarnessBridge)

| Point | When | Method | Gates |
|-------|------|--------|-------|
| 1 Reconnaissance | `prepare_gate()` entry | `CRGBridge.run_reconnaissance()` | 3, 4 |
| 2 Tier 3 Guidance | `prepare_gate()` per-dim | `CRGBridge.get_minimal_context(dim)` | 3, 4 |
| 3 Pre-fix Safety | Before each auto-fix commit | `HarnessBridge.check_pre_fix_safety()` | 2, 3, 4 |
| 4 Drift Check | After each fix round | `HarnessBridge.check_post_round_drift()` | 3, 4 |

### 6 Deep Integration Hooks (Deterministic)

| # | Location | Signal | Decision |
|---|----------|--------|----------|
| 1 | `evaluate_dimension.md` | `risk_score` | `eval_depth` = deep / standard / fast |
| 2 | `score.py` | `community_cohesion.score` | Architecture = CRG score (authoritative) |
| 3 | `score.py` | `flow_coverage.score` | Error handling = CRG score (authoritative) |
| 4 | `improvement_plan.md` | `dead_code_ratio` | Escalate low→medium if ratio > 5% |
| 5 | `evaluate_dimension.md` | `hub_risk_map[].severity` | Fan-in bucket: crit/high/med/low |
| 6 | `crg_reconnaissance.md` | `suggested_questions[]` | Auto-seed issue registry (category→dim→severity) |

## CRG Analysis Thresholds

All thresholds are environment-variable overridable (defined in `crg_analysis.py`):

| Env Var | Default | Effect |
|---------|---------|--------|
| `CRG_RISK_DEEP` | 0.7 | risk >= this → deep analysis |
| `CRG_RISK_FAST` | 0.3 | risk < this → fast scan |
| `CRG_COHESION_HEALTHY` | 0.4 | Community health minimum |
| `CRG_COMMUNITY_OVERSIZED` | 50 | God-module threshold |
| `CRG_DEAD_CODE_RATIO` | 0.05 | Dead code escalation threshold |
| `CRG_HUB_CRIT_FANIN` | 15 | Critical fan-in threshold |
| `CRG_HUB_HIGH_FANIN` | 8 | High fan-in threshold |
| `CRG_FLOW_GOOD_PCT` | 80 | Flow health minimum |

## Data Flow

```
setup_target.py::init_crg()
  └→ crg_integration.ensure_ready()         # Build graph
  └→ writes .sessi-work/crg_status.json

HarnessBridge.prepare_gate() [Gate 3/4]
  ├→ CRGBridge.run_reconnaissance()         # Full graph rebuild
  ├→ CRGBridge.get_minimal_context(dim)     # Per Tier 3 dimension
  ├→ CRGBridge.refresh_graph()             # Gate 2 lightweight
  ├→ check_pre_fix_safety()                # Pre-compute blast radius
  └→ check_post_round_drift()              # Pre-compute drift status

LLM executes crg_reconnaissance.md (Steps 1-11):
  ├→ 9 CRG MCP tool calls                  # ~3,900 tokens
  ├→ crg_analysis.py seed_issues           # Auto-seed registry
  └→ crg_analysis.py metrics               # → crg_metrics.json

score.py::compute_overall_score():
  └→ _apply_crg_subscores():
      architecture  = community_cohesion.score   # CRG only
      error_handling = flow_coverage.score       # CRG only
```

## MCP Tool Coverage

CRG provides 27 MCP tools. Key tools used by harness-methodology:

### Reconnaissance (9 tools, ~3,900 tokens)
- `get_minimal_context` — Orientation (Step 1)
- `list_graph_stats` — Baseline metrics (Step 2)
- `get_suggested_questions` — Investigation priorities (Step 3)
- `get_hub_nodes` / `get_bridge_nodes` — High-risk components (Step 4)
- `list_communities` / `get_community` — Module cohesion (Step 5)
- `get_knowledge_gaps` — Untested hotspots (Step 6)
- `get_surprising_connections` — Unexpected couplings (Step 7)
- `refactor_tool(dead_code)` — Dead code (Step 8)

### Tier 3 Evaluation (per dimension)
- `get_minimal_context` — Orientation
- `get_hub_nodes` / `list_communities` — Architecture context
- `find_large_functions` — Readability
- `list_flows` / `get_flow` — Performance / error handling
- `get_affected_flows` / `semantic_search_nodes` — Error handling
- `get_hub_nodes` / `get_wiki_page` — Documentation

### Fix Safety Gate
- `get_minimal_context` — Direction
- `get_review_context` — Pre-fix context
- `get_impact_radius` — Blast radius

### Structural Verification
- `build_or_update_graph` — Incremental refresh
- `detect_changes` — Drift measurement

## Gate-CRG Mapping

| Gate | Phase | CRG Features | Structural Dims |
|------|-------|-------------|-----------------|
| Gate 1 | P3/4/5/7/8 per-FR | None | None (Tier 1 only) |
| Gate 2 | P3 exit | Graph refresh + impact check | None (Tier 1+2 only) |
| Gate 3 | P4 exit | Full (Points 1-4) | Architecture, error_handling (CRG-only) |
| Gate 4 | P6 full | Full + B3 mandatory recon | Architecture, error_handling (CRG-only) |

## Verifying CRG Health

```bash
# Is CRG installed? (now in CORE section)
python3 scripts/verify_tools.py

# Graph status
code-review-graph status
cat .sessi-work/crg_status.json

# Reconnaissance output (Gate 3/4)
cat .sessi-work/crg_reconnaissance.json

# Metrics for scoring
python3 crg_analysis.py metrics
cat .sessi-work/crg_metrics.json
```

## Related Files

| File | Role |
|------|------|
| `harness/crg_bridge.py` | Programmatic API (CRGBridge class) |
| `harness/harness_bridge.py` | Gate orchestration (4 injection points) |
| `harness/ssi/scripts/crg_integration.py` | CLI wrapper for bash invocation |
| `harness/ssi/scripts/crg_analysis.py` | Deterministic metrics engine |
| `harness/ssi/scripts/score.py` | CRG-only scoring for structural dims |
| `harness/ssi/prompts/crg_reconnaissance.md` | 11-step reconnaissance protocol |
| `harness/ssi/prompts/evaluate_dimension.md` | CRG depth gating + scoring |
| `harness/ssi/prompts/improvement_plan.md` | CRG blast radius safety gate |
| `harness/ssi/prompts/verify_round.md` | CRG structural drift verification |
| `harness/gate_configs/gate2_p3_exit.yaml` | Gate 2: impact_check |
| `harness/gate_configs/gate3_p4_exit.yaml` | Gate 3: full CRG |
| `harness/gate_configs/gate4_p6_full.yaml` | Gate 4: full CRG + B3 |
| `.mcp.json` | MCP server registration |
| `.claude/settings.json` | Auto-update hooks |
