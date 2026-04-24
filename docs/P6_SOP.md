# Phase 6 — Quality Assurance (P6 SOP)
<!-- COMPLETE REPLACEMENT of original P6 SOP (Gap G4) -->
<!-- Input: All Phase 1-5 artifacts + quality_manifest.json -->
<!-- Output: Gate 4 report + Hermes APPROVE -->

## Step 6.1 — Gate 4 (12-dim full harness)
```python
from harness.harness_bridge import HarnessBridge
result = HarnessBridge().run_gate(gate_num=4, project_root=".", phase=6)
# 12 dims, All Tiers, score_gate=85, max_rounds=3
# CRG: full integration (Point 1-4)
# mutation_testing: median_runs=3
```

## Step 6.2 — Hermes Reviewer (Gap G2)
```python
from core.agent_spawner import AgentSpawner
review = AgentSpawner().spawn(
    role="reviewer",
    prompt=f"Gate 4 Score: {result.score}/100. Open issues: {result.open_critical} critical.",
    context={"phase": 6},
    model="hermes",
    phase=6,
)
# Reviewer reads agent_personas/REVIEWER.md + Gate 4 results
# Returns: {review_status: APPROVE|REJECT, confidence, violations, summary}
```

## Exit Conditions
```
Gate 4 score >= 85 (score_gate)
AND critical_open == 0
AND Hermes Reviewer review_status == "APPROVE"
```
