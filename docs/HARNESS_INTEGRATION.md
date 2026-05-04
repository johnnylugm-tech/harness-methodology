# HARNESS_INTEGRATION.md
# Gate Embedding: methodology-v2 × software_self_improvement

## Overview

The harness-methodology embeds 4 quality Gates into the existing 8-Phase ASPICE workflow.
No new Phases are added — Gates replace specific existing steps.

## Gate Embedding Map

| Gate | Replaces | Phase | Trigger |
|------|----------|-------|---------|
| Gate 1 | `check_fr_full.py` Layer 3 | P3, P5, P7, P8 | per-FR completion |
| Gate 2 | `cli.py auto-research --phase 3` | P3 | phase exit |
| Gate 3 | `cli.py auto-research --phase 4` | P4 | phase exit |
| Gate 4 | Entire P6 SOP | P6 | phase exit |

## P3 SOP Changes

### Before
```
Layer 3 (per-FR): CQG linter + complexity (~1 min)
POST-FLIGHT: cli.py auto-research --project {REPO} --phase 3
```

### After
```
Layer 3 (per-FR): harness_bridge.run_gate(gate_num=1, fr_id=FR-XXX, phase=3)
  → 3 dims: linting (90), type_safety (85), test_coverage (80)
  → Blocking: any dim < threshold → developer fixes → re-run

POST-FLIGHT (phase exit): harness_bridge.run_gate(gate_num=2, phase=3)
  → 7 dims (Tier 1+2), score_gate=75, max_rounds=3, early_stop=true
  → Blocking: score < 75 → issue-driven improvement plan → iterate
```

## P4 SOP Changes

### After
```
POST-FLIGHT (phase exit): harness_bridge.run_gate(gate_num=3, phase=4)
  → 12 dims (Tier 1+2+3), score_gate=80, max_rounds=3
  → CRG: reconnaissance + tier3_guidance + impact_check + drift_check
```

## P5 SOP Changes

### After
```
Layer 3 (per-FR): harness_bridge.run_gate(gate_num=1, fr_id=FR-XXX, phase=5)
  → Same as P3 Gate 1 (per-FR delivery verification)
```

## P6 SOP — Complete Replacement

### Before
```
Step 6.1: Agent A (qa) → QUALITY_REPORT.md
Step 6.2: Agent B (architect) → APPROVE/REJECT
Exit: TH-02 ≥80% + TH-07 ≥90
```

### After
```
Step 6.1: harness_bridge.run_gate(gate_num=4, phase=6)
         → 12 dims, All Tiers, score_gate=85, max_rounds=3
         → CRG: full (reconnaissance + tier3 guidance + impact + drift)
         → mutation_testing median_runs=3

Step 6.2: AgentSpawner.spawn(role="reviewer", model="hermes", phase=6)
         → Hermes MCP send→wait→read
         → Reviewer persona from agent_personas/REVIEWER.md
         → Prompt: Gate 4 score + dim breakdown + open issues

Exit: Gate 4 score ≥ 85 AND critical_open == 0 AND Hermes APPROVE
```

## P7 / P8 SOP Changes

```
check_fr_full Layer 3: same as Gate 1 (per-FR, 3 dims)
auto-research: unchanged (cli.py auto-research, not gate-specific)
steering: unchanged (cli.py steering, P7/P8 only)
```

## Early-Stop Logic (Gates 2–4)

```
Each round:
  CASE 1 (PASS):     score ≥ score_gate AND critical==0 AND high==0 → quality_complete=True
  CASE 2 (CONTINUE): score ≥ score_gate BUT issues remain → keep iterating (anti-pattern guard)
  CASE 3 (PLATEAU):  3 consecutive rounds no new issues → emit deferred_fixes.md, stop
  CASE 4 (BLOCKED):  max_rounds exhausted, not PASS → GateBlockedError

Score reconciliation: final_score = min(tool_score, llm_score)
```

## CRG Integration Points

| Point | When | Action | Gate |
|-------|------|--------|------|
| 1 Reconnaissance | Phase entry | 9 queries → seed issue_registry | 3, 4 |
| 2 Tier3 Guidance | Before each Tier3 dim eval | get_minimal_context | 3, 4 |
| 3 Pre-fix Safety | Before each fix round | get_impact_radius ≥ 0.7 → defer | 2, 3, 4 |
| 4 Drift Check | After each round | detect_changes drift > 0.4 → revert | 3, 4 |

## Phase Readiness Rationale

| Gate | Why these dims | Why not others |
|------|---------------|----------------|
| Gate 1 (per-FR) | linting/type_safety/test_coverage: raw correctness | security/arch need multi-FR context |
| Gate 2 (P3 exit) | +security/secrets/license/mutation: must clear before testing | arch/readability: refactoring unsafe before P4 |
| Gate 3 (P4 exit) | All 12: code+tests stable, safe for deep review | — |
| Gate 4 (P6 full) | All 12 + CRG: final gate, performance safe to optimize | — |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HERMES_REVIEWER_TARGET` | Yes | — | e.g. `telegram:6308981865` |
| `HERMES_TIMEOUT_MS` | No | `30000` | Reviewer response timeout (ms) — maps to `HarnessBridge.GATE4_HERMES_TIMEOUT_MS`; env var override not yet wired in code |
| `SSI_ROOT` | No | `software_self_improvement` | Path to SSI installation |
