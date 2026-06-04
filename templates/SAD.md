# SAD - {Project Name}

> On-demand Lazy Load template.

## 1. Architecture Overview
{High-level architecture description}

## 2. Module Design

### 2.1 {Module Name}

| Attribute | Value |
|-----------|-------|
| Responsibility | {responsibility} |
| External Interface | {API} |
| Dependencies | {dependency modules} |

#### Logical Constraints
- {constraint 1}
- {constraint 2}

## 3. Error Handling
| Level | Handling Strategy |
|-------|------------------|
| Level 1 | Immediate return |
| Level 2 | Retry 3 times |
| Level 3 | Graceful degradation |

## 4. Technology Choices
| Technology | Rationale |
|------------|----------|
| {technology} | {reason} |

---

## 5. SAB Block (machine-readable — BINDING CONTRACT)

> **CONTRACT**: Field names, types, `sab:` root key, and `phase` as int must
> match `core/quality_gate/sab_parser.py:render_canonical_sab_template()`.
> Do NOT hand-write the YAML — paste from the canonical template and replace
> EXAMPLE values with your project's real values.
> Validate before committing: `python3 scripts/generate_sab.py --validate --project .`

<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  created_at: "{YYYY-MM-DD}"
  phase: 2  # MUST be int, NOT a string — parser raises on 'phase: "2"'
  project: "{project_name}"

  layers:  # EXAMPLE — replace with your project's layers
    - name: api
      modules: ["app.api.webhooks"]
      allowed_dependencies: ["service"]

  allowed_dependencies:
    - from: api
      to: service

  quality_targets:
    max_complexity: 15
    min_coverage: 80
    max_coupling: 0.3

  nfr_dimension_mapping: {}  # OPTIONAL — auto-derived from nfr_traceability.type

  nfr_traceability:
    NFR-01:
      # type MUST be one of 8 legal values listed below:
      # Enforceable (mapped to gate dim):
      #   performance, security, maintainability, reliability, testability
      # Advisory (no scoring tool, auto-added to advisory_only):
      #   deployability, scalability, usability
      type: performance
      target: "p95 < 200ms"  # use ">=N" or "≥N" to raise the gate floor
      module: app.processing.pipeline

  advisory_only: []  # AUTO-FILLED by parser — omit or leave []

  gate_score_overrides: {}  # AUTO-DERIVED by parser — omit or leave {}

  fr_module_traceability:  # EXAMPLE — one entry per FR
    FR-01: "app.api.webhooks"

  architecture_constraints:
    - "no_circular_dependencies"

  high_risk_modules:
    - "app.api.webhooks"
```
<!-- SAB:END -->

Note: Fill in the YAML above — it is used for Drift Detection and gate scoring.
Generate: `python3 scripts/generate_sab.py --project .`
