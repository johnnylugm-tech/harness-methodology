---
name: harness-methodology
version: 2.3.0
description: |
  全流程軟體開發管線編排與品質門禁。Phase 1-8、A/B 協作、12 維度品質憲章。
  Use when: user asks to execute a phase, plan work, run quality gates, or implement FRs.
  Not applicable: one-shot scripts, quick fixes, non-software tasks.
---

# SKILL.md — harness-methodology

> **Version**: v2.3.0 | **Framework**: harness-methodology | **Academic Benchmark**: 91/100

---

## 0. Agent Behavioral Contract (READ FIRST — NOT Reference)

This section is **procedural, not descriptive**. It tells you (the main agent) what to DO when the user asks you to perform phase work. Reference material starts at §1.

### 0.1 Entry Procedure — Mandatory First Actions

When the user says "execute Phase N", "start P3", "implement FR-X", or any phase-work request:

```
1. GENERATE PLAN (always first action for a new phase):
   python harness_cli.py plan-phase --phase N --repo . --output .methodology/phaseN_plan.md
   → Internally calls generate_full_plan.py. Produces the authoritative task list for this phase.

2. PRESENT PLAN to user. Summarize: phase, FR count, preflight checks, checkpoints, estimated effort.
   Do NOT execute any work yet.

3. WAIT for user confirmation ("confirm", "execute", "proceed", "開始", "確認").
   NEVER start work without explicit user confirmation.

4. EXECUTE plan top-to-bottom. You are the ORCHESTRATOR, not Agent A or B:

   [PREFLIGHT]     → python harness_cli.py run-phase --phase N
   [A/B Work]      → Agent A: dispatch via sessions_spawn / sub-agent to develop
                   → Agent B: dispatch as STATELESS sub-agent to review (embed all content in prompt)
                   → Log both entries to sessions_spawn.log (HR-01, HR-10)
                   → NEVER role-play A or B yourself — they MUST be separate sessions (HR-01: A≠B)
   [CHECKPOINT-K]  → run-gate → Claude evaluates inline → finalize-gate → git push

### 0.1a Pre-Execution Mandatory Checklist (Learn-Before-Process)

Before executing any phase work, Agent MUST confirm the following.
This mirrors garden-skills' "learn before you process" constraint —
do NOT start work until every item is checked.

- [ ] 已讀取 `constitution/CONSTITUTION.md` §2 了解當前 phase 的 gate threshold 與維度權重
- [ ] 已讀取 `core/quality_gate/constitution/profile.py` 了解當前 profile 的 dimension keywords
- [ ] 已讀取 `core/auto_fix/classifier.py` 了解 CLASSIFICATION_TABLE 的策略分類（31 entries）
- [ ] 已確認 phase 對應的 gate 編號、最低分數、所需維度數
- [ ] 已確認 `templates/plan_phase_template.md` 中的 CHECKPOINT 標記位置
- [ ] ⏭️ 以上全部確認後，才能開始執行

5. GATE FAIL? → auto-fix (up to `--auto-fix-rounds` attempts) → re-run gate. NEVER advance past a failing gate (HR-08).
   If auto-fix exhausts all rounds → escalated to human (see SAD.md §3.19 for 9 escalation conditions).
   Use `--no-auto-fix` to fall back to detect→block→wait_for_human.

6. PHASE COMPLETE → Verify Phase Completion Checklist (§0.4) → advance to Phase N+1 (back to step 1).
```

**Crash recovery**: `python harness_cli.py generate-next-plan --project .` → open plan file → resume from next unchecked item.

### 0.2 Source of Truth — One Authority Per Moment

| Moment | Authority | Action |
|--------|-----------|--------|
| Phase entry (new phase) | SKILL.md §1–§2 | Check routing, gates, hard rules |
| Inside a phase | `.methodology/phaseN_plan.md` | Follow checklist top-to-bottom |
| After crash / context reset | `generate-next-plan` | Get position report, then resume plan |

> Do NOT re-read SKILL.md mid-phase for task details — the plan file is the authority.

### 0.3 Verify At Each Boundary

| Boundary | What to verify | CLI |
|----------|---------------|-----|
| Before any phase work | Entry gate verify, FSM state, previous phase artifacts, constitution, kill-switch, drift, SAB, traceability, gap analysis, CI readiness | `run-phase --phase N` |
| After each FR (P3/P4/P5/P7/P8) | Gate 1 per-FR (each dim ≥ 75) | `run-gate --gate 1 --fr-id FR-XX` + evaluate + `finalize-gate` |
| Phase exit (P3→Gate2, P4→Gate3, P6→Gate4) | Gate score ≥ threshold + Phase Truth ≥ 90% (HR-11) | `run-gate --gate N` + evaluate + `finalize-gate` |
| P1/P2 exit | Human peer review (no automated gate) | Deliverables: SRS.md / SAD.md + ADR.md |
| After crash | Current position + next checkpoint | `generate-next-plan` |

### 0.4 Phase Completion Checklist (Mandatory — Every Phase)

Before advancing to Phase N+1, confirm ALL:

