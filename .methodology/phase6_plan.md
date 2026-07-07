# Phase 6 Full Execution Plan -- harness-methodology

> **Version**: v2.7.0 (project plan)
> **Project**: harness-methodology
> **Date**: 2026-05-31
> **Framework**: harness-methodology v2.7.0
> **Phase**: 6 - Quality Assurance
> **Status**: Full version (including Phase 6 detailed tasks)
> **Mode**: Dynamic (load-context at execution time)


---

## Phase 6 Tasks: Quality Assurance

### Phase 6 Overview
Phase 6 is a complete Gate 4 evaluation. Gate 4 replaces the entire P6 SOP.
No FR loop — Gate 4 evaluates the full project (14 dims, CRG recon, fully automated).

> **Checkpoint Index** (push to GitHub = checkpoint saved):
> - CHECKPOINT-GATE-4: Gate 4 (Full Project — 14 dims, fully automated)

### Entry Gate Verification

- [ ] **[ENTRY-CHECK]** Gate 3 PASS (P4 exit — P5 has no exit gate, P5 completed stands between):
  Verify P5 output artifacts exist: `05-verification/VERIFICATION_REPORT.md` + `05-verification/BASELINE.md`
  Proof: .methodology/quality_manifest.json records Gate 3 PASS from P4.
  If NOT confirmed: return to Phase 4 and complete exit gate first.

### Pre-Phase Preflight

- [ ] **[PREFLIGHT]** Run phase hooks (FSM, Kill-Switch, Drift):
  ```bash
  python3 harness_cli.py run-phase --phase 6 --project .
  ```
  If FAILED: fix FSM/Drift issues. There is no gate bypass flag.
  Re-run `run-phase` after each fix. Max 3 attempts.
  After 3 FAIL: escalate to human — provide last `run-phase --phase 6` full output.
  Human fix → re-run `run-phase --phase 6 --project .` → PASS required before continuing.

- [ ] **[PREFLIGHT-CI]** Confirm CI wiring unchanged (should be set since P1):
  1. `.github/workflows/harness_quality_gate.yml` exists
  2. Git hooks installed (`ls .git/hooks/prepare-commit-msg`)
  3. harness importable (submodule, PYTHONPATH, or vendored `quality_gate/`)
  4. Phase 6 confirmed in `.methodology/state.json` (`advance-phase` already run)
  > If stale: run `python3 harness_cli.py init-project --phase 6 --project . --overwrite`

### 🔄 [PHASE-CONTEXT] — Load Before Starting

```bash
python3 harness_cli.py load-context --phase 6 --project . --json \
  > .sessi-work/phase6_ctx.json
```
> Outputs `fr_ids`, `fr_details`, `modules` from current project state.

### P6 Phase End Audit (+ A/B Review)

> A/B collaboration is active for Phase 6 deliverables (HR-01).
> Agent A generates QUALITY_REPORT.md and RELEASE_NOTES.md.
> Agent B (ARCHITECT) reviews the deliverables and verifies Gate 4 score.

### Pre-Gate Preparation
- [ ] Confirm all FRs are merged to main branch
- [ ] Confirm no open critical or high issues from Gate 3

### Gate 4 Result JSON — Required Fields

> `finalize-gate --gate 4` validates A3 **before** scoring. Missing/insufficient → `[BLOCKED]`.

- [ ] **[A3] `devil_advocate`** + **`devil_advocate_evidence`** — artifact-backed DA challenge for all Tier 3 dims:
  ```json
  "devil_advocate": {
    "architecture": true, "readability": true, "error_handling": true,
    "documentation": true, "performance": true
  },
  "devil_advocate_evidence": {
    "architecture": {
      "challenger_model": "claude",
      "challenge": "<≥120 chars: the challenger persona's actual critique of the design/score>",
      "response": "<≥120 chars: the defence / justification>"
    }
  }
  ```
  > A bare boolean is **not** accepted (A3 is artifact-backed): for each Tier 3 dim, dispatch a
  > Claude sub-agent with a challenger persona, then record its `challenge` + `response` text.
  > **Orchestrator Pattern** (architecture/error_handling score = 0 due to hub-and-spoke):
  > complete the DA challenge AND add `"da_waiver": {"architecture": true}` to bypass the
  > score threshold — the waiver also requires the `devil_advocate_evidence.architecture` artifact.
  > See `harness/ssi/prompts/evaluate_dimension.md` §Orchestrator.

- [ ] **[A5] `issue_registry_path`** (advisory — no longer blocks) — path to the issue registry:
  ```json
  "issue_registry_path": ".sessi-work/issue_registry.json"
  ```
  > Advisory only: the registry is agent-written, so its presence never verified anything.
  > Populate via `issue_tracker.py add` during G4b for a useful audit trail.


### 🔒 CHECKPOINT-GATE-4: Phase 6 Exit
> linting(90) · type_safety(85) · test_coverage(80) · security(80) · secrets_scanning(100) · license_compliance(100) · mutation_testing(70) · architecture(80) · readability(80) · error_handling(80) · documentation(75) · performance(75) · integration_coverage(75) · test_assertion_quality(70)  [CRG recon inside run-gate · D4 spec-coverage unified ≥90%]

- [ ] **G4a** Prepare Gate 4:
  ```bash
  python3 harness_cli.py run-gate --gate 4 --phase 6 --project .
  ```
  Read the evaluation prompt printed above.
  (CRG recon triggered inside run-gate automatically — no separate action needed)

