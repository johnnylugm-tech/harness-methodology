# Phase 3 Full Execution Plan -- harness-methodology

> **Version**: v2.7.0 (project plan)
> **Project**: harness-methodology
> **Date**: 2026-05-31
> **Framework**: harness-methodology v2.7.0
> **Phase**: 3 - Implementation
> **Status**: Full version (including Phase 3 detailed tasks)
> **Mode**: Dynamic (load-context at execution time)


---

## Phase 3 Tasks: Implementation

### Phase 3 Overview
Phase 3 implements all FR modules according to SAD, including unit tests.
Each FR ends with a Gate 1 quality evaluation (CHECKPOINT). Phase exits via Gate 2.

> **Crash Recovery**: `python3 harness_cli.py resume-fr-phase --phase 3 --project .`
> prints the next pending step. Each `run-fr-step` auto-pushes to GitHub on completion.
> Per-FR TDD-RED/GREEN/IMPROVE/GATE1 each push immediately (idempotent on re-run).
> At milestones, `HANDOVER.md` is written with phase/FR/status summary.

> **Checkpoint Index**:
> - MILESTONE: P3-mid push (≥50% FRs Gate 1 PASS) → **HANDOVER.md**
> - MILESTONE: P3-pre-gate2 push (all FRs done) → **HANDOVER.md**
> - CHECKPOINT-GATE-2: Gate 2 (Phase 3 Exit) → **push + HANDOVER.md**

### Entry Gate Verification

- [ ] **[ENTRY-CHECK]** P2 review-complete:
  Proof: git log contains commit 'phase2(review-complete): Phase 2 deliverables APPROVED'.
  If NOT confirmed: return to Phase 2 and complete exit gate first.

- [ ] **[P2-ARTIFACTS]** Verify Phase 2 output artifacts exist:
  ```bash
  ls -la 02-architecture/SAD.md 02-architecture/ADR.md 02-architecture/TEST_SPEC.md \
     .methodology/quality_manifest.json .methodology/SAB.json
  git log --oneline --grep="APPROVE" -1
  ```
  If any file missing: return to Phase 2 and complete missing deliverables.

### Pre-Phase Preflight

- [ ] **[PREFLIGHT]** Run phase hooks (FSM, Constitution, Kill-Switch, Drift, CI Readiness):
  ```bash
  python3 harness_cli.py run-phase --phase 3 --project .
  ```
  If FAILED: fix FSM/Constitution/Drift issues. There is no gate bypass flag.
  Re-run `run-phase` after each fix. Max 3 attempts.
  After 3 FAIL: escalate to human — provide last `run-phase --phase 3` full output.
  Human fix → re-run `run-phase --phase 3 --project .` → PASS required before continuing.

- [ ] **[PREFLIGHT-CI]** Confirm CI wiring unchanged (should be set since P1):
  1. `.github/workflows/harness_quality_gate.yml` exists
  2. Git hooks installed (`ls .git/hooks/prepare-commit-msg`)
  3. harness importable (submodule, PYTHONPATH, or vendored `quality_gate/`)
  4. Phase 3 confirmed in `.methodology/state.json` (`advance-phase` already run)
  > If stale: run `python3 harness_cli.py init-project --phase 3 --project . --overwrite`

### 🔄 [PHASE-CONTEXT] — Load Before Starting

```bash
python3 harness_cli.py load-context --phase 3 --project . --json \
  > .sessi-work/phase3_ctx.json
```
> Outputs `fr_ids`, `fr_details`, `modules` from current project state.
> All `{FR-ID}` references in tasks below come from this file.

### FR Tasks — Expanded at Execution Time

- [ ] **[ENV-CHECK]** Run ONCE before the FR loop — `GATE1`/`GATE1-DELTA` preflight requires `.sessi-work/env_check_result.json`:
  ```bash
  python3 harness_cli.py run-env-check --phase 3 --project .
  # evaluate inline → write .sessi-work/env_check_result.json →
  python3 harness_cli.py finalize-env-check --phase 3 --project .
  ```
  > Without this, every `run-fr-step --step GATE1` blocks on 'env_check_result.json not found'.

> Read `fr_ids` from `.sessi-work/phase3_ctx.json`.
> For each `{FR-ID}` in the list, execute the template below:

---
**{FR-ID} — {FR-TITLE from fr_details}**

- [ ] **[ORCH-RED]**     `run-fr-step --phase 3 --fr-id {FR-ID} --step TDD-RED --project . --srs 01-requirements/SRS.md`
- [ ] **[P3-MIRROR]**    `python3 harness_cli.py check-test-mirrors-spec --phase 3 --fr-id {FR-ID} --test-file tests/test_*.py --project .`
- [ ] **[ORCH-GREEN]**   `run-fr-step --phase 3 --fr-id {FR-ID} --step TDD-GREEN --project . --srs 01-requirements/SRS.md`
- [ ] **[ORCH-IMPROVE]** `run-fr-step --phase 3 --fr-id {FR-ID} --step TDD-IMPROVE --project .`
- [ ] **[ORCH-GATE1]**   `run-fr-step --phase 3 --fr-id {FR-ID} --step GATE1 --project .`
> Gate 1 thresholds: linting(90) · type_safety(85) · test_coverage(80)
> Crash recovery: `resume-fr-phase --phase 3 --project .`
>
> **Gate 1 outcomes:**
> - CASE 1 PASS:    Gate 1 PASS → continue to next {FR-ID}
> - CASE 2 FAIL:    Fix failing dims → re-run `run-fr-step --step GATE1`
>   (linting: `ruff check . --fix`; coverage: add tests; type_safety: fix mypy errors;
>   test_assertion_quality: add assertions to zero-assert test functions)
> - CASE 3 BLOCKED: 3 rounds still failing → escalate to human.
>   Provide: Gate 1 output + failing dimension details.

