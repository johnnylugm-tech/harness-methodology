# harness-methodology 測試規格遵守度改善方案

> 版本：v1.1  
> 日期：2026-05-19  
> 來源：omnibot-full Phase 1-5 TDD 缺口分析  
> 適用：harness-methodology P1~P8 流程優化

---

## 背景

對 `omnibot-full` 專案（FR-01~FR-24，344 個測試函式，98.4% line coverage）執行
完整 TDD 驗證清單比對後，發現 harness-methodology 存在三個結構性盲區，導致即使
Gate score 達標、coverage 優異，仍有大量規格要求的測試從未被寫出。

### 缺口規模摘要

| 層次 | 清單條目 | 已覆蓋 | 缺失 |
|------|---------|--------|------|
| Phase 1 功能單元（§1-13） | ~80 | ~50 | ~30 |
| Phase 2 功能單元（§14-24） | ~75 | ~65 | ~10 |
| Phase 3 功能單元（§25-40） | ~100 | 0 | ~100 |
| Cross-cutting（§41-50） | ~55 | ~5 | ~50 |
| **合計** | **~210+** | **~120** | **~90+** |

典型漏洞實例：
- `Knowledge CRUD API`（§11）完全無測試，coverage 仍 98.4%
- `Webhook 429` 端點整合測試缺失（unit 存在但 HTTP 層未驗）
- Redis Streams（§23）、Retry（§24）、Phase 3 全組（§25~§40）無 FR 對應
- 安全紅隊、KPI、部署驗證測試（§41-50）無任何規劃

---

## 根本原因診斷

### 缺陷 1：harness 量的是「代碼品質」，不是「規格遵守度」

**現狀**：Gate score = ruff + mypy + pytest-cov + phase_truth

這些全是**已存在程式碼**的品質指標，對「你根本沒寫這個 FR 的測試」完全盲目。
pytest-cov 無法偵測**不存在**的測試——它只能量已執行程式碼的覆蓋率。

**等效問題**：可以在完全沒有 Knowledge API endpoint 測試的情況下拿到 100 分，
只要其他模組的 line coverage 夠高。

### 缺陷 2：P1 SRS 產出「規格」，但不產出「測試清單」

**現狀**：P1 只要求產生 SRS.md。需要測試的清單（如 omnibot 的
`omnibot-tdd-verification-checklist.md`）是手動在 spec repo 另做，harness 完全
不知道它的存在，gate 也不驗證它。

**等效問題**：SRS 定義了 FR-07 Knowledge Layer，但 harness 沒有機制確認
`tests/test_fr07.py` 必須覆蓋 `version_desc 排序`、`limit_5`、
`inactive_excluded` 等具體行為。

### 缺陷 3：RED 階段沒有強制驗證

**現狀**：D1 只查 commit 時間間隔（防批次偽造），不查「測試 commit 是否早於
source commit」。

**omnibot 實證**：所有 FR 的 test + source 在同一 commit，完整繞過 RED 要求，
harness 未阻擋。

---

## 改善方案

### 改善 I-1：TEST_INVENTORY.yaml 成為 P1 法定交付物

**影響**：HIGH｜**實作難度**：MEDIUM

#### 說明

P1 在產出 SRS.md 的同時，必須產出 `TEST_INVENTORY.yaml`——一份機器可讀的
「必需測試函式清單」，涵蓋 unit、integration、security、kpi 四個測試層次，以及
所有 cross-cutting 跨切面測試（§41-50 類型）。

#### TEST_INVENTORY.yaml 格式

```yaml
# TEST_INVENTORY.yaml  (P1 交付物，P3/P4 Gate 驗證用)
format_version: "1.0"

fr_tests:
  FR-07:
    unit:
      - test_knowledge_layer_rule_match_exact_question
      - test_knowledge_layer_rule_match_orders_by_version_desc
      - test_knowledge_layer_rule_match_limit_5
      - test_knowledge_layer_rule_match_inactive_excluded
      - test_knowledge_layer_escalate_when_no_match
    integration:
      - test_api_knowledge_get_pagination
      - test_api_knowledge_post_returns_201
      - test_api_knowledge_not_found_returns_404

  FR-11:  # (依此格式列出所有 FR)
    unit:
      - test_knowledge_create_returns_api_response
    integration:
      - test_knowledge_bulk_import

cross_cutting:
  security:
    - test_redteam_prompt_injection_direct_webhook_payload
    - test_rate_limiter_redis_unavailable_blocks_all_by_default
    - test_redteam_rbac_agent_cannot_delete_knowledge
  kpi:
    - test_kpi_p95_latency_phase1_under_3s
    - test_gate_p1_fcr_at_least_50_percent
  deployment:
    - test_deploy_docker_compose_all_services_healthy
    - test_backup_pg_basebackup_and_restore
  version_consistency:
    - test_backward_compat_phase1_tests_pass_in_phase2_env
```

