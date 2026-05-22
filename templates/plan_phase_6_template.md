# Phase 6 Execution Plan — {PROJECT_NAME}

> **Version**: {VERSION}
> **Project**: {PROJECT_NAME}
> **Date**: {DATE}
> **Framework**: harness-methodology {VERSION}
> **Phase**: 6 — Quality Assurance

---

## 0. Execution Protocol

```
[Step 0] READ state.json → current_phase=6
[Step 1] LOAD SKILL.md §4 Phase routing
[Step 2] CHECK entry conditions → blocker → STOP
[Step 3] EXECUTE SOP → LAZY LOAD docs/P6_SOP.md
[Step 4] RECORD output | collect quality data (no A/B — Phase End Audit)
[Step 5] CHECK exit conditions → fail → FIX + RETRY
[Step 6] UPDATE state.json phase=7 → GOTO 1
```

**CLI Commands**:
```bash
python3 harness_cli.py run-phase --phase 6 --project .
python3 harness_cli.py run-gate --gate 4 --phase 6
python3 harness_cli.py finalize-gate --gate 4 --phase 6
python3 harness_cli.py generate-next-plan --phase 6
```

---

## 1. Hard Rules (HR-01 through HR-15)

| HR | Rule | Consequence | Action |
|----|------|-------------|--------|
| HR-01 | A/B must be different Agents, no self-review | Terminate -25 | **Phase 6: NOT applicable** — A/B replaced by Phase End Audit |
| HR-02 | Quality Gate requires actual command output | Terminate -20 | Save stdout for each QG |
| HR-03 | Phase must execute in sequence, no skipping | Terminate -30 | state.json phase=6 |
| HR-04 | HybridWorkflow mode=ON | Terminate | **Phase 6: A/B removed** — Gate 4 is the exit mechanism |
| HR-05 | On conflict, harness-methodology wins | Log | disputes resolved by harness-methodology |
| HR-06 | No frameworks outside spec | Terminate -20 | forbidden list |
| HR-07 | DEVELOPMENT_LOG must record session_id | -15 | record session_id per entry |
| HR-08 | Phase end must run Quality Gate | Terminate -10 | Gate 4 (`run-gate --gate 4 --phase 6`) |
| HR-09 | Claims Verifier must pass | Terminate -20 | citations match |
| HR-10 | sessions_spawn.log A/B records | Terminate -15 | **Phase 6: NOT applicable** — Phase End Audit replaces A/B check |
| HR-11 | Phase Truth < 90% blocks next Phase | Terminate | <90% → PAUSE |
| HR-12 | A/B review > 5 rounds → PAUSE | - | **Phase 6: NOT applicable** |
| HR-13 | Phase elapsed > estimated x3 → PAUSE | - | record start_time |
| HR-14 | Integrity < 40 → FREEZE | - | check Integrity post-QG |
| HR-15 | citations must include line numbers + artifact_verification | -15 | no citations = task failed |

---

## 2. Phase 6 Execution Protocol (No A/B)

> **Phase 6 does NOT use A/B collaboration.** A/B is Phase 1-2 only.
> Phase 6 exit is controlled by:
> 1. **Phase End Audit** — verifies QUALITY_REPORT.md exists, Gate 4 score recorded in manifest, all FRs merged.

### TH Thresholds (Phase 6)

| TH | Metric | Threshold | Verification |
|----|--------|-----------|-------------|
| TH-02 | Constitution total score | >=80% | `core/quality_gate/constitution/runner.py --type all` |
| TH-07 | Logic correctness | >=90 | `phase-verify` |
| TH-15 | Phase Truth | >90% | `advance-phase` (built-in HR-11 check) |

### Applicable HR Rules (Phase 6)

HR-02 | HR-03 | HR-05 | HR-06 | HR-07 | HR-08 | HR-09 | HR-11 | HR-13 | HR-14 | HR-15

### Quality Data Collection

- [ ] Agent (orchestrator) collects quality data from TEST_RESULTS.md, BASELINE.md, VERIFICATION_REPORT.md
- [ ] Generate QUALITY_REPORT.md using `scripts/generate_quality_report.py`:
  ```bash
  python3 scripts/generate_quality_report.py --project $REPO
  ```
- [ ] Verify QUALITY_REPORT.md covers all 14 dimensions and references BASELINE + VERIFICATION_REPORT

### Phase End Audit (Required before Gate 4 Hermes push)

```bash
python3 scripts/phase_end_audit.py --phase 6 --project .
```

Fix all CRITICAL gaps before proceeding. See `scripts/phase_end_audit.py` for details.

---

## 3. Prior Phase Artifacts

{artifacts_summary}

> Verify all prior phase artifacts exist and are complete before execution.
> Phase 6 builds on Phase 5 system testing: BASELINE.md and VERIFICATION_REPORT.md.

---

## 4. Output Tree

