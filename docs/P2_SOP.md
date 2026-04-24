# Phase 2 — Architecture Design (P2 SOP)
<!-- Role A: Architect | Role B: Reviewer (Hermes) -->
<!-- Input: SRS.md -->
<!-- Output: SAD.md + quality_manifest.json (Gap G1) -->
<!-- Exit: TH-04 SAB + TH-14 architecture review APPROVE -->

## P2 Exit — quality_manifest.json Generation (Gap G1)
```python
from harness.harness_bridge import HarnessBridge
HarnessBridge().generate_quality_manifest(
    fr_ids=["FR-001", "FR-002"],  # from SRS.md
    sad_path="docs/SAD.md"
)
# Output: .methodology/quality_manifest.json
```

> **TODO**: Populate full SOP from methodology-v2.
