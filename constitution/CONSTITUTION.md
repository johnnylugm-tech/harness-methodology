# Constitution — 團隊憲章

> 本文件定義團隊的不可變原則。所有專案必須遵守這些規則。
> 此為編譯後的 artifact，不可手動變更。版本哈希用於驗證完整性。
> schema_version: 2.7

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
| Gate 1 | P3/P4/P5/P7/P8 (per FR) | **per-dim** (no composite) | 3 (lint/type/cov) | FR 級別檢查 |
| Gate 2 | P3 exit | **≥ 75** | 9 | Phase 級別 composite |
| Gate 3 | P4 exit | **≥ 80** | 14 | 完整 CRG recon |
| Gate 4 | P6 exit | **≥ 85** | 14 | 全專案 |

### 2.2 品質維度權重

以下為各 Gate 實際執行的工具維度與權重（即 `harness/gate_configs/` 中定義的加權值，總和 = 100%）。

#### Gate 1（P3/P4/P5/P7/P8 per-FR — 3 維度, 各維度各自門檻；無 composite score_gate）
| 維度 | 權重 | 門檻 | 工具 |
|------|------|------|------|
| linting | 33% | ≥90 | ruff |
| type_safety | 33% | ≥85 | pyright |
| test_coverage | 34% | ≥80 | pytest-cov |

#### Gate 2（P3 exit — 9 維度, composite ≥75）
| 維度 | 權重 | 門檻 | 工具 |
|------|------|------|------|
| linting | 12% | ≥90 | ruff |
| type_safety | 12% | ≥85 | pyright |
| test_coverage | 12% | ≥80 | pytest-cov |
| security | 12% | ≥80 | bandit |
| secrets_scanning | 8% | ≥100 | gitleaks |
| license_compliance | 8% | ≥100 | scancode |
| mutation_testing | 20% | ≥70 | mutmut |
| integration_coverage | 10% | ≥60 | pytest-cov-integration |
| test_assertion_quality | 6% | ≥60 | ast-assertions |

#### Gate 3（P4 exit — 14 維度, composite ≥80）
| 維度 | 權重 | 門檻 | 工具 |
|------|------|------|------|
| linting | 10% | ≥90 | ruff |
| type_safety | 10% | ≥85 | pyright |
| test_coverage | 10% | ≥80 | pytest-cov |
| security | 10% | ≥80 | bandit |
| secrets_scanning | 8% | ≥100 | gitleaks |
| license_compliance | 7% | ≥100 | scancode |
| mutation_testing | 10% | ≥70 | mutmut |
| integration_coverage | 5% | ≥60 | pytest-cov-integration |
| architecture | 10% | ≥80 | code-review-graph |
| readability | 6% | ≥80 | radon-mi |
| error_handling | 7% | ≥80 | ast-error-handling |
| documentation | 1% | ≥75 | ast-docstrings |
| test_assertion_quality | 2% | ≥60 | ast-assertions |
| performance | 4% | ≥75 | pytest-benchmark |

#### Gate 4（P6 exit — 14 維度, composite ≥85）
| 維度 | 權重 | 門檻 | 工具 |
|------|------|------|------|
| linting | 7% | ≥90 | ruff |
| type_safety | 7% | ≥85 | pyright |
| test_coverage | 7% | ≥80 | pytest-cov |
| security | 8% | ≥80 | bandit |
| secrets_scanning | 7% | ≥100 | gitleaks |
| license_compliance | 7% | ≥100 | scancode |
| mutation_testing | 8% | ≥70 | mutmut |
| architecture | 14% | ≥80 | code-review-graph |
| readability | 8% | ≥80 | radon-mi |
| error_handling | 8% | ≥80 | ast-error-handling |
| documentation | 6% | ≥75 | ast-docstrings |
| performance | 6% | ≥75 | pytest-benchmark |
| integration_coverage | 5% | ≥75 | pytest-cov-integration |
| test_assertion_quality | 2% | ≥70 | ast-assertions |

> ¹ **D4_SpecCoverage**（v2.6 統一）: TEST_SPEC.md 為唯一溯源來源。單一 spec-coverage 檢查驗證 TEST_SPEC.md 中宣告的每一個測試案例都有對應的測試函式存在於 tests/。閾值: Gate1(per-FR)=40%, Gate2=60%, Gate3=80%, Gate4=90%。使用 `harness_cli.py spec-coverage-check --project . --threshold N`。`check-test-inventory` CLI 已棄用，會自動委派至 `spec-coverage-check`。

> ² **NFR enforcement（SAB → gate_score_overrides）**: SAD.md `nfr_traceability` 的每個 NFR 依 `type` 映射到**實際 gate 維度** — `performance→performance`、`security→security`、`maintainability→readability`、`reliability→error_handling`、`testability→test_assertion_quality`（`sab_parser._NFR_TYPE_TO_DIM`）。命中的維度自動衍生 `gate_score_overrides`（threshold floor，只升不降）：「有 NFR 背書的維度不可被 da_waiver 降到標準門檻以下」；NFR target 含 `≥N` 時取較嚴者。`deployability/scalability/usability` 無對應評分工具 → 標 `advisory_only`（人工/文件審查，誠實不假裝 enforce）。`finalize_gate` 經 `_load_manifest_sab → gate_score_overrides` 套用。

