# Phase 8 — Configuration Management (P8 SOP)
<!-- steering loop + auto-research (unchanged from methodology-v2) -->
<!-- Gate 1 applies to check_fr_full Layer 3 per-FR checks -->

## Gate 1 (P8 per-FR, same as P3)
```python
from harness.harness_bridge import HarnessBridge
HarnessBridge().run_gate(gate_num=1, project_root=".", phase=8, fr_id="FR-001")
```

## Steering Loop (unchanged)
```bash
python cli.py steering run --phase 8
```

> **TODO**: Populate full SOP from methodology-v2.
