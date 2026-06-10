# Adding Language Support — SOP / Checklist

> 為 harness-methodology 新增一個目標語言(例如 Go、Rust、Java)的標準作業程序。
> v2.8.0 的 JavaScript/TypeScript 支援即依本清單落地;每個步驟都標注對應的
> 實作位置與驗收方式。**順序即依賴順序,不可跳步(Anti-Shortcut)。**

---

## 0. 前置閘(動工前必讀)

**R8 鐵律**:`harness/ssi/scripts/score.py` R8 規定任何 `requires_tool_execution`
維度的 `tool_score` 不得為 null。這代表:

- **14 個維度必須全部有工具**才能註冊語言 — 「先接 12 個、剩 2 個之後補」不存在。
  `tests/test_toolchain_registry.py::test_language_covers_every_gate_dimension`
  會在 registry 註冊不完整時直接紅燈(R8 提前到 registry 層)。
- 某維度在該語言生態沒有現成工具時,選項只有兩個:
  1. **框架自建 in-process 掃描器**(先例:`js-mi` 用 tree-sitter 自算 radon 同款
     MI 公式;`js-doc-coverage` 取代不存在的 docstring 工具);
  2. **語意等價的政策替代**(先例:純 JS 的 type_safety 用 JSDoc + `tsc --checkJs`,
     而不是放棄該維度)。

**分數可重現性鐵律**:同一份程式碼在任何機器、任何時間必須得到同分。
所以:工具版本一律 `==` 釘死、規則集 vendored(不用遠端 pack)、
machine-readable 報告(JSON reporter / artifact)優先於刮 stdout 文字。

---

## 1. Checklist(按層次逐項,每項含驗收)

### 1.1 Toolchain Registry(`harness/toolchains/registry.py`)

- [ ] `DIMENSION_TOOLS["<lang>"]` 補滿 **全部 14 維**(dimension → tool_id;
      runner 相依的維度用 `{"<runner>": id, "default": id}` dict 形式)
- [ ] 每個新 tool_id 一筆 `ToolSpec`:
  - `cmd`:argv tuple,佔位符只有 `{root}`/`{test_target}`;
    套件管理執行器要用防網路旗標(npm 是 `npx --no-install`,其他生態找等價物)
  - `check_cmd`:可用性探測(exit 0 = 已安裝)
  - `scorer`:能共用就共用(JS 的 `js-mi` 重用 `radon-mi` scorer,因為輸出
    schema 相容 — 優先這樣設計)
  - 跑不完/不該 inline 跑的工具標 `skip_inline=True`(mutation/license 類)
  - in-process 掃描器標 `in_process=True`
  - 工具把報告寫檔的,設 `output_artifact`(run_tool 會把檔案內容接在
    `_ARTIFACT_MARKER` 後;scorer 解析 artifact 而非 stdout)
- [ ] **驗收**:`pytest tests/test_toolchain_registry.py` 綠(完整性不變量
      自動 parametrize 到新語言)

### 1.2 偵測(`harness/toolchains/detect.py` + `core/utils/lang_patterns.py`)

- [ ] `detect_language`:manifest 檔規則(如 `go.mod` → go);
      與既有語言同時存在 → 回 None(歧義必須 `--language` 明示,禁止猜)
- [ ] `core/utils/lang_patterns.py`:`SOURCE_EXTENSIONS["<lang>"]` +
      測試檔慣例(必要時擴充 `TEST_FILE_PATTERN` / `is_test_file`)
      — **單一來源在 core 側**,因為 core 不得 import harness
- [ ] 測試命名慣例決策:D4 spec-coverage 與 P1 Naming Authority 靠
      「TEST_SPEC.md `test_fn` 名 == 實作中的測試名」集合匹配。
      新語言要嘛沿用 `test_*` 風格識別名(JS 先例:`it('test_frNN_x')` 標題),
      要嘛擴充 `harness_cli._scan_test_functions` 的抽取邏輯
- [ ] **驗收**:`tests/test_toolchain_registry.py::TestLanguageDetection` 加新 case

### 1.3 Scorers(`harness/tool_runners.py`)

- [ ] 每個新工具一個 `_score_<tool>` + `_SCORERS` 註冊
- [ ] 懲罰曲線對齊既有同維度工具(lint=每違規 −2、type=每錯誤 −5、
      security=HIGH−10/MED−3/LOW−1、bench=>3000ms−50/>1000ms−25),
      讓門檻(90/85/80…)跨語言可比;偏差要在校準階段(§2)記錄理由
- [ ] 解析失敗回 **None**(工具壞掉絕不靜默給 100);
      「沒測試/沒可量測物」的語意對齊 Python 工具(exit-5 → None 等)
