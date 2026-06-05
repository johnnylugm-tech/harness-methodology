# Proposal: RED Test Quality Gate

> 提案狀態：Draft
> 提案日期：2026-06-04
> 觸發事件：tts-new FR-01 RED test sub-assertion 寫錯（`if source in ("垃圾", "和")` 對 1-音節 Bopomofo「和」誤套 2-音節空格規則），繞過 5 個已 lock 的 spec 文檔，進入 GREEN 階段才被主代理觸發 Trigger A2，事後選 B（修改 test）收尾。

## 一、問題重述

### 1.1 事件真相（已實證）

tts-new FR-01 RED test（commit `3ed9d80`）對 Bopomofo 規則的 sub-assertion：

```python
# --- AC5 (sub-assertion, cases 3 and 8): Bopomofo entries must be
# emitted as space-separated syllables (SPEC.md L41, L47).
if source in ("垃圾", "和"):
    assert " " in expected, (...)
```

**事實鏈**（5 個 spec 文檔全部 verbatim 一致）：

| 文檔 | 對「和」說什麼 | 行號 |
|---|---|---|
| SPEC.md | `和（連接詞）｜ㄏㄢˋ` | L46 |
| SRS.md | `和→ㄏㄢˋ` | L162 |
| TEST_INVENTORY.yaml | `和→ㄏㄢˋ` | L25 |
| TEST_SPEC.md | `和→ㄏㄢˋ` | L98 |
| test parametrize | `和→ㄏㄢˋ` | (parametrize id) |

**真正的矛盾點**：RED test 的 sub-assertion 內部把「垃圾」的多音節規則（`ㄌㄜˋ ㄙㄜˋ` 兩音節需空格）誤套到「和」的 1-音節 Bopomofo（`ㄏㄢˋ` 無音節可分隔）。**矛盾只在 test 內部**，不在 test ↔ 5 文檔之間。

### 1.2 根本缺陷：TDD-RED 流程不驗「test 是否合理」

TDD 教科書設計的 RED 階段只驗「test 會 fail」：

- ✓ 12 個 parametrize case 在缺實作時 fail（這次是過的）
- ✗ 沒驗 sub-assertion 條件是否對應 spec 規則（這次錯在這）
- ✗ 沒驗 sub-assertion 觸發集合是否結構上適用該規則（這次錯在這）

RED 階段「寫對」完全依賴 agent 自身能力，**沒有 process gate**。這是 TDD 流程的已知限制——**不能用工具解決，因為這是流程紀律問題**。

### 1.3 為何不在 harness 工具層修

我前幾輪曾提議：
- 在 harness 寫「mirror chain verifier」
- 寫 SPEC.md parser 自動生成 test parametrize
- 寫「sub-assertion 音節數 vs 觸發集合」linter

**這些都是試圖用工具解紀律問題——錯的方向**：

| 工具方案 | 為何不治本 |
|---|---|
| 寫 SPEC.md parser | harness 不該耦合用戶檔案格式；每個專案 SPEC.md 長不一樣 |
| mirror chain verifier | 假設「鏡像層一致 = test 對」——但 spec 鏡像可能本身就有結構 bug（這次就是） |
| sub-assertion 音節數 linter | 把 Bopomofo 規則硬編碼到 harness——harness 不該懂應用層規則 |

**正解**：流程紀律問題用流程紀律手段解——commit template、人工 review gate、自我審查 trigger。**不需改 harness core 工具**。

## 二、5 條把關手段

依「把 RED 階段卡住、不讓錯的 test 進入 GREEN」目標，列 5 條具體做法，**全都不需要改 harness 工具**：

### 做法 1：RED commit message 必填「spec alignment check」段

**機制**：RED agent 寫完 test，commit message 必填一段 spec alignment self-check。

**模板**：

```
test(RED): <FR-id> failing test

Spec alignment check:
- <N> parametrize cases verbatim from <TEST_INVENTORY.yaml 路徑> <行號>
- AC<sub-id> sub-assertion: <觸發集合> = <{case1, case2, ...}>
  - spec rule source: <SPEC.md L## / TEST_INVENTORY L##>
  - why each case in <觸發集合> satisfies this rule: <逐個說明>
- All expected literals byte-identical to <鏡像源>
```

**治這次 bug 的機制**：RED agent 寫到「和」時必須填「why each case satisfies this rule」——強迫他想「為什麼和也要套空格規則？」→ 自我審查 trigger。

**成本**：commit template 加一段；agent 多寫 3-5 行 message。

**效益**：捕獲「sub-assertion 條件沒引 spec 來源」的明顯錯誤。

### 做法 2：sub-assertion 條件禁用 `in` 列表、只准用 `==` 對單一 case

**機制**：明確文字紀律——sub-assertion block 內的 `if` 條件只能 `==` 一個字面量，不能 `in` 一個 tuple。

**理由**：
- `source == "垃圾"`：明確指定「這個規則只給垃圾」
- `source in ("垃圾", "和")`：暗示「這幾個都需要這個規則」——**結構上容易把不相干的 case 拉進來**

