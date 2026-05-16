# harness-methodology

**8-Phase ASPICE AI Development Methodology + 4-Gate 12-Dimension Quality Harness**

Design Score: **92/100** Academic Benchmark

## Architecture

| Layer | Name | Description |
|-------|------|-------------|
| Part 1 | 乾淨版 (Clean) | 30+ ported modules from methodology-v2, adapted for Claude Code |
| Part 2 | 整合版 (Integrated) | 4-Gate quality harness + constitution HR enforcement across 8 Phases |
| Part 3 | 優化版 (Optimized) | 6 structural gap fixes (G1–G6) + constitution/ module (bvs_runner, citation_parser, verification_constitution_checker) |

## 8-Phase Gate Map

| Phase | Gate | Dims | score_gate | CRG |
|-------|------|------|------------|-----|
| P3 per-FR | Gate 1 | 3 (Tier 1) | — (per-dim) | ✗ |
| P3 exit | Gate 2 | 7 (Tier 1+2) | 75 | pre-fix only |
| P4 exit | Gate 3 | 12 (All Tiers) | 80 | full |
| P6 full | Gate 4 | 12 (All Tiers) | 85 | full |

## Key Files

| File | Purpose |
|------|---------|
| `SKILL.md` | Framework spec (YAML frontmatter + HR rules, thresholds, phase SOPs) |
| `CONTRIBUTING.md` | Maintainer SOP: module structure, versioning, release flow, troubleshooting |
| `harness_cli.py` | Standalone CLI entry point (plan-phase, run-gate, run-pipeline, etc.) |
| `harness/harness_bridge.py` | Gate trigger + CRG integration |
| `harness/gate_configs/` | 4 Gate YAML configurations |
| `core/auto_fix/` | AutoFixEngine: classify → fix → verify → loop (13 strategies, 9 escalation conditions) |
| `scripts/list-modules.py` | Module manifest inventory + validation (`--validate` in CI) |
| `scripts/validate_cross_refs.py` | Cross-reference checker (CLASSIFICATION_TABLE ↔ STRATEGY_REGISTRY) |
| `.github/workflows/` | CI: `harness_ci.yml` (PR gate) + `release.yml` (tag-driven release) |

## Quick Start

### 全自主模式（推薦）— 一次啟動，P1→P8 自動執行

```bash
export HERMES_REVIEWER_TARGET="telegram:YOUR_CHAT_ID"

# 全管道（P3+ 計劃在 P2 產出 SAD.md 後動態生成）
python harness_cli.py run-pipeline \
  --phase-from 1 --phase-to 8 \
  --project /path/to/project \
  --auto-fix-rounds 3

# Gate blocked 或 SRS/SAD 缺少時 exit 10 → 修復後接續：
python harness_cli.py run-pipeline --phase-from N --project /path/to/project
```

人類僅需介入 3 次：提供 SRS.md (P1)、提供 SAD.md (P2)、Gate 4 Telegram APPROVE。
其餘所有品質問題由 AutoFixEngine 自動修復（最多 `--auto-fix-rounds` 輪，預設 3）。
9 項嚴格的人類介入條件見 SAD.md §3.18。

### 手動分步

```bash
python harness_cli.py plan-phase  --phase 3
python harness_cli.py run-phase   --phase 3
python harness_cli.py run-gate    --gate 1 --phase 3 --fr-id FR-01 --auto-fix-rounds 3
python harness_cli.py run-gate    --gate 2 --phase 3 --auto-fix-rounds 3
python harness_cli.py status
```

> `cli.py` is the full parent-system entry point (requires 30+ external modules). Use `harness_cli.py` for standalone harness operations.

## Integrate Into Your Project

See **[INTEGRATION.md](INTEGRATION.md)** for:
- Git hooks setup (`scripts/setup-git-hooks.sh`)
- Drift monitor cron (`scripts/cron_drift_monitor.py`)
- Recommended target-project GitHub Actions
- Environment variables reference

## Score Reconciliation

```
final_score = min(tool_score, llm_score)   # CRG can only lower, never raise
```

Early-stop (Gates 2–4): PASS → CONTINUE (anti-pattern guard) → PLATEAU → BLOCKED

---
*harness-methodology v2.4 | Academic Benchmark 92/100 | [SAD.md](SAD.md) | [INTEGRATION.md](INTEGRATION.md)*