- [ ] **驗收**:captured-output fixture 單元測試(先例:`tests/test_tool_runners_js.py`)

### 1.4 In-process 掃描器(`harness/lang_scanners/`)

- [ ] 四個掃描器:assertions / error-handling / doc-coverage / MI,
      **輸出 JSON schema 與 Python 版完全一致**(scorer 才能共用):
      `{total, asserted, zero_assert}` / `{total, with_handler, no_handler,
      exempt_count, exempt_files}` / `{total, with_doc, missing}` 或 `{}` /
      `{file: {"mi": x, "rank": r}}`
- [ ] parser 依賴(tree-sitter grammar 等)在 `requirements.txt` **釘版**,
      並寫進 ToolSpec.check_cmd;import 一律 lazy(python-only 安裝不付成本)
- [ ] pragma 豁免沿用 comment-style 無關的字串:`pragma: no error-handling`
- [ ] `harness/lang_scanners/__init__.py::RUNNERS` 註冊四個 tool_id
- [ ] **驗收**:fixture+golden 測試(先例:`tests/test_lang_scanners_js.py`),
      含 zero-assert shell、pragma 豁免、MI 排序(複雜檔 < 簡單檔)

### 1.5 Traceability / Spec-coverage(core 側)

- [ ] `core/traceability/scanner.py::SAD_ROW_PATTERN` 副檔名 alternation 加新語言
- [ ] `harness_cli._scan_test_functions` 抽取新語言的測試識別名
- [ ] `harness_cli._trace_dirty_state` 已走 `iter_test_files` — 確認新語言
      測試檔被 mtime 探測涵蓋
- [ ] Gate-1 FR-scoped overrides:加 `_print_fr_scoped_overrides_<lang>`
      (per-FR 範圍化策略:JS 先例用測試標題過濾 `-t test_frNN`;
      若該語言 runner 不支援標題過濾,改用檔案過濾並記錄取捨)
- [ ] **驗收**:fixture 專案測試(先例:`tests/test_scanner_js_traceability.py`)

### 1.6 Mirror Gate / RED 自洽(`core/quality_gate/red_assertion_check.py`)

- [ ] 新增 `check_test_mirrors_spec_<lang>`;v1 接受 **structure-only** 範圍:
      parse 失敗 / 無測試案例 = error;spec 謂詞對齊 = needs_review INFO
      (引擎契約:不猜)。語意級謂詞對齊是 Python-only 能力 — 這是已知限制,
      不是 bug;要升級就要為該語言寫 predicate 正規化器
- [ ] `cmd_check_test_mirrors_spec` 依副檔名 dispatch
- [ ] **TEST_SPEC.md 謂詞語法不變**:spec 層謂詞一律 Python 表達式
      (`len(result) == 4`),與實作語言無關 — 在 TEST_SPEC 模板明示

### 1.7 Truth Verifier / Mutation / Auto-fix

- [ ] `phase_truth_verifier`:`check_pytest`/`check_coverage` 加語言分支
      (測試 runner argv + coverage artifact 讀取)
- [ ] `mutation_enforcer`:`run_<tool>_precheck` 同契約
      (`(True,"")` / `(False,reason)`),敗因含 file:line 清單;
      `run_mutation_precheck` 路由
- [ ] `core/traceability/auto_fix_propose.py`:stub 模板 + 註解風格 +
      `_closest_module` 副檔名
- [ ] **驗收**:monkeypatched-subprocess 測試
      (先例:`tests/test_js_truth_mutation_autofix.py`)

### 1.8 工具驗證與安裝面

- [ ] `harness/ssi/scripts/verify_tools.py::CORE_BY_LANG["<lang>"]`
- [ ] `templates/<lang>_toolchain/`:釘版相依 manifest + lint/type/test/bench
      設定模板 + bench 輸出契約(統一 JSON:`{"benchmarks":[{"name","mean_ms"}]}`)
- [ ] `harness_cli cmd_init_project`:語言專屬 toolchain 安裝步驟(6b 模式:
      merge 相依、複製設定、印安裝提醒)
- [ ] `requirements.txt`:harness 側新依賴釘版(掃描器 parser、SAST 工具)
- [ ] S3 evidence patterns:`harness/harness_bridge.py::_TOOL_CONTENT_PATTERNS`
      為每個新 tool_id 加真偽判別 pattern(注意「乾淨輸出為空」的工具要
      規定 agent 附 `echo "exit=$?"` 之類的 marker — tsc 先例)

### 1.9 文件

- [ ] `harness/ssi/prompts/evaluate_dimension.md`:每維度加該語言的命令小節
      (命令與公式必須和 registry/scorer 一字不差 — registry 是 single source)
