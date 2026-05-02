# SAD - Harness Methodology v1.0 (Detailed)

## 1. 架構概覽 (Architecture Overview)

### 1.1 核心設計哲學
`harness-methodology` 的架構核心是**紀律與可追溯性**。它並非一個自由形式的代理人框架，而是一個基於 **ASPICE** (汽車軟體流程改進及能力測定) 標準的、高度結構化的 **8 階段管線式 (8-Phase Pipelined) 系統**。整個系統的設計旨在將軟體開發流程從模糊的自然語言指令，轉化為一系列可被程式化驗證、可追蹤、可量化的步驟。

此專案的本質是對一個成熟的內部框架 (`methodology-v2`) 進行大規模的**重構與精煉**，其首要任務是**移除死碼 (Dead Code)**，並將核心功能遷移至一個更現代、更具確定性的執行環境。

### 1.2 宏觀架構：8-Phase Pipe & 4-Gate Filter
系統的宏觀流程可以被視為一個「管線與過濾器 (Pipe-and-Filter)」模型：
*   **管線 (Pipe)**：由 8 個依序執行的軟體開發階段 (P1-P8) 組成。每個階段都接收上一階段的交付物 (Artifacts) 作為輸入，並產出標準化的交付物作為下一階段的輸入。這個流程確保了資訊的有序流動。
*   **過濾器 (Filter)**：在關鍵的階段出口，設立了 4 個品質門 (Gate 1-4)。這些品質門會對當前階段的產出進行多維度的、程式化的品質掃描與評分。只有當分數達到預設閾值 (`score_gate`) 時，"管線"才能繼續流動，否則將被阻塞並啟動自動修復迴圈。

### 1.3 關鍵架構模式
*   **延遲載入工廠 (Lazy-Loading Factory)**：`cli.py` 作為系統的總入口，採用此模式來管理超過 30 個子系統。這確保了 CLI 的快速啟動，並只在需要時才載入相應模組，有效降低了記憶體開銷。
*   **A/B 審查與外部化驗證 (A/B Review with Externalized Validation)**：為了解決同模型審查的盲點，框架將「審查者 (Reviewer)」角色的執行完全外部化。透過 `reviewer_router.py` 模組，審查請求被發送到獨立的 Hermes MCP 服務，實現了開發與審查的分離。
*   **橋接模式 (Bridge Pattern)**：`harness/` 目錄下的模組 (如 `harness_bridge.py`, `crg_bridge.py`) 體現了橋接模式。它們將 `methodology-v2` 的核心邏輯與新整合的 `software_self_improvement` 品質門以及 Code Review Graph (CRG) 分析工具進行了解耦，使得兩邊可以獨立演進。

---

## 2. 模組設計 (Module Design)

### 2.1 `cli.py` (Orchestrator)
*   **職責**: 系統的唯一入口，負責解析使用者命令，並將其分派到對應的子系統或核心流程。它也是延遲載入工廠的宿主。
*   **關鍵組件**:
    *   `MethodologyCLI` class: 主類別。
    *   `_FACTORIES` dictionary: 定義了所有可用的子系統及其建構函式。
    *   `__getattr__` method: 實現延遲載入的核心。
    *   `run()` method: 命令分派器。
    *   `cmd_*()` methods: 12 個核心命令的具體實現入口，負責調用更深層的邏輯 (如 `harness_bridge` 或 `agent_spawner`)。
*   **依賴**: `argparse`, `os`, 以及 `_FACTORIES` 中定義的所有子系統。
*   **介面**: `python cli.py [COMMAND] [ARGS...]`

### 2.2 `core/agent_spawner.py` (Agent Spawner)
*   **職責**: 框架中**唯一需要被重構**的核心元件。負責根據角色 (Role) 和上下文，準備 Prompt 並啟動一個 AI 代理人任務。它是連接方法論與底層 AI 執行環境的關鍵。
*   **關鍵組件**:
    *   `AgentSpawner` class:
        *   `spawn()` method: 核心方法。根據傳入的 `model` 參數 ('claude' 或 'hermes') 來決定是啟動一個本地的開發任務，還是發起一個遠端的審查請求。
        *   `_load_persona()`: 實現 "Need-to-Know" 原則，根據角色動態載入對應的 Persona 文件。
        *   `_build_prompt()`: 將 Persona、SOP、以及任務指令組合成一個完整的 Prompt。
