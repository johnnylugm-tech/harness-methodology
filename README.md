# harness-methodology

**8-Phase ASPICE AI Development Methodology + 4-Gate 12-Dimension Quality Harness**

Design Score: **92/100** Academic Benchmark

## Architecture

| Layer | Name | Description |
|-------|------|-------------|
| Part 1 | 乾淨版 (Clean) | 30+ ported modules from methodology-v2, adapted for Claude Code |
| Part 2 | 整合版 (Integrated) | 4-Gate quality harness embedded across 8 Phases |
| Part 3 | 優化版 (Optimized) | 6 structural gap fixes (G1–G6) |

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
| `SKILL.md` | Framework spec (HR rules, thresholds, phase SOPs) |
| `harness/harness_bridge.py` | Gate trigger + CRG integration |
| `harness/gate_configs/` | 4 Gate YAML configurations |
| `.methodology/enforcement.json` | Team constitution (configurable thresholds) |
| `core/agent_spawner.py` | Task tool + Hermes MCP reviewer routing |

## Quick Start

```bash
# Standalone harness CLI (no external dependencies beyond this repo)
export HERMES_REVIEWER_TARGET="telegram:YOUR_CHAT_ID"
python harness_cli.py plan-phase  --phase 3
python harness_cli.py run-phase   --phase 3
python harness_cli.py run-gate    --gate 2 --phase 3
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
*harness-methodology v2.0 | Academic Benchmark 92/100 | [SAD.md](SAD.md) | [INTEGRATION.md](INTEGRATION.md)*
