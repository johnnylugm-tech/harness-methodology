# Proposal Adjudications — 提案裁決賬本

> **協議**:任何新 Gap 分析報告或優化提案,執行前**先查此賬本**。若主張已有條目,且該條目的
> re-open condition 尚未滿足,**引用條目編號駁回,不重複查證**。只有 re-open condition 已滿足,
> 或主張與所有既有條目在技術內容上確實不同,才展開新一輪查證。
>
> 本賬本只存**判定**與 **re-open 條件**,不複製論證全文——論證與實測數字的單一真相來源(SSOT)
> 是各自的詳細出處文件;在此重複會製造雙源漂移,兩處各改一半就對不上。

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
