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
[Step 4] RECORD output | SPAWN A/B agent
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
| HR-01 | A/B must be different Agents, no self-review | Terminate -25 | QA spawn then Architect spawn (strict order) |
| HR-02 | Quality Gate requires actual command output | Terminate -20 | Save stdout for each QG |
| HR-03 | Phase must execute in sequence, no skipping | Terminate -30 | state.json phase=6 |
| HR-04 | HybridWorkflow mode=ON, enforce A/B | Terminate | prompt contains mode=ON |
| HR-05 | On conflict, harness-methodology wins | Log | disputes resolved by harness-methodology |
| HR-06 | No frameworks outside spec | Terminate -20 | forbidden list |
| HR-07 | DEVELOPMENT_LOG must record session_id | -15 | record session_id per entry |
| HR-08 | Phase end must run Quality Gate | Terminate -10 | stage-pass --phase 6 |
| HR-09 | Claims Verifier must pass | Terminate -20 | citations match |
| HR-10 | .methodology/sessions_spawn.log must have A/B records | Terminate -15 | 2 records per step |
| HR-11 | Phase Truth < 90% blocks next Phase | Terminate | <90% → PAUSE |
| HR-12 | A/B review > 5 rounds → PAUSE | - | stop at 5 rounds |
| HR-13 | Phase elapsed > estimated x3 → PAUSE | - | record start_time |
| HR-14 | Integrity < 40 → FREEZE | - | check Integrity post-QG |
| HR-15 | citations must include line numbers + artifact_verification | -15 | no citations = task failed |

---

## 2. A/B Collaboration (HR-01, HR-04)

### On Demand / Need to Know

| Principle | Definition |
|-----------|------------|
| **Need to Know** | Only provide essential info; details only when asked |
| **On Demand** | Sub-agent reads artifact paths directly, no dumping |
| **Single Responsibility** | Each Sub-agent handles one task only |

### HR Constraints (Phase 6)
HR-01 | HR-07 | HR-08 | HR-10

### TH Thresholds (Phase 6)

| TH | Metric | Threshold | Verification |
|----|--------|-----------|-------------|
| TH-02 | Constitution total score | >=80% | `core/quality_gate/constitution/runner.py --type all` |
| TH-07 | Logic correctness | >=90 | `phase-verify` |
| TH-15 | Phase Truth | >90% | `phase-verify` |

### A/B Roles (Phase 6)

| Role | Agent | Responsibility |
|------|-------|----------------|
| **Agent A** | `QA_ENGINEER` | Quality data collection, QUALITY_REPORT writing |
| **Agent B** | `ARCHITECT` | Quality confirmation, review verification |

### A/B Protocol Execution

- [ ] **[A-1]** Agent A (qa): Quality data collection → generate QUALITY_REPORT.md
  - Docstrings: `[Phase 6]` tag + `Citations:` with line numbers (HR-15)
- [ ] **[A-2]** Agent A returns `{status, result, confidence, citations, summary}`
- [ ] **[A-DISPATCH]** Dispatch Agent A:
  ```bash
  python3 harness_cli.py dispatch --role qa --fr-id Gate4 \
    --prompt "Generate QUALITY_REPORT.md from TEST_RESULTS, BASELINE, VERIFICATION_REPORT" --phase 6 --project $REPO
  ```
- [ ] **[B-1]** Agent B (architect): Review QUALITY_REPORT — check constitution score, logic correctness, release recommendation
- [ ] **[B-2]** Agent B returns JSON — parse `review_status`:
  - `APPROVE` → proceed to Gate 4
  - `REJECT` → Agent A fixes gaps → re-dispatch B. Max 5 rounds (HR-12).
- [ ] **[B-DISPATCH]** Dispatch Agent B:
  ```bash
  python3 harness_cli.py dispatch --role reviewer --fr-id Gate4 \
    --prompt "Review QUALITY_REPORT against quality criteria" --phase 6 --project $REPO
  ```
  > AgentSpawner auto-logs to `.methodology/sessions_spawn.log` on dispatch (HR-10).

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
- [ ] `.methodology/sessions_spawn.log` — complete A/B session records
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

### 5.2 Quality Dimensions (Gate 4 — 12 dimensions)

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

> Gate 4 evaluates the full project across 12 dimensions with CRG structural recon.
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
- [ ] **[ASPICE]** All 12 dimensions have measurable evidence in artifact paths

### 9.7 Verify Checkpoint
- [ ] Confirm `HANDOVER.md` exists at project root
- [ ] Confirm `quality_manifest.json` records Gate 4 PASS
- [ ] Confirm git log shows gate commit
- [ ] Phase Truth >= 90% (HR-11): run `python3 harness_cli.py run-pipeline --phase-from 6`

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