```
06-quality/
+-- QUALITY_REPORT.md         # Quality report (14-dimension assessment)
+-- MONITORING_PLAN.md        # Monitoring plan
```
```
Project Root/
+-- RELEASE_NOTES.md          # Release notes (auto-generated from git log)
+-- FINAL_SIGN_OFF.md         # Final sign-off document
```

### Phase 6 Deliverable Checklist

- [ ] `06-quality/QUALITY_REPORT.md` — 14-dimension quality assessment
- [ ] `06-quality/MONITORING_PLAN.md` — Monitoring plan
- [ ] `RELEASE_NOTES.md` — Release notes
- [ ] `FINAL_SIGN_OFF.md` — Final sign-off with gate scores
- [ ] Constitution score >= 80%
- [ ] Logic correctness >= 90
- [ ] Phase Truth > 90%
- [ ] `.methodology/audit_gaps_6.md` — Phase End Audit has no CRITICAL gaps (`phase_end_audit.py --phase 6`)
- [ ] [ASPICE] QUALITY_REPORT.md references BASELINE.md by filename keyword `BASELINE`
- [ ] [ASPICE] QUALITY_REPORT.md references VERIFICATION_REPORT.md by filename keyword `VERIFICATION_REPORT`

---

## 5. Quality Evaluation Tasks (Pre-Gate Preparation)

### 5.1 Pre-Gate Checklist
- [ ] Confirm all FRs merged to main branch
- [ ] Confirm no open critical or high issues from previous gate

### 5.2 Quality Dimensions (Gate 4 — 14 dimensions)

| Dim | Dimension | Weight | Evaluation Command | Target |
|-----|-----------|--------|-------------------|--------|
| 1 | **linting** | 10% | `ruff check` | 0 errors |
| 2 | **type_safety** | 10% | `mypy --strict` | 0 errors |
| 3 | **test_coverage** | 10% | `pytest --cov` | >= 80% |
| 4 | **security** | 8% | Bandit scan | 0 HIGH issues |
| 5 | **secrets_scanning** | 6% | `detect-secrets` | 0 secrets |
| 6 | **license_compliance** | 6% | License audit | 0 forbidden licenses |
| 7 | **mutation_testing** | 8% | `mutmut` / `mutpy` | score >= 70% |
| 8 | **architecture** | 8% | CRG structural coupling check | >= 90% |
| 9 | **readability** | 6% | Maintainability index | Radon MI >= 70 |
| 10 | **error_handling** | 6% | CRG error flow analysis | 100% flow coverage |
| 11 | **documentation** | 6% | Docstring / API coverage | 100% coverage |
| 12 | **performance** | 6% | BASELINE.md regression check | No regression |
| 13 | **integration_coverage** | 5% | Integration test coverage | >= 80% |
| 14 | **test_assertion_quality** | 5% | Assertion density/quality | >= 90% |

### 5.3 Task Table

| Task | Owner | Input | Output |
|------|-------|-------|--------|
| Quality data collection | QA_ENGINEER | TEST_RESULTS.md, BASELINE.md | Quality data |
| Constitution check | QA_ENGINEER | All Phase outputs | Check report |
| QUALITY_REPORT writing | QA_ENGINEER | All check results | QUALITY_REPORT.md |

---

## 6. Agent Prompt Templates

### QA_ENGINEER

```
TASK: Generate QUALITY_REPORT
TASK_ID: task-p6-qa

GOAL: Comprehensive quality assessment to ensure system meets release standards.

ON DEMAND READS (read only necessary sections):
- 04-testing/TEST_RESULTS.md (failed cases)
- 05-verification/BASELINE.md (performance baseline data)
- 06-quality/QUALITY_REPORT.md (existing version if any)

OUTPUT:
- 06-quality/QUALITY_REPORT.md
- Issue fix plan (if issues found)

PASS CRITERIA:
- Constitution quality total >= 80%
- Gate 4 score >= 85
- All HIGH priority issues resolved or risk accepted
- MAX 5 iteration rounds

FORBIDDEN:
- Concealing quality issues
- Leaving HIGH priority issues unresolved
- Report data not matching reality
- Missing citations / no line numbers (HR-15 violation)

OUTPUT_FORMAT:
{
  "status": "success|error",
  "result": "QUALITY_REPORT.md path",
  "confidence": 1-10,
  "citations": ["TEST_RESULTS.md#L30-L40"],
  "summary": "under 50 chars"
}
```

---

## 7. Quality Gate (Step 9)

```bash
# 1. Constitution Check
python3 -m core.quality_gate.constitution.runner

# 2. Logic Verification
python3 harness_cli.py check-logic --project {PROJECT_PATH}

# 3. Coverage Check
pytest {PROJECT_PATH}/tests/ --cov={SOURCE_DIR} --cov-report=term -q
```

---


---

