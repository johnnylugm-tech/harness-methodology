# Plan: 清除 HERMES_REVIEWER_TARGET 與 P6 Gate 4 的殘留連結

**日期**: 2026-05-23
**狀態**: Done
**Slug**: remove-hermes-gate4-residue

---

## 目標

移除所有暗示 `HERMES_REVIEWER_TARGET` 為 P6 Gate 4 必要條件的文件和文字，同時保留 `reviewer_router.py` 中 Agent B A/B 協作的實際用途。

---

## 現況假設

- `reviewer_router.py` 的 priority chain（Hermes → Gemini → sub-agent）仍在 P1–P6 的 A/B 審查中使用，**沒有要被移除**
- `HERMES_REVIEWER_TARGET` 環境變數本身仍被 `reviewer_router.py` 使用，只是初始化引導和 SAD.md 文件將其**錯誤地與 P6 Gate 4 緊密綁定**
- SAD.md 已記錄「`_require_hermes_approve` REMOVED in v2.4」，但有四個地方還沒同步更新

---

## 需要修改的檔案

### 1. `SAD.md`

#### 1a. 刪除第 2792–2796 行（整段 Hermes APPROVE 章節）

```
**Hermes APPROVE (P6 Gate 4)**
- Trigger: `messages_send` to `HERMES_REVIEWER_TARGET` env var (e.g. `telegram:USER_ID`)
- Timeout: 120 s (`GATE4_HERMES_TIMEOUT_MS=120000`; env-overridable via `HERMES_TIMEOUT_MS`)
- Fallback: cold-read (`messages_read`) if no reply within timeout
- Failure: Hermes unavailable or reviewer rejects → escalate to human
```

**替代文字**: 移除該段落，不做替代。Gate 4 不再需要 Hermes。
如需保留 Hermes 在 A/B 協作中的角色，移到架構文件的其他合適章節。

#### 1b. 移除第 1723 行（YAML 區塊說明）

```
> Gate 4 additionally requires Hermes reviewer APPROVE (enforced at a higher
> orchestration level, not in the YAML itself).
```

**替代文字**: 移除該行（YAML 本身已是完整定義）。

#### 1c. 更新第 2298–2300 行（三步快速開始）

目前：
```
2. Set env vars (HERMES_REVIEWER_TARGET, etc.)
```

修改為：
```
2. Set required env vars:
   export ANTHROPIC_API_KEY=sk-...       # Required for all LLM evaluation
   export HERMES_REVIEWER_TARGET=telegram:YOUR_CHAT_ID   # Optional — enables Hermes A/B reviewer (P1–P6)
```

（加入 `ANTHROPIC_API_KEY` 是因為它是真正 Required 的，並將 Hermes 降為 Optional）

### 2. `SKILL.md`

#### 2a. 修改第 32–33 行

目前：
```
a. [required] export HERMES_REVIEWER_TARGET=telegram:YOUR_CHAT_ID
   (A/B Agent B uses this from P1; strictly required at P6. Set now for full quality.)
```

修改為：
```
a. [optional] export HERMES_REVIEWER_TARGET=telegram:YOUR_CHAT_ID
   (Enables Hermes A/B reviewer in priority chain — falls back to Gemini→Claude if unset.
    Optional from P1; not required at P6 Gate 4 since Hermes APPROVE was removed in v2.4.
    Set for higher review quality.)
```

#### 2b. 更新第 33 行的 "(strictly required at P6)" 錯誤

### 3. `INTEGRATION.md`

#### 3a. 修改第 295 行環境變數表格

目前描述側重「P6 嚴格需要」。

修改為說明其真實用途：
```
| `HERMES_REVIEWER_TARGET` | `reviewer_router.py` | — | Optional. Enables Hermes as primary reviewer in the A/B
| | | | priority chain (Hermes→Gemini→Claude). If unset, chain degrades gracefully.
| | | | Used by Agent B A/B collaboration from P1; NOT required for P6 Gate 4. |
```

### 4. `CLAUDE.md.template`（可選）

第 10 行：
```
- Hermes Reviewer Target: ${HERMES_REVIEWER_TARGET}
```