---

### P3 Milestone Pushes (10-Push Strategy ③④)

> Per-FR steps push automatically via `run-fr-step`. The two **milestone pushes** below
> also write `HANDOVER.md` with phase/FR/status summary and push to origin.
> All FR IDs in this project: <FR-01,FR-02,…>

- [ ] **PUSH ③ — P3-mid** (trigger when ≥50%/N FRs have Gate 1 PASS):
  ```bash
  python3 harness_cli.py push-milestone --type p3-mid --project . \
    --fr-done 50% --fr-total N --fr-ids <comma-separated FR-IDs with Gate 1 PASS>
  ```
  > `--fr-ids` lists the FRs with Gate 1 PASS so far. Replace `<comma-separated FR-IDs with Gate 1 PASS>` with actual.
  > Writes HANDOVER.md + commits + pushes. Next session reads HANDOVER.md to resume.

- [ ] **PUSH ④ — P3-pre-gate2** (trigger when all N FRs Gate 1 PASS, before Gate 2):
  ```bash
  python3 harness_cli.py push-milestone --type p3-pre-gate2 --project . \
    --fr-ids <comma-separated FR-IDs with Gate 1 PASS>
  ```
  > Last stable snapshot before Gate 2 evaluation. HANDOVER.md + push.


### 🔒 CHECKPOINT-GATE-2: Phase 3 Exit
> linting(90) · type_safety(85) · test_coverage(80) · security(80) · secrets_scanning(100) · license_compliance(100) · mutation_testing(70) · integration_coverage(60) · test_assertion_quality(60)  [D4 spec-coverage unified ≥60%]

- [ ] **G2a** Prepare Gate 2:
  ```bash
  python3 harness_cli.py run-gate --gate 2 --phase 3 --project .
  ```
  Read the evaluation prompt printed above.

- [ ] **G2b** Evaluate all Gate 2 dimensions inline:
  - Follow `harness/ssi/prompts/evaluate_dimension.md`
  - Write result to `.sessi-work/gate2_result.json`
  - Failing dim: fix code → re-evaluate → re-score
  > Auto-fix engine may attempt to correct linting/coverage/type_safety issues automatically.

- [ ] **G2c** Finalize Gate 2:
  ```bash
  python3 harness_cli.py finalize-gate --gate 2 --phase 3 --project .
  ```
- [ ] **[D4]** D4 spec-coverage-check — unified v2.6 (Gate 2 threshold 60%):
  ```bash
  python3 harness_cli.py spec-coverage-check --project . --threshold 60.0
  ```
  FAIL → fix missing test implementations → re-run until coverage meets threshold

  **Early-stop cases after G2c:**
  - CASE 1 PASS:     score ≥ score_gate AND critical==0 → `quality_complete=True` → G2d
  - CASE 2 CONTINUE: score ≥ score_gate BUT issues remain → fix → repeat G2a
  - CASE 3 PLATEAU:  3 consecutive rounds, no new issues → `deferred_fixes.md` → proceed to push
  - CASE 4 BLOCKED:  max_rounds exhausted, not PASS → `GateBlockedError` → escalate to human
    > Human fix → re-run `run-gate --gate 2 → finalize-gate --gate 2` → CASE 1 PASS required before continuing.

- [ ] **G2d** ✅ Verify checkpoint saved (finalize-gate above already pushed + wrote HANDOVER.md):
  ```bash
  # Confirm HANDOVER.md exists at project root (written by finalize-gate → commit_and_push_gate)
  ls -la HANDOVER.md
  git log --oneline -1
  ```
  > `finalize-gate --gate 2` (G2c) calls `commit_and_push_gate()` which writes
  > `HANDOVER.md` **before** committing + pushing. No separate push needed here.
  > If HANDOVER.md is missing, re-run `finalize-gate` (do **not** raw-push).

- [ ] **[PHASE-TRUTH]** Phase Truth ≥ 90% (HR-11) — verified by advance-phase
  > **FAIL** → check `phase_truth_verifier` output in `.sessi-work/`
  >   → identify which phase link or gate artifact failed
  >   → fix artifacts → re-run `advance-phase`
  >   → If 3 consecutive failures: escalate to human with `phase_truth_verifier` log

### Phase 3 Deliverables
- [ ] `03-development/src/` - All FR modules implemented
- [ ] `tests/` - Unit tests (≥80% coverage per FR)
- [x] `.methodology/sessions_spawn.log` — auto-populated by AgentSpawner (non-blocking debug trail)
- [ ] Gate 1 PASS for every FR
- [ ] Gate 2 PASS (phase exit, composite ≥ 75)

### Phase 3 → Phase 4: Testing

- [ ] Confirm ALL checkpoints in this plan are ✓  (no skips — HR-03)
- [ ] **[TDD-PRECHECK]** Verify TDD checks pass — advance-phase enforces both:
  - `pytest --tb=short -q --cov=03-development/src --cov-fail-under=100` (exit 9)
  - `python3 harness_cli.py spec-coverage-check --project . --threshold 60.0` (exit 10, D4 unified v2.6)
  > For genuinely untestable lines add: `# pragma: no cover` (requires justification comment).

- [ ] Advance FSM to Phase 4 (writes new HANDOVER.md + local commit):
  ```bash
  python3 harness_cli.py advance-phase --completed 3 --project .
  ```
- [ ] Confirm `HANDOVER.md` reflects Phase 4 entry (`P4-entry` checkpoint, correct plan path)
- [ ] Open `phase4_plan.md` and follow from the top.
- [ ] If session crashes during Phase 4: read `HANDOVER.md` or run `generate-next-plan`
