# Phase 3 — Code Implementation (P3 SOP)
<!-- Role A: Developer | Role B: Reviewer (Hermes) -->
<!-- Input: SAD.md + quality_manifest.json -->
<!-- Output: implementation/ + TEST_PLAN.md + Gate 1/2 results -->

## Step 0 — TDD Scaffolding (在任何 implementation 之前)

> **強制規則**: 在任何實作程式碼產出前，每個 FR 必須先有對應的 failing test。

### 0.1 從 SAD.md 提取 FR 清單
```python
# 解析 SAD.md → 抽出所有 FR-XXX ID + acceptance criteria
fr_list = parse_fr_ids("SAD.md")  # e.g. ["FR-001", "FR-002", "FR-003"]
```

### 0.2 為每個 FR 建立 failing test stub

For each FR, create `tests/test_fr_<id>.py`:
```python
# tests/test_fr_001.py — [FR-001: <FR title from SAD.md>]
import pytest

@pytest.mark.tdd
def test_fr_001_happy_path():
    """AC: <acceptance criteria from SAD.md>"""
    raise NotImplementedError("TDD: implement after FR-001 is coded")

@pytest.mark.tdd
def test_fr_001_edge_case():
    """AC: <edge case from SAD.md>"""
    raise NotImplementedError("TDD: implement after FR-001 is coded")
```

### 0.3 生成 TEST_PLAN.md (P4 Input artifact)

| FR ID | Test File | Test Cases | Acceptance Criteria | TDD Status |
|-------|-----------|------------|---------------------|------------|
| FR-001 | test_fr_001.py | happy_path, edge_case | `<AC from SAD.md>` | 🔴 RED |

### 0.4 Commit RED state

```bash
git add tests/test_fr_*.py TEST_PLAN.md
git commit -m "test: TDD stubs [RED] for all FRs + TEST_PLAN.md"
```

> SPEC trace 驗證: `python3 scripts/check_spec_trace.py SAD.md tests/`
> 應回傳 Exit 0 (所有 FRs 有對應測試檔案)。

---

## Step 1 — Implementation (RED → GREEN, 逐 FR)

每個 FR 按 quality_manifest.json priority 順序進行：
1. 實作 FR 功能
2. 將 `raise NotImplementedError` 替換為真實 assertions
3. `pytest tests/test_fr_<id>.py -v` → 必須 ✅ pass
4. 執行 Gate 1 for this FR
5. `git commit -m "feat(FR-001): implement + GREEN tests"`

> **規則**: 當前 FR tests 仍為 RED 時，不得跳至下一個 FR。

---

## Gate 1 — Per-FR (replaces check_fr_full Layer 3)

```bash
python harness_cli.py run-gate --gate 1 --phase 3 --fr-id FR-001
# 3 dims: linting(90) / type_safety(85) / test_coverage(80)
# test_coverage(80) 語義更新: TDD tests 需覆蓋此 FR 的 AC，非僅 line coverage
# Blocking: any dim < threshold → auto-fix (up to --auto-fix-rounds) → re-run gate
# Use --no-auto-fix to disable automatic repair
```

> **SPEC alignment**: Gate 1 的 `test_coverage` 確認 TDD tests 覆蓋 FR 的
> stated acceptance criteria，非僅統計程式行覆蓋率。

---

## Gate 2 — Phase Exit (replaces auto-research P3)

```bash
python harness_cli.py run-gate --gate 2 --phase 3
# 7 dims, score_gate=75, max_rounds=3, early_stop=true
# 額外 check: 所有 test_fr_*.py 存在且為 GREEN state
# Blocking: score < 75 OR any FR still RED -> issue-driven plan -> iterate
```

---

## P3 Exit Checklist

- [ ] `TEST_PLAN.md` 已生成並 commit
- [ ] 每個 SAD.md 中的 FR 皆有 `tests/test_fr_*.py`
- [ ] `check_spec_trace.py SAD.md tests/` 回傳 Exit 0
- [ ] 所有 TDD tests 為 GREEN state
- [ ] 每個 FR 的 Gate 1 通過
- [ ] Gate 2 通過 (score ≥ 75)
