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
