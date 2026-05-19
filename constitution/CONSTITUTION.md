# Constitution — 團隊憲章

> 本文件定義團隊的不可變原則。所有專案必須遵守這些規則。
> 此為編譯後的 artifact，不可手動變更。版本哈希用於驗證完整性。
> schema_version: 2.3

---

## 1. 核心價值觀

### 1.1 品質優先

```
品質 > 速度 > 功能
```

- 任何交付物必須通過對應 Quality Gate 才能進入下一 Phase
- 測試覆蓋率必須 >= 70%（Phase 3）, >= 80%（Phase 4+）
- 安全掃描必須通過（Phase 3+）

### 1.2 可維護性

```
代碼是給人看的，其次是給機器跑的
```

- 所有公開 API 必須有 docstring
- 函數長度 <= 50 行
- Cyclomatic complexity <= 10

### 1.3 可追溯性

```
每個需求都可以追溯到實作，每個實作都可以追溯到驗證
```

- 所有任務必須有 ID (`[TASK-XXX]` 或 `[FR-XX]`)
- 所有變更必須有 conventional commit message
- 所有驗證必須有記錄 (sessions_spawn.log)

---

## 2. Quality Gate 閾值（權威來源）

### 2.1 Per-Gate 最低分數

| Gate | Phase | 最低分數 | 維度數 | 說明 |
|------|-------|---------|--------|------|
| Gate 1 | P3/P4/P5/P7/P8 (per FR) | **≥ 75** | 3 (lint/type/cov) | FR 級別檢查 |
| Gate 2 | P3 exit | **≥ 75** | 9 | Phase 級別 SSI |
| Gate 3 | P4 exit | **≥ 80** | 14 | 完整 CRG recon |
| Gate 4 | P6 exit | **≥ 85** | 14 | 全專案 + Hermes APPROVE |

### 2.2 品質維度權重

| 維度 | Gate 1 | Gate 2 | Gate 3 | Gate 4 |
|------|--------|--------|--------|--------|
| D1_Linting | 33% | 15% | 10% | 10% |
| D2_TypeSafety | 33% | 15% | 10% | 10% |
| D3_Coverage | 34% | 15% | 10% | 10% |
| D4_TestInventory | — | 5% | 5% | 5% |
| D5_Security | — | 10% | 10% | 10% |
| D6_Performance | — | 10% | 10% | 10% |
| D7_Maintainability | — | 10% | 10% | 10% |
| D8_Documentation | — | 10% | 10% | 5% |
| D9_Architecture | — | — | 5% | 5% |
| D10_Testing | — | — | 5% | 5% |
| D11_Traceability | — | — | 5% | 5% |
| D12_Compliance | — | — | 5% | 5% |
| D13_Constitution | — | 10% | 5% | 5% |

### 2.3 Entry Gate 前提條件

每個 Phase 進入前必須驗證前置條件：

| Phase | Entry Gate | 驗證方式 |
|-------|-----------|----------|
| P1 | None | 無前置 Phase |
| P2 | Agent B¹ (P1) | `git log` 確認 P1 APPROVE |
| P3 | Agent B¹ (P2) | `git log` 確認 P2 APPROVE |
| P4 | Gate 2 (P3) | `git log` 確認 P3 Gate 2 PASS |
| P5 | Gate 3 (P4) | `git log` 確認 P4 Gate 3 PASS |
| P6 | Gate 3 (P5) | `git log` 確認 P5 Phase Truth PASS |
| P7 | Gate 4 (P6) | `git log` 確認 P6 Gate 4 PASS |
| P8 | Gate 4 (P6) | `git log` 確認 P6 Gate 4 PASS |

> ¹ **Agent B¹** = Agent B peer review of deliverables (P1/P2 produce documents, not code).

### 2.4 Constitution Score 門檻

| Phase Range | 適用 TH | 維度 | 門檻 |
|-------------|---------|------|------|
| P1 | TH-03, TH-04 | correctness + security | =100% (FrameworkEnforcer BLOCK) |
| P2 | TH-03, TH-04, TH-05 | correctness + security + maintainability | correctness=100%, security=100%, maintainability>90% |
| P3 | TH-03, TH-04, TH-05 | correctness + security + maintainability | correctness=100%, security=100%, maintainability>90% |
| P4 | TH-03, TH-04, TH-05, TH-06 | correctness + security + maintainability + coverage | =100% / >90% / >90% (FrameworkEnforcer BLOCK) |
| P5–P8 | TH-02 | constitution 綜合 | ≥80% (FrameworkEnforcer BLOCK + Constitution) |

### 2.5 Threshold Rules (TH-01 ~ TH-17)

全專案品質閾值權威來源，對齊 methodology-v2 v9.1。

