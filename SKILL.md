# SKILL.md — harness-methodology

> **Version**: v6.50.0 | **Framework**: harness-methodology | **Academic Benchmark**: 91/100

---

## 1. Phase Routing

| Phase | Name | Entry Score | Exit Gate | Key Artifact |
|-------|------|-------------|-----------|---------------|
| P1 | Requirements Specification | — | Human¹ | SRS.md |
| P2 | Architecture Design | Human¹ | Human¹ | SAD.md, ADR.md |
| P3 | Implementation | Human¹ | Gate2 (75) | code + tests |
| P4 | Testing | Gate2 | Gate3 (80) | TEST_RESULTS.md |
| P5 | Verification & Delivery | Gate3 | Gate3 (80) | BASELINE.md |
| P6 | Quality Assurance | Gate3 | Gate4 (85) | QUALITY_REPORT.md |
| P7 | Risk Management | Gate4 | Gate4 (85) | RISK_REGISTER.md |
| P8 | Configuration Management | Gate4 | Gate4 (85) | CONFIG_RECORDS.md |

> ¹ **Human¹** = human peer review of deliverables. NOT `run-gate --gate 1`. Gate 1 only applies to code phases (P3–P5, P7, P8) where linting/type_safety/test_coverage can be measured. P1/P2 produce documents, not code. P6 has no per-FR Gate 1 — it uses a single Gate 4 (12-dim full audit) at phase exit.

### Gate Definitions

| Gate | Phases | score_gate | Blocking |
|------|--------|------------|----------|
| Gate1 | P3, P4, P5, P7, P8 per-FR | 75 (each dim) | yes |
| Gate2 | P3 exit | 75 | yes |
| Gate3 | P4 exit | 80 | yes |
| Gate4 | P6 full | 85 | yes |

---

## 2. Hard Rules (HR)

| ID | Rule | Score Impact |
|----|------|--------------|
| HR-01 | A/B must be different Agents; self-review forbidden | -25 / Terminate |
| HR-02 | Quality Gate requires actual stdout output | -20 / Terminate |
| HR-03 | Phase order must be sequential; no skipping | -30 / Terminate |
| HR-04 | HybridWorkflow mode=ON mandatory | Terminate |
| HR-05 | harness-methodology wins all conflicts | Log |
| HR-06 | External frameworks outside spec forbidden | -20 / Terminate |
| HR-07 | DEVELOPMENT_LOG must record session_id | -15 |
| HR-08 | Phase end requires Quality Gate pass | -10 / Terminate |
| HR-09 | Claims Verifier citations must pass | -20 / Terminate |
| HR-10 | sessions_spawn.log must have A/B entries | -15 / Terminate |
| HR-11 | Phase Truth < 70% blocks phase advance (P3–P8) | Terminate |
| HR-12 | A/B review > 5 rounds triggers PAUSE | — |
| HR-13 | Phase execution > 3× estimate triggers PAUSE | — |
| HR-14 | Integrity < 40 triggers FREEZE | — |
| HR-15 | citations must include line numbers + artifact_verification | -15 |

---

## 3. A/B Collaboration Protocol

```
Agent A (DEVELOPER / architect / tester / devops / qa / risk)
  |-- [TDD-1] Write failing test for FR requirement (RED)
  |-- [TDD-2] Implement FR until test passes (GREEN)
  |-- [TDD-3] Refactor without breaking tests (IMPROVE)
  |-- returns JSON: {status, files, confidence, citations, summary}
  |
Agent B (REVIEWER / architect)
  |-- reviews Agent A output against SRS + SAD
  |-- returns JSON: {status, review_status, reason, confidence, citations, summary}
  |
[Constitution Check]  -- BVS + HR-09 validation
[HR-12]               -- iteration guard (max 5 rounds)
```

### Per-Phase A/B Roles

