# Phase 4 — Testing (P4 SOP)
<!-- Role A: QA Engineer | Role B: Reviewer (Hermes) -->
<!-- Input: implementation/ + TEST_PLAN.md -->
<!-- Output: TEST_RESULTS.md + Gate 3 result -->

## P4 前提

P3 TDD stubs 已全數 GREEN。P4 在此基礎上擴展：
- Integration tests (FR 間交互)
- Regression tests (非 happy path / boundary conditions)
- Performance / load tests (per quality_manifest.json targets)

---

## Step 1 — Integration Test 擴展

針對 SAD.md 中有互動關係的 FR pair，建立整合測試：

```python
# tests/test_integration_fr001_fr002.py
import pytest

@pytest.mark.integration
def test_fr001_output_feeds_fr002():
    """Integration: FR-001 output → FR-002 input contract"""
    ...
```

---

## Step 2 — TEST_RESULTS.md 生成

```markdown
# TEST_RESULTS.md
| FR ID | Unit Tests | Integration Tests | Coverage | Status |
|-------|-----------|------------------|----------|--------|
| FR-001 | 3 PASS | 1 PASS | 87% | ✅ |
| FR-002 | 2 PASS | 1 PASS | 82% | ✅ |
| **TOTAL** | N PASS | M PASS | X% | ✅/❌ |
```

---

## Step 3 — SPEC Trace 驗證

SAD.md 每個 FR 必須對應到 TEST_RESULTS.md 的一行。自動化驗證：

```bash
python3 scripts/check_spec_trace.py SAD.md tests/
# Exit 0: all FRs traced (Gate 3 可繼續)
# Exit 1: untested FRs found (Gate 3 blocked — 必須先補齊 test_fr_XXX.py)
```

---

## Step 4 — Gate 1 (per-FR)

```bash
python harness_cli.py run-gate --gate 1 --phase 4 --fr-id FR-001
# Blocking: any dim < threshold → auto-fix → re-run gate
```

---

## Step 5 — Gate 3 — Phase Exit (replaces auto-research P4)

```bash
python harness_cli.py run-gate --gate 3 --phase 4
# 15 dims (all tiers), score_gate=80, max_rounds=3
# 新增維度: integration_coverage (0.05), test_assertion_quality (0.02)
# mutation_testing: objective_primary=true (tool_score 優先於 llm_score)
# CRG: reconnaissance + tier3_guidance + impact_check + drift_check
# D4 pre-check: spec-coverage ≥ 80% (Gate 4: ≥ 90%) (unified v2.6)
# 新增 pre-gate check: spec_trace_coverage = 100%
#   → scripts/check_spec_trace.py 回傳 Exit 1 時直接 raise GateBlockedError，不進入 SSI runner
# Blocking: score < 80 OR spec_trace_coverage < 100%
```

---

## P4 Exit Checklist

- [ ] `TEST_RESULTS.md` 已生成
- [ ] `scripts/check_spec_trace.py SAD.md tests/` 回傳 Exit 0 (100% FRs traced)
- [ ] Integration tests 已涵蓋 FR 互動關係
- [ ] `spec-coverage-check --threshold 80 --strict` 通過（所有 FR 有 test 對應，TEST_SPEC.md 單一來源 v2.6）
- [ ] Gate 3 通過 (score ≥ 80 AND spec_trace = 100%)

---

## Agent A Dispatch Template (P4 — per FR)

Orchestrator: copy this when spawning Agent A for a specific FR.

```
[TASK]
Phase: 4 — Testing | FR-ID: {fr_id} | Role: QA_ENGINEER

SRS requirement:
> {paste FR-XX section from docs/SRS.md — embed, not file path}

TEST_PLAN entry for this FR:
> {paste relevant row from TEST_PLAN.md — embed}

Implementation to test:
> {paste the FR's implementation code — embed, not file path}

Test types to execute:
1. Unit: verify function-level correctness per acceptance criteria
2. Integration: verify FR interacts correctly with dependent FRs
3. Regression: verify edge cases and boundary conditions
4. (if NFR-performance) Load: verify response time / throughput

Expected output:
- test results (PASS/FAIL per test case)
- coverage report (≥ 80%)
- updated TEST_RESULTS.md row for this FR
- JSON: {"status": "success", "files": ["TEST_RESULTS.md"],
         "confidence": N, "test_count": N, "pass_count": N,
         "coverage": N, "citations": [...], "summary": "..."}
```

## Agent B Dispatch Template (P4 — per FR)

Orchestrator: copy this when spawning Agent B for a specific FR.

```
[TASK]
Phase: 4 — Testing | FR-ID: {fr_id} | Role: ARCHITECT (reviewer)

SRS requirement:
> {paste FR-XX section from docs/SRS.md — embed, not file path}

SAD constraint:
> {paste relevant module spec from docs/SAD.md — embed}

Agent A test output:
> {paste full test results + TEST_RESULTS.md row — embed, not file path}

Review criteria:
1. Do tests cover ALL acceptance criteria for this FR? (SRS trace)
2. Do integration tests cover FR interaction contracts? (SAD)
3. Are edge cases tested, not just happy path?
4. Coverage ≥ 80%? If not, are gaps documented?
5. Are test assertions meaningful (verifying behavior, not placeholders)?

Expected output:
- JSON: {"status": "success", "review_status": "APPROVE|REJECT",
         "confidence": N, "violations": [...], "citations": [...],
         "summary": "..."}
```
