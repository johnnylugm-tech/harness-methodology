# Core Bug Hunt Report

方法論：dynamic Workflow，CRG MCP 導航（`build_or_update_graph_tool` full rebuild → `find_large_functions_tool`(Function/Class) + `get_architecture_overview_tool` 三路信號 → 依信號數排序取前 15 個 suspect → 每個 suspect 用 `query_graph_tool`(callers_of/callees_of) 導航 + 讀原始碼 hunt → 逐一 1-vote 驗證）。範圍：`core/` 下產品程式碼，排除 `test`。

CRG 掃出 **81 個 core/ suspect**，取信號數最高的 **15 個** hunt，產出 **25 個候選**，全數驗證為 **25/25 CONFIRMED**（0 PLAUSIBLE、0 REFUTED）。以下兩項在報告產出後另行人工核對原始碼確認屬實：`mutation_enforcer.py:569`（本倉庫自己的 `setup.cfg` 確實用逗號分隔 `paths_to_mutate`，且 `src_dir.exists()` 恆 False）、`auto_fix/__init__.py:135`（`classify()` 確實未轉發 `context.problem_type`）。

## core/quality_gate/mutation_enforcer.py（8 個發現，最嚴重的檔案）
- **:569** — `paths_to_mutate` 逗號分隔值被當單一路徑，`src_dir.exists()` 恆 False → mutation precheck 靜默永遠通過（**已核對，本倉庫自己中招**）
- **:656** — `mutmut results` 的 returncode 未檢查，subprocess 崩潰時 `if out:` 為 False → 誤判為「無存活變異」的乾淨通過
- **:831** — sqlite 讀取失敗與「零變異」用同一 `(0,0)` 回傳，無法區分損毀快取與真正零變異
- **:845** — workdir cache 未生成時，跳過清除 project-root 舊 `.mutmut-cache`，導致下游讀到過期分數
- **:303** — `testpaths` 多值設定被當單一路徑拼接，寫入 workdir 的 setup.cfg 損毀
- **:318** — `pythonpath` 多值設定同樣問題，且判斷失敗後保留原始（壞掉的）相對路徑，重現該函式自己標註要修的 Bug #106
- **:228** — `_copy_setup_cfg_to_workdir` 讀 project 根目錄 setup.cfg，未依 `_resolve_mutmut_workdir` 實際選定的 cwd 讀取巢狀設定

## core/quality_gate/constitution/profile.py + runner.py（4 個）
- **profile.py:439** — `_p3_security_kw`/`_p4_security_kw` 算出來後從未接上任何 PhaseProfile（死代碼），而 `runner.py:382` 仍無條件對 P3 索取 security 關鍵字，退回到含已聲稱移除詞彙的全域清單
- **profile.py:429** — 註解說 composite_threshold=80、maintainability「保留」，實際 P3 profile 是 30.0、只剩 correctness——註解與後面的 Bug #35 修正互相矛盾
- **runner.py:497** — `_scan_directory` 對 phase≥3 只 glob `*.py`，導致 P5-P8（驗證/品質/風險/配置階段，交付物是 .md）永遠掃到空集合，回傳 score=100.0/passed=True 的假通過（已用垃圾內容 md 實測重現，分數 100/100/100/100）
- **runner.py:384** — `_keyword_stuffing_penalty` 的 `is_markdown` 只傳給 correctness 維度，security/maintainability/coverage 仍用程式碼等級的嚴格門檻，對 .md 文件誤判關鍵字堆疊

## core/auto_fix/__init__.py（2 個）
- **:135** — `AutoFixEngine.fix()` 透過 `self.classify(context)` 重新分類，但只轉發 `source`/`details`，靜默丟棄呼叫端已設定的 `context.problem_type`；classifier 的 source-prefix fallback 常解析出錯誤的 problem_type，導致 dispatch 到錯的修復策略（`phase_hooks.py` 的 traceability 修復、`orchestration/__init__.py` 的 constitution 修復都中招）（**已核對**）
- **:236** — post-fix AST 守門只用 `files[0]` 算出的 `allowed_node_name` 去驗證所有被改動的檔案，合法的多檔修復會被誤判為不安全並回滾，但先處理的檔案改動已落地——造成部分回滾的工作區

## core/agent_spawner.py（2 個）
- **:166** — `TimeoutExpired`/非零 returncode 分支在 regression-guard 區塊（195-210 行）之前就 return，子 agent 逾時或崩潰後的破壞性編輯完全不會被檢查或記錄
- **:202** — `_log_dispatch` 在 regression-guard 覆寫 status 之前就寫入日誌，導致 `sessions_spawn.log` 永遠篩不出 `REGRESSION_GUARD` 事件

## core/phase_hooks.py（2 個）
- **:469** — auto-fix 後的 re-verify 呼叫 `check_traceability()` 但沒有重新套用 `TRACEABILITY_MATRIX.overlay.yaml`，導致已手動標記 VERIFIED 的 FR 重新出現，即使真正的缺口已修復，P5+ gate 仍卡住（作者自己在 470 行的註解承認跳過了 overlay，但理由不成立）
- **:746** — config-liveness 孤兒偵測用子字串比對而非精確比對，未宣告的 key 若是某個已宣告 key 的子字串就會被誤判為已宣告，漏掉該函式本該抓的情境

## core/quality_gate/spec_tracking_checker.py（1 個）
- **:336** — NFR 覆蓋率掃描（4c）例外處理是 fail-open，`nfr_pct` 停在預設值 100.0，與 sibling 4a/4b 的 fail-closed 不一致，讓損壞/未測量的掃描直接通過 Gate 2-4

## core/quality_gate/cross_artifact.py（2 個）
- **:198** — coverage 數字擷取正則抓「第一個」出現的百分比而非目標值，同檔案有多個百分比時會抓錯，可能觸發假的 CRITICAL 不符合
- **:196** — 正則要求數字前必須緊跟 "coverage"/"covered" 字樣，連函式自己註解舉的例子（裸百分比）都抓不到，導致捏造/過期的覆蓋率宣稱完全不受檢查

## core/quality_gate/phase_truth_verifier.py（1 個）
- **:56** — `self.results` 在 `__init__` 初始化為 `{}`，但 `verify()` 只建立區域變數，從未賦值回 `self.results`，導致 `to_fix_context()` 永遠讀到空集合（唯一有效呼叫路徑目前還沒有生產端呼叫者用到，屬於死掉但已確認會壞的方法）

## core/submodule_sync.py（2 個）
- **:244** — `_cli` 只捕捉 `SubmoduleSyncError`，`--message` 樣板若含未知 `{placeholder}` 會拋出未捕捉 `KeyError`，此時 submodule 已經 fast-forward 但 parent repo commit 沒做
- **:268** — parent-repo commit/push 錯誤處理只捕 `CalledProcessError`，但 `_run()` 固定 60 秒 timeout，逾時拋出的 `TimeoutExpired` 不是 `CalledProcessError` 的子類，會直接未捕捉往外拋

## core/quality_gate/bug_hunt_verifier.py（2 個）
- **:130** — `repro_test` 若非字串型別（如 int），`(root / repro).is_file()` 拋出未捕捉 `TypeError`；唯一呼叫端 `harness_bridge.py` 用寬泛 `except Exception` 吞掉並靜默跳過 Gate 3 override（本應是要 block 的）
- **:111** — `resolution = finding.get("resolution") or {}` 只擋掉 falsy 值，若 `resolution` 是非 dict 但 truthy（如字串），`.get("status")` 拋出未捕捉 `AttributeError`，同樣被上層寬泛 except 吞掉