- [ ] **G4b** Evaluate all Gate 4 dimensions inline:
  - Follow `harness/ssi/prompts/evaluate_dimension.md`
  - Write result to `.sessi-work/gate4_result.json`
  - Failing dim: fix code → re-evaluate → re-score
  > Auto-fix engine may attempt to correct linting/coverage/type_safety issues automatically.
  > **CRG-ONLY dims** (architecture, error_handling): scores come from `crg_metrics.json`.
  > If score = 0 due to Orchestrator/hub-and-spoke pattern: complete DA challenge (A3 above)
  > and set `da_waiver` in gate4_result.json to bypass the threshold.
  > See `harness/ssi/prompts/evaluate_dimension.md` §Orchestrator Pattern False Positive.

- [ ] **G4c** Finalize Gate 4:
  ```bash
  python3 harness_cli.py finalize-gate --gate 4 --phase 6 --project .
  ```
  > **PUSH ⑧ in the 10-Push Strategy**: `finalize-gate --gate 4` writes HANDOVER.md + commits + pushes.
- [ ] **[D4]** D4 spec-coverage-check — unified v2.6 (Gate 4 threshold 90%):
  ```bash
  python3 harness_cli.py spec-coverage-check --project . --threshold 90.0
  ```
  FAIL → fix missing test implementations → re-run until coverage meets threshold

  **Early-stop cases after G4c:**
  - CASE 1 PASS:     score ≥ score_gate AND critical==0 → `quality_complete=True` → G4d
  - CASE 2 CONTINUE: score ≥ score_gate BUT issues remain → fix → repeat G4a
  - CASE 3 PLATEAU:  3 consecutive rounds, no new issues → `deferred_fixes.md` → proceed to push
  - CASE 4 BLOCKED:  max_rounds exhausted, not PASS → `GateBlockedError` → escalate to human
    > Human fix → re-run `run-gate --gate 4 → finalize-gate --gate 4` → CASE 1 PASS required before continuing.

- [ ] **G4d** ✅ Verify checkpoint saved (finalize-gate above already pushed + wrote HANDOVER.md):
  ```bash
  # Confirm HANDOVER.md exists at project root (written by finalize-gate → commit_and_push_gate)
  ls -la HANDOVER.md
  git log --oneline -1
  ```
  > `finalize-gate --gate 4` (G4c) calls `commit_and_push_gate()` which writes
  > `HANDOVER.md` **before** committing + pushing. No separate push needed here.
  > If HANDOVER.md is missing, re-run `finalize-gate` (do **not** raw-push).

- [ ] **G4e** Generate Release Notes:
  Create `RELEASE_NOTES.md` at project root summarizing changes since Gate 3.
  Include: version, date, FR list, Gate 4 composite score, known limitations.
  Reference: `06-quality/QUALITY_REPORT.md` (auto-generated by G4c finalize-gate).

- [ ] **G4f** Generate Final Sign-Off:
  Create `FINAL_SIGN_OFF.md` at project root.
  Include: project name, completion date, Gate 4 composite score, sign-off statement.
  Must reference `BASELINE.md` and `VERIFICATION_REPORT.md` (verification provenance).

- [ ] **G4g** Agent B Peer Review (HR-01):
  Agent B (ARCHITECT) reviews `06-quality/QUALITY_REPORT.md` and `RELEASE_NOTES.md`.
  Confirm all FRs are merged and Gate 4 score ≥ 85.

- [ ] **[PHASE-TRUTH]** Phase Truth ≥ 90% (HR-11) — verified by advance-phase
  > **FAIL** → check `phase_truth_verifier` output in `.sessi-work/`
  >   → identify which phase link or gate artifact failed
  >   → fix artifacts → re-run `advance-phase`
  >   → If 3 consecutive failures: escalate to human with `phase_truth_verifier` log

### Phase 6 Deliverables
- [ ] Gate 4 PASS (composite ≥ 85, all 14 dims, CRG recon done)
- [ ] `QUALITY_REPORT.md` - Quality report (auto-generated by Gate 4)
- [ ] `RELEASE_NOTES.md` - Release notes
- [ ] `FINAL_SIGN_OFF.md` - Final sign-off
- [x] `.methodology/sessions_spawn.log` — auto-populated by AgentSpawner (non-blocking debug trail)

### Phase 6 → Phase 7: Risk Management

- [ ] Confirm ALL checkpoints in this plan are ✓  (no skips — HR-03)
- [ ] **[GIT-TAG]** Push Gate 4 git tag (SKILL.md §0.4):
  ```bash
  SCORE=$(python3 -c "import json; d=json.load(open('.sessi-work/gate4_result.json')); print(d.get('composite_score','XX'))" 2>/dev/null || echo 'XX')
  git tag -a "harness-v4-$(date +%Y%m%d)-score${SCORE}" -m "Gate 4 PASS (score ${SCORE})"
  git push origin --tags
  ```

- [ ] **[TDD-PRECHECK]** Verify TDD checks pass — advance-phase enforces both:
  - `pytest --tb=short -q --cov=03-development/src --cov-fail-under=100` (exit 9)
  - `python3 harness_cli.py spec-coverage-check --project . --threshold 90.0` (exit 10, D4 unified v2.6)
  > For genuinely untestable lines add: `# pragma: no cover` (requires justification comment).

- [ ] Advance FSM to Phase 7 (writes new HANDOVER.md + local commit):
  ```bash
  python3 harness_cli.py advance-phase --completed 6 --project .
  ```
- [ ] Confirm `HANDOVER.md` reflects Phase 7 entry (`P7-entry` checkpoint, correct plan path)
- [ ] Open `phase7_plan.md` and follow from the top.
- [ ] If session crashes during Phase 7: read `HANDOVER.md` or run `generate-next-plan`
