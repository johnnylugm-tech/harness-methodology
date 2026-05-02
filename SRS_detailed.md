# SRS - Harness Methodology v1.0

## 1. 總覽

### 1.1 專案目標
本專案旨在對一個既有的 AI 代理人方法論框架 (`methodology-v2`) 進行重構、清理與升級，最終產出一個名為 `harness-methodology` 的獨立、高品質 Python 套件。此框架的核心目標是為 AI 代理人（特別是大型語言模型）的軟體開發生命週期提供一個具備高度工程紀律、可追蹤、可驗證的標準化流程。

最終交付品質需在一個特定的「學術評估基準 (Academic Benchmark)」上達到 **≥ 91/100** 的設計分數。

### 1.2 範圍
*   **IN**：
    *   從 `methodology-v2` 中萃取所有活躍的核心模組。
    *   移除所有已識別的死碼 (Dead Code)。
    *   將核心 `AgentSpawner` 從舊的 `sessions_spawn` 執行環境遷移至 Claude Code 的 `Task` 工具。
    *   整合一個新的、基於 12 個維度的 4 層品質門 (Quality Gate) 系統。
    *   整合 Code Review Graph (CRG) 以提供確定性的程式碼結構分析。
    *   透過連接外部 Hermes MCP 服務來解決同模型審查 (Same-model A/B bias) 的問題。
    *   產出所有必要的交付物模板 (SRS, SAD, TEST_PLAN 等)。
*   **OUT**：
    *   重新設計 8 階段 (8-Phase) 的核心流程。
    *   支援 `methodology-v2` 的舊有執行環境 (OpenClaw runtime)。
    *   實現計畫中明確標示為「死碼」或「企業級依賴」的特性 (如 Hunter Agent, Langfuse 整合等)。

### 1.3 目標使用者
本框架的目標使用者為軟體開發團隊中的不同角色，框架透過 `agent_personas` 模組為每個角色定義了其行為與權責：
*   **Product Manager**: 負責定義需求 (Phase 1)。
*   **Architect**: 負責架構設計 (Phase 2)。
*   **Developer**: 負責程式碼實現 (Phase 3)。
*   **QA Engineer**: 負責測試與品質保證 (Phase 4, 6)。
*   **DevOps**: 負責交付與設定管理 (Phase 5, 8)。
*   **Reviewer**: 一個獨立的審查角色，用於 A/B 驗證 (主要由 Hermes MCP 承擔)。
*   **Johnny (Operator)**: 作為人類操作員，發起階段性指令。

---

## 2. 功能性需求 (Functional Requirements)

系統必須提供一個 CLI (`cli.py`) 作為主要入口，並支援以下 12 個核心命令來驅動 8-Phase 工作流程：

| 命令 | 功能描述 |
|---|---|
| `plan-phase` | 針對指定階段 (Phase)，生成詳細的執行計畫文件 (`Plan_Phase_N.md`)。 |
| `run-phase` | 執行指定階段的計畫。 |
| `stage-pass` | 當一個階段的所有出口條件滿足時，生成一個加密的通行權杖 (Stage-pass token)。 |
| `end-phase` | 標記一個階段的正式結束。 |
| `update-step` | 更新計畫中某個步驟的狀態。 |
| `phase-verify` | 驗證一個階段的交付物完整性與真實性 (Phase Truth Verifier)。 |
| `trace-check` | 檢查需求的可追蹤性 (例如從 FR 到測試案例)。 |
| `enforce` | 在特定階段 (P3) 強制執行團隊憲法中定義的策略。 |
| `auto-research` | (在 P7/P8) 執行自動化的品質評估與研究。 |
| `quality-gate` | 執行四個品質門 (Gate 1-4) 的核心命令。 |
| `verify-artifact` | 驗證單一交付物的合規性。 |
| `steering` | (在 P7/P8) 啟動一個指導迴圈 (Steering Loop) 來處理風險與監控。 |

---

### 2.1 八階段工作流程 (8-Phase Workflow)
系統必須支援一個標準化的、從 P1 到 P8 的軟體開發生命週期，每個階段都有明確的輸入、輸出與出口條件。

