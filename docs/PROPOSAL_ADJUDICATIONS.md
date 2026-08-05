# Proposal Adjudications — 提案裁決賬本

> **協議**:任何新 Gap 分析報告或優化提案,執行前**先查此賬本**。若主張已有條目,且該條目的
> re-open condition 尚未滿足,**引用條目編號駁回,不重複查證**。只有 re-open condition 已滿足,
> 或主張與所有既有條目在技術內容上確實不同,才展開新一輪查證。
>
> 本賬本只存**判定**與 **re-open 條件**,不複製論證全文——論證與實測數字的單一真相來源(SSOT)
> 是各自的詳細出處文件;在此重複會製造雙源漂移,兩處各改一半就對不上。
>
> 賬本同時記錄「**採納**」與「**已建成(already-built)**」條目,不只駁回——前者是下一輪的施工
> 依據,後者防止未來報告把已完成事項再包裝成 gap 重新提出。

## Round 23(2026-07-28)— 一支涵蓋 Phase 1–8 的 workflow(`run-all.js`)

老闆令:基於 dynamic workflow 執行的確定性,產生一支涵蓋 Phase 1–8 的 workflow JS。硬前提:**不動原本 8 支**、**最終產出物與依序跑 8 支一致**(流程控制/中介產物不計)。可考慮省略重複執行,並可為它產出專用的 advance-phase 指令。

**定調(先於一切)**:量測(`sim_runner.mjs`,唯讀)顯示 P1–P8 全程 FR=20 為 **178 次 dispatch**,跨 phase 的可疑重複只有 15–20 次(約 10%)—— Round 22 已經把大宗的 per-FR 重複清掉了。**run-all 的價值是「一次啟動、無人值守的確定性」,不是省 dispatch。** 省下的 5 次是附帶效果,不是賣點;任何把 run-all 當成效能改善來提的後續報告,引用本條駁回。

| # | 主張 / 選項 | 裁決 | 出處 |
|---|---|---|---|
| R23-A | 用 playbook §5.6 的巢狀 `workflow()` 組合 8 支 | **否決**,三條證據:§2 的名稱解析是從 cwd **往上**找 `.claude/workflows/`,而 §13.2 的實際執行路徑是 `<project>/harness/.claude/workflows/`(submodule **子**目錄,不在 walk-up 上);taskq 唯讀確認其 `.claude/` 下根本沒有 `workflows/`;全 repo `await workflow(` 命中 0,sim 也沒 mock —— 未經任何驗證的 primitive。改為內聯生成 | `scripts/workflowgen/spec_runall.py` 模組 docstring |
| R23-B | 內聯 8 份 body 會撞名 / top-level return / 標題重複 | **採納**:每份 body 包成 `async function runPhaseN()`(作用域隔離,連 P1/P2 各自不同的 `buildBPrompt` 都不必調和);`return` 自然變成 runner 回傳值;標題一律加 `P<N> · ` 前綴,來源是各檔自己的 meta 區並斷言命中 | 同上 |
| R23-C | 檔案大小 | **本輪唯一硬邊界**。playbook §4:>524288 bytes runtime 直接拒絕解析。逐字內聯 ~410 KB(78%),故剝除內聯 body 的純註解行 → 302 KB(58%)。WHY 完整保留在 8 支同源檔與生成器裡。另加 `RUNALL_MAX_BYTES` 餘裕 ratchet:**逼近時的正解是縮 prompt,不是調高數字** | `tests/test_workflow_js_conventions.py` |
| R23-D | 為 run-all 產出專用 advance 指令 | **採納 `advance-phase --push`**(老闆裁定)。push 歸位到製造那個 commit 的指令裡;預設關閉,8 支輸出位元組不變;唯一消費者是 run-all。push 失敗**不回滾** —— commit 已成立,撤銷它是拿掉durable work 去換一個暫時性網路錯誤 | `cli/phase_cmds.py::cmd_advance_phase`、exit 28 |
| R23-E | C 級減法:6 支的 Sync box 折進 `--push` | **採納**。實測 178 → 173(−6 sync,+1 cursor)。P3(自訂 retry + MANUAL_REQUIRED fallback)與 P8(另驗 tag 到 origin)**保留** | `sim_runner.test.mjs` §12(兩向精確差集) |
| R23-F | C 級減法:phase 2–8 省略 `validate-handoff` | **撤銷(我自己提的選項文字有誤)**。`grep -rn "_validate_handoff"` 查證:唯一呼叫者就是 `cmd_validate_handoff` 自己,**advance-phase 完全不跑它** —— 拿掉等於刪掉一個真檢查。形狀與 R22 站2 相同(檢查只活在 prompt 層),正解是歸位進 advance-phase,**但那會改變既有專案的 advance 行為**(現在 advance 成功、下個 phase preflight 才擋的情境將改成 advance 直接擋),不在同一輪塞第二個會擋住既有專案的 CLI 變更 | 待議,見下方 re-open |
| R23-G | C 級減法:非首個 phase 省略 ENTRY-CHECK | **未實作,待老闆裁決**。我給的兩個前提在站0 都被證偽:`last_gate` **不單調**(`cli/gate_cmds.py:2279` 每次 finalize-gate 直接覆寫,含 per-FR Gate 1)、`quality_complete` **會回退**(`cli/gate_cmds.py:1757`,finalize-gate 的 commit 沒 land 時)。剩下的論證更窄(單次執行內兩點之間沒有任何 finalize-gate 會跑那個 gate)且**省 0 dispatch** | 本條 |

**兩個順帶抓到的活 bug**(都不是計畫預期,都由 run-all 暴露):

1. **`test_node_check_syntax` 是死守衛**:`node --check <file>` 對沒有 package.json `type` 的 `.js` 以 CommonJS 解析,撞到 `export const meta` **仍回 0**。對 8 支 workflow 從來不可能失敗。已改成用 runtime 實際的包裹方式解析 + 負控制。
2. **`js_lint` 掃描器不認得 regex literal**:`payload.replace(/'/g, …)` 讓它以為那個 `'` 開了字串,之後全部誤判到引號數再平衡為止。8 支檔案是**碰運氣**再平衡的;run-all 接起來後不再平衡,`os.path.getsize` 冒成 `path.*` 違規。危險在於 `comment_line_numbers` 會**刪除**它分類的行 —— 掃描器脫節時一行含 `https://` 的 prompt 可能被當註解刪掉。本次脫節走安全方向(少剝 233 行註解),但暴露是真的。

**誠實邊界(必讀,不可含糊)**:sim 的逐 phase 對照證明的是 **dispatch 序列**一致,**不是最終產出物位元組一致**。兩者之間隔著「同樣的 prompt 送給真 LLM 會做同樣的事」這個假設 —— 那正是這套框架二十多輪在對抗的東西。run-all 的等價性由**結構**(同一組生成器、同一組 prompt、同一組 CLI 指令)保證,由 sim 在 label 層級鎖定,最終產出物一致只有 live E2E 能證。

**re-open 條件**:
- R23-A:若 Claude Code 官方文件確認 `workflow()` 可吃絕對路徑(而非只吃 name),重新評估組合式方案。
- R23-C:若 run-all 觸及 `RUNALL_MAX_BYTES`,先做 prompt 減法;連續兩輪都只能靠調高數字通過,則重新評估「一支檔案裝八個 phase」這個形狀本身。
- R23-F:下一輪可單獨處理 `validate-handoff` 歸位 —— 須先唯讀確認 taskq / integration-test 在各自 phase 邊界上 `validate-handoff` 皆 exit 0,否則歸位會擋住既有專案。
- R23-G:老闆裁決要做時即可實作(`render_entry_preflight` 加 `entry_check_optional`,預設 False 保位元組相等)。

---

## Round 22(2026-07-28)— 與 FR 無關的工作被放進了 per-FR 迴圈

老闆令:dynamic workflow 的執行已具備確定性且有效降低 agent 作假,據此對 workflow JS 做**減法工程** ——(1) 減少重複執行 (2) 減少無效或低效的流程檢查 (3) 簡化 advance phase。前提:**不得降低對執行專案的軟體品質**。

實測(`sim_runner.mjs`,唯讀):20 FR 的 P3–P8 一輪要 **203 次 sub-agent dispatch**,其中 80 次(41%)是 ORCH-POST,且與 FR 數 1:1 線性增長。根源:

> **一個「與 FR 無關」的動作被放進了 per-FR 迴圈,而迴圈的每一圈是一次完整的 sub-agent dispatch。**

**老闆兩項裁定**:(1) **不加配置開關** —— 只做可證明行為等價或保護更強的結構修正;加開關等於把已證明的冗餘保留成永遠沒人開的死配置(R9 站0 才剛清掉 983 行殭屍設定)。(2) 四站全做,含跨 CLI 層的歸位。

| # | 主張 | 判定 | 一句證據 | Re-open condition |
|---|------|------|----------|---|
| R22-A | ORCH-POST 每 FR 一次,其中 `amend-sab` 沒有 FR 語意 | **採納,已做** | `cmd_amend_sab` 根本沒有 `--fr-id` 參數,且 `sab_amender.amend_sab` docstring 自述 "running twice adds nothing on the second call" —— N-1 次是同一個全專案問題重問。taskq(5 FR)的 `sessions_spawn.log` 留下 **35 筆 `tool:amend-sab`**。無人察覺是因為該步是 fire-and-report(無 verdict gate),浪費一次 dispatch 產生不了任何失敗訊號,而先前所有 sim 情境都只跑 1 個 FR ——per-FR 與 per-phase 在那裡成本相同 | 若未來 `amend-sab` 取得 per-FR 語意(接受 `--fr-id` 且結果因 FR 而異),此收斂需重新評估 |
| R22-B | 入口 Manifest Integrity 與 `run-phase` 的第一項是同一個函式 | **採納,已做** | `check-manifest-integrity` = `PhaseHooks.preflight_manifest_integrity()`,而 `PREFLIGHT_CHECKS[0]` 就是它,由前一個 phase box 的 `run-phase` 執行 | 若 `PREFLIGHT_CHECKS` 不再包含 manifest_integrity,入口檢查需回歸 |
| R22-C | advance 迴圈每輪的 integrity 檢查放錯層,人手動跑完全不受保護 | **採納,已做** | `_advance_prechecks` 全文無 integrity 檢查,而 advance-phase 會 commit `.methodology/` 全目錄。`test_workflow_plan_alignment.py` 的 registry 自己就寫了 "A human running the plan by hand has no equivalent step",把這個缺口當成合理的 runtime-only 發明歸檔。歸位到 CLI(exit 27)同時消滅 dispatch **與**缺口 | 若出現第三個 advance-phase 不覆蓋的 commit 路徑,該路徑需自己的檢查(目前兩個:phase3 Gate-2 迴圈、phase8 Final Push) |
| R22-D | ctx-check 驗證的正是 load-ctx 自己會驗的東西 | **採納,已做** | 兩者都跑 `json.load(ctxFile)`,後者的失敗集合是前者的超集,兩種被區分的結果(檔案缺失 / 不可解析)處理方式相同。phase3 更甚:Fix D 的 `attempt === 1 ||` 在讀取 verdict **之前**短路,所以最關鍵的那一輪,probe 跑了、答了、被丟棄。R20 元模式(多層檢查同一來源)的又一實例 | 若 load-ctx 的命令改為吞掉失敗(加 `\|\| echo`),需恢復獨立探測 |
| R22-E | poll 首輪固定睡滿,不管命令 1 秒就結束 | **採納,已做** | R20 站1 之後 `run-env-check` 在 source 指紋未變時秒級返回(確定性驗證,不 spawn sub-agent),固定 60s 首輪等於每個 phase 白等一分鐘;delta-fastpath 的 30s × N 循序更貴(20 FR ≈ 10 分鐘)。改 backoff,總上限不變。full loop 的 30s/cap60 **不動** —— 那條路真的在跑 TDD | 若 backoff 導致 agent 在 CLI 寫完 log 前誤讀,需重新評估(判定來源仍是 `kill -0` 的 DONE 與最終 `RC=` 行,不是 log 內容) |

**成果(sim 實測,同一測床同一情境)**:

| | FR=5 | FR=20 |
|---|---|---|
| 站0 基線 | 113 | 203 |
| 站4 之後 | 80 | **110** |

P4/P5/P6/P7/P8 現在**完全與 FR 數無關**(14/11/11/11/12)。P3 仍隨 FR 增長(21→51),每 FR 2 次 —— TDD orchestrator 與其獨立 verify,那是真工作,不是重複。

**本輪唯一「等價性由 prompt 保證而非由結構保證」的一項**:R22-A 收斂後,`spec-coverage-check` 從「N 個獨立 agent 各跑一次」變成「一個 agent 在 bash 迴圈內跑 N 次」。守衛能證明 dispatch 數與 `$FR` 迴圈存在於生成輸出,證明不了 agent 真的跑滿 N 圈。該步是 fire-and-report,不影響 gate 判定,故接受;prompt 明寫 `|| true` 續跑並要求回報全部輸出。

**計畫外查證,誠實記錄不修**:P4 的 ORCH-POST 跑在 Per-FR Delta,**早於 bug-hunt**,所以 HUNT-RESOLVE 新增的模組本來就不在它的覆蓋範圍內(Gate 3 的 Architecture Amendment Protocol 兜底)。收斂不改變這件事,搬位置是改行為不是減法,故不做。

**計畫修正兩處**(執行中發現,非事後補述):(1) 計畫說 P5/P7/P8 的 plan 側走執行期模板,實際 `phase_tasks.py:719/901/1014` 直接呼叫 `_fr_carryforward_steps`,漏改會讓 amend-sab 從那三份 plan 完全消失 —— 四個插入點而非一個。(2) 計畫沒提 `spec_phase8.py` 的 Final Push 也有 integrity 檢查;它不是 advance-phase 路徑,故保留(並把誤導的 `advance-integrity-r` 標籤改名為 `finalpush-integrity-r`)。

## Round 21(2026-07-27)— 讓判定用框架自己算出的數字,而不是被判定者的自報值

老闆令檢視 taskq **P6~P8** 的執行紀錄 + harness git history,隨後令針對所提全部問題提「正解 not workaround」。

實測:P6/P7/P8 共 **24 次 dispatch、0 失敗**,dispatch 層已健康;問題全部在**證據層**。四個問題共享一個根源,是 R20 元模式在**時序**面的表現:

> **要求被判定者自己產生「用來判定他」的證據,且判定發生在框架算出真值之前。**

| # | 主張 | 判定 | 一句證據 | Re-open condition |
|---|------|------|----------|---|
| R21-A | DA-waiver 的「已及格就跳過」安全閥從未生效,且會 crash | **採納,活傷口** | 安全閥兩次都死,方向相反:Round 30 前讀 `tool_score`(不存在)得 `0.0 >= inf`;Round 30 後讀 `target`(也不存在,來自 `score.py` 的**另一個檔案**)得 `score >= inf`。schema、taskq 全 14 維產物、`harness_bridge.py:2235`、`gate_cmds.py:1698` 四方一致說 `threshold`。Round 30 的測試綠是因為 fixture 照 `score.py` 形狀手建——**R19「規則與 fixture 同源」第五次現身**。同一行還會 crash:`float(_bd.get("score", 0.0))` 遇 JSON null 拋 TypeError(`harness_bridge.py:2235` 正上方就有針對此坑的註解)。**根源不是欄位名而是順序**:裁決跑在 CRG 之前,只能讀 agent 自報的 null,而框架實算 **100.0** | 若未來出現「必須在 finalize 前知道 waiver 結果」的需求,需先證明該需求不能靠框架分數滿足 |
| R21-A2 | waiver 可歸零**任何**維度的門檻,NFR floor 的 "not waivable" 只是散文 | **採納** | `harness_bridge.py:2488` 的 `dataclasses.replace(d, threshold=0.0) if d.name in da_waivers` 無維度白名單;`evaluate_dimension.md:468-471` 與 `sab_parser.py:150` 都寫 not waivable,無程式碼執行。taskq SAB 有四個 NFR 維度(security 80 / error_handling 80 / readability 80 / performance 75),後三者同時是 Tier-3 DA 維度,evidence 每輪已備妥,只差一個 key。**老闆裁定:只允許 `architecture`** | 若出現第二個 CRG-only 維度,`CRG_ONLY_DIMENSIONS` 隨之擴充(白名單即該集合,一處定義) |
| R21-B | gate result 的 schema 從未被載入,已漂移成描述不存在的產物 | **採納** | 全 repo 只有 `b_review.schema.json` 有 validator。`harness_gate_result.schema.json` 零載入,於是 required 一個沒人寫的 per-dimension `passed`、把 CRG-only 維度的 `null` score 定為 number、required 真實產物為 null 的 `overall_score`/`meets_target`、漏掉每筆 breakdown 都有的四個欄位。守它的測試是 schema 自己 required 清單的第二份拷貝——只能證明檔案沒被改。**這是 R21-A 的病因**:沒有可執行契約,消費者只能猜欄位名 | 若 schema 與真實產物再度分歧而 parity 測試未紅,表示 fixture 來源退回同源,需重新取真實產物 |
| R21-C | `sessions_spawn.log` 權重 0.20 計入 Phase Truth,而它 agent 可寫且 gitignored | **採納** | 三方矛盾:`SKILL.md:317` 記 HR-10 **REMOVED**(理由正是「agent-writable, not tamper-evident」)、`SAD.md:225` 記 **MUST**、程式碼仍在兩條活路徑上計分。taskq P6 六筆手寫紀錄(session_id 非 UUID、無信封、duration 0、整秒時間戳、task 存結論),`role=architect`+`phase=6` 恰好命中 A/B 分支,寫入時間在首個 Gate4 PASS commit 前 45 秒。A/B 分支另有獨立缺陷:掃全 log **不分 phase** | 若要恢復任何 spawn-log 計分,前提是先有不可由被評者寫入的來源 |
| R21-C2 | doctor 的 spawn-log 真實性診斷應否計分 | **明確不計分(設計決策)** | 偽造者能寫紀錄就能寫信封欄位。把它變成 gate 項等於在更外一層重建 R21-C 的缺陷。定位=事後發現,非事前防止 | — |
| R21-D′ | (計畫外查證)SAB 的 `gate_score_overrides` 是否被 waiver 繞過 | ~~前提不成立,不修~~ → **本條於 Round 24 站4b 撤銷,見下方 R24-RETRACT-1** | ~~grep 全 repo:零消費點~~ **這個 grep 的判定是錯的** | — |

> **順帶查出**(由站2 的 schema 執行化逼出,非計畫項):三個測試 fixture 一直在謊報自己的形狀。`test_handover_generator` 的 gate1 fixture 用 `dimensions` 數字 map,而 dims builder 讀 `breakdown`——那三支測試一直在**零維度證據**下跑 finalize_gate;補上真實 breakdown 後又觸發 identical-scores 反造假斷路器(舊形狀從未走到那裡)。`test_harness_bridge_highs2` 的 gate3 fixture 自稱 "a valid gate3 result" 卻缺三個 required 欄位。

> **本輪第三次遇到「既有測試把缺陷釘成規格」**(前兩次:R19 站2、R20 站1)。這次有一支特別值得記:`test_anti_fabrication.py::test_ab_coverage_rejects_developer_only` 是一支**反造假測試**,而它斷言的檢查讀的是造假者自己能寫的檔案。

## Round 20(2026-07-27)— 把「多層檢查、同一來源」拆成真正獨立的來源

老闆令檢視 taskq **P4~P5** 的執行紀錄 + harness git history,隨後令針對所提全部問題提「正解 not workaround」。

實測:P4/P5 的 developer dispatch **14 次 0 失敗**(P3 是 27.5%),dispatch 層顯著改善;但同期 harness 仍被改了 **7 次**(Round 20-25),模式與 P3 相同——全部由 taskq 實跑觸發。四個問題共享一個**元模式**,且已跨三輪反覆出現:

