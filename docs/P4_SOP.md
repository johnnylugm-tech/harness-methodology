# Phase 4 — Testing (P4 SOP)
<!-- Role A: QA Engineer | Role B: Reviewer (Hermes) -->
<!-- Input: implementation/ + TEST_PLAN.md -->
<!-- Output: TEST_RESULTS.md + Gate 3 result -->

## Gate 3 — Phase Exit (replaces auto-research P4)
```python
from harness.harness_bridge import HarnessBridge
HarnessBridge().run_gate(gate_num=3, project_root=".", phase=4)
# 12 dims (all tiers), score_gate=80, max_rounds=3
# CRG: reconnaissance + tier3_guidance + impact_check + drift_check
```

> **TODO**: Populate full SOP from methodology-v2.