## 9. 🔒 CHECKPOINT-1: Gate 4 — Phase 6 Exit

> Gate 4 evaluates the full project across 14 dimensions with CRG structural recon.
> score_gate >= 85.

### 9.1 Pre-Gate Preparation
- [ ] Confirm all FRs are merged to main branch
- [ ] Confirm no open critical or high issues from Gate 3

### 9.2 Run Gate 4
```bash
python3 harness_cli.py run-gate --gate 4 --phase 6
```
- Read the evaluation prompt printed above
- CRG structural recon is triggered inside run-gate automatically
- Write result to `.sessi-work/gate4_result.json`
- Failing dim: fix code → re-evaluate → re-score

### 9.3 Finalize Gate 4
```bash
python3 harness_cli.py finalize-gate --gate 4 --phase 6
```

- [ ] **[D4-BACKWARD]** D4 backward spec-coverage-check (Gate 4 threshold 90%):
  ```bash
  python3 harness_cli.py spec-coverage-check --project . --threshold 90.0
  ```
  FAIL → fix missing test implementations → re-run Gate 4 (repeat 9.2-9.3)

**Early-stop cases after finalize-gate:**
| Case | Condition | Action |
|------|-----------|--------|
| CASE 1 — PASS | score >= 85 AND critical==0 | Proceed to 9.5 |
| CASE 2 — CONTINUE | score >= 85 BUT issues remain | Fix → repeat 9.2 |
| CASE 3 — PLATEAU | 3 consecutive rounds, no new issues | Write `deferred_fixes.md` → proceed |
| CASE 4 — BLOCKED | max_rounds exhausted, not PASS | Escalate to human |

### 9.4 Generate Deliverables

#### QUALITY_REPORT.md
- Automatically generated by `finalize-gate --gate 4`
- Contains: 14-dimension score table, FR coverage summary, defect statistics
- [ASPICE] Must reference `BASELINE.md` and `VERIFICATION_REPORT.md` by filename keyword

#### RELEASE_NOTES.md
- [ ] Generate from git log since last release tag + quality_manifest.json
- Sections: Features (git log), Bug Fixes, Quality Scores (from Gate 4), Known Issues
- Output: project root `RELEASE_NOTES.md`

#### FINAL_SIGN_OFF.md
- [ ] Generate from template with all gate scores, open items list, sign-off section
- Output: project root `FINAL_SIGN_OFF.md`
- Template: `templates/FINAL_SIGN_OFF.md`

### 9.5 ASPICE Traceability Check
- [ ] **[ASPICE]** QUALITY_REPORT.md references `BASELINE.md` by filename keyword
- [ ] **[ASPICE]** QUALITY_REPORT.md references `VERIFICATION_REPORT.md` by filename keyword
- [ ] **[ASPICE]** All 14 dimensions have measurable evidence in artifact paths

### 9.6 Verify Checkpoint
- [ ] Confirm `HANDOVER.md` exists at project root
- [ ] Confirm `quality_manifest.json` records Gate 4 PASS
- [ ] Confirm git log shows gate commit
- [ ] Phase Truth >= 90% (HR-11): verified by `python3 harness_cli.py advance-phase --completed 6` (built-in check)

---

## 10. Commit Format

```
[Phase 6] Gate 4 PASS (score=S) (HASH)
[Phase 6] RELEASE_NOTES.md generated (HASH)
[Phase 6] FINAL_SIGN_OFF.md signed (HASH)
```

---

## 11. Time Estimate

| Stage | Estimate |
|-------|----------|
| Pre-execution | 10 min |
| Quality Evaluation | 60 min |
| Gate 4 Evaluation | 45 min |
| Deliverable Generation | 20 min |
| **Total** | **~2.2 hours** |

---

## 12. Phase 6 → Phase 7: Risk Management

- [ ] Confirm ALL checkpoints in this plan are ✓ (no skips — HR-03)
- [ ] Push Gate 4 git tag (SKILL.md §0.4):
  ```bash
  SCORE=$(python3 -c "import json; d=json.load(open('.sessi-work/gate4_result.json')); print(d.get('composite_score','XX'))" 2>/dev/null || echo 'XX')
  git tag -a "harness-v4-$(date +%Y%m%d)-score${SCORE}" -m "Gate 4 PASS (score ${SCORE})"
  git push origin --tags
  ```
- [ ] Generate Phase 7 plan:
  ```bash
  python3 harness_cli.py plan-phase --phase 7 --project $REPO \
    --output $REPO/.methodology/phase7_plan.md
  ```
- [ ] Advance FSM to Phase 7:
  ```bash
  python3 harness_cli.py advance-phase --completed 6 --project .
  ```
- [ ] Confirm `HANDOVER.md` reflects Phase 7 entry
- [ ] Open `phase7_plan.md` and follow from the top

---

*Generated from SKILL.md {VERSION} + P6_SOP.md {VERSION}*