- [ ] `SKILL.md` 語言支援節、`README.md` 語言矩陣、`INTEGRATION.md` 整合步驟、
      `CONTRIBUTING.md` registry 維護節、`templates/TEST_SPEC.md` 命名慣例、
      `templates/harness_quality_gate.yml` CI 安裝步驟、
      `docs/MUTATION_TESTING_PLAYBOOK.md` 工具章

---

## 2. 校準協議(Calibration — 註冊後、發版前)

1. 建兩個 pilot fixture(乾淨版 + 故意含缺陷版),跑 Gate 1→4。
2. 對照同等品質 Python 專案的 14 維分數分佈;重點看 lint/security 的
   violation 密度差異。
3. **門檻不動,動扣分係數**:若新語言工具天然回報更多/更少違規,調整
   scorer 係數使「同等品質 ≈ 同分」,並把依據寫進本檔附錄。
4. 連跑兩次確認分數零漂移(釘版驗證)。

## 3. 驗收定義(Definition of Done)

- [ ] `pytest tests/ -q` 全綠(既有測試零修改 — 行為保持的證據)
- [ ] `tests/test_toolchain_registry.py` 完整性不變量綠(新語言 × 4 gates × 全維度)
- [ ] `python3 scripts/list-modules.py --validate` 綠
- [ ] pilot 專案 P1→P8 端到端全 gate 通過,score 檔過 score.py R1/R2/R4/R5/R8
- [ ] `ruff check` / `pyright` 乾淨
- [ ] 校準記錄寫入本檔附錄

## 4. 明確不支援清單(v2.8 現狀,新語言比照)

- 混語言 monorepo(單一 state.json `language`;一專案一工具鏈)
- Mirror gate 的語意級謂詞對齊(Python-only;其他語言 structure-only + needs_review)
- `fix_low_coverage` / `fix_pytest_failures` auto-fix 策略(unwired,Python-only)
- Gate-1 FR-scope 的 import-graph 檔案級偵測(Python-only;JS 用標題過濾替代)

---

## 附錄 A — v2.8.0 JS/TS 校準記錄

**Pilot**: `tests/fixtures/ts_vitest_project`(2 FRs;故意缺陷:mapper.ts 未用變數
/ 無 error handling / 缺 JSDoc;一個 zero-assert 測試殼)。
工具版本:requirements.txt + fixture package.json 釘版(2026-06-10 對 npm/PyPI 驗證)。

| Dimension | Tool | Pilot 分數 | 判定 |
|---|---|---|---|
| linting | eslint | 96.0 | 缺陷偵測 ✓(violations ×−2,與 ruff 同曲線) |
| type_safety | tsc | 100.0 | clean compile ✓ |
| test_coverage | vitest-cov | 87.5 | 真 v8 coverage(json-summary artifact)✓ |
| security | semgrep-js | 100.0 | 無 findings(規則集另以含漏洞 fixture 驗證:2 ERROR+2 WARNING → 74.0)|
| readability | js-mi | 63.9 | tree-sitter MI;複雜檔 < 簡單檔排序已測 |
| error_handling | js-error-handling | 50.0 | mapper.ts 缺陷偵測 ✓ |
| documentation | js-doc-coverage | 50.0 | mapToken 缺 JSDoc 偵測 ✓ |
| test_assertion_quality | js-assertions | 75.0 | zero-assert 殼偵測 ✓ |
| performance | js-bench | None | 無 benchmarks → 維度尚不適用(同 pytest exit-5 語意)|
| mutation / secrets / license / architecture | stryker / gitleaks / scancode / CRG | —(skip-list / 語言無關) | precheck 與 unit 測試覆蓋 |

**係數調整**:無 — 沿用 Python 同維度曲線,pilot 上缺陷→扣分方向與幅度合理。
首個生產級 JS 專案跑完 Gate 2 後重新檢視 lint violation 密度。

**零漂移驗證**:eslint 與 js-mi 連跑兩次分數相同(ZERO-DRIFT)。

**過程中抓到並修復**:vitest 4 移除 `--reporter=basic`(custom-reporter 載入錯誤)
→ registry 與 phase_truth_verifier 改用預設 reporter(分數來源是 json-summary
artifact,reporter 無關)。教訓:外部工具旗標必須過 slow-tier 真實 e2e,unit
fixture 測不出 CLI 介面變動。

**環境注意**:semgrep / tree-sitter 裝在 harness 的 Python 環境;`run_tool` 以
ambient PATH 執行 — harness 環境未 activate 時 semgrep 會報 rc=-3(Tool not
found),S2 preflight 會先擋下,非 silent failure。
