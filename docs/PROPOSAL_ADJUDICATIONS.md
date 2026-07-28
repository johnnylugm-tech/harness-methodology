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
| R21-D′ | (計畫外查證)SAB 的 `gate_score_overrides` 是否被 waiver 繞過 | **前提不成立,不修** | grep 全 repo:`gate_score_overrides` 只在 `sab_parser.py` 產生與序列化,**零消費點**——NFR floor 從一開始就沒被套用到 gate 門檻,所以「被 waiver 繞過」不是活傷口。誠實記為獨立缺口,不在本輪範圍 | NFR floor 接線是獨立議題;若要做,先確認它與 `_dim_thresholds` 的合併語意 |

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