#### 新增 harness 命令：`check-test-inventory`

```python
def cmd_check_test_inventory(args: argparse.Namespace) -> int:
    """D4: Test Inventory Compliance — compare TEST_INVENTORY.yaml against actual test files."""
    project = Path(args.project).resolve()
    inventory_path = project / "TEST_INVENTORY.yaml"

    if not inventory_path.exists():
        if args.strict:
            print("[BLOCKED] TEST_INVENTORY.yaml not found. P1 must produce this file.")
            return 8
        print("[WARN] TEST_INVENTORY.yaml not found — skipping D4 check.")
        return 0

    # 掃描 tests/*.py 取得所有函式名
    actual_fns = _scan_test_functions(project / "tests")

    # 讀清單
    required = yaml.safe_load(inventory_path.read_text())
    all_required = []
    for fr, layers in required.get("fr_tests", {}).items():
        for fns in layers.values():
            all_required.extend(fns)
    for fns in required.get("cross_cutting", {}).values():
        all_required.extend(fns)

    missing = [f for f in all_required if f not in actual_fns]
    covered = len(all_required) - len(missing)
    pct = covered / len(all_required) * 100 if all_required else 100.0

    print(f"[D4] Test Inventory: {covered}/{len(all_required)} ({pct:.1f}%)")
    if missing:
        print(f"  Missing ({len(missing)}):")
        for fn in missing[:20]:
            print(f"    ✗ {fn}")

    threshold = getattr(args, "threshold", 80.0)
    if pct < threshold:
        print(f"\n[BLOCKED] D4 Test Inventory Compliance {pct:.1f}% < {threshold}% threshold")
        return 1
    return 0
```

#### Gate 整合

- Gate 2（P3 exit）：D4 pre-check，threshold = 60%（寬鬆，因 Phase 2/3 測試尚未完成）
- Gate 3（P4 exit）：D4 正式計分維度，threshold = 80%
- Gate 4（P6 exit）：D4 threshold = 90%（含 cross-cutting 全部）

#### P1 checklist 新增強制項

```
□ 產出 TEST_INVENTORY.yaml（格式：fr_tests + cross_cutting，含 unit/integration/security/kpi 分層）
□ 每個 FR 的 integration 層至少列出對應 API endpoint 的 2xx + 4xx 測試名稱
□ cross_cutting.security 至少涵蓋 prompt injection、rate limit、PII 三類
□ cross_cutting.deployment 至少涵蓋 docker-compose health + DB migration
```

#### 生命週期管理（P1→P3 同步機制）

TEST_INVENTORY.yaml 由 P1 產出，但 P3 開發過程中 FR 可能拆分、合併、新增。
為防止「P1 灌水少列規避 threshold」以及「P3 加新 FR 但 YAML 未更新」：

1. **P1→P2 transition**：`advance-phase --completed 1` 時對 YAML 做 checksum
   （記錄 `sha256(TEST_INVENTORY.yaml)` 到 state.json）
2. **Gate 1 per-FR**：每次 `finalize-gate --gate 1 --fr-id FR-XX` 時檢查該 FR
   是否在 YAML 中聲明。未聲明 → 非關鍵 block，但記錄 warn 到 gate 日誌
3. **P2/P3 diff 檢測**：`check-test-inventory --diff-mode` 比較當前 YAML 與 P1 checksum，
   若 FR 數量減少超過 20% → 觸發 review 提醒（非硬 block）
4. **Gate 3 D4 正式計分**：以 finalize 當下的 YAML 為準，不回溯 P1

---

### 改善 I-2：Gate 1 強制 FR→測試檔案對應