| Phase | 輸入 | 核心產出 (Artifacts) |
|---|---|---|
| **P1: Requirements** | 原始需求文件 | `SRS.md` |
| **P2: Architecture** | `SRS.md` | `SAD.md`, `quality_manifest.json` |
| **P3: Implementation**| `SAD.md`, FRs | 原始碼, 單元測試 |
| **P4: Testing** | 原始碼, `SRS.md`| `TEST_PLAN.md`, `TEST_RESULTS.md` |
| **P5: Delivery** | `TEST_RESULTS.md` | `DEPLOYMENT.md`, `SPEC_TRACKING.md` |
| **P6: QA** | 所有前期交付物 | `QUALITY_REPORT.md` |
| **P7: Risk** | `QUALITY_REPORT.md`| `RISK_REGISTER.md`, `MONITORING_PLAN.md` |
| **P8: Config Mgmt** | 所有前期交付物 | `CONFIG_RECORDS.md`, `BASELINE.md` |

---

### 2.2 品質門 (Quality Gate)
系統必須實現一個四層的、基於 12 個維度的品質門系統，以程式化的方式強制執行品質標準。

| Gate | 觸發點 | 核心目的 |
|---|---|---|
| **Gate 1** | P3, P5, P7, P8 (每個 FR 完成後) | 基礎程式碼品質檢查 (Linting, Typing, Coverage)。 |
| **Gate 2** | P3 (階段出口) | 引入安全性與變異測試，確保程式碼在整合測試前的穩固性。 |
| **Gate 3** | P4 (階段出口) | 首次進行全維度 (12-dim) 評估，包含架構、可讀性、效能等深度檢查。 |
| **Gate 4** | P6 (階段出口) | 對最終產品進行最嚴格的全維度品質總檢。 |

---

## 3. 非功能性需求 (Non-Functional Requirements)

### 3.1 可靠性 & 可重現性 (Reliability & Reproducibility)
*   **FR-NFR-01 (G3)**：系統的變異測試 (`mutation_testing`) 結果必須是穩定的。應透過執行至少 3 次並取中位數的方式來消除隨機性。
*   **FR-NFR-02**：系統中基於 LLM 的評分必須與基於確定性工具 (如 CRG, linters) 的評分進行校準，取兩者中的較低分 (`min(tool_score, llm_score)`)，以防止分數膨脹。

### 3.2 安全性 (Security)
*   **FR-NFR-03**：系統必須包含一個 `kill_switch` 模組，能在偵測到安全風險或不確定性超過閾值時，強制中斷 AI 代理人的執行。
*   **FR-NFR-04**: Gate 2/3/4 在執行自動修復前，必須先呼叫 CRG 進行影響半徑分析 (`get_impact_radius`)。若風險分數超過閾值 (0.7)，則應推遲修復，避免引入更大風險。

### 3.3 可追蹤性 (Traceability)
*   **FR-NFR-05 (G5)**：系統必須提供一種機制，將功能需求 (FR) 與程式碼、測試案例、以及 `issue_registry` 中的發現 (finding) 建立雙向連結。
*   **FR-NFR-06 (G1)**：系統必須在 P2 結束時生成一份 `quality_manifest.json` 文件，作為後續所有階段的品質合約與追蹤基準。

### 3.4 驗證獨立性 (Validation Independence)
*   **FR-NFR-07 (G2)**：為避免同模型審查的偏見，系統的審查者 (Reviewer) 角色必須透過一個獨立的通道 (`reviewer_router.py`) 與外部審查服務 (Hermes MCP) 對接。
*   **FR-NFR-08 (HR-01)**：系統必須強制執行「禁止自我審批」原則。
*   **FR-NFR-09 (HR-06)**：系統必須禁止審查者直接修改執行者的工作產出。

### 3.5 可維護性 (Maintainability)
*   **FR-NFR-10**：新倉庫 (`harness-methodology`) 中不得包含任何在 `FINAL-PLAN.md` 中被標記為「死碼」的模組。
*   **FR-NFR-11**：框架本身的核心模組 (如 `harness/`) 必須具備單元測試，且測試覆蓋率不低於 80%。

### 3.6 文件化 (Documentation)
*   **FR-NFR-12 (萃取自 #13)**：系統必須能自動記錄每次 AI 代理人執行的「決策日誌 (`DecisionLog`)」，以結構化的 YAML 格式儲存。
*   **FR-NFR-13 (G6)**：系統必須能根據專案狀態，自動生成或更新一份標準化的 `CLAUDE.md` 文件，作為與 AI 代理人交接工作的標準介面。

---

## 4. 外部介面 (External Interfaces)
*   **Claude Code `Task` Tool**: `agent_spawner` 模組將使用此工具來執行開發類型代理人的任務。
*   **Hermes MCP**: `reviewer_router` 模組將透過此 MCP 協議來發送審查請求、等待並接收審查結果。此介面必須透過環境變數 `HERMES_REVIEWER_TARGET` 進行配置。
