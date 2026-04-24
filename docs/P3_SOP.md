# Phase 3 — Code Implementation (P3 SOP)
<!-- Role A: Developer | Role B: Reviewer (Hermes) -->
<!-- Input: SAD.md + quality_manifest.json -->
<!-- Output: implementation/ + Gate 1/2 results -->

## Gate 1 — Per-FR (replaces check_fr_full Layer 3)
```python
from harness.harness_bridge import HarnessBridge
HarnessBridge().run_gate(gate_num=1, project_root=".", phase=3, fr_id="FR-001")
# 3 dims: linting(90) / type_safety(85) / test_coverage(80)
# Blocking: any dim < threshold -> developer fixes -> re-run
```

## Gate 2 — Phase Exit (replaces auto-research P3)
```python
HarnessBridge().run_gate(gate_num=2, project_root=".", phase=3)
# 7 dims, score_gate=75, max_rounds=3, early_stop=true
# Blocking: score < 75 -> issue-driven plan -> iterate
```

> **TODO**: Populate full SOP from methodology-v2.