**例外**：若 sub-assertion 真的需要對多 case 套同一規則，必須在 commit message 引用 spec 條目說明「為什麼這些 case 在一起」。

**治這次 bug**：RED 寫成 `source in ("垃圾", "和")` 違反規則 → reject。

**成本**：純文字紀律，0 工具成本。

**效益**：結構性杜絕一整類錯誤（sub-assertion 條件含無關 case）。

### 做法 3：RED 階段必跑 mirror consistency self-check 命令

**機制**：每個專案根目錄放 `scripts/check_red_consistency.sh`（或 `Makefile` target），RED agent 必跑。

**檢查內容**：
1. 從 test 檔抓所有 parametrize expected literal
2. 從 test 檔抓所有 sub-assertion 內的 string literal
3. 比對 `TEST_INVENTORY.yaml` 對應行的 expected
4. **任何 test literal 沒出現在 TEST_INVENTORY 對應行 = FAIL**

**實作**：harness 內建 `scripts/check_red_consistency.py` 通用工具，接受：
- `--test-glob`：test 檔 pattern（如 `tests/test_fr*.py`）
- `--spec-yaml`：鏡像源 YAML（如 `TEST_INVENTORY.yaml`）
- 不耦合任何應用層規則（不需懂 Bopomofo、不需懂台語、不需懂應用領域）

**治這次 bug**：
- parametrize `和→ㄏㄢˋ` 對 TEST_INVENTORY L25 ✓
- sub-assertion 內 `" "` （空格 literal）對 TEST_INVENTORY L25：TEST_INVENTORY 沒寫「和要含空格」→ **FAIL**
- RED agent 看到 FAIL → 知道 sub-assertion 寫錯 → 改

**成本**：寫一次 ~50 行 Python（harness 內建為通用 module）；各專案各加一個 shell wrapper 跑它。

**效益**：自動驗「test literal 對最近鏡像層 verbatim 一致」。**短鏡像鏈，harness 職責最小**。

### 做法 4：RED → GREEN 之間加 Agent B review gate

**機制**：RED commit 後，**Agent B（reviewer）必須 sign-off 才進 GREEN**。

**Sign-off 條件**：
1. 讀 RED test，列出「對每個 parametrize case 的 expected 值，spec 在哪」
2. 列出「每個 sub-assertion 的觸發條件與 spec 哪條對應」
3. 任何 sub-assertion 條件含 case X 但 case X 在 spec 對應條目**結構上不適用**該規則 → REJECT

**治這次 bug**：Agent B 看到 `if source in ("垃圾", "和")` 會問「為什麼和也要含空格？spec 對和的規則是什麼？」→ agent A 解釋不了 → reject。

**成本**：每次 RED 加一次 review。**這是流程而非工具的成本**。

**效益**：peer review，捕獲率最高；但**流程成本**也最高（每次 RED 多一道 review）。

### 做法 5：RED 階段必列「parametrize id ↔ spec source ↔ rule」對照表

**機制**：RED commit 必含一張表：

```
| parametrize id        | spec source                         | rule applied                                      |
| 視頻→影片            | TEST_INVENTORY L11                  | verbatim substitution                             |
| 垃圾→ㄌㄜˋ ㄙㄜˋ     | TEST_INVENTORY L13 + SPEC L41       | space-separated (2-syllable)                       |
| 和→ㄏㄢˋ             | TEST_INVENTORY L19 + SPEC L46       | verbatim (1-syllable, no space rule applicable)   |
```

**治這次 bug**：RED agent 寫到「和」時必須填「rule applied」——強迫他想「和的 rule 是什麼？」，會意識到「和 1 音節、不適用空格規則」。

**成本**：commit message 模板改；agent 寫表。

**效益**：強迫對 spec 來源自覺，留 audit trail。

## 三、5 條對比

| # | 做法 | 改動位置 | 成本 | 治這次 bug？ | 通用性 |
|---|---|---|---|---|---|
| 1 | RED commit message spec alignment 段 | commit template | 極低 | ✓（自我審查 trigger） | 高 |
| 2 | sub-assertion 禁用 `in` 列表，只准 `==` 單值 | 流程紀律 | 極低 | ✓（結構性杜絕） | 中（只防此類） |
| 3 | RED 必跑 mirror consistency check | 各專案 script + harness 通用 tool | 低（~50 行 Python，harness 內建） | ✓（自動驗） | 高（通用工具） |
| 4 | RED → GREEN 加 Agent B review gate | 流程 | 中（每次 RED 加 review） | ✓（peer review） | 高 |
| 5 | RED 必列「id ↔ source ↔ rule」對照表 | commit template | 低 | ✓（強迫對 spec 來源自覺） | 高 |

## 四、建議組合

### Phase 1：立即上線（純流程/紀律，零工具改動）

**組合 1 + 2 + 5**：
- RED commit message 模板加 2 段（spec alignment 段 + 對照表）
- sub-assertion 禁用 `in` 列表規則
- 自我審查 + commit 留 audit trail

**預期效益**：捕獲「sub-assertion 條件含無關 case」「sub-assertion 沒引 spec 來源」「agent 沒意識到規則適用範圍」三類錯誤。

