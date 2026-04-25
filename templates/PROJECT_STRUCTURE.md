# Project Structure Template

> Spec Kit style standardized project structure

---

## Standard Directory Structure

```
project/
+-- 00-summary/             # Summary
+-- 01-requirements/        # Phase 1: Requirements (SRS.md)
+-- 02-architecture/        # Phase 2: Architecture (SAD.md)
+-- 03-development/src/     # Phase 3: Code implementation
+-- 03-development/tests/   # Phase 3: Tests
+-- 04-testing/             # Phase 4: Test verification
+-- 05-deployment/          # Phase 5: Deployment
+-- 06-maintenance/         # Phase 6+: Operations
+-- .methodology/           # Framework state tracking
```

> NOTE: Phase number aligns with directory prefix (Phase N -> 0N-xxx/)

---

## 01-requirements Template

### SRS.md

```markdown
# Requirements Specification

## Project Name
[name]

## Functional Requirements

### FR-001: [Feature Name]
- **Priority**: P0 / P1 / P2
- **Acceptance Criteria**: [testable criteria]
```

---

## 02-architecture Template

### SAD.md

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
+-- FR-01/                  # FR-01 module
|   +-- __init__.py
|   +-- main.py
|   +-- test_fr01.py
+-- FR-02/                  # FR-02 module
```

---

*Template version: 1.0.0*