**影響**：HIGH｜**實作難度**：LOW（~15 行）

#### 說明

每個 FR 在 Gate 1 finalize 時，必須在 `tests/` 目錄下存在對應的測試檔案，
否則硬封鎖。這杜絕「先 implement 後補測試」以及「整個 FR 無測試但 coverage 不降」
兩種模式。

#### 實作位置

`cmd_finalize_gate` → Gate 1 路徑，接在 HR-10 sessions_spawn.log 檢查之後。

```python
def _check_fr_test_file_exists(project: Path, fr_id: str) -> tuple[bool, str]:
    """每個 FR 必須有對應的 test_frXX.py 才能 finalize Gate 1."""
    m = re.match(r"FR-(\d+)", fr_id, re.IGNORECASE)
    if not m:
        return True, ""  # 非標準 FR-ID，略過
    num = m.group(1).zfill(2)
    test_dir = project / "tests"
    # 接受 test_fr07.py 或 test_fr7.py 兩種命名
    patterns = [f"test_fr{num}.py", f"test_fr{num.lstrip('0')}.py"]
    for pat in patterns:
        if (test_dir / pat).exists():
            return True, ""
    return False, (
        f"[BLOCKED] FR test file missing: tests/test_fr{num}.py\n"
        f"  TDD requires a test file BEFORE implementation is merged.\n"
        f"  Create tests/test_fr{num}.py with at minimum one failing test."
    )

# 插入 cmd_finalize_gate Gate 1 section：
# ── D-FR: FR test file completeness ──────────────────────────────────
if args.gate == 1 and fr_id:
    _fr_ok, _fr_msg = _check_fr_test_file_exists(project_path, fr_id)
    if not _fr_ok:
        print(_fr_msg)
        return 8  # exit 8 = Missing deliverables
```

---

### 改善 I-3：RED Phase 強制（增強 D1）

**影響**：HIGH｜**實作難度**：LOW（~40 行）

#### 說明

現有 D1 只查 commit 時間間隔（防批次偽造）。新增對 commit ordering 的驗證：
`tests/test_frXX.py` 的首次 commit 時間戳必須**早於**對應 source 檔案的首次
commit 時間戳。

這是 TDD RED→GREEN 的最低限度驗證。

#### 實作

```python
def _check_red_phase_ordering(project: Path, fr_id: str) -> tuple[bool, str]:
    """D1 extension: 測試 commit 必須早於 source commit（TDD RED→GREEN）."""
    m = re.match(r"FR-(\d+)", fr_id, re.IGNORECASE)
    if not m:
        return True, ""
    num = m.group(1).zfill(2)

    def _first_commit_ts(glob_pattern: str) -> float | None:
        """取指定 glob 最早的 add commit 時間戳（Unix epoch）."""
        r = subprocess.run(
            ["git", "-C", str(project), "log", "--diff-filter=A",
             "--format=%ct", "--", glob_pattern],
            capture_output=True, text=True, timeout=10,
        )
        lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        # git log 預設新→舊；最後一行是最早
        return float(lines[-1]) if lines else None

    test_ts = _first_commit_ts(f"tests/test_fr{num}.py")
    if test_ts is None:
        # 也接受 test_fr{num.lstrip('0')}.py
        test_ts = _first_commit_ts(f"tests/test_fr{num.lstrip('0')}.py")

    if test_ts is None:
        return False, (
            f"[BLOCKED] D1-RED: tests/test_fr{num}.py has no git history.\n"
            f"  Commit the failing test BEFORE implementing the source."
        )

    # 依 FR-NN 推斷 source 路徑命名慣例（可配置）
    src_patterns = [
        f"*/fr{num.lstrip('0')}*",
        f"*fr_{num.lstrip('0')}*",
        f"*fr{num}*",
    ]
    src_ts: float | None = None
    for pat in src_patterns:
        src_ts = _first_commit_ts(pat)
        if src_ts is not None:
            break

    if src_ts is not None and src_ts < test_ts:
        lag = int(test_ts - src_ts)
        return False, (
            f"[BLOCKED] D1-RED: Source committed {lag}s BEFORE test for {fr_id}.\n"
            f"  TDD requires RED (failing test commit) → GREEN (source commit).\n"
            f"  The test file's first commit must predate the source file's first commit."
        )
    return True, ""
```

