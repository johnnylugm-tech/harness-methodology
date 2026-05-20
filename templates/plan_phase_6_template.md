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
[Step 4] RECORD output | collect quality data (no A/B — Phase End Audit + Hermes APPROVE)
[Step 5] CHECK exit conditions → fail → FIX + RETRY
[Step 6] UPDATE state.json phase=7 → GOTO 1
```

**CLI Commands**:
```bash
python3 harness_cli.py run-phase --phase 6 --project .
python3 harness_cli.py run-gate --gate 4 --phase 6
python3 harness_cli.py finalize-gate --gate 4 --phase 6
python3 harness_cli.py await-hermes-approve --project . [--timeout-ms 90000]
python3 harness_cli.py generate-next-plan --phase 6
```

---

## 1. Hard Rules (HR-01 through HR-15)

| HR | Rule | Consequence | Action |
|----|------|-------------|--------|
| HR-01 | A/B must be different Agents, no self-review | Terminate -25 | **Phase 6: NOT applicable** — A/B replaced by Hermes APPROVE + Phase End Audit |
| HR-02 | Quality Gate requires actual command output | Terminate -20 | Save stdout for each QG |
| HR-03 | Phase must execute in sequence, no skipping | Terminate -30 | state.json phase=6 |
| HR-04 | HybridWorkflow mode=ON | Terminate | **Phase 6: A/B removed** — Gate 4 + Hermes APPROVE is the exit mechanism |
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
> Phase 6 exit is controlled by two mechanisms:
> 1. **Phase End Audit** — verifies QUALITY_REPORT.md exists, Gate 4 score recorded in manifest, all FRs merged.
> 2. **Hermes APPROVE** — human reviewer approves via Telegram (see §6 Hermes flow below).

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
- [ ] Verify QUALITY_REPORT.md covers all 12 dimensions and references BASELINE + VERIFICATION_REPORT

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
+-- QUALITY_REPORT.md         # Quality report (12-dimension assessment)
+-- MONITORING_PLAN.md        # Monitoring plan
```
```
Project Root/
+-- RELEASE_NOTES.md          # Release notes (auto-generated from git log)
+-- FINAL_SIGN_OFF.md         # Final sign-off document
```

### Phase 6 Deliverable Checklist

- [ ] `06-quality/QUALITY_REPORT.md` — 12-dimension quality assessment
- [ ] `06-quality/MONITORING_PLAN.md` — Monitoring plan
- [ ] `RELEASE_NOTES.md` — Release notes
- [ ] `FINAL_SIGN_OFF.md` — Final sign-off with gate scores
- [ ] Constitution score >= 80%
- [ ] Logic correctness >= 90
- [ ] Phase Truth > 90%
- [ ] `.methodology/audit_gaps_6.md` — Phase End Audit has no CRITICAL gaps (`phase_end_audit.py --phase 6`)
- [ ] [ASPICE] QUALITY_REPORT.md references BASELINE.md by filename keyword `BASELINE`
- [ ] [ASPICE] QUALITY_REPORT.md references VERIFICATION_REPORT.md by filename keyword `VERIFICATION_REPORT`
- [ ] Hermes APPROVE received from reviewer

---

## 5. Quality Evaluation Tasks (Pre-Gate Preparation)

### 5.1 Pre-Gate Checklist
- [ ] Confirm all FRs merged to main branch
- [ ] Confirm no open critical or high issues from previous gate
- [ ] Confirm `HERMES_REVIEWER_TARGET` env var is set (e.g. `telegram:6308981865`)
- [ ] Confirm `HERMES_TIMEOUT_MS=90000` is set in `.env` or shell environment

### 5.2 Quality Dimensions (Gate 4 — 14 dimensions)

| Dim | Dimension | Weight | Evaluation Command | Target |
|-----|-----------|--------|-------------------|--------|
| 1 | **Completeness** | 10% | FR checklist vs SRS acceptance criteria | 100% FRs covered |
| 2 | **Correctness** | 10% | `pytest` pass rate | 100% pass |
| 3 | **Consistency** | 8% | SAD → code alignment (`check-trace`) | >= 90% |
| 4 | **Clarity** | 8% | Docstring/citation audit | All public fns documented |
| 5 | **Test Coverage** | 10% | `pytest --cov=app/` | >= 80% |
| 6 | **Maintainability** | 8% | `ruff check` | 0 errors |
| 7 | **Reliability** | 10% | Integration test pass rate | >= 90% |
| 8 | **Performance** | 8% | BASELINE.md regression check | No regression |
| 9 | **Security** | 8% | Bandit / safety scan | 0 HIGH issues |
| 10 | **Traceability** | 8% | FR→SRS→SAD→code→test chain | 100% traceable |
| 11 | **Integrity** | 6% | Constitution score | >= 80% |
| 12 | **Phase Truth** | 6% | PhaseTruthVerifier | >= 90% |

### 5.3 Task Table

| Task | Owner | Input | Output |
|------|-------|-------|--------|
| Quality data collection | Agent A (qa) | TEST_RESULTS.md, BASELINE.md | Quality data |
| Constitution check | Agent A (qa) | All Phase outputs | Check report |
| Logic correctness verification | Agent B (architect) | Code, TEST_RESULTS | Verification report |
| QUALITY_REPORT writing | Agent A (qa) | All check results | QUALITY_REPORT.md |

---

## 6. Agent Prompt Templates

