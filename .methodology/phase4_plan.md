# Phase 4 Full Execution Plan -- harness-methodology

> **Version**: v2.7.0 (project plan)
> **Project**: harness-methodology
> **Date**: 2026-05-31
> **Framework**: harness-methodology v2.7.0
> **Phase**: 4 - Testing
> **Status**: Full version (including Phase 4 detailed tasks)
> **Mode**: Dynamic (load-context at execution time)


---

## Phase 4 Tasks: Test Planning & Execution

### Phase 4 Overview
Phase 4 formulates and executes a complete test plan based on Phase 3 code.
Each FR ends with a Gate 1 re-evaluation (CHECKPOINT). Phase exits via Gate 3 (14 dims).

> **Crash Recovery**: `python3 harness_cli.py resume-fr-phase --phase 4 --project .`
> prints the next pending step. Each `run-fr-step` auto-pushes to GitHub on completion.
> Per-FR TDD-RED/GREEN/IMPROVE/GATE1 each push immediately (idempotent on re-run).
> At milestones, `HANDOVER.md` is written with phase/FR/status summary.

> **Checkpoint Index**:
> - CHECKPOINT-0: TEST_PLAN.md (generate before per-FR testing starts)
> - MILESTONE: P4-mid push (≥50% FRs Gate 1 PASS) → **HANDOVER.md**
> - MILESTONE: P4-pre-gate3 push (all FRs done, before Gate 3) → **HANDOVER.md**
> - CHECKPOINT-GATE-3: Gate 3 (Phase 4 Exit) → **push + HANDOVER.md**

### Entry Gate Verification

- [ ] **[ENTRY-CHECK]** Gate 2 PASS:
  Proof: .methodology/quality_manifest.json records Gate 2 PASS from P3.
  If NOT confirmed: return to Phase 3 and complete exit gate first.

### Pre-Phase Preflight

- [ ] **[PREFLIGHT]** Run phase hooks (FSM, Kill-Switch, Drift):
  ```bash
  python3 harness_cli.py run-phase --phase 4 --project .
  ```
  If FAILED: fix FSM/Drift issues. There is no gate bypass flag.
  Re-run `run-phase` after each fix. Max 3 attempts.
  After 3 FAIL: escalate to human — provide last `run-phase --phase 4` full output.
  Human fix → re-run `run-phase --phase 4 --project .` → PASS required before continuing.

- [ ] **[PREFLIGHT-CI]** Confirm CI wiring unchanged (should be set since P1):
  1. `.github/workflows/harness_quality_gate.yml` exists
  2. Git hooks installed (`ls .git/hooks/prepare-commit-msg`)
  3. harness importable (submodule, PYTHONPATH, or vendored `quality_gate/`)
  4. Phase 4 confirmed in `.methodology/state.json` (`advance-phase` already run)
  > If stale: run `python3 harness_cli.py init-project --phase 4 --project . --overwrite`

### 🔄 [PHASE-CONTEXT] — Load Before Starting

```bash
python3 harness_cli.py load-context --phase 4 --project . --json \
  > .sessi-work/phase4_ctx.json
```
> Outputs `fr_ids`, `fr_details`, `modules` from current project state.
> All `{FR-ID}` references in tasks below come from this file.

### CHECKPOINT-0: Generate TEST_PLAN.md (advisory — Finding #11)

