# Claude Code Workflow Tool — Playbook

> 整合官方開發手冊 (https://code.claude.com/docs/en/workflows) 與
> harness-methodology 四輪迭代 (v1 → v4，於 integration-test 消費端實跑)
> 踩過的所有坑。
>
> **撰寫日期**: 2026-06-20 · **收編為 harness SSOT**: 2026-07-15（Round 11 站5）
> **基於 Claude Code v2.1.183**
> **對象**: 開發者（改 `scripts/workflowgen/` 生成器）+ 維運者（監看 / 調試
> workflow run，通常在 integration-test 消費端）
> **專案上下文**: harness-methodology 8-phase workflow（`.claude/workflows/
> phaseN-*.js`，Round 11 起由 `scripts/workflowgen/` 生成）
>
> **來源**: 本文件原生成於 integration-test 專案（`init-project` 產出的
> per-project 副本，`.methodology/workflow-playbook.md`）。harness-methodology
> 本身——實際撰寫並生成這 8 個 workflow 檔案的框架——過去沒有這份文件，
> 造成方法論文件本身的回歸缺口（Round 11 站0 審計發現）。本文件現在
> 收編為 harness 側 SSOT；integration-test 的 per-project 副本維持原樣
> 不動（歷史留存），未來的維護以本檔案為準。§13 是收編時新增的維護章，
> 記錄生成流程與 submodule 消費規則，其餘章節內容為 Claude Code Workflow
> 的通用行為與踩坑記錄，不隨收編而改變。

---

## 1. 什麼是 Workflow(快速對齊)

| 物件 | 誰持有計畫 | 中間結果 | 中斷恢復 | 規模 |
|------|----------|---------|---------|------|
| **Subagent** | Claude 每輪決策 | 進入主對話 context | 整輪重來 | 每輪幾個 |
| **Skill** | Claude 依照指示 | 進入主對話 context | 重來 | 同 subagent |
| **Agent team** | Lead agent 監看 peer | 共享 task list | Peer 持續跑 | 一群長期 |
| **Workflow** | **Script 本身** | **Script 變數** | **同 session 內 resume** | **數十到數百個 agents** |

**Workflow = 把 plan 寫成 code**。Script 持有 loop、branching、中間結果;主對話 context 只看到最後答案。

**決策何時用 workflow**(滿足任一):
- 任務需要比單一對話能協調更多的 agents
- 想要 orchestration 寫成可讀、可重跑的 script
- 想套用可重複的 quality pattern(例: 對抗式 review、多角度草稿、獨立交叉查證)

**不要用 workflow**(滿足任一):
- 任務可在主對話 1-2 輪解決
- 結果只是探索性單一答案(不需要 adversarial review)
- 任務需要 mid-run 的人為決策 → 拆成多個小 workflow(因為 runtime 無 mid-run user input)

---

## 2. 檔案位置與啟動方式

### 存放位置(由近到遠)

| 路徑 | 範圍 | 適用 |
|------|------|------|
| `./<cwd>/.claude/workflows/<name>.js` | 該 package | 套件層級 workflow |
| `./<repo>/.claude/workflows/<name>.js` | 該 repo | 專案層級 (建議放這裡) |
| `~/.claude/workflows/<name>.js` | 全域個人 | 個人跨專案用 |

**Monorepo 行為** (v2.1.178+): 從 cwd 沿路往上找最近的 `.claude/workflows/`;若多個 `.claude/` 都定義同名 workflow,執行最近的那個。

**衝突規則**: Project workflow 優先於 Personal workflow(同名時)。

### 啟動方式

```bash
# 1. Slash command (最常用)
> /my-workflow-name arg1 arg2

# 2. /workflows view → 按 Enter → 選 saved workflow
> /workflows

# 3. 透過 ultracode 自動觸發
> ultracode: audit every API endpoint under src/routes/ for missing auth checks
> /effort ultracode    # 全 session 自動用 workflow

# 4. Programmatic(Agent 內)
Workflow({ script: "..." })              # inline script
Workflow({ scriptPath: "/abs/path.js" }) # 絕對路徑 (推薦)
Workflow({ name: "phase1-requirements" })# 用 name (有 cache 風險,見 §6.5)
Workflow({ scriptPath, resumeFromRunId: "wf_xxx" })  # resume
```

---

## 3. Script 結構 — meta 物件

### 必要欄位

```javascript
export const meta = {
  name: 'phase1-requirements',              // 必填,識別用
  description: 'Phase 1 Requirements ...',  // 必填,出現在 /workflows list
  whenToUse: 'optional; 顯示在 workflow list', // optional
  phases: [                                 // optional,顯示在 progress view
    { title: 'Preflight' },
    { title: 'Sub-Task 1/4 — SRS.md' },
    ...
  ],
}
```

### meta 規則(Validator hard errors)

| 規則 | 違規 → 結果 |
|------|-----------|
| 必須 FIRST statement | `ERROR: export const meta must be the FIRST statement` |
| 必須是純 literal | `ERROR: meta contains a spread`, `ERROR: meta contains a template literal`, `ERROR: meta appears to contain a function call` |
| 不能用 `__proto__` / `constructor` / `prototype` 當 key | `ERROR: meta uses reserved key` |
| 必須含 `name` 欄位 | `ERROR: meta is missing a name field` |
| 必須含 `description` 欄位 | `ERROR: meta is missing a description field` |

**正確範例**:
```javascript
export const meta = {
  name: 'my-workflow',
  description: 'Does X then Y',
  phases: [{ title: 'Phase 1' }, { title: 'Phase 2' }],
}
```

**錯誤範例**(會被 validator 擋):
```javascript
const prefix = 'my-'
export const meta = { name: prefix + 'workflow', ... }  // ❌ 函式呼叫
export const meta = { name: 'my-workflow', desc: `template` } // ❌ template literal
export const meta = { name: 'my-workflow', __proto__: {} }   // ❌ reserved key
```

---

## 4. Script 語法限制 — 什麼不能用

> harness 端執法：`tests/test_workflow_js_conventions.py` 對 8 個生成的
> phase workflow JS 做同一組禁令的機械掃描（comment/string-aware，見
> `scripts/workflow_audit/js_lint.py`），任何一項在 `scripts/workflowgen/`
> 生成結果中復發都會在這個 repo 的 pytest 就擋下，不必等到 integration-test
> 端真的啟動 workflow 才失敗。

### Hard errors (validator + runtime 都會擋)

| 違規 | 原因 | 解法 |
|------|------|------|
| `Date.now()` | 破壞 resume(時間漂移) | 由 args 傳入 timestamp,或 workflow 回傳後再 stamp |
| `Math.random()` | 破壞 resume | 由 args 傳入 seed,或用 prompt 加 index 區分 |
| `new Date()` 無參數 | 同上 | 同上 |
| 檔案大小 > 524288 bytes (512 KB) | runtime 拒絕解析 | 拆 workflow 或縮減 prompt |

### Warnings (validator 警告 + runtime 直接 throw)

| 違規 | Runtime 行為 | 解法 |
|------|-------------|------|
| `import('node:fs')` / `await import()` | **runtime 直接 throw** | 用 `agent()` 委派檔案 I/O |
| `fs.*` / `path.*` / `process.*` / `require()` | runtime 直接 throw | 同上 |
| `import ... from ...` (靜態) | runtime 直接 throw | 同上 |

> **v1 踩坑**: shipped workflow 用了 `const fs = await import('node:fs')`。
> Validator 只 warn,runtime 直接 throw — 因為 script 沒有 Node API 存取權。
> **正確解法**: 從 script 移除所有 host API;檔案讀寫交給 agent()。

### 語言限制

- ✅ **純 JavaScript** (不用 TypeScript,type annotation 會 parse error)
- ✅ 無 `import()` / `require()`
- ❌ TypeScript 型別註記(會被 parser 拒絕)
- ❌ `node:fs` / `node:path` 等 Node 模組

### Runtime 限制 (runtime 的額外約束,validator 抓不到)

| 約束 | 影響 |
|------|------|
| 無 mid-run user input | 唯一能 pause 的是 agent permission prompt;要在 stage 之間人工 sign-off → 拆成多個 workflow |
| 無 fs / shell access from script | 所有 I/O 透過 agent() |
| ≤ 16 concurrent agents (CPU cores - 2 取小) | 超過會 queue |
| ≤ 1000 agents total per run | runaway backstop |
| ≤ 4096 items per parallel/pipeline 呼叫 | 超過是顯式 error |

### 4.1 Runtime 的錯誤處理能力 = 終止（Round 28 量測）

**runtime 對錯誤只有一種反應：結束整個 run。** 它不重試、不隔離失敗的 stage、
不降級、不跨 session 續跑。這決定了一件事:**所有容錯都必須寫在生成的 JS 裡**。

| 情境 | runtime 行為 | harness 端的對策 |
|------|-------------|-----------------|
| script 頂層 throw / unhandled rejection | run 死,**不產生任何結果** —— 沒有 phase、沒有原因、沒有續跑點 | `generate_workflows._wrap_top_level_boundary` 給每支生成的 phase JS 一個頂層 try/catch;run-all 另有 driver 的 per-phase try/catch |
| `agent()` 內部 reject(transient transport error) | 原樣往上拋,runtime 不接 | 同上。這是 2026-07-30 那次「83 個 dispatch / 3 小時後整個 run 死掉」的形狀 |
| `agent()` resolve 成 null / 空字串(session limit) | 正常回傳,**不是錯誤** | 每個呼叫點自己判 `length < 10` → `session_limit_blocked` |
| phase 回傳了 driver 不認得的形狀 | runtime 不管 | run-all fail closed:只有 `phase_complete: true` 才續跑(§13.2) |
| 檔案 > 512 KB | 拒絕解析 | `RUNALL_MAX_BYTES` 餘裕 ratchet |

**量測方法(可重跑)**:`sim_runner.test.mjs` 對每支檔案的每個 dispatch label
逐一注入一個 rejecting `agent()`,數有幾個會逃逸成 unhandled throw。Round 28
之前:八支獨立 phase JS **84/217 逃逸**,run-all **0/85** —— 差別就是 run-all
有 driver 的 try/catch 而它們沒有。

> **推論(重要)**:因為 runtime 不跨 session resume,而**同 session 的 resume 又
> 要求 script 位元組不變**(§6.3),修好一個 workflow JS bug 之後**不可能真的
> resume** —— cache 從第一個改動的 `agent()` 起全部失效。正解不是去修 resume,
> 而是**讓重新啟動等於續跑**:state.json cursor + 各 phase 的 GUARD/sentinel
> 短路。這也是為什麼「乾淨的中止點」比「聰明的重試」重要。

---

## 5. Script API — agent / parallel / pipeline / phase / log

### 5.1 `agent(prompt, opts)`

```javascript
const result = await agent(prompt, {
  label: 'a1-srs-r1',           // 顯示在 progress view
  phase: 'Sub-Task 1/4',        // 分組到 phase box
  agentType: 'general-purpose', // 'general-purpose' | 'Explore' | 'Plan' | 自訂
  model: 'haiku',               // 覆寫 session model (cheaper reviewers 用 haiku)
  effort: 'medium',             // 'low' | 'medium' | 'high' | 'xhigh' | 'max'
  schema: SCHEMA,               // JSON Schema → 強制 agent 呼叫 StructuredOutput tool
  isolation: 'worktree',        // 給 agent 獨立 git worktree (expensive!)
})
```

**回傳值**:
- 沒 `schema:` → 字串 (agent 最終訊息)
- 有 `schema:` → 已驗證的 object (runtime 幫你 JSON.parse + AJV validate)

### 5.2 `schema:` 行為 — **踩坑重點**(v4 修訂: gate verdict 必須用扁平 schema)

- `schema:` 強制 subagent 呼叫 `StructuredOutput` tool
- Agent 必須以 tool call 形式回 JSON,**不能用 plain text**
- 若 agent 回 text, runtime 會 retry 2 次 → 仍失敗 → **整個 workflow fail**

> **v2 踩坑**: workflow 用 `schema: B_SCHEMA` 給 B-review agent。
> 一個 B-review agent 多次返回 JSON-as-text,runtime throw:
> `agent({schema}): subagent completed without calling StructuredOutput (after 2 in-conversation nudges)`
> 整個 workflow 直接 fail。

> **v3 曾經的解法(已推翻)**: 全面禁用 `schema:`,改用 balanced-brace parser 解析 prose。
> **v4 檢討(2026-07-02)**: v3 的全面禁令本身是 workaround——它把「大聲失敗、有驗證、有 retry」
> 換成「無聲有損通道」。之後的 #126/#134/#135/#136/ENV_CHECK_RC-paraphrase 全部是
> regex-over-LLM-prose 這個類別的下游發作(agent 會改寫、摘要、腦補,即使 prompt 寫了
> "verbatim. Do not paraphrase")。v2 真正的教訓是「**複雜巢狀 schema × 重認知 agent**
> 才會 compliance 失敗」,不是 schema 機制本身。

**v4 規則(現行,harness 端執法見 `scripts/workflowgen/js_blocks.py` 的
`render_gate_loop`/`render_persist_approval` 等共用區塊生成器)**:

1. **Gate verdict(PASS/FAIL 判定)必須用扁平小 schema**(2-3 個欄位,如
   `{pass: boolean, reason: string}` / `{rc: integer}`)搭配 bash-proxy agent。
   禁止 regex 比對 agent prose 作為 gate 判定。
2. **Verdict authority**: 重認知 orchestrator(TDD/Gate2-4/Advance)保留 prose 敘事,
   其 PASS/FAIL **永不**從 prose 解析——由獨立的 schema proxy agent 讀 harness
   權威產物(manifest `quality_complete` / state.json `current_phase` / CLI exit code /
   git log milestone commit)。
3. **複雜巢狀輸出 × 重認知 agent**(如 phase6 Peer Review 的 verdicts 陣列)維持
   prose + balanced-brace parser——這是 v2 踩坑仍然成立的唯一範圍。這組純函式
   (`balancedJsonAt`/`extractLastJson`/`parseAgentJson`)現在活在
   `scripts/workflowgen/js_src/json_utils.mjs`,附 `node:test` 單元測試,生成時
   inline 到每個 phase 檔:

```javascript
function balancedJsonAt(text, start) {
  if (text[start] !== '{' && text[start] !== '[') return null
  let depth = 0, inStr = false, esc = false
  for (let i = start; i < text.length; i++) {
    const c = text[i]
    if (esc) { esc = false; continue }
    if (c === '\\') { esc = true; continue }
    if (c === '"') { inStr = !inStr; continue }
    if (inStr) continue
    if (c === '{' || c === '[') depth++
    else if (c === '}' || c === ']') {
      depth--; if (depth === 0) return text.slice(start, i + 1)
    }
  }
  return null
}

function extractLastJson(text) {
  let last = null
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '{' || text[i] === '[') {
      const block = balancedJsonAt(text, i)
      if (block) {
        try { last = JSON.parse(block); i += block.length - 1 } catch {}
      }
    }
  }
  return last
}
```

### 5.3 Schema 必須是 top-level const(踩坑)

> **v2 踩坑**: 在 `agent(prompt, { schema: { type:'object', properties:{...} } })` 裡
> 直接放複雜 inline schema 物件 → runtime parse error: `Unexpected token (330:62)`。

> **正確**: schema 必須是 top-level const(harness 端 `test_workflow_js_conventions.py`
> 的 `test_no_banned_runtime_constructs` 掃描 `schema:\s*\{` 這個 shape,任何生成器
> 回歸都會在這個 repo 的 pytest 被擋下):

```javascript
const B_SCHEMA = {
  type: 'object',
  properties: {
    review_status: { type: 'string', enum: ['APPROVE', 'REJECT'] },
    reason: { type: 'string' },
    citations: { type: 'array', items: { type: 'string' } },
    gaps: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['low', 'medium', 'high'] },
          message: { type: 'string' },
          fr_id: { type: ['string', 'null'] },
        },
        required: ['severity', 'message', 'fr_id'],
        additionalProperties: false,
      },
    },
  },
  required: ['review_status', 'reason', 'citations', 'gaps'],
  additionalProperties: false,
}
```

### 5.4 `parallel(thunks)` vs `pipeline(items, ...stages)`

| 函式 | 語義 | 用法 |
|------|------|------|
| `parallel([t1, t2, ...])` | 所有 thunks 並行,**barrier**:全部完成才回 | 需要 dedup 全部結果、early-exit |
| `pipeline(items, stage1, stage2, ...)` | 每個 item 跑完所有 stages,**stage 間不 barrier** | item 獨立任務串接 |

> **Pipeline by default** — Workflow tool 文件原文:
> "Only reach for a barrier (parallel between stages) when you genuinely need ALL prior-stage results together."

```javascript
// ✅ Pipeline(預設): 3 個 FR 各自 A→B→C,不互相等待
const results = await pipeline(
  frIds,
  fr => agent(`Author ${fr}`, { label: `a-${fr}`, phase: 'Author' }),
  rev => agent(`Review`, { label: `b-${rev.frId}`, phase: 'Review' }),
  fix => agent(`Fix`, { label: `c-${fix.frId}`, phase: 'Fix' }),
)

// ❌ Barrier (不必要): 全部 A 完才跑任何 B
const aResults = await parallel(frIds.map(fr => () => agent(`Author ${fr}`)))
const bResults = await parallel(aResults.map(a => () => agent(`Review ${a.frId}`)))
```

### 5.5 `phase(title)` 與 `log(message)`

```javascript
phase('Sub-Task 1/4 — SRS.md')   // 開始一個 phase box,後續 agent() 歸到這 box
log('SRS.md: Agent A + Agent B') // 顯示一行 narrator 訊息
```

### 5.6 `workflow(nameOrRef, args)` — 巢狀 workflow(只能一層)

```javascript
const result = await workflow('sub-helper', { some: 'input' })
```

- Nesting depth 限制 1 (workflow() 內不能再 workflow())
- 與 parent 共享 concurrency cap + agent counter + token budget

### 5.7 `args` 全域變數

- 由 `Workflow({ args: ... })` 傳入
- 可能是 string (JSON-encoded) 或 object (structured)
- **Workflow tool 文件說**: "Claude passes the list as structured data, so the script can call array and object methods on `args` directly without parsing it first"

> **踩坑(v2)**: 透過某些 Agent tool format 呼叫 `Workflow({ scriptPath: ... })` 時,**args 完全沒傳過去**
> (silently undefined)。當時的解法是設一個寫死的 `DEFAULT_REPO` 常數當 fallback。

> **現行實作(`58f8b2f`,取代上面的 DEFAULT_REPO 寫死路徑)**: `resolveRepo()`
> 優先採用 `args.repo`(要求絕對路徑,否則 throw);沒有 `args.repo` 時改派一個
> agent 用 Bash 從目前 CWD 往上走訪,找到「同時有 `harness_cli.py` 與
> `.methodology/`、且不是 git submodule 工作樹」的目錄。**submodule 排除**是必要
> 條件——harness 框架若以 git submodule 形式掛在專案下(見 §13),`harness/`
> 目錄本身同時符合前兩個條件,樸素 walk-up 會誤停在那裡而非真正的專案根目錄。
> submodule 工作樹的判斷式:`[ -f .git ] && head -1 .git | grep -q "^gitdir: "`
> (submodule 的頂層 `.git` 是檔案,不是目錄,內容以 `gitdir:` 開頭)。實作見
> `scripts/workflowgen/js_blocks.py::RESOLVE_REPO_FN_BLOCK`:

```javascript
async function resolveRepo() {
  if (typeof args === 'string') { try { args = JSON.parse(args) } catch {} }
  let argRepo = ''
  if (args && typeof args === 'object' && typeof args.repo === 'string' && args.repo.length > 0) argRepo = args.repo
  if (argRepo) {
    if (!argRepo.startsWith('/')) {
      throw new Error('[workflow] args.repo must be an absolute path; got "' + argRepo + '"')
    }
    return argRepo
  }
  const r = await agent(
    'You are the REPO RESOLVER. Find the project root by walking up from your current CWD until a directory contains BOTH `harness_cli.py` AND `.methodology/` AND is NOT a git submodule working tree.\n'
    + 'A git submodule working tree is detected by `[ -f .git ] && head -1 .git 2>/dev/null | grep -q "^gitdir: "`.\n'
    + 'Run EXACTLY this command via Bash: cd "$(pwd)"; while [ "$(pwd)" != "/" ] && ! { [ -f harness_cli.py ] && [ -d .methodology ] && ! { [ -f .git ] && head -1 .git 2>/dev/null | grep -q "^gitdir: "; }; }; do cd ..; done; if [ -f harness_cli.py ] && [ -d .methodology ]; then echo "REPO=$(pwd)"; else echo "REPO_NOT_FOUND cwd=$(pwd)"; fi\n'
    + 'Report the literal stdout as your final message.',
    { label: 'resolve-repo', agentType: 'general-purpose' }
  )
  const text = String(r ?? '').trim()
  const match = text.match(/REPO=(\S+)/)
  if (match && match[1].startsWith('/')) return match[1]
  throw new Error('[workflow] REPO not auto-detected (resolver returned: "' + text.slice(0, 200) + '"). Pass args.repo = absolute path or run from inside the project repo.')
}
```

### 5.8 `budget` 物件(token budget tracking)

```javascript
while (budget.total && budget.remaining() > 50_000) {
  const result = await agent("Find bugs", { schema: BUG_SCHEMA })
  bugs.push(...result.bugs)
}
log(`${bugs.length} found, ${Math.round(budget.remaining()/1000)}k remaining`)
```

- `budget.total` = `null` 表示沒設 target
- `budget.spent()` = 整個 turn 主 loop + 所有 workflows 累計
- `budget.remaining()` = `Math.max(0, total - spent())` 或 `Infinity`(沒 target)

---

## 6. 啟動與管理 Workflow

### 6.1 三種 launch 方式

```javascript
Workflow({ script: "..." })                    // inline,debugging
Workflow({ scriptPath: "/abs/path.js" })      // 絕對路徑(推薦)
Workflow({ name: "phase1-requirements" })     // 用 name,從 .claude/workflows/ 找
```

### 6.2 /workflows view 控制

| Key | 動作 |
|-----|------|
| `↑` / `↓` | 選 phase 或 agent |
| `Enter` / `→` | 鑽進去看 prompt + tool calls + result |
| `Esc` | 退出 |
| `j` / `k` | 細節 overflow 時捲動 |
| `p` | pause / resume |
| `x` | 停選定 agent;若焦點在 run 則停整個 workflow |
| `r` | 重啟選定 agent |
| `s` | 存成可重用的 command |

### 6.3 Resume

```javascript
// Pause 後,workflow 內已完成 agent() 回傳 cached result;未跑的才 live 重跑
Workflow({ scriptPath, resumeFromRunId: "wf_xxx" })
```

- **限制**: 必須同 session;離開 Claude Code → 下個 session 從頭跑
- 為了 cache 命中,**script 不能改**;改了只 cache 到第一個改動的 agent() call

### 6.4 Permission prompt

| Permission mode | 啟動時是否問 |
|-----------------|-------------|
| Default / accept edits | 每次都問 (除非已 "don't ask again") |
| Auto | 第一次問,Yes 後記住;ultracode on 時跳過 |
| Bypass permissions / `claude -p` / Agent SDK | 從不問,直接跑 |

Subagent 永遠跑在 `acceptEdits` 模式,繼承你的 tool allowlist;檔案 edit 自動批准。

### 6.5 Name resolver cache bug(踩坑)

> **v2 踩坑**: shipped workflow 檔案已修改(27,123 bytes 新版),
> 但 runtime persisted 還是舊版(19,085 bytes 預先 snapshot)。
> 用 `Workflow({ name: 'phase1-requirements' })` 啟動 → 跑的還是舊版。

> **正確解法**: 用絕對路徑 `scriptPath:` 跳過 name resolver cache。

### 6.6 停不掉舊 run(踩坑)

Agent 工具**無法**直接停 workflow run。只能:
1. 請 user 在 `/workflows` view 按 `x`
2. 或讓 workflow 自己跑到出錯/完成

---

## 7. 子 Agent 與 Tool 行為

### 7.1 Agent 怎麼選 tool

`agentType` 影響可用 tool:
- `general-purpose` — 預設,全部工具
- `Explore` — read-only 搜尋(不可寫)
- `Plan` — read-only 設計規劃

**自訂 agent type** 透過 `subagent_type` (e.g. `Explore`, `code-reviewer`)。

### 7.2 Stateless agent sandbox(踩坑)

B-review agent **無 file access**。`Read` tool 也可能 hallucinate(見 §8.2)。

> **正確解法**: 將需要 review 的完整內容 **embed 在 prompt 內**,絕對不要只給路徑:

```javascript
function buildBPrompt(role, docs, checklist) {
  let p = 'You are ' + role + '.\n'
  p += 'You have NO access to any files — all context is provided below.\n\n'
  for (const [label, content] of docs) {
    p += '=== [' + label + '] ===\n' + content + '\n\n'
  }
  p += 'Review checklist:\n' + checklist + '\n\n'
  p += 'Return JSON only.'
  return p
}
```

### 7.3 Do-not 列表 — 防止 agent over-reach(踩坑)

> **v2 踩坑**: 一個 preflight agent 拿到含完整 P1 plan 的 prompt,
> general-purpose agent 判定「我可以全部做完」,3 分鐘做完整個 P1。

> **正確解法**: 每個 agent prompt 加明確的 **SCOPE RULES (DO NOT)**:

```javascript
const A_SCOPE_RULES = '\n\nSCOPE RULES (you MUST obey):\n'
  + '- DO NOT write any deliverable OTHER than the one specified in step 2.\n'
  + '- DO NOT run git commit, git push, advance-phase, push-checkpoint, or any phase-transition command.\n'
  + '- DO NOT run constitution-check, peer-review, or any quality-gate command.\n'
  + '- DO NOT spawn other agents or do the work of downstream sub-tasks.\n'
  + '- ONLY do steps 1-4 above. Return the JSON when done.\n'
```

---

## 8. 反覆踩過的真實坑(必讀)

### 8.1 P1 ❌ `import('node:fs')` 讓 shipped workflow 起不來

**症狀**: `import() is not available in workflow scripts`

**根因**: Script 想讀檔,寫了 `const fs = await import('node:fs')`。Validator 只 warn,runtime 直接 throw。

**正確解法**: Script 不做 I/O,改叫 `agent()` 帶 Bash tool:

```javascript
const fileContent = await agent(
  'Use Bash to run: cat /abs/path/file.md\n'
  + 'Return the EXACT stdout as your final message. No commentary.',
  { label: 'load-file', agentType: 'general-purpose' }
)
```

### 8.2 P2 ❌ Read tool 會 hallucinate 檔案內容

**症狀**: Brief loader agent 用 Read tool 讀 `PROJECT_BRIEF.md`,
回傳內容卻是 CLAUDE.md / memory 裡的描述(完全不同檔)。

**根因**: LLM agent 的 Read tool 拿到路徑後會用訓練資料「猜」內容;若同目錄有其他檔案,可能混淆。

**正確解法**: 用 Bash `cat` 取絕對位元組 — stdout 唯一通道就是檔案內容,LLM 無法替換:

```javascript
const brief = await agent(
  'Use ONLY the Bash tool. Run EXACTLY: cat ' + REPO + '/PROJECT_BRIEF.md\n'
  + 'Do NOT use Read tool. Return EXACT stdout.',
  { label: 'load-brief', agentType: 'general-purpose' }
)
// + defensive validation: 必須以 "# Project Brief" 開頭 + 長度 >=50
```

### 8.3 P3 ❌ B-review reject loop 沒 revise step → 無限循環

**症狀**: B-agent 一直 REJECT SRS,理由幾乎相同 (placeholder、NFR count)。
A 已經從 on-disk 讀檔返回正確內容,B 的高 severity gaps 是 reviewer 對「完整性」的過度解讀。Loop 跑滿 5 輪還沒收斂。

**正確解法**:
1. Loop 滿 `MAX_B_ROUNDS` 時 **ESCALATE**(return error),不要再 continue
2. A agent 在 round > 1 必須 review previous B-2 review JSON 並**套用 HIGH severity 修正**(surgical Edit,不是 rewrite)

### 8.4 P4 ❌ `schema:` 讓 B-review agent fail

**症狀**: `agent({schema}): subagent completed without calling StructuredOutput (after 2 in-conversation nudges)`
整個 workflow 直接 fail。

**根因**: `schema:` 強制 subagent 呼叫 StructuredOutput tool,不能用 plain text。
某些 B-review agent 偏偏就回 plain-text JSON。

**正確解法**: 移除 `schema:`,用 §5.2 的 balanced-brace parser 自己 parse。

### 8.5 P5 ❌ Schema 必須 top-level const

**症狀**: `Script parse error: Unexpected token (330:62)`

**根因**: Inline 複雜 schema 物件 → runtime parser 拒絕。

**正確解法**: 把所有 schema 提到 top-level `const`,如 §5.3 範例。

### 8.6 P6 ❌ Name resolver 給 stale cache

**症狀**: 改完 workflow 檔,launch 卻跑舊版。

**正確解法**: 用 `scriptPath: '/abs/path.js'` 而非 `name: 'xxx'`。

### 8.7 P7 ❌ Inline schema 改物件後忘了改 schema 物件定義

**症狀**: 改 `schema:` 物件失敗,runtime 報 unexpected token 但 line number 指錯位置。

**正確解法**: §5.3 — top-level const。

### 8.8 P8 ❌ args 不會自動 fallback,workflow 立即 fail

**症狀**: 透過 Agent 工具呼叫 `Workflow({ scriptPath: ... })` 完全沒帶 args → script 內 `args === undefined` → 立即 return error。

**當時的解法**: §5.7 — script 內設 `DEFAULT_XXX` fallback 常數。

**現行解法(取代上面,`58f8b2f`)**: §5.7 的 `resolveRepo()` agent-based
walk-up——沒有 `args.repo` 時不再回退到寫死路徑,而是派一個 agent 用
Bash 從 CWD 往上找,並排除 git submodule 工作樹。這同時解決了「換一台機器
/ 換一個消費專案,寫死路徑就失效」的後續問題。

### 8.9 P9 ❌ Agent 回「BAD JSON shape」繼續 retry 浪費 token

**症狀**: Round 1 agent 返回 invalid JSON (沒 `files[0].content`),script 只 log + continue,跑完整輪才 escalate。

**正確解法**: Parse failure 立即 hard error return,不要 retry(重試不會自己修好):

```javascript
try {
  a = parseAgentJson(aResult, 'A-r' + round)
} catch (e) {
  return { error: 'A parse failed (round ' + round + ')', detail: e.message }
}
```

### 8.11 P11 ❌ TaskStop 殺死 agent → journal result 未寫入 → resume 重複執行

**症狀**: Agent 完成工作（已寫檔案、已回傳 JSON），但 journal 只有 STARTED 沒有 RESULT。Resume 時 agent 被重試。常見於「agent 在主 context 呼叫 TaskStop 時剛好完成最後一步但 runtime 還沒 flush journal」。

**影響**: Agent 重跑一次（通常很快，因為 self-check pattern 會看到檔案已存在），但浪費 token 且 wall-clock 增加。

**正確處理**:
1. **不要在 agent 跑到一半時呼叫 TaskStop**。只在確認 workflow task 已完成（收到通知）或明確中止整個流程時才停。
2. Resume 是無害的：重試 agent 只是重讀已存在的檔案並快速回傳 OK。
3. **設計 Agent A 為冪等**：先 `test -f <file>` → 若 EXISTS 就直接讀 + return OK,不 overwrite。

```javascript
// ✅ Agent A 冪等範例
'1. Self-check: test -f ' + REPO + '/02-architecture/SAD.md && echo EXISTS || echo MISSING\n'
+ '   - If EXISTS: Read it. If complete, return OK without rewriting.\n'
+ '2. If MISSING: author the full deliverable.\n'
+ 'Return compact JSON only.\n'
```

### 8.10 P10 ❌ 兩個 workflow run 競爭同一組 deliverables

**症狀**: 舊 v2 run 還在跑、新的 v3 又 launch → 兩 run 互相覆寫同一個 SRS.md,內容不一致。

**正確解法**: User 必須手動 `/workflows` view 按 `x` 停舊 run。Agent 工具沒 API 可停 workflow。

---

## 9. 設計模式 — 從四輪迭代萃取

### 9.1 HybridWorkflow(HR-04)

- Agent A 負責 author(寫 deliverable)
- Agent B 負責 review(評 A 的產出)
- **絕不自己 role-play A 或 B**(orchestrator 只看 result)

### 9.2 STATELESS B-review sandbox

- B 無 file access → 必須 embed 完整 doc 內容在 prompt
- 這是「force function」:迫使 B 不依賴外部狀態,review 可重現

### 9.3 A self-check pattern

- Round 1: A 先 `test -f <deliverable>`,如果存在就 Read + return JSON(快速通過)
- Round > 1: A 先 review previous B-2 review JSON,套用 HIGH severity 修正,surgical Edit
- 避免 A 每次都 overwrite 整個檔案(surgical 才不會破壞既有合約)

### 9.4 B-2 loop logic(HR-12 + phase1_plan.md B-2)

```
APPROVE + all gaps low          → break (continue to next sub-task)
APPROVE + any med/high gap      → A fixes → re-dispatch B (round 2)
REJECT                           → A fixes → re-dispatch B
MAX_B_ROUNDS (5) without resolve → ESCALATE (hard return error, not silent break)
```

### 9.10 Compact JSON + disk read pattern（Agent A 禁止嵌入檔案內容）

Agent A 在 JSON response 內嵌入完整檔案內容 → 輸出 token 超限 → JSON 截斷 → orchestrator 拿到 null → abLoop 誤認為失敗。

**規則**: Agent A 的 JSON response **禁止**包含 `files[].content`。orchestrator 自己從磁碟讀。

```javascript
// ✅ 正確：compact JSON + 分開讀磁碟
// Agent A prompt 結尾:
'Return ONLY this compact JSON — do NOT embed file content (content is read from disk separately):\n'
+ '{"status":"OK","confidence":"high|medium|low","citations":["..."],"summary":"<1-2 lines>"}\n'

// Orchestrator 拿到 aResult 後:
let a
try { a = parseAgentJson(aResult, 'A-sad-r' + round) }
catch (e) { log('A JSON parse fail (likely truncated): ' + e.message.slice(0, 80)); a = null }
// 不論 a 是否 null，都從磁碟讀內容:
content = await loadFileViaBash(cfg.diskPath, cfg.diskPrefix || '', cfg.phaseName)
if (content.startsWith('ERROR:') || content.length < 50) {
  return { error: cfg.deliverable + ' not found on disk after A' }
}
```

**附加防護**: `loadFileViaBash` 加 `expectPrefix` 驗證，防止 agent 讀到錯誤檔案：

```javascript
async function loadFileViaBash(relPath, expectPrefix, phaseName) {
  const res = await agent(`cat ${REPO}/${relPath}`, { model: 'haiku', ... })
  const content = (typeof res === 'string' ? res : String(res ?? '')).trim()
  if (expectPrefix && content.length > 50 && !content.startsWith(expectPrefix) && !content.startsWith('ERROR:')) {
    return 'ERROR: content-mismatch — expected prefix "' + expectPrefix + '", got: ' + content.slice(0, 120)
  }
  return content
}
// 呼叫時指定各 deliverable 的前綴:
// SAD.md → '# SAD'
// adr/ADR.md → '# Architecture Decision Records'
// TEST_SPEC.md → '#'
```

### 9.5 Bash cat > Read tool for content loading

```javascript
// ✅ Reliable: bash cat (stdout = exact bytes)
const content = await agent(`Use ONLY Bash. Run: cat ${PATH}. Return stdout verbatim.`, opts)

// ❌ Unreliable: Read tool (LLM may hallucinate)
const content = await agent(`Use Read tool on ${PATH}. Return content.`, opts)
```

### 9.6 模型選擇(cost optimization)

```javascript
{ agentType: 'general-purpose' }              // 預設,用 session model
{ model: 'haiku' }                            // 6x 便宜,給 B-review 用
{ model: 'haiku', effort: 'low' }             // 更省
```

### 9.7 Preflight agent 要 super narrow

> 不給 preflight agent 看完整 P1 plan;只給「跑 3 個 bash 命令並回報」。
> 這樣它不會自行決定「既然能跑完,就全做完吧」。

### 9.8 Push + Advance 拆兩階段(per phase1_plan.md)

```javascript
phase('Push');     // push-checkpoint --phase 1 (retry until success, no --no-verify)
phase('Advance');  // advance-phase --completed 1 + verify HANDOVER.md
```

Push 沒成功就不 advance;Advance 失敗就保留 P1 狀態由人工介入。

### 9.9 Subagent prompt 結構樣板

```
You are <ROLE>. Your task: <ONE-LINE TASK>.
You have NO access to any files — all context is provided below.

=== [DOC 1: <LABEL>] ===
<content>

=== [DOC 2: <LABEL>] ===
<content>

<CHECKLIST_OR_INSTRUCTIONS>

SCOPE RULES (you MUST obey):
- DO NOT <bad action 1>
- DO NOT <bad action 2>
- ONLY do <good action 1> through <good action N>.

Return JSON only:
{...schema...}
```

---

## 10. 監看與調試

### 10.1 找到 run 狀態

```
/workflows    # 列出所有 run,選一個鑽進去看
```

每個 run:
- Phase box(顯示 agent count + token total + elapsed time)
- Agent detail: prompt、recent tool calls、result

### 10.2 Transcript 路徑

每個 run 的 script + 每個 agent 的逐字 transcript 寫到:

```
~/.claude/projects/<session-hash>/subagents/workflows/<run-id>/
  journal.jsonl                           # script 的 started/result 事件
  agent-<uuid>.jsonl                      # 該 agent 的完整 message log
  agent-<uuid>.meta.json                  # {agentType: 'general-purpose'}
```

讀 journal 找失敗根因:

```bash
JOURNAL=~/.claude/projects/<session>/subagents/workflows/wf_xxx/journal.jsonl
grep '"type":"result"' $JOURNAL | python3 -c "
import json, sys
for line in sys.stdin:
    d = json.loads(line)
    r = d.get('result', {})
    print(f\"{d['agentId'][:12]}: status={r.get('review_status') or r.get('status')} gaps={len(r.get('gaps') or [])}\")
"
```

### 10.3 用 validator 預檢 script

```bash
# /tmp/validate-workflow.mjs 是 ray-amjad/claude-code-workflow-creator 官方 validator 副本
node /tmp/validate-workflow.mjs /abs/path/workflow.js

# 輸出:
# ok — phase1-requirements.js passes (36539 bytes)
# 3 error(s) in xxx.js — fix before running.
```

harness 端等效檢查(不需要外部 validator 副本,見 §13.1):

```bash
node --check .claude/workflows/phase1-requirements.js   # 語法門
pytest tests/test_workflow_js_conventions.py -q          # §4 禁令 + 512KB + meta-first
```

### 10.4 Token 估算(節錄自 v4 經驗)

| Phase | Agents | Tokens |
|-------|--------|--------|
| Preflight | 1 | 15-25k |
| Brief loader | 1 | 5k |
| 4 × (A-read + B-review) | 8 | 40k |
| Constitution check | 1 | 5-10k |
| Peer review | 1 | 8-12k |
| Push + Advance | 2 | 30-50k |
| **小計(順利 1 輪)** | **14** | **~100-150k** |

若 B 反覆 REJECT,每多一輪 +30k。設 budget cap 避免失控。

---

## 11. Turn off / Disable

若不想用 workflow:

| 方式 | 範圍 | 持續 |
|------|------|------|
| `/config` → 關閉 "Dynamic workflows" | 個人 | 永久 |
| `~/.claude/settings.json` 加 `"disableWorkflows": true` | 個人 | 永久 |
| 環境變數 `CLAUDE_CODE_DISABLE_WORKFLOWS=1` | 該啟動 | session |

關閉後 `/deep-research` 不可用、`ultracode` 關鍵字無效。

---

## 12. 速查表

| 我想做... | 做法 |
|----------|------|
| 寫新 workflow | 1. 寫 `.claude/workflows/<name>.js` (export const meta 第一行)<br>2. `node /tmp/validate-workflow.mjs <path>`<br>3. `Workflow({ scriptPath: '/abs/path' })` |
| 改 workflow | `Workflow({ scriptPath, resumeFromRunId: 'wf_old' })` cache 命中已完成部分 |
| 停 workflow | 請 user 在 `/workflows` view 按 `x` |
| 監看 | `/workflows` |
| B-review | stateless prompt,完整 embed 文件 |
| 讀檔 | Bash cat (不用 Read tool) |
| 解析 agent JSON | balanced-brace parser(若用 schema: 風險見 §5.2/§8.4) |
| 省 token | B-review 用 `model: 'haiku'` |
| 防 over-reach | 加 SCOPE RULES (DO NOT) |
| Parse failure | 立即 hard error,不要 retry |
| **改 8 個 phase workflow 中的任何一個** | **不要手改 `.claude/workflows/*.js`——改 `scripts/workflowgen/`,見 §13** |

---

## 13. 維護：harness 端生成 → integration-test 消費鏈

> 本節是 harness-methodology 專屬維護規範,不是 Claude Code Workflow 官方
> 行為——記錄 Round 11(2026-07)workflowgen 遷移之後,這 8 個 phase
> workflow 檔案的正確變更與消費流程。上游消費專案（如
> integration-test）透過 git submodule 引用本文件與本 repo 產出的 JS,
> 不應該、也不需要反向修改它們。

### 13.1 SSOT 反轉：`.claude/workflows/*.js` 現為生成產物

Round 11 前：8 個 phase workflow JS 手寫維護,`resolveRepo()`/A-B review
機器/verdict schema 等在 8 檔之間逐字複製——修一個 bug 要改 8 處
(`58f8b2f` 一例)。

Round 11 後：SSOT 移至 `scripts/workflowgen/`(鏡射 `scripts/plangen/` 架構):

| 檔案 | 角色 |
|------|------|
| `scripts/workflowgen/js_blocks.py` | 共用 JS 區塊生成器(resolveRepo/budget-guard/verdict schema/A-B review 機器…) |
| `scripts/workflowgen/phase_specs.py` | 8 個 phase 的宣告式 spec(步驟序列、milestone type、artifacts allowlist…) |
| `scripts/workflowgen/js_src/*.mjs` | 純函式(`balancedJsonAt` 等),`node:test` 可直測,生成時 inline 剝除 `export` |
| `scripts/workflowgen/generate_workflows.py` | Facade:`--write` 落地、`--check` 比對 |

**`.claude/workflows/*.js` 現在是生成產物,禁止手改**——改動流程:

```bash
# 1. 改 scripts/workflowgen/{js_blocks,phase_specs}.py
# 2. 重新生成
python3 scripts/workflowgen/generate_workflows.py --write

# 3. 跑 golden + 對齊 + 慣例測試
REGEN_WORKFLOWS=1 pytest tests/test_workflowgen_golden.py -q   # 若故意改動,先 regen golden
pytest tests/test_workflowgen_golden.py tests/test_workflow_js_conventions.py tests/test_workflow_plan_alignment.py -q
```

手改 `.claude/workflows/*.js` 會被 `tests/test_workflowgen_golden.py` 的
byte-equal 檢查抓到;下一次 `--write` 也會無聲蓋掉手改內容,不會有警告。

### 13.2 submodule bump 消費(integration-test 端)

integration-test 透過 `.gitmodules` 將 harness-methodology 掛在 `harness/`
底下,實際執行的 workflow 檔案路徑是
`integration-test/harness/.claude/workflows/phaseN-*.js`——也就是這個
repo(harness-methodology)產出的檔案本身,不是另一份拷貝。

消費新版的正確流程(在 integration-test 端執行):

```bash
cd harness              # submodule 目錄
git fetch origin
git checkout <harness-methodology 新 commit/tag>
cd ..
git add harness
git commit -m "chore: bump harness submodule to <sha>"
```

### 13.2b `run-all.js` — 第 9 個生成檔(Round 23)

`.claude/workflows/run-all.js` 涵蓋 Phase 1–8,**一次啟動跑完整條方法論**。它不是第 9 份手寫 spec,而是 `scripts/workflowgen/spec_runall.py` 呼叫**同一組** `generate_phaseN()` 把 8 份 body 內聯進 `async function runPhaseN()`:

```bash
python3 scripts/workflowgen/generate_workflows.py --write   # 9 個檔一起(8 phase + run-all)
python3 scripts/workflowgen/generate_workflows.py --check   # 9/9
```

**改任何一支 phase 生成器都會同時改動 run-all** —— golden(`tests/golden/workflowgen/run-all.js`)會在同一個 commit 的 diff 裡把這個扇出顯示出來,不會延後變成來路不明的第三方重生。

維護時必須知道的四件事:

| 事項 | 說明 |
|---|---|
| **只有兩類東西提到頂層** | `resolveRepo`/`REPO`/`PY`(提出去才有「解析一次而非八次」),以及 verdict schema(§5.3 硬性要求 `schema:` 必須是 top-level const,巢狀會壞 parser)。A/B 機器、JSON helper、`checkManifestIntegrity`、phase 專屬常數一律留在 runner 內,靠函式作用域隔離 —— P1 與 P2 各自不同的 `buildBPrompt` 因此不必調和 |
| **標題一律 `P<N> · ` 前綴** | 8 支重名的 `Entry & Preflight`/`Advance`/`Sync` 在同一個 progress view 裡無法閱讀。前綴同時套用在 `phase()`、`phase: '…'` 與 `phase: <變數>`(`loadFileViaPython` 等 4 處由呼叫端傳入 box 名) |
| **512 KB 是硬牆** | §4 的上限對 run-all 只有一份餘裕,因此生成時剝除內聯 body 的純註解行(WHY 完整留在 8 支同源檔)。`RUNALL_MAX_BYTES` 是餘裕 ratchet:**撞到時先縮 prompt,不要調高數字** |
| **起跑點讀 state.json** | `current_phase` 決定從哪個 phase 開始跑到 8;讀不到就**中止**,不猜 —— 猜 Phase 1 會在成熟專案上重跑整個需求階段。中途死掉就重新啟動 run-all,各 phase 自己的 GUARD 會短路已完成的工作 |

**等價性怎麼被鎖住**:`scripts/workflowgen/js_src/sim_runner.test.mjs` §11 對每個 N 斷言「run-all 的 `P<N> ·` dispatch 序列 == 單獨跑 `phaseN-*.js` 的序列」,§12 再用兩向精確差集鎖住唯一允許的差異(6 個 Sync 折進 `advance-phase --push`、多一次 cursor 讀)。**這證明的是 dispatch 序列,不是最終產出物位元組相等** —— 後者只有 live E2E 能證。

### 13.3 禁止在 submodule 內修補(HR-17)

> **HR-17**(`CONSTITUTION.md` / `SKILL.md`)：**嚴禁從專案端修改
> `harness/`(methodology submodule)內的任何檔案**。發現 bug 必須回報
> 上游;submodule 內的 hotfix 會造成 diverged fork 且上游不可見。唯一
> 允許的 submodule 操作為 `git submodule update --remote`。

**歷史違規模式**(Round 11 站0 審計發現,已終結)：早期修補流程是在
integration-test 的 submodule 工作區(`integration-test/harness/`)內
直接改動 workflow JS 原始檔,再用類似
`sync(workflows): sync workflow JS files from integration-test`
(`8a2cf00`)、`sync(workflows): update JS workflows from integration-test
for T1-A/T1-B architecture alignment`(`459caa7`)這樣的 commit,把改動
「回灌」進 harness-methodology 本體。這條路徑本身就是 HR-17 違規——bug
在 submodule 工作區被修好之前,harness 本體一直是壞的;而如果那次
「回灌」被忘記,兩邊會永久 diverge 且無人察覺。

**Round 11 之後的正確流程**：

1. 在 integration-test 端發現 workflow JS 的 bug/gap → **不要**在
   `integration-test/harness/.claude/workflows/` 下修改任何檔案。
2. 回到 harness-methodology 本體,改 `scripts/workflowgen/` 生成器
   (§13.1 流程),跑全套 gate,commit。
3. integration-test 執行 `git submodule update --remote` 消費新版
   (§13.2)。
4. 需要驗證行為變更時,在 integration-test 端跑一次真實 E2E——harness
   本體無法直接跑 workflow runtime(見「風險與限制」：行為等值無法在
   harness 側全驗,結構三斷言 + lint + 人審 diff 是本 repo 這一側的上限)。

這個順序保證 harness-methodology 永遠是唯一真相來源,integration-test
永遠是純消費端,不會再出現「submodule 內先修、之後才想起要回灌」的
diverged-fork 風險。

---

## 附錄 A: integration-test 真實案例時序

| 版本 | 症狀 | 根因 | 修法 |
|------|------|------|------|
| **v1** shipped | `import() is not available in workflow scripts` | `const fs = await import('node:fs')` 想要 fs I/O | 全移除,改叫 agent() |
| **v1** shipped | `Script parse error: Unexpected token (330:62)` | inline schema 太複雜 | schema 提到 top-level const |
| **v2** | general-purpose preflight agent 3 分鐘做完 P1 | prompt 包含完整 P1 plan | 加 SCOPE RULES DO-NOT |
| **v2** run | name resolver cache 給舊版 | shipped file 已 cp 但 runtime persisted 預先 snapshot | `Workflow({ scriptPath })` 取代 `name` |
| **v2** run | `subagent completed without calling StructuredOutput` | `schema:` 強制 tool call,某 agent 回 text | 移除 `schema:`,改 balanced-brace parser |
| **v3** run | B-review 無限 REJECT(>3 輪) | Brief loader agent 用 Read tool 讀 PROJECT_BRIEF.md,回傳 hallucinated 內容(來自 CLAUDE.md / memory) | 改 Bash `cat` + 加 defensive validation |
| **v4** run | (進行中) | — | — |
| **Round 11**(harness 端) | 8 檔手寫維護,共用邏輯逐字複製 8 份,一個 bug 修 8 處(`58f8b2f`) | 無 SSOT,workflow runtime 又禁止 import 使跨檔共用只能複製貼上 | `scripts/workflowgen/` render-from-SSOT(§13.1) |

## 附錄 B: 官方/社群資源

- 官方手冊: https://code.claude.com/docs/en/workflows
- 文件索引: https://code.claude.com/docs/llms.txt
- Validator 來源: https://github.com/ray-amjad/claude-code-workflow-creator (main/scripts/validate-workflow.mjs)
- Subagent 文件: https://code.claude.com/docs/en/sub-agents
- 設定文件: https://code.claude.com/docs/en/settings
- 權限模式: https://code.claude.com/docs/en/permission-modes
