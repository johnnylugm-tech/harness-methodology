# Phase 2 Full Execution Plan -- harness-methodology

> **Version**: v2.7.0 (project plan)
> **Project**: harness-methodology
> **Date**: 2026-05-31
> **Framework**: harness-methodology v2.7.0
> **Phase**: 2 - Architecture Design
> **Status**: Full version (including Phase 2 detailed tasks)
> **Mode**: Dynamic (load-context at execution time)


---

## Phase 2 Tasks: Architecture Design

### Phase 2 Overview
Phase 2 designs the system architecture based on SRS, producing SAD and ADR.
**Exit gate = Agent B peer review of deliverables** (not `harness run-gate --gate 1`).

> **Crash Recovery**: after each push, `HANDOVER.md` is written to project root.
> If context is lost, read `HANDOVER.md` first — it contains phase, status, and next steps.

> **Checkpoint Index** (push to GitHub = checkpoint + HANDOVER.md saved):
> - CHECKPOINT-1: Agent B Peer Review (Phase 2 Exit) → `push-checkpoint --phase 2`

### Entry Gate Verification

- [ ] **[ENTRY-CHECK]** P1 review-complete:
  Proof: git log contains commit 'phase1(review-complete): Phase 1 deliverables APPROVED'.
  If NOT confirmed: return to Phase 1 and complete exit gate first.

- [ ] **[P1-ARTIFACTS]** Verify all 4 Phase 1 deliverables exist (CONSTITUTION.md §2.3 P2 entry requirement):
  ```bash
  ls 01-requirements/SRS.md \
     01-requirements/SPEC_TRACKING.md \
     01-requirements/TRACEABILITY_MATRIX.md \
     TEST_INVENTORY.yaml
  ```
  All 4 files must exist. If any is missing → return to Phase 1 to complete them before entering Phase 2.

### Pre-Phase Preflight

- [ ] **[PREFLIGHT]** Run phase hooks (FSM, Constitution, Kill-Switch, Drift, CI Readiness):
  ```bash
  python3 harness_cli.py run-phase --phase 2 --project .
  ```
  If FAILED: fix FSM/Constitution/Drift issues. There is no gate bypass flag.
  Re-run `run-phase` after each fix. Max 3 attempts.
  After 3 FAIL: escalate to human — provide last `run-phase --phase 2` full output.
  Human fix → re-run `run-phase --phase 2 --project .` → PASS required before continuing.

- [ ] **[PREFLIGHT-CI]** Confirm CI wiring unchanged (should be set since P1):
  1. `.github/workflows/harness_quality_gate.yml` exists
  2. Git hooks installed (`ls .git/hooks/prepare-commit-msg`)
  3. harness importable (submodule, PYTHONPATH, or vendored `quality_gate/`)
  4. Phase 2 confirmed in `.methodology/state.json` (`advance-phase` already run)
  > If stale: run `python3 harness_cli.py init-project --phase 2 --project . --overwrite`

### 🔄 [PHASE-CONTEXT] — Load Before Starting

```bash
python3 harness_cli.py load-context --phase 2 --project . --json \
  > .sessi-work/phase2_ctx.json
```
> Outputs `fr_ids`, `fr_details`, `modules` from current project state.

### Task Decomposition (Dependency Analysis)

**Phase 2 has 3 deliverables with sequential dependencies:**

| Order | Deliverable | Depends On | Agent A | Agent B |
|-------|------------|------------|---------|---------|
| 1 | `SAD.md` | (none — starting point) | ARCHITECT | TECH_LEAD |
| 2 | `ADR.md` | SAD.md | ARCHITECT | TECH_LEAD |
| 3 | `TEST_SPEC.md` | ADR.md | ARCHITECT | TECH_LEAD |

**Execution rule**: Each deliverable must pass Agent B review BEFORE starting the next.
If a deliverable is REJECTED, fix only that deliverable — earlier APPROVED deliverables
are not re-opened. This bounds backtracking to a single step.

### Architecture Design (Serial A/B per Deliverable)

### Sub-Task 1/3: SAD.md — Software Architecture Document — components, interfaces, FR→module mapping, data flows

**Depends on**: none — starting point
**Agent A**: ARCHITECT
**Agent B**: TECH_LEAD

**A/B Work** (HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews):
- [ ] **[A-1]** Agent A (ARCHITECT): Design system architecture → write SAD.md → validate every FR has a module mapping
  - FORBIDDEN: vague/non-testable acceptance criteria
