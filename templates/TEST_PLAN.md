# TEST_PLAN.md - {Project Name}

> On-demand Lazy Load template.

## 1. Test Objectives
{Test objective description}

## 2. Test Scope
- {scope 1}
- {scope 2}

## 3. Test Strategy
| Type | Strategy |
|------|----------|
| Unit Test | {strategy} |
| Integration Test | {strategy} |
| System Test | {strategy} |

## 4. Test Environment
- Environment: {environment}
- Tools: {tools}

## 5. Test Case List

| ID | Type | Description | Expected Result | Status |
|----|------|-------------|-----------------|--------|
| TC-01 | Positive | {description} | {result} | DRAFT |

---

## 6. Test Block (machine-readable)

<!-- TEST:START -->
```json
{
  "version": "1.0",
  "created_at": "{YYYY-MM-DD}",
  "phase": 4,
  "project": "{project_name}",
  "test_cases": [
    {
      "id": "TC-01",
      "type": "unit|integration|e2e",
      "description": "{description}",
      "expected_result": "{expected result}",
      "fr_coverage": ["FR-01", "FR-02"]
    }
  ],
  "test_strategy": {
    "unit_coverage_target": 80,
    "branch_coverage_target": 70,
    "integration_coverage_target": 60
  }
}
```
<!-- TEST:END -->
