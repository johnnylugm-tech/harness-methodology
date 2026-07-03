# Phase 9 — Maintenance (P9 SOP)

<!-- Role A: DEVELOPER | Role B: REVIEWER -->
<!-- Input: all P1-P8 artifacts + incoming problem reports / change requests -->
<!-- Output: 09-maintenance/MAINTENANCE_LOG.md + updated SRS/SAD/TEST_SPEC/src/tests -->
<!-- Exit: NEVER — P9 is a re-entrant steady state (advance-phase --completed 9 is BLOCKED) -->

## P9 前提

P9 是交付後的**維護態**：可長期駐留、可重複進入,所有工作以變更工單
(Change Request)為單位。對應 ASPICE:

- **CR-BUG**(`--type bug`)→ SUP.9 問題解決管理(Problem Resolution Management)
- **CR-FEAT**(`--type feat`)→ SUP.10 變更請求管理(Change Request Management)

鐵律:**沒有 CR 就沒有程式碼改動**。每張 CR 的改動必須寫回既有 phase
資料夾產物(01-requirements/SRS.md、02-architecture/SAD.md + TEST_SPEC.md、
03-development/src + tests),並重新進入追溯鏈——`cr-close` fail-closed
強制執行,任何缺項都會列出並擋下。

進入方式:P8 完成後 `advance-phase --completed 8`(entry gate:Gate 4 PASS +
`state.json phase_completed[8]`)。

---

## Step 1 — 開票(both types)

```bash
python3 harness_cli.py cr-open --type bug  --title "..." --severity high --project .
python3 harness_cli.py cr-open --type feat --title "..." --project .
```

工單寫入 `.methodology/change_requests/CR-NN.json`(機器狀態,單一事實來源)。
狀態機:`OPEN → ANALYZED → APPROVED → IN_PROGRESS → VERIFIED → CLOSED`,
任一非終態可 → `REJECTED`(需 `rejected_reason`)。

---

## Step 2A — CR-BUG 流程(SUP.9)

1. **Repro 先行**(TDD-RED 語義):先寫**失敗的**復現測試,再記錄:
   ```bash
   python3 harness_cli.py cr-update --cr CR-NN --set repro_test=tests/test_crNN_repro.py
   ```
   > repro_test 路徑必須實存(anti-fabrication),否則 APPROVED 被擋。
2. **Root cause** 記入工單後才能 ANALYZED:
   ```bash
   python3 harness_cli.py cr-update --cr CR-NN --set root_cause="..." --status ANALYZED
   python3 harness_cli.py cr-update --cr CR-NN --status APPROVED
   python3 harness_cli.py cr-update --cr CR-NN --status IN_PROGRESS
   ```
3. **修復**:保留 `[FR-XX]` 註解;若 SRS 驗收條件本身錯誤,允許修正 SRS.md
   並記入 `impact_analysis`。
4. **驗證**:repro test 轉綠 + 全套測試綠。

## Step 2B — CR-FEAT 流程(SUP.10)

1. **影響分析 + 核准**(SUP.10 approval decision):
   ```bash
   python3 harness_cli.py cr-update --cr CR-NN \
       --set affected_frs=FR-XX,FR-YY \
       --set impact_analysis.srs=true --set impact_analysis.sad=true \
       --set impact_analysis.test_spec=true \
       --set approval.approved_by=<name> --set approval.justification="..."
   python3 harness_cli.py cr-update --cr CR-NN --status ANALYZED
   python3 harness_cli.py cr-update --cr CR-NN --status APPROVED --status IN_PROGRESS
   ```
2. **規格回寫**(寫回凍結產物,不繞過):
   - `01-requirements/SRS.md` — 新增/更新 `### FR-XX:` 條目(canonical ID)
   - `02-architecture/SAD.md` — FR→module 表列;新模組 → `amend-sab`
   - `02-architecture/TEST_SPEC.md` — FR 測試 section;`TEST_INVENTORY.yaml` 條目
3. **TDD 實作**(與 P3 同紀律):
   ```bash
   python3 harness_cli.py run-fr-step --step TDD-RED --fr-id FR-XX --phase 9 --project .
   # → TDD-GREEN → TDD-IMPROVE → GATE1
   ```

---

## Step 3 — Gate 1 重評(both types)

觸碰的每個 FR 必須重跑 Gate 1;未觸碰的 FR 走 delta 回歸驗證:

```bash
python3 harness_cli.py run-gate      --gate 1 --fr-id FR-XX --phase 9 --project .
python3 harness_cli.py finalize-gate --gate 1 --fr-id FR-XX --phase 9 --project .
# 未觸碰 FR(回歸確認):
python3 harness_cli.py run-gate --gate 1 --fr-id FR-YY --phase 9 --delta --project .
```

> quality_manifest 更新必須是**外科手術式 append**(fr_ids 與
> fr_module_traceability 同步增長)——嚴禁 `--force` 整檔重生,
> 那會抹掉累積的 gate_results 並觸發 manifest 完整性 Pattern A 阻斷。

---

## Step 4 — 追溯鏈重建(both types)

```bash
python3 harness_cli.py build-trace-attestation --project . --write
python3 harness_cli.py verify-trace --project .   # 必須 exit 0
```

---

## Step 5 — 收尾(cr-close,fail-closed)

```bash
python3 harness_cli.py cr-update --cr CR-NN --set resolution.fix_commit=<sha> --status VERIFIED
python3 harness_cli.py cr-close  --cr CR-NN --project .
```

`cr-close` 檢查清單(任一缺項 → 列出 + exit 1,工單不變):
1. 工單內在證據:`resolution.fix_commit`;CR-BUG 另需 repro_test 實存;`affected_frs` 非空
2. Gate 1:每個 affected FR 在 quality_manifest `gate_results.gate1` 有 `quality_complete: true`
3. Trace attestation:`verify_attestation` exit 0
4. Drift:spec/SAD drift 無 HIGH+ 項目

全過 → 工單 CLOSED + `09-maintenance/MAINTENANCE_LOG.md` append + decision log 稽核條目。

---

## Step 6 — Milestone push(每張 CR 一推)

```bash
python3 harness_cli.py push-milestone --type cr-close --cr CR-NN --project .
```

> 前提:工單已 CLOSED。寫 HANDOVER.md(phase 9)+ commit + push。

---

## P9 不變量

| 不變量 | 執行者 |
|--------|--------|
| 無 CR 不改碼 | 人工紀律 + MAINTENANCE_LOG 稽核 |
| CR-BUG 無實存 repro test 不得 APPROVED/CLOSED | `cr_manager.validate_transition`(anti-fabrication) |
| CR-FEAT 無核准人+理由不得 APPROVED | `cr_manager.validate_transition` |
| 觸碰 FR 必重跑 Gate 1 | `cr-close` 檢查 quality_manifest |
| manifest 只能外科 append | 人工紀律 + `preflight_manifest_integrity` Pattern A |
| attestation 收尾必乾淨 | `cr-close` → `verify_attestation` |
| `advance-phase --completed 9` 永遠 BLOCKED | `cmd_advance_phase` |