#### 插入位置

`cmd_finalize_gate` Gate 1 路徑（`fr_id` 已存在，接在 HR-10 區塊後）：

```python
if args.gate == 1 and fr_id:
    # I-2: FR test file existence check
    _fr_ok, _fr_msg = _check_fr_test_file_exists(project_path, fr_id)
    if not _fr_ok:
        print(_fr_msg)
        return 8
    # I-3: RED phase ordering
    _red_ok, _red_msg = _check_red_phase_ordering(project_path, fr_id)
    if not _red_ok:
        print(_red_msg)
        return 1
```

注意：`fr_id` 由 CLI 參數 `--fr-id` 傳入（現有 Gate 1 已實作此模式），
無需額外解析。

#### 實作注意事項

**時間戳選擇**：`git log --format=%at`（author timestamp）優先於 `%ct`（committer
timestamp）。Rebase 會更新 committer timestamp 但保留 author timestamp，使用 `%at`
可降低 false positive。若 `%at` 不可得（import 歷史等）再 fallback 到 `%ct`。

**效能**：Gate 1 finalize 時可能含 10+ 個 FR，每 FR 2 次 `git log` subprocess call。
批次化：單次 `git log --name-only --format="%at %H" -- "tests/test_fr*.py"` 一次
讀取所有 FR 的檔案新增時間，再用 Python 解析分組，而非逐 FR 呼叫。

#### 已知限制

- git history 重寫（`rebase -i`, `--amend`）可繞過，但現有 D1 batch-fabrication
  防護已有一定嚇阻效果，兩者互補。
- Source pattern 推斷 `f"*/fr{num}*"` 依賴命名慣例；對 source 分散多檔或命名不一致
  的專案可能找不全。建議在 `project.json` 或 gate YAML 中加入可配置的
  `source_patterns` 覆寫。

---

### 改善 I-4：SRS 模板加入 Cross-cutting Test Section

**影響**：HIGH｜**實作難度**：LOW（模板修改）

#### 說明

P1 SRS 模板末尾加入強制章節。`cmd_plan_phase --phase 1` 生成 SRS.md 時自動注入。
這使 §41-50 類型的跨切面測試從 P1 就被明確規劃，不再落入 FR 縫隙。

#### 注入到 SRS.md 模板的章節

```markdown
## Cross-Cutting Test Requirements

> 此章節由 harness P1 模板自動注入，開發者必須填入具體測試名稱後才可進入 P2。

### API Completeness（每個端點必須有以下四類測試）
- 正常流程 (2xx)
- 認證失敗 (401)
- 速率限制 (429)
- 驗證錯誤 (400/422)

**待填清單**（開發者補充）：
- [ ] `test_webhook_telegram_rate_limited_returns_429`
- [ ] `test_api_knowledge_not_found_returns_404`
- [ ] ...

### Security Red Team
- [ ] `test_redteam_prompt_injection_direct_webhook_payload`
- [ ] `test_redteam_rate_limit_burst_attack_blocked`
- [ ] `test_redteam_pii_mixed_real_fake_card_luhn`

### KPI Gates（對應 ODD SQL + k6）
- [ ] `test_kpi_p95_latency_phase<N>_under_<X>s`
- [ ] `test_kpi_fcr_phase<N>_target_<X>_percent`

### Deployment Smoke
- [ ] `test_deploy_docker_compose_all_services_healthy`
- [ ] `test_deploy_health_endpoint_returns_200_after_startup`
- [ ] `test_backup_pg_basebackup_and_restore` (Phase 3)

### Version Consistency（Phase 2+ 必填）
- [ ] `test_backward_compat_phase<N-1>_tests_pass_in_phase<N>_env`
```

#### P1 checklist 新增項目

```
□ Cross-Cutting Test Requirements 章節：所有空格填寫完整（不可留 placeholder）
□ 每個 API 端點至少列出 4 類測試名稱
□ Security red team 至少 3 個具體測試
□ KPI gates 對應具體目標值（不可寫「TBD」）
```

#### 自動化驗證限制

