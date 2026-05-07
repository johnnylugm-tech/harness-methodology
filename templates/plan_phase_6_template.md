# Phase 6 Execution Plan - {PROJECT_NAME}

> **Version**: {VERSION}
> **Project**: {PROJECT_NAME}
> **Date**: {DATE}
> **Framework**: harness-methodology {VERSION}

---

## 0. Execution Protocol (SS 0)

```
[Step 0] READ state.json -> current_phase=6
[Step 1] LOAD SKILL.md SS4 Phase routing
[Step 2] CHECK entry conditions -> blocker -> STOP
[Step 3] EXECUTE SOP -> LAZY LOAD docs/P6_SOP.md
[Step 4] RECORD output | SPAWN A/B agent
[Step 5] CHECK exit conditions -> fail -> FIX + RETRY
[Step 6] UPDATE state.json phase=7 -> GOTO 1
```

**CLI Commands**:
```bash
python3 harness_cli.py run-phase --phase 6 --project .
python3 harness_cli.py push-checkpoint --phase 6
python3 harness_cli.py run-gate --gate 4 --phase 6
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
| HR-10 | sessions_spawn.log must have A/B records | Terminate -15 | 2 records per step |
| HR-11 | Phase Truth < 70% blocks next Phase | Terminate | <70% -> PAUSE |
| HR-12 | A/B review > 5 rounds -> PAUSE | - | stop at 5 rounds |
| HR-13 | Phase elapsed > estimated x3 -> PAUSE | - | record start_time |
| HR-14 | Integrity < 40 -> FREEZE | - | check Integrity post-QG |
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
| **Agent A** | `qa` | Quality data collection, QUALITY_REPORT writing |
| **Agent B** | `architect` | Quality confirmation, review verification |

---

## 3. Prior Phase Artifacts

{artifacts_summary}

> Verify all prior phase artifacts exist and are complete before execution.

---

## 4. Output Tree

```
06-quality/
+-- QUALITY_REPORT.md      # Quality report (primary output)
+-- MONITORING_PLAN.md     # Monitoring plan
```

### Phase 6 Deliverable Checklist

- [ ] `06-quality/QUALITY_REPORT.md` - Quality dimension assessment
- [ ] `06-quality/MONITORING_PLAN.md` - Monitoring plan
- [ ] Constitution score >= 80%
- [ ] Logic correctness >= 90
- [ ] Phase Truth >90%
- [ ] `sessions_spawn.log` - complete A/B session records

---

## 5. Quality Evaluation Tasks (4 total)

### 5.1 Quality Dimensions

| Dimension | Metric | Target | Verification |
|-----------|--------|--------|-------------|
| Maintainability | Constitution score | >= 80% | constitution runner |
| Logic Correctness | Logic correctness score | >= 90 | phase-verify |
| Test Coverage | Coverage | >= 80% | pytest --cov |
| Phase Truth | Phase Truth score | >90% | phase-verify |

### 5.2 Task Table

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
- 05-verify/BASELINE.md (performance baseline data)
- 06-quality/QUALITY_REPORT.md (existing version if any)

OUTPUT:
- 06-quality/QUALITY_REPORT.md
- Issue fix plan (if issues found)

PASS CRITERIA:
- Constitution quality total >= 80%
- Logic correctness score >= 90
- All HIGH priority issues resolved or risk accepted

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
- 05-verify/BASELINE.md

VERIFICATION CHECKLIST:
1. Constitution quality total >= 80%
2. Logic correctness score >= 90
3. HIGH priority issues resolved or risk accepted
4. Quality trend reasonable vs Baseline
5. Release recommendation clear

REJECT_IF:
- Constitution < 80% -> REJECT
- HIGH priority issues unresolved -> REJECT
- Data not matching reality -> REJECT
- Missing citations or no line numbers -> REJECT (HR-15)

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

## 8. sessions_spawn.log Format (HR-10)

2 records per Phase (qa + architect):

```json
{"timestamp": "ISO8601", "role": "qa", "task": "generate QUALITY_REPORT", "session_id": "xxx"}
{"timestamp": "ISO8601", "role": "architect", "task": "review QUALITY_REPORT", "session_id": "yyy"}
```

---

## 9. Commit Format

```
[Phase 6] QUALITY_REPORT established (HASH)
```

---

## 10. Time Estimate

| Stage | Estimate |
|-------|----------|
| Pre-execution | 10 min |
| Quality Evaluation | 60 min |
| **Total** | **~1 hour** |

---

*Generated from SKILL.md {VERSION} + P6_SOP.md {VERSION}*
