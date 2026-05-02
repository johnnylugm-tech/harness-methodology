# SAD - Harness Methodology v1.0 (Standalone)

## 1. 架構驅動因素 (Architectural Drivers)

本系統的架構並非隨意選擇，而是由 SRS 中定義的一系列嚴格的非功能性需求所驅動。這些需求是理解後續所有設計決策的基礎。

*   **驅動因素 1：驗證獨立性 (NFR-4)**
    *   **需求**: 必須從根本上避免「AI 自己審查自己」所帶來的確認偏誤。
    *   **架構決策**: 此需求直接導致了 **`ReviewerRouter` 模組** 的誕生以及 **Hermes MCP 外部介面** 的整合。架構上必須將「審查」職責完全分離出去，使其成為一個可替換、獨立的外部服務。

*   **驅動因素 2：可追蹤性與可審計性 (NFR-3 & NFR-6)**
    *   **需求**: 開發流程中的每一步、每一個決策都必須是可被追蹤和事後審計的。
    *   **架構決策**: 這催生了 **8-Phase Pipe-and-Filter (管線與過濾器) 宏觀架構**。透過將開發流程切分為固定的階段，並讓標準化的「交付物 (Artifacts)」在其中流動，我們得以在每個節點進行檢查與記錄。`quality_manifest.json` 和 `DecisionLog` 則是此架構下的關鍵產物，它們為「可追蹤性」提供了資料基礎。

*   **驅動因素 3：可靠性與可重現性 (NFR-1)**
    *   **需求**: 系統的品質評估必須是穩定且可靠的，不能過度依賴 LLM 的隨機性。
    *   **架構決策**: 此需求驅動了兩項關鍵設計：
        1.  **混合評分機制**: 在品質門的評分中，引入了 `min(tool_score, llm_score)` 的概念，確保 LLM 的評分不能高於確定性工具的評分，為結果提供一個「可靠下限」。
        2.  **整合確定性分析工具**: 引入 **Code Review Graph (CRG)** 作為架構、錯誤處理等維度的分析工具，因為它能提供基於圖論的、可重現的結構性指標，降低了對 LLM 主觀判斷的依賴。

*   **驅動因素 4：安全性 (NFR-2)**
    *   **需求**: 系統必須內建安全機制，以防止 AI 代理人執行破壞性操作或引入漏洞。
    *   **架構決策**: 設計了獨立的 **`KillSwitch` 模組** 和 **Pre-fix Impact Analysis (修復前影響分析)** 流程。`KillSwitch` 採用斷路器模式，提供了一個獨立於主流程之外的安全後盾。而影響分析則在 Gate 的修復迴圈中增加了一個安全檢查點。

*   **驅動因素 5：可維護性 (NFR-5)**
    *   **需求**: 框架本身必須易於理解、擴充和維護。
    *   **架構決策**: 這直接導向了 **延遲載入工廠 (Lazy-Loading Factory)** 和 **橋接 (Bridge)** 模式的應用。延遲載入將各個子系統解耦，而橋接模式則將核心流程與具體的品質門實現、CRG 工具等分離開來。

---

## 2. 宏觀架構與設計模式

### 2.1 宏觀架構：8-Phase Pipe & 4-Gate Filter
如「驅動因素 2」所述，系統採用此宏觀架構。
*   **管線 (Pipe)**: 8 個軟體開發階段 (P1-P8) 構成了主流程管線。
*   **過濾器 (Filter)**: 4 個品質門 (Gate 1-4) 在 P3, P4, P6 的出口處扮演過濾器角色，確保只有品質達標的交付物才能進入下一階段。`harness_bridge.py` 是實現此過濾器邏輯的核心控制器。

### 2.2 關鍵設計模式
*   **延遲載入工廠 (Lazy-Loading Factory)**: 應用於 `cli.py`，用於管理所有子系統的實例化。
*   **策略模式 (Strategy Pattern)**: 應用於 `agent_spawner.py`。`spawn` 方法中的 `model` 參數 (`'claude'` vs `'hermes'`) 就是一個策略選擇器，它根據不同的策略（本地執行 vs. 遠端審查）來切換底層的執行邏輯。
*   **橋接模式 (Bridge Pattern)**: 應用於 `harness/` 目錄。`harness_bridge` 將「方法論流程」這個抽象部分與「品質門的具體實現」這個實現部分分離開來。
*   **外觀模式 (Façade Pattern)**: `cli.py` 為整個複雜的子系統提供了一個簡單、統一的外部介面。
*   **代理模式 (Proxy Pattern)**: `reviewer_router.py` 作為遠端 Hermes MCP 服務的一個本地代理，封裝了網路通訊的複雜性。

---

## 3. 模組設計 (Module Design)

### 3.1 `cli.py` (Orchestrator & Façade)
*   **設計理由**: 為了向使用者提供一個簡單、一致的互動入口 (FR-1)，同時保持內部系統的可維護性 (NFR-5)，`cli.py` 被設計為一個外觀和工廠。
*   **職責**: 解析命令、管理子系統生命週期、分派任務。
*   **介面**: `MethodologyCLI().run(args)`。

### 3.2 `core/agent_spawner.py` (Agent Spawner & Strategy Context)
*   **設計理由**: 為了滿足系統需要執行不同類型代理人（開發 vs. 審查）的需求 (FR-2.1, NFR-4.1)，`AgentSpawner` 被設計為一個策略上下文。
*   **職責**: 抽象化代理人的啟動過程。它不關心任務如何被執行，只關心根據策略（`model` 參數）選擇正確的執行器（`Task` tool 或 `ReviewerRouter`）。同時，它也負責根據 "Need-to-Know" 原則，在啟動前為代理人注入必要的上下文（Persona, SOP）。
*   **介面**: `AgentSpawner().spawn(model, ...)`。

