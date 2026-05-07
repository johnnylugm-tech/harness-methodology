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
python3 harness/scripts/scripts/check_spec_trace.py SAD.md tests/
# Exit 0: all FRs traced (Gate 3 可繼續)
# Exit 1: untested FRs found (Gate 3 blocked — 必須先補齊 test_fr_XXX.py)
```

---

## Gate 3 — Phase Exit (replaces auto-research P4)

```bash
python harness_cli.py run-gate --gate 3 --phase 4
# 12 dims (all tiers), score_gate=80, max_rounds=3
# CRG: reconnaissance + tier3_guidance + impact_check + drift_check
# 新增 pre-gate check: spec_trace_coverage = 100%
#   → scripts/check_spec_trace.py 回傳 Exit 1 時直接 raise GateBlockedError，不進入 SSI runner
# Blocking: score < 80 OR spec_trace_coverage < 100%
```

---

## P4 Exit Checklist

- [ ] `TEST_RESULTS.md` 已生成
- [ ] `scripts/check_spec_trace.py SAD.md tests/` 回傳 Exit 0 (100% FRs traced)
- [ ] Integration tests 已涵蓋 FR 互動關係
- [ ] Gate 3 通過 (score ≥ 80 AND spec_trace = 100%)