| ID | Metric | Threshold | Applicable Phases | Verify Method |
|----|--------|-----------|-------------------|---------------|
| TH-01 | ASPICE Compliance Rate | >80% | 1–8 | `trace-check` |
| TH-02 | Constitution Total Score | ≥80% | 5–8 | `run-gate` D12 |
| TH-03 | Constitution — Correctness | =100% | 1–4 | `run-constitution` |
| TH-04 | Constitution — Security | =100% | 1–4 | `run-constitution` |
| TH-05 | Constitution — Maintainability | >90% | 2–4 | `run-constitution` |
| TH-06 | Constitution — Test Coverage | >90% | 4 | `run-constitution` |
| TH-07 | Logic Correctness Score | ≥90 | 5–8 | `phase-verify` |
| TH-08 | AgentEvaluator Standard | ≥80 | 1–2 | `evaluate` |
| TH-09 | AgentEvaluator Strict | ≥90 | 3–8 | `evaluate --strict` |
| TH-10 | Test Pass Rate | =100% | 3–8 | `pytest` |
| TH-11 | Unit Test Coverage | ≥70% | 3 | `coverage` |
| TH-12 | Unit Test Coverage | ≥80% | 4–8 | `coverage` |
| TH-13 | SRS FR Coverage | =100% | 4–8 | `trace-check` |
| TH-14 | Specification Completeness | =100% | 1 | `verify-spec` |
| TH-15 | Phase Truth | >90% | 1–8 | `phase-verify` |
| TH-16 | Code-to-SAD Mapping Rate | =100% | 3 | `trace-check` |
| TH-17 | FR-to-Test Mapping Rate | ≥90% | 4 | `trace-check` |

> **Phase → TH mapping**:
> P1: TH-01, TH-03, TH-04, TH-08, TH-14, TH-15
> P2: TH-01, TH-03, TH-04, TH-05, TH-08, TH-15
> P3: TH-03, TH-04, TH-05, TH-08, TH-09, TH-10, TH-11, TH-15, TH-16
> P4: TH-01, TH-03, TH-04, TH-05, TH-06, TH-10, TH-12, TH-13, TH-15, TH-17
> P5: TH-02, TH-07, TH-15
> P6: TH-02, TH-07, TH-15
> P7: TH-07, TH-15
> P8: TH-02, TH-15

---

## 3. 審批規則

| 變更類型 | 審批者 | 必須通過 |
|----------|--------|----------|
| Phase 1-2 交付物 | Agent B¹ | Agent B APPROVE |
| Phase 3-8 程式碼 | Agent B (Reviewer) | Gate check ≥ threshold |
| Gate 4 全專案 | Hermes APPROVE | 120s timeout + cold-read fallback |
| 緊急修復 | Human (Johnny) | Minimum Gate 1 check |

---

## 4. 命名規範

### 4.1 代碼

| 類型 | 規則 | 範例 |
|------|------|------|
| 變數 | snake_case | `user_name` |
| 函數 | snake_case | `get_user()` |
| 類別 | PascalCase | `UserService` |
| 常量 | UPPER_SNAKE | `MAX_RETRY` |

### 4.2 Git

| 類型 | 格式 | 範例 |
|------|------|------|
| Feature | `feat(P3): description` | `feat(P3): add user auth` |
| Fix | `fix(P4): description` | `fix(P4): resolve login bug` |
| Docs | `docs(P1): description` | `docs(P1): SRS.md v1` |

---

## 5. HR 規則（違反即終止）

| ID | 規則 | 後果 |
|----|------|------|
| HR-01 | A/B 不同 Agent，禁自寫自審 | 終止 -25 |
| HR-02 | Quality Gate 需實際命令輸出 | 終止 -20 |
| HR-03 | Phase 順序執行，不可跳過 | 終止 -30 |
| HR-04 | HybridWorkflow mode=ON，強制 A/B | 終止 |
| HR-07 | DEVELOPMENT_LOG 需記錄 session_id | -15 |
| HR-08 | Phase 結束需執行 Quality Gate | 終止 -10 |
| HR-09 | Claims Verifier 驗證需通過 | 終止 -20 |
| HR-10 | sessions_spawn.log 需有 A/B 記錄 | 終止 -15 |
| HR-11 | Phase Truth < 90% 禁進入下一 Phase | 終止 |
| HR-12 | A/B 審查 > 5 輪 → PAUSE | — |
| HR-13 | Phase 執行 > 預估 ×3 → PAUSE | — |
| HR-14 | Integrity < 40 → FREEZE 全面審計 | — |
| HR-15 | citations 格式：`檔案#L行號` | -15 |

---

## 6. 驗證關卡清單

### 6.1 Preflight Check（每個 Phase 執行前）
- [ ] FSM state ≠ FREEZE / PAUSED
- [ ] KillSwitch = CLOSED
- [ ] Constitution validation (via runner)
- [ ] Tool registry 可用

### 6.2 Gate Check（每個 Gate 評估時）
- [ ] All dimensions score ≥ per-gate threshold
- [ ] Constitution score ≥ 100 (P1-P2) / ≥ 90 (P3-P4) / ≥ 80 (P5-P8)
- [ ] No CRITICAL constitution violations (R001-R007)
- [ ] sessions_spawn.log has 2 entries (Agent A + Agent B)

### 6.3 Hermes APPROVE (Gate 4 only)
- [ ] Message sent to HERMES_REVIEWER_TARGET
- [ ] "APPROVE" reply received within 120s
- [ ] Timeout → cold-read messages_read → check latest

---

## 7. BVS (Behaviour Validation System) Phase 整合

| Phase | BVS 行為 |
|-------|----------|
| Phase 1-2 | 自動 skip (無 sessions_spawn 行為資料) |
| Phase 3+ | 自動執行 invariant checks (HR-03,07,09,10,12,13,15) |

---

*本文檔最後更新：2026-05-08*
*版本：2.4.0*
*此為 harness-methodology 的編譯後 artifact，源自 methodology-v2 v9.1*