### Agent A (qa)

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
- Logic correctness score >= 90
- All HIGH priority issues resolved or risk accepted
- MAX 5 iteration rounds (HR-12)

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

### Agent B (architect)

```
TASK: Review QUALITY_REPORT
TASK_ID: task-p6-review

REVIEW SCOPE (read only necessary sections):
- 06-quality/QUALITY_REPORT.md
- 04-testing/TEST_RESULTS.md
- 05-verification/BASELINE.md

VERIFICATION CHECKLIST:
1. Constitution quality total >= 80%
2. Logic correctness score >= 90
3. HIGH priority issues resolved or risk accepted
4. Quality trend reasonable vs Baseline
5. Release recommendation clear

REJECT_IF:
- Constitution < 80% → REJECT
- HIGH priority issues unresolved → REJECT
- Data not matching reality → REJECT
- Missing citations or no line numbers → REJECT (HR-15)

OUTPUT_FORMAT:
{
  "status": "APPROVE|REJECT",
  "confidence": 1-10,
  "violations": ["specific issue"],
  "quality_score": "Constitution score",
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

## 8. .methodology/sessions_spawn.log Format (HR-10)

2 records per Phase (qa + architect):

```json
{"timestamp": "ISO8601", "role": "qa", "task": "generate QUALITY_REPORT", "session_id": "xxx"}
{"timestamp": "ISO8601", "role": "architect", "task": "review QUALITY_REPORT", "session_id": "yyy"}
```

---

## 9. 🔒 CHECKPOINT-1: Gate 4 — Phase 6 Exit

> Gate 4 evaluates the full project across 14 dimensions with CRG structural recon.
> score_gate >= 85, Hermes APPROVE required.

### 9.1 Pre-Gate Preparation
- [ ] Confirm all FRs are merged to main branch
- [ ] Confirm no open critical or high issues from Gate 3
- [ ] Confirm `HERMES_REVIEWER_TARGET` env var is set (e.g. `telegram:6308981865`)
- [ ] Confirm `HERMES_TIMEOUT_MS=90000` is set

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

### 9.4 Hermes Approval Gate
> ⛔ Gate 4 requires external APPROVE from Hermes reviewer before finalizing.

```
┌──────────────────────────────────────────────────────────────┐
│  Hermes Approval Flow (async, timeout=90000ms)              │
│                                                              │
│  1. SEND   → mcp__hermes__messages_send                      │
│               Manifest: QUALITY_REPORT.md summary +          │
│               request for APPROVE/REJECT with reason          │
│                                                              │
│  2. WAIT   → mcp__hermes__events_wait(timeout_ms: 90000)     │
│               HIT  → parse response                          │
│               MISS → step 3 (cold-read)                      │
│                                                              │
│  3. READ   → mcp__hermes__messages_read                      │
│               Extract latest message + next_cursor           │
│                                                              │
│  4. PARSE  → LLM: APPROVE → proceed; REJECT → fix;          │
│               info → record + continue                       │
│                                                              │
│  5. RETRY  → max 3 rounds. If still no APPROVE → CASE 4     │
└──────────────────────────────────────────────────────────────┘
```

- [ ] **[HERMES-1]** Send Gate 4 result summary to reviewer via Hermes:
  ```python
  # Include: gate score, dim-by-dim breakdown, critical issues list
  mcp__hermes__messages_send(target=os.environ["HERMES_REVIEWER_TARGET"],
    message=f"Gate 4 assessment complete. Score: {score:.1f}/100. Requesting APPROVE.")
  ```
- [ ] **[HERMES-2]** Wait for response (events_wait with 90000ms timeout)
- [ ] **[HERMES-3]** Parse response → APPROVE or REJECT
- [ ] **[HERMES-4]** If APPROVE: proceed. If REJECT: fix issues → re-run Gate 4
- [ ] **[HERMES-5]** If 3 rounds exhausted without APPROVE → CASE 4 BLOCKED

### 9.5 Generate Deliverables

#### QUALITY_REPORT.md
- Automatically generated by `finalize-gate --gate 4`
- Contains: 12-dimension score table, FR coverage summary, defect statistics
- [ASPICE] Must reference `BASELINE.md` and `VERIFICATION_REPORT.md` by filename keyword

#### RELEASE_NOTES.md
- [ ] Generate from git log since last release tag + quality_manifest.json
- Sections: Features (git log), Bug Fixes, Quality Scores (from Gate 4), Known Issues
- Output: project root `RELEASE_NOTES.md`

#### FINAL_SIGN_OFF.md
- [ ] Generate from template with all gate scores, open items list, sign-off section
- Output: project root `FINAL_SIGN_OFF.md`
- Template: `templates/FINAL_SIGN_OFF.md`

### 9.6 ASPICE Traceability Check
- [ ] **[ASPICE]** QUALITY_REPORT.md references `BASELINE.md` by filename keyword
- [ ] **[ASPICE]** QUALITY_REPORT.md references `VERIFICATION_REPORT.md` by filename keyword
- [ ] **[ASPICE]** All 14 dimensions have measurable evidence in artifact paths

### 9.7 Verify Checkpoint
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
| Quality Evaluation (A/B) | 60 min |
| Gate 4 Evaluation | 45 min |
| Hermes Approval | 15 min (wait time) |
| Deliverable Generation | 20 min |
| **Total** | **~2.5 hours** |

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