*   **依賴**: Claude Code `Task` tool, `harness/reviewer_router.py` (用於 `model="hermes"` 的情況), `agent_personas/`。
*   **介面**: `AgentSpawner().spawn(role, prompt, context, model, ...)`

### 2.3 `harness/harness_bridge.py` (Harness Bridge)
*   **職責**: **Part 2 (整合版) 的核心**。作為 `methodology-v2` 流程與新 `software_self_improvement` 品質門之間的橋樑。它負責在正確的階段觸發正確的 Gate，解析 Gate 結果，並根據結果決定是阻斷流程還是放行。
*   **關鍵組件**:
    *   `HarnessBridge` class:
        *   `run_gate(gate_num, ...)`: 核心入口，根據 Gate 編號載入對應的 YAML 設定檔，並調用底層的品質門執行器。
        *   `_invoke_harness()`: 內部方法，負責與 `software_self_improvement` 框架互動，處理 `max_rounds`, `early_stop` 等迭代邏輯。
        *   `_update_quality_manifest()`: 在 Gate 執行後，將結果寫回 `.methodology/quality_manifest.json`，實現追蹤。
        *   `generate_quality_manifest()`: 在 P2 結束時被呼叫，用於創建初始的品質清單。
*   **依賴**: `harness/crg_bridge.py`, `PyYAML`, `software_self_improvement` (作為一個子流程或庫)。
*   **介面**: `HarnessBridge().run_gate(...)`

### 2.4 `harness/reviewer_router.py` (Reviewer Router)
*   **職責**: **Gap G2 的解決方案**。將審查任務從主框架中分離出去，透過 Hermes MCP 發送到外部審查服務，以解決同模型審查的偏見問題。
*   **關鍵組件**:
    *   `ReviewerRouter` class:
        *   `review(role, prompt, ...)`: 核心方法。它會構建一個包含 Persona 和任務描述的標準化 Prompt，透過 `mcp__hermes__messages_send` 發送，然後使用 `mcp__hermes__events_wait` 長輪詢等待結果，最後解析返回的 JSON。
*   **依賴**: `mcp_tools` (特別是 `hermes` 相關工具), `os` (讀取環境變數 `HERMES_REVIEWER_TARGET`)。
*   **介面**: `ReviewerRouter().review(...)`

### 2.5 `core/quality_gate/` (Quality Checkers)
*   **職責**: 這不是一個單一模組，而是一個**模組集合**，提供了 Gate 系統所需的大部分確定性檢查能力。每個模組都對應一個特定的品質維度。
*   **關鍵組件 (範例)**:
    *   `fr_coverage_checker.py` (M5): 檢查功能需求 (FR) 是否被程式碼完整實現。
    *   `naming_convention_checker.py` (M6): 檢查 FR、NFR、TC 的命名是否符合 `enforcement.json` 中定義的正規表示式。
    *   `sab_parser.py` (M8): 解析 `SAD.md` 中的 SAB Block，為架構漂移檢測提供基準。
    *   `compliance_matrix_checker.py` (M9): 檢查交付物是否符合 ASPICE 規範。
*   **依賴**: `config_loader.py` (讀取 `enforcement.json`)。
*   **介面**: 這些模組通常被 `harness_bridge` 或其他上層模組 (如 `unified_gate.py`) 所調用，不直接暴露給 `cli.py`。

### 2.6 `implement/kill_switch/` (Kill Switch)
*   **職責**: **Feature #4 的底層實現**。作為一個安全後盾，它提供了一個中斷機制。當 `phase_hooks.py` 偵測到不確定性 (`UQLM`) 或風險分數超過預設安全閾值時，會觸發此模組，強制停止當前的代理人任務。
*   **關鍵組件**:
    *   `kill_switch.py`: 提供 `trip()` 介面。
    *   `circuit_breaker.py`: 實現了斷路器模式，防止在短時間內重複觸發。
*   **依賴**: 無。這是一個相對獨立的安全模組。
*   **介面**: `KillSwitch().trip()`

---

## 3. 資料流程 (Data Flow)
系統的資料流程嚴格遵循 8-Phase 管線模型，每個階段的標準化輸出是下一階段的輸入。

1.  **P1 (Requirements)**:
    *   **輸入**: 非結構化的使用者需求。
    *   **輸出**: `SRS.md`。
2.  **P2 (Architecture)**:
    *   **輸入**: `SRS.md`。
    *   **輸出**: `SAD.md` 和 `.methodology/quality_manifest.json`。