> Generate `04-testing/TEST_PLAN.md` from SRS.md FR acceptance criteria.
> This step runs once before per-FR test execution.
>
> **Status (Finding #11 fix)**: TEST_PLAN.md is **advisory**, not blocking.
> No harness tool produces it (the P4 plan instructed the agent to write
> it manually, but agents that followed the plan verbatim often produced
> a stub or skipped the step and got hard-blocked by the
> `phase_artifact_enforcer` for P4 VERIFY). It is now an
> `advisory` artifact: agents are encouraged to write it for traceability
> and per-FR test planning, but its absence is a warning, not a gate
> block. TEST_RESULTS.md (which is hard-blocked) is the real
> per-FR-coverage signal.

**Generate TEST_PLAN.md** (orchestrator runs directly — not a sub-agent dispatch):
- [ ] Read SRS.md FR acceptance criteria → write TEST_PLAN.md with per-FR test cases
  - For each FR: test case ID, description, input, expected output, priority
  - Include positive, negative, boundary, and edge case categories
  - Output: `04-testing/TEST_PLAN.md`
- [ ] Verify TEST_PLAN.md covers all FRs from manifest/quality_manifest.json
- [ ] **[TP-DONE]** TEST_PLAN.md written (recommended): all FRs have ≥1 test case, NFRs addressed

### FR Tasks — Expanded at Execution Time

- [ ] **[ENV-CHECK]** Run ONCE before the FR loop — `GATE1`/`GATE1-DELTA` preflight requires `.sessi-work/env_check_result.json`:
  ```bash
  python3 harness_cli.py run-env-check --phase 4 --project .
  # evaluate inline → write .sessi-work/env_check_result.json →
  python3 harness_cli.py finalize-env-check --phase 4 --project .
  ```
  > Without this, every `run-fr-step --step GATE1-DELTA` blocks on 'env_check_result.json not found'.

> Read `fr_ids` from `.sessi-work/phase4_ctx.json`.
> For each `{FR-ID}` in the list, execute the template below:

---
**{FR-ID} — {FR-TITLE from fr_details}**

- [ ] **[ORCH-GATE1-DELTA]** `run-fr-step --phase 4 --fr-id {FR-ID} --step GATE1-DELTA --project .`
> Crash recovery: `resume-fr-phase` auto-detects code changes → switches to full TDD if needed.
> **Auto-skip**: if NO FR's code changed since its last Gate 1 PASS, `advance-phase --completed 4`
> treats this entire DELTA loop as satisfied automatically — you may skip the per-FR steps.
> Only FRs whose code actually changed need a re-evaluation.
>
> **GATE1-DELTA outcomes:**
> - CASE 1 PASS:    Gate 1 PASS → continue to next {FR-ID}
> - CASE 2 FAIL:    Gate 1 FAIL → full TDD auto-triggered by crash recovery:
>   `run-fr-step --phase 4 --fr-id {FR-ID} --step TDD-RED` → TDD-GREEN → TDD-IMPROVE → GATE1
> - CASE 3 BLOCKED: 3 TDD rounds still failing → escalate to human.
>   Provide: last Gate 1 output + pytest failure log.

---

### P4 Milestone Pushes (10-Push Strategy ⑤⑥)

> Per-FR steps push automatically via `run-fr-step`. The two **milestone pushes** below
> also write `HANDOVER.md` with phase/FR/status summary and push to origin.
> All FR IDs in this project: <FR-01,FR-02,…>

- [ ] **PUSH ⑤ — P4-mid** (trigger when ≥50%/N FRs have Gate 1 PASS):
  ```bash
  python3 harness_cli.py push-milestone --type p4-mid --project . \
    --fr-done 50% --fr-total N --fr-ids <comma-separated FR-IDs with Gate 1 PASS>
  ```
  > `--fr-ids` lists the FRs with Gate 1 PASS so far. Replace `<comma-separated FR-IDs with Gate 1 PASS>` with actual.
  > Writes HANDOVER.md + commits + pushes. Next session reads HANDOVER.md to resume.

- [ ] **PUSH ⑥ — P4-pre-gate3** (trigger when all N FRs Gate 1 PASS, before Gate 3):
  ```bash
  python3 harness_cli.py push-milestone --type p4-pre-gate3 --project . \
    --fr-ids <comma-separated FR-IDs with Gate 1 PASS>
  ```
  > Last stable snapshot before Gate 3 evaluation. HANDOVER.md + push.

### TEST_RESULTS.md Summary

- [ ] **[TEST-RESULTS-SUMMARY]** Finalize `04-testing/TEST_RESULTS.md` before milestone push:
  - Summarise test execution: test cases run, pass/fail outcome, any deferred issues
  - Real test execution is enforced by advance-phase TDD-PRECHECK (`pytest --cov-fail-under=100`), not by string-matching this document


### 🔒 CHECKPOINT-GATE-3: Phase 4 Exit
> linting(90) · type_safety(85) · test_coverage(80) · security(80) · secrets_scanning(100) · license_compliance(100) · mutation_testing(70) · integration_coverage(60) · architecture(80) · readability(80) · error_handling(80) · documentation(75) · test_assertion_quality(60) · performance(75)  [CRG recon inside run-gate · D4 spec-coverage unified ≥80%]

- [ ] **G3a** Prepare Gate 3:
  ```bash
  python3 harness_cli.py run-gate --gate 3 --phase 4 --project .
  ```
  Read the evaluation prompt printed above.
  (CRG recon triggered inside run-gate automatically — no separate action needed)

- [ ] **G3b** Evaluate all Gate 3 dimensions inline:
  - Follow `harness/ssi/prompts/evaluate_dimension.md`
  - Write result to `.sessi-work/gate3_result.json`
  - Failing dim: fix code → re-evaluate → re-score
  > Auto-fix engine may attempt to correct linting/coverage/type_safety issues automatically.
  > **CRG-ONLY dims** (architecture, error_handling): scores come from `crg_metrics.json`.
  > If score = 0 due to Orchestrator/hub-and-spoke pattern: complete DA challenge (A3 above)
  > and set `da_waiver` in gate4_result.json to bypass the threshold.
  > See `harness/ssi/prompts/evaluate_dimension.md` §Orchestrator Pattern False Positive.

- [ ] **G3c** Finalize Gate 3:
  ```bash
  python3 harness_cli.py finalize-gate --gate 3 --phase 4 --project .
  ```
- [ ] **[D4]** D4 spec-coverage-check — unified v2.6 (Gate 3 threshold 80%):
  ```bash
  python3 harness_cli.py spec-coverage-check --project . --threshold 80.0
  ```
  FAIL → fix missing test implementations → re-run until coverage meets threshold

  **Early-stop cases after G3c:**
  - CASE 1 PASS:     score ≥ score_gate AND critical==0 → `quality_complete=True` → G3d
  - CASE 2 CONTINUE: score ≥ score_gate BUT issues remain → fix → repeat G3a
  - CASE 3 PLATEAU:  3 consecutive rounds, no new issues → `deferred_fixes.md` → proceed to push
  - CASE 4 BLOCKED:  max_rounds exhausted, not PASS → `GateBlockedError` → escalate to human
    > Human fix → re-run `run-gate --gate 3 → finalize-gate --gate 3` → CASE 1 PASS required before continuing.

- [ ] **G3d** ✅ Verify checkpoint saved (finalize-gate above already pushed + wrote HANDOVER.md):
  ```bash
  # Confirm HANDOVER.md exists at project root (written by finalize-gate → commit_and_push_gate)
  ls -la HANDOVER.md
  git log --oneline -1
  ```
  > `finalize-gate --gate 3` (G3c) calls `commit_and_push_gate()` which writes
  > `HANDOVER.md` **before** committing + pushing. No separate push needed here.
  > If HANDOVER.md is missing, re-run `finalize-gate` (do **not** raw-push).

- [ ] **[PHASE-TRUTH]** Phase Truth ≥ 90% (HR-11) — verified by advance-phase
  > **FAIL** → check `phase_truth_verifier` output in `.sessi-work/`
  >   → identify which phase link or gate artifact failed
  >   → fix artifacts → re-run `advance-phase`
  >   → If 3 consecutive failures: escalate to human with `phase_truth_verifier` log

### Phase 4 Deliverables
- [ ] `TEST_PLAN.md` - Test plan
- [ ] `TEST_RESULTS.md` - Test results (test execution summary)
- [ ] `COVERAGE_REPORT.md` - Coverage report
- [x] `.methodology/sessions_spawn.log` — auto-populated by AgentSpawner (non-blocking debug trail)
- [ ] Gate 1 PASS for every FR
- [ ] Gate 3 PASS (phase exit, composite ≥ 80, 14 dims)

### Phase 4 → Phase 5: Verification & Delivery

- [ ] Confirm ALL checkpoints in this plan are ✓  (no skips — HR-03)
- [ ] **[TDD-PRECHECK]** Verify TDD checks pass — advance-phase enforces both:
  - `pytest --tb=short -q --cov=03-development/src --cov-fail-under=100` (exit 9)
  - `python3 harness_cli.py spec-coverage-check --project . --threshold 80.0` (exit 10, D4 unified v2.6)
  > For genuinely untestable lines add: `# pragma: no cover` (requires justification comment).

- [ ] Advance FSM to Phase 5 (writes new HANDOVER.md + local commit):
  ```bash
  python3 harness_cli.py advance-phase --completed 4 --project .
  ```
- [ ] Confirm `HANDOVER.md` reflects Phase 5 entry (`P5-entry` checkpoint, correct plan path)
- [ ] Open `phase5_plan.md` and follow from the top.
- [ ] If session crashes during Phase 5: read `HANDOVER.md` or run `generate-next-plan`
