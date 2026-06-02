# Phase 1 Full Execution Plan -- harness-methodology

> **Version**: v2.7.0 (project plan)
> **Project**: harness-methodology
> **Date**: 2026-05-31
> **Framework**: harness-methodology v2.7.0
> **Phase**: 1 - Requirements Specification
> **Status**: Full version (including Phase 1 detailed tasks)
> **Mode**: Dynamic (load-context at execution time)


---

## Phase 1 Tasks: Requirements Specification

### Phase 1 Overview
Phase 1 is the project starting point. Define complete SRS.
**Exit gate = Agent B peer review of deliverables** (not `harness run-gate --gate 1`).

> **Crash Recovery**: after each push, `HANDOVER.md` is written to project root.
> If context is lost, read `HANDOVER.md` first — it contains phase, status, and next steps.

> **Checkpoint Index** (push to GitHub = checkpoint + HANDOVER.md saved):
> - CHECKPOINT-1: Agent B Peer Review (Phase 1 Exit) → `push-checkpoint --phase 1`

### Phase 1 Precondition

- [ ] **[PROJECT-BRIEF]** Prepare `PROJECT_BRIEF.md` at project root **before starting Phase 1**:
  - Project domain, stakeholders, business goals (1–2 pages)
  - Key constraints (technical, regulatory, budget, timeline)
  - This file is **Agent B's primary context** for all P1 reviews (embedded as DOC 1 in each B-1 prompt)
  - Source: project owner / product manager supplies this before Phase 1 begins
  - Not a P1 deliverable — it is the seed input that drives requirements authoring

### Pre-Phase Preflight

- [ ] **[PREFLIGHT]** Run phase hooks (FSM, Constitution, Kill-Switch, Drift, CI Readiness):
  ```bash
  python3 harness_cli.py run-phase --phase 1 --project .
  ```
  If FAILED: fix FSM/Constitution/Drift issues. There is no gate bypass flag.
  Re-run `run-phase` after each fix. Max 3 attempts.
  After 3 FAIL: escalate to human — provide last `run-phase --phase 1` full output.
  Human fix → re-run `run-phase --phase 1 --project .` → PASS required before continuing.

- [ ] **[PREFLIGHT-CI]** Verify CI wiring (all 3 items auto-set by `init-project`):
  1. `.methodology/state.json` exists with `current_phase = 1`
  2. `.github/workflows/harness_quality_gate.yml` exists in project root
  3. Git hooks installed (`ls .git/hooks/prepare-commit-msg`)
  4. Phase stored in `.methodology/state.json` — single source of truth (no GitHub variable needed)
  If any item (1-3) is missing — run automated fix:
  ```bash
  python3 harness_cli.py init-project --phase 1 --project .
  ```
  Re-verify items 1-3 after running.
  If still failing after `init-project`: escalate to human — provide `init-project` error output.

### 🔄 [PHASE-CONTEXT] — Load Before Starting

```bash
python3 harness_cli.py load-context --phase 1 --project . --json \
  > .sessi-work/phase1_ctx.json
```
> Outputs `fr_ids`, `fr_details`, `modules` from current project state.

### Task Decomposition (Dependency Analysis)

**Phase 1 has 4 deliverables with sequential dependencies:**

| Order | Deliverable | Depends On | Agent A | Agent B |
|-------|------------|------------|---------|---------|
| 1 | `SRS.md` | (none — starting point) | REQUIREMENTS_ENGINEER | BUSINESS_ANALYST |
| 2 | `SPEC_TRACKING.md` | SRS.md | REQUIREMENTS_ENGINEER | BUSINESS_ANALYST |
| 3 | `TRACEABILITY_MATRIX.md` | SRS.md, SPEC_TRACKING.md | REQUIREMENTS_ENGINEER | BUSINESS_ANALYST |
| 4 | `TEST_INVENTORY.yaml` | TRACEABILITY_MATRIX.md | REQUIREMENTS_ENGINEER | BUSINESS_ANALYST |

**Execution rule**: Each deliverable must pass Agent B review BEFORE starting the next.
If a deliverable is REJECTED, fix only that deliverable — earlier APPROVED deliverables
are not re-opened. This bounds backtracking to a single step.

### Requirements Authoring (Serial A/B per Deliverable)

