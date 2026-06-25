# Sample SAD — used by the make_sab_from_sad fixture factory and the
# sab_golden.json golden reference. Mirrors the structure a real SAD.md
# would carry in its SAB block.

## 5. Software Architecture Baseline (SAB)

<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  created_at: "2026-06-25"
  phase: 3
  project: sample
  layers:
    - name: cli
      modules:
        - sample/cli.py
      allowed_dependencies: [core]
    - name: core
      modules:
        - sample/executor.py
        - sample/cache.py
      allowed_dependencies: []
  allowed_dependencies:
    - {from: cli, to: core}
  quality_targets:
    max_complexity: 15
    min_coverage: 80
    max_coupling: 0.3
  fr_module_traceability:
    FR-01: sample.cli
    FR-02: sample.executor
  architecture_constraints:
    - no_circular_dependencies
    - no_shell_true
  high_risk_modules:
    - sample.executor
```
<!-- SAB:END -->