- [ ] **[A-2]** Agent A returns `{status, files, confidence, citations, summary}`
- [ ] **[B-1]** Agent B (TECH_LEAD) — dispatch as **STATELESS** subagent:
  > ⚠️  **STATELESS SANDBOX**: Agent B has ZERO access to local files or /tmp.
  > NEVER write 'read 01-requirements/SRS.md' in the prompt — it will fail silently.
  > ALL context must be pasted verbatim into the prompt text. This is mandatory.
  >
  > **Lesson (stateless agent)**: Rounds 2-3 failed because prompts used file paths.
  > Round 4 succeeded only after embedding full document content directly.

  **Embed these documents in full** (copy content, not paths):
  - `01-requirements/SRS.md (full)`
  - `draft 02-architecture/SAD.md (full)`

  **Agent B prompt structure** (use this template verbatim):
  ```
  You are TECH_LEAD. Your task: review the following deliverable (SAD.md).
  You have NO access to any files — all context is provided below.

  === [DOC 1: 01-requirements/SRS.md (full)] ===
  <<paste full content here>>

  === [DOC 2: draft 02-architecture/SAD.md (full)] ===
  <<paste full content here>>

  Review checklist:
  - Every FR maps to ≥1 module?
  - NFRs addressed (latency/security/cost)?
  - No circular dependencies?
  - Data flow diagrams consistent?

  Return JSON only:
  {"review_status":"APPROVE"|"REJECT",
   "reason":"<concise summary>",
   "citations":["file:line"],
   "gaps":[{"severity":"low|medium|high","message":"<issue>","fr_id":"<FR-XX or null>"}]}
  ```

- [ ] **[B-2]** Agent B returns JSON — parse `review_status` **AND** `gaps` severity:
  > gaps schema: `[{"severity": "low|medium|high", "message": "...", "fr_id": "FR-XX or null"}]`
  - `APPROVE` + all gaps are `low` → continue to Sub-Task 2/3
  - `APPROVE` + any gap is `medium` or `high` → fix gaps → **re-dispatch B as round 2**
    (embed same docs as B-1 above, replacing `SAD.md` with its updated content)
    → continue to Sub-Task 2/3 only after round-2 APPROVE
  - `REJECT` → Agent A fixes gaps → re-dispatch B. Max 5 rounds (HR-12).
    > If round 5 REJECT: escalate to human — orchestrator cannot self-resolve.
    > Human fix → re-dispatch Agent B (same prompt + updated content) → `APPROVE` required before continuing.

  > ⚠️ **BLOCKING**: Do NOT start the next Sub-Task until this sub-task's current
  > round is fully APPROVED (including any required round 2).
  > AgentSpawner records dispatches to `.methodology/sessions_spawn.log` (non-blocking debug trail).

  > fr_id uses P2 as phase-level placeholder; replace with FR-XX for FR-specific plans.

### Sub-Task 2/3: ADR.md — Architecture Decision Records — document key design decisions (tech stack, patterns, interfaces, trade-offs) with context and consequences

**Depends on**: SAD.md (+ Sub-Task 1/3 review: previous review gaps carry forward)
**Agent A**: ARCHITECT
**Agent B**: TECH_LEAD

**A/B Work** (HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews):
- [ ] **[A-1]** Agent A (ARCHITECT): Extract key architecture decisions from SAD.md → write individual ADR entries → validate rationale and consequences are recorded
  - FORBIDDEN: vague/non-testable acceptance criteria
- [ ] **[A-2]** Agent A returns `{status, files, confidence, citations, summary}`
- [ ] **[B-1]** Agent B (TECH_LEAD) — dispatch as **STATELESS** subagent:
  > ⚠️  **STATELESS SANDBOX**: Agent B has ZERO access to local files or /tmp.
  > NEVER write 'read 01-requirements/SRS.md' in the prompt — it will fail silently.
  > ALL context must be pasted verbatim into the prompt text. This is mandatory.
  >
  > **Lesson (stateless agent)**: Rounds 2-3 failed because prompts used file paths.
  > Round 4 succeeded only after embedding full document content directly.

  **Embed these documents in full** (copy content, not paths):
  - `Previous Sub-Task B-2 review JSON — SAD.md (Sub-Task 1/3, gaps field may contain non-blocking caveats)`
  - `02-architecture/SAD.md (APPROVED — full content)`
  - `draft 02-architecture/ADR.md (full content)`
  - `templates/ADR.md (template format)`

  **Agent B prompt structure** (use this template verbatim):
  ```
  You are TECH_LEAD. Your task: review the following deliverable (ADR.md).
  You have NO access to any files — all context is provided below.

  === [DOC 1: Previous Sub-Task B-2 review JSON — SAD.md (Sub-Task 1/3, gaps field may contain non-blocking caveats)] ===
  <<paste full content here>>

  === [DOC 2: 02-architecture/SAD.md (APPROVED — full content)] ===
  <<paste full content here>>

  === [DOC 3: draft 02-architecture/ADR.md (full content)] ===
  <<paste full content here>>

  === [DOC 4: templates/ADR.md (template format)] ===
  <<paste full content here>>

  Review checklist:
  - Upstream deliverable review caveats addressed? (check previous B-2 gaps field)
  - All major decisions documented (tech stack, patterns, interfaces)?
  - Each ADR has clear context, decision, and consequences?
  - Alternatives considered documented?
  - Decision aligns with SAD.md architecture?

  Return JSON only:
  {"review_status":"APPROVE"|"REJECT",
   "reason":"<concise summary>",
   "citations":["file:line"],
   "gaps":[{"severity":"low|medium|high","message":"<issue>","fr_id":"<FR-XX or null>"}]}
  ```

