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

## 5. SAB Block (machine-readable)

<!-- SAB:START -->
```json
{
  "version": "1.0",
  "created_at": "{YYYY-MM-DD}",
  "phase": 2,
  "project": "{project_name}",
  "layers": [
    {
      "name": "{layer_name}",
      "modules": ["FR-XX", "..."],
      "allowed_dependencies": ["{other_layer}"]
    }
  ],
  "dependencies": {
    "{layer_A}": ["{layer_B}"],
    "{layer_B}": ["{layer_C}"]
  },
  "quality_targets": {
    "max_complexity": 15,
    "min_coverage": 80,
    "max_coupling": 0.3
  },
  "nfr_traceability": {
    "NFR-01": {
      "type": "{performance|security|reliability|maintainability}",
      "target": "{measurable target, e.g. p95 < 200ms}",
      "module": "{responsible module path, e.g. app.processing}"
    }
  },
  "fr_module_traceability": {
    "FR-01": "{responsible module name, e.g. app.models}"
  },
  "architecture_constraints": [],
  "high_risk_modules": []
}
```
<!-- SAB:END -->

Note: Fill in the JSON above — it is used for Drift Detection.
