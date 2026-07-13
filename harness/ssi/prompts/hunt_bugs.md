# Adversarial Bug-Hunt Protocol (Gate 3 — adversarial_review)

> **Canonicalized from the tts-new hunt** (50 confirmed / 9 refuted after a
> near-perfect Gate 4 — the run that motivated this dimension). This document
> is the AUTHORITATIVE protocol; `templates/workflows/hunt-bugs.js` is a
> Claude Code dynamic-workflow reference implementation of it.
>
> Output contract: `.methodology/bug_hunt_report.json` per
> `schemas/bug_hunt_report.schema.json` (+ a human-readable markdown report in
> `03-development/.audit/`). The `adversarial_review` Gate-3 dimension is
> computed by the framework (`core/quality_gate/bug_hunt_verifier.py`) from
> that JSON — **do NOT write a `.sessi-work` score file for this dimension.**

---

## Execution Contract(強制)

- **異源模型**:hunt/verify agents 應使用與開發此代碼庫不同的模型(降低
  spec→測試→實作的同源盲點 — 同一模型會驗證自己的誤解)。
- **前置**:CRG graph 已建(`code-review-graph build`);targeting manifest
  已生成(`python harness_cli.py bug-hunt-targets --project .` →
  `.methodology/bug_hunt_targets.json`)。
- **不可造假**:每個 finding 必須引用真實 `file:line` 與 verbatim code
  snippet;verifier 必須實際 Read 檔案。發明 bug 充數 = 整份報告無效。
- **Quality > quantity**:某 (module, lens) 無真 bug 就回空 findings。

## Phase 1 — CRG Scout(1 agent)

讀 `bug_hunt_targets.json`。對每個 target 模組呼叫 CRG
`get_review_context`(include_source, max_depth=2);對 high_risk 模組另查
`tests_for` / `callers_of`;`list_flows` 取系統級 flows。輸出 ≤5000 字的
掃描上下文(模組 → key functions @ line / callers / test coverage /
suspicious patterns),供所有 hunters 共用。

**Survivor 線索**:manifest 的 `mutation_survivors` 條目 = 「沒有任何測試
assert 的行為」。Scout 必須把 survivor 對應的函式標注為 PRIORITY,hunters
優先審查這些函式。

**威脅模型線索(Round 10)**:manifest 的 `threat_model` 條目來自
`SAD.md` §6 的 STRIDE-lite 威脅模型(`applicability: full` 時才存在)——每條
是設計階段**已宣告**的具體攻擊向量(`category`/`description`/`owner_module`/
`boundary`),不是掃描器猜的。Scout 必須把每個 threat 的 `owner_module`
標注為 PRIORITY 並在掃描上下文附上該 threat 的 `description`,hunters
針對該向量**強制**驗證:宣告的 `mitigation` 是否真的擋住這個攻擊(而非只
是存在防禦性程式碼)。

## Phase 2 — Hunt(平行 agents)

配對規則(來自 manifest):`high_risk` 模組 × 3 specialist lenses;
`standard` 模組 × 1 general lens。

### Lenses(定義原文,不可稀釋)

| lens | focus |
|---|---|
| correctness | Business logic errors, boundary conditions, null/empty handling, off-by-one, type mismatches, incorrect assumptions about input data. |
| concurrency | Race conditions, thread safety, async/await issues, shared mutable state, lock ordering, ordering of side effects, lifecycle of long-lived objects across awaits. |
| resilience | Error handling gaps, missing timeouts, broken fallbacks, resource leaks (files/sockets/connections/child procs), partial-failure handling, error swallowing, NFR compliance for degraded modes. |
| general | Any concrete, reachable bug — wrong return type, broken validation, dead branch, leaked resource, missing rollback, incorrect status code, log/PII leak, input size limit (DoS), wrong default. Skip stylistic nits and hypotheticals. |

### Hunter 規則