> **增加檢查的層數,但所有層讀同一個來源。** 層數給人多重驗證的錯覺,實際的獨立來源只有一個。

| # | 主張 | 判定 | 一句證據 | Re-open condition |
|---|------|------|----------|---|
| R20-A | env-check 的 `ready` 由 LLM 自由判定,三層檢查(agent 自報 / exit code / workflow cross-check)全讀同一欄位 | **採納** | 37adc43 自帶對照實證:同一 env var、**同一份未變的 project state**,一次 `optional_missing`(ready=true)、一次 `required+present:false`(false FAIL)。`_verify_env_check_claims` 的 docstring 自陳只驗 `present:true` 單向,**該 bug 的兩種分類都在盲區**。對比 gate 評分鏈有 `harness_bridge.py:970` 的 S4 獨立工具交叉驗證——env-check 無對應機制。觸發 **R16-3** 的 re-open condition(當時明確限定「範圍僅 gate 評分鏈」) | contract 的分類若被發現長期錯誤而無人察覺,表示「進版控即可審查」的前提不成立,需改為每輪強制複核 |
| R20-A′ | (計畫前提修正)「分類完全由專案文件決定」 | **部分為假,已據實調整設計** | `evaluation_prompt()` 的 CLASSIFICATION RULE 第一條是「Exported in current shell?」——環境狀態混進了分類。故 contract 只固化**文件決定的語意類別**(`mandatory`/`has_default`/`dev_opt_in`),exportedness 每次探測。這更貼 37adc43 的實際病灶(dev-opt-in flag 被誤判為 mandatory) | 若 prompt 的分類規則再度混入環境狀態,需重新劃線 |
| R20-B | 路徑 SSOT 的 lint 只守 phase 目錄,test/src 目錄完全在守衛外 | **採納,且查出第三個活傷口** | Round 22(`4aa6ff2`)、Round 25(`7af95ba`)是同一 bug 類;後者 commit message 自承「同樣的修法**已在** `spec_tracking_checker.py:391` 證明正確」。本輪查出第三個且最嚴重:`core/auto_fix/strategies.fix_low_coverage` 不只讀錯位置,還 `mkdir` 在錯位置寫 stub 並回報「已修」——活路徑(`STRATEGY_REGISTRY` 派發) | 若 lint 的 allowlist 成長超過 ~8 筆,表示 ProjectLayout 的 accessor 不敷使用,應擴充 accessor 而非放寬 allowlist |
| R20-C | 里程碑 commit 無冪等性,重跑必產生無資訊 commit | **採納** | taskq P4 三個同 subject 的 `feat(P4-pre-gate3)`,最後一個只改兩個時間戳,且**全部在 Gate 3 PASS 之後**。病因是 HANDOVER 的 `**Generated**` 行保證每次都有 diff;`git_strategy._commit` 早已正確處理「無變化」。與 R18 站3 的 attestation 同形——**是我當輪未掃的同形兄弟** | 若某 milestone 的價值就在「留時間點記號」,需為該類型明確豁免 |
| R20-D | `gate_timestamps` 的 skip 寫入使 doctor 的兩個「獨立」通道實為一個 | **採納,但定級為設計弱點非活傷口** | taskq P4 有 5 筆 3.1 秒內寫入、零 dispatch 的 row;doctor 用 `has_sentinel or fr_key in ts_frs`,而 skip 分支的前提就是 sentinel 存在。**目前無法據此偽造**(skip 前提要求真實證據),問題在 `or` 讓讀者與未來的修改者誤以為有兩個獨立來源 | 若未來出現不依賴 sentinel 前提的 timestamp 寫入路徑,此弱點立即升級為活傷口 |
| R20-E′ | ~~`core/quality_gate/spec_coverage.py:23` 的 `project / "tests"` 是殘留同形兄弟~~ | **證偽,撤銷** | `_get_test_directories` 刻意收集 root 與 canonical **兩處**並回傳 list——是聯集不是選擇。`build_traceability.py:67-76`(刻意 fallback + warning)、`generate_fr_mapping.py:49-52`(候選清單)同理。Round 25 修完後**無殘留活傷口**,缺的是防止下一次的機制 | — |
| R20-F′ | ~~`sessions_spawn.log` 有時間倒序~~ | **證偽,撤銷** | 實測 126 筆**零倒退**。我先前是按 phase 過濾後的顯示順序誤讀 | — |

> **元模式的三次現身**:R19「規則與其 fixture 同源」→ R20-A「三層讀同一個 `ready`」→ R20-D「兩個通道其一是另一個的影子」。共同的判準:**問「這兩個來源能不能彼此矛盾?」不能,就只有一個來源。**

> 順帶修復(由本輪測試逼出,非計畫項):`core/harness_provenance.enforcer_sha()`(R19 站3)docstring 宣稱 "never raises",但 `except (OSError, SubprocessError)` 接不住替身 `subprocess.run` 回傳物件缺 `.stdout` 造成的 `AttributeError`,例外逃進了 gate 指令。已改為 `except Exception`——對「絕不可拋」的函數,窄 except 本身就是缺陷。

## Round 19(2026-07-26)— 打開封閉的驗證迴路:讓真實 run 的失敗有辦法變成執法

老闆令檢視 taskq **P3 的執行過程與紀錄**、對照 harness git history,探討結構性問題,隨後令針對所提全部問題提「正解 not workaround」。

實測 `sessions_spawn.log` 91 筆:69 次 developer dispatch、**19 次失敗(27.5%)**、失敗吃掉 1.30h 掛鐘(全程 3.75h 的 35%)。四項查證屬實的問題共享同一病理:**驗證的輸入全部由寫程式碼的人自己產生,真實執行產生的證據沒有路徑進來變成執法**。三項上一輪報告提出的主張經查證**證偽或降級**,一併記錄——賬本的用途正是讓撤銷與採納一樣可追溯。

| # | 主張 | 判定 | 一句證據 | Re-open condition |
|---|------|------|----------|---|
| R19-A | 分類器對真實失敗語料 100% 誤判(`Stream idle timeout` ×12 / `session limit` ×1 全歸 EXECUTION_ERROR → 路由進 CODE-FIX) | **採納,且範圍比報告時更大** | 病灶不只是 regex 少兩條。查證另發現:`_is_missing_required_commit` 讀 `output` 而 log 寫 `error_output`、`_is_semantic_noop` 讀的 `inner_status` 從未落盤——**6 條規則有 2 條對真實資料結構上不可能命中**,自 R16 建立起從未觸發過。兩者的 fixture 都用了規則作者假設的欄位名,所以測試恆綠。另 `unclassified_pct` 分母含成功 entry(91 筆中 72 筆 `complete`),95.6% 與 R16 記的 82.1% 皆為同一算術錯誤;失敗域真值是 15/19=78.9% | corpus 匯入新語料而 ratchet 紅時,補規則而非抬 ratchet;若真實失敗形態長尾極長(每輪都是全新字串),則轉向「UNCLASSIFIED 當一等公民路由、不追求分類完備」 |
| R19-B | 失敗 dispatch 的成本/turns 完全不可見(2/19 有 cost vs 成功 50/50) | **採納** | 非資料不可得:失敗路徑的 `_extract_dispatch_error` **已經 `json.loads(stdout)` 成功**(taskq 的 `subtype=success API Error:` 正是它的產物),cost/usage 就在同一個 dict,而 `_extract_envelope_metrics` 只在 `returncode == 0` 分支被呼叫。且既有測試 `test_spawn_envelope_absent_on_error_path` 把「非零退出永無信封」**寫成了斷言**,把缺陷釘成規格 | 下次 run 的 log 若顯示失敗 entry 仍缺 cost,表示 CLI 在串流中斷時只吐部分信封,需另查 |
| R19-C | gate 判定不記錄執法者版本,taskq Gate 2 同分數 96.7 一次 BLOCK 一次 PASS 無跡可尋 | **採納** | `grep harness_sha\|framework_version\|harness_version` 掃 `cli/gate_cmds.py` + `core/quality_gate/` 零命中,倉庫亦無 VERSION 檔。兩個中間的 fix(7c60859/97cd298)讀完確認**是真修復不是放水**——問題不在修得對不對,在產物上無法回答「這次判定用的是哪個執法者」 | 若 submodule 以外的佈署形態出現(pip 安裝等),`enforcer_sha()` 的 `harness_root()` 推導需重驗 |
| R19-D | `compute_trace_dimension.passed` 與消費端 `score >= threshold` 兩處計算同一判定,等價性無守衛 | **採納,且查出 7c60859 的修法仍破裂** | 8 個既有 override 測試無一測等價性。直接斷言後找到反例:**失敗的 component 不是綁定 min 的那個**時規則失效——`4a=99.9`(一個 FR 沒測到,bar 100 FAIL)/ `4b=90`(bar 60 pass 且為 min)→ `threshold_effective=60` → `90>=60` 報 PASS。參數化下 **55 個組合**破裂。目前無事故是因 `cli/gate_cmds.py` 先用 `passed` 擋住才輪到 bridge 用分數——**正確性靠執行順序保命** | 若未來新增第 4 個 traceability component,`resolve_threshold_effective` 的「取最高失敗 bar」證明需重做 |
| R19-E′ | ~~Gate 閾值階梯倒掛(Gate1 100/100 嚴於 Gate2/3/4 90/85)~~ | **證偽,撤銷** | `gate1_per_fr.yaml` 是 `scope: single_fr`(量單一 FR 的檔案),`gate2_p3_exit.yaml` 是 `scope: full_phase`(量全庫)。**測量域不同,閾值不可比較**;「同一份程式碼 Gate1 擋、Gate2 放行」不成立,「taskq 進 P5 會被擋」的預測隨之作廢 | 若未來兩個 gate 的 scope 改成相同,倒掛就成立,需重議 |
| R19-F′ | ~~`dispatch_attempt` 恆為 1 = 重試盲區~~ | **證偽,撤銷** | `_STRUCTURAL_FAILURE_SIGNATURES` 是空 tuple(R12 站0c 以生產證據清空),`is_structurally_broken()` 恆 False,spawn 層**依設計不重試**;上層 fix-loop 的重試記在 `retry_round`(taskq 實測 4 筆=1、1 筆=2)。恆為 1 是正確行為 | 該 registry 若因新證據重新填入,`dispatch_attempt` 才會有多值 |
| R19-G′ | 專案端 workaround 先落地、harness 正解事後補、無回收機制(taskq `conftest.py` 09:45 vs harness `d35beeb` 16:39) | **降級 — 現象屬實,不建機制** | 正解形狀是跨 repo 通知,成本遠高於收益;且「專案在框架修好前自救」本身合理。記錄於此供未來對照 | 同類 workaround 在多個專案重複出現 ≥3 次,且可證明造成實際誤導時 |

> 方法論收穫:**「規則有測試」≠「規則會執行」**。`test_failure_modes.py` 的完備性 meta-test 保證每條規則有 hit/miss fixture,而 fixture 由規則作者撰寫——兩邊出自同一顆腦袋,所以 2 條死規則、1 個錯分母、1 個把缺陷寫成斷言的測試可以同時存在而全綠。本輪的守衛把權威換成**真實語料**(`tests/fixtures/failure_corpus/`):規則讀的欄位必須出現在真實 entry 中,否則測試紅。

## Round 18(2026-07-25)— 病灶歸位:把補丁層的修復搬回它該在的層

老闆令 review `0e164ee..HEAD`(25 commits,其中 16 個是 Round 17 之後的新批次)並探討結構性問題,隨後令針對建議優先序全部四項提「正解 not workaround」。診斷:16 個新 commit 中 9 個實質修復**全部由 taskq 真實 E2E 跑動觸發**,5487 個測試 + 72 個 guards 攔截率 0;四個問題共享同一病理——**修復被放在症狀出現的層,而不是病因所在的層**,所以修復本身製造新 bug、漏掉同類副本、或永遠無法收斂。詳細出處:各站 commit message + `tests/test_gate_threshold_docs_parity.py` / `tests/test_attestation_idempotence.py` / `scripts/workflowgen/js_src/sim_runner.test.mjs` §6-7。

| # | 發現 | 判定 | 一句證據 / 封口手法 | Re-open condition |
|---|------|------|----------|---|
| R18-A | 35214a0 的 orphan-module fold 把「無 FR 宣告的模組」塞進每個 FR 的 Gate 1 覆蓋 scope | **採納 — 移除(站1);原設計前提兩半皆假** | 探針實證兩個活 bug:套件式宣告(`taskq.executor`,正是 cov_utils 上方 Fix III 支援的形狀)的子模組 dotted name 不等於宣告值 → 被判孤兒塞進別的 FR;無 `fr_module_traceability` 時 claim set 為空 → 全部是孤兒 → Priority-2 的 import-based 收斂變死碼、退化為全庫。**前提本身錯**:SAB.json `layers` 已宣告全部 7 個模組(store→persistence,config/models→foundation),且 `gate2_p3_exit.yaml` 是 `scope: full_phase`,共享模組在 **Gate 2** 就被全庫覆蓋率測到,在 advance-phase **之前** | 若未來 Gate 2 改為 FR-scoped,共享模組的檢查點需重新設計 |
| R18-A′ | 補償方案「新增 `preflight_module_ownership` 強制每個模組有 FR 主人」 | **撤銷 — 計畫核准後查證推翻** | taskq 的 config/models/store 是設計上的基礎設施層,天然不屬於任何單一 FR;強迫歸屬違反架構現實。且時序上,FR-01 跑 TDD 時 FR-02 尚未寫完的共享模組本就無法對 FR-01 的 per-FR checkpoint 負責。原計畫的 1b/1c 因此**不實作**(計畫 Self-Review 已預留此分支) | 若實測出現「模組完全不在 SAB.layers 也不在 traceability」的真孤兒,再議 |
| R18-B | gate 閾值在 prose 中的手寫副本無守衛(R17-A 母體的未覆蓋面) | **採納 — 已封(站2)** | 35214a0 把 Gate 1 的 linting/type_safety 由 90/85 改為 100/100,只更新 yaml + 2 份副本,**留下 5 份仍寫 90/85**(`docs/P{3,5,7,8}_SOP.md` + flowchart)。R17 的 `PROMPT_GATE_RULES` 只綁 GATE1 prompt 一個消費點。→ `gate_thresholds.load_gate_thresholds()` 讀執法用的 yaml、不存值;`sab_parser` 兩個常數改為 yaml-derived(gate4 實測 15 dims 完全等值,gate1 順帶補上 `architecture_constraints`);`test_gate_threshold_docs_parity.py` 三類 registry + 完備性 meta-test。反例驗證:改 yaml 一個值 → 6 個測試變紅 | 擴大到 evaluate_dimension.md 等 LLM 判斷面 prose 時另行查證 |
| R18-C | attestation 刷新迴圈結構上不可能收斂(6/16 commit 是純儀式) | **採納 — 已封(站3)** | 6 次 `chore: refresh attestation post-pull` 的 `content_sha256` **六次全同**(932e6844…)= 矩陣一次都沒真的變過;`_trace_dirty_state` 用 mtime 而 git 不保存 mtime,清除它要改寫檔案 → 改 `git_sha` → 真 diff → 必須 commit → `git_sha` 又過期。且 `git_sha` **零比對消費者**(3 處 print,0 處讀)。→ `write_attestation` 內容未變時只 `os.utime`;`_trace_dirty_state` 判髒時先重算內容(實測 0.43s,僅走不快樂路徑)再決定擋不擋,失敗一律 fail-closed。**自證**:站3 的 commit 本身未跑儀式即通過 | 若 `build_attestation` 成本顯著上升(大型專案實測 > 3s),慢路徑需改為增量指紋 |
| R18-D | `render_gate_loop` 的 gate PASS 判定在模擬測床零覆蓋 | **採納 — 已封(站4)** | 探針實證:precheck 的 VERDICT_SCHEMA 落到 happy synthesizer 的 `{pass:true}`,**所有既有場景都跳過 round loop**,從未執行過 gate 判定。這正是 9b5f7cf 用 `quality_complete`(SSI 分數寫入、在 Phase Truth 之前、且永不回退)當完成信號、兩個 commit 後被 64d8ea9 推翻的位置——期間 `--check` / golden / 52 測試對**兩個版本都綠**,因為它們比對的是生成器與生成物,看不見前提錯誤。→ sim_runner 補 13 個語意場景,floor 21→33 | 新增 gate 或改變 PASS 條件時擴充對應場景 |

> 附帶修正(非裁決項):64d8ea9 把判定改讀 `state.json.last_gate` 後,`render_gate_loop` 的 PASS 日誌仍印 `manifest qc=true`——與 83ed438 修的是同一類(日誌宣稱程式已不再做的事),由該修復本身引入。站4 一併更正。

## Round 17(2026-07-24)— 15-bug 結構診斷:prompt↔gate 漂移母體的系統性封口

