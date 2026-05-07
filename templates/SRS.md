# SRS - {Project Name}

> On-demand Lazy Load template.

## 1. Requirements Overview
{Brief description of project goals}

## 2. Functional Requirements

| ID | Requirement Description | Implementation Function (est.) | Verification Method |
|----|------------------------|-------------------------------|--------------------|
| FR-01 | {requirement} | {function_name} | {verification} |
| FR-02 | ... | ... | ... |

## 3. Non-Functional Requirements (NFR)

| ID | Type | Requirement | Test Method |
|----|------|-------------|-------------|
| NFR-01 | Performance | {requirement} | {test method} |
| NFR-02 | Security | {requirement} | {test method} |

## 4. Constraints
- {constraint 1}
- {constraint 2}

## 5. Glossary
| Term | Definition |
|------|------------|
| {term} | {definition} |

---

## 6. FR Block (machine-readable)

<!-- FR:START -->
```json
{
  "version": "1.0",
  "created_at": "{YYYY-MM-DD}",
  "phase": 1,
  "project": "{project_name}",
  "functional_requirements": [
    {
      "id": "FR-01",
      "description": "{requirement description}",
      "implementation_functions": ["{function_name}"],
      "verification_method": "{verification}"
    }
  ],
  "non_functional_requirements": [
    {
      "id": "NFR-01",
      "type": "performance|security|reliability|maintainability",
      "description": "{requirement description}",
      "test_method": "{test method}"
    }
  ]
}
```
<!-- FR:END -->

Note: Fill in the JSON above - used for downstream requirements traceability.
