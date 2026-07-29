# harness-methodology

**8-Phase ASPICE AI Development Methodology + 4-Gate 14-Dimension Quality Harness**

Design Score: **91/100** Academic Benchmark

## Supported Target Languages (v2.8.0+)

| Language | Detection | Lint | Types | Tests/Coverage | Security | Mutation | MI / docs / assertions / error-handling |
|---|---|---|---|---|---|---|---|
| python (default) | pyproject/setup.cfg | ruff | pyright | pytest + coverage | bandit | mutmut | framework `ast` scanners |
| typescript | tsconfig.json | eslint | `tsc --noEmit` | vitest or jest (json-summary) | semgrep (vendored rules) | StrykerJS | framework tree-sitter scanners |
| javascript | package.json | eslint | JSDoc + `tsc --checkJs` | vitest or jest (json-summary) | semgrep (vendored rules) | StrykerJS | framework tree-sitter scanners |

One language per project — `init-project` detects it (explicit `--language` on ambiguity)
and persists it to `.methodology/state.json`. JS/TS test titles MUST follow the
`it('test_frNN_xxx')` convention (D4 spec-coverage matches by name). Tool resolution
single source: `harness/toolchains/registry.py`. Adding a language:
[docs/ADDING_LANGUAGE_SUPPORT_SOP.md](docs/ADDING_LANGUAGE_SUPPORT_SOP.md).

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
| P4 exit | Gate 3 | 14 + adversarial_review | 80 | full |
| P6 full | Gate 4 | 14 (All Tiers) | 85 | full |

> **Adversarial Quality Layer (v2.9)**: Gate 3 adds `adversarial_review` — a
> framework-owned LLM bug-hunt verdict — plus static reliability/config
> preflights and architecture-risk test triggers. See
> [docs/ADVERSARIAL_QUALITY_LAYER.md](docs/ADVERSARIAL_QUALITY_LAYER.md).

## Key Files

| File | Purpose |
|------|---------|
| `SKILL.md` | Framework spec (YAML frontmatter + HR rules, thresholds, phase SOPs) |
| `CONTRIBUTING.md` | Maintainer SOP: module structure, versioning, release flow, troubleshooting |
| `harness_cli.py` | Standalone CLI entry point (plan-all, load-context, plan-phase, run-gate, etc.) |
| `harness/harness_bridge.py` | Gate trigger + CRG integration |
| `harness/gate_configs/` | 4 Gate YAML configurations |
| `core/auto_fix/` | AutoFixEngine: Partially retained (only trace gap fix is wired) + 5 guardrails |
| `scripts/list-modules.py` | Module manifest inventory + validation (`--validate` in CI) |
| `scripts/validate_cross_refs.py` | Cross-reference checker (CLASSIFICATION_TABLE ↔ STRATEGY_REGISTRY) |
| `.github/workflows/` | CI: `harness_ci.yml` (PR gate) + `release.yml` (tag-driven release) |

## Quick Start

### 1. Claude Code Workflow 啟動（主推薦模式）

本框架預設使用 **Claude Code Dynamic Workflow** 作為專案開發的主要控制驅動器（Primary Execution Driver）。透過 `.claude/workflows/` 下的 Workflow JS 腳本，實現全自動、動態流轉的開發管線。

#### 核心效益 (Why Workflows?)
* **確定性控制流 (Determinism)**：由 JavaScript 腳本精確控制 phase 推進、內容載入與門禁檢查，確保 LLM Agent 嚴格遵守規範，不會因 Prompt 偏差而遺漏關鍵審核或跳過階段。
* **全自動動態流轉 (Dynamic Workflow)**：`run-all.js` 會根據 `.methodology/state.json` 自動讀取狀態，實現自 Phase 1 至 Phase 8 的無縫自動推進。
* **零人類介入與極致品質 (Zero Human Intervention & 98.5 Benchmark)**：在 Workflow 模式下（如 `run-all-by-workflow` 實證），開發過程可實現 **全流程零人類介入 (0 次介入)**，從 Phase 1 到 Phase 8 自動貫穿，且最終軟體品質評分達到 **98.5/100** 的極致水準。僅在極端例外狀況下觸發 [SAD.md](SAD.md) §3.18 的 9 項升級條件時才需人工介入。
* **零指令差錯與標準化**：封裝自動載入 Context、觸發 Preflight、派發 Agent A (Developer) / Agent B (Reviewer)、品質門禁與 Milestone Push，完全免除手動敲下數十條 CLI 指令的記憶負擔與誤操作。

#### 啟動方式

```bash
# 一鍵全自動執行 Phase 1 ~ Phase 8
claude -p "run workflow .claude/workflows/run-all.js"

# 或針對特定單一 Phase 進行流轉 (例如 Phase 3)
claude -p "run workflow .claude/workflows/phase3-implementation.js"
```

---

### 2. 手動 / Phase Plan CLI 分步執行（傳統 / 除錯備用模式）

當你需要手動單步除錯、無 Workflow 環境、或針對特定 FR / Phase 進行單步調試時，可保留採用 `harness_cli.py` 手動指令流：

```bash
# 1. 專案初始化與動態 Plan 生成 (僅需一次)
python harness_cli.py init-project --project . --phase 1
python harness_cli.py plan-all --project .

# 2. 手動單階段執行 (以 Phase 3 為例)
python harness_cli.py load-context --phase 3 --project . --json > .sessi-work/phase3_ctx.json
python harness_cli.py run-phase   --phase 3
python harness_cli.py run-gate    --gate 1 --phase 3 --fr-id FR-01
python harness_cli.py finalize-gate --gate 1 --phase 3 --fr-id FR-01 --project .
python harness_cli.py run-gate    --gate 2 --phase 3
python harness_cli.py finalize-gate --gate 2 --phase 3 --project .
python harness_cli.py audit-phase   --phase 3 --project .
python harness_cli.py push-milestone --type p3-pre-gate2 --project .
python harness_cli.py status
```

> `cli.py` is the full parent-system entry point (requires 30+ external modules). Use `harness_cli.py` for standalone harness operations.

## Integrate Into Your Project

See **[INTEGRATION.md](INTEGRATION.md)** for:
- Git hooks setup (`scripts/setup-git-hooks.sh`)
- Recommended target-project GitHub Actions
- Environment variables reference

## Server-Side Enforcement (Bypass-Proof)

The methodology uses three CI-level enforcement mechanisms to prevent hook bypassing (`git push --no-verify`):
1. **Push-milestone sentinel**: Blocks pushes if `push-milestone` isn't called before Phase 3+ changes.
2. **Agent B approval gate**: Blocks pushes if FRs lack approval or traceable citations.
3. **P8 archive check**: Blocks pushes if Phase 8 `HANDOVER.md` references non-existent phases.

Test coverage is centrally tracked via `TEST_SPEC.md` and enforced using `spec-coverage-check`.

## Score Reconciliation

```
final_score = tool_score   # LLM scoring abolished — all 14 dimensions are tool-scored
                           # score.py R4: score must equal tool_score (LLM cannot adjust)
                           # score.py R8: tool_score must not be null for any dimension
                           # CRG structural signals can flag issues but do not lower scores
```

Early-stop (Gates 2–4): PASS → CONTINUE (anti-pattern guard) → PLATEAU → BLOCKED

---
*harness-methodology v2.12.0 | Academic Benchmark 91/100 | [SAD.md](SAD.md) | [INTEGRATION.md](INTEGRATION.md) | [docs/CONFIGURATION.md](docs/CONFIGURATION.md)*
