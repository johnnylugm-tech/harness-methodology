# Harness Methodology - Gemini CLI Gap Analysis Report

**日期**: 2026年4月27日
**目標**: 深入研究 `harness-methodology` 框架在 Gemini CLI 環境中執行的技術差距。

---

## 1. 核心 SDK 與運行環境 Gap (Infrastructure Gap)

*   **`claude_code_sdk` 硬依賴**
    *   **描述**: `core/agent_spawner.py` 程式碼中顯式 `import Task from claude_code_sdk`。這是為了在 Claude Code 環境中派發子 Agent。
    *   **Gap**: Gemini CLI **不具備該 SDK**，這會導致 Python 腳本執行時報 `ImportError`。Gemini CLI 雖然有 `invoke_agent` 工具，但其呼叫介面與 `Task` 不同。
*   **MCP 工具呼叫慣例 (Underscore Mismatch)**
    *   **描述**: 框架（如 `harness/reviewer_router.py`）預期 MCP 工具名稱為 `mcp__hermes__messages_send`（雙底線隔離）。
    *   **Gap**: Gemini CLI 的 MCP 工具命名規範通常是單底線（如 `mcp_hermes_messages_send`）。此外，框架預期工具以 Python 函數形式從 `mcp_tools` 模組匯入，但在 Gemini CLI 中，這些工具是外部註冊的 Action。

## 2. A/B Agent 隔離與獨立性 Gap (Orchestration Gap)

*   **HR-01 (獨立性) 違反風險**
    *   **描述**: 方法論的核心硬規則 **HR-01** 要求 Developer 與 Reviewer 必須是不同的 Agent 實例。
    *   **Gap**: Gemini CLI 的子 Agent 支持目前會回退到單一會話執行。這意味著「開發者」與「審查者」很可能是同一個 Agent 實體，這會導致**確認偏誤 (Confirmation Bias)**，違反了該方法論「異質審查」的設計初衷。

## 3. 工作流與指令集 Gap (Workflow Mandate Gap)

*   **Topic Model 不同步**
    *   **描述**: Gemini CLI 強制要求在多步驟任務中使用 `update_topic`。
    *   **Gap**: 方法論的 Python CLI (`cli.py`) 未整合 `update_topic`。這導致執行複雜任務時，用戶無法獲得結構化的主題更新報告。
*   **計畫模式衝突**
    *   **描述**: Gemini CLI 有 `enter_plan_mode` 工具。
    *   **Gap**: 方法論的 `python cli.py plan-phase` 僅產生 Markdown 文件，並未觸發 Gemini CLI 的原生計畫模式（Plan Mode），導致兩者狀態不連動。

## 4. 任務追蹤與元數據 Gap (Metadata Gap)

*   **Task Tracker 孤島**
    *   **描述**: 方法論使用 `core/task_splitter.py` 分解任務並記錄在 `sessions_spawn.log`。
    *   **Gap**: Gemini CLI 內建了 `tracker_create_task`。方法論產生的任務無法自動對接到 Gemini CLI 的追蹤系統中，導致任務可視化失效。

## 5. 記憶層級與憲法衝突 (Memory & Constitution Gap)

*   **Hard Rules 儲存位置**
    *   **描述**: 方法論將規則儲存在 `.methodology/enforcement.json`。
    *   **Gap**: Gemini CLI 依賴 `GEMINI.md`（項目層級）與 `MEMORY.md`（私有層級）。將硬規則分散在 `.methodology/` 可能導致 Agent 在推理時忽略這些約束，除非顯式讀取該檔案。

---

## 建議修復路徑

1.  **適配器 (Adapter)**: 改寫 `AgentSpawner` 以偵測 Gemini CLI 環境，並將其任務轉化為 `tracker_create_task` 呼叫。
2.  **工具對映**: 在 `harness/reviewer_router.py` 中增加轉換層，解決 `mcp__` 與 `mcp_` 的命名差異。
3.  **進度掛鉤**: 在 `PhaseHooks` 中加入 `update_topic` 調用。
4.  **記憶遷移**: 將核心硬規則（Hard Rules）遷移至 `GEMINI.md` 以確保最高優先級。