- [ ] **[B-2]** Agent B returns JSON — parse `review_status` **AND** `gaps` severity:
  > gaps schema: `[{"severity": "low|medium|high", "message": "...", "fr_id": "FR-XX or null"}]`
  - `APPROVE` + all gaps are `low` → continue to Sub-Task 3/3
  - `APPROVE` + any gap is `medium` or `high` → fix gaps → **re-dispatch B as round 2**
    (embed same docs as B-1 above, replacing `ADR.md` with its updated content)
    → continue to Sub-Task 3/3 only after round-2 APPROVE
  - `REJECT` → Agent A fixes gaps → re-dispatch B. Max 5 rounds (HR-12).
    > If round 5 REJECT: escalate to human — orchestrator cannot self-resolve.
    > Human fix → re-dispatch Agent B (same prompt + updated content) → `APPROVE` required before continuing.

  > ⚠️ **BLOCKING**: Do NOT start the next Sub-Task until this sub-task's current
  > round is fully APPROVED (including any required round 2).
  > AgentSpawner records dispatches to `.methodology/sessions_spawn.log` (non-blocking debug trail).

  > fr_id uses P2 as phase-level placeholder; replace with FR-XX for FR-specific plans.

### Sub-Task 3/3: TEST_SPEC.md — Test Specification Catalog — named test cases from SRS (single source of truth, D4 unified check)

**Depends on**: ADR.md (+ Sub-Task 2/3 review: previous review gaps carry forward)
**Agent A**: ARCHITECT
**Agent B**: TECH_LEAD

**A/B Work** (HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews):
- [ ] **[A-1]** Agent A (ARCHITECT): Generate TEST_SPEC.md via derive_test_cases.md skill → preserve TEST_INVENTORY.yaml names where specified → apply 8-Question Protocol per FR (Q1-Q8 + Step 2.5 Interface Contracts + Step 4 Infrastructure Wiring) → populate cross-cutting section
  - FORBIDDEN: vague/non-testable acceptance criteria