**總改動量**：commit template 改 + 一頁紀律規範文件。

### Phase 2：1-2 個 sprint 內（harness 內建工具）

**+ 3**：harness 內建 `scripts/check_red_consistency.py` 通用工具，各專案自配 config 跑。

**預期效益**：自動驗「test literal 對最近鏡像層 verbatim 一致」。**harness 不耦合應用層規則**（不需懂 Bopomofo、不需懂台語、不需懂任何應用領域）。

**總改動量**：harness 加 ~50 行 Python + 每專案一個 shell wrapper。

### Phase 3：3 個月內考慮（組織級流程）

**+ 4**：RED → GREEN 加 Agent B review gate，給流程留 buffer。

**預期效益**：peer review，捕獲率最高；但**流程成本**也最高。

**前置條件**：團隊有 reviewer 池、review SLA 共識。

## 五、不在範圍（明確說不做的）

| 不做 | 為何 |
|---|---|
| harness 寫 SPEC.md parser 自動生成 test | harness 不該耦合用戶檔案格式 |
| harness 寫「mirror chain verifier」（跨 P1-P3 全鏈驗） | 通用解但工程量大、不是這次 bug 的特定解 |
| harness 寫「sub-assertion 音節數 vs 觸發集合」linter | 把應用層規則硬編碼到 harness——harness 不該懂應用層 |
| 改 TDD 教科書式 RED 標準 | 教科書定義「RED = fail」是共識，擴張 RED 職責會引發社群反彈 |

## 六、給 tts-new 立即可做的 follow-up

不需等 harness 改，tts-new 端立即可做：

1. **amend `6c50246` commit message** 加一句：
   ```
   sub-assertion 收窄到只對「垃圾」觸發，理由：5 文檔（SPEC L46 / SRS L162 / TEST_INVENTORY L25 / TEST_SPEC L98 / test parametrize）lock 住 和→ㄏㄢˋ 無空格；SRS L165 明說「多音節才需空格」。
   ```
2. 在 [tests/test_fr01.py L108-118](file:///Users/johnny/projects/tts-new/03-development/tests/test_fr01.py) 補 spec citation 註解（做法 1+5 雛形）。
3. 起 `scripts/check_red_consistency.sh` prototype，套用做法 3。

## 七、決策點

請拍板：

1. **Phase 1（1+2+5）是否立即上線**？commit template 改 + 一頁紀律規範。
2. **Phase 2（+3）何時開工**？`scripts/check_red_consistency.py` 我可以直接寫。
3. **Phase 3（+4）是否要**？需先確認團隊 reviewer 池。
4. **tts-new 端 follow-up** 是否要做？（amend commit + 補註解 + prototype script）

---

## 附錄：本提案的自我審查

### 假設驗證

| 假設 | 驗證狀態 |
|---|---|
| tts-new FR-01 RED test 在 commit `3ed9d80` 確實寫成 `if source in ("垃圾", "和")` | ✓ `git show 3ed9d80:03-development/tests/test_fr01.py` 實證 |
| 5 個 spec 文檔對「和」全部一致寫 `ㄏㄢˋ` 無空格 | ✓ 5 個檔案逐行 Read 實證 |
| SPEC.md 是用戶給的 docx 原文（不是 SRS/TEST_INVENTORY 衍生） | ✓ SPEC.md 自述「合併原始 docx 規格與 SRS.md 優化版。**所有實作以此文件為準**」+ SRS L3「Authoritative source: SPEC.md」 |
| 工具方案（mirror chain verifier、SPEC.md parser）會錯 | ✓ 已論證：耦合用戶檔案格式、超越 harness 職責 |
| 流程紀律方案（commit template、review gate、self-check script）能治這次 bug | ✓ 逐條對應錯誤模式 |

### 最可能錯的地方

- 做法 1（commit message 強迫對齊）對 LLM 自我合理化無效——LLM 可能會找理由 support 已寫的 code。**信心 Low**。
- 做法 2（禁用 `in` 列表）可能誤禁正當使用案例（如 5 個 case 共用同一 regex 檢查）。需配例外條款。**信心 Medium**。
- 做法 3（mirror consistency check）若 TEST_INVENTORY 本身就有 bug，harness 會誤放行。**鏡像層的正確性依賴 P1/P2 階段**，不是 P3 RED 階段的責任。**信心 Medium**。
- 做法 4（review gate）增加流程成本，小團隊/單人開發可能不適用。**信心 High**（只是不適用，不會誤傷）。

### 未驗證的關鍵問題

- tts-new 實際 RED agent 是 LLM 還是人？
- 做法 1 對 LLM 自我審查的真實捕獲率（無實證數據）
- 流程紀律方案在「RED agent 是 LLM、無人工 review」環境下的有效性

### 信心等級

- 5 條手段能治這次 bug：High（直接對應錯誤模式）
- 5 條手段在 LLM-only 環境下也有效：Medium（無實證）
- 不在 harness 加工具的決定：High（明確論證 harness 不該耦合應用層）