此章節的 `[ ]` checklist 依賴 human compliance（P1→P2 時人工確認），後續 gate
無對應的自動化掃描。一項緩解措施：`check-test-inventory` 加上 `--srs-crosscut`
flag 掃描 SRS.md 的 Cross-Cutting 章節，確認所有 `[ ]` 已被 `[x]` 取代且
`<N>`、`<X>` placeholder 已被具體值替換。

---
### 改善 I-5：Integration 測試層區分

**影響**：MEDIUM｜**實作難度**：LOW（conftest + gate YAML）

#### 說明

在 `init-project` 生成的標準 `conftest.py` 中加入 pytest marker，強制區分
unit（mock DB）與 integration（真實 HTTP）。Gate 3 新增
`integration_coverage` 維度。

#### conftest.py 注入

```python
# conftest.py (harness init-project 注入的標準配置)
import pytest

def pytest_configure(config):
    config.addinivalue_line("markers", "unit: pure unit test (all external deps mocked)")
    config.addinivalue_line("markers", "integration: integration test (real HTTP / real DB)")
    config.addinivalue_line("markers", "security: security / red team test")
    config.addinivalue_line("markers", "kpi: KPI validation test (ODD SQL / k6 wrapper)")

def pytest_collection_modifyitems(items):
    # 未標 marker 的測試預設為 unit（不強制 fail，降低遷移成本）
    for item in items:
        if not any(item.iter_markers(name=m)
                   for m in ["unit", "integration", "security", "kpi"]):
            item.add_marker(pytest.mark.unit)
```

#### gate_3.yaml 新增維度

```yaml
# 新增到 gate_3.yaml dimensions 清單
- name: integration_coverage
  tier: 2
  requires_tool_execution: true
  tool: pytest
  description: |
    執行：pytest -m integration --collect-only -q（只收集，不執行）
    Score = min(100, (integration_test_count / api_endpoint_count) * 100)
    Pass threshold: 每個 SAD 定義的 API endpoint ≥ 1 個 integration test
    若無 integration tests 則 score = 0（不得以 unit 替代）

##### api_endpoint_count 來源定義

`api_endpoint_count` 從 SAD.md 解析：掃描 `## API` 章節下方的方法+路徑行
（`GET|POST|PUT|DELETE|PATCH /…`）。若 SAD.md 無 API 章節或屬純 library 專案，
此維度自動跳過（score = N/A，不計入 gate 權重）。

##### conftest.py 注入事實修正

現有 `conftest.py` 是 harness 自用測試配置（已有 markers: core/extended/integration/gate
等），**非** `init-project` 生成。I-5 需在 templates/ 新增獨立的 `conftest.target.yaml`
作為專案 conftest.py 範本，而非修改現有檔案。

---

## 改善後的流程對比

```
【現在的 harness 流程】
P1: SRS.md
           ↓
P3: code + tests
           ↓ run-gate
           ruff / mypy / pytest-cov → Gate score
           ↑
    看不出「少了什麼測試」——coverage 98.4% 但 90 條測試從未被要求寫

【改善後的流程】
P1: SRS.md + TEST_INVENTORY.yaml
    └─ 含 unit/integration/security/kpi/cross-cutting 全分層清單
           ↓
P3: Gate 1 per-FR
    ├─ I-2: test_frXX.py 必須存在，否則 exit 8
    └─ I-3: test commit 時間 < source commit 時間，否則 exit 1
           ↓
P4: Gate 3
    ├─ I-1: D4 TEST_INVENTORY coverage ≥ 80%（逐一比對函式名）
    ├─ I-5: integration_coverage 維度（每 endpoint ≥ 1 整合測試）
    └─ ruff / mypy / pytest-cov（現有維度保留）
           ↓
P6: Gate 4
    └─ I-1: D4 threshold ≥ 90%（cross-cutting 全部到位）
```

---

## 偵測能力預估

| 缺口類型（omnibot 實例） | 現在 | 改善後 |
|---|---|---|
| Knowledge CRUD API 完全無測試 | ❌ coverage 98% 不報 | ✅ I-1 D4 missing 清單 |
| Webhook 429 端點整合缺失 | ❌ | ✅ I-5 integration marker check |
| Redis Streams / Retry 無 FR 測試檔 | ❌ | ✅ I-2 Gate 1 block |
| Phase 3 整組（§25-40）缺失 | ❌ | ✅ I-1 TEST_INVENTORY 清點 |
| RED 階段未遵守（test = source 同 commit） | ❌ D1 只看時間 | ✅ I-3 commit ordering |
| 安全紅隊測試無規劃 | ❌ | ✅ I-4 SRS cross-cutting 強制 |
| KPI / 部署測試落入 FR 縫隙 | ❌ | ✅ I-4 + I-1 cross_cutting 節 |