- [ ] **[A-2]** Agent A returns `{status, files, confidence, citations, summary}`
- [ ] **[B-1]** Agent B (TECH_LEAD) — dispatch as **STATELESS** subagent:
  > ⚠️  **STATELESS SANDBOX**: Agent B has ZERO access to local files or /tmp.
  > NEVER write 'read 01-requirements/SRS.md' in the prompt — it will fail silently.
  > ALL context must be pasted verbatim into the prompt text. This is mandatory.
  >
  > **Lesson (stateless agent)**: Rounds 2-3 failed because prompts used file paths.
  > Round 4 succeeded only after embedding full document content directly.

  **Embed these documents in full** (copy content, not paths):
  - `Previous Sub-Task B-2 review JSON — ADR.md (Sub-Task 2/3, gaps field may contain non-blocking caveats)`
  - `01-requirements/SRS.md (APPROVED — full content)`
  - `02-architecture/SAD.md (APPROVED — full content)`
  - `02-architecture/ADR.md (APPROVED — full content)`
  - `draft 02-architecture/TEST_SPEC.md (full content)`

  **Agent B prompt structure** (use this template verbatim):
  ```
  You are TECH_LEAD. Your task: review the following deliverable (TEST_SPEC.md).
  You have NO access to any files — all context is provided below.

  === [DOC 1: Previous Sub-Task B-2 review JSON — ADR.md (Sub-Task 2/3, gaps field may contain non-blocking caveats)] ===
  <<paste full content here>>

  === [DOC 2: 01-requirements/SRS.md (APPROVED — full content)] ===
  <<paste full content here>>

  === [DOC 3: 02-architecture/SAD.md (APPROVED — full content)] ===
  <<paste full content here>>

  === [DOC 4: 02-architecture/ADR.md (APPROVED — full content)] ===
  <<paste full content here>>

  === [DOC 5: draft 02-architecture/TEST_SPEC.md (full content)] ===
  <<paste full content here>>

  Review checklist:
  - Upstream deliverable review caveats addressed? (check previous B-2 gaps field)
  - Every FR has ≥1 named test case?
  - 8-Question Protocol applied per FR (Q1-Q8 + Step 2.5; YAML names do NOT exempt missing categories)?
  - Cross-cutting section complete?
  - Summary table populated?
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
    (embed same docs as B-1 above, replacing `TEST_SPEC.md` with its updated content)
    → all deliverables complete; proceed to Agent B Peer Review only after round-2 APPROVE
  - `REJECT` → Agent A fixes gaps → re-dispatch B. Max 5 rounds (HR-12).
    > If round 5 REJECT: escalate to human — orchestrator cannot self-resolve.
    > Human fix → re-dispatch Agent B (same prompt + updated content) → `APPROVE` required before continuing.

  > ⚠️ **BLOCKING**: Do NOT start the next Sub-Task until this sub-task's current
  > round is fully APPROVED (including any required round 2).
  > AgentSpawner records dispatches to `.methodology/sessions_spawn.log` (non-blocking debug trail).

  > fr_id uses P2 as phase-level placeholder; replace with FR-XX for FR-specific plans.

### SAB Generation (Machine-Readable Architecture Baseline)

- [ ] **[SAB]** Generate `.methodology/SAB.json` from SAD.md §6 SAB block:
  ```bash
  python3 scripts/generate_sab.py --project .
  ```
  - SAB.json contains: layers, modules, allowed_dependencies, quality_targets
  - Used by: drift detector (M2), gate architecture dimension, constitution check
  - Also embedded inline in `quality_manifest.json` via `harness_bridge`

### Phase 2 Deliverables
- [ ] `SAD.md` — Software Architecture Document (every FR has module mapping)
- [ ] `ADR.md` — Architecture Decision Records (tech stack, patterns, interfaces)
- [ ] `TEST_SPEC.md` — Test specification catalog (named test cases from SRS, single source of truth — D4 unified check)
- [ ] `.methodology/quality_manifest.json` — Quality manifest (FR list + SAB data)
- [ ] `.methodology/SAB.json` — Machine-readable architecture baseline
- [x] `.methodology/sessions_spawn.log` — auto-populated by AgentSpawner (non-blocking debug trail)


### 🔒 CHECKPOINT-PEER-REVIEW: Agent B Peer Review — Phase 2 Exit
> Phase 1/2 exit gate = Agent B document review (NOT `harness run-gate --gate 1`).
> APPROVE criteria: all FRs addressed, no critical gaps, terminology consistent.

- [ ] **[B-1]** Agent B (TECH_LEAD) — dispatch as **STATELESS** subagent (holistic review of all deliverables):
  > ⚠️  **STATELESS SANDBOX**: Agent B has ZERO access to local files or /tmp.
  > NEVER pass file paths in the prompt — ALL document content must be pasted verbatim.
  >
  > **Lesson (stateless agent)**: Rounds 2-3 failed because prompts used file paths.
  > Round 4 succeeded only after embedding full document content directly.

  **Embed ALL deliverables in full** (copy content, not paths):
  > Note: `quality_manifest.json` and `SAB.json` are machine-generated by `generate_sab.py`
  > and are NOT embedded for manual review. Agent B reviews the human-authored documents only.
  - `02-architecture/SAD.md (full content)`
  - `02-architecture/ADR.md (full content)`
  - `02-architecture/TEST_SPEC.md (full content)`

  **Agent B prompt structure** (use this template verbatim):
  ```
  You are TECH_LEAD. Your task: holistic review of ALL Phase 2 deliverables.
  You have NO access to any files — all context is provided below.

  === [DOC 1: 02-architecture/SAD.md] ===
  <<paste full content here>>

  === [DOC 2: 02-architecture/ADR.md] ===
  <<paste full content here>>

  === [DOC 3: 02-architecture/TEST_SPEC.md] ===
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

- [ ] **[B-PUSH]** ✅ PUSH ② — Push to GitHub + HANDOVER.md — retry until success (CHECKPOINT-PEER-REVIEW saved):
  > Run `push-checkpoint` → if blocked, read the error → fix → re-run until green.
  > Do NOT use `--no-verify` to bypass.
  ```bash
  python3 harness_cli.py push-checkpoint --phase 2 --project .
  ```
  > This writes `HANDOVER.md` (crash-recovery checkpoint) to project root,
  > then commits + pushes all changes to origin.
  > After a crash, read HANDOVER.md first — it tells you where you were.

### Phase 2 → Phase 3: Implementation

- [ ] Confirm ALL checkpoints in this plan are ✓  (no skips — HR-03)
- [ ] Advance FSM to Phase 3 (writes new HANDOVER.md + local commit):
  ```bash
  python3 harness_cli.py advance-phase --completed 2 --project .
  ```
- [ ] Confirm `HANDOVER.md` reflects Phase 3 entry (`P3-entry` checkpoint, correct plan path)
- [ ] Open `phase3_plan.md` and follow from the top.
- [ ] If session crashes during Phase 3: read `HANDOVER.md` or run `generate-next-plan`
