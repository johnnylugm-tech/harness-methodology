# SAD - Harness Methodology v1.0 (Standalone v90 - As-Built)

## 1. 架構驅動因素 (Architectural Drivers)

本系統的架構由 SRS 中定義的一系列嚴格的非功能性需求所驅動。這些需求是理解後續所有設計決策的基礎。

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
系統採用此宏觀架構。
*   **管線 (Pipe)**: 8 個軟體開發階段 (P1-P8) 構成了主流程管線。
*   **過濾器 (Filter)**: 4 個品質門 (Gate 1-4) 在 P3, P4, P6 的出口處扮演過濾器角色，確保只有品質達標的交付物才能進入下一階段。`harness_bridge.py` 是實現此過濾器邏輯的核心控制器。

### 2.2 關鍵設計模式
*   **延遲載入工廠 (Lazy-Loading Factory)**: 應用於 `cli.py`。
*   **策略模式 (Strategy Pattern)**: 應用於 `agent_spawner.py`，根據 `model` 參數切換執行策略。
*   **橋接模式 (Bridge Pattern)**: 應用於 `harness/` 目錄，將方法論流程與具體工具實現分離。
*   **外觀模式 (Façade Pattern)**: `cli.py` 為整個複雜的子系統提供了一個簡單、統一的外部介面。
*   **代理模式 (Proxy Pattern)**: `reviewer_router.py` 作為遠端 Hermes MCP 服務的本地代理。

---

## 3. 詳細模組設計 (Detailed Module Design)

### 3.1 `harness/harness_bridge.py` (Gate Controller & Bridge)
*   **職責**: 管理品質門的完整生命週期，是連接方法論流程與底層品質檢查工具的核心橋樑。
*   **公開 API**:
    *   `HarnessBridge().run_gate(gate_num: int, project_root: str, phase: int, fr_id: str | None = None) -> GateResult`: 執行一個完整的品質門流程。
    *   `HarnessBridge().generate_quality_manifest(fr_ids: list[str], sad_path: str) -> Path`: 在 P2 結束時，生成初始的品質清單。
*   **核心邏輯 (`run_gate` method)**:
    1.  呼叫 `_load_config(gate_num)` 從 `harness/gate_configs/` 目錄載入對應的 YAML 設定檔。
    2.  記錄開始時間 `t0`。
    3.  根據設定檔中的 `crg.reconnaissance` 旗標，決定是否呼叫 `self.crg.run_reconnaissance()` 執行前期結構偵察。
    4.  **呼叫 `_invoke_harness(...)` 來執行核心的品質門迭代。**
    5.  **[!!] 關鍵實現注意**: 當前 `_invoke_harness` 方法的實現是一個 `NotImplementedError` 存根。這意味著品質門的核心迭代邏輯（即與 `software_self_improvement` 框架的整合）尚未實現。任何對 `run_gate` 的呼叫在目前版本都會失敗。
    6.  (理論上) 在 `_invoke_harness` 成功回傳 `GateResult` 物件後，會呼叫 `_update_quality_manifest()` 將結果寫入 `.methodology/quality_manifest.json`。
    7.  呼叫 `self._effort.record()` 和 `self._log.write()` 記錄本次 Gate 執行的耗時與決策日誌。
    8.  根據 `gate_num` 執行阻斷邏輯：
        *   若 `gate_num == 1`，只要 `result.dimensions` 中有任何一個維度的分數低於其門檻，就拋出 `GateBlockedError`。
        *   若 `gate_num > 1`，如果最終的 `result.score` 低於設定檔中的 `score_gate`，或 `result.quality_complete` 為 `False`，則拋出 `GateBlockedError`。
    9.  若所有檢查通過，回傳 `GateResult` 物件。
*   **實現細節**:
    *   `_load_config`: 依賴 `PyYAML` 函式庫。
    *   `generate_quality_manifest`: 依賴 `scripts/generate_sab.py` 中的 `parse_sad` 函式。若該腳本或SAD文件解析失敗，會產生一個空的 `sab` 物件，確保流程不中斷。

### 3.2 `harness/reviewer_router.py` (Reviewer Proxy)
*   **職責**: 封裝與外部 Hermes MCP 服務的非同步通訊，實現獨立的 A/B 審查。
*   **公開 API**:
    *   `ReviewerRouter(target: str = HERMES_TARGET)`: 建構函式。若 `HERMES_REVIEWER_TARGET` 環境變數未設定，將拋出 `ValueError`。
    *   `review(self, role: str, prompt: str, phase: int, fr_id: str | None = None) -> dict`: 發送審查請求並等待結果。