**選項 A（保守）**: 保留不變 — `.claude/CLAUDE.md` 仍是事實，只是敘述需要對齊。
**選項 B（積極）**: 改為：
```
- Reviewer Chain: ${REVIEWER_CHAIN:-hermes,gemini}  (Hermes optional; configure via REVIEWER_CHAIN env var)
```

**建議採用選項 B**，這樣可以清楚表達 chain 是可配置的，不只是 Hermes 一個選項。

---

## 步驟清單

1. [ ] 備份即將修改的四個檔案（SAD.md, SKILL.md, INTEGRATION.md, CLAUDE.md.template）
2. [ ] 修改 `SAD.md`:
   - [x] 刪除第 2792–2796 行的 Hermes APPROVE 段落
   - [x] 刪除第 1723 行的 Gate 4 Hermes APPROVE 說明
   - [x] 更新第 2298–2300 行的三步引導
3. [ ] 修改 `SKILL.md`:
   - [x] 將 HERMES_REVIEWER_TARGET 從 [required] 改為 [optional]
   - [x] 更新說明文字，移除「strictly required at P6」
4. [ ] 修改 `INTEGRATION.md`:
   - [x] 更新表格敘述，說明這是 Optional 且非 P6 Gate 4 必要
5. [ ] 修改 `CLAUDE.md.template`:
   - [x] 將 `Hermes Reviewer Target` 改為 `Reviewer Chain`
   - [x] 加入 `REVIEWER_CHAIN` 環境變數說明
6. [ ] 搜尋確認沒有其他地方殘留「Gate 4 Hermes」「P6 Hermes APPROVE」等關鍵字
7. [ ] 執行 `git diff` 確認變更範圍
8. [ ] 提交並 push

---

## 驗證方式

修改完成後，執行以下搜尋應無結果：
```bash
# 確認沒有「Gate 4 + Hermes APPROVE」的殘留描述
grep -rn "Gate 4.*Hermes\|Hermes.*Gate 4\|Gate.*4.*Hermes APPROVE\|Hermes APPROVE.*Gate" SAD.md SKILL.md INTEGRATION.md

# 確認沒有「strictly required at P6」等誤導性描述
grep -rn "required at P6\|required.*P6\|P6.*required" SAD.md SKILL.md

# 確認 reviewer_router.py 本身的 Hermes 用途仍在
grep -n "HERMES_REVIEWER_TARGET" harness/reviewer_router.py
```

預期結果：
- 第一條搜尋：無結果
- 第二條搜尋：無結果
- 第三條搜尋：仍能找到 `_parse_chain` 和 `__init__` 中的 `HERMES_TARGET` 讀取

---

## 風險與取捨

| 風險 | 說明 | 緩解 |
|---|---|---|
| SAD.md 架構章節刪除後，讀者不知道 Hermes 的實際用途 | Hermes 在 A/B 協作中的角色文件現在散在各處 | 考慮在 SAD.md 的 reviewer_router 章節（§3.2 或 §4.2）保留一份簡短說明 |
| `reviewer_router.py` 若未來也被移除，這次修改就不夠徹底 | 只是文件清理，不影響 code | 這是假設性風險，目前 code 仍在使用 |
| `CLAUDE.md.template` 改動會導致已生成的 `.claude/CLAUDE.md` 與模板不一致 | 舊專案不受影響；新專案使用新模板 | 這是預期行為 |

---

## 開放問題（已確認）

1. **`reviewer_router.py` 的 A/B 協作覆蓋哪些 Phase？**
   → **Phase 1 和 Phase 2**。P1–P2 的 A/B 審查使用 Hermes 優先 chain；P3 以後流程不同。

2. **SAD.md 是否需要在 reviewer_router 專屬章節補充 Hermes 用途簡要說明？**
   → **不需要，刪除所有殘留描述即可**。A/B 協作範圍已確認是 P1–P2，文件不需要補充。

3. **`CLAUDE.md.template` 是否改為 `Reviewer Chain`？**
   → **確認可行**。

---

## 預計變更行數

| 檔案 | 刪除行數 | 新增行數 | 淨變化 |
|---|---|---|---|
| SAD.md | ~12 | ~2 | -10 |
| SKILL.md | 2 | 4 | +2 |
| INTEGRATION.md | 1 | 3 | +2 |
| CLAUDE.md.template | 1 | 2 | +1 |
| **總計** | **~16** | **~11** | **-5** |