**預估殘餘盲區（~10%）**：測試存在但語意錯誤（如 FR-11 的
`pgvector_index` 斷言缺失），仍需 Agent B reviewer 人工判斷。

---

## 未涵蓋的缺口（已知殘餘）

### 測試語意正確性

所有 I-1~I-5 改善僅檢查「測試存在與否」（檔案存在、函式名存在、commit 順序），
不檢查測試的語意品質（assertion 是否合理、邊界案例是否覆蓋）。這是方案的主要
盲區，需仰賴 Agent B reviewer Code Review、mutation testing score、以及人工
審查來補足。

### SRS cross-cutting checklist 自動化程度

I-4 的 `[ ]` checklist 無對應 gate 層級的自動驗證。`check-test-inventory` 可加
`--srs-crosscut` flag 做正則掃描，但 checkbox 被勾選不等於測試存在，仍為
semantic gap。

---

### 改善 I-6：測試語意品質自動化偵測（基於現有工具）

**影響**：HIGH｜**實作難度**：MEDIUM

#### 說明

I-1~I-5 只檢查「測試存在」，不檢查「測試寫對沒」。但 harness 已有兩個現成工具
可以量測測試的語意品質，無需引入新基礎設施。

#### I-6a: Mutation Score 硬門檻（現有工具強化）

**現狀**：`mutation_testing`（Gate 2/3/4）是 Tier 1 維度，由 Gemini Flash 評分。
但 mutmut 的輸出（survived/killed mutant 比例）本身就是客觀的**語意品質度量**——
被突變殺死的測試比例直接反映 assertion 的抓錯能力。

**強化方案**：

```yaml
# gate_2.yaml / gate_3.yaml / gate_4.yaml 修改
mutation_testing:
  tier: 1          # 維持 Tier 1（工具執行是強制的，R8）
  threshold: 70    # 現有
  tool: mutmut
  requires_tool_execution: true
  # 新增：mutant_kill_rate 取代 llm_score
  # 當 mutmut 實際執行成功時，score = min(mutmut_score, llm_score)
  # 而非現有的 min(tool_score, llm_score) → tool_score = mutmut_score
  # 若 mutmut 執行失敗（SUSPENDED），此維度不計分
  objective_primary: true   # ← 新增 flag
```

關鍵規則：
1. `objective_primary: true` → score.py 對此維度改用 `min(mutmut_score, llm_score)` 而非 `min(tool_score, llm_score)`
2. mutmut_score 來自 `mutmut results` 的 `survived/total` 比例：`(1 - survived/total) * 100`
3. 若 mutmut 執行成功，tool_score 即為客觀分數，llm_score 只能下調不能上調
4. Gate 2/3 現有 threshold 70 不變

**實作變更**：
- `score.py`：R8 後新增 `R8b` 規則，檢查 `objective_primary` flag，強制使用 tool_score 作為 primary
- `evaluate_dimension.md`：mutation_testing 章節 Step 3 加入 `objective_primary` 評分規則
- Gate YAML：三個 gate 的 mutation_testing 區塊加入 `objective_primary: true`

#### I-6b: Assertion Density Score（新 Tier 2 維度）

**原理**：每個 test function 的 assert 數量低 → 測試很可能寫了但沒真正驗證行為。
現有工具即可計算。

**gate_3.yaml 新增**：