*   **核心邏輯 (`review` method)**:
    1.  檢查 `mcp_tools` 是否可用，若否，則拋出 `RuntimeError`。
    2.  呼叫 `_build_prompt()` 來構建包含 Persona 和標準化頁尾的 Prompt。
    3.  依序呼叫 `mcp__hermes__messages_send()` 發送請求。
    4.  呼叫 `mcp__hermes__events_wait()` 進入長輪詢等待，超時由 `HERMES_TIMEOUT_MS` 環境變數（預設 120 秒）控制。
    5.  呼叫 `mcp__hermes__messages_read(limit=1)` 獲取最新一條回覆。
    6.  若無回覆，則進入 `_parse_response("")` 流程。
    7.  `_parse_response()` 方法會用正規表示式 `r"\{.*\}"` 從回覆中提取第一個 JSON 字串。若提取成功且 JSON 解析成功，則回傳該物件；否則，回傳一個固定的 `REJECT` 格式物件，並將原始回覆的前 200 字元放入 `summary` 中。
*   **實現細節**:
    *   模組頂層的 `get_reviewer_model()` 函式定義了在 P7/P8 階段應使用 `'claude'` 模型，但當前的 `review()` 方法並未呼叫此函式，其邏輯**只實現了 `'hermes'` 路徑**。
    *   此模組高度依賴 `mcp_tools` 的可用性。

### 3.3 `harness/crg_bridge.py` (Deterministic Analysis Bridge)
*   **職責**: 封裝與 Code Review Graph (CRG) 工具的互動。所有互動均透過執行外部 Python 腳本完成，而非直接的函式庫呼叫。
*   **公開 API**:
    *   `is_available() -> bool`: 檢查 `mcp__code_review_graph` 是否可被 import，以此判斷 CRG 是否安裝。結果會被快取。
    *   `run_reconnaissance(project_root: str) -> dict`: 執行 `scripts/crg_integration.py ensure`。
    *   `get_minimal_context(project_root: str, dimension: str) -> dict`: 執行 `scripts/crg_integration.py context`。
    *   `check_impact(project_root: str, ref: str = "HEAD", threshold: float = 0.7) -> bool`: 執行 `scripts/crg_integration.py risky`。腳本回傳碼為 1 時，此方法回傳 `True` (代表有風險)。
    *   `check_drift(project_root: str, threshold: float = 0.4) -> bool`: 讀取 `.sessi-work/crg_metrics.json` 檔案，比較其中的 `structural_drift` 值與閾值。
*   **實現細節**:
    *   所有 `subprocess.run` 呼叫都依賴一個名為 `SSI_ROOT` 的環境變數來定位 `software_self_improvement` 的根目錄，預設值為 `'software_self_improvement'`。
    *   此模組被設計為「優雅降級」：如果 `is_available()` 為 `False`，所有公開方法將直接回傳空值 (`{}` 或 `False`)，確保主流程不會因 CRG 未安裝而崩潰。

---

## 4. 核心工作流程時序 (Sequence Diagrams)

### 4.1 流程一：執行 Gate 2 (P3 出口)
此流程展示了當前程式碼的**實際**執行路徑。

```
Operator -> cli.py: `run_gate(gate_num=2, phase=3, ...)`
  │
  └──> harness_bridge.py: `run_gate(2, ...)`
       │
       │ 1. `_load_gate_config(2)` -> 載入 `gate2_p3_exit.yaml`
       │
       │ 2. `_invoke_harness(config, ...)`
       │    │
       │    └──> [!!] 拋出 `NotImplementedError`
       │
       └──> **流程中斷**
```
**結論**: 現有程式碼無法完成一次完整的 Gate 執行，因為與 `software_self_improvement` 的整合點是空的。

### 4.2 流程二：A/B 審查 (Hermes MCP)
此流程展示了 `AgentSpawner` 如何根據 `model` 參數切換到遠端審查模式。

