# Phase 8 — Configuration Management (P8 SOP)

<!-- Role A: DEVOPS | Role B: ARCHITECT -->
<!-- Input: all P1-P7 artifacts + RISK_REGISTER.md -->
<!-- Output: CONFIG_RECORDS.md -->
<!-- Exit: Phase Truth ≥ 90% (HR-11) — cleared by P6 Gate 4 -->

## P8 前提

P8 記錄每個 FR 的配置項，驗證環境一致性，並掃描機密洩漏。
P8 是管線最終 phase。沒有獨立 exit gate — 由 P6 Gate 4 背書，
以 Phase Truth ≥ 90% (HR-11) 為退出條件。

---

## Step 1 — Configuration Item Audit（Agent A: DEVOPS, 逐 FR）

針對每個 FR，記錄所有配置項：

### 1.1 Environment Variables
```markdown
| FR ID | Variable | Dev | Staging | Prod | Purpose |
|-------|----------|-----|---------|------|---------|
| FR-01 | API_URL  | http://... | https://... | https://... | backend endpoint |
```

### 1.2 Secrets & Credentials
- 每個 secret 的儲存方式（env / vault / secrets manager）
- 是否有 hardcoded secret（→ 立即修復，block release）
- Rotation policy（何時輪換、誰負責）

### 1.3 Feature Flags
```markdown
| FR ID | Flag Name | Default | Override Env | Owner |
|-------|-----------|---------|-------------|-------|
| FR-01 | ENABLE_NEW_AUTH | false | prod=true | ... |
```

### 1.4 External Dependencies
- API endpoints、資料庫連線字串、第三方服務
- 每個依賴的 SLA / timeout / retry policy

---

## Step 2 — Secret Leak Scan

```bash
# Scan codebase for hardcoded secrets
grep -rE "(password|secret|token|api_key|private_key)\s*=\s*['\"]" \
  --include="*.py" --include="*.yml" --include="*.yaml" \
  --include="*.json" --include="*.env" . | grep -v "example\|test\|mock"
```

> 任何匹配項必須在 P8 exit 前解決（移入 env var / vault）。

---

## Step 3 — Env Parity Check

```bash
# Compare env var keys across environments (shell-based, no script required)
diff <(grep -oE '^[A-Z_]+' .env.dev | sort) <(grep -oE '^[A-Z_]+' .env.prod | sort)
diff <(grep -oE '^[A-Z_]+' .env.staging | sort) <(grep -oE '^[A-Z_]+' .env.prod | sort)
```
> 無差異 = env key sets 一致。有差異 → 孤兒變數需補齊或標註原因。

檢查清單：
- [ ] 所有 env var 在 dev/staging/prod 都有定義（值可不同，key 必須一致）
- [ ] 無 dev-only 或 prod-only 的孤兒變數（除非有註解說明）
- [ ] 機敏值（secrets）不以 plaintext 存在 repo 中

---

## Step 4 — CONFIG_RECORDS.md 生成

使用 `templates/CONFIG_RECORDS.md`：

```markdown
# CONFIG_RECORDS.md — {Project Name}

## Environment Variables
| FR ID | Variable | Dev | Staging | Prod | Purpose |
|-------|----------|-----|---------|------|---------|

## Secrets
| FR ID | Secret Name | Storage | Rotation | Last Rotated |
|-------|------------|---------|----------|-------------|

## Feature Flags
| FR ID | Flag | Default | Override | Owner |

## External Dependencies
| FR ID | Service | Endpoint | SLA | Timeout | Retry |
```

---

## Step 5 — Gate 1 (per-FR)

```bash
python harness_cli.py run-gate --gate 1 --phase 8 --fr-id FR-001
# 3 dims: linting(100) / type_safety(100) / test_coverage(80)
```

---

## Step 6 — Phase Truth 計算

```bash
python harness_cli.py run-phase --phase 8
# Checks: Phase Truth ≥ 90% (HR-11)
#   - CONFIG_RECORDS.md 涵蓋所有 FR
#   - 無 hardcoded secrets
#   - env parity 確認完整
#   - sessions_spawn.log 記錄完整
```

---

## P8 Exit Checklist（最終管線退出）

- [ ] `CONFIG_RECORDS.md` 已生成，涵蓋所有 FR
- [ ] Secret leak scan 無 hardcoded secrets
- [ ] Env parity 確認（dev/staging/prod 一致）
- [ ] 每個 FR 的 Gate 1 通過
- [ ] Phase Truth ≥ 90% (HR-11)
- [ ] `sessions_spawn.log` 有每個 FR 的 A/B 記錄（HR-10）
- [ ] 全部 8 phase 的 HANDOVER.md 完整
- [ ] `python scripts/list-modules.py --validate` 通過
- [ ] `python scripts/validate_cross_refs.py` 通過
- [ ] `python -m pytest tests/ -v` 全部通過

---

## 管線完成

```bash
git add CONFIG_RECORDS.md .methodology/sessions_spawn.log HANDOVER.md
git commit -m "feat(P8): CONFIG_RECORDS.md complete — pipeline finished ({N} FRs)"
git tag "harness-v8-$(date +%Y%m%d)-done"
git push origin main --tags
```

---

## Agent A Dispatch Template (P8 — per FR)

Orchestrator: copy this when spawning Agent A for a specific FR.

```
[TASK]
Phase: 8 — Configuration Management | FR-ID: {fr_id} | Role: DEVOPS

SRS requirement:
> {paste FR-XX section from docs/SRS.md — embed, not file path}

SAD architecture context:
> {paste relevant module + dependency info from docs/SAD.md — embed}

Task:
1. Document configuration items for this FR (env vars, secrets, feature flags)
2. Verify env parity (dev/staging/prod consistency)
3. Check for secret leaks in code/config
4. Populate CONFIG_RECORDS.md entry for {fr_id}

Expected output:
- CONFIG_RECORDS.md row for {fr_id}
- JSON: {"status": "success", "files": ["CONFIG_RECORDS.md"],
         "confidence": N, "config_items": [...],
         "secrets_checked": N, "env_parity_ok": true|false,
         "citations": [...], "summary": "..."}
```

## Agent B Dispatch Template (P8 — per FR)

Orchestrator: copy this when spawning Agent B for a specific FR.

```
[TASK]
Phase: 8 — Configuration Management | FR-ID: {fr_id} | Role: ARCHITECT (reviewer)

SRS requirement:
> {paste FR-XX section from docs/SRS.md — embed, not file path}

Agent A config documentation:
> {paste CONFIG_RECORDS.md entry — embed, not file path}

Review criteria:
1. All configuration items documented? (env vars, secrets, flags)
2. Any hardcoded secrets in the code? (scan output)
3. Env parity confirmed across dev/staging/prod?
4. Configuration items traceable to FR requirements?

Expected output:
- JSON: {"status": "success", "review_status": "APPROVE|REJECT",
         "confidence": N, "violations": [...], "citations": [...],
         "summary": "..."}
```