### Sub-Task 1/4: SRS.md — Software Requirements Specification — functional + non-functional requirements

**Depends on**: none — starting point
**Agent A**: REQUIREMENTS_ENGINEER
**Agent B**: BUSINESS_ANALYST

**A/B Work** (HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews):
- [ ] **[A-1]** Agent A (REQUIREMENTS_ENGINEER): Elicit requirements → write FRs/NFRs in SRS.md (### FR-XX: format) → validate completeness
  - FORBIDDEN: vague/non-testable acceptance criteria
- [ ] **[A-2]** Agent A returns `{status, files, confidence, citations, summary}`
- [ ] **[B-1]** Agent B (BUSINESS_ANALYST) — dispatch as **STATELESS** subagent:
  > ⚠️  **STATELESS SANDBOX**: Agent B has ZERO access to local files or /tmp.
  > NEVER write 'read 01-requirements/SRS.md' in the prompt — it will fail silently.
  > ALL context must be pasted verbatim into the prompt text. This is mandatory.
  >
  > **Lesson (stateless agent)**: Rounds 2-3 failed because prompts used file paths.
  > Round 4 succeeded only after embedding full document content directly.

  **Embed these documents in full** (copy content, not paths):
  - `Project description / stakeholder brief`
  - `draft 01-requirements/SRS.md (full content)`

  **Agent B prompt structure** (use this template verbatim):
  ```
  You are BUSINESS_ANALYST. Your task: review the following deliverable (SRS.md).
  You have NO access to any files — all context is provided below.

  === [DOC 1: Project description / stakeholder brief] ===
  <<paste full content here>>

  === [DOC 2: draft 01-requirements/SRS.md (full content)] ===
  <<paste full content here>>

  Review checklist:
  - All FRs testable? (no vague criteria)
  - NFRs measurable?
  - No contradictions between FRs?
  - Every stakeholder need covered?

  Return JSON only:
  {"review_status":"APPROVE"|"REJECT",
   "reason":"<concise summary>",
   "citations":["file:line"],
   "gaps":[{"severity":"low|medium|high","message":"<issue>","fr_id":"<FR-XX or null>"}]}
  ```

- [ ] **[B-2]** Agent B returns JSON — parse `review_status` **AND** `gaps` severity:
  > gaps schema: `[{"severity": "low|medium|high", "message": "...", "fr_id": "FR-XX or null"}]`
  - `APPROVE` + all gaps are `low` → continue to Sub-Task 2/4
  - `APPROVE` + any gap is `medium` or `high` → fix gaps → **re-dispatch B as round 2**
    (embed same docs as B-1 above, replacing `SRS.md` with its updated content)
    → continue to Sub-Task 2/4 only after round-2 APPROVE
  - `REJECT` → Agent A fixes gaps → re-dispatch B. Max 5 rounds (HR-12).
    > If round 5 REJECT: escalate to human — orchestrator cannot self-resolve.
    > Human fix → re-dispatch Agent B (same prompt + updated content) → `APPROVE` required before continuing.

  > ⚠️ **BLOCKING**: Do NOT start the next Sub-Task until this sub-task's current
  > round is fully APPROVED (including any required round 2).
  > AgentSpawner records dispatches to `.methodology/sessions_spawn.log` (non-blocking debug trail).

  > fr_id uses P1 as phase-level placeholder; replace with FR-XX for FR-specific plans.

### Sub-Task 2/4: SPEC_TRACKING.md — Spec Tracking Matrix — maps every FR to its current status, owner, and acceptance state

**Depends on**: SRS.md (+ Sub-Task 1/4 review: previous review gaps carry forward)
**Agent A**: REQUIREMENTS_ENGINEER
**Agent B**: BUSINESS_ANALYST

**A/B Work** (HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews):
- [ ] **[A-1]** Agent A (REQUIREMENTS_ENGINEER): Build spec tracking matrix from SRS.md FRs → assign status/owner per FR → validate completeness
  - FORBIDDEN: vague/non-testable acceptance criteria
- [ ] **[A-2]** Agent A returns `{status, files, confidence, citations, summary}`
- [ ] **[B-1]** Agent B (BUSINESS_ANALYST) — dispatch as **STATELESS** subagent:
  > ⚠️  **STATELESS SANDBOX**: Agent B has ZERO access to local files or /tmp.
  > NEVER write 'read 01-requirements/SRS.md' in the prompt — it will fail silently.
  > ALL context must be pasted verbatim into the prompt text. This is mandatory.
  >
  > **Lesson (stateless agent)**: Rounds 2-3 failed because prompts used file paths.
  > Round 4 succeeded only after embedding full document content directly.

  **Embed these documents in full** (copy content, not paths):
  - `Previous Sub-Task B-2 review JSON — SRS.md (Sub-Task 1/4, gaps field may contain non-blocking caveats)`
  - `01-requirements/SRS.md (APPROVED — full content)`
  - `draft 01-requirements/SPEC_TRACKING.md (full content)`

  **Agent B prompt structure** (use this template verbatim):
  ```
  You are BUSINESS_ANALYST. Your task: review the following deliverable (SPEC_TRACKING.md).
  You have NO access to any files — all context is provided below.

  === [DOC 1: Previous Sub-Task B-2 review JSON — SRS.md (Sub-Task 1/4, gaps field may contain non-blocking caveats)] ===
  <<paste full content here>>

  === [DOC 2: 01-requirements/SRS.md (APPROVED — full content)] ===
  <<paste full content here>>

  === [DOC 3: draft 01-requirements/SPEC_TRACKING.md (full content)] ===
  <<paste full content here>>

  Review checklist:
  - Upstream deliverable review caveats addressed? (check previous B-2 gaps field)
  - Every FR from SRS.md listed?
  - Status field populated per FR?
  - Owner assigned per FR?
  - No orphan FRs (in SRS but not tracked)?

  Return JSON only:
  {"review_status":"APPROVE"|"REJECT",
   "reason":"<concise summary>",
   "citations":["file:line"],
   "gaps":[{"severity":"low|medium|high","message":"<issue>","fr_id":"<FR-XX or null>"}]}
  ```

- [ ] **[B-2]** Agent B returns JSON — parse `review_status` **AND** `gaps` severity:
  > gaps schema: `[{"severity": "low|medium|high", "message": "...", "fr_id": "FR-XX or null"}]`
  - `APPROVE` + all gaps are `low` → continue to Sub-Task 3/4
  - `APPROVE` + any gap is `medium` or `high` → fix gaps → **re-dispatch B as round 2**
    (embed same docs as B-1 above, replacing `SPEC_TRACKING.md` with its updated content)
    → continue to Sub-Task 3/4 only after round-2 APPROVE
  - `REJECT` → Agent A fixes gaps → re-dispatch B. Max 5 rounds (HR-12).
    > If round 5 REJECT: escalate to human — orchestrator cannot self-resolve.
    > Human fix → re-dispatch Agent B (same prompt + updated content) → `APPROVE` required before continuing.

  > ⚠️ **BLOCKING**: Do NOT start the next Sub-Task until this sub-task's current
  > round is fully APPROVED (including any required round 2).
  > AgentSpawner records dispatches to `.methodology/sessions_spawn.log` (non-blocking debug trail).

  > fr_id uses P1 as phase-level placeholder; replace with FR-XX for FR-specific plans.

### Sub-Task 3/4: TRACEABILITY_MATRIX.md — Requirements Traceability Matrix — bidirectional traceability from FRs through design to tests

**Depends on**: SRS.md, SPEC_TRACKING.md (+ Sub-Task 1/4, 2/4 review: previous review gaps carry forward)
**Agent A**: REQUIREMENTS_ENGINEER
**Agent B**: BUSINESS_ANALYST

**A/B Work** (HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews):
- [ ] **[A-1]** Agent A (REQUIREMENTS_ENGINEER): Build bidirectional traceability matrix → link FRs → design elements → test cases → validate coverage
  - FORBIDDEN: vague/non-testable acceptance criteria
- [ ] **[A-2]** Agent A returns `{status, files, confidence, citations, summary}`
- [ ] **[B-1]** Agent B (BUSINESS_ANALYST) — dispatch as **STATELESS** subagent:
  > ⚠️  **STATELESS SANDBOX**: Agent B has ZERO access to local files or /tmp.
  > NEVER write 'read 01-requirements/SRS.md' in the prompt — it will fail silently.
  > ALL context must be pasted verbatim into the prompt text. This is mandatory.
  >
  > **Lesson (stateless agent)**: Rounds 2-3 failed because prompts used file paths.
  > Round 4 succeeded only after embedding full document content directly.

  **Embed these documents in full** (copy content, not paths):
  - `Previous Sub-Task B-2 review JSON — SRS.md (Sub-Task 1/4, gaps field may contain non-blocking caveats)`
  - `Previous Sub-Task B-2 review JSON — SPEC_TRACKING.md (Sub-Task 2/4, gaps field may contain non-blocking caveats)`
  - `01-requirements/SRS.md (APPROVED — full content)`
  - `01-requirements/SPEC_TRACKING.md (APPROVED — full content)`
  - `draft 01-requirements/TRACEABILITY_MATRIX.md (full content)`

  **Agent B prompt structure** (use this template verbatim):
  ```
  You are BUSINESS_ANALYST. Your task: review the following deliverable (TRACEABILITY_MATRIX.md).
  You have NO access to any files — all context is provided below.

  === [DOC 1: Previous Sub-Task B-2 review JSON — SRS.md (Sub-Task 1/4, gaps field may contain non-blocking caveats)] ===
  <<paste full content here>>

  === [DOC 2: Previous Sub-Task B-2 review JSON — SPEC_TRACKING.md (Sub-Task 2/4, gaps field may contain non-blocking caveats)] ===
  <<paste full content here>>

  === [DOC 3: 01-requirements/SRS.md (APPROVED — full content)] ===
  <<paste full content here>>

  === [DOC 4: 01-requirements/SPEC_TRACKING.md (APPROVED — full content)] ===
  <<paste full content here>>

  === [DOC 5: draft 01-requirements/TRACEABILITY_MATRIX.md (full content)] ===
  <<paste full content here>>

  Review checklist:
  - Upstream deliverable review caveats addressed? (check previous B-2 gaps field)
  - Bidirectional traceability established? (FR→design→test and back)
  - Every FR has ≥1 downstream link?
  - No orphan requirements?
  - Coverage complete (all FRs traceable)?

  Return JSON only:
  {"review_status":"APPROVE"|"REJECT",
   "reason":"<concise summary>",
   "citations":["file:line"],
   "gaps":[{"severity":"low|medium|high","message":"<issue>","fr_id":"<FR-XX or null>"}]}
  ```

- [ ] **[B-2]** Agent B returns JSON — parse `review_status` **AND** `gaps` severity:
  > gaps schema: `[{"severity": "low|medium|high", "message": "...", "fr_id": "FR-XX or null"}]`
  - `APPROVE` + all gaps are `low` → continue to Sub-Task 4/4
  - `APPROVE` + any gap is `medium` or `high` → fix gaps → **re-dispatch B as round 2**
    (embed same docs as B-1 above, replacing `TRACEABILITY_MATRIX.md` with its updated content)
    → continue to Sub-Task 4/4 only after round-2 APPROVE
  - `REJECT` → Agent A fixes gaps → re-dispatch B. Max 5 rounds (HR-12).
    > If round 5 REJECT: escalate to human — orchestrator cannot self-resolve.
    > Human fix → re-dispatch Agent B (same prompt + updated content) → `APPROVE` required before continuing.

  > ⚠️ **BLOCKING**: Do NOT start the next Sub-Task until this sub-task's current
  > round is fully APPROVED (including any required round 2).
  > AgentSpawner records dispatches to `.methodology/sessions_spawn.log` (non-blocking debug trail).

  > fr_id uses P1 as phase-level placeholder; replace with FR-XX for FR-specific plans.

### Sub-Task 4/4: TEST_INVENTORY.yaml — Test Inventory — P1 naming authority, feeds TEST_SPEC.md (D4 unified source)

**Depends on**: TRACEABILITY_MATRIX.md (+ Sub-Task 3/4 review: previous review gaps carry forward)
**Agent A**: REQUIREMENTS_ENGINEER
**Agent B**: BUSINESS_ANALYST

**A/B Work** (HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews):
- [ ] **[A-1]** Agent A (REQUIREMENTS_ENGINEER): Generate TEST_INVENTORY.yaml from SRS.md FR acceptance criteria → assign test function names per FR → validate naming convention
  - FORBIDDEN: vague/non-testable acceptance criteria
- [ ] **[A-2]** Agent A returns `{status, files, confidence, citations, summary}`
- [ ] **[B-1]** Agent B (BUSINESS_ANALYST) — dispatch as **STATELESS** subagent:
  > ⚠️  **STATELESS SANDBOX**: Agent B has ZERO access to local files or /tmp.
  > NEVER write 'read 01-requirements/SRS.md' in the prompt — it will fail silently.
  > ALL context must be pasted verbatim into the prompt text. This is mandatory.
  >
  > **Lesson (stateless agent)**: Rounds 2-3 failed because prompts used file paths.
  > Round 4 succeeded only after embedding full document content directly.

  **Embed these documents in full** (copy content, not paths):
  - `Previous Sub-Task B-2 review JSON — TRACEABILITY_MATRIX.md (Sub-Task 3/4, gaps field may contain non-blocking caveats)`
  - `01-requirements/SRS.md (APPROVED — full content)`
  - `01-requirements/TRACEABILITY_MATRIX.md (APPROVED — full content)`
  - `draft TEST_INVENTORY.yaml (full content)`

  **Agent B prompt structure** (use this template verbatim):
  ```
  You are BUSINESS_ANALYST. Your task: review the following deliverable (TEST_INVENTORY.yaml).
  You have NO access to any files — all context is provided below.

  === [DOC 1: Previous Sub-Task B-2 review JSON — TRACEABILITY_MATRIX.md (Sub-Task 3/4, gaps field may contain non-blocking caveats)] ===
  <<paste full content here>>

  === [DOC 2: 01-requirements/SRS.md (APPROVED — full content)] ===
  <<paste full content here>>

  === [DOC 3: 01-requirements/TRACEABILITY_MATRIX.md (APPROVED — full content)] ===
  <<paste full content here>>

  === [DOC 4: draft TEST_INVENTORY.yaml (full content)] ===
  <<paste full content here>>

  Review checklist:
  - Upstream deliverable review caveats addressed? (check previous B-2 gaps field)
  - Every FR has ≥1 test function?
  - Test function names follow naming convention?
  - All FRs from TRACEABILITY_MATRIX covered?
  - All upstream deliverables consistent with each other? No contradictory decisions?

  Return JSON only:
  {"review_status":"APPROVE"|"REJECT",
   "reason":"<concise summary>",
   "citations":["file:line"],
   "gaps":[{"severity":"low|medium|high","message":"<issue>","fr_id":"<FR-XX or null>"}]}
  ```

- [ ] **[B-2]** Agent B returns JSON — parse `review_status` **AND** `gaps` severity:
  > gaps schema: `[{"severity": "low|medium|high", "message": "...", "fr_id": "FR-XX or null"}]`
  - `APPROVE` + all gaps are `low` → all deliverables complete; proceed to Agent B Peer Review
  - `APPROVE` + any gap is `medium` or `high` → fix gaps → **re-dispatch B as round 2**
    (embed same docs as B-1 above, replacing `TEST_INVENTORY.yaml` with its updated content)
    → all deliverables complete; proceed to Agent B Peer Review only after round-2 APPROVE
  - `REJECT` → Agent A fixes gaps → re-dispatch B. Max 5 rounds (HR-12).
    > If round 5 REJECT: escalate to human — orchestrator cannot self-resolve.
    > Human fix → re-dispatch Agent B (same prompt + updated content) → `APPROVE` required before continuing.

  > ⚠️ **BLOCKING**: Do NOT start the next Sub-Task until this sub-task's current
  > round is fully APPROVED (including any required round 2).
  > AgentSpawner records dispatches to `.methodology/sessions_spawn.log` (non-blocking debug trail).

  > fr_id uses P1 as phase-level placeholder; replace with FR-XX for FR-specific plans.

### Phase 1 Deliverables
- [ ] `SRS.md` - Software Requirements Specification (FRs + NFRs)
- [ ] `SPEC_TRACKING.md` - Spec tracking matrix
- [ ] `TRACEABILITY_MATRIX.md` - Requirements traceability matrix
- [ ] `TEST_INVENTORY.yaml` - Test inventory (P1 naming authority — feeds TEST_SPEC.md)
- [x] `.methodology/sessions_spawn.log` — auto-populated by AgentSpawner (non-blocking debug trail)


### 🔒 CHECKPOINT-PEER-REVIEW: Agent B Peer Review — Phase 1 Exit
> Phase 1/2 exit gate = Agent B document review (NOT `harness run-gate --gate 1`).
> APPROVE criteria: all FRs addressed, no critical gaps, terminology consistent.

- [ ] **[B-1]** Agent B (BUSINESS_ANALYST) — dispatch as **STATELESS** subagent (holistic review of all deliverables):
  > ⚠️  **STATELESS SANDBOX**: Agent B has ZERO access to local files or /tmp.
  > NEVER pass file paths in the prompt — ALL document content must be pasted verbatim.
  >
  > **Lesson (stateless agent)**: Rounds 2-3 failed because prompts used file paths.
  > Round 4 succeeded only after embedding full document content directly.

  **Embed ALL deliverables in full** (copy content, not paths):
  - `01-requirements/SRS.md (full content)`
  - `01-requirements/SPEC_TRACKING.md (full content)`
  - `01-requirements/TRACEABILITY_MATRIX.md (full content)`
  - `TEST_INVENTORY.yaml (full content)`

  **Agent B prompt structure** (use this template verbatim):
  ```
  You are BUSINESS_ANALYST. Your task: holistic review of ALL Phase 1 deliverables.
  You have NO access to any files — all context is provided below.

  === [DOC 1: 01-requirements/SRS.md] ===
  <<paste full content here>>

  === [DOC 2: 01-requirements/SPEC_TRACKING.md] ===
  <<paste full content here>>

  === [DOC 3: 01-requirements/TRACEABILITY_MATRIX.md] ===
  <<paste full content here>>

  === [DOC 4: TEST_INVENTORY.yaml] ===
  <<paste full content here>>

  Review checklist:
  - All FRs covered across all deliverables?
  - No contradictions between deliverables?
  - Each item testable/traceable?
  - All gaps from sub-task reviews addressed?
  - Terminology consistent across all documents?

  Return JSON only:
  {"review_status":"APPROVE"|"REJECT",
   "reason":"<concise summary>",
   "citations":["file:line"],
   "gaps":[{"severity":"low|medium|high","message":"<issue>","fr_id":"<FR-XX or null>"}]}
  ```

- [ ] **[B-2]** Agent B returns JSON — parse `review_status` **AND** `gaps` severity:
  - `APPROVE` + all gaps are `low` → proceed to push (CHECKPOINT saved)
  - `APPROVE` + any gap is `medium` or `high` → fix gaps → **re-dispatch B as round 2**
    (embed same docs as B-1 above with updated content) → push only after round-2 APPROVE
  - `REJECT` → fix all gaps → re-dispatch B. Max 5 rounds (HR-12).
    > If round 5 REJECT: escalate to human — orchestrator cannot self-resolve.
    > Human fix → re-dispatch Agent B (same prompt + updated content) → `APPROVE` required before continuing.

- [ ] **[B-PUSH]** ✅ PUSH ① — Push to GitHub + HANDOVER.md — retry until success (CHECKPOINT-PEER-REVIEW saved):
  > Run `push-checkpoint` → if blocked, read the error → fix → re-run until green.
  > Do NOT use `--no-verify` to bypass.
  ```bash
  python3 harness_cli.py push-checkpoint --phase 1 --project .
  ```
  > This writes `HANDOVER.md` (crash-recovery checkpoint) to project root,
  > then commits + pushes all changes to origin.
  > After a crash, read HANDOVER.md first — it tells you where you were.

### Phase 1 → Phase 2: Architecture Design

- [ ] Confirm ALL checkpoints in this plan are ✓  (no skips — HR-03)
- [ ] Advance FSM to Phase 2 (writes new HANDOVER.md + local commit):
  ```bash
  python3 harness_cli.py advance-phase --completed 1 --project .
  ```
- [ ] Confirm `HANDOVER.md` reflects Phase 2 entry (`P2-entry` checkpoint, correct plan path)
- [ ] Open `phase2_plan.md` and follow from the top.
- [ ] If session crashes during Phase 2: read `HANDOVER.md` or run `generate-next-plan`
