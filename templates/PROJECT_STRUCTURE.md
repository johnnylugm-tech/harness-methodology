# Project Structure Template

> Standardized project structure (Spec Kit style)

---

## Standard Directory Layout

```
project/
+-- 00-summary/             # Summary
+-- 01-requirements/        # Phase 1: Requirements (SRS.md)
+-- 02-architecture/        # Phase 2: Architecture (SAD.md)
+-- 03-development/src/     # Phase 3: Implementation
+-- 03-development/tests/   # Phase 3: Tests
+-- 04-testing/             # Phase 4: Test verification
+-- 05-deployment/          # Phase 5: Deployment
+-- 06-maintenance/         # Phase 6+: Operations
+-- .methodology/           # Framework state tracking
```

> Note: Phase number aligns with directory prefix (Phase N -> 0N-xxx/)

---

## 01-requirements Template

```markdown
# Requirements Specification

## Project Name
[Name]

## Functional Requirements

### FR-001: [Feature Name]
- **Priority**: P0 / P1 / P2
- **Acceptance Criteria**: [testable criteria]
```

---

## 02-architecture Template

```markdown
# Architecture Design

## System Overview
[System architecture diagram]

## Component Design

### Component: [Name]
- **Responsibility**: [description]
```

---

## 03-development Template

```
src/
+-- FR-01/
|   +-- __init__.py
|   +-- main.py
|   +-- test_fr01.py
+-- FR-02/
```

---

## 04-testing Template

```
tests/
+-- test_fr01.py
+-- test_fr02.py
+-- integration/
```

---

*Template version: 1.0.0*