```yaml
- name: test_assertion_quality
  tier: 2
  requires_tool_execution: false  # 不需安裝額外工具，用 Bash 完成
  description: |
    使用 Python AST 靜態分析 tests/ 目錄，計算以下指標：

    # 1) Assertion Density: assert 總數 / test function 總數
    python3 -c "
    import ast, sys
    from pathlib import Path
    for f in Path('tests').rglob('*.py'):
        try:
            tree = ast.parse(f.read_text())
            funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith('test_')]
            asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
            print(f'{f.name}: {len(funcs)} tests, {len(asserts)} asserts, {len(asserts)/max(len(funcs),1):.1f} avg')
        except SyntaxError:
            print(f'{f.name}: parse error')
    "

    # 2) Zero-Assert Test Detection: 找出完全沒有 assert 的 test function
    python3 -c "
    import ast
    from pathlib import Path
    zero_assert = []
    for f in Path('tests').rglob('*.py'):
        try:
            tree = ast.parse(f.read_text())
            for n in ast.walk(tree):
                if isinstance(n, ast.FunctionDef) and n.name.startswith('test_'):
                    has_assert = any(isinstance(s, ast.Assert) for s in ast.walk(n))
                    if not has_assert:
                        zero_assert.append(f'{f.name}::{n.name}')
        except SyntaxError:
            pass
    if zero_assert:
        print(f'Zero-assert tests ({len(zero_assert)}):')
        for z in zero_assert[:20]:
            print(f'  - {z}')
    else:
        print('All test functions have at least one assert.')
    "

    Score = min(100, assertion_density * K1 + (1 - zero_assert_ratio) * K2)
    # K1, K2 為可配置權重；預設 K1=25, K2=50
    # 當 assertion_density ≥ 2.0 且 zero_assert_ratio ≤ 5% 時得分 ≥ 80
```

**Threshold 校準規則**：上述 `assertion_density ≥ 2.0`、`zero_assert_ratio ≤ 5%`、
`K1=25`、`K2=50` 是**初始預設值**。實作後首次執行時必須以
**harness-methodology 自身專案的測試資料**為 baseline 校準，而非以 omnibot 或
外部專案的數據決定 threshold。做法：

1. 首次部署時以 `--collect-only` 模式執行 assertion scan，記錄當前專案的 density
   與 zero-assert ratio 作為 baseline
2. 若 baseline 本身即低於 threshold（如 density=1.5），則門檻應逐步調升
   （如先設 threshold=baseline×0.8，每 gate round +10%），避免一次壓死
3. 每 Gate 3/4 round 記錄 assertion quality score，追蹤長期趨勢後再鎖定最終門檻

**Gate 整合**：
- Gate 3（P4 exit）：正式計分維度，weight = 0.05（輕量，從 test_coverage weight 挪出）
- Gate 4（P6 exit）：維持，weight 不變

#### I-6c: CRG Untested Hub 儀表板

**現狀**：CRG 的 `untested_hots` / `untested_hotspots` / `bridge_needs_tests`
已在 `crg_analysis.py` computed metrics 中（issue_tracker severity 映射），
但在 gate 評分中未直接使用。

**強化方案**：在 D4（I-1 `check-test-inventory`）中加 `--crg-gaps` flag，
讀取 `.sessi-work/crg_reconnaissance.json` 的 `untested_hotspots` 清單，
與 TEST_INVENTORY.yaml 的 fr_tests 比對：

```python
def _check_crg_test_gaps(project: Path) -> list[str]:
    """Cross-reference CRG untested hotspots against TEST_INVENTORY.yaml."""
    crg_path = project / ".sessi-work" / "crg_reconnaissance.json"
    if not crg_path.exists():
        return []
    recon = json.loads(crg_path.read_text())
    untested = recon.get("untested_hotspots", [])
    if not untested:
        return []

    inventory = project / "TEST_INVENTORY.yaml"
    if not inventory.exists():
        return [f"CRG reports {len(untested)} untested hotspots, but TEST_INVENTORY.yaml missing"]

    required = _flatten_test_names(yaml.safe_load(inventory.read_text()))
    # CRG hotspot names are function names; check if any are NOT in the inventory
    gaps = [h for h in untested if h.get("name") not in required]
    return [f"TEST_INVENTORY missing CRG-reported hotspot: {g['name']} (fan_in={g.get('fan_in','?')})"
            for g in gaps[:10]]
```

**Gate 整合**：Gate 3/4 D4 的延伸檢查，不獨立成維度。

#### 預估效果

| 語意問題類型 | 現有工具 | 改善前 | I-6a | I-6a+I-6b | +I-6c |
|------------|---------|--------|------|-----------|-------|
| 測試無 assert（只 call 不驗證） | AST | ❌ | ❌ | ✅ | ✅ |
| Test coverage 高但 assertion 弱 | mutmut | ❌ | ✅ | ✅ | ✅ |
| High fan-in 函式完全無測試 | CRG | ❌ | ❌ | ❌ | ✅ |
| 測試 assertion 寫錯（邏輯相反） | N/A | ❌ | ❌ | ❌ | ❌ |