### 3.3 `harness/harness_bridge.py` (Gate Controller & Bridge)
*   **設計理由**: 為實現 Pipe-and-Filter 架構中的「過濾器」部分 (FR-3)，需要一個中心化的控制器來管理品質門的觸發與結果處理。
*   **職責**: 載入 Gate 設定檔、調用品質門執行器、解析並記錄結果、並根據 `score_gate` 執行流程控制（阻塞或放行）。它是連接核心方法論與品質門具體實現的橋樑。
*   **介面**: `HarnessBridge().run_gate(...)`。

### 3.4 `harness/reviewer_router.py` (Reviewer Proxy)
*   **設計理由**: 這是實現「驗證獨立性」(NFR-4.1) 的核心模組。
*   **職責**: 作為 Hermes MCP 服務的代理，封裝了所有與外部審查服務通訊的細節，包括構建標準化 Prompt、發送請求、長輪詢等待以及解析回傳的 JSON 結果。
*   **介面**: `ReviewerRouter().review(...)`。

### 3.5 `harness/crg_bridge.py` (Deterministic Analysis Bridge)
*   **設計理由**: 為滿足「可靠性」需求 (NFR-1.2)，需要一個介面來與確定性分析工具 CRG 進行互動。
*   **職責**: 封裝與 Code Review Graph 工具的互動，提供 `run_reconnaissance`, `check_impact`, `check_drift` 等高階介面，並設計了「優雅降級 (Graceful Degradation)」機制——若 CRG 未安裝，這些呼叫將靜默跳過，確保主框架的健壯性。
*   **介面**: `CRGBridge().check_impact(...)` 等。

---

## 4. 資料流程架構 (Data-Flow Architecture)

系統的資料流嚴格遵循 P1 到 P8 的順序，以標準化的「交付物 (Artifacts)」作為階段間的傳遞媒介，這是實現「可追蹤性」(NFR-3) 的基礎。

*   **P1**: `(User Need) -> [process] -> SRS.md`
*   **P2**: `SRS.md -> [process] -> (SAD.md, quality_manifest.json)`
*   **P3**: `(SAD.md, quality_manifest.json) -> [process] -> (Source Code, Unit Tests) -> [Gate 1/2 Filter]`
*   **P4**: `(Source Code) -> [process] -> (TEST_PLAN.md, TEST_RESULTS.md) -> [Gate 3 Filter]`
*   **P5**: `TEST_RESULTS.md -> [process] -> DEPLOYMENT.md`
*   **P6**: `(All Artifacts) -> [process] -> QUALITY_REPORT.md -> [Gate 4 Filter]`
*   **P7**: `QUALITY_REPORT.md -> [process] -> (RISK_REGISTER.md, MONITORING_PLAN.md)`
*   **P8**: `(All Artifacts) -> [process] -> (CONFIG_RECORDS.md, BASELINE.md)`

---

## 5. SAB Block (machine-readable, Final)

<!-- SAB:START -->
```json
{
  "version": "1.2",
  "created_at": "2026-04-27",
  "project": "harness-methodology",
  "layers": [
    {
      "name": "0_Entrypoint_Facade",
      "description": "The command-line interface, acting as a simple facade to the system.",
      "modules": ["cli.py"],
      "allowed_dependencies": ["1_Integration_Bridge", "2_Core_Orchestration"]
    },
    {
      "name": "1_Integration_Bridge",
      "description": "The bridge layer that connects the core logic to external tools and services like Quality Gates, CRG, and Hermes.",
      "modules": ["harness/harness_bridge.py", "harness/reviewer_router.py", "harness/crg_bridge.py"],
      "allowed_dependencies": ["2_Core_Orchestration", "3_Quality_Features", "4_Base_Utilities"]
    },
    {
      "name": "2_Core_Orchestration",
      "description": "Manages the 8-phase workflow and agent lifecycle.",
      "modules": ["core/agent_spawner.py", "core/phase_manager.py", "core/plan_manager.py", "core/steering/steering_loop.py"],
      "allowed_dependencies": ["3_Quality_Features", "4_Base_Utilities"]
    },
    {
      "name": "3_Quality_Features",
      "description": "Concrete implementations of various quality checks and safety features.",
      "modules": ["core/quality_gate/", "core/phase_hooks.py", "detection/", "gap_detector/", "implement/kill_switch/", "enforcement/"],
      "allowed_dependencies": ["4_Base_Utilities"]
    },
    {
      "name": "4_Base_Utilities",
      "description": "Cross-cutting concerns like configuration, schemas, and templates.",
      "modules": ["schemas/", "core/config_loader.py", "templates/"],
      "allowed_dependencies": []
    }
  ],
  "dependencies": {
    "0_Entrypoint_Facade": ["1_Integration_Bridge", "2_Core_Orchestration"],
    "1_Integration_Bridge": ["2_Core_Orchestration", "3_Quality_Features", "4_Base_Utilities"],
    "2_Core_Orchestration": ["3_Quality_Features", "4_Base_Utilities"],
    "3_Quality_Features": ["4_Base_Utilities"]
  },
  "quality_targets": {
    "max_complexity": 20,
    "min_coverage": 80,
    "max_coupling": 0.35
  }
}
```
<!-- SAB:END -->