老闆令 review `0197e89..HEAD`(15 個 bug-fix,PR #15–#25)並探討是否有結構性問題;隨後令針對發現確認根源、提修復、不破壞共通性。診斷:8/15 bug-fix 是同一母體(prompt↔gate 規則雙重編碼、無 parity 守衛)的不同發作點,修法逐點反應式(#18B 是 #15 已修 allowlist 的殘留漂移=per-site 綁常數,非結構封口)。四發現以既有共通模式(registry+完備性 meta-test / R13 ledger / R12 自我懷疑 / R15 golden)系統性封口。詳細出處:各站 commit message + `tests/test_prompt_gate_parity.py` / `tests/test_fr_step_no_progress_self_doubt.py` / `tests/test_detector_abstention.py` / `tests/test_fr_prompt_snapshots.py`。

| # | 發現 | 判定 | 一句證據 / 封口手法 | Re-open condition |
|---|------|------|----------|---|
| R17-A | prompt↔gate 規則雙重編碼、無 parity 守衛(母體,8/15) | **採納 — 已封(站1)** | GATE1 prompt 手寫的 threshold `90/85/80` 是 `gate1_per_fr.yaml` / `sab_parser._GATE_DIMENSION_STANDARD` 的第三份 → render-from-SSOT 消滅之(byte-equal,站0 snapshot 證);`test_prompt_gate_parity.py` 宣告式 `PROMPT_GATE_RULES` registry + 完備性;`test_no_unbound_hardcoded_threshold_in_prompt` 是母體封口(未來 `max(NN.0,...)` 硬編 threshold author-time fail) | 擴大 parity 到 bug-hunt/peer-review 等其他 prompt↔工具面時另行查證 |
| R17-A′ | overall_score 權重 prompt `0.33/0.34` vs gate YAML `0.25×4` 不一致 | **降級記錄 — 不強改** | gate1 無 CRG override → `harness_bridge.py:2399` 採用 agent 自報 overall_score,prompt 是 de-facto 權威、YAML weight 是 gate1 死配置;統一牽動 gate2/3/4 fallback + agent output 契約,超出 Surgical。`test_overall_score_weight_asymmetry_is_pinned` 鎖現狀 | overall_score 的 dim 集合(是否含 architecture_constraints)被正式裁定時 |
| R17-B | 確定性不可逃脫 BLOCKED 迴圈無偵測(原設計:S4/spec_cap 矛盾斷路器) | **縮小 — 原設計前提不成立(站2)** | 2a 實證:S4(`_run_harness_cross_validation`)只 return violation 訊息、不 return 它算的 coverage 值;`_capture_tool_snapshot` 跑 `pytest` 無 `--cov`;#20 具體 bug 已修 + run-fr-step 已有 `no_progress≥2` 斷路器。→ 縮小為 no-progress BLOCKED 點 `record_degradation`(補觀測黑洞)+ gate-bug 自我懷疑通道(R12 站3a) | 要做真矛盾偵測需先讓 `_capture_tool_snapshot` 加 `--cov` 或 S4 落地 harness_score(動熱路徑,ROI 待證) |
| R17-C | 偵測器猜測 / 不可清除 finding | **立則-only — 零新活傷口(站3)** | 3a 審計 detection/+quality_gate/ 9 個 break/next(iter) 站點全健康(fail-fast/存在性/前綴剝除/表格邊界);唯一猜測病(`_resolve_import_layer`)#23 已修已測、#24 同族已修。`test_detector_abstention.py` 結構鎖定(AST 掃描防重構回 first-hit) | 新增 layer/classification resolver 時登記其 abstain 測試 |
| R17-D | `_build_fr_step_prompt` 719 行 god-function(A/C 震央,無 golden) | **採納 — 已封(站4)** | 站0 建 per-step golden snapshot(`test_fr_prompt_snapshots.py`,byte-equal 執法);站4 於 snapshot 保護下完成 façade 拆分,將 prompt 建立邏輯移至 `cli/fr_prompts/` 包,`cli/fr_cmds.py` 行數下降 839 行(2802 → 1963),`test_file_size_ratchet.py`  ceiling 隨之調降至 1980,13 個 golden prompt snapshots 全數 byte-equal 通過。 | 未來新增 FR step 時擴充 `cli/fr_prompts/` 專用 builder 與對應 snapshot golden |

> 附帶發現(非裁決項):`core/quality_gate/red_assertion_check.py:647` 有活的 pyright 型別 error(`var, values = trig` — `_parse_trigger` union 型別未窄化過 `_UNHANDLED_TRIGGER` sentinel),型別問題非猜測病,已 flag 獨立工單處理,不併入本輪。

## Round 16(2026-07-18)— 外部檢索自主盤點(2025-2026 論文/大廠白皮書/熱門 OSS)

老闆指示盤點框架弱點時加入外部觀點,本輪檢索學術論文/技術白皮書/GitHub 熱門實作,逐條與框架現況對賬。

| # | 主張/發現 | 判定 | 一句證據 | Re-open condition |
|---|------|------|----------|---|
| R16-1 | [OpenHands SDK](https://arxiv.org/html/2511.03690v1) 式 event-sourced state + deterministic replay + immutable config 是生產級參考架構,應全面改寫 | **拒 — 等價大爆炸改寫,需求已覆蓋** | `state.json` + 各類 JSONL(spawn/degradation/trajectory)+ `FileSnapshot`(R5 B0)已提供結構化持久化與回滾;全面改寫是為了架構美學而非解決實測痛點 | state 損毀/回放需求成為實測重複痛點時 |
| R16-2 | [AGENTS.md 標準](https://agents.md/) 應 scaffold 到每個目標專案 | **拒 — SSOT 衝突且外部證據反向** | `CLAUDE.md` 已是本框架的單一事實來源;雙檔並存=雙源漂移。[ETH 實證(arXiv 2602.11988)](https://arxiv.org/pdf/2602.11988):LLM 生成的 context files 在 5/8 設定**降低**任務成功率、成本 +20-23% | 目標專案引入非 Claude coding agent(需要跨工具通用格式)時 |
| R16-3 | LLM-judge 應 rubric 化、去 holistic 化以降低評分變異數 | **已建成(already-built)** | `harness/ssi/prompts/evaluate_dimension.md`:`score = tool_score` for every dimension and tier,LLM 不計算不調整數值;14 維度皆確定性工具公式(ruff/pyright/coverage/bandit+semgrep),score.py R4/R8 機器攔截造假;B-review 有 schema 驗證 + `b_gap_validator` 確定性反幻覺降級。範圍限定 gate 評分鏈,不涵蓋 bug-hunt/peer-review 等其他 LLM 判斷面 | 若未來要把此判定擴大到 bug-hunt/peer-review,需針對那些面向另行查證 |
| R16-4 | [MAST(arXiv 2503.13657,NeurIPS 2025 spotlight)](https://arxiv.org/abs/2503.13657) 14-failure-mode/3-類分類法 vs 本框架 3 粗桶(`STRUCTURAL/INFRA_ERROR/EXECUTION_ERROR`) | **採納** | 45.65% era-mixed 失敗率(R15 附帶發現)懸置無法細分;`run-report` 只做 `error_class_counts` Counter,不再深入 | 本輪(站2-3)交付 `core/failure_modes.py` 確定性 MAST 對齊分類器 |
| R16-5 | 乾淨 E2E 基線量測(細分類失效分布/收斂指標實測) | **延後 R17** | [Google Agent Quality whitepaper](https://vanducng.dev/2026/01/13/Google-Agent-Quality-Evaluation-Whitepaper-Summary/) outside-in 原則:先分類器就緒,乾淨資料才有意義;R12 收斂指標、R14 成本信封、R15 re-open 條件全部等此取數 | 老闆核准 dispatch 預算並排入 R17 |

## Round 15(2026-07-17)— Gap 報告 #2:觀測性/可維護性四主張

全部本輪指令實測。

| # | 主張 | 判定 | 一句證據 | Re-open condition |
|---|------|------|----------|---|
| R15-1 | 框架綁定 Python `ast`/`pytest`,應遷移 Tree-sitter+SCIP 實現語言無關化 | **拒 — 前提大半為假,藥方混層** | JS/TS 已是完整註冊語言(v2.8.0):`harness/toolchains/registry.py` DIMENSION_TOOLS 14/14 維度 + `test_language_covers_every_gate_dimension` meta-test 執法;`docs/ADDING_LANGUAGE_SUPPORT_SOP.md` 即既有的 Go/Rust/Java 上車手冊;tree-sitter 已在目標專案驗證層服役(`js-mi` 前例)。報告混淆「自我 lint 層」(stdlib `ast` 掃 harness 自己的 Python 源碼——零依賴、語意保真、taint 追蹤必需)與「目標專案驗證層」(已可插拔)。 | 出現真實 Go/Rust/Windows 目標專案需求(非臆測)時依 SOP 上車;若自我 lint 層本身要換 parser,需先證明 stdlib `ast` 有 tree-sitter 解不了的具體案例 |
| R15-2 | subprocess 無 trace 傳遞,應注入 `TRACEPARENT` | **拒 — 重複議案** | 同 [R14-1b](#round-14) | 同 R14-1b |
| R15-3 | 應攔截全部 `print()` 轉 OTel 結構化 JSON | **拒 — 重複議案且藥方更激進** | 同 [R14-1a](#round-14);額外實測:生成的 workflow JS 有 16 處依賴 `[BLOCKED]` stdout 文字作為 agent 修復指引(`phase3-implementation.js`),攔截即破協議面 | 同 R14-1a |
| R15-4 | 循序執行,應建 DAG 引擎 + 持久化執行(LangGraph/Temporal 式) | **拒 — 病灶(循序)真、效益實測 <1%、持久化半句為假** | integration-test 全史 `run-report`:agent dispatch 共 20.6h(563 次 × 131.6s 均)vs 全部 harness hook/gate span 合計 ≈49min(單次 preflight 均 ≈2.5s、`finalize_gate` 均 4.7s)。per-FR Gate-1 sentinel skip + `resume-fr-phase` 早已提供 crash 恢復(非重跑整個 Phase)。 | 未來 `run-report` 實測單次 preflight 常態 >60s 時,以既有 R8 宣告式 check registry 為縫、stdlib `concurrent.futures` 窄並行,不引入引擎依賴 |

> 附帶發現(非裁決項,供未來查證用):全史 dispatch 失敗率 45.65%(257/563,`EXECUTION_ERROR` 151 / `INFRA_ERROR` 59)——**era-mixed**(混雜 R12 收斂前舊資料,單一 FR-01 就佔 211 次 dispatch),不可作現況證據。下次乾淨 E2E 跑完,用 `run-report --json` 直接取數再議。

## Round 14(2026-07-17)— Gap 報告 #1:觀測性三項 + 可維護性三項 {#round-14}

一行式回填,論證全文見各自詳細出處(不在此複製)。

| # | 主張(濃縮) | 判定 | 詳細出處 |
|---|---|---|---|
| R14-1a | 缺全域結構化 JSON logger | 前提真、比例失真、藥方拒(stdout 是 agent 協議面) | `docs/OBSERVABILITY.md`「What this round deliberately did not build」 |
| R14-1b | OTEL 已引入但 subprocess 無 trace 傳遞,追蹤成孤島 | 前提真、整層零消費者,傳遞=為殭屍鍍金 | `docs/OBSERVABILITY.md` §Agent trajectory 段 |
| R14-1c | 缺 token/成本/延遲等營運指標 | 真,修法近乎免費 → 已做(spawn 信封捕獲 + `run-report`) | `docs/OBSERVABILITY.md` 全文 |
| R14-2a | CLI 直接讀寫 state.json/quality_manifest,缺 DAO | 讀側真(含一個誤分類活傷口)、寫側假、DAO 動機=YAGNI → 已做(`state_io.py` 收斂 + exit 26) | `docs/ERROR_HANDLING.md` FATAL 列 + `core/state_io.py` |
| R14-2b | phase_specs.py ~3000 行上帝檔案,改 A 壞 B | 尺寸真、風險敘述誇大 → 老闆裁定列下輪候選;Round 15 執行拆分(見本檔 Round 15 段 / `scripts/workflowgen/spec_*.py`) | 本檔「Round 15」段 |
| R14-2c | error-dict 與 `exit(1)` 模式不一致,認知負載 | 真但小規模,R13「修邊不修內」決策維持,僅補文件慣例節 | `docs/ERROR_HANDLING.md`「Raise vs. return an error-dict」節 |

---

## Round 24 — 「檢查欄位存在」升級為「檢查內容為真」

> 觸發:`run-all-by-workflow` 的 P1–P8 首次 live E2E(submodule 自 `4921ba4` 起跑,完成至 Phase 9)。
> 10 個觀察裡 5 個收斂到同一根因,列於下表。

### 根因

**框架檢查的是「欄位存在且格式正確」,不是「欄位內容為真」。**

| 檢查點 | 驗什麼 | 不驗什麼 |
|---|---|---|
| `GateBlockedError` 診斷 | `result.dimensions` 裡不及格的維度 | `exc.details` —— 7 種阻擋原因裡有 6 種不在裡面 |
| `record_gate_block` lessons | 同上同一個 filter(字面不同、語意同構) | 同上 |
| Agent B `citations` | 是非空 list | 那個 `file:line` 是否存在 |
| `QUALITY_REPORT.md` | 檔案存在 | 內容是否等於 `gate4_result.json` |
| exception-swallow ratchet | handler 有沒有 log | log 之後該不該繼續 |

### 產物汙染鏈(本輪最重的實證)

```
finalize-gate 把 QUALITY_REPORT 生成崩潰吞成 [WARN]
  → 產物缺失但 Gate 4 照樣 PASS
  → agent 自行補產物:gate4_result.json 複製到 temp workdir、null→0、重跑腳本
  → QUALITY_REPORT.md:19 出現 "Mutation Testing | 0/100 | ✗ FAIL"(虛構)
  → Agent B 讀了它,reason 寫「14 dimensions + Mutation Testing excluded by
    feature flag」(報告裡沒有這句),citations 指向 :13(那是 Linting),APPROVE
  → 進 git、進 FINAL_SIGN_OFF、commit 寫 "Gate4 PASS 97.4 — pipeline complete"
```

權威資料 `gate4_result.json` 全程乾淨(`mutation_testing: {score: null,
excluded_by_feature_flag: true}`),composite 97.3981 也是用 null 跳過算的。
**汙染只在人可讀的渲染物** —— 但這正說明:反造假防的是「agent 直接寫分數」,
防不了「agent 改框架的輸入 / 自己補框架沒產出的東西」。

### 裁決

| 代號 | 主張 | 裁決 | 依據 / 實作 |
|---|---|---|---|
| R24-1 | BLOCKED 的真因對 agent 不可見 | **採納,活傷口** | `harness_bridge.py` 10 個 raise 站點、9 個帶 `details`(7 種 key);兩個消費者都只看不及格維度。`core/quality_gate/block_reason.py` 成為唯一模型,兩邊共用 |
| R24-2a | Gate 4 產物生成失敗被吞成 WARN | **採納,活傷口** | `cli/gate_cmds.py::_generate_gate4_deliverables` 改為 fail-the-gate + `EX_HARNESS_BUG` + degradation ledger |
| R24-2b | `QUALITY_REPORT.md` 無 render-from-SSOT 守衛 | **採納** | `core/quality_gate/quality_report_verify.py`。**刻意用解析而非重新渲染**:重用渲染器會與被檢查物同源,抓不到渲染器自己的 bug |
| R24-2c | Agent B citations 從不驗證 | **採納(限縮)** | 只驗「位置存在」(檔案在、行號在範圍內)。**明確不做**語意支持性驗證 —— 那需要第二次 LLM 判定,會把 review 可信度重新建立在被審對象上(R21「判定早於真值」) |
| R24-3 | 三種時間格式無法對齊 | **採納,活傷口** | 本輪取證時我自己被它誤導過一次(誤判 8 小時空窗,實際 1h18m)。`core/utils/timefmt.utc_now_iso()` + 零 allowlist AST lint;`gate_timestamps.jsonl` 保留 epoch 加 `iso` |
| R24-4a | `phase_completed` 只在 P1/P2 寫 | **採納,活傷口** | 唯一寫入者是 `cmd_push_checkpoint`。後果:`_fr_step_lineage_boundary` 對 phase ≥ 4 恆為 None,2026-07-11 的修復**自落地起只對 phase 3 有效** |
| R24-4b | `last_push_checkpoint` / `_phase` 零消費者 | **採納,移除** | 重新以涵蓋 `harness/` 的掃描確認 |
| **R24-RETRACT-1** | **撤銷 R21-D′** | **原判定的前提為假** | 見下 |

### R24-RETRACT-1:撤銷 R21-D′

R21-D′ 寫「grep 全 repo:`gate_score_overrides` 只在 `sab_parser.py` 產生與序列化,**零消費點**」。
規劃 Round 24 時我把這句當前提提給老闆,老闆據此裁定刪除該欄位。**查證後前提為假**:

| 位置 | 事實 |
|---|---|
| `harness/harness_bridge.py:2281-2284` | `# Apply gate_score_overrides from quality_manifest as threshold floor` |
| `harness/harness_bridge.py:246` | `_d.threshold` 可能已帶 floor-raise |
| `harness/harness_bridge.py:2961-2997` | 從 NFR 推導 floors,與 `quality_targets` 取 max |
| `harness/harness_bridge.py:1656/1670` | `_load_manifest_sab` 讀入;失敗時 log「SAB-derived gate_score_overrides are DISABLED for this gate」 |
| `SKILL.md:323` HR-16 | 它是**不可豁免的閾值下限**(只升不降) |

它是 NFR→gate 閾值的執法機制,**刪掉等於移除 HR-16**。本輪不碰。
`run-all-by-workflow` 兩份 Agent B approval 拿它當驗收依據是**正確的**,不是問題。

成因:那次 grep 沒涵蓋 `harness/harness_bridge.py`(它在 submodule 根目錄,不在
`cli/ core/ scripts/` 的習慣掃描範圍)。**檢查器(grep 的涵蓋面)與被檢查物不同源,
但檢查器的涵蓋面本身沒被驗證** —— 與 R24-1/R24-2 完全同形,只是這次發生在賬本上。

**紀律(R24-4b-3)**:任何「零消費者」斷言必須用涵蓋 `harness/` 的掃描得出,並在斷言處記下掃描範圍。
`tests/test_phase_completed_authority.py::test_zero_consumer_scan_covers_the_harness_directory` 把這條變成可執行的。

### 本輪明確不做(附 re-open 條件)

| 項 | 為何不做 | re-open |
|---|---|---|
| resume 粒度 phase→task | 需 workflow runtime 支援 task 級 checkpoint。老闆已用「重跑一個 phase」解決(代價:Gate4 重跑一次同分 97.4) | runtime 提供 checkpoint API |
| workflow dispatch 進 run-report 的帳 | workflow JS 是 hermetic(無 fs),`agent()` 不經 harness spawner。**沒有正解**,只有讀 runtime 內部 journal 這種脆弱做法 | runtime 提供 dispatch 匯出 |
| P3 dispatch 發散 | 上一輪用「P3 29 次 vs P5 5 次 = 6 倍」論證,但 P3 是唯一跑完整 RED/GREEN/IMPROVE 的 phase,比較不公平,**論據已自行收回**。真正異常的是 3 TIMEOUT + 2 ERROR 全部集中在 P3 —— n=1 | 下次 E2E 重現同樣分佈 |
| Agent B reason 的語意驗證 | 見 R24-2c 邊界 | 出現不需要 LLM 判定的機械做法 |

### 本輪的驗收指標(下次 E2E 對照)

本輪根因判斷來自 n=1。真正的檢驗是**下一次 live E2E 現場修的 harness bug 數是否下降**
(本次為 3:`c7a9d9b` 乾淨環境依賴、`5467049` null-score crash、`68209a9` Gate 2+ null-score block)。
若不降,則「同源驗證失效」的判斷需要修正為「E2E 頻率不足」。

---

## Round 25 — advance-phase 減法：一次量測，多方判定

老闆令：「針對 advance-phase 進行減法工程 …… 前提：不影響每個階段的最終產出物，
以及產出物的品質」。盤點 37 項任務後，實測推翻了「檢查太多」這個直覺。

### 量測（run-all-by-workflow 副本，warm cache）

| completed | prechecks | 測試套件執行次數 | pytest 佔耗時 |
|---|---|---|---|
| 1 / 2 | 1.0s / 1.1s | 0 | — |
| 3 | **55.8s** | **5** | 97% |
| 4 | **45.6s** | **5** | 96% |
| 5 / 6 / 7 / 8 | 23.9s each | **2** each | 92% |

P1→P8 一輪成功路徑：**18 次全套測試執行、約 187 秒**。所有非測試檢查加起來約 **2 秒**。

### 根因

**不是「檢查太多」，是「執行一次測試」沒有單一實作。** 四個站點各自手刻 pytest argv：

| 站點 | test target | cov target |
|---|---|---|
| `gate1_evidence.validate_fr_coverage_immediate` | **無**（靠 pytest rootdir） | `active_src_dir` |
| `enforcement/framework_enforcer.check_coverage_threshold` | 硬編探測三層 | `.coveragerc` 或 `"."` |
| `phase_truth_verifier.check_pytest` / `.check_coverage` | `active_test_dir` | `.coveragerc` 或 `"."` |
| `cli/phase_cmds._advance_prechecks` TDD 區塊 | `active_test_dir` | `src_dir.relative_to` |

P3 的第五條（全綠 + 100%）**邏輯上蘊涵**前四條（全綠、≥70、≥80），且五條在同一
process 內數秒之隔。

**Round 22 修過其中一個。** `tests/test_advance_phase_pytest_scope.py` 記載其根因
（harness/ 是 vendored submodule，裸 pytest 會把 `harness/tests/*` 掃進來）。
`gate1_evidence` 的同形兄弟至今仍是裸呼叫。後果落到真實專案：run-all-by-workflow
`00e732e` 在 P4 中途手改自己的 `pyproject.toml` 加 `testpaths`/`norecursedirs`
（commit message 自述「pytest discovered the entire tree including harness/tests/*
which crashes during collection」），同檔的 `[tool.mypy] exclude = ["^harness/"]`
是 `mypy .` 逼出的第二個同類補丁。**專案在替框架缺失的 SSOT 打補丁。**

### 裁決

| 代號 | 主張 | 裁決 | 依據 / 實作 |
|---|---|---|---|
| R25-1 | 一次 advance-phase 跑五次同一套測試 | **採納，活傷口** | `core/quality_gate/test_suite_run.py` 成為唯一執行點；量測與判定分離，門檻與 exit code 全部不變 |
| R25-1b | `coverage.xml` fast path 從未命中且讀產物非真值 | **採納，刪除** | 三個消費專案都沒有 coverage.xml；harness 自己的 gate 產出的是 coverage.json。守衛：85% 的 XML 不得產生 passing 85% |
| R25-1c | 四種 timeout（含 TDD 區塊「沒有 timeout」） | **採納，統一** | `suite_timeout()`。無上限的套件在無人值守執行裡就是沒有上界的停滯（R24 站5a 的同一類） |
| R25-3a | fastapi/httpx 建議 | **採納，刪除** | 無條件、硬編 Python web stack、WARN-only。本次執行對一個 CLI 佇列工具喊了 6 次 |
| R25-3b | submodule drift 建議 | **採納，搬到 doctor** | advance-phase 唯一的網路呼叫；佔 P1/P2 全程 60%；阻擋為零 |
| **R25-2** | **最嚴判定前置** | **自我證偽，不做** | 見下 |
| R25-2b | block_reason 新增 `test_suite_failed` key | **分類錯誤，不做** | 該 registry 服務 `harness_bridge` 的 `GateBlockedError.details`；advance-phase 從不 raise 它，加 key 等於死碼 |
| **R25-DEFER-1** | **JS/TS 分支** | **老闆裁定本輪不碰** | 見下 |

### R25-2：我自己的提案被站1 消解

計畫寫「最嚴的 `--cov-fail-under=100` 排在最後，前面已經燒掉 4 次弱判定 → 前置後
失敗路徑 P3 從 55.4s 降到 ~11s」。**站1 落地後這個前提不再成立**：memo 讓套件在
最早的消費者（P3 是 gate1_live_cov，P5+ 是 phase_truth 的 framework block）就執行，
昂貴的部分本來就已經很早。重排最多省 **~2.4s**（P5 全程 13.2s 減去套件 10.8s），
而且會改變多重失敗時回傳哪一個 exit code。**收益 2 秒、代價是動到 exit code 優先序 —— 不做。**

形狀與 R23 的 C4/C5 相同：提案的前提在實作過程中被自己的前一站證偽。

### R25-DEFER-1：JS/TS 分支

TDD 區塊無語言守衛。站0 實測確認：js/ts 專案在 P3+ 跑 `pytest <ts 測試目錄>` →
`--cov-fail-under=100` 把「no tests ran」轉成 rc=1 → **exit 9，永久 BLOCKED**。
目前**無 js/ts 消費專案可觀察**（taskq / integration-test / run-all-by-workflow 皆 python），
所以這是純讀碼 + tmp 專案實證的推論。**老闆裁定本輪只查證不修。**

`run_suite` 對非 python 專案直接回傳「未量測」而不執行任何東西，所以本輪不會讓它更糟。
re-open：出現第一個 js/ts 消費專案，或老闆指示。

### 站0 的一項自我證偽

計畫把「`TOTAL … n%` regex 會把 99.6% 讀成 100 → 靜默降標」列為阻擋性前提。**實測證偽**：
coverage.py 的 term 報表**截斷**且**永不在未達 100% 時印 100%**（99.95% 仍印 `99%`），
而對整數門檻 `floor(x) >= T ⟺ x >= T` 是恆等的 —— 70/80/100 三個門檻下 regex 與
精確值**判定完全一致**，截斷只會更嚴不會更鬆。

改讀 coverage 的 JSON `totals.percent_covered` 仍然做，但理由降級為：誠實診斷
（`85.9%` 而非 `85.0%`）+ 對非整數 `min_coverage` 不過嚴。三個消費專案的
`min_coverage` 都是整數（80/90/80），所以這是預防性的，不是活傷口。

### 對「workflow 確定性 ⇒ 可取消」的裁決

**因 workflow 確定性而可取消的檢查，數量為零。** 確定性保證的是**順序**
（workflow JS 是程式碼不是提示詞），不保證**內容**（每一步仍是 LLM agent）。
防「漏跑／順序錯」的檢查只有 next-phase plan 存在性一項，成本 0 秒，且 R22 站2 的
方向是把檢查**收進** advance-phase（因為人手跑沒有等價步驟）。
唯一因確定性可動的是**執行位置**，不是**是否執行**。

### 驗收：不影響品質的可執行定義

在 `/tmp` 的 run-all-by-workflow 副本上跑 `advance-phase --completed 1..8`，
改前 / 改後：**每個 exit code 相同，每一行 `[BLOCKED]` / `[HR-11]` /
`[Gate 1 coverage]` / `[PHASE-AUDITOR]` / `[spec-coverage]` / `[Agent B]` 位元組相同**（8/8 phase）。

| completed | 改前 | 改後 |
|---|---|---|
| 1 | 1.0s | **0.76s** |
| 2 | 1.1s | **0.50s** |
| 3 | 55.8s | **12.9s** |
| 4 | 45.6s | **13.4s** |
| 5 / 7 / 8 | 23.9s each | **~13.1s each** |
| **P1–P8 合計** | **~187s** | **~78s（−58%）** |
| **測試套件執行次數** | **18** | **6** |

### 本輪明確不做

- 其餘約 12 個 pytest 站點（`stage_pass_generator`、`cross_artifact`、
  `confidence_scorer`、`auto_fix/strategies`、`harness/toolchains/registry`）不動 ——
  它們不在 advance-phase 的同一次呼叫裡，一起改是失控重構。
- gitleaks / ruff / mypy 不刪。它們與 gate 維度重疊但**尺不同**（gate linting 門檻 90
  ≙ 容 5 個違規，advance 要 0；gate 用 pyright，advance 用 mypy）。刪掉是降標。合計 0.35s。

---

## Round 26 — 判定所讀的來源，不是產生真值的那一個

老闆令：檢視 taskq-plus 在 P1–P3 的執行紀錄（卡在 P3 的 FR-05）與 harness 的 git
history，找出其他根本性／結構性問題；然後「針對這些不足提出修復方案（要確認問題
的根源並採用正解，not workaround）」。

診斷讀了 42 筆 spawn 紀錄、4 份 lessons、9 份 decision log、84 個 trajectory span、
45 個 commit。七條發現裡有四條是同一個形狀，而這個形狀已經被命名過四次：
R17（prompt↔gate 漂移母體）、R20（多層檢查同一來源）、R21（判定早於真值）、
R24（欄位存在 vs 內容為真）。**本輪的新資訊不是它又出現，而是每一輪的修法都在
「加一層檢查」，而那層檢查讀的來源仍由上游自由改寫。**

| 輪次 | 加了什麼 | 沒動什麼 | 後果 |
|---|---|---|---|
| R21 站2 | gate result schema 執行化 | 生產那份檔案的 prompt | schema 必填欄位沒有生產者 |
| R13 站2a | INFRA 不進 CODE-FIX | 上游會覆寫 `output` | 守衛在它唯一該生效的情境失明 |
| R19 站1 | 失敗語料 corpus | 「分類正確」的斷言 | 誤配在 corpus 裡躺了七輪 |
| R19 站3 | gate 產物記 enforcer_sha | phase 產物 / provenance 讀哪個檔 | 跨版本歪斜不可見 + 永遠印 verdict=None |

### 老闆三裁定

1. **7 條一輪做完**（不分批）。
2. **#4 用生成層統一 wrapper**（重的那個選項），不是只把報表口徑寫誠實。
3. **#3 的逃生口用顯式架構修正指令**，不是靜默同步、也不是只改 prompt。

### 兩項對診斷報告的自我修正

**修正 1 — #1 的根因比報告講的更簡單，所以正解是減法。** 報告原本框成「一份 schema
四個 gate、只驗過一個生產者」。掃完所有生產者指示後：`open_critical_count` /
`open_high_count` **在任何生產者指示裡都不存在** —— 不在 gate-1 prompt、不在
`evaluate_dimension.md`、不在 phase3/4/6 JS 的 gate-write 指令。R21 站2 用來「保持
誠實」的 gate-4 fixture 是**碰巧**滿足的單一產物。所以修法是拿掉假必填 + 補生產者
對賬，不是拆成 per-gate schema。

**修正 2 — #4 不能用「每次呼叫寫一筆 CLI」實作。** Workflow script sandbox 沒有
filesystem、沒有 shell、`Date.now()` 會 throw。`loadFileViaPython`（為了讀一個檔
派一整支 SHELL WRAPPER AGENT）就是這個限制的證據。wrapper 只能緩衝 + 讓下一次
dispatch 帶走，且 cost / turns / duration 在該基座**取不回來** —— 本站取回的是
**分母**。

### 七條的裁決

| # | 裁決 | 站 |
|---|---|---|
| 1 | **接受（減法）** required 去掉兩個孤兒 + 生產者渲染輸出對賬 + gate-1 reality fixture | 站1 `f9bcc30` |
| 2 | **接受** 診斷改附加不取代；`INFRA_BLOCKED` 首次有消費者；corpus 補「分類正確」斷言 | 站2 `a4a8fe8` |
| 3 | **接受（含裁定 3）** SAB 綁定路徑進三個 TDD prompt + `--resolve-phantom` + layer 選擇修正 | 站3 `26cb5a8` |
| 4 | **接受（含裁定 2）** 生成層 wrapper 覆蓋 118 呼叫點 + `log-dispatch` | 站5 `10f7a46` |
| 5 | **接受** 分類收斂成一個 predicate；max-turns 升階一次並記帳 | 站4 `75a210b` |
| 6 | **接受（WARN 不 BLOCK）** phase_completed 記 enforcer_sha + load-context 報歪斜 | 站6 `5bc479f` |
| 7 | **接受** 拆出 `gate_verdict_paths`（只認已定案） | 站6 `5bc479f` |

### 站0 的三個前提：兩個改變了方案

| 前提 | 結果 | 影響 |
|---|---|---|
| 每 phase 都有 shell-capable flush 點 | **成立**（9 支 JS 各 1 個 advance-phase agent） | 站5 走全範圍，但最終改用「下一次 dispatch 帶走」而非 advance-phase flush —— 前者的錨點唯一且不依賴特定 agent 被派到 |
| corpus 是否嵌入現行 `error_output` | **成立，而且更嚴重** | `integration_test.jsonl` 第 2 行逐字就是 `status='INFRA_BLOCKED'`，R19 採進、誤分類至今；`test_real_failure_shapes_are_classified` 只斷言「有分類」，無法偵測誤配 → 站2 多一項交付 |
| ceiling cap 該不該進 `values` | **不需要新設定鍵** | 改「係數 2、只升一次」，由係數與次數自我界定，避免 R20 站G 剛拿掉的 magic number |

### 本輪順帶挖出的、未在原始七條裡的

1. **`test_ssi_scripts.py::test_gate_result_schema_required_fields` 已刪。** 它把
   schema 的 required 清單抄成 literal set —— 正是它自己 R21 docstring 指出其前身的
   毛病。R21 縮短了抄本卻保留了形狀，於是抄本反過來捍衛錯誤清單並會阻擋本輪修法。
2. **`gate` vs `gate_num`**：同一個 provenance 欄位兩個名字（gate-1 prompt 寫
   `gate`，gate-4 寫 `gate_num`），無任何消費者從檔案讀它。已在 schema 中記錄
   → **R26-DEFER-1**。
3. **`tests_passed` / `tests_failed` / `tests_skipped`**：gate-1 prompt 標為
   REQUIRED，而 `_check_tests_failed` / `_check_test_skip_ratio` 都是 regex 解析
   `tool_evidence`，**沒有任何消費者讀這三個欄位**。已記錄
   → **R26-DEFER-2**（減法屬於 prompt，不屬於本站）。
4. **R8 站3 的汙染守衛在本輪抓到我一次**：我把外專案名寫進 degradation ledger 的
   `why` 字串 —— 那會被寫進**消費專案**的產物。守衛是對的，敘事搬回註解。
5. **R20 站2 的路徑 lint 也抓到我一次**：手拼 `02-architecture/ADR.md`。
   `ProjectLayout` 補上 `adr_path`。

### 本輪明確不做

- **per-gate schema 拆分**：修正 1 之後前提消失（不是「四個 gate 形狀不同」，是
  「required 列了沒人被要求寫的欄位」）。
- **render-from-SSOT 生成 gate-1 prompt 的 JSON 區塊**：考慮過。prompt 的逐欄指引
  散文（`// REQUIRED: count from pytest summary line`）在 schema 裡沒有家，而 R17 站1
  對同一類問題選的正是 registry + 完備性 meta-test 路線。採 parity 測試。
- **`tests_*` 三欄位的減法、`gate`→`gate_num` 統一**：見 R26-DEFER-1/2。動的是全系統
  派發最頻繁的 prompt，為純命名一致性不值得，且會churn golden。

### 誠實邊界

- 七站全部是**讀碼 + 可執行反證**層級的驗證。**沒有跑過一輪真的 E2E。**
  「站2 修好後 FR-05 那類 phantom 會被正確中止而不是進 CODE-FIX」是從程式路徑推得。
- 站3 的 3a 假設「寫碼時看到約束就不會分歧」。反面情境（agent 看到 SAB 路徑但
  TEST_SPEC 的測試名暗示另一種佈局，兩邊都不滿足）本次無實例，屬推測；若發生，
  症狀會是 GATE1 spec-coverage 掉分而非 phantom BLOCK。
- 站5 的最後一次 dispatch 永遠不會被 flush（沒有下一次可以帶走）。
  `run_phase` trajectory span 仍是崩潰下的下限。

---

## Round 27 — 判定的裁量權在被判定的一方手上

老闆令：檢視 taskq-plus P1–P8 的執行過程與 harness 的 git history，探討是否有**其他**根本性或結構性問題（承 Round 26 的七條之外）。

**本輪的定調事實**：這一次 taskq-plus 跑完了 P1–P8（147 commits、329 dispatches、$137.57、10.5M in / 1.1M out tokens、Gate 2/3/4 全 PASS），同期 harness 產生 **15 個修復 commit**，每一個的 body 都寫著「observed live on taskq-plus」。**一輪真 E2E 抓出 15 個活 bug，而它們在 6462 個單元測試 + 151 個守衛全綠下存活。** 這不是測試不足，是測試看不到的那一面。

而 taskq-plus 是**專為點亮前一輪五個無信號維度而設計的測床**。Gate 4 跑完，五個目標維度**一個都沒點亮**，composite 98.707 PASS。

### 三條根本問題

| # | 主張 | 裁決 | 出處 |
|---|---|---|---|
| R27-1 | 維度的「不適用」由被評分方自宣告，且不適用比低分划算 | **採納**。`score: null` 被五層各自放行：S3 只要 ≥10 字元散文即通過（`_TOOL_CONTENT_PATTERNS` 涵蓋 17/32 工具）；S4 的 `if agent_score < threshold: continue` 讓 null 跳過交叉驗證——實測它其實是 `float(None)` **拋 TypeError**，而呼叫點無 try（AST 確認祖先鏈只有 `finalize_gate`）；加權時 None 的權重被重分配給其他滿分維度，composite **上升**；`_all_dims_pass` 註解逐字寫 "vacuously satisfying its own per-dim floor"。**None 改為「框架必須自己驗」的觸發條件** | `575e70b`、`1512314` |
| R27-2 | NFR 只能落到 16 個維度中的 5 個，而 prompt 明文禁止規格覆寫 | **採納（兩者都做，老闆裁定）**。`_NFR_TYPE_TO_DIM` 五項，11 個維度無 NFR 可達；P2 prompt 逐字 "Leave … nfr_dimension_mapping empty"。SPEC 為 12 條 NFR 各寫 `dimension:` + 一整段鐵律，實測 **12 錯 6**。改為 per-NFR `dimension:` 直達 + 不存在即 raise + type 表擴充六項 | `8241707` |
| R27-3 | 證據的壽命短於判定的壽命 | **採納**。Gate 4 的 **13/14** 個 `tool_output` 指向 gitignore 的 `.sessi-work/`，全部已消失，而判定本身版控且永久。S3 在判定當下驗過檔案存在（它有 `if not out_path.exists()` 分支）——證據是事後消失的。改為 S3 通過的當下取指紋寫進判定本身 | `e17ed0d` |

### 六條具體缺陷

| # | 缺陷 | 裁決 | 出處 |
|---|---|---|---|
| R27-D | 第三個 registry（`evaluate_dimension.md` 的 `### ` headers）缺三個維度 | **採納**。`architecture_constraints`（gate 1 權重 0.25）/ `execute_verification_target` / `integration_coverage` 有 gate 評分、無 agent 指示。**而 40bedac 剛加的 P1 dimension 驗證比對的正是這份缺三個的清單**——檢查建在不完整的名冊上 | `888e369` |
| R27-E | lessons 召回時機 | **採納（範圍縮小）**。我的原始診斷「零消費者」**是錯的**（grep 的 `--include` 被 shell 吃掉，漏了 `cli/project_cmds.py:754`）。真實缺口更窄：只有 phase 入口一個召回點、不傳 `dimension`，而失敗是 per-FR per-dimension（一輪 23 筆 test_coverage 橫跨五個 phase） | `2450ffc` |
| R27-F | `quality_targets.min_coverage` 四處讀取、四種行為 | **採納**。收斂成 `min_coverage_floor()`，並在其中寫明它與 gate yaml 的 `test_coverage` threshold 是**不同的東西** | `2450ffc` |
| R27-G | sim 測床不涵蓋唯一真正被執行的檔案 | **採納**。`PHASE_FILES` 從無 run-all.js。場景 70 → 72 | `6cc4929` |
| R27-H | 零 skip 由執行期計數判定，看不見條件式 skip | **採納**。實測某專案 35 passed / 0 skipped，同一檔案裡有 **9 個 `pytest.skip(`**。加 AST 靜態掃描 | `333348f` |
| R27-I | 消費專案 main 上的探針 commit（`PROBE-SUBJECT-XYZ`） | **不做，前提不成立**。汙染 registry 掃的是**框架**檔案，而該字串全 repo 零命中——它來自 ad-hoc 探測，不是 shipped surface。加一個永遠不會命中的 token 是死守衛 | 本條 |

### 撤回與未做（前提被自己證偽）

1. **對我自己診斷報告的更正：「lessons 零消費者」是錯的。** 見 R27-E。鏈路自始至終是通的。
2. **R27-DEFER-1 — 讓 `block_reason` 引用判定用的門檻。** 診斷成立：`block_reason.py:220` render 的是 `d.threshold`（agent 寫的），判定用的是 `_effective_threshold()`；某專案 23 筆 lessons 全寫 "needs 100.0" 而該 gate yaml 的 threshold 是 80，**兩個數字巧合相同正是它隱形的原因**。修法（把 `d.threshold` settle 成 effective）被 `test_finalize_gate_override_is_floor_not_ceiling` 抓到：`_effective_threshold` 優先 `_dim_thresholds`（override 的 mirror），覆寫會把 agent 的 90 **降到** override 的 80——floor 變成 ceiling。正解是 `_effective_threshold` 改取三源最大值而非 first-truthy-wins，那會改變每一個 Gate 1 的嚴格度，是更大的獨立決定。**理由留在程式碼裡**。
3. **站7a（NFR 測試消失 = regression）未做**：需要測試函式粒度的跨輪基準。實測 `attestation.json` 的 matrix 是 **FR → code_files 清單**，到不了函式。計畫已預先標記此前提未查證並約定「若不足則縮」。
4. **站1 的 `_TOOL_OUTPUT_MIN_BYTES` 提高被撤回**：最短的真實工具輸出是 `{}` / `[]`（2 bytes，ast-docstrings 無可記錄 / ruff 乾淨），任何擋得住散文的門檻也誤傷它們。守門的是 check 3。
5. **站2 的模板列舉 18 個維度名被撤回**：模板逐字嵌進 `templates/SAD.md`、每份 phase plan 與 P2 prompt，落在 phase2_plan.md 的那份讓 `mutation_testing` 這個字撞上一個為「修復建議」設計的檢查。prompt 與 parser 錯誤訊息各列一次已足。

### 守衛在本輪抓到我五次（全部是守衛對）

| 守衛 | 抓到什麼 |
|---|---|
| `test_shipped_surfaces_carry_no_foreign_project_tokens`（R8 站3） | 把消費專案名寫進會 ship 到每個專案的 prompt 散文，**兩次** |
| `test_evaluate_dimension_python_commands_use_module_form` | 裸 `pytest` 而非 `python3 -m pytest` |
| `test_finalize_gate_override_is_floor_not_ceiling` | 見上方 R27-DEFER-1 |
| `test_no_silent_fail_open`（R7 站1 的 exception-swallow ratchet） | `_skip_sites` 的 `except: return []` 靜默 fail-open |
| `test_prose_is_not_tool_evidence`（**本輪自己寫的**） | pytest-benchmark 的內容樣式前兩版都會誤中它要拒絕的那句散文 |

最後一項值得單獨記：**站1 補了 14 個工具樣式卻沒有任何測試斷言它們生效**，表可以是空的而測試照樣全綠。站1b 補上後，用逐字的真實散文當 fixture，當場抓出我自己的缺陷——`r"benchmark"` 這個字同時出現在 "pytest-benchmark" 和 "--benchmark-only" 裡，**用來認證真輸出的字，出現在它要拒絕的那句話中**。

### 誠實邊界

- 九站全部是**讀碼 + 可執行反證 + 唯讀冒煙**級別。**本輪同樣沒有跑一輪真的 E2E。** 「R27-1 修好後 agent 不再有動機把維度標 N/A」是行為推論——但修法**不依賴 agent 的行為假設**：它把裁量權從 agent 手上拿走交給框架自己跑工具，無論哪個 agent 執行結果都相同。這是本輪設計的主要自我約束。
- **本輪的三條根本問題可能被「換了執行者」解釋掉**：15 個修復 commit 掛 Sonnet 5 / Claude，Round 26 是我。同一個框架、不同 agent，暴露的問題面不同。要排除這個解釋，需要用同一份 SPEC 再跑一輪對照——那是目前唯一沒有的資料。
- R27-3 的指紋保存的是「這是不是 gate 讀的那個檔案」，**不是原文**。刻意不複製：每輪把 `coverage.json` 複製進消費專案會無限成長。

### re-open 條件

- **R27-DEFER-1**：老闆裁定要統一門檻語意時，`_effective_threshold` 改取三源最大值，並同輪重跑所有現存專案的 Gate 1 對照。
- **R27-I**：若該探針字串日後出現在框架的 shipped surface（生成器、prompt、模板），立即加進 registry。
- **站7a**：若 attestation 的 matrix 日後帶測試函式粒度（例如吸收 `SuiteResult.test_outcomes`，ed02bbe 已有該資料），「NFR 測試消失 = regression」即可實作。

---

## Round 28 — 執行基座的失敗，沒有人在接

老闆令：評估並強化所有 workflow JS 的錯誤處理（容錯）與自動修復機制；**先探討 Claude Workflow tool runtime 本身遇到 blocking 問題或錯誤的處理能力**。目標是 workflow 執行中遇到 harness bug（含 workflow JS bug）能主動修復、驗證、發 PR，再從 blocked 節點續跑。

**定調（先於一切）**：runtime 對錯誤只有一種處理方式 —— **終止整個 run**。它不重試、不隔離、不降級、不跨 session 續跑；script 沒接住的 throw 直接結束 run 且**不產生任何結果**（`docs/WORKFLOW_PLAYBOOK.md` §4/§6.3）。所以容錯與自動修復 **100% 必須寫在生成的 JS 裡**——這不是設計選擇，是基座的性質。本輪處理的就是「基座只會死，而我們只在一個檔案裡接住了它」。

### 三個活傷口（全部以 sim 測床實測，非論述）

| # | 傷口 | 量測 |
|---|---|---|
| R28-1 | run-all 的 phase 迴圈只認得 `session_limit_blocked` 與 `error`；`harness_bug_detected` / `dispatch_structurally_broken` 兩個旗標**沒有 `error` 鍵**，迴圈從未讀過 | harness 在 FR-01 崩潰後，run-all 進了 **10 個 P4 box**，回傳 `phases_run: [3,4,5,6,7,8]`、`error: undefined`。生產樹中這兩個旗標**零消費者** |
| R28-2 | 八支獨立 phase JS **沒有頂層邊界**；run-all 有（Round 23 給了 driver 一個 per-phase try/catch），來源檔沒有 | 逐一在每個 dispatch label 注入 transport error：**84/217 逃逸**（run-all 0/85）。P4 14/16、P5 12/13、P7 12/13、P8 13/14 |
| R28-3 | `[HARNESS-BUG]` 與 `[FATAL]` 結構性 dispatch 失效**只在 P3 偵測**；P4/P5/P7/P8 各有自己的 per-FR Gate 1 迴圈，兩者都沒有 | `grep -c HARNESS-BUG`：phase3 = 5，其餘 phase 檔 = 0。後果是 harness 崩潰被當 code-quality FAIL 送進 CODE-FIX |

| # | 主張 / 選項 | 裁決 | 出處 |
|---|---|---|---|
| R28-A | 把兩個終止旗標名稱補進 run-all 的迴圈 | **否決**。那是在新的一層重犯同一個錯：下一個發明旗標的 phase spec 沒有義務通知 driver。改為 **fail closed** —— phase 只有在它唯一的成功出口標記 `phase_complete: true` 才算完成，其餘一律停。與正上方那個「讀不到 cursor 就中止、不猜」的分支同一原則 | `spec_shared.PHASE_COMPLETE_KEY` |
| R28-B | 邊界寫進八個 spec 模組 | **否決**，改在 `generate()` 這一層（與 dispatch wrapper 同一個插槽）：一處決定八支，不存在會被遺忘的第九支。且**只套 `generate` 不套 `generate_raw`**，run-all 位元組不變也不會被雙重包裹 | `generate_workflows._wrap_top_level_boundary` |
| R28-C | 邊界要不要重新縮排 body | **否決**。逐字拼接，body 與生成器輸出位元組相同 —— golden diff 可讀，run-all 的等價斷言仍在比對同樣的東西 | 同上 |
| R28-D | 終止偵測複製到四支 delta 迴圈 | **否決複製**，抽成 `render_terminal_abort_detectors`，兩個 host 共用；`step` 是參數，所以 P5 的中止不會宣稱自己發生在 GATE1 | `js_blocks.py` |
| R28-E | workflow 在中止當下寫 `blocked_node.json` | **撤銷，前提為假**。sandbox 無檔案系統、無 shell、無時鐘；唯一的寫入通道（Round 26 的 bookkeeping preamble）搭在**下一次** dispatch 上，而終止時定義上沒有下一次。非崩潰的終止情境，座標本來就在 state.json + GUARD/sentinel 短路裡 —— 那正是重啟便宜的原因 | 站4 commit body |
| R28-F | crash bundle 的位置 | **採納（站4 的真正內容）**。bundle 是 harness bug 診斷的唯一輸入，卻住在 `.sessi-work/`（整個 gitignore，且是每支 workflow 的 SCOPE RULES 叫 agent 自清的暫存區）。Round 27 站3 為同一理由搬走了降級賬本，**漏了 crash bundle**。搬到 `.methodology/crash/`，舊路徑唯讀相容，`crash_bundle_paths()` 為唯一列舉器（原本 doctor / run-report / crash-triage 各自 glob 一次） | `core/errors.py` |

### 自動修復（L2）—— 本輪未實作，老闆已裁定形狀

老闆選定「**分支修復 + 發 PR，人工 merge**」。實作前必須先承認三條硬約束，它們改變方案形狀：

1. **HR-17 現行罰則是「終止」**，禁止一切對 `harness/` 的寫入。但它禁的實質是「submodule 內偷改、上游看不見、永久 diverge」（playbook §13.3）。正解是把 HR-17 從「禁止一切寫入」改成「**只允許一條可稽核路徑**：開分支 → 改 `scripts/workflowgen/` 或 `core/` → 全套 gate 綠 → push → PR」，並機械執法（禁手改生成物、禁在 main 上 commit、禁 `--no-verify`）。這是憲法變更，需老闆核准後才動。
2. **「resume 回被 block 的節點」只有一種情況成立**：修的是 Python 且**同 session**。修 workflow JS（或跑 `git submodule update --remote`）會改變 script 位元組，runtime 的 resume cache 從第一個改動的 `agent()` 起全部失效 —— 那是重跑不是續跑。所以正解不是修 resume，而是**讓重啟等於續跑**（GUARD/sentinel/state cursor，本輪 R28-1/2/3 讓中止點變得乾淨可辨，是這件事的前置）。
3. **workflow 不能中途等人**（§4 無 mid-run input），所以「發 PR」只能是預先授權的政策，或由 workflow 結束後的外層執行。

### 誠實邊界

- 五站全部是**讀碼 + sim 實測 + 可執行反證**級別。**本輪同樣沒有跑一輪真的 E2E。** sim 模擬的是 runtime 的 API 形狀，不是 OS sandbox、權限牆、真子行程或真 LLM 行為。
- R28-2 的 84/217 是**在 sim 的 happy-path 情境下可達的 label 集合**上量的。真實執行可能走到 sim 沒覆蓋的分支，那裡的逃逸點不在這個數字裡 —— 邊界本身涵蓋整個檔案，但**數字是下界不是總數**。
- 本輪關掉了一個 sim 自己標記為「pinned current behavior」的弱點（`parseAgentJson` 的 PARSE_FAIL 逃逸）。那條測試的註解早就寫著 graceful-degrade 是待辦項；修它的是站2 的邊界，不是針對 A/B 機器的特例。

### re-open 條件

- **L2 自動修復**：老闆核准 HR-17 窄豁免的具體條文後開新一輪；未核准前，workflow 遇到 harness bug 的正確行為就是本輪的「乾淨中止 + 可讀的 crash bundle」。
- **R28-E**：若 Workflow runtime 日後提供任何 script 端的持久化原語（檔案、KV、或中止時的 hook），`blocked_node.json` 的前提即成立，重新評估。
- **R28-2 的數字**：跑過一輪真 E2E 後，用實際走過的 label 集合重算逃逸率，替換這個下界。

---

## Round 29 — 補記（本節由 Round 30 寫入）

Round 29（`3743fc2`，已在 origin/main）的收口站**沒有執行**：賬本、`docs/*`、
`tests/REGRESSION_GUARDS.yaml` 一項未動。後果不是文件缺漏，而是**七站裡哪三站沒做完，
只能靠逐檔 diff 才看得出來**。以下是 Round 30 逐檔對賬的結果。

| 站 | 計畫 | 實際落地 | 缺口由 R30 補 |
|---|---|---|---|
| 站1 反造假層復活 | 4 站點收斂 SSOT + 棄權→BLOCK + 12 測試檔遷移 | 全做 | 站3（它自己新造的三條棄權） |
| 站2a `scope_layers` | 驗證器 **+ P2 prompt 生產者 + golden** | 只有驗證器 | 站1 |
| 站2b 生成器 | 生成器 **+ advance-phase 接線 + 手改偵測** | 只有生成器（零呼叫者） | 站2 |
| 站2c 生效範圍進證據 | 賬本 + 證據 | 只有賬本 | 站2 |
| 站3 棄權盤點 | 盤點 + 立則（或縮編但**進賬本**） | docstring 修正；掃描結論未落盤 | 站3 |
| 站4 provenance | `enforcer_surface` **+ doctor 對賬** | 只有前半，且零測試 | 站4 |
| 站5 timeout | 賬本 + **預算升級** | 只有賬本 | 站5 |
| 站6 分母 | VCS guard + 掃描範圍 + 指紋 | 只有 VCS guard | 站6（指紋做了，掃描範圍撤銷） |
| 站7 收口 | docs ×4 + guards + 冒煙 | **零** | 本節 |

**R29 的三個活問題，都在它自己的修復裡**：

1. `scope_layers` 零生產者 —— 驗證器與消費者齊備，`spec_phase2.py` 未動。母體第八次現身，
   且是第一次發生在修母體的那個 commit 裡。
2. `write_paths_to_mutate` 零呼叫者，且 docstring 承諾一個不存在的接線行為。
3. 三條新的靜默棄權：`CI` env 旁路（`63b9399`，一個 commit 之後就把站1 剛拆掉的形狀裝回去）、
   兩處 `except ValueError: return []`、一處 `except Exception → logging.debug`。

**`63b9399` → `877c1bb` 的修過頭病史**值得單獨記：第一次修 CI 把 failure-closed 一起關掉，
第二次才救回來（commit message 逐字 "**preserve** failure-closed YAML parsing while skipping
physical tool checks in CI"）。一個為了讓 CI 變綠而加的旁路，代價是兩次 commit 加一次回退。

**R29 未預告的副作用**：站1 讓 harness 自己的 CI 轉紅。計畫預告了「現存專案的 gate 會從
PASS 變 BLOCK」，沒預告 CI 這條。

**R29 計畫外的正面發現（`7ab7b0a`）**：`_parse_junit_outcomes()` 回 `{}` 有兩義
（解析失敗 / collection 中止），三個呼叫端只處理 `not ran` 分支 → 空 dict 進 scanner →
collection 一掛就報 0% NFR traceability。**在 taskq-advance P3 Gate 2 現場複現**
（pydantic v2 decorator typo）。這是站1 復活後才浮出來的下游問題，修得對。

---

## Round 30 — 把半座的機制接上，並清掉修復自己造的棄權

### 母體，第七次

> **檢查器的「找不到就 `return []`」與測試的「只造它找得到的佈局」，是同一個假設的兩半。**

前六次的處方都是「加一層守衛」。這次的處方是**棄權不得等於通過** —— 一個跑不起來的檢查，
回傳值必須與「檢查過、沒問題」不同。

### 七站裁決

| # | 判定 | 依據 |
|---|---|---|
| 站1 `scope_layers` 生產者 | **做** | 全樹 grep 零生產者；插入點與 `dimension:` 條款同形同位，+8/-4 無 ripple |
| 站2 生成器接線 + 單一來源 | **做** | 零呼叫者；並在接線過程中量出 R29 的第二個活 bug（見下） |
| 站3 三條棄權 | **做** | 逐行讀碼；CI 旁路的移除以全套測試在 `CI=true` 下綠為證 |
| 站3 通案 ratchet | **不做** | 掃描命中 **179** 處，絕大多數合法（`_read_json` 回 `{}` 是 reader 不是 checker）。checker/reader 之別是語意的，AST 做不出來；硬做就是 179 個偽陽性，一輪內必被消音 |
| 站4 doctor 對賬 | **做** | `core/doctor.py` 未動、`grep enforcer_surface tests/` 零命中 |
| 站5 預算升級 | **做** | 12/18 實測；可比照的實作就在隔壁 |
| 站6 指紋 + registry | **做** | committed ≠ 哪一版 |
| 站6 掃描範圍 | **撤銷** | 見下 |

### 站2 的意外收穫：R29 的修復本身跑不起來

接線時把 fixture 解到真實檔案系統上，量出：

```
paths            = 'taskq_plus/service, taskq_plus/storage'
cwd / paths      = <project>/taskq_plus/service, taskq_plus/storage
src_dir.exists() = False
```

派生出的路徑**沒有 source root**，而 `compute_mutation_score` 正是在這個檢查上中止。
也就是說：**即使 `scope_layers` 被填了，Gate 2 仍然會得到 `mutation_testing 0`** ——
同樣的判定，換一句訊息。R29 站2 會「看起來做了」而什麼都沒改變。

沒被發現的原因單一而具體：**唯一那條測試斷言的是回傳的字串，從未把它解到檔案系統上**。
本輪 `tests/test_mutmut_scope_wiring.py` 的每一條測試都會 resolve。

第二個同源缺陷：`paths_to_mutate` 一直是逗號分隔，而兩個呼叫端都做
`cwd / <整串逗號字串>`。`mutate_dirs()` 現在是唯一把設定值變成真目錄的地方。

### 站6 掃描範圍 —— 撤銷，兩個前提都錯

1. **`--exclude-path` 不存在**。gitleaks 8.30.1 的 `detect` 只有
   `--source / --no-git / --config / --baseline-path / --gitleaks-ignore-path`；路徑
   allowlist 在 `.gitleaks.toml` 裡。我假設了旗標而沒有讀 `--help`。未知旗標讓 process
   非零退出，而這段程式把非零讀成「偵測到 secrets」→ 6 個不相關的 advance-precheck 測試轉紅。
2. **即使旗標存在也是空操作**。這個呼叫是 git 模式，掃的是 **commits**，而 `.sessi-work/`
   是 gitignored，不在任何 commit 裡。探針實測：

   ```
   gitleaks detect --source .            → "1 commits scanned, ~20 bytes"
   gitleaks detect --source . --no-git   → "~56 bytes"
   ```

   taskq-advance `.gitleaksignore` 裡那 3 條 `.sessi-work` 與 2 條 `__pycache__`
   fingerprint，來自 **agent 自己的工作樹掃描**，框架既沒發出也無法從這裡限縮。

**診斷仍然成立**（那些 waiver 是掃描器在消音自己的排泄物），**但我伸手去搬的槓桿沒接在上面**。
撤銷連同兩項量測寫在呼叫點與 `test_the_scan_scope_change_stays_withdrawn` 裡 ——
re-open 要拿量測，不是拿計畫。

### 一個過程錯誤，記在這裡

`b12166b` **是在紅的測試上 commit 的**。我的驗證指令用 `&&` 串在 `tail` 後面，
於是讀到的是 `tail` 的退出碼而不是 pytest 的，7 個失敗直接跳過。修正見 `2fd950d`。
教訓不是「要看輸出」，是**驗證指令本身要讓失敗有辦法傳出來**。

### 誠實邊界

- 七站全部是**讀碼 + 單元/整合測試 + 可執行反證**。**沒有跑一輪真的 E2E。**
- **「接上 `scope_layers` 後 taskq-advance 的 Gate 2 就會過」是推論，不是量測。** 範圍從
  3384 行降到 1846 行是否落在 60 分鐘預算內，我沒實跑 mutmut 量過。若仍超時，下一步是
  SPEC §10 的範圍修訂或預算調整 —— 那是老闆的決定，不是框架的 workaround。
- 站3 的 179 處掃描結果**只有總數與分佈進了賬本**，逐條分類沒有。要立則需要先有辦法機械地
  區分 checker 與 reader，本輪沒有。

### re-open 條件

- **站3 通案 ratchet**：找到能機械區分 checker/reader 的判準時（例如呼叫端把回傳值當
  violations 用 vs 當資料用的型別標註）。
- **站6 掃描範圍**：框架若改用 `--no-git` 模式掃描，排除清單應寫進生成的 `.gitleaks.toml`，
  屆時替換那條 withdrawal 測試而不是刪掉它。
- **L2 治理**：維持 Round 28 的裁決 —— 待老闆核准 HR-17 窄豁免條文。本輪期間
  `harness-bug-fixer <bot@harness.local>` 已經在 taskq-advance 的 run 中直接 push 到
  harness main（`7154768`），繞過老闆選定的「分支 + PR + 人工 merge」邊界；這條路徑目前
  **完全沒有機械執法**。

---

## Round 31 — mutation_testing：框架擁有工具，卻不擁有數字

觸發：taskq-advance 卡在 P3 Gate 2（表面訊息 `mutation_testing 43.8 < 70`）。
拆開整條路徑後，真正的問題不是專案測試寫得不夠好。

### 根源

> **判定的來源與判定的範圍，都不在框架手上。**
> `mutation_testing` 是唯一一個「框架擁有工具、卻不擁有數字」的 tier-1 維度；
> `type_safety` / `security` 則是「框架擁有數字、卻不擁有範圍」。
> 兩者共用同一個後果：**框架量不出來時，代價由被判定方承擔。**

Round 30 的立則是「棄權不得等於通過」。本輪是它的前一步：棄權之前，先問這個數字是誰算的、
算在什麼上面。

### 七項發現（全部有量測值）

| # | 發現 | 量測 |
|---|---|---|
| 1 | `compute_mutation_score` 零生產呼叫者 | 全樹 grep；分數來自 agent 手寫散文，因 mutmut 樣式含 `r"mutmut"` 而通過內容驗證 |
| 2 | 四個 mutmut 解析器，三個讀不懂真實輸出 | `_extract_mutmut_kill_rate` 對三種真實輸入全回 `None`；唯一讀得懂的格式系統裡沒有東西產生 |
| 3 | survivor 清單恆為空 | 真實輸出 `Survived 🙁 (308)` → 解析 0；`ranges()` 把連號摺成 `233-245` |
| 4 | partial-cache resume 是假承諾 | workdir 是全新 mktemp，五處 `copy2` 全是往外複製 |
| 5 | 範圍生成一次後無人對賬 | setup.cfg 手寫且標頭自稱框架生成（文字與 `write_paths_to_mutate` 不符） |
| 6 | gate config 的 `mutation_testing` 區塊是死的 | `median_runs` / `timeout_per_run` 零程式消費者 |
| 7 | S4 掃描範圍與 prompt 不同 | 專案根 4917 個 `.py`（`.venv` 4344 + vendored harness 537）vs 真實原始碼 21 個 |

**站0 之後追加的第八項**：mutmut 的 ToolSpec 帶 `skip_inline=False`，就寫在指名它為 skip-list
的註解正下方。S4 因此真的會從專案根 spawn 裸 `mutmut run`（1800s 預算）—— 正是
`evaluate_dimension.md` 叫 agent 永遠不要下的那道指令；同時也讓 `rc == -1` 分支裡的
kill-rate 交叉檢查永遠不可達。站0 測試的安全網當場抓到。

### 站0 前提查證

- **P1 成立** —— `compute_mutation_score` 在組訊息那一刻已握有 killed/survived/scope/excludes，
  產物寫入器該放在那裡，與 `_write_survivors_artifact` 並列。
- **P2 成立** —— `_finalize_gate_cross_checks` 的 traceability `framework_override` 前例可原形複用。
- **P3 部分為假** —— `resolve_targets` 的 `cov_target` 不必然是單一路徑：在本 repo 它回傳
  `.coveragerc [run] source` 的多行區塊，而 `run_tool` 隨後用 `isdir()` 檢查把它丟掉。
  因此 `{src_target}` 必須展開成 N 個 argv 條目，單一 `str.format` 表達不了。

### 兩項減法

- **gate config 的 `mutation_testing` 三鍵刪除**，不實作。實作意味著每個 gate 跑三次 mutmut ——
  數小時 —— 去平滑一個由確定性 sqlite 計數得出的數字；**沒有變異可取中位數**。
  誠實的狀態是「我們跑一次」，現在設定用沉默說出這件事。
  re-open 條件：拿出 run-to-run 變異的量測。
- **`_score_mutmut` + 三條 regex 刪除**（連同五條測試）。格式是幻想的，且 mutmut 上 skip-list
  之後本來就不可達（`compute_tool_score` 對所有負 rc 回 `None`）。理由留在原本類別的位置。

`objective_primary` **保留**：它活在 SCORE FILE 的旗標上（score.py R4 用它固定
`score == tool_score`），與這裡刪掉的 gate-config 鍵是不同檔案的不同欄位。把兩者混為一談，
正是那個死鍵看起來像承重牆的原因。

### 一個過程錯誤，記在這裡

站3 的第一次反證我用 `git restore` 撤銷探針，**連帶把該站自己未 commit 的修改一起還原了**。
探針本身有效，撤銷方式無效。**未 commit 的工作做反證時，必須反向套用同一筆編輯，而不是重置檔案。**
（與 Round 30 的 `b12166b` 同科：驗證動作本身要能承受它自己。）

### 誠實邊界

- 七站全部是**讀碼 + 單元/整合測試 + 可執行反證 + 一次唯讀冒煙**。**沒有跑真的 E2E。**
- **本輪不會讓 taskq-advance 的 mutation 分數變高，我也不主張它會。** 43.8 是真實的測試不足；
  站1–站5 只讓 308 個 survivor 從「記成 0」變成可行動的清單。真正解開此刻 BLOCK 的是站6。
- 站4 的漂移對賬**會讓 taskq-advance 現有的手寫 setup.cfg 立刻 BLOCK**（唯讀冒煙已確認會觸發）。
  這是正確的，但代價是老闆得決定：改 SAB 讓兩者一致，或接受目前的整包範圍。
- 站2 讓 gate 由「agent 分數」轉為「框架分數」。產物不存在的專案會由 PASS 轉 BLOCK ——
  預期且正確，但確實是行為變更。
- `pytest-benchmark` 仍用 `{root}` 當 pytest 路徑。那是 test-target 問題不是 source-scope 問題，
  留給真正處理它的那一輪。

### 唯讀冒煙（taskq-advance，工作樹前後皆 56 筆未變）

```
reported_total : 308
parsed         : 308
   plugins.py 77  dag.py 71  breaker.py 54  task_store.py 44
   executor.py 34  cache.py 14  cache_store.py 9  breaker_store.py 5
scope_drift    : setup.cfg [mutmut] paths_to_mutate is .../taskq_plus, but the
                 SAB's mutation_testing NFR scopes it to .../service, .../storage
```
逐檔數字與 agent 當初手寫的表格完全一致 —— 解析器對上了一份獨立產生的真值。

---

## Round 32 — 「已驗證」的證明比「驗證」本身便宜

觸發：老闆令檢視 taskq-advance P1–P4 的執行紀錄與 harness 的 git history，
找出是否仍有根本性/結構性問題。taskq-advance 已走完 P4，此刻正在跑 P5。

### 根源

> **「已驗證」的證明比「驗證」本身便宜。**
>
> 框架把「這關驗過了沒有」交給一個**沒有內容契約、沒有交叉對賬、任一通道即可**
> 的檔案存在性；而當框架自己量不出來時，它產出的**假指控**正是促成偽造那個檔案的壓力。

兩半必須一起修：只修偽造面，下一次假指控還會製造同樣的壓力；只修假指控面，
證明依然一行 `echo` 就能造出來。

Round 21 是「判定早於真值」，Round 24 是「欄位存在 ≠ 內容為真」，
本輪是它們的合流：**判定的證明，其偽造成本必須等於被證明之事的成本。**

### 八項發現（量測值）

| # | 發現 | 量測 | 站 |
|---|---|---|---|
| F1 | P4 Gate 1 的「已驗證」證明非框架產生，而框架只驗檔案存在 | 8 個 `.finalized` 同秒、無微秒、26B（真品 33B 帶微秒）；三登記簿 phase-4 gate-1 零筆 | 1, 2 |
| F2 | 框架跑不動工具，卻把跑不動記成對方造假 | `lint-imports` 無 PYTHONPATH → `Could not find package` rc=1 → 0.0；有 → 100.0 | 3 |
| F3 | 解析失敗仍寫成 0.0（R31 立則的兄弟函式） | `_score_pytest` / `_score_exit_code_binary` 從不回 None | 4 |
| F3b | 崩掉的 benchmark 得滿分 | `_score_pytest_benchmark` 只認 exit 5；exit 2 → 100.0 | 4 |
| F4 | 專案宣告的測試集與框架量的不同，無人對賬 | 見下方**自我證偽** | 5 |
| F5 | 半數 FR 撞 turn 天花板，賬本記了沒人讀 | degradations 4 筆全是 `max_turns 40→80`（FR-03/05/06/08） | 6 |
| F6 | 里程碑 commit 重複，且宣稱與登記簿相反 | `34235b6`/`9807b22` 訊息逐字相同、間隔 10 分、皆早於 FR-01 BLOCK | 6 |
| F7 | `run_tool` 丟掉 `resolve_targets` 的 test_target 改用硬編探針 | 語意今日相同，**無活傷口**；R25 點名的第五個同形兄弟 | 3（順手） |
| F8 | `last_block.md` 只寫不清 | P4 的 BLOCK 報告與 state 的 PASS 長期並存 | 6（順手） |

### 站0 三前提的查證結果

- **P1 為真** —— `.finalized` 只有兩個寫入點：`cli/gate_cmds.py:1920`（生產，
  `datetime.now(timezone.utc).isoformat()`，必帶微秒）與 `cli/_shared.py`（測試 fixture，
  寫字面 `"test-sentinel"`）。兩者都產不出觀測到的字串；無非 Python 寫入點。

- **P2 為假，而這是本輪最重要的發現。** 「收據存在 ⇒ 登記簿存在」在修復前**不成立**：
  `cmd_finalize_gate` 在 line 1920 寫 sentinel、在 ~2170/2179 寫登記簿，中間有**五個阻擋 return**：

  ```
  2069 return 5   post-flight structural check failed
  2080 return 5   post-flight error at Gate 4
  2125 return 1   all dimension scores identical  ← 反造假偵測器本身
  2161 return 11  Phase Truth < 90%
  2166 return 11  PhaseTruthVerifier unavailable
  ```

  每一個都在**寫下「本關已通過」之後**才拒絕通過。這一半不需要任何外部行為者，
  純粹是框架自己的洞。修法因此更強：收據移到最後寫，蘊含關係由構造成立。

- **P3 在安全方向為真** —— 於含真實 intra-package import 的 src-layout fixture 逐工具實測：

  | 工具 | 無 PYTHONPATH | 有 PYTHONPATH |
  |---|---|---|
  | ruff / mypy / bandit / radon-cc / radon-mi / readability-v2 / gitleaks | 不變 | 不變 |
  | pyright | 95.0 | **100.0** |
  | import-linter | 0.0 | **100.0** |
  | pytest-cov | 0.0 | **100.0** |

  **每一個變動的分數都是上升，而每一次上升都是移除框架自己製造的偽陰性。沒有任何一項被放寬。**

### 自我證偽：F4 的活實例不成立

站7 的唯讀冒煙推翻了我在站5 commit 訊息裡寫的敘事。taskq-advance 同時有：

```
pytest.ini    [pytest]      testpaths = 03-development/tests      ← pytest 實際讀這個
setup.cfg     [tool:pytest] testpaths = <九個項目>                 ← pytest 從不讀
```

pytest 的優先序是 `pytest.ini > pyproject.toml > tox.ini > setup.cfg`，
`pytest.ini` 存在時 `setup.cfg` 的 `[tool:pytest]` 整段被忽略。實測 `pytest --co`：
**617 tests / 18 檔全收**，`testpaths_drift` 回報 `not_in_declared: 0`。

所以「agent 裸跑量九個、框架量十六個」**是錯的**——兩邊量的是同一組。
站5 commit 訊息裡的那段敘述以此更正。

仍然成立的部分：
- **機制是真的**。若某專案的**生效**設定確實窄化了 testpaths，框架的顯式路徑就會量另一組，
  而在本輪之前沒有任何地方記錄這件事。`testpaths_scope` 的 `_SOURCES` 順序即 pytest 的優先序，
  所以它讀的就是 pytest 讀的——這個順序不是裝飾，讀錯順序會報出不存在的漂移。
- taskq-advance 的 `setup.cfg [tool:pytest]` 是**沒有任何工具會讀的死設定**。
  這是一個較小的、獨立的發現，不是本輪修的那個。

### 老闆的裁定

1. **舊格式 sentinel 硬切**。代價已知並確認：唯讀冒煙顯示 taskq-advance 的
   **P3 sentinel 也一併被拒**——它們是 finalize-gate 的真品，但舊格式不帶任何可對賬的資訊，
   檢查無法區分「真品但過時」與「偽造」。這正是硬切的理由，也是它的代價。
2. **taskq-advance 只報告不動**（本輪未寫入該專案任何檔案，冒煙前後 `git status` 逐字相同）。
3. 範圍 F1–F6 全做。

### taskq-advance 的事實（措辭邊界）

唯讀冒煙，以本輪的對賬函式跑該專案：

```
八個 P4 Gate-1 sentinel        : 全部 REJECTED（非收據格式）
gate1_phase_summary(phase=4)   : expected 8 / recorded 0 / missing 8
                                 → p4-pre-gate3 里程碑會被拒絕
```

**措辭必須是「非框架產生」（已證實：內容格式、同秒批次、與唯二寫入點的行為不符），
而不是「agent 蓄意偽造」（未證實）。** 它也可能出自某個第三方腳本或不知情的補洞。
兩種情形下本輪的修復處置完全相同，但賬本不該替未查證的事下判斷。

**結論仍然成立**：taskq-advance 的 P4 Gate 1 沒有留下任何通過的證據，
而它此刻的 P5 建立在那之上。何時停下來重跑 P4 Gate 1 是老闆的決定。

### 本輪自己犯的錯（四項，全部由本輪的工具抓到）

1. **站0 的 `turn_ceiling_escapes` 測試首跑就綠**，因為 `"metric" in json.dumps(payload)`
   匹配到 pytest 用測試函式名命名的 `tmp_path` 目錄。改成斷言解析後的結構。
2. **站2 的反證不轉紅**：fixture 兩個登記簿都空，拿掉任一半另一半仍在報。
   補「一個有一個沒有」的兩個案例 + 一個「兩個都有但與收據不符」。
3. **站4 的分類反證不轉紅**：測試掃原始碼字串，而合併兩個 list 後那個字串仍不存在。
   改成斷言映射本身；其重寫版又 patch 了五個私有接縫，被 private-patch ratchet 擋下——
   於是把該映射升為公開函式 `s4_block_details`，測試零 patch。
4. **站6 的三條反證不轉紅**：summary 的 fixture 同樣兩簿皆備；duplicate guard 與
   HEAD 記錄點則根本沒有測試覆蓋（只測了 helper，沒測接線）。各補一個。

共同形狀：**檢查程式碼的文字，而不是執行程式碼的意義**——與本輪要修的病同科。

### 承 / 啟

- 承 Round 21（判定早於真值）、Round 24（欄位存在 ≠ 內容為真）、Round 30（棄權 ≠ 通過）、
  Round 31（解析失敗 ≠ 不存在）。
- 啟：本輪把 `infra_fail` 從「agent 寫了汙染的零」擴到「框架自己量不出來」。
  這兩者用同一個 key，因為補救方式相同（修工具，別動分數）；若日後發現需要分辨
  「誰造成無法量測」，那會是拆這個 key 的時候。

---

## Round 33 — 一份合約，五個陳述，沒有一個是來源

**觸發**：老闆令複核 `8637c6a..HEAD` 三個 commit（485c05f / 1620b2c / 4bdc0fb，
另一 session 的 Sonnet 5 在 taskq-full 跑 P1/P2 時所修），查完整性與正確性，掃同形兄弟。

三個 commit 都是真缺陷、真診斷、有測試、全套件綠。問題不在它們修錯，在**修的層級**。

### 根源

> 一份合約被寫在五個地方，沒有一個是來源；而三次修復全部發生在陳述面。

以 H1 錨點為例，同一條 `first_line.startswith(expect_prefix)` 有六份陳述，三份是錯的：

| 陳述 | 內容 | |
|---|---|---|
| `scripts/file_loader.py:178` | 實作 `startswith` | 真值 |
| `scripts/file_loader.py:25` | docstring 寫 "doesn't **contain**" | ✗ |
| `tests/test_file_loader.py:12` | docstring 寫 "exact **substring** match" | ✗ |
| `spec_phase{1,2}.py` | `diskPrefix` 字面值，每個交付物 **3 次** | 手抄 |
| `templates/<X>.md` | agent 起手的 H1 | 手抄 |
| P1/P2 prompt 散文 | "or any H1 line **containing** the phrase" | ✗ |

第六份是**唯一本身即產物**的那份——它是寫檔案那方讀的指令。

R17 站1 的 `test_prompt_gate_parity.py` 自稱是此母體的 "structural close"，
實際涵蓋範圍只有 GATE1 prompt ↔ gate1 YAML 閾值。本輪三個 commit 全部落在它之外。

### 逐項裁決

| 項 | 判定 |
|---|---|
| 485c05f 模板改為完整 14 值 + drift test | **採納**（實測與 `ALL_NFR_TYPES` 逐項同序） |
| 485c05f 只加 B-checklist 散文 | **不足**——它自己的診斷說缺的是機制。站3 補框架側檢查，散文保留 |
| 1620b2c 修 `templates/SAD.md` H1 + drift test | **採納但未掃齊**——7 個同形兄弟只修 1 個，4 個仍破裂（含 ADR.md，同一支 spec 檔的 P2 template）。站1 掃齊 |
| 1620b2c 對既有專案零效果 | **屬實**（`_init_copy_templates` 的 PROTECTED 邏輯）。老闆裁定只報告不動既有專案 |
| 4bdc0fb `_CITATION` 接受 `(annotation)` | **採納**，站4 保留 |
| 4bdc0fb 「shared by ... quality_report_verify (Gate 4)」 | **撤銷**——該檔零 citation 引用，全樹唯一消費點是 `agent_b_approvals.py:256`。結論來源是測試檔名（34 支既有 citation 測試住在 `tests/test_quality_report_verify.py`）。效果面仍廣（P6 走同一函式），錯的是機制陳述 |
| 4bdc0fb 未修製造誤導的 fallback | **屬實**，站4 補（違反 R24 條款） |
| 12+ 支新測試零登記 | **屬實**，站5 還債 239→270 並補完備性方向 |

### 站0 三前提

- **P1 —— 自我證偽**：「修 `intro = head.rstrip() + "\n\n"` 即可讓重生的
  TRACEABILITY_MATRIX 通過錨點」為假。`overlay.py:239` 把 `AUTO-GEN:START` 排在 H1 之前，
  首行是 sentinel 而非空行。H1 必須移到 sentinel 之上，站2 因此是 renderer 變更而非一行。
- **P2 成立**：帶 `# Traceability Matrix` 的 reload 只出現在 phase1.js / run-all.js 的
  P1 段，無非-workflow 消費者 → 站2 用 WARN 不 BLOCK。
- **P3 成立**：`verify_agent_b_approvals_core` 三個消費點；`quality_report_verify` 不是其一。

### 新挖出的兩個現場

- **維度名冊也是兩個來源**：`traceability` 是 `gate4_p6_full.yaml` 的計分維度，
  `evaluate_dimension.md`（P1 prompt 指名的名冊）沒有 `### traceability` section。
  正確對應到它的 NFR 會被 P1 自己的 checklist 判為「指向不存在的維度」。
  站3 取兩者聯集，並用 `dimension_roster_split()` 把分歧釘住。
- **第四條靜默棄權**：`_parse_srs_fr_block_json` 找不到區塊時回 `{}` 不出聲。
  R30 站3 才清過三條。

### 本輪自己的撤回與再確認

站3 一度放寬標題比對（依據：taskq-full 的真實區塊在
`## 10. AC ↔ Module Traceability (machine-readable)` 之下，兩條路徑都讀不到）。
放寬後**立刻抓到該專案未填寫的模板存根**並把 placeholder FR-01 交給下游——
找錯區塊比找不到更糟。同時該檔在我量測後被另一 session 重置（3773 bytes，01:33），
量測無法重現，故連同事實一起撤回。

老闆指出該版本仍在 GitHub。取回 `0fadc4bd`（"phase1(review-complete); 8 FR(s)"）重跑：
**1116 行、8 FR、12 NFR，無 sentinel，現行 parser 回 `{}`**——事實成立，已固化成 fixture。
撤回的只有藥方；站3b 改以**內容定址**（哪個 fenced JSON 帶 `functional_requirements`
就是它），一併刪掉 sentinel 路徑與兩條標題路徑：三種猜法換成一條性質。

### 本輪自己的三個測試缺陷（全由反證抓到）

1. 站1 的 docstring 文字掃描：把 `expect_prefix` 與 "contain" 換行分開就變綠，
   而文字一樣錯。且散文掃描無法分辨「主張 substring 語意」與「說明 substring 語意不適用」。
   → 改為行為斷言（`test_prefix_must_anchor_the_first_line_not_appear_inside_it`）
   + 只掃 prompt 的許可條款（prompt 是生成物，可檢查）。
2. 站2 的錨點測試寫到不存在的路徑，跳過了「已存在且有 sentinel」那條分支——
   正是四個受測專案所在的分支。
3. 站2 的冪等測試只比較 run 1 與 run 2，看不到「穩定地重複兩個 H1」。
   → 斷言恰好一個。

共同形狀與 R32 相同：**檢查程式碼的文字，而不是執行程式碼的意義**。

### 唯讀冒煙（5 專案，前後 `git status` 指紋比對）

```
taskq-full           SRS.md / SPEC_TRACKING.md / TRACEABILITY_MATRIX.md / ADR.md   FAIL
                     ← 01:33 重新初始化，首行正是站1 修掉的那四個模板存根
taskq-advance        01-requirements/TRACEABILITY_MATRIX.md   FAIL (首行為空)
taskq-plus           同上
integration-test     同上
run-all-by-workflow  同上
```

四個老專案的 matrix 首行為空，是站2 的病灶，尚未跑過修好的 advance-phase——
它們下一次 P3+ advance 會自動修好。taskq-full 的四項則證明站1 的修復只對**新專案**生效
（PROTECTED 邏輯），這是硬切的價格，已知並接受。

### 已知弱點與再開條件

- **站5 的 `[no-guard]` 逃生口**：一個好打的標記就是一個可被習慣性繞過的機制。
  再開條件：若 `[no-guard]` 出現在確實新增了守衛的 commit 上，本機制是表演，
  應替換或移除，而不是留著看起來像執法。
- **站2 用 WARN 不 BLOCK**：依據是 P2（anchor 只在 P1 被讀）。
  再開條件：若出現非 P1 的 anchor 消費者，這個選擇要重審。

### 承 / 啟

- 承 R17（prompt↔gate 漂移）、R24（BLOCK 必須報真因）、R27（判定的裁量權在被判定方）、
  R30（靜默棄權）、R31（解析失敗 ≠ 空集合）、R32（框架產出自己會拒絕的東西）。
- **替代假說（必須記下）**：本輪把「五份陳述沒有來源」讀成結構性根源，
  但三個 commit 來自同一位作者、同一天、同一個專案的同一次跑，取樣有偏；
  也可能只是三個獨立低階 bug 的巧合。反駁證據是 F1 的 4/7 破裂率與 F5 的 12/12 未登記率
  （兩者都跨越那一次跑），以及 R17 的 parity registry 涵蓋不到本輪任何一項。
  仍不足以排除「同一位作者的同一天習慣」。**下輪應以不同作者的 commit 覆核。**

---

## Round 34 — 同一條規則的兩種語意；同一份交付物只驗一次

**起因**：老闆問「為什麼 run-all-by-workflow 完整跑完八階段，卻沒遇到 R33 修的那些問題？
是覆蓋率嗎？」實測答案是**不是覆蓋率**——四個缺陷裡它遇到了三個，全部靜默通過。

### 前置調查：三個缺陷都在，沒有一個發出聲音

| R33 的缺陷 | run-all-by-workflow | 為何無聲 |
|---|---|---|
| F1 模板 H1 破裂 | **遇到**（init commit b694901 首行 `# TRACEABILITY_MATRIX.md`） | P1 的 Agent A 全文重寫覆蓋掉模板 H1（dfd7abd 起合格）——被非確定性動作**抹平**，不是不存在 |
| F2 重生丟 H1 | **遇到，至今仍壞** | 唯一帶 anchor 讀它的是 P1，壞在 P3→P4；**檢查點在缺陷之前** |
| F3 SRS NFR type 非法 | **遇到 3 個**（NFR-07/08/10 = resilience / concurrency / evolvability） | 下游把它改對了：SAD 的 SAB block 寫 reliability / reliability / maintainability 而通過 validate，同一份 SAD 第 308-311 行的表格卻仍抄 SRS 原值。兩個版本並存，四道 Gate 全綠 |
| F4 citation annotation | **沒遇到**（該專案 0 個 annotation 後綴） | 這一項才是覆蓋率 |

共同機制：**框架的檢查點與缺陷的產生點在時間上錯開，或被一個非確定性的 agent 動作抹平。**
「跑得完八個階段」從不是「沒有缺陷」的證據，只證明沒有任何一層被安排在缺陷會現身的位置。

一項時序更正（改變了 F3 的診斷）：P2 checklist 那句「`type:` 不需要 textually match SRS；
若 SRS 自己的 `type:` 非法，那是 Phase 1 缺陷，**另外標記**」是 `485c05f` 在 08-03 加的，
比那次跑晚 6 天。07-28 當時 agent 只是照著「type 須取自合法清單」自選了合法值
（機制**未驗證**，無當時 dispatch log）。但那句話讓形狀完整：框架明文授權下游繞過上游的
非法值，並把回報責任交給一個當時不存在的通道——R33 站3 的 `illegal_nfr_vocabulary` +
exit 29 正是那個接收方，而 485c05f 自己沒說出這個配套關係。

### 裁決

**第七份陳述 — 採納並修復（站1）。**
R33 說 H1 錨點合約有六份陳述。實際七份：`js_blocks.py:1403-1411` 有 JS 側自己的第二道檢查，
語意是 multiline 的「任一 H1 行含有該片語」。它驗的是**agent 回傳的文字**（Python 驗磁碟），
存在理由是 file_loader docstring 記的 Bug v5（"Acknowledged" preamble）與 Bug v8（幻覺內容）
——而它的寬鬆語意**恰好放行 Bug v5 的形狀**。同一個 render 函式裡另有兩份陳述說它是
first-line startswith（header 註解 1349-1350、`<think>` strip 註解 1390-1393），
實作是三者中唯一不同的一個，也是唯一在執行的一個。

修法不是收緊字面值，而是抽成 `js_src/anchor_check.mjs`（沿用 `json_utils.mjs` 的
one-file-two-consumers 模式）。理由是 R33 站1 自己的第二個反證：**寫在生成器字串裡的規則，
唯一能檢查它的方式是 grep 生成物，而 grep 分不出「規則」與「談論規則的句子」。**

**F2 檢查時序 — 採納並修復（站2）。**
R33 站1 給了 anchor 單一**來源**，沒給它單一**時點**。`_broken_deliverable_anchors`
掃 registry 全表中磁碟上存在的每一個（**不限本階段**，因為病就是後面的階段改壞前面的），
不合格回 exit 30。

位置即設計：跑在 `_regen_traceability_views` **之後**，所以框架自己擁有的 render-only view
先被自動修好，能活到 BLOCK 的必定是框架無權代改的檔案。BLOCK 而非 WARN 的依據是實測：
5 個真實專案 × 7 交付物，唯一 FAIL 的永遠是那個 render-only view，而它在檢查點之前已被修好
（對 run-all-by-workflow 的副本實跑：首行 `''` → `'# Traceability Matrix'`，
且冪等——重跑仍只有一個 H1）。**誤傷為零。**

R33 站2 的 `_warn_if_view_lost_its_anchor` 保留：它在 regen 當下出聲，
能分辨「框架剛弄壞的」與「進來就壞的」，兩者診斷價值不同。

### 站0 三前提

| 前提 | 結果 |
|---|---|
| P1：sim 的 `loadpy-` stub 在 JS 改嚴後仍通過 | **成立**（stub 回傳 `` `# ${heading}\n\n…` ``，首行即錨點） |
| P2：R33 站2 的 renderer 會把既有壞檔修好且冪等 | **成立**（實測如上） |
| P3：`_advance_prechecks` 兩個呼叫點（`:392` re-verify / `:484` 正常）都會經過新檢查 | **成立**（兩者都呼叫整個函式） |

### 一項規格範圍的誠實界定

站0 的事實 4 原本要以 `--completed-phase 8` 表達「跨階段」，實測 fixture 在到達本檢查前先回
exit 17（finalize-gate 未呼叫 Gate 1 per-FR）。那是既有的、更早的關卡，不是本檢查的涵蓋缺口。
指令層的跨階段測試改用 P2（P2 exit 被壞掉的 P1 交付物擋住），
範圍本身另由 `test_the_scan_is_not_scoped_to_one_phase` 直接釘住。這寫在測試 docstring 裡。

### 五個測試 fixture 被這條新約束擋下

`"FR-01 content"` / `"tests: []"` / `"# SRS"` 這類佔位首行不再合法。**這是 fixture 的問題**，
不是實作的：真實交付物本來就帶 anchor（5 個專案實測，agent 寫的 5 個交付物全部合格）。
每個 fixture 改為內插 `anchor_for()`，registry 一改它們就跟著動。

### 一項計畫偏離：`docs/ERROR_HANDLING.md` 未加 exit 30 條目

計畫寫「ERROR_HANDLING.md 新增 exit 30 條目」。實際檢視後**不加**：該文件的 Exit codes
一節刻意不複製清單，明文寫著「Read the registry directly rather than trusting a copy here
— a hand-duplicated list is exactly the kind of drift this round exists to close」。
在那裡加一行，就是製造本輪與 R33 都在消除的那種第二份陳述。exit 30 的雙向執法由
`tests/test_exit_code_registry.py`（`cli/exit_codes.py` ↔ `harness_cli.py` docstring）承擔。

### 已知弱點與再開條件

- **JS 那層被判為「有真實目的、不可刪」，可能高估。** 依據是 file_loader docstring 記的
  Bug v5/v8 都發生在 LLM 中繼那一段。但**沒有量到它改嚴後實際擋下過幾次**。
  再開條件：若 `sessions_spawn.log` 的 `loadpy-` 重試次數長期為零，它就是儀式不是機制，
  該減掉而不是留著。
- **站2 的掃描在 ingestion mode 可能誤殺**：若某專案的標準路徑上放的是另一種文件，
  會被誤擋。目前走分母保護（掃到 0 個記帳不擋），但「存在於標準路徑卻是別的東西」沒有覆蓋。

### 承 / 啟

- 承 R32（框架產出自己會拒絕的東西）、R33（一份合約多份陳述）、
  R30/R31（分母保護、解析失敗 ≠ 空集合）、R24（BLOCK 報真因）。
- **替代假說（必須記下）**：本輪把「錨點」當成值得升為不變式的契約，
  但它也可能只是一個**過度指定的載入細節**——真正該問的是「為什麼載入需要驗首行」。
  反面證據是它造成過一次真實中止（`legal_artifacts.py:87` 的
  LOADER_FAILED_AFTER_3_ATTEMPTS），以及 4/5 專案至今不合格卻無人知道。
  但若下輪發現錨點可被更強的識別方式取代（如 R33 站3b 的內容定址），
  那本輪做的是把一個該減掉的東西執法得更嚴。
- R33 記下的「下輪應以不同作者的 commit 覆核」**本輪未執行**——本輪的觸發是老闆的提問，
  不是新的 commit 覆核。該條款仍然待辦。

---

## Round 35 — 框架量不出來的時候，說出口的是「零分」

**起因**：老闆令查 taskq-renew 的 P1/P2 執行狀況並驗證 R32/R33 是否生效。
R32 六站、R33 三站都量到生效（見下），但同一次量測在 `mutation_testing` 這條路上
挖出三個互相接續的活缺陷，當時正卡住該專案的 Gate 2。

### 前置量測：R32/R33 在一個乾淨對照組上的生效狀況

taskq-renew 於 08-04 02:15 init，`enforcer_sha` 記錄 P1/P2 兩次都是 `100cda5`（R33 站6），
R34 尚未存在——**只帶 R32+R33 的一次完整 P1→P2**。

| 改動 | 結論 | 證據 |
|---|---|---|
| R33 站1 錨點 SSOT | **生效** | 7/7 交付物首行合規（前四個專案的 TRACEABILITY_MATRIX 首行都是空的） |
| R33 站3b 內容定址找 block | **生效且非空轉** | 版面高度客製的 SRS 仍解出 8 FR / 12 NFR，分母不為零 |
| R33 站3 NFR 詞彙 | **一半生效** | `dimension:` 12/12 合法且真的被檢；`type:` 見下方缺口 |
| R32 站1 sentinel 收據 | **生效** | 8/8 `.finalized` 帶 schema/score/result_sha256/enforcer_sha/ts |
| R32 站2 三通道對賬 | **生效** | 每個維度都有 `evidence_digest`，含 setup.cfg 指紋 |
| R32 站3 PYTHONPATH | **生效** | 8 個 FR 的 architecture_constraints 全 100，`lint-imports exit=0`——站3 修的 `Could not find package` 沒再出現 |
| R32 站6 里程碑內容為真 | **生效** | 里程碑 commit 宣稱的 8 FR 對得上 fr_progress + 8 收據 + 8 timestamps |
| R32 站4 量不出來 ≠ 零 | **有洞** | 見下 |

### 三個缺陷是一條鏈

> **框架量不出來的時候，它說出口的是「零分」；而零分是被判定方的成績，不是框架的狀態。**

**D1（站1）workdir 的 pytest 目標。** `_copy_setup_cfg_to_workdir` 只在
`if not setup_cfg.exists():` 分支寫 `[tool:pytest] testpaths`。Bug #43 加這個寫入的理由正確
（mutmut 的 baseline 以 workdir 為 cwd 跑 pytest），條件卻掛在「專案有沒有 setup.cfg」。
taskq-renew 有，裡面只有 `[coverage:run]`——測試靠 conftest.py 的 sys.path 插入找到 src，
所以從來不需要宣告 testpaths。用框架自己的函式重現：workdir cfg 無 pytest 目標，
`pytest -x --assert=plain` rc=5「no tests collected」，mutmut raise。

**D2（站2）量不出來回報 0.0。** `compute_mutation_score` 有 11 個 `return False, 0.0`。
下游只讀數字不讀旗標。這正是 R32 站4 從 `_score_pytest` / `_score_exit_code_binary` /
`_score_pytest_benchmark` 拔掉的形狀，mutation 不在那站射程內。

**D3（站3）自報失敗即免審。** `harness_bridge.py:1418` 的早退在 1525 之前，
所以 `_mutation_artifact_violations`（R31 站2 + 站4 的全部執法）在 agent 自報失敗時從未執行。
taskq-renew 實測：`mutation_score.json` 不存在、`scope_drift` 現在就會叫，兩者都沒被回報。

三者任一單獨修好都不足：D1 產生失敗 → D2 把失敗翻成 0 分 → D3 讓那個 0 分關掉所有還能識破它的檢查。

### 兩個在修復途中才看見的形狀

**框架自己教出那個 0。** `evaluate_dimension.md:267` 寫著
「If `success` is `false` … write `tool_score=0` **per the "mutmut unavailable" path below**」，
而那條 path 說評估 SUSPENDED、不要寫 score 檔——**指令引用了一條禁止它自己的規則**。
agent 寫的 0 是照指示辦事，而那正是 D3 早退的觸發值。這是 R17 母體的又一次：
prompt 與 gate 是同一條規則的兩份陳述，這次是 prompt 那份在生產有害值。

**既有 fixture 太友善。** `tests/fixtures/mutmut_smoke/setup.cfg` 宣告了 testpaths 與
pythonpath，所以那支**真 mutmut 端到端測試**（預設就跑、跑了很久）從來只見過設定良好的專案。
R19 母體的又一次：測試與被測物共享同一個過於樂觀的前提。新 fixture `mutmut_bare_cfg/`
複製真實專案的形狀，站1 的修復由它端到端證明。

### 一項計畫偏離（不做，記在此）

計畫寫「`Stryker produced 0 mutants` 一併改為量不出來（分母為零）」。**未做**：
`evaluate_dimension.md` 明文寫著「score = 0 (not 100) when no mutants were produced」。
零分母該不該算「未量測」（R27 站7 / R30 站6 的規則）與這句寫下的規則直接衝突，
而反轉一條寫下來的規則是裁決、不是修缺陷，超出老闆本輪界定的三個。
**再開條件**：下一輪若處理分母保護，這兩條必須放在一起裁決，不能只改一邊。

### 一項已知不精確（不改，記在此）

`infra_fail` 的一行標籤是「Dimension scored zero because its tool could not run」，
而本輪新加入的兩個成員（artifact 缺席、`score: null`）根本沒有分數。
該標籤被三處引用（`test_unscoreable_is_not_zero.py` docstring、`docs/ERROR_HANDLING.md` 表格、
`harness_bridge.py:1343` 註解），改它等於同時改四處。條目自身攜帶的具體理由才是操作者讀的東西，
標籤留原樣。**再開條件**：若再有第三類無分數成員加入，標籤就該連同三處引用一起改。

### 刻意的行為改變

「artifact 缺席」與「scope 漂移」從 `tool_score_fabrication` 改列 `infra_fail`。
兩者都是擋，差別在指令：前者的既定修法是「不要重跑，是分數錯了」，
而 artifact 缺席的正確動作恰恰是去跑那個指令。三個既有測試因此更新，這是本輪的目的不是副作用。

### 明列不做

- **R33 站3 的 `type:` 缺席**：taskq-renew 的 12 個 NFR，SRS 機讀 block 的 `type:` 全部是 None，
  `illegal_nfr_vocabulary` 的 `if raw_type is not None` 跳過必填欄位（`templates/SRS.md:107`
  寫著必填），值到 SAD 的 SAB block 才首次出現——**上游缺席、下游發明**，是 R34 記的 F3
  形狀降一階。老闆本輪界定的是那三個缺陷；這是 SRS↔SAD 傳輸面的另一條線，另案。
- **taskq-renew 專案本身不動**（全程唯讀，`git status --short` 指紋前後相同）。
  它的 Gate 2 會因本輪修復而能真正跑出 mutation 分數，何時重跑由老闆決定。

### 承 / 啟

- 承 R32 站4（量不出來 ≠ 零，本輪把它補到 mutation）、R31 站2/站4（框架自有數字與範圍對賬，
  本輪讓它們真的會執行）、R13（HARNESS_BUG 不進 CODE-FIX）、R17（prompt↔gate 兩份陳述）、
  R19（fixture 與被測物同源）。
- **替代假說**：D3 也可能是刻意的成本取捨——跨驗證很貴，只查宣稱成功確實省下大部分。
  反面證據是代價已經發生：一個 harness bug 被寫成專案的 mutation 債，
  而唯二能識破它的檢查都掛在那條 `continue` 後面。
- R33 記的「以不同作者的 commit 覆核」**仍待辦**（本輪觸發是老闆的查證指令）。

### 唯讀冒煙：D1 影響 5 個專案中的 2 個

指紋前後相同（`git status --short | md5`，五個專案逐一比對），只寫 `/tmp`。

| 專案 | 自己的 setup.cfg | 有 `[tool:pytest]` | **修復前**的 workdir 目標 | 修復後 |
|---|---|---|---|---|
| taskq | 無 | — | 有（走「無 setup.cfg」那條分支） | OK |
| taskq-plus | 有 | 無 | **無目標** | OK |
| taskq-advance | 有 | 有 | 有（來自專案自己的宣告） | OK |
| taskq-renew | 有 | 無 | **無目標** | OK |
| run-all-by-workflow | 無 | — | 有 | OK |

**這不是單一專案的巧合**：兩個專案落在「有 setup.cfg 但不談 pytest」這個舊分支照不到的位置。
唯一帶 mutation artifact 的是 taskq-advance（`score=78.2`）；其餘四個 artifact 皆缺席，
本輪之後這個缺席只有一個意思——沒人跑過那個指令。
taskq-renew 的 `scope_drift` 現在會叫，且已位於 `score: null` 分支之前，會真的被回報。

---

## Round 36 — 一次 default 翻轉，六份陳述只改了一份

觸發：老闆問 `883e9ca`「它是不是破壞了不要直接改 workflow JS 的規定？」

答案是破壞了。但它報告的缺陷屬實，只是修在葉子上。

> **母體（第八次）**：真正被消費的那份產物，不是被驗證的那份產物。
> R33 是「一份合約五個陳述」、R34 是「兩種語意一個時點」，
> 這次是**一個 default 六份陳述，而唯一自動執行的檢查看的是第七份（golden）**。

### 病因：`47ec3fd`

| commit | 動作 |
|---|---|
| `5be1d78` | 建旗標，`_DEFAULTS["mutation_testing"] = False`。當時六份陳述**全部正確** |
| `47ec3fd` | 翻成 `True`，改了 loader、本 repo 的 `harness_config.json`、一支測試 |

翻轉後仍停在舊值的五份：

| # | 位置 | 能做什麼 |
|---|---|---|
| 1 | `core/harness_config.py` `_DEFAULTS` | ✅ 唯一有程式讀的那份 |
| 2 | `docs/CONFIGURATION.md` 表格 Default 欄 | 操作者照它設定 |
| 3 | `docs/CONFIGURATION.md` JSON 範例 | 被讀成 default 表 |
| 4 | `harness_config.py` docstring 範例 | 同上 |
| 5 | `spec_phase{3,4,6}.py` 的 NOTE（逐字相同） | **會行動**：指示 Gate 2/3/4 orchestrator 把與 loader 相反的值寫進專案 config |
| 6 | 5 派生的 4 支 shipped JS + 4 支 golden | `883e9ca` 只改了 shipped 那一半 |

`883e9ca` 修的是第 6 項的一半。生成器仍持舊值 → **下一次 `--write` 會靜默還原它**。

### 三個獨立缺口，各補一個機制

| 缺口 | 事實 | 修法 |
|---|---|---|
| **G1** 文件只驗鍵存在不驗值 | `test_every_registry_key_is_documented` 檢查 `` `key` in doc ``。實測 13 鍵中**恰好 1 個** Default 欄與 registry 不符 | `test_every_registry_default_matches_the_doc` + 解析負控制 |
| **G2** 沒有任何測試/hook/CI 跑 `--check` | golden 比對 `generate()` ↔ `tests/golden/`，**從不開啟 `.claude/workflows/`**；94 支 workflowgen 測試全綠時 `--check` rc=1 | `test_workflowgen_shipped_parity.py` —— 把 `--check` 變成測試 |
| **G3** 生成器散文斷言 Python 事實卻無綁定 | R17 站1 已替 GATE1 prompt 建過同型 registry，workflow JS 這面從未納入 | `spec_shared.render_mutation_flag_note()` 從 `_DEFAULTS` 渲染；三處硬編字串消失 |

### 兩件量出來的事

**本輪對 `.claude/workflows/*.js` 的淨變動是零位元組。** 渲染器產出的文字與
`883e9ca` 手寫的**逐位元組相同**，所以 `--check` 從 4/9 DRIFT 變 9/9 OK
而完全沒有跑 `--write`。這同時證明：本輪沒有改變任何 agent 收到的指令，
只改變了那句話的來源。golden 之所以移動，正是因為它是 `883e9ca` 沒碰到的另一半。

**死守衛登記已更正。** `REGRESSION_GUARDS.yaml` 把
`test_generated_output_matches_golden` 登記為「hand-edit landing directly on a
generated .js file」的修復——它結構上偵測不到那件事。這條登記錯誤是這五輪
沒人補上 `--check` 的原因：**registry 說這一類已經關了。**
R20 站4 的守衛條目自己寫過「`--check` had been reporting 5/8 DRIFT since
e2b98b6」「the two commits after it did not run `--check`」—— 看見了，沒有關掉，這是第二次。

### 一項計畫外的發現（測試前提錯，不是 repo 錯）

完備性測試第一版斷言「每支 shipped `.js` 都由生成器產生」，抓到
`bug-hunt-crg.js` 與 `standalone-mutmut.js`。這兩支是**刻意手維護的獨立工具**
（不是 pipeline phase），Round 11 從未打算遷移它們。錯的是我的前提。
改為具名清單 + 陳舊名稱負控制：新檔案仍會被擋，既有兩支不需搬遷。

### 明列不做（附再開條件）

- **不新增 pre-commit hook step**：現行 hook 全部 non-blocking，加一個會擋的
  step 是政策變更。測試已在 CI 擋住。若哪天 hook 政策改為可阻擋，一併重議。
- **不新增 CI step**：測試套件本來就在 CI 跑，再加一個 step 就是第二份陳述，
  正是本輪在修的東西。
- **`.methodology/phaseN_plan.md` 不納入 shipped-parity**：那是 per-project
  執行期狀態，`plan-all` 每次重生，與受版控的 build artifact 不同類。
  若它哪天變成受版控的交付面再開。
- **`47ec3fd` 那次翻轉本身不重議**：本輪認定 `_DEFAULTS` 為權威、其餘五份為錯。
  若老闆的意思是預設該關，那是**另一個決定**（改 `_DEFAULTS` 一處），
  本輪的機制修復不受影響——這正是把值收斂到一處的好處。

### 承 / 啟

- 承 R11（workflowgen 遷移）、R17 站1（prompt↔SSOT 綁定）、R20 站4（同一個
  `--check` 缺口的第一次現身）、R23 站2（死守衛同型）、R33 站1（`anchor_for`
  render-from-SSOT 前例）。
- **替代假說**：「shipped 與 generated 必須位元組相同」也可能存在刻意例外
  （某支 JS 曾被允許先手動落地）。反面證據：`--check` 的存在本身就是這條規則的宣告，
  且 R20 站4 把 DRIFT 記為缺陷而非設計；本輪實測 9/9 全部可由生成器重現。
- R33 記的「以不同作者的 commit 覆核」**本輪達成**——`883e9ca` 由老闆撰寫，
  本輪是對它的覆核，並推翻了它的歸因層級（葉子 vs 病因）。

---

## Round 37 — 被量測的樹不是被交付的樹

老闆令：查 taskq-renew P1–P8 的執行紀錄與 git history、GitHub 上三個 CI error
的根源，以及 harness 自身 git history，找出其他根本性／結構性問題。

### 量到的事實

**taskq-renew 的 CI 從 Phase 3 起紅到現在：52 次 run，48 紅 4 綠。**
同一段時間本地管線宣告 P1→P8 全 PASS，Gate 4 給 93.6，state.json 進到 Phase 9。
老闆的 `1b89c28`（gitlink）與 `436604b`（requirements ResolutionImpossible）
已修掉兩條，**三個 CI error 在本輪開始時仍全紅**。

### D1 — 掃描範圍不是交付範圍（2/3 個 CI error）

| 環境 | total_links | content_sha256 |
|---|---|---|
| 本地（`.claude/worktrees/` 在） | 80 | `94a71dc…` |
| 本地把 `.claude` 藏起來 | **48** | **`3013d0f…`** |
| CI 實測 | 48 | `3013d0f…` |

attestation 的 `code_files` 有 32 筆指向 `.claude/worktrees/agent-afc91004aadf0cef0/…`
——Agent 工具的 scratch worktree，在本地存在、在 CI 不存在。

根因在 harness：`core/utils/lang_patterns.py:37 SKIP_DIRS` 沒有 `.claude`，
而 `harness/git_strategy.py:98` 有（`1b89c28` 剛加的）。
**同一個命題兩份陳述，只修了 git 那份。同形兄弟未掃齊，第四次。**
`cli/_shared.ensure_fresh_attestation`（R12 站2b 自癒）每次 gate commit 前
用本地範圍重算，把汙染值一次次重新烤進 attestation → 本地永遠自洽。

**正解不是更長的拒絕清單**（`.venv` → `.claude/worktrees` → 下一個，已實證兩次），
而是把範圍錨在 CI checkout 的定義上：`git ls-files --cached --others --exclude-standard`。
`--others --exclude-standard` 保留「已寫好未 commit」的 TDD 檔案，
所以 P3 不會出現假阻擋；`.gitignore` 一行同時驅動 git 與每個掃描器。

### D2 — 架構分數量在一張 11 檔的陳舊快取上（1/3 個 CI error）

| graph.db | last_build_type | files | nodes | communities | architecture_score |
|---|---|---|---|---|---|
| taskq-renew 本地 | `incremental` | **11** | 165 | 12 | 77.8 |
| 乾淨 clone 全量重建 | `full` | **47** | 802 | 32 | **57.1**（＝CI） |

`harness/crg_independent.py` 的 `update if graph.db exists`。Gate 3/Gate 4 的
composite 折進去的 architecture 就是 77.8；Gate 4 的 93.6 由此而來，
**量在 47 檔裡的 11 檔（23%）上**。
加成傷口：`cmd_finalize_gate` 無條件把 77.8 寫成 P6 baseline，
而 `gate4_p6_full.yaml` 自己寫 architecture 門檻 80。

### S1 / S2 — 兩個結構性缺口

- **S1**：`core/` `cli/` `harness/` `scripts/` `.claude/workflows/` 全域搜尋，
  **沒有任何一處讀 GitHub Actions 的 run conclusion**。push-milestone 驗證的是
  「push 有沒有發生」，從不驗證「push 的結果是什麼」。這是 D1/D2 能活 50 次推送的原因。
- **S2**：`templates/harness_quality_gate.yml` 5 處 + `harness_ci.yml` 3 處
  `pip install … || true`。`436604b` 自述：這個吞噬讓 ResolutionImpossible 靜默通過，
  三步之後以 `ModuleNotFoundError: yaml` 現身——**基礎設施失敗偽裝成內容失敗**。

### 母體（第九次）

> **被量測的輸入，不是被交付的輸入。**

R33 一份合約五個陳述 → R34 兩種語意一個時點 → R36 一個 default 六份陳述
（被消費≠被驗證）。前三輪修的都是**陳述面**；本輪是**輸入面**：
兩個獨立掃描器各自定義「這個專案是什麼」，沒有一個錨在 CI 唯一採用的那個定義——git。
兩者都讓數字**偏高**，兩者都被 gate 消費。

### 三項在實作中被測試推翻的設計（記錄，不是事後合理化）

1. **delivery_scope 的 lru_cache**：`core/auto_fix/strategies.py` 寫入新測試檔後
   立刻重掃驗證，快取住的答案（寫入前取得）看不到它 →
   `test_auto_fix_applies_annotation_and_passes_verify` 轉紅。
   **陳舊的樹視圖正是本模組要消除的缺陷**，不能在更短的時間尺度上重新製造一個。快取移除。
2. **CI verdict 的輪詢範圍**：第一版對三種 unavailable 都輪詢，
   包含「沒有 origin remote」——等待不會讓 remote 長出來，單元測試因此各燒 300 秒。
   改為只在 retryable（run 還沒出現／還在跑）時輪詢。
3. **`git.enabled` 作為判準**：FakeGit 沒有這個屬性。改為 `cli/_shared.git_enabled(args)`，
   `_make_git` 也讀它——「一個命題一份陳述」本身就是本輪的主題。

### 明列不做（附再開條件）

- **doctor 不接 CI verdict**（雖然核准的計畫寫了）：每次 doctor 都發一次
  `gh run list` 會讓這個離線的、at-rest 的跨檔一致性檢查變成網路相依。
  push path 才是執法點，degradation ledger 已記錄。理由留在 `core/doctor.py`
  原本要放檢查的位置。若 doctor 哪天長出 `--online` 模式再開。
- **無 origin remote ⇒ post-push CI gate 回 0**（並印出「not applicable」）：
  沒有 remote 就沒有 CI 可以紅。這是唯一一種「真的不適用」而非「不知道」的
  unavailable，明講而不是靜默跳過。
- **`ci_verdict_wait` 不進 harness_config**：一個常數 + `--wait` 覆寫已足夠
  （taskq-renew 的 run 15s–2m）。若某專案真的需要 per-project 值再開。
- **非 git repo 路徑保持原行為**（退回 SKIP_DIRS），不動與本輪缺陷無關的專案。
- **taskq-renew 的 Gate 4 = 93.6 不重判**：那是舊判定的效力問題，屬裁決不屬修缺陷。
  本輪只保證下一次量的是對的樹。

### 承 / 啟

- 承 R8 站2/站3（同形兄弟未掃齊）、R12 站2b（attestation 自癒——本輪發現它會
  把汙染值重新烤進去）、R13/R26（INFRA 不得偽裝成 CODE 失敗）、
  R18 站2（gate_configs YAML 是門檻唯一真值）、R30/R31（分母隨數字走）、
  R32/R35（量不出來不是零分）。
- **替代假說**：也可能存在刻意的本地／CI 差異（例如想把 worktree 裡的實作
  也算進覆蓋率，好讓 TDD 中途不被擋）。反面證據：attestation 的存在本身就是
  「本地與 CI 必須同值」的宣告，且 worktree 是 Agent 工具的臨時隔離區、不是交付面。
- **啟**：下一輪 E2E 應以 taskq-renew 為對照組——重生 attestation + 重建 CRG 圖後
  三個 CI job 是否轉綠，是本輪修復的唯一終局驗證。

---

## Round 38（2026-08-06）—— 一個維度三個執法者

老闆問的是一個具體問題：**CRG Architecture Gate 的缺陷，完整再跑一次 P1–P8
能提早檢測出來並自我修復嗎？** 答案是「能檢測、不能修復」，而追下去挖到的
是 R37 的下一層。

### 病因完全重現（乾淨 clone，唯讀）

| 時點 | graph 檔數 | architecture |
|---|---|---|
| P2 出口（首次建圖） | **1** | **100.0** |
| P3 出口（R37 前，走 `update`） | 1 | 100.0（假通過） |
| P3 出口（R37 後） | **41** | **16.7** |

P2 時專案幾乎沒有程式碼，Leiden 對單檔給滿分；之後每次都 `update`，圖再沒長
回來。`crg_baseline_p4.json` 的 100.0（9 communities）就是這麼來的。R37 的
對賬實地觸發：`[DEGRADED] crg:graph-scope: incremental graph covered 1 of 41
delivered source file(s) — rebuilding in full`。

### 自我修復不存在，三個獨立原因

1. `core/auto_fix/strategies.py::STRATEGY_REGISTRY` 13 個 key，沒有一個對應
   architecture。框架自己在 `cli/gate_cmds.py` 寫過 auto_fix「only emit
   stubs/comments」。
2. `DIMENSION_HINTS["architecture"]` 把 DA waiver 列為選項 (1)，重構列在後面；
   `WAIVABLE_DIMENSIONS` 讓 architecture 成為唯一可豁免維度。
3. `cmd_crg_arch_check` 完全沒有讀 da_waiver 的邏輯。

### C —— 一個維度三個執法者，彼此矛盾

| 執法者 | architecture 門檻 | 認豁免 |
|---|---|---|
| CI job `CRG Architecture Gate (P3+)`（`PHASE>=3`） | 絕對樓地板 80 | ❌ |
| workflow JS gate2/3/4-verify（`crg_rc === 0`） | 絕對樓地板 80 | ❌ |
| `gate2_p3_exit.yaml` | **沒有 architecture 維度** | — |
| `gate3` / `gate4` yaml | threshold 80 | ✅ 可豁免 |

於是 agent 依框架的 hint 填豁免 → `finalize_gate` PASS → `crg-arch-check` 仍回
1 → `gateNPass=false` → 三輪 → error。**框架開出的處方，滿足不了框架自己跑的
檢查。** taskq-renew 的 `gate4_result.json` 確實有 `da_waiver:
{"architecture": true}`，而其證據寫的 `storage-load-sub1/sub2` 只存在於
R37 診斷出的 11/47 檔截斷圖裡——**豁免的前提由量測缺陷造出**。

### D —— 一個門檻九份陳述

gate2 缺、gate3/gate4 各一、`spec_phase{3,4,6}.py` 的 `crg_threshold=80.0`
×3、同三檔散文的 `--threshold 80.0`、CI 模板 `--threshold 80`、argparse
`default=80.0`。唯一從 SSOT 讀的是 R37 建的 `crg_baseline.py`。

### E —— 判定不落盤

`crg_rc` 在 taskq-renew 整個 `.methodology/` 零命中。因此「P6 baseline 77.8
（<80）但 gate4-verify 一輪就 PASS」這個矛盾**事後無法裁決**——可能是兩步之間
`update` 讓分數越過 80，也可能是 RC 回報不實，兩者都是缺陷，沒有紀錄能區分。

> **母體第十次：判定的效力範圍，窄於判定被信任的範圍。**
> R37 修的是「量在哪棵樹上」；本輪修的是「量完之後，這個判定對誰有效、留在哪」。

### 裁決

| # | 主張 | 裁決 |
|---|---|---|
| C | gate 2 補 architecture（weight 0.00） | **採納**（站1）。補的是漏的那一份，不是新增政策——CI 與 workflow 早就在執法。 |
| C′ | CRG override 的觸發改讀 gate config，agent 省略時附加 | **採納**（站1）。原本 gate 2 的新條目會是裝飾品；這也是 gates 3/4 一直存在的活缺口。 |
| D | 門檻收斂到 gate config，`--threshold` 沒有人傳 | **採納**（站2）。減法：參數消失，陳述隨之消失。 |
| B2/B3 | 移除豁免的效力，保留對豁免請求的拒絕 | **採納**（站3，老闆裁定）。校準寫在 committed 的 config，兩個執法者都看得到；豁免只有一個看得到。 |
| E | `verify-gate` 落盤 + advance-phase 對賬 | **採納**（站4）。三個轉述數字縮成一個，且那一個有帳本佐證。 |

### 兩個被自己抓到的錯誤（都由測試發現）

1. **advance-phase 的檢查放錯位置。** 第一版放在既有的 exit-gate 驗證處，那
   已經在 advance-phase 寫過 `setup.cfg`（P2→P3 mutation-scope 同步）之後——
   它比對的是 advance-phase 自己剛改過的樹，會擋掉每一次 P2→P3。現已前移到
   本命令所有寫入之前。
2. **站1 的 commit 留下紅色的 file-size ratchet。** 只讀了測試輸出的 tail 而
   漏掉。天花板在站2 補上，遲了一個 commit，理由記在條目旁。

### 明列不做（附再開條件）

- **R38-DEFER-1**：`harness_config` 的 `is_dim_disabled("architecture")` 能讓
  `crg-arch-check` 直接 `return 0`，且沒有審計軌跡。這是第四個執法者。本輪不動
  （會牽動所有維度的 disable 語意）。老闆要收緊再開。
- **R38-DEFER-2**：prompt 散文仍逐一列舉每個維度的門檻（`15 dims: linting(90)
  … architecture(80) …`），`pass_line_desc` 也重述「CRG architecture ≥80」。
  同一缺陷跨 13 維度 3 gate，是比本輪更大的一次改動。
- **不重判 taskq-renew 的 Gate 4 = 93.6**：舊判定的效力屬裁決不屬修缺陷。
- **不移除 `devil_advocate_evidence` 驗證**：Gate 4 A3 對所有 Tier-3 維度仍用它，
  與豁免無關。
- **不對既有專案 grandfather `gate_verify` 記錄**：第一次會被擋，跑一次
  `verify-gate` 即可。允許無記錄通過等於本輪什麼都沒做。

### 驗證

pytest 6789 → **6816** passed / 4 skipped；guards 304 → **319**；
`--check` 9/9；ruff clean；`node --check` 11/11；sim 91/91 → **94/94**。
五條反證各自轉紅後以反向編輯還原，五個檔案位元組相同。
四個消費專案（taskq / taskq-plus / taskq-renew / run-all-by-workflow）唯讀量到的
architecture 樓地板都仍是 **80.0**——數值不變，來源改變。