1. 用 Read 完整讀目標檔;需要呼叫關係時用 CRG query(callers_of/callees_of/tests_for)。
2. Bug 必須在當前 code path **可達**,且有具體 failure scenario。
3. 每個 finding 輸出 strict JSON(schema 見 `bug_hunt_report.schema.json`
   findings item;此階段尚無 `confirmed`/`resolution` 欄位):
   module, lens, severity(critical|high|medium|low), title, description,
   file, line_start, line_end, code_snippet(≤8 行 verbatim), reasoning
   (引用證明該行有 bug 的具體行號 + 觸發輸入/場景), suggested_fix(≤5 行),
   confidence(high|medium|low)。

### Severity rubric

- **critical**:資料遺失/錯誤結果回傳給呼叫者、功能整體不可達(死碼路徑)、
  吞掉 CancelledError/SystemExit 級別的控制流破壞
- **high**:可達的 hang/leak/race(生產事故級)、安全邊界缺失
- **medium**:特定條件下的錯誤行為、可恢復的資源問題
- **low**:防禦性缺失、文案/警告一致性

## Phase 3 — Adversarial Verify(每 finding 2 個平行 verifiers)

- **Refuter**:預設 `is_real=false`,除非無法以具體證據反駁。檢查:引用的
  代碼確實在該行?周圍已有 guard/fallback 處理?failure scenario 真的可達?
  反駁必須引用行號。
- **Confirmer**:預設 `is_real=false`,除非能證明。追 data flow(輸入 X 真的
  到達 buggy line?),查 `tests_for`(已有測試覆蓋該路徑且通過 → 很可能已
  處理),只有能描述「具體觸發 + 預期 vs 實際」才確認。
- **確認規則(嚴格版)**:`confirmed = true` 需要 **2/2 verifiers is_real**,
  或 **1/2 is_real 且該 verifier 的 evidence 含具體行號引用**。其餘 →
  `confirmed = false`(列入報告的 refuted 區,附 refutation)。

## Phase 4 — Synthesize(1 agent)

1. **JSON 工件**(gate 的輸入):寫 `.methodology/bug_hunt_report.json` —
   top-level `generated_at`/`git_sha`(掃描時 HEAD)/`targets_manifest`/
   `lenses`/`raw_count`/`confirmed_count`/`refuted_count`/`findings[]`。
   每個 finding 補 `id`(`<module>#<n>`)、`confirmed`、`verify_evidence`,
   並初始化 `resolution: {"status": "open"}`(confirmed)或直接記
   refutation(unconfirmed → `resolution.status: "refuted"` +
   `refute_evidence` 取自 refuter)。
2. **Markdown 報告**(人讀):`03-development/.audit/bug-report-<date>.md`,
   繁體中文,≤2000 字 — 掃描摘要表(module × severity)、確認 bugs
   (severity 降序,每條:模組/位置、問題、證據、修復)、被反駁清單(一句
   理由)、修復優先順序、掃描方法。引 `file:line`,不貼長代碼。

## Post-hunt — Resolution(Gate 3 放行條件)

`bug_hunt_verifier` 規則(Critical + High 都 block):

| status | 要求 |
|---|---|
| `open` | confirmed critical/high → **Gate 3 BLOCKED**,必須轉為 resolved 或 refuted |
| `resolved` | 需 `fix_commit`(修復 commit sha)**或** `repro_test`(專案內真實存在的測試檔,先 RED 重現再修到 GREEN — anti-fabrication) |
| `refuted` | 需 `refute_evidence`(反例引用或文件化例外) |

medium/low 與 unconfirmed findings 不擋 gate(留檔追蹤)。報告 `git_sha`
與 HEAD 不符只警告不擋 — 大幅改動後建議重跑 hunt。

## 與其他機制的分工

- 靜態可確定的(subprocess 無 timeout、TOCTOU、except BaseException、
  config 死鍵)由 **preflight battery** 與 **error_handling 維度**先攔 —
  hunters 不必重複報告 preflight 已 block 的項目。
- mutation survivors 由 targeting manifest 餵入(Phase 1)— survivor triage
  是 hunt 的輸入,不是獨立 gate 項。
