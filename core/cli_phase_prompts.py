# Phase Prompts - All 8 Phases
# This module contains Phase-specific prompts for plan-phase generation

PHASE_PROMPTS = {
    1: {
        "name": "需求規格",
        "agent_a": "architect",
        "agent_b": "reviewer",
        "developer": """```
TASK: 制定軟體需求規格 (SRS)
TASK_ID: task-p1
═══════════════════════════════════════

【階段目標】
建立完整的軟體需求規格（SRS），包含功能需求(FR)和非功能需求(NFR)

【On Demand 讀取】（只讀這些章節，❌ 禁止 dump 全文）
- TASK_INITIALIZATION_PROMPT.md（只讀專案目標和約束）

【產出】
- SRS.md：軟體需求規格文件
- SPEC_TRACKING.md：規格追蹤矩陣
- TRACEABILITY_MATRIX.md：需求追蹤矩陣

【驗證標準】
- Constitution SRS ≥80%
- 每個 FR 有明確的驗收標準
- 每個 NFR 可追蹤到 FR
- Traceability 矩陣 100% 完整

【FORBIDDEN】
- ❌ 遺漏任何 FR 或 NFR
- ❌ 規格模糊、無法驗證
- ❌ 遺漏介面規格
- ❌ 無 citations 或 citations 無行號 → HR-15 違規

【OUTPUT_FORMAT】
{{
 "status": "success|error|unable_to_proceed",
 "result": "實際產出（SRS.md 路徑）",
 "confidence": 1-10,
 "citations": ["TASK_INITIALIZATION_PROMPT.md#L10-L20"],
 "summary": "50字內"
}}

HR-15 強制執行：citations 必須包含「檔名#L行號」格式
═══════════════════════════════════════
```""",
        "reviewer": """```
TASK: Review 軟體需求規格 (SRS)
TASK_ID: task-p1-review
═══════════════════════════════════════

【審查範圍】（只讀這些章節，❌ 禁止 dump 全文）
- SRS.md（只讀 FR 和 NFR 章節）
- SPEC_TRACKING.md
- TRACEABILITY_MATRIX.md

【驗證檢查清單】
1. 每個 FR 有明確的驗收標準
2. 每個 NFR 可追蹤到對應的 FR
3. Traceability 矩陣完整（FR → NFR → Test）
4. Constitution SRS 分數 ≥80%
5. 介面規格清晰（輸入/輸出/錯誤處理）

【REJECT_IF】
- ❌ FR 遺漏或模糊 → REJECT
- ❌ NFR 無法驗證 → REJECT
- ❌ Traceability 不完整 → REJECT
- ❌ Constitution < 80% → REJECT
- ❌ 缺少 citations 或無行號 → REJECT（HR-15）

【OUTPUT_FORMAT】
{{
 "status": "APPROVE|REJECT",
 "confidence": 1-10,
 "violations": ["具體問題"],
 "constitution_score": "分數",
 "summary": "50字內"
}}
═══════════════════════════════════════
```"""
    },
    
    2: {
        "name": "架構設計",
        "agent_a": "architect",
        "agent_b": "reviewer",
        "developer": """```
TASK: 制定系統架構文件 (SAD) + 架構決策記錄 (ADR)
TASK_ID: task-p2
═══════════════════════════════════════

【階段目標】
基於 SRS 設計系統架構，包含模組邊界、介面、資料流

【On Demand 讀取】（只讀這些章節，❌ 禁止 dump 全文）
- SRS.md（只讀 FR 需求和介面規格）
- 任務初始化提示（只讀約束）

【產出】
- SAD.md：系統架構文件
- ADR.md：架構決策記錄（每個關鍵決策一筆）

【驗證標準】
- Constitution SAD ≥80%
- SAD↔SRS 一致性 =100%
- 每個 FR 有對應的 Module
- 每個 Module 有明確職責和介面

【FORBIDDEN】
- ❌ 偏離 SRS 的需求
- ❌ 模組邊界模糊或重疊
- ❌ 遺漏錯誤處理機制
- ❌ 無 citations 或無行號 → HR-15 違規

【OUTPUT_FORMAT】
{{
 "status": "success|error|unable_to_proceed",
 "result": "實際產出（SAD.md, ADR.md 路徑）",
 "confidence": 1-10,
 "citations": ["SRS.md#L20-L30", "SAD.md#L10-L15"],
 "summary": "50字內"
}}

HR-15 強制執行：citations 必須包含「檔名#L行號」格式
═══════════════════════════════════════
```""",
        "reviewer": """```
TASK: Review 系統架構文件 (SAD) + 架構決策記錄 (ADR)
TASK_ID: task-p2-review
═══════════════════════════════════════

【審查範圍】（只讀這些章節，❌ 禁止 dump 全文）
- SAD.md（只讀 Module 邊界和介面章節）
- ADR.md（只讀決策理由）
- SRS.md（只讀 FR 對應章節）

【驗證檢查清單】
1. SAD↔SRS 一致性 =100%（每個 FR 有對應 Module）
2. 每個 Module 有明確職責（單一職責原則）
3. 介面規格清晰（輸入/輸出/錯誤處理）
4. ADR 記錄關鍵決策及其理由
5. Constitution SAD 分數 ≥80%

【REJECT_IF】
- ❌ SAD↔SRS 不一致 → REJECT
- ❌ Module 職責重疊 → REJECT
- ❌ 介面模糊 → REJECT
- ❌ ADR 決策理由不足 → REJECT
- ❌ 缺少 citations 或無行號 → REJECT（HR-15）

【OUTPUT_FORMAT】
{{
 "status": "APPROVE|REJECT",
 "confidence": 1-10,
 "violations": ["具體問題"],
 "consistency_score": "SRS↔SAD 一致性 %",
 "summary": "50字內"
}}
═══════════════════════════════════════
```"""
    },
    
    3: {
        "name": "代碼實現",
        "agent_a": "developer",
        "agent_b": "reviewer",
        "developer": """```
TASK: {fr['fr']} {fr['title']}
TASK_ID: task-{fr_num}
═══════════════════════════════════════

【先決條件】
cd {repo_path}
pwd  # 確認在正確目錄

【Phase Hooks 整合】（HR-09 強制執行）
⚠️ 每個 FR 執行前後必須呼叫 PhaseHooksAdapter：
import sys
from pathlib import Path
sys.path.insert(0, '{methodology_path}')
from adapters.phase_hooks_adapter import PhaseHooksAdapter

adapter = PhaseHooksAdapter(
    project_path="{repo_path}",
    phase={phase_num},
    feature_flags={{"uqlm": True, "gap_detector": True, "hunter": True, "shields": True}}
)
# Preflight：只在第一個 FR 前執行一次
adapter.preflight_all()

# FR 前
adapter.monitoring_before_dev("{fr_id}")

# Developer 實作...

# FR 後（developer result 傳入）
hook_result = adapter.monitoring_after_dev("{fr_id}", result=dev_result)
if not hook_result.get("passed"):
    raise Exception(f"Hook blocked by Feature: {{hook_result}}")

【階段目標】
依據 SAD 實作指定模組，包含單元測試

【On Demand 讀取】（只讀這些章節，❌ 禁止 dump 全文）

SRS.md 只讀取：
- §{fr['fr']} 需求描述
- §{fr['fr']} 測試案例（有的話）

SAD.md 只讀取：
- §Module 邊界對照表（對應 {fr['fr']} 的章節）

【產出】
- {fr.get('file', 'app/processing/{fr_num}.py')}：實作代碼
- tests/test_{fr_num}.py：單元測試

【驗證標準】
- pytest 100% 通過
- 覆蓋率 ≥70%
- docstring 包含 [FR-XX] 標記
- docstring 包含 Citations（SRS.md#L行號, SAD.md#L行號）

【FORBIDDEN】
- ❌ dump SRS.md/SAD.md 全文
- ❌ app/infrastructure/（已廢除，請用正確目錄）
- ❌ docstring 沒有 [FR-XX] 標記
- ❌ docstring 沒有 Citations（含行號）
- ❌ @type: edge
- ❌ ... 省略 → 任務失敗
- ❌ 無 citations 或 citations 無行號 → HR-15 違規
- ❌ citations 未寫入 code docstring → HR-15 違規
- ❌ 未呼叫 PhaseHooks → HR-09 違規

【OUTPUT_FORMAT】
{{
  "status": "success|error|unable_to_proceed",
  "result": "實際產出（路徑）",
  "confidence": 1-10,
  "citations": ["{fr['fr']}", "SRS.md#L23-L45", "SAD.md#L50-L60"],
  "hook_calls": {{
    "monitoring_before_dev": {{ "blocked": False }},
    "monitoring_after_dev": {{
      "passed": True,
      "shield_verdict": "{{shield_verdict}}",
      "uqlm_score": {{uqlm_score}},
      "hunter_severity": null
    }}
  }},
  "summary": "50字內"
}}

HR-15 強制執行：citations 必須包含「檔名#L行號」格式
HR-09 強制執行：每個 FR 必須呼叫 Phase Hooks
═══════════════════════════════════════
```""",
        "reviewer": """```
TASK: Review {fr['fr']} {fr['title']}
TASK_ID: task-{fr_num}-review
═══════════════════════════════════════

【先決條件】
cd {repo_path}

【Phase Hooks 整合】
# Reviewer 執行前
adapter.monitoring_before_rev("{fr_id}")

【審查範圍】（只讀這些章節，❌ 禁止 dump 全文）

待審查檔案：
- {fr.get('file', 'app/processing/{fr_num}.py')}（每個函數的 docstring 需含 [FR-XX]）
- tests/test_{fr_num}.py

規格參考：
- SRS.md §{fr['fr']}（只讀需求和測試案例章節）

【驗證檢查清單】
1. 每個公開函數的 docstring 含 [FR-XX] 標記
2. 每個公開函數的 docstring 含 Citations（SRS.md#L行號, SAD.md#L行號）
3. 測試覆蓋率 ≥70%
4. pytest 100% 通過
5. 無邏輯錯誤或安全漏洞
6. Constitution 測試覆蓋率 >90%（TH-06）

【REJECT_IF】
- ❌ docstring 無 [FR-XX] 標記 → REJECT
- ❌ docstring 無 Citations（含行號）→ REJECT
- ❌ NFR 約束違背 → REJECT
- ❌ confidence < 6 → REJECT
- ❌ 缺少 citations 或 citations 無行號 → REJECT（HR-15）
- ❌ 覆蓋率 < 70% → REJECT
- ❌ 未呼叫 PhaseHooks → HR-09 違規

# Reviewer 完成後
hook_result = adapter.monitoring_after_rev("{fr_id}", result=rev_result)

【OUTPUT_FORMAT】
{{
  "status": "APPROVE|REJECT",
  "confidence": 1-10,
  "violations": ["具體問題"],
  "coverage": "覆蓋率 %",
  "hook_calls": {{
    "monitoring_before_rev": {{ "blocked": False }},
    "monitoring_after_rev": {{ "passed": True }}
  }},
  "summary": "50字內"
}}
═══════════════════════════════════════
```"""
    },
    
    4: {
        "name": "測試規劃與執行",
        "agent_a": "qa",
        "agent_b": "reviewer",
        "developer": """```
TASK: 制定測試計畫 (TEST_PLAN) + 執行測試 (TEST_RESULTS)
TASK_ID: task-p4
═══════════════════════════════════════

【階段目標】
基於 Phase 3 代碼制定完整測試計畫並執行

【On Demand 讀取】（只讀這些章節，❌ 禁止 dump 全文）
- SRS.md（只讀 FR 需求和驗收標準）
- SAD.md（只讀 Module 介面）
- src/（只看導出的公開介面）

【產出】
- TEST_PLAN.md：測試計畫（測試策略、環境、風險）
- TEST_RESULTS.md：測試結果（執行記錄、通過率）
- COVERAGE_REPORT.md：覆蓋率報告

【驗證標準】
- Constitution 測試覆蓋率 >90%（TH-06）
- FR↔測試 映射率 ≥90%
- 整合測試 100% 通過
- 效能測試達標（如有）

【FORBIDDEN】
- ❌ 測試案例未對應 FR
- ❌ 測試環境未隔離
- ❌ 關鍵路徑未覆蓋
- ❌ 無 citations 或無行號 → HR-15 違規

【OUTPUT_FORMAT】
{{
 "status": "success|error|unable_to_proceed",
 "result": "實際產出（TEST_PLAN.md, TEST_RESULTS.md 路徑）",
 "confidence": 1-10,
 "citations": ["SRS.md#L20-L30", "src/"],
 "summary": "50字內"
}}

HR-15 強制執行：citations 必須包含「檔名#L行號」格式
═══════════════════════════════════════
```""",
        "reviewer": """```
TASK: Review 測試計畫 (TEST_PLAN) + 測試結果 (TEST_RESULTS)
TASK_ID: task-p4-review
═══════════════════════════════════════

【審查範圍】（只讀這些章節，❌ 禁止 dump 全文）
- TEST_PLAN.md
- TEST_RESULTS.md
- COVERAGE_REPORT.md

【驗證檢查清單】
1. 每個 FR 有對應的測試案例
2. FR↔測試 映射率 ≥90%
3. 關鍵路徑覆蓋完整
4. 測試環境與正式環境一致
5. Constitution 測試分數 >80%

【REJECT_IF】
- ❌ FR 未完全覆蓋 → REJECT
- ❌ 關鍵路徑未測試 → REJECT
- ❌ 環境不一致 → REJECT
- ❌ 覆蓋率 < 80% → REJECT
- ❌ 缺少 citations 或無行號 → REJECT（HR-15）

【OUTPUT_FORMAT】
{{
 "status": "APPROVE|REJECT",
 "confidence": 1-10,
 "violations": ["具體問題"],
 "coverage_rate": "FR↔測試 映射率 %",
 "summary": "50字內"
}}
═══════════════════════════════════════
```"""
    },
    
    5: {
        "name": "驗證交付",
        "agent_a": "devops",
        "agent_b": "architect",
        "developer": """```
TASK: 建立系統 Baseline + Monitoring Plan
TASK_ID: task-p5
═══════════════════════════════════════

【階段目標】
依據測試結果建立系統 Baseline，確保可監控、可追溯

【On Demand 讀取】（只讀這些章節，❌ 禁止 dump 全文）
- TEST_RESULTS.md（只讀通過/失敗統計）
- SRS.md（只讀效能需求和約束）

【產出】
- BASELINE.md：系統基線（效能基準、配置快照）
- MONITORING_PLAN.md：監控計畫（指標、警報閾值）
- VERIFICATION_REPORT.md：驗證報告

【驗證標準】
- Baseline 效能符合 SRS 約束
- Monitoring 覆蓋關鍵指標
- 警報閾值設定合理

【FORBIDDEN】
- ❌ Baseline 偏離實際效能
- ❌ 監控指標遺漏關鍵項目
- ❌ 警報閾值過寬或過嚴
- ❌ 無 citations 或無行號 → HR-15 違規

【OUTPUT_FORMAT】
{{
 "status": "success|error|unable_to_proceed",
 "result": "實際產出（BASELINE.md, MONITORING_PLAN.md 路徑）",
 "confidence": 1-10,
 "citations": ["TEST_RESULTS.md#L10-L20"],
 "summary": "50字內"
}}

HR-15 強制執行：citations 必須包含「檔名#L行號」格式
═══════════════════════════════════════
```""",
        "reviewer": """```
TASK: Review 系統 Baseline + Monitoring Plan
TASK_ID: task-p5-review
═══════════════════════════════════════

【審查範圍】（只讀這些章節，❌ 禁止 dump 全文）
- BASELINE.md
- MONITORING_PLAN.md
- TEST_RESULTS.md（只讀統計）

【驗證檢查清單】
1. Baseline 效能符合 SRS 約束
2. Monitoring 覆蓋所有關鍵指標
3. 警報閾值合理（可達標且不會誤報）
4. 監控儀表板可追蹤
5. Constitution 驗證分數 ≥80%

【REJECT_IF】
- ❌ Baseline 不符合 SRS → REJECT
- ❌ 監控指標遺漏 → REJECT
- ❌ 警報閾值不合理 → REJECT
- ❌ 缺少 citations 或無行號 → REJECT（HR-15）

【OUTPUT_FORMAT】
{{
 "status": "APPROVE|REJECT",
 "confidence": 1-10,
 "violations": ["具體問題"],
 "summary": "50字內"
}}
═══════════════════════════════════════
```"""
    },
    
    6: {
        "name": "品質保證",
        "agent_a": "qa",
        "agent_b": "architect",
        "developer": """```
TASK: 生成品質報告 (QUALITY_REPORT)
TASK_ID: task-p6
═══════════════════════════════════════

【階段目標】
進行全面品質評估，確保系統達到發布標準

【On Demand 讀取】（只讀這些章節，❌ 禁止 dump 全文）
- TEST_RESULTS.md（只讀失敗案例）
- BASELINE.md（只讀效能數據）
- QUALITY_REPORT.md（如有，讀取現有版本）

【產出】
- QUALITY_REPORT.md：品質報告（品質維度、指標、問題清單）
- 問題修復計畫（如有問題）

【驗證標準】
- Constitution 品質總分 ≥80%
- 邏輯正確性分數 ≥90
- 所有高優先問題已修復或接受風險

【FORBIDDEN】
- ❌ 隱瞞品質問題
- ❌ 高優先問題未處理
- ❌ 報告數據與實際不符
- ❌ 無 citations 或無行號 → HR-15 違規

【OUTPUT_FORMAT】
{{
 "status": "success|error|unable_to_proceed",
 "result": "實際產出（QUALITY_REPORT.md 路徑）",
 "confidence": 1-10,
 "citations": ["TEST_RESULTS.md#L30-L40"],
 "summary": "50字內"
}}

HR-15 強制執行：citations 必須包含「檔名#L行號」格式
═══════════════════════════════════════
```""",
        "reviewer": """```
TASK: Review 品質報告 (QUALITY_REPORT)
TASK_ID: task-p6-review
═══════════════════════════════════════

【審查範圍】（只讀這些章節，❌ 禁止 dump 全文）
- QUALITY_REPORT.md
- TEST_RESULTS.md
- BASELINE.md

【驗證檢查清單】
1. Constitution 品質總分 ≥80%
2. 邏輯正確性分數 ≥90
3. 高優先問題已修復或接受風險
4. 品質趨勢合理（相較 Baseline）
5. 發布建議明確

【REJECT_IF】
- ❌ Constitution < 80% → REJECT
- ❌ 高優先問題未處理 → REJECT
- ❌ 數據與實際不符 → REJECT
- ❌ 缺少 citations 或無行號 → REJECT（HR-15）

【OUTPUT_FORMAT】
{{
 "status": "APPROVE|REJECT",
 "confidence": 1-10,
 "violations": ["具體問題"],
 "quality_score": "Constitution 分數",
 "summary": "50字內"
}}
═══════════════════════════════════════
```"""
    },
    
    7: {
        "name": "風險管理",
        "agent_a": "qa",
        "agent_b": "pm",
        "developer": """```
TASK: 風險識別、評估與緩解計畫
TASK_ID: task-p7
═══════════════════════════════════════

【階段目標】
識別、追蹤並制定所有已識別風險的緩解策略

【On Demand 讀取】（只讀這些章節，❌ 禁止 dump 全文）
- QUALITY_REPORT.md（只讀問題和風險章節）
- SRS.md（只讀約束和假設）

【產出】
- RISK_REGISTER.md：風險註冊表（風險描述、機率、影響、狀態）
- RISK_MITIGATION_PLANS.md：緩解計畫（每個風險的應對策略）
- RISK_STATUS_REPORT.md：風險狀態報告

【驗證標準】
- 所有已識別風險有緩解計畫
- 風險狀態正確（Open/InProgress/Closed）
- 緩解計畫可行且已分配責任

【FORBIDDEN】
- ❌ 遺漏已知風險
- ❌ 風險評估不客觀
- ❌ 緩解計畫不具體或不可行
- ❌ 無 citations 或無行號 → HR-15 違規

【OUTPUT_FORMAT】
{{
 "status": "success|error|unable_to_proceed",
 "result": "實際產出（RISK_REGISTER.md 路徑）",
 "confidence": 1-10,
 "citations": ["QUALITY_REPORT.md#L20-L30"],
 "summary": "50字內"
}}

HR-15 強制執行：citations 必須包含「檔名#L行號」格式
═══════════════════════════════════════
```""",
        "reviewer": """```
TASK: Review 風險管理文件
TASK_ID: task-p7-review
═══════════════════════════════════════

【審查範圍】（只讀這些章節，❌ 禁止 dump 全文）
- RISK_REGISTER.md
- RISK_MITIGATION_PLANS.md
- QUALITY_REPORT.md

【驗證檢查清單】
1. 所有已識別風險有對應緩解計畫
2. 風險評估合理（機率×影響）
3. 緩解計畫具體且可行
4. 責任分配明確
5. 追蹤機制到位

【REJECT_IF】
- ❌ 風險遺漏 → REJECT
- ❌ 評估不客觀 → REJECT
- ❌ 緩解計畫不可行 → REJECT
- ❌ 缺少 citations 或無行號 → REJECT（HR-15）

【OUTPUT_FORMAT】
{{
 "status": "APPROVE|REJECT",
 "confidence": 1-10,
 "violations": ["具體問題"],
 "risk_count": "風險數量",
 "summary": "50字內"
}}
═══════════════════════════════════════
```"""
    },
    
    8: {
        "name": "配置管理",
        "agent_a": "devops",
        "agent_b": "pm",
        "developer": """```
TASK: 建立配置管理系統，確保可追溯性
TASK_ID: task-p8
═══════════════════════════════════════

【階段目標】
建立完整的配置管理系統，確保系統可部署、可重現

【On Demand 讀取】（只讀這些章節，❌ 禁止 dump 全文）
- RISK_REGISTER.md（只讀已知風險）
- BASELINE.md（只讀配置快照）
- QUALITY_REPORT.md（如有）

【產出】
- CONFIG_RECORDS.md：配置記錄（環境、版本、參數）
- DEPLOYMENT_CHECKLIST.md：部署檢查清單
- ENVIRONMENT_SPEC.md：環境規格
- requirements.lock：依賴鎖定

【驗證標準】
- requirements.lock 存在且完整
- 部署檢查清單 100% 可執行
- 配置記錄可追溯到每個元件

【FORBIDDEN】
- ❌ requirements.lock 與實際不符
- ❌ 部署檢查清單不完整
- ❌ 配置記錄遺漏關鍵參數
- ❌ 無 citations 或無行號 → HR-15 違規

【OUTPUT_FORMAT】
{{
 "status": "success|error|unable_to_proceed",
 "result": "實際產出（CONFIG_RECORDS.md, requirements.lock 路徑）",
 "confidence": 1-10,
 "citations": ["BASELINE.md#L10-L15"],
 "summary": "50字內"
}}

HR-15 強制執行：citations 必須包含「檔名#L行號」格式
═══════════════════════════════════════
```""",
        "reviewer": """```
TASK: Review 配置管理文件
TASK_ID: task-p8-review
═══════════════════════════════════════

【審查範圍】（只讀這些章節，❌ 禁止 dump 全文）
- CONFIG_RECORDS.md
- DEPLOYMENT_CHECKLIST.md
- ENVIRONMENT_SPEC.md
- requirements.lock

【驗證檢查清單】
1. requirements.lock 與實際完全一致
2. 部署檢查清單可完整執行
3. 配置記錄涵蓋所有環境（Dev/Staging/Prod）
4. 版本一致性（元件版本、依賴版本）
5. 部署流程可重現

【REJECT_IF】
- ❌ requirements.lock 不完整或不一致 → REJECT
- ❌ 部署清單不完整 → REJECT
- ❌ 配置遺漏關鍵參數 → REJECT
- ❌ 缺少 citations 或無行號 → REJECT（HR-15）

【OUTPUT_FORMAT】
{{
 "status": "APPROVE|REJECT",
 "confidence": 1-10,
 "violations": ["具體問題"],
 "deployment_ready": true/false,
 "summary": "50字內"
}}
═══════════════════════════════════════
```"""
    }
}

# Helper function to get prompts for a phase
def get_phase_prompts(phase: int) -> dict:
    """Get developer and reviewer prompts for a phase"""
    return PHASE_PROMPTS.get(phase, PHASE_PROMPTS[3])

# Helper function to get role for a phase
def get_phase_role(phase: int, is_agent_a: bool = True) -> str:
    """Get agent role for a phase"""
    prompts = PHASE_PROMPTS.get(phase, PHASE_PROMPTS[3])
    return prompts["agent_a"] if is_agent_a else prompts["agent_b"]