| Phase | Agent A Role | Agent B Role | Agent A Task | Agent B Task |
|-------|------------|------------|--------------|--------------|
| P1 | REQUIREMENTS_ENGINEER | BUSINESS_ANALYST | Draft SRS.md with `### FR-XX:` sections | Review SRS.md against business goals; verify FR-ID traceability |
| P2 | ARCHITECT | TECH_LEAD | Design SAD.md; write ADR.md for key decisions | Review SAD.md for feasibility, consistency, SRS alignment |
| P3 | DEVELOPER | REVIEWER | TDD: RED → GREEN → REFACTOR per FR | Review code against SRS/SAD; verify tests pass; check citations |
| P4 | QA_ENGINEER | ARCHITECT | Execute TEST_PLAN.md per FR; verify coverage ≥ 80% | Review test results; confirm coverage gaps documented; validate traceability |
| P5 | DEVELOPER | REVIEWER | Verify acceptance criteria per FR against SRS.md | Review acceptance verification; cross-check BASELINE.md against SRS |
| P6 | QA_ENGINEER | ARCHITECT | Generate QUALITY_REPORT.md (12-dim audit); prepare RELEASE_NOTES.md | Review quality report; confirm all FRs merged and Gate 4 score ≥ 85 |
| P7 | DEVOPS | ARCHITECT | Assess risk per FR; draft mitigation plans; populate RISK_REGISTER.md | Review risk assessments; verify mitigation plans actionable |
| P8 | DEVOPS | ARCHITECT | Document config per FR; populate CONFIG_RECORDS.md | Review config records; verify env parity; confirm no secret leaks |

> All phases: Agent A ≠ Agent B (HR-01). Both write `sessions_spawn.log` (HR-10).
> P3/P4/P5/P7/P8: 2 entries per FR. P1/P2/P6: 2 entries per phase.

### FORBIDDEN in any agent output

- `app/infrastructure/` imports (deprecated)
- `@covers: L1 Error` annotations
- `@type: edge` test type
- Docstrings without `[FR-XX]` reference
- Docstrings without `Citations:` section with line numbers

---

## 4. sessions_spawn.log Format (HR-10)

Two entries per FR (developer + reviewer):

```json
{"timestamp": "2026-04-26T10:00:00", "fr_id": "FR-01", "role": "developer",
 "session_id": "dev-abc123", "status": "success", "confidence": 8}
{"timestamp": "2026-04-26T10:05:00", "fr_id": "FR-01", "role": "reviewer",
 "session_id": "rev-def456", "status": "success", "review_status": "APPROVE"}
```

---

## 5. State Machine (FSM)

```
INIT -> RUNNING -> PAUSED -> RUNNING
                -> FREEZE  (Integrity < 40)
                -> DONE    (all phases complete)
RUNNING -> OPEN   (KillSwitch triggered)
OPEN    -> HALF_OPEN -> CLOSED  (recovery)
```

State stored in `.methodology/state.json`:
```json
{"current_phase": 3, "state": "RUNNING", "last_update": "2026-04-26T10:00:00"}
```

---

## 6. Decision Rules

- **SKILL.md governs**: phase order, gate thresholds, hard rules (HR-01–HR-15), A/B protocol.
- **Plan governs**: task sequence within a phase; specific file paths; CLI commands.
- **Conflict**: SKILL.md wins on rules; plan wins on task order / phase-specific steps.
- **Never skip checkpoints**: If a gate fails, fix and re-run — never advance without PASS.
- **A/B is mandatory**: HR-01 (A≠B), HR-04 (HybridWorkflow ON), HR-10 (sessions_spawn.log) apply to every FR in every phase.

---

## 7. On-Demand Reference

| Need | Where |
|------|-------|
| Module API (kill_switch, detection, gap_detector, core/, enforcement/) | `SAD.md` §3–§4 |
| CLI commands (plan-phase, run-gate, run-pipeline, etc.) | `harness_cli.py --help` |
| Gate thresholds & quality dimensions | `constitution/CONSTITUTION.md` §2 |
| SSI evaluation prompts & scripts | `harness/ssi/prompts/`, `harness/ssi/scripts/` |
| Crash recovery position | `python harness_cli.py generate-next-plan` |
| E2E phase flowchart (all 8 phases, gates, A/B roles) | `docs/superpowers/plans/harness_phase_flowchart.md` |
| Integration setup (git hooks, CI, submodule) | `INTEGRATION.md` |
| Phase Truth weights & verifier | `core/quality_gate/phase_truth_verifier.py` |
| Constitution rule parser & HR compliance | `constitution/` directory |
| A/B agent personas | `agent_personas/` directory |

---

*harness-methodology v6.50.0 — Academic Benchmark 91/100*