最後一列（assertion logic error）無工具可自動偵測，需仰賴 mutation testing 側面
反映 + Code Review。

**實施優先**：
| 優先 | 項目 | 主要變更 | 行數 |
|------|------|---------|------|
| P1 | I-6a mutation objective_primary | `score.py`, `gate_*.yaml`, `evaluate_dimension.md` | ~30 |
| P2 | I-6b assertion density | `gate_3.yaml`, new score script or inline bash | ~50 |
| P2 | I-6c CRG untested hub cross-ref | `cmd_check_test_inventory` CRG flag | ~40 |

---

## 導入策略（Backward Compatibility）

**決定：不實作 migration helpers。** 既有專案只需手動補 `TEST_INVENTORY.yaml` 後即可
正常 advance，無需特殊 migration code。新專案從 P1 開始自動適用完整生命週期。

---

## Harness 自身測試計畫

| 改善 | 需新增測試 | 驗證方式 |
|------|-----------|---------|
| I-1 `cmd_check_test_inventory` | `test_test_inventory_missing_file`, `test_test_inventory_all_covered`, `test_test_inventory_below_threshold`, `test_test_inventory_lifecycle_checksum` | 建立暫存 YAML + mock test files |
| I-2 `_check_fr_test_file_exists` | `test_fr_test_file_exists_ok`, `test_fr_test_file_missing_fr07`, `test_fr_test_file_non_standard_fr_skip` | 暫存 test 目錄 + 空檔案 |
| I-3 `_check_red_phase_ordering` | `test_red_ordering_test_first`, `test_red_ordering_source_first_blocked`, `test_red_ordering_no_git_history`, `test_red_ordering_batched_performance` | 暫存 git repo + 控制 commit 順序 |
| I-4 SRS cross-cutting scan | `test_srs_crosscut_checklist_complete`, `test_srs_crosscut_placeholder_detected` | 模板比對 |
| I-5 integration marker | `test_integration_coverage_score`, `test_integration_coverage_no_api_skip` | pytest collect-only + mock SAD.md |

預估測試行數：~200 行，分散在 `tests/test_score_validator.py`（新增 class）和
`tests/test_harness_cli.py`（或新增 `tests/test_test_compliance.py`）。

---

## 實施優先順序（修正版）

| 優先 | 改善 | 主要變更檔案 | 估計行數 |
|------|------|------------|---------|
| P0（立即） | I-2 FR test file check | `harness_cli.py` | ~15 |
| P0（立即） | I-3 RED phase ordering（`%at` + 批次化） | `harness_cli.py` | ~55 |
| P1（本週） | I-4 SRS cross-cutting section | `templates/SRS.md` + `harness_cli.py` (checklist scan) | ~50 |
| P1（本週） | I-5 integration marker | `templates/conftest.target.yaml` + `gate_3.yaml` | ~30 |
| P2（下週） | ~~I-1 TEST_INVENTORY + check command + lifecycle~~ | `harness_cli.py` + new YAML schema | ~150 ✅ |
| P2（下週） | Harness self-tests（I-1~I-5） | `tests/test_test_compliance.py` | ~200 |
| ~~P3（下下週）~~ | ~~Migration helpers（`--warn-only`, `--threshold`）~~ | ~~`harness_cli.py`~~ | ~~~40~~ 🚫 |

---

## I-1 完成狀態

- `templates/TEST_INVENTORY.yaml` — ✅ 已完成
- `harness_cli.py` — `cmd_check_test_inventory` + `_run_test_inventory_check` helper
- Lifecycle:
  - P1 `advance-phase` — checksum 寫入 state.json ✅
  - `cmd_finalize_gate` — Gate 2 threshold=60%, Gate 3=80%+CRG, Gate 4=90%+CRG+SRS ✅
- CONSTITUTION.md — D4_TestInventory 維度已加入 ✅

*文件版本：v1.1 | 2026-05-19*  
*對應分析來源：omnibot-full vs. omnibot-tdd-verification-checklist.md*