### 2.3 Entry Gate 前提條件

每個 Phase 進入前必須驗證前置條件：

| Phase | Entry Gate | 驗證方式 |
|-------|-----------|----------|
| P1 | None | 無前置 Phase |
| P2 | P1 交付物完成（SRS.md + SPEC_TRACKING.md + TRACEABILITY_MATRIX.md + TEST_INVENTORY.yaml） | `git log` + quality_manifest 確認 P1 PASS |
| P3 | P2 交付物完成（SAD.md + quality_manifest.json + TEST_SPEC.md） | `git log` + quality_manifest 確認 P2 PASS |
| P4 | Gate 2 (P3) | `git log` 確認 P3 Gate 2 PASS |
| P5 | Gate 3 (P4) | `git log` 確認 P4 Gate 3 PASS |
| P6 | Gate 3 (P5) | `git log` 確認 P5 Phase Truth PASS |
| P7 | Gate 4 (P6) | `git log` 確認 P6 Gate 4 PASS |
| P8 | Gate 4 (P6) | `git log` 確認 P6 Gate 4 PASS |

> ¹ Agent B peer review of deliverables (僅 Phase 1-2 適用。Phase 3-8 改以 Phase End Audit 替代).

### 2.4 Constitution Score 門檻

| Phase Range | 適用 TH | 維度 | 門檻 |
|-------------|---------|------|------|
| P1 | TH-03, TH-04 | correctness + security | =100% (FrameworkEnforcer BLOCK) |
| P2 | TH-03, TH-04, TH-05 | correctness + security + maintainability | correctness=100%, security=100%, maintainability>90% |
| P3 | TH-03, TH-04, TH-05 | correctness + security + maintainability | correctness=100%, security=100%, maintainability>90% |
| P4 | TH-03, TH-04, TH-05, TH-06 | correctness + security + maintainability + coverage | =100% / >90% / >90% (FrameworkEnforcer BLOCK) |
| P5–P8 | TH-02 | constitution 綜合 | ≥80% (FrameworkEnforcer BLOCK + Constitution) |

> **掃描範圍**: P1/P2 掃描 `.md` 文件（SRS.md、SAD.md 為主要交付物）。P3+ 只掃描 `.py` 原始碼 — 掃 `.md` 容易被 keyword stuffing 繞過，程式碼的 docstring/type hint/安全詞彙才能真實反映品質。

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
| Phase 3-8 程式碼 | Gate check (Quality Gate) | Gate check ≥ threshold + Phase End Audit |
| Gate 4 全專案 | Gate check (Quality Gate) | score ≥ 85 + quality_complete |
| 緊急修復 | Human (Johnny) | Minimum Gate 1 check + Phase End Audit |

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
| HR-01 | A/B 為不同 sub-agent session 分派，禁自寫自審（Phase 1-2；log-count 審計已移除——不可獨立驗證） | Workflow |
| HR-02 | Quality Gate 需實際命令輸出 | 終止 -20 |
| HR-03 | Phase 順序執行，不可跳過 | 終止 -30 |
| HR-04 | HybridWorkflow mode=ON，強制 A/B（Phase 1-2） | 終止 |
| HR-07 | ~~DEVELOPMENT_LOG 需記錄 session_id~~ **已移除** — agent-writable，不具防偽性；與 HR-10 相同理由 | — |
| HR-08 | Phase 結束需執行 Quality Gate | 終止 -10 |
| HR-09 | ~~Claims Verifier 驗證需通過~~ **不再強制**——BVS log-based claims invariant 已移除（由 agent-writable sessions_spawn.log 重檢，不可獨立驗證）；claims_verifier 子系統未接入 FSM | — |
| HR-10 | ~~sessions_spawn.log 需有 A/B 記錄~~ **已移除**——log 由代理自寫、非防竄改；A/B 品質改由 deliverable review + 工具計分 gate 把關 | — |
| HR-11 | Phase Truth < 90% 禁進入下一 Phase | 終止 |
| HR-12 | A/B 審查 > 5 輪 → PAUSE（Phase 1-2） | — |
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
- [ ] (P1-P2) sessions_spawn.log has 2 entries (Agent A + Agent B)
- [ ] (P3+) Phase End Audit: `.methodology/audit_gaps_{N}.md` 無 CRITICAL gaps

---

## 7. BVS (Behaviour Validation System) Phase 整合

| Phase | BVS 行為 |
|-------|----------|
| Phase 1-2 | 自動 skip (無 sessions_spawn 行為資料) |
| Phase 3+ | 自動執行 invariant checks (HR-03,07,09,13,15) + Phase End Audit |

---

*本文檔最後更新：2026-05-08*
*版本：2.4.0*
*此為 harness-methodology 的編譯後 artifact，源自 methodology-v2 v9.1*