```
some_module -> agent_spawner.py: `spawn(model="hermes", role="Reviewer", ...)`
  │
  └──> ReviewerRouter.py: `review(...)`
       │
       │ 1. `_build_prompt_for_reviewer(...)` (注入 `REVIEWER.md` persona)
       │
       │ 2. mcp_tools: `mcp__hermes__messages_send(target, prompt)`
       │    │
       │    │ (Network Request to Hermes MCP Server)
       │    │
       │    └──> Hermes MCP Server: 接收請求，轉發給後端的 Reviewer LLM
       │
       │ 3. mcp_tools: `mcp__hermes__events_wait(...)`
       │    │
       │    │ (Long-polling... 等待 Hermes Server 的新事件)
       │    │
       │    ├──< Hermes MCP Server: `(發送 "新訊息" 事件)`
       │    │
       │    └──> returns `event`
       │
       │ 4. mcp_tools: `mcp__hermes__messages_read(limit=1)`
       │    │
       │    └──> returns `[last_message]`
       │
       │ 5. `_parse_json_response(last_message.content)` (使用 regex 提取 JSON)
       │
       └──> returns `{"review_status": "APPROVE", ...}`
```
**結論**: `ReviewerRouter` 的實現是完整且可執行的，前提是 `mcp_tools` 可用且環境變數已設定。

---

## 5. 附錄 A：設定檔規格 (Configuration Schemas)

### 5.1 `gate_configs/*.yaml`
此類檔案定義了每個品質門的行為。

| 欄位 | 型別 | 必要 | 描述 |
|---|---|---|---|
| `gate` | `int` | 是 | 門編號 (1-4)。 |
| `trigger` | `str` | 是 | 觸發條件: `'per_fr_completion'` 或 `'phase_exit'`。 |
| `phase` | `int` | 否 | 作用階段 (僅 `trigger: phase_exit` 時需要)。 |
| `scope` | `str` | 是 | 作用範圍: `'single_fr'`, `'full_phase'`, 或 `'full_project'`。 |
| `dimensions` | `list[dict]` | 是 | 評分維度列表。 |
| `dimensions[].name` | `str` | 是 | 維度名稱 (如 `linting`)。 |
| `dimensions[].tier` | `int` | 是 | LLM 等級 (1=最快, 3=最強)。 |
| `dimensions[].model` | `str` | 是 | 使用的模型 (`gemini-flash`, `claude`)。 |
| `dimensions[].threshold`| `int` | 是 | 此維度的通過閾值 (0-100)。 |
| `dimensions[].weight` | `float` | 是 | 在總分中的權重。 |
| `blocking` | `bool` | 是 | 是否為阻塞性 Gate。 |
| `score_gate` | `int` | 否 | 總分通過閾值 (僅 Gate 2/3/4)。 |
| `max_rounds` | `int` | 是 | 最大自動修復迴圈數。 |
| `early_stop` | `bool` | 是 | 是否在找到第一個 issue 後就提早停止評估。 |
| `saturation_rounds` | `int` | 是 | 連續多少輪沒有新 issue 就判定為「飽和」。 |
| `mutation_testing` | `dict` | 否 | 變異測試相關設定。 |
| `mutation_testing.median_runs`| `int` | 是 | 執行次數，取中位數。 |
| `mutation_testing.timeout_per_run`|`int`|是|單次執行的超時秒數。|
| `crg` | `dict` | 否 | Code Review Graph 整合設定。 |
| `crg.enabled` | `bool` | 是 | 是否啟用 CRG。 |
| `crg.reconnaissance`| `bool` | 是 | 是否在 Gate 開始前執行前期偵察。 |
| `crg.tier3_guidance`|`bool`|是|是否在 Tier 3 維度評估前獲取 CRG 指引。|
| `crg.impact_check` | `bool` | 是 | 是否在修復前進行影響分析。 |
| `crg.impact_threshold`|`float`|是|影響分析的風險分數閾值。|
| `crg.drift_threshold`|`float`|是|架構漂移的閾值。|
| `replaces` | `str` | 是 | 此 Gate 取代了舊流程中的哪個部分。 |

### 5.2 `.methodology/quality_manifest.json`
此檔案的結構由 `schemas/quality_manifest.schema.json` 嚴格定義。它是一個 JSON Schema Draft 7 文件，詳細定義了每個欄位、類型、格式與約束。開發者應直接參考此 schema 檔案作為最權威的規格。

---

## 6. SAB Block (machine-readable, Final v90)
_(此部分與上一版相同，因其已準確反映了最終設計的分層)_
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
