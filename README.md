# harness-methodology

**8-Phase ASPICE AI Development Methodology + 4-Gate 14-Dimension Quality Harness**

Design Score: **91/100** Academic Benchmark

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
| P3 exit | Gate 2 | 9 (Tier 1+2) | 75 | pre-fix only |
| P4 exit | Gate 3 | 14 (All Tiers) | 80 | full |
| P6 full | Gate 4 | 14 (All Tiers) | 85 | full |

## Key Files

| File | Purpose |
|------|---------|
| `SKILL.md` | Framework spec (YAML frontmatter + HR rules, thresholds, phase SOPs) |
| `CONTRIBUTING.md` | Maintainer SOP: module structure, versioning, release flow, troubleshooting |
| `harness_cli.py` | Standalone CLI entry point (plan-phase, run-gate, etc.) |
| `harness/harness_bridge.py` | Gate trigger + CRG integration |
| `harness/gate_configs/` | 4 Gate YAML configurations |
| `core/auto_fix/` | AutoFixEngine: classify → fix → verify → loop (13 strategies, 9 escalation conditions) |
| `scripts/list-modules.py` | Module manifest inventory + validation (`--validate` in CI) |
| `scripts/validate_cross_refs.py` | Cross-reference checker (CLASSIFICATION_TABLE ↔ STRATEGY_REGISTRY) |
| `.github/workflows/` | CI: `harness_ci.yml` (PR gate) + `release.yml` (tag-driven release) |

## Quick Start

### 分步執行（推薦）

人類僅需介入 2 次：提供 SRS.md (P1)、提供 SAD.md (P2)。
9 項嚴格的人類介入條件見 SAD.md §3.18。

步驟依序執行：

```bash
python harness_cli.py plan-phase  --phase 3
python harness_cli.py run-phase   --phase 3
python harness_cli.py run-gate    --gate 1 --phase 3 --fr-id FR-01 --auto-fix-rounds 3
python harness_cli.py finalize-gate --gate 1 --phase 3 --fr-id FR-01 --project .
python harness_cli.py run-gate    --gate 2 --phase 3 --auto-fix-rounds 3
python harness_cli.py finalize-gate --gate 2 --phase 3 --project .
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
final_score = tool_score   # LLM scoring abolished — all 14 dimensions are tool-scored
                           # score.py R4: score must equal tool_score (LLM cannot adjust)
                           # score.py R8: tool_score must not be null for any dimension
                           # CRG structural signals can flag issues but do not lower scores
```

Early-stop (Gates 2–4): PASS → CONTINUE (anti-pattern guard) → PLATEAU → BLOCKED

---
*harness-methodology v2.6.0 | Academic Benchmark 91/100 | [SAD.md](SAD.md) | [INTEGRATION.md](INTEGRATION.md)*