3.  **P3 (Implementation)**:
    *   **輸入**: `SAD.md`, `quality_manifest.json`。
    *   **輸出**: 原始碼 (`src/`) 和單元測試 (`tests/`)。
    *   **Filter**: `Gate 1` (per-FR) 和 `Gate 2` (phase exit)。
4.  **P4 (Testing)**:
    *   **輸入**: 原始碼和單元測試。
    *   **輸出**: `TEST_PLAN.md`, `TEST_RESULTS.md`, `TRACEABILITY_MATRIX.md`。
    *   **Filter**: `Gate 3`。
5.  **P5 (Delivery)**:
    *   **輸入**: `TEST_RESULTS.md`。
    *   **輸出**: `DEPLOYMENT.md` 和簽署後的 `SPEC_TRACKING.md`。
6.  **P6 (QA)**:
    *   **輸入**: 所有前序交付物。
    *   **輸出**: `QUALITY_REPORT.md`。
    *   **Filter**: `Gate 4`。
7.  **P7 (Risk)**:
    *   **輸入**: `QUALITY_REPORT.md`。
    *   **輸出**: `RISK_REGISTER.md`, `MONITORING_PLAN.md`。
8.  **P8 (Config Mgmt)**:
    *   **輸入**: 所有前序交付物。
    *   **輸出**: `CONFIG_RECORDS.md`, `BASELINE.md`。

---

## 4. Technology Choices (Expanded)
| 技術 / 模式 | 理由 |
|---|---|
| Python 3 | AI/ML 生態系的標準語言，擁有豐富的函式庫支援。 |
| `argparse` | Python 內建的 CLI 參數解析庫，穩定且使用者熟悉。 |
| **延遲載入工廠** | 核心架構模式，旨在實現毫秒級的 CLI 啟動速度和高效的記憶體管理。 |
| **Hermes MCP** | 用於實現審查者角色的外部化，是解決同模型 A/B 測試偏見、達成「驗證獨立性」的關鍵技術。 |
| **Code Review Graph (CRG)** | 作為一個確定性的程式碼分析工具，為架構、錯誤處理等主觀性較強的品質維度提供一個客觀的、可量化的評分下限 (floor)，減少對 LLM 的完全依賴。 |
| **YAML** | 用於定義 Gate 設定檔 (`gate_configs/*.yaml`) 和決策日誌 (`decision_log.py`)，因其比 JSON 更具可讀性。 |
| **SQLite** | 用於 `effort_tracker.py`，因為它是 Python 內建的、輕量級的資料庫，無需外部依賴即可實現結構化資料的儲存與查詢。 |
| **JSON** | 用於 `quality_manifest.json` 和 SAB Block，因其在程式間的互操作性最強，易於解析和驗證。 |

---

## 5. SAB Block (machine-readable, Refined)

<!-- SAB:START -->
```json
{
  "version": "1.1",
  "created_at": "2026-04-27",
  "phase": 2,
  "project": "harness-methodology",
  "layers": [
    {
      "name": "0_CLI_Entry",
      "modules": ["cli.py"],
      "allowed_dependencies": ["1_Harness_Integration", "2_Core_Orchestration"]
    },
    {
      "name": "1_Harness_Integration",
      "modules": ["harness/harness_bridge.py", "harness/reviewer_router.py", "harness/crg_bridge.py"],
      "allowed_dependencies": ["2_Core_Orchestration", "3_Quality_Features", "4_Base_Utilities"]
    },
    {
      "name": "2_Core_Orchestration",
      "modules": ["core/agent_spawner.py", "core/phase_manager.py", "core/plan_manager.py", "core/steering/steering_loop.py"],
      "allowed_dependencies": ["3_Quality_Features", "4_Base_Utilities"]
    },
    {
      "name": "3_Quality_Features",
      "modules": ["core/quality_gate/", "core/phase_hooks.py", "detection/", "gap_detector/", "implement/kill_switch/", "enforcement/"],
      "allowed_dependencies": ["4_Base_Utilities"]
    },
    {
      "name": "4_Base_Utilities",
      "modules": ["schemas/", "core/config_loader.py", "templates/"],
      "allowed_dependencies": []
    }
  ],
  "dependencies": {
    "0_CLI_Entry": ["1_Harness_Integration", "2_Core_Orchestration"],
    "1_Harness_Integration": ["2_Core_Orchestration", "3_Quality_Features", "4_Base_Utilities"],
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
