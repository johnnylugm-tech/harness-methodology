# HARNESS_INTEGRATION.md
# Gate Embedding: methodology-v2 × software_self_improvement

> **Scope**: How gates embed into the 8-phase ASPICE workflow (SSI model, per-phase SOP changes, CRG integration).
> For installation/wiring/setup, see [`INTEGRATION.md`](../INTEGRATION.md) at repo root.

## Overview

The harness-methodology embeds 4 quality Gates into the existing 8-Phase ASPICE workflow.
No new Phases are added — Gates replace specific existing steps.

## Gate Embedding Map

| Gate | Replaces | Phase | Trigger |
|------|----------|-------|---------|
| Gate 1 | `harness_cli.py run-gate --gate 1` | P3, P4, P5, P7, P8 | per-FR completion |
| Gate 2 | `harness_cli.py run-gate --gate 2` | P3 | phase exit |
| Gate 3 | `harness_cli.py run-gate --gate 3` | P4 | phase exit |
| Gate 4 | `harness_cli.py run-gate --gate 4` | P6 | phase exit |

## Gate Evaluation Model

SSI is a Claude Code skill — Claude IS the evaluation engine. The subprocess-based
`run_gate()` is deprecated. All gates now use a two-phase API:

```
1. harness_bridge.prepare_gate(gate_num, project_root, phase, fr_id)
   → loads config, triggers CRG recon, returns GateContext

2. Claude evaluates inline using harness/ssi/prompts/evaluate_dimension.md
   → writes $PROJECT/.sessi-work/gate{N}_result.json

3. harness_bridge.finalize_gate(ctx)
   → reads result, checks thresholds, updates manifest, raises GateBlockedError
```

SSI assets are embedded at `harness/ssi/` — no external repo needed.

## P3 SOP Changes

### Before
```
Layer 3 (per-FR): CQG linter + complexity (~1 min)
POST-FLIGHT: (parent-system) auto-research --project {REPO} --phase 3
```

### After
```
Layer 3 (per-FR):
  python harness_cli.py run-gate --gate 1 --phase 3 --fr-id FR-XXX
  (Claude evaluates: 3 dims — linting (90), type_safety (85), test_coverage (80))
  python harness_cli.py finalize-gate --gate 1 --phase 3 --fr-id FR-XXX
  → Blocking: any dim < threshold → developer fixes → re-run

POST-FLIGHT (phase exit):
  python harness_cli.py run-gate --gate 2 --phase 3
  (Claude evaluates: 7 dims, score_gate=75)
  python harness_cli.py finalize-gate --gate 2 --phase 3
  → Blocking: score < 75 OR not quality_complete
```

## P4 SOP Changes

### After
```
POST-FLIGHT (phase exit):
  python harness_cli.py run-gate --gate 3 --phase 4
  (Claude evaluates: 12 dims, score_gate=80, CRG recon triggered automatically)
  python harness_cli.py finalize-gate --gate 3 --phase 4
```

## P5 SOP Changes

### After
```
Layer 3 (per-FR):
  python harness_cli.py run-gate --gate 1 --phase 5 --fr-id FR-XXX
  (same 3 dims as P3 Gate 1)
  python harness_cli.py finalize-gate --gate 1 --phase 5 --fr-id FR-XXX
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
Step 6.1:
  python harness_cli.py run-gate --gate 4 --phase 6
  (Claude evaluates: 12 dims, score_gate=85, CRG full recon)
  python harness_cli.py finalize-gate --gate 4 --phase 6

Step 6.2: Hermes MCP APPROVE (wired in finalize_gate → _require_hermes_approve)
         → Hermes MCP send→wait→read from reviewer_router.py
         → Prompt: Gate 4 score + dim breakdown + open issues

Exit: Gate 4 score ≥ 85 AND critical_open == 0 AND quality_complete AND Hermes APPROVE
```

## P7 / P8 SOP Changes

```
check_fr_full Layer 3: same as Gate 1 (per-FR, 3 dims)
auto-research: unchanged (parent-system only, not gate-specific)
steering: unchanged (parent-system only, P7/P8 only)
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
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key for all LLM-based gate evaluation (Gates 1–4). Required in both local and CI contexts. |
| `PYTHONPATH` | Option B only | — | Must include harness-methodology repo root (e.g. `/opt/harness`). Not needed for Option A (submodule) or Option C (copy). |
| `HERMES_REVIEWER_TARGET` | Yes | — | e.g. `telegram:6308981865` |
| `HERMES_TIMEOUT_MS` | No | `120000` | Hermes reviewer response timeout (ms, default 2 min). Wired in both `reviewer_router.py` (module-level constant) and `HarnessBridge.GATE4_HERMES_TIMEOUT_MS` (class constant). |
| `SSI_ROOT` | No | `harness/ssi` | Path to SSI installation (default: embedded `harness/ssi/`) |