- [ ] All checkpoints in plan marked done
- [ ] HANDOVER.md written (auto on git push via GitStrategy)
- [ ] Git pushed to remote (confirmed push output, no "push skipped")
- [ ] Next phase plan exists (`plan-phase --phase N+1` completed)
- [ ] state.json updated (phase advanced in `.methodology/state.json`)
- [ ] Git tag pushed (Gate 4 only): `harness-v4-YYYYMMDD-scoreXX`

### 0.5 NEVER

- Start coding without `plan-phase` output
- Execute before user confirms the plan
- Skip preflight (`run-phase`)
- Advance phase after gate failure (HR-08)
- Mix manual mode and `run-pipeline` in the same phase
- Re-read SKILL.md for task details mid-phase (use plan file)
- Skip `sessions_spawn.log` entries (HR-10)
- **Role-play both Agent A and Agent B in the same session (HR-01)**
- **Send Agent B file paths as input — Agent B is stateless, embed content in prompt**

### 0.6 Quick Reference — CLI Entry Points

| Intent | Command |
|--------|---------|
| Plan a new phase | `python harness_cli.py plan-phase --phase N --repo . --output .methodology/phaseN_plan.md` |
| Run preflight for a phase | `python harness_cli.py run-phase --phase N` |
| Run a gate evaluation | `python harness_cli.py run-gate --gate N --phase P [--fr-id FR-XX]` |
| Finalize a gate | `python harness_cli.py finalize-gate --gate N --phase P` |
| Recover from crash | `python harness_cli.py generate-next-plan --project .` |
| Audit a completed phase | `python harness_cli.py audit-phase --phase N --repo .` |
| Full autonomous pipeline | `python harness_cli.py run-pipeline --phase-from N [--auto-fix-rounds 3] [--no-auto-fix]` |

> Full execution loop details: SAD.md §9. Phase E2E flow + entry/exit matrix: SAD.md §11.

---

## 1. Phase Routing

| Phase | Name | Entry Score | Exit Gate | Key Artifact |
|-------|------|-------------|-----------|---------------|
| P1 | Requirements Specification | — | Human¹ | SRS.md |
| P2 | Architecture Design | Auto (git log)† | Human¹ | SAD.md, ADR.md |
| P3 | Implementation | Auto (git log)† | Gate2 (75) | code + tests |
| P4 | Testing | Gate2 | Gate3 (80) | TEST_RESULTS.md |
| P5 | Verification & Delivery | Gate3 | None¹ | BASELINE.md |
| P6 | Quality Assurance | Gate3 | Gate4 (85) | QUALITY_REPORT.md |
| P7 | Risk Management | Gate4 | None² | RISK_REGISTER.md |
| P8 | Configuration Management | Gate4 | None² | CONFIG_RECORDS.md |

> ¹ **Human¹** = human peer review of deliverables. NOT `run-gate --gate 1`. Gate 1 only applies to code phases (P3–P5, P7, P8) where linting/type_safety/test_coverage can be measured. P1/P2 produce documents, not code. P6 has no per-FR Gate 1 — it uses a single Gate 4 (12-dim full audit) at phase exit.
>
> ¹ **None¹** (P5) = Phase Truth check only (HR-11: ≥90%); no separate exit gate evaluation.
>
> ² **None²** (P7/P8) = Cleared by P6 Gate 4; Phase Truth check only (HR-11: ≥90%); no re-evaluation.
>
> † Entry gate: `_verify_entry_gate()` in `harness_cli.py` checks git log for human APPROVE (P2/P3) or `quality_manifest.json` gate PASS (P4+).

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
| HR-11 | Phase Truth < 90% blocks phase advance (P3–P8) | Terminate |
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
{"current_phase": 3, "state": "RUNNING", "last_gate": 1, "last_fr": "FR-03",
 "last_update": "2026-04-26T10:00:00"}
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
| Module API (kill_switch, detection, gap_detector, core/, enforcement/) | `SAD.md` §3–§6 |
| Agent execution loop, modes, phase completion checklist, recovery | `SAD.md` §9 |
| Autonomous pipeline, per-phase A/B roles table (P1–P8), human checkpoints | `SAD.md` §10 |
| Phase E2E flow, entry/exit matrix, preflight hooks, Phase Truth weights | `SAD.md` §11 |
| Gate evaluation CLI flow, result file schema, SSI assets | `SAD.md` §12 |
| CLI commands (plan-phase, run-gate, run-pipeline, etc.) | `harness_cli.py --help` |
| Gate thresholds & quality dimensions | `constitution/CONSTITUTION.md` §2 |
| Full Mermaid phase flowchart | `docs/superpowers/plans/harness_phase_flowchart.md` |
| Integration setup (git hooks, CI, submodule, init-project) | `INTEGRATION.md` |
| Crash recovery position | `python harness_cli.py generate-next-plan` |
| Constitution rule parser & HR compliance | `constitution/` directory |
| A/B agent personas | `agent_personas/` directory |

---

*harness-methodology v6.50.0 — Academic Benchmark 91/100*
