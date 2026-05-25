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

### 0.1a 驗證 FR→Test 檔案對應（Test Compliance Check）

Gate 前自動驗證（`cmd_finalize_gate` 內建 hook）：
- **I-1 D4**: Gate 1+ 檢查 TEST_SPEC.md 涵蓋率（Gate 1≥40%, Gate 2≥60%, Gate 3≥80%, Gate 4≥90%）
- **I-2**: 每個 FR 必須有對應的 `tests/test_fr_{id}.py` 才算完整（FR→test file check）
- **I-3**: 測試檔案 commit 必須早於實作程式碼（RED-first ordering）

```bash
python harness_cli.py spec-coverage-check --project . --threshold 60
# 返回：各 FR 的 TEST_SPEC.md test case 函式覆蓋狀態
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
# 10 dims (Tier 1+2), score_gate=75, max_rounds=3, early_stop=true
# 新增維度: integration_coverage (0.10), test_assertion_quality (0.06)
# mutation_testing: objective_primary=true (tool_score 優先於 llm_score)
# 額外 check: 所有 test_fr_*.py 存在且為 GREEN state
# D4 pre-check: spec-coverage ≥ 60% (unified v2.6)
# 額外 check: spec-coverage-check 無 FAIL
# Blocking: score < 75 OR any FR still RED -> issue-driven plan -> iterate
```

---

## P3 Exit Checklist

- [ ] `TEST_PLAN.md` 已生成並 commit
- [ ] 每個 SAD.md 中的 FR 皆有 `tests/test_fr_*.py`
- [ ] `check_spec_trace.py SAD.md tests/` 回傳 Exit 0
- [ ] 所有 TDD tests 為 GREEN state
- [ ] 每個 FR 的 Gate 1 通過
- [ ] `spec-coverage-check --threshold 60` 驗證通過（FR→test 對應，TEST_SPEC.md 單一來源 v2.6）
- [ ] Gate 2 通過 (score ≥ 75)

---

## Agent A Dispatch Template (P3 — per FR)

Orchestrator: copy this when spawning Agent A for a specific FR.

```
[TASK]
Phase: 3 — Implementation | FR-ID: {fr_id} | TDD step: {RED|GREEN|REFACTOR}

SRS requirement:
> {paste FR-XX section from docs/SRS.md — embed, not file path}

SAD constraint:
> {paste relevant module spec + dependency constraints from docs/SAD.md — embed}

TDD contract:
- RED:   write failing test in tests/test_fr_{id}.py
- GREEN: implement until test passes
- REFACTOR: clean up without breaking tests

Expected output:
- tests/test_fr_{id}.py (pytest, 1+ test functions)
- implementation file at {module_path}
- pytest tests/test_fr_{id}.py -v → ALL PASS
- JSON: {"status": "success", "files": [...], "confidence": N,
         "citations": [{"file": "...", "line": N, "content": "..."}],
         "summary": "..."}
```

## Agent B Dispatch Template (P3 — per FR)

Orchestrator: copy this when spawning Agent B for a specific FR.

```
[TASK]
Phase: 3 — Implementation | FR-ID: {fr_id} | Role: REVIEWER

SRS requirement:
> {paste FR-XX section from docs/SRS.md — embed, not file path}

Code to review (Agent A output):
> {paste all files Agent A produced — embed, not file path.
   Agent B is STATELESS (§0.5). NEVER pass file paths to Agent B.}

SAD constraint:
> {paste relevant module spec from docs/SAD.md — embed}

Review criteria:
1. Does implementation satisfy FR acceptance criteria? (SRS)
2. Does implementation follow module design? (SAD)
3. Are TDD tests meaningful (not just assert True)?
4. Code style: SOLID, no dead code, no magic numbers?
5. Citations: do line numbers in Agent A's citations match actual code?

Expected output:
- JSON: {"status": "success", "review_status": "APPROVE|REJECT",
         "confidence": N, "violations": [...], "citations": [...],
         "summary": "..."}
```
