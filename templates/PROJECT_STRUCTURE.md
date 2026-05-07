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
+-- 05-verify/               # Phase 5: Verification & Delivery
+-- 06-quality/              # Phase 6: Quality Assurance
+-- 07-risk/                 # Phase 7: Risk Management
+-- 08-config/               # Phase 8: Configuration Management
+-- .methodology/            # Framework state tracking
```

> Note: Phase number aligns with directory prefix (Phase N -> 0N-xxx/)

---

## 01-requirements Template

```markdown
# Requirements Specification

## Project Name
[Name]

## Functional Requirements

### FR-01: [Feature Name]
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
