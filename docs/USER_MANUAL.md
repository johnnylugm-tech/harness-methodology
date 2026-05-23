# Harness Methodology — User Manual v2.4

> **Audience**: Engineers using Claude (AI agent) + harness-methodology to execute software development projects.
> **Framework version**: v2.4 | **Document date**: 2026-05-11

---

## Table of Contents

1. [Overview](#1-overview)
2. [Prerequisites & Setup](#2-prerequisites--setup)
3. [Core Concepts](#3-core-concepts)
4. [Basic Flow — Happy Path (P1 → P8)](#4-basic-flow--happy-path-p1--p8)
5. [Phase-by-Phase Guide](#5-phase-by-phase-guide)
6. [Quality Gate Reference](#6-quality-gate-reference)
7. [Alternative Flows](#7-alternative-flows)
8. [Interactive Conversation Patterns](#8-interactive-conversation-patterns)
   - [8.8 全自主模式（推薦）](#88-全自主模式推薦)
9. [CLI Reference](#9-cli-reference)
10. [Environment Variables](#10-environment-variables)
11. [Troubleshooting](#11-troubleshooting)
12. [GitHub Integration Setup](#12-github-integration-setup)

---

## 1. Overview

Harness Methodology is an **8-phase quality-gated software development framework**. You work through each phase by:

1. **Generating a plan** — the harness parses your SRS/SAD artifacts and produces a checklist
2. **Executing with Claude** — you describe tasks to Claude; developer + reviewer agents do the work
3. **Running quality gates** — automated scoring blocks advancement until standards are met
4. **Advancing** — once gates pass, move to the next phase

```
P1 Requirements → P2 Architecture → P3 Implementation → P4 Testing
→ P5 Verification → P6 Quality Assurance → P7 Risk → P8 Config
         ↑                    ↑                  ↑             ↑
       [pre-flight]        [Gate 1]           [Gate 2/3]    [Gate 4]
```

**What the harness does for you:**
- Pre-flight checks before each phase (FSM state, constitution compliance)
- Per-FR quality checks during implementation (Gate 1)
- Phase-exit quality gates that block advancement until score thresholds are met
- Audit trail: every gate decision logged to `.methodology/decision_logs/`
- Effort tracking in SQLite (`.methodology/effort_metrics.db`)

---

## 2. Prerequisites & Setup

### 2.1 Required

```bash
# Python 3.10+
python3 --version

# PyYAML (for gate config loading)
pip install pyyaml

# Clone the repo
git clone https://github.com/johnnylugm-tech/harness-methodology.git
cd harness-methodology
```

### 2.2 Optional but Recommended

```bash
# SSI is embedded at harness/ssi/ — no external install needed for gate runs

# Hermes MCP — optional, used for Agent B A/B reviews
export HERMES_REVIEWER_TARGET=telegram:YOUR_CHAT_ID   # or other target

# CRG (Code Review Graph) — optional, enhances Gate 3/4 scoring
# Gracefully skipped if not installed

# Git hooks (target-project integration) — run ONCE in your target project root:
bash harness/scripts/harness-init.sh --phase 1   # recommended: idempotent, CI-embeddable
# After this: git commit / push trigger harness checks automatically
```

> **Three init entry points** (from lowest to highest level):
> - `setup-git-hooks.sh` — installs git hooks only, interactive prompts for phase
> - `harness-init.sh --phase N` — idempotent superset: hooks + state.json + CI YAML; safe to re-run
> - `harness_cli.py init-project` — upper-layer orchestrator: checks harness importability, generates CI workflow, calls `setup-git-hooks.sh`, initializes `.methodology/state.json`
>
> If you ran `setup-git-hooks.sh` earlier, running `harness-init.sh` is safe — it detects already-installed hooks and skips them.

### 2.3 Project Directory Structure

Your project (the codebase being built) should have this layout:

```
your-project/
  SRS.md                    ← Phase 1 output (required for plan-phase P2+)
  SAD.md                    ← Phase 2 output (required for plan-phase P3+)
  .methodology/
    state.json              ← FSM state (auto-created)
    quality_manifest.json   ← Gate results (created at P2 exit)
    decision_logs/          ← Per-gate YAML audit entries
    effort_metrics.db       ← SQLite effort tracking
    hooks.json              ← Optional lifecycle hooks (v2.4+)
    hooks.log               ← Hook execution log (auto-created, v2.4+)
    workspaces/             ← Per-FR isolation workspaces (auto-created, v2.4+)
  .sessi-work/              ← SSI gate workspace (auto-created)
```

> **Note**: `harness_cli.py` refers to the *project root* with `--project` or `--repo`.
> The harness-methodology repo itself is the *framework* — keep them separate.

### 2.4 GitHub Integration — Invisible Mode

> **TL;DR**: One-time setup → harness runs in the background forever. Developers just use `git` normally — no extra commands needed.

```
One-time setup                      Automatic thereafter
────────────────────────────────────────────────────────────
setup-git-hooks.sh           →  hooks fire on every commit / merge / push
Add CI YAML to target repo   →  gate check on every push (blocks if fail)
Set DRIFT_PROJECT_PATH       →  drift alert every hour (log / email / Slack)
```

| Trigger | Mechanism | Blocking? |
|---|---|---|
| `git commit` | `prepare-commit-msg` hook — phase-aware gate check | ✅ blocks commit |
| `git merge` | `post-merge` hook — drift detection | ❌ log only |
| `git push` | `pre-push` hook — full phase gate | ✅ blocks push |
| git push (CI) | `gate-check` workflow | ✅ blocks bad push |

> **Full setup guide**: [§12 GitHub Integration Setup](#12-github-integration-setup) | [INTEGRATION.md](../INTEGRATION.md)

---

## 3. Core Concepts

### 3.1 Phases (P1–P8)

| Phase | Name | Key Output | Gates |
|---|---|---|---|
| P1 | Requirements Specification | `SRS.md` | — |
| P2 | Architecture Design | `SAD.md`, `quality_manifest.json` | — |
| P3 | Implementation | Code + unit tests | Gate 1 (per-FR), Gate 2 (exit) |
| P4 | Testing | Test plan + results | Gate 1 (per-FR), Gate 3 (exit) |
| P5 | Verification & Delivery | Baseline, monitoring plan | Gate 1 (per-FR) |
| P6 | Quality Assurance | Quality report | Gate 4 (exit, full project) |
| P7 | Risk Management | Risk register | Gate 1 (per-FR) |
| P8 | Configuration Management | Config records | Gate 1 (per-FR) |

### 3.2 Functional Requirements (FRs)

Each phase works on a list of FRs (e.g. `FR-01`, `FR-02`). Each FR is an atomic unit of work:
- Developer agent builds/tests it in an **isolated workspace** (`.methodology/workspaces/phase_{N}/FR-XX/`)
- Reviewer agent reviews it (stateless, embed content in prompt)
- Gate 1 checks it before marking it done
- Workspace is cleaned up after Gate 1 passes

### 3.3 Quality Gates

| Gate | When | Scope | Blocking threshold |
|---|---|---|---|
| Gate 1 | Per-FR at P3/P4/P5/P7/P8 | Single FR | Per-dimension (linting≥90, type≥85, coverage≥80) |
| Gate 2 | P3 exit | Full phase | Composite score ≥ 75 |
| Gate 3 | P4 exit | Full phase | Composite score ≥ 80, all 14 dims |
| Gate 4 | P6 exit | Full project | Composite score ≥ 85 |

> **Normative reference**: Gate pass criteria are **MUST**-level requirements per RFC 2119. See [SAD.md §2.4 Conformance Matrix](../SAD.md#24-conformance-matrix-rfc-2119) for the full conformance specification.

### 3.4 FSM States

The framework tracks project state in `.methodology/state.json`:

```
INITIAL → ACTIVE → (phase-by-phase) → COMPLETE
                ↓
              PAUSED   ← manual pause or gate block
                ↓
              FREEZE   ← kill switch or critical violation
```

---

## 4. Basic Flow — Happy Path (P1 → P8)

```
┌─────────────────────────────────────────────────────────────┐
│  SETUP                                                      │
│  1. harness_cli.py manifest --fr-ids FR-01 FR-02 --sad SAD.md  │  ← P2 exit
└─────────────────────────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────────────────────────┐
│  EACH PHASE (repeat for P3–P8)                             │
│  1. plan-phase  --phase N --project /project                  │
│  2. [Claude] Execute phase tasks per plan                  │
│  3. run-phase   --phase N --project /project               │  ← pre-flight
│  4. [Claude] Per-FR dev + review loop                      │
│  5. run-gate    --gate 1 --phase N --fr-id FR-XX           │  ← per FR (Gates trigger)
│  6. run-gate    --gate N+1 --phase N                       │  ← phase exit gate
│  7. status      --project /project                         │  ← confirm
└─────────────────────────────────────────────────────────────┘
```

**Condensed happy-path command sequence** for P3:

```bash
# 1. Generate plan
python harness_cli.py plan-phase --phase 3 --project /project --output /project/phase3_plan.md

# 2. Pre-flight check
python harness_cli.py run-phase --phase 3 --project /project

# 3a. Per-FR gate after each FR is done
python harness_cli.py run-gate --gate 1 --phase 3 --project /project --fr-id FR-01
python harness_cli.py run-gate --gate 1 --phase 3 --project /project --fr-id FR-02

# 4. Phase exit gate
python harness_cli.py run-gate --gate 2 --phase 3 --project /project

# 5. Check status
python harness_cli.py status --project /project
```

---

## 5. Phase-by-Phase Guide

### Phase 1 — Requirements Specification

**Goal**: Produce `SRS.md` covering all FRs and NFRs.

**Interactive prompt to Claude**:
```
我們開始 Phase 1：需求規格。

專案描述：[你的專案描述]

請：
1. 訪談我以確認所有功能需求（FR）
2. 識別非功能需求（NFR）
3. 輸出完整的 SRS.md，格式包含 ### FR-01: [名稱] 與 ### NFR-01: [名稱]
```

**Output**: `SRS.md` in project root.

**harness command**:
```bash
python harness_cli.py plan-phase --phase 1 --project /project
# (No gate for P1 — output is purely documentary)
```

---

### Phase 2 — Architecture Design

**Goal**: Produce `SAD.md` + initialize `quality_manifest.json`.

**Interactive prompt to Claude**:
```
我們進入 Phase 2：架構設計。

已有：SRS.md（見附件或 /project/SRS.md）

請：
1. 設計系統架構（模組劃分、資料流、技術選型）
2. 輸出 SAD.md
3. 列出本專案的 FR ID 清單（用於生成 quality manifest）
```

**After Claude produces SAD.md**, initialize the manifest:
```bash
python harness_cli.py manifest \
  --fr-ids FR-01 FR-02 FR-03 \
  --sad /project/SAD.md
```

**Expected output**:
```
quality_manifest.json written → /project/.methodology/quality_manifest.json
  fr_ids        : ['FR-01', 'FR-02', 'FR-03']
  generated_at  : phase 2
```

---

### Phase 3 — Implementation

**Goal**: Implement all FRs with unit tests. Gate 1 per FR, Gate 2 at exit.

**Interactive prompt to Claude**:
```
Phase 3：實作。

已有：SRS.md、SAD.md
FR 清單：FR-01, FR-02, FR-03

請對每個 FR 依序執行：
1. [Developer] 實作 FR-XX 模組（含單元測試）
2. [Reviewer] 審查 FR-XX 實作
3. 告知我審查結果後，我會執行 Gate 1

從 FR-01 開始。
```

**After each FR is reviewed**, run Gate 1:
```bash
python harness_cli.py run-gate --gate 1 --phase 3 --project /project --fr-id FR-01
```

**After all FRs pass Gate 1**, run Gate 2 (phase exit):
```bash
python harness_cli.py run-gate --gate 2 --phase 3 --project /project
```

---

### Phase 4 — Testing

**Goal**: Comprehensive test plan + execution. Gate 1 per FR, Gate 3 at exit.

**Interactive prompt to Claude**:
```
Phase 4：測試。

請：
1. 制定完整測試計畫（TEST_PLAN.md）：單元、整合、效能、安全測試
2. 執行所有測試並記錄結果（TEST_RESULTS.md）
3. 確保每個 FR 的覆蓋率符合要求（≥80%）

對每個 FR 完成後告知我，我會執行 Gate 1 確認。
```

```bash
# Per-FR gate
python harness_cli.py run-gate --gate 1 --phase 4 --project /project --fr-id FR-01

# Phase exit gate (14 dimensions including architecture + error_handling + integration_coverage + test_assertion_quality)
python harness_cli.py run-gate --gate 3 --phase 4 --project /project
```

---

### Phase 5 — Verification & Delivery

**Goal**: System-level verification. No gate exit, but Gate 1 applies to FRs.

**Interactive prompt to Claude**:
```
Phase 5：驗證與交付。

請：
1. 對照 SRS.md 逐一驗證所有 FR 是否完整交付
2. 建立系統 baseline（BASELINE.md）
3. 輸出驗證報告（VERIFICATION_REPORT.md）
```

```bash
python harness_cli.py run-phase --phase 5 --project /project
```

---

### Phase 6 — Quality Assurance

**Goal**: Final full-project quality check. Gate 4 (score≥85).

**A/B Roles**: No A/B roles at Phase level (replaced by automated Phase End Audit)

**Interactive prompt to Claude**:
```
Phase 6：品質保證。

請：
1. 執行全面品質審查（所有14個維度）
2. 輸出 QUALITY_REPORT.md
3. 準備 RELEASE_NOTES.md 與 FINAL_SIGN_OFF.md
```

**Run Gate 4** (requires SSI):
```bash
python harness_cli.py run-gate --gate 4 --phase 6 --project /project
# → Runs SSI evaluation (14 dims, score ≥ 85)
# → mutation_testing: objective_primary=true
# → Runs finalize-gate to check threshold:
python harness_cli.py finalize-gate --gate 4 --phase 6 --project /project
```

---

### Phase 7 — Risk Management

**Goal**: Risk register + mitigation plans. Gate 1 per FR.

**Interactive prompt to Claude**:
```
Phase 7：風險管理。

請：
1. 識別所有技術、進度、資源、外部風險
2. 建立 RISK_REGISTER.md
3. 為每個高風險項制定緩解計畫
4. 使用 Claude（非 Hermes）進行審查（Phase 7 自動路由至 Claude）
```

```bash
python harness_cli.py run-gate --gate 1 --phase 7 --project /project --fr-id FR-RISK-01
```

> **Note**: Phase 7 and 8 automatically route to Claude reviewer (not Hermes). This is enforced by `get_reviewer_model(phase=7)` returning `"claude"`.

---

### Phase 8 — Configuration Management

**Goal**: Complete configuration records. Gate 1 per config item.

**Interactive prompt to Claude**:
```
Phase 8：組態管理。

請：
1. 記錄所有環境、部署、安全、監控設定（CONFIG_RECORDS.md）
2. 建立發布檢查清單（RELEASE_CHECKLIST.md）
```

```bash
python harness_cli.py run-gate --gate 1 --phase 8 --project /project --fr-id FR-CFG-01
python harness_cli.py effort --project /project   # review total effort
```

---

## 6. Quality Gate Reference

### Gate 1 — Per-FR Lightweight Check

| Dimension | Tier | Model | Threshold | Weight |
|---|---|---|---|---|
| linting | 1 | gemini-flash | 90 | 0.33 |
| type_safety | 1 | gemini-flash | 85 | 0.33 |
| test_coverage | 1 | gemini-flash | 80 | 0.34 |

- Blocking: **per-dimension** (not composite score)
- On fail: fix the specific dimension and re-run Gate 1 for that FR
- Max rounds: 1 (no auto-iteration)

### Gate 2 — P3 Phase Exit

| Score threshold | 75 | Dimensions | 10 |
|---|---|---|---|
| Max rounds | 3 | Saturation rounds | 3 |
| CRG | impact_check | mutation_testing | median_runs=3, objective_primary=true |
| New dims | D4_TestInventory (pre-check ≥ 60%) | integration_coverage (w=0.10) | test_assertion_quality (w=0.06) |

### Gate 3 — P4 Phase Exit

| Score threshold | 80 | Dimensions | 15 (full) |
|---|---|---|---|
| Max rounds | 3 | CRG | full recon + tier3_guidance |
| New dims | integration_coverage (w=0.05) | test_assertion_quality (w=0.02) | — |

### Gate 4 — Full Project (P6 exit)

| Score threshold | 85 | Dimensions | 15 (full) |
|---|---|---|---|
| Max rounds | 3 | Human review | None (Fully automated) |
| New dims | integration_coverage (w=0.05) | test_assertion_quality (w=0.02) | — |

---

## 7. Alternative Flows

### AF-01 — Gate Blocked → Fix → Re-run

```
run-gate → GateBlockedError
  │
  ├─ Read gate output: which dimensions failed?
  │    [FAIL] linting: 82.0 (threshold=90)
  │
  ├─ Tell Claude:
  │    "Gate 1 blocked for FR-01: linting score 82 (need ≥90).
  │     Please fix all linting issues in FR-01 module and re-run."
  │
  ├─ Claude fixes → confirm
  │
  └─ Re-run:
       python harness_cli.py run-gate --gate 1 --phase 3 --fr-id FR-01
```

### AF-02 — Preflight Check Failed

```
run-phase → "PRE-FLIGHT FAILED"
  │
  ├─ Common causes:
  │    • state.json shows FREEZE or PAUSED
  │    • constitution violations in docs/
  │    • Phase regression (trying to run P5 when state shows P3)
  │
  ├─ Diagnose:
  │    python harness_cli.py status --project /project
  │
  ├─ Skip preflight for run-gate (v2.4+):
  │    python harness_cli.py run-gate --gate N --phase N --project /project --skip-preflight
  │
  └─ Fix constitution violations:
       Tell Claude: "Pre-flight constitution check failed. Review docs/ for
       HR violations and fix them before I re-run."
```

### AF-04 — SSI Runner Not Installed

```
run-gate → "[ERROR] Install software_self_improvement..."
  │
  ├─ Install SSI:
  │    pip install -e /path/to/software_self_improvement
  │    # or: pip install software_self_improvement
  │
  ├─ Verify:
  │    python3 -c "import software_self_improvement"
  │
  └─ Re-run gate
```

### AF-05 — HR-12 Iteration Limit Hit

HR-12 states: no more than 5 ineffective review iterations per FR.

```
PhaseHooks.monitoring_hr12_check → returns False (iteration ≥ 5)
  │
  ├─ This means: the developer-reviewer loop ran 5+ times without convergence
  │
  ├─ Tell Claude:
  │    "FR-XX has exceeded 5 review iterations without passing.
  │     Please step back and identify the root cause rather than incremental fixes.
  │     Propose a fundamental redesign of this FR implementation."
  │
  └─ After redesign: restart FR from monitoring_before_dev
```

### AF-06 — Kill Switch Triggered

```
KillSwitch.evaluate_and_trigger → circuit OPEN for agent_id
  │
  ├─ Agent is halted (is_agent_circuit_open returns True)
  │
  ├─ Diagnose:
  │    from kill_switch import KillSwitch
  │    ks = KillSwitch()
  │    history = ks.get_interrupt_history(agent_id="AGENT_X")
  │
  ├─ Manual re-enable (after acknowledging cause):
  │    ks.re_enable("AGENT_X", operator_id="HUMAN", acknowledgment="Root cause fixed: ...")
  │
  └─ Resume phase
```

### AF-07 — Phase Rollback (State Regression)

```
You need to re-run a phase that was already marked complete.
  │
  ├─ Edit .methodology/state.json:
  │    {"state": "ACTIVE", "current_phase": N-1, "last_update": "..."}
  │
  ├─ Re-run from that phase:
  │    python harness_cli.py plan-phase --phase N --project /project
  │    python harness_cli.py run-phase --phase N --project /project
  │
  └─ Note: gate results are preserved in quality_manifest.json
       If you want to reset gate results, edit gate_results in the manifest
```

### AF-08 — Plan Not Accurate (SRS Parsing Failed)

```
plan-phase outputs empty or incorrect tasks
  │
  ├─ Check SRS.md format:
  │    Must have: ### FR-01: [title] sections
  │    Must have: ### NFR-01: [title] sections
  │
  ├─ Check repo path:
  │    python harness_cli.py plan-phase --phase 3 --project /path/to/PROJECT
  │    (not harness-methodology repo itself)
  │
  └─ Fallback: use plan as a starting point and supplement with manual tasks
       Tell Claude: "Here's the generated plan. Please also check SRS.md
       directly for any FR I may have missed."
```

---

## 8. Interactive Conversation Patterns

### 8.1 Starting a New Project

```
使用者：
我要開始一個新的軟體專案，使用 harness-methodology 框架。
專案描述：[描述]
技術棧：[Python/TypeScript/etc]

請從 Phase 1 開始，幫我完成需求訪談並輸出 SRS.md。

Claude 會：
- 訪談需求
- 識別 FR 和 NFR
- 輸出完整 SRS.md
```

### 8.2 Phase 執行提示模板

```
使用者：
我們進入 Phase [N]：[相名稱]。

相關檔案：
- SRS.md：[path 或 paste]
- SAD.md：[path 或 paste（若 P3+）]

已生成的計畫（plan-phase 輸出）：
[貼上 plan-phase 輸出]

FR 清單：FR-01, FR-02, FR-03

請按照計畫依序執行，每完成一個 FR 請報告，等我確認後繼續。
```

### 8.3 Gate 失敗後的修復對話

```
使用者：
Gate [N] 失敗，以下是輸出：

GATE 2 BLOCKED
  score: 71.3 (need ≥75)
  [FAIL] mutation_testing: 65.0 (threshold=70)
  [FAIL] security: 72.0 (threshold=80)

請針對失敗維度進行修復：
1. mutation_testing：增加 mutation test 覆蓋率
2. security：修復以下安全問題

修復後告知我，我會重新執行 Gate 2。
```

### 8.4 查看進度

```
使用者：
顯示目前專案進度。

# 然後執行：
python harness_cli.py status --project /project
python harness_cli.py effort --project /project

# 將輸出貼給 Claude：
"目前狀態如下，請分析進度並建議下一步："
[paste output]
```

### 8.5 多 FR 並行處理

```
使用者：
我有 5 個 FR（FR-01 到 FR-05），它們彼此獨立。
請設計並行處理策略，同時開發 2-3 個 FR，但每個 FR 完成後我需要先執行 Gate 1 再繼續。

Claude 策略：
- FR-01 + FR-02 並行開發 → Gate 1 x2 → FR-03 + FR-04 並行 → Gate 1 x2 → FR-05 → Gate 1
- Phase exit: Gate 2
```

### 8.6 僅使用 harness 工具（不運行 SSI）

```
使用者：
我目前沒有安裝 SSI，但想使用 harness 的計畫和狀態功能。

可用功能（無需 SSI）：
✅ plan-phase — 生成執行計畫
✅ run-phase  — 執行 pre/post-flight hooks（constitution 檢查）
✅ manifest   — 初始化 quality manifest（不含 gate 結果）
✅ status     — 查看 FSM 狀態
✅ effort     — 查看時間追蹤

❌ run-gate   — 需要 SSI（會顯示安裝提示）
```

### 8.7 提供 GitHub Repo — 自動套用 Harness（無感模式）

**目標**：只要給 Claude 一個 GitHub repo URL，harness 自動套用，開發者日常工作流程完全不受影響。

```
使用者：
這是我的目標專案：https://github.com/myorg/myproject
目前在 Phase 3（實作階段）。
請幫我套用 harness-methodology 的 GitHub 整合。

Claude 會依序確認並執行：
1. harness 可 import（submodule / clone+PYTHONPATH / 直接複製）
2. setup-git-hooks.sh 安裝 hooks 到目標專案 .git/hooks/
3. .methodology/state.json 初始化（current_phase = 3）
4. 建立 CI workflow（.github/workflows/harness_quality_gate.yml）
5. 確認 DRIFT_PROJECT_PATH（可選）
```

**設定完成後的開發者體驗（完全透明）：**
```
✅ git commit  → 自動 phase check（不通過則阻擋 + 提示修復指令）
✅ git push    → 自動 pre-push gate + CI gate-check（不通過則 push 被擋下）
✅ 每小時      → drift monitor 自動偵測架構偏移
❌ 不需要手動執行任何 harness 指令於日常開發
```

**若 Claude 無本地存取（pure chat mode）**，提供一次性指令：
```bash
cd /your/target/project
git submodule add https://github.com/johnnylugm-tech/harness-methodology harness
bash harness/scripts/harness-init.sh --phase 1   # idempotent — safe to re-run
```

---

### 8.8 全自主模式（推薦）

**一次 prompt，Claude 自主完成 P1→P8。人類只需介入 3 個時間點。**

#### 啟動 prompt（使用者給 Claude 一次）

```
建一個 [專案描述]。
技術棧：Python 3.11
Repo：/path/to/project

請使用 harness-methodology 全自主執行 P1→P8。
Gate 4 若分數未達自動核准門檻，我會在 Telegram 收到通知並 APPROVE；其餘全自動。
```

#### Claude 的執行策略（分步執行）

> **關鍵設計**：P3+ 的計劃（FR 清單）從 P2 產出的 `SAD.md` 動態讀取。
> `run-pipeline` 已被 v2.5 移除（當前成熟度不穩定），全面採用分步執行。

```bash
# 每 Phase 開始先產計劃（P3+ 必須等 SAD.md 存在）
python harness_cli.py plan-phase --phase $N --project $PROJECT

# Preflight
python harness_cli.py run-phase --phase $N --project $PROJECT

# 每 FR Gate 1（FR IDs 自動從 quality_manifest.json 讀取）
python harness_cli.py run-gate --gate 1 --phase $N \
  --project $PROJECT --fr-id FR-01 --auto-fix-rounds 3

# Phase exit gate
python harness_cli.py run-gate --gate 2 --phase 3 \
  --project $PROJECT --auto-fix-rounds 3

# 確認狀態
python harness_cli.py status --project $PROJECT
```

#### 人類介入點（僅 2 個）

| 時機 | 觸發條件 | 行動 | 預計時間 |
|------|---------|------|---------|
| P1 需求 | SRS.md 不存在，pipeline exit 10 | 提供 `SRS.md`（含 `### FR-XX:` 段落） | ~5 min |
| P2 架構 | SAD.md 不存在，pipeline exit 10 | 提供 `SAD.md`（含 FR ID） | ~10 min |

> **Gate block 自動處理**：`--auto-fix-rounds 3` 讓 SSI 內部自行修復最多 3 輪。
> 3 輪後仍 BLOCKED → pipeline exit 10，Claude 報告根因，等待人類指示後 `--phase-from N` 重跑。

#### Pipeline 退出碼

| Exit Code | 含義 | 行動 |
|-----------|------|------|
| `0` | 所有 phase 完成 | Done ✓ |
| `1` | 硬錯誤（SSI 未安裝、manifest 遺失等） | 診斷錯誤訊息 |
| `10` | PAUSE — 需人類介入 | 修復後 `--phase-from N` 重跑 |
| `11` | Phase Truth < 90%（HR-11 驗證失敗） | 修復品質問題後 `--phase-from N` 重跑 |

---

## 9. CLI Reference

### `plan-phase` — 生成 Phase 執行計畫

```bash
python harness_cli.py plan-phase \
  --phase  3          \  # Phase 編號 1-8（必填）
  --repo   /project   \  # 專案路徑（預設：.）
  --output plan.md       # 輸出路徑（省略則 stdout）
```

**輸入**：讀取 `--repo` 下的 `SRS.md`、`SAD.md`、`TEST_PLAN.md` 等  
**輸出**：Markdown 格式的任務清單，含每個 FR 的詳細任務  
**依賴**：純 stdlib，無外部套件

---

### `run-phase` — 執行 Phase Hooks（Pre/Post-flight）

```bash
python harness_cli.py run-phase \
  --phase   3          \  # Phase 編號（必填）
  --project /project      # 專案路徑（預設：.）
```

**Pre-flight 檢查**：
- FSM 狀態（不在 FREEZE/PAUSED）
- Constitution 合規性（`docs/` 目錄）
- Tool Registry 可用性（若已安裝）
- Drift / Traceability / Gap analysis（完整強制執行）

**返回碼**：0=pre-flight通過；1=失敗（修復問題後重跑）

### `pre-commit-check` — Git Commit Hook 輕量檢查

```bash
python harness_cli.py pre-commit-check \
  --phase   3          \  # Phase 編號（必填）
  --project /project      # 專案路徑（預設：.）
```

**用途**：git commit hook 專用（`prepare-commit-msg` / `post-merge`）。只跑 FSM + Constitution + Kill-switch，跳過 drift/traceability/gap/CI，保持 commit 速度。完整強制執行在 `run-phase` / `finalize-gate`。

> ⚠️ 不要在 pipeline 或 agent 腳本中以 `pre-commit-check` 替代 `run-phase`。

> Preflight 失敗不可繞過。修正問題後重新執行。

---

### `run-gate` — 執行品質門

```bash
python harness_cli.py run-gate \
  --gate    2          \  # Gate 編號 1-4（必填）
  --phase   3          \  # 當前 Phase（必填）
  --project /project   \  # 專案路徑（預設：.）
  --fr-id   FR-01      \  # FR ID（Gate 1 必填）
  --skip-preflight        # v2.4+: 跳過 preflight 驗證（預設：啟用 preflight）
```

**返回碼**：永遠為 `0` — `run-gate` 只準備評估上下文並印出提示，不做閾值判斷。  
閾值判斷與阻塞（exit 1）由 `finalize-gate` 負責（見下節）。  
**需要**：SSI 已安裝
**v2.4+**：`run-gate` 預設在 gate 評估前先執行 preflight 驗證。使用 `--skip-preflight` 跳過。

> **Gate 1 vs CI**: Gate 1 需要 `--fr-id FR-XX`，必須由開發者在本地逐 FR 執行。  
> CI 使用 `run-phase --phase $PHASE --project .`（**非** `run-gate`）——`run-phase` 自動選擇正確的 phase-exit gate（P3→Gate 2、P4→Gate 3）並處理 auto-fix loop。  
> Gate 1 **不**在 CI 中自動對所有 FR 執行。

---

### `finalize-gate` — 完成品質門評估

```bash
python harness_cli.py finalize-gate \
  --gate    2          \  # Gate 編號 1-4（必填）
  --phase   3          \  # 當前 Phase（必填）
  --project /project   \  # 專案路徑（預設：.）
  --fr-id   FR-01      \  # FR ID（Gate 1 必填）
  --no-git               # 停用 gate 通過後的 git commit/push（測試用）
```

**時機**：`run-gate` 印出評估提示後，Claude 完成評估並寫入 `.sessi-work/gate{N}_result.json`，再執行此命令。  
**返回碼**：0=gate 通過；1=Gate Blocked（分數或閾值未達標）；2=錯誤（result.json 不存在等）


> **完整兩步流程**（`run-pipeline` 已於 v2.5 移除，此為標準流程）：
> ```bash
> # Step 1 — 準備評估上下文，Claude 評估後寫入 result.json
> python harness_cli.py run-gate --gate 2 --phase 3 --project /project
> # Claude 評估 → 寫 .sessi-work/gate2_result.json
>
> # Step 2 — 讀取結果，檢查閾值，更新 manifest
> python harness_cli.py finalize-gate --gate 2 --phase 3 --project /project
> ```

---

### `manifest` — 生成 Quality Manifest（P2 exit）

```bash
python harness_cli.py manifest \
  --fr-ids FR-01 FR-02 FR-03 \  # FR ID 清單（必填）
  --sad    SAD.md               # SAD.md 路徑（預設：SAD.md）
```

**輸出**：`.methodology/quality_manifest.json`  
**時機**：Phase 2 完成後執行一次

---

### `status` — 查看專案狀態

```bash
python harness_cli.py status \
  --project /project   \  # 專案路徑（預設：.）
  --json               \  # v2.4+: 機器可讀 JSON 輸出
  --full                  # v2.4+: 包含 test stats + auto-fix round 資訊
```

**顯示**：FSM 狀態 + Phase 進度表（8 phases）+ quality_manifest 中所有 gate 結果摘要  
**`--json`**：輸出 JSON 格式，適合 CI/CD pipeline 或自動化腳本  
**`--full`**：額外顯示 test 數量、coverage %、auto-fix 使用輪數

---

### `check-test-inventory` — 驗證測試覆蓋完整性（P3+）

```bash
python harness_cli.py check-test-inventory \
  --diff-mode              \  # 只檢查本次 diff 的 FR（P3 Gate 2 使用）
  --strict                 \  # P4+ 嚴格模式：assertion quality + RED-first ordering
  --threshold 60           \  # D4 最低涵蓋率（預設：60，Gate 2=60/Gate 3=80/Gate 4=90）
  --srs-crosscut           \  # 啟用 cross-cutting SRS 需求掃描
  --crg-gaps                  # 啟用 CRG untested hub 交叉比對
```

**檢查項目**：
- **I-1**: 所有 FR 有對應 test 檔案（`tests/test_fr_*.py`）
- **I-2**: FR→test 檔案名對應一致（`test_fr_{id}.py` 命名慣例）
- **I-3**: RED-first ordering（測試 commit 早於實作程式碼）
- **I-5**: Integration tests 已標記 `@pytest.mark.integration`
- **I-6b**: Test assertion quality ≥ 閾值（AST-based density 掃描）
- **I-6c**: CRG hub 節點有對應測試覆蓋

**返回碼**：0=所有檢查通過；1=有 FAIL（`--strict` 模式）；2=錯誤

---

### `init-project` — 一鍵初始化目標專案

```bash
python harness_cli.py init-project \
  --project /path/to/target \  # 目標專案路徑（必填）
  --phase   1              \   # 起始 Phase（預設：1）
  --overwrite                  # 覆寫已存在的 CI workflow / git hooks / state.json
```

**自動執行步驟**：
1. 確認 harness 可匯入（`harness_cli.py` 與 `core/`/`harness/` 存在於目標路徑）
2. 生成 `.github/workflows/harness_quality_gate.yml`（CI 配置）
3. 執行 `setup-git-hooks.sh`（互動式；用 `--ci-only` 略過）
4. 初始化 `.methodology/state.json`（`current_phase = <N>`）

> **與其他初始化入口的關係**：
> - `setup-git-hooks.sh` — 僅安裝 git hooks，互動式提問 phase
> - `harness-init.sh --phase N` — 冪等初始化，自動跳過已完成步驟（適合 Makefile/CI 嵌入）
> - `init-project`（此命令）— 生成 CI workflow + 呼叫 `setup-git-hooks.sh` + 初始化 `.methodology/state.json`；適合新專案一鍵初始化
>
> **推薦流程**：新專案用 `init-project`；已有配置的存量專案用 `harness-init.sh`（冪等安全）。

**返回碼**：0=成功；1=目標路徑不存在或無寫入權限

---

### `effort` — 查看工時統計

```bash
python harness_cli.py effort \
  --phase   3          \  # 篩選特定 Phase（省略=全部）
  --project /project      # 專案路徑（預設：.）
```

**顯示**：Gate 執行次數、平均耗時、各 Phase/Gate 細分  
**資料來源**：`.methodology/effort_metrics.db`（SQLite）

---

### Lifecycle Hooks（v2.4+）

Hooks 是可選的 shell/Python 指令，在特定 phase/gate/FR 事件自動執行。定義檔：`.methodology/hooks.json`

**支援的事件**：

| Event | 觸發時機 | 失敗行為 |
|-------|---------|---------|
| `before_phase` | Phase 開始前 | Fatal — 中止 phase |
| `after_gate_pass` | Gate 通過後 | 記錄並忽略 |
| `on_gate_fail` | Gate 失敗後 | 記錄並忽略 |
| `on_escalate` | Auto-fix 升級至人類時 | 記錄並忽略 |
| `after_fr_complete` | FR 完成後 | 記錄並忽略 |
| `before_phase_advance` | Phase 推進前 | Fatal — 阻擋推進 |

**範例 `.methodology/hooks.json`**：
```json
{
  "hooks": [
    {"name": "lint-check", "event": "before_phase", "command": "ruff check .", "timeout": 30, "required": true},
    {"name": "coverage-report", "event": "after_fr_complete", "command": "pytest --cov=app/ --cov-report=term -q", "timeout": 120},
    {"name": "notify-gate-fail", "event": "on_gate_fail", "command": "echo 'Gate failed' >> .methodology/alerts.log"}
  ]
}
```

**執行記錄**：所有 hook 執行結果寫入 `.methodology/hooks.log`（JSONL 格式）。

---

## 10. Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Optional — only needed when `STEERING_PROVIDER_TYPE=anthropic` (Steering LLM judge). Not required for gate evaluation (Gates 1–4 use tool-based scoring only). |
| `HERMES_REVIEWER_TARGET` | `""` | Hermes reviewer target (e.g. `telegram:6308981865`). Used for Agent B reviewer for A/B collaboration (`reviewer_router.py`) — active from P1, degrades gracefully to Gemini→Claude sub-agent if unset. |
| `HERMES_TIMEOUT_MS` | `120000` | Hermes long-poll timeout in ms (default: 2 minutes) |
| `SSI_ROOT` | `harness/ssi` | Path to embedded SSI package (auto-detected from harness_cli.py) |
| `DRIFT_PROJECT_PATH` | cwd | **Required for drift monitor** — absolute path to target project. Without this, `cron_drift_monitor.py` silently analyses the cron job's working directory instead of your project. |
| `PYTHONPATH` | — | Must include harness-methodology repo root for imports. **Only needed for Option B (global clone)** — not required for Option A (submodule) or Option C (copy). |

**Setup example**:
```bash
# Optional — only needed when STEERING_PROVIDER_TYPE=anthropic (Steering LLM judge):
# export ANTHROPIC_API_KEY=sk-ant-...
export HERMES_REVIEWER_TARGET=telegram:1234567890
export DRIFT_PROJECT_PATH=/path/to/your/project
# Option B (global clone) only:
export PYTHONPATH=/path/to/harness-methodology:$PYTHONPATH
```

**Hook-internal variable** (not an env var — do not `export`):

| Variable | Scope | Purpose |
|---|---|---|
| `HARNESS_CLI` | Shell-local inside each `.git/hooks/*` script | Auto-detected path to `harness_cli.py`. Visible in `bash -x` hook output. Set to `""` when auto-detection fails (Option B). **Override by patching the hook file**, not by exporting this variable — `export HARNESS_CLI=...` has no effect because the hook re-assigns it unconditionally. |

---

## 11. Troubleshooting

### `ModuleNotFoundError: No module named 'core'`
```bash
# Run from the harness-methodology repo root
cd /path/to/harness-methodology
python harness_cli.py ...
```

### `[ERROR] Install software_self_improvement...`
```bash
pip install -e /path/to/software_self_improvement
python3 -c "import software_self_improvement; print('OK')"
```

### `Gate 1 always fails — linting threshold 90`
```
# Tell Claude:
"Gate 1 requires linting score ≥ 90. Please run a linter on the FR-XX 
module and fix ALL warnings/errors before I re-submit."
```

### `status shows state.json not found`
```bash
# Initialize state manually:
mkdir -p /project/.methodology
echo '{"state": "ACTIVE", "current_phase": 1, "last_update": "'"$(date -u +%Y-%m-%dT%H:%M:%S)"'"}' \
  > /project/.methodology/state.json
```

### `plan-phase outputs empty task list`
```
Cause: SRS.md not found at expected path, or FR format doesn't match.

Required SRS.md format:
  ### FR-01: [Title]
  **Description**: [description]

Required path: {--repo}/SRS.md  (or {--repo}/01-requirements/SRS.md)
```

### `pre-flight blocked by constitution`
```bash
# Fix the constitution violation — preflight failures cannot be bypassed:
# Tell Claude: "Pre-flight constitution check found violations.
# Please review docs/ and fix HR policy violations."

```

### `hook failed — required hook blocked phase start`
```bash
# Check which hook failed and why:
cat .methodology/hooks.log | python3 -m json.tool

# Common causes:
#   - Command not found (missing CLI tool)
#   - Timeout (increase "timeout" in hooks.json)
#   - Non-zero exit code (check hook output in hooks.log)

# Fix the hook command or remove the failed hook from hooks.json, then re-run.
```

### `Gate 1 preflight check blocked — use --skip-preflight`
```bash
# If preflight blocks a gate evaluation (v2.4+):
python harness_cli.py run-gate --gate 1 --phase 3 --project /project --fr-id FR-01 --skip-preflight

# Preflight runs automatically before each gate evaluation.
# Use --skip-preflight only when you've already verified preflight separately.
```

### `git hooks installed but commits never blocked (Option B install)`

**Symptom**: `setup-git-hooks.sh` ran, `.git/hooks/prepare-commit-msg` exists, but bad commits go through silently with no gate output.

**Cause**: Option B (global clone at `/opt/harness`) places `harness_cli.py` at a path the hooks don't auto-detect. Hooks check only `$PROJECT_ROOT/harness_cli.py` and `$PROJECT_ROOT/harness/harness_cli.py` — neither exists for Option B.

**Diagnose**:
```bash
bash -x .git/hooks/prepare-commit-msg /dev/null 2>&1 | grep -E "HARNESS_CLI|Warning"
# If output shows: Warning: harness_cli.py not found → hooks are silently skipping
```

**Fix**: Apply the manual patch from §12.1 (Option B workaround) to all three hooks:
```bash
# Open each hook file and add your harness path:
# .git/hooks/prepare-commit-msg, .git/hooks/pre-push, .git/hooks/post-merge
#
# Find the line:  else\n    HARNESS_CLI=""
# Add before it:  elif [ -f "/opt/harness/harness_cli.py" ]; then
#                     HARNESS_CLI="/opt/harness/harness_cli.py"
#
# Repeat for all three hook files. Then verify:
bash -x .git/hooks/prepare-commit-msg /dev/null 2>&1 | grep HARNESS_CLI
# Expected: HARNESS_CLI=/opt/harness/harness_cli.py
```

---

## 12. GitHub Integration Setup

> **Quick answer**: Run `harness-init.sh` once in your target project. It is fully idempotent — safe to embed in any init script (Makefile, setup.sh, CI bootstrap). Already-done steps are skipped automatically.

### 12.1 One-Time Setup (Idempotent)

**Step 0 — install harness** (once per machine, not per project):

```bash
# Option A — git submodule (recommended)
cd /your/target/project
git submodule add https://github.com/johnnylugm-tech/harness-methodology harness

# Option B — global clone + PYTHONPATH (add to .zshrc/.bashrc)
git clone https://github.com/johnnylugm-tech/harness-methodology /opt/harness
echo 'export PYTHONPATH=/opt/harness:$PYTHONPATH' >> ~/.zshrc

# Option C — copy harness/ into project
cp -r /path/to/harness-methodology/harness /your/target/project/
cp /path/to/harness-methodology/harness_cli.py /your/target/project/
```

**How git hooks find `harness_cli.py`** — each installed hook auto-detects the CLI at runtime:

```
Priority 1: $PROJECT_ROOT/harness_cli.py       ← Option C (copy)
Priority 2: $PROJECT_ROOT/harness/harness_cli.py ← Option A (submodule)
Not found:  warning printed, hook exits 0 (non-blocking) ← Option B needs manual fix
```

| Install option | Hook behaviour | PYTHONPATH needed? |
|---|---|---|
| A (submodule) | Auto-detected at `harness/harness_cli.py` ✅ | No — hook `cd`s to project root where `harness/` is a subdir |
| B (global clone) | **Not auto-detected** — hook skips silently ⚠️ | Yes — add `export PYTHONPATH=/opt/harness:$PYTHONPATH` to shell profile AND symlink or set `HARNESS_CLI` in hook |
| C (copy) | Auto-detected at `./harness_cli.py` ✅ | No |

> **Option B workaround**: After running `setup-git-hooks.sh`, open `.git/hooks/prepare-commit-msg` and add before the `HARNESS_CLI=""` fallback:
> ```bash
> elif [ -f "/opt/harness/harness_cli.py" ]; then
>     HARNESS_CLI="/opt/harness/harness_cli.py"
> ```
> Apply the same patch to `pre-push` and `post-merge`. This is a known limitation of Option B with git hooks.

**Steps 1–3 — run the init script** (idempotent, safe to re-run):

```bash
# From inside your target project:
bash /path/to/harness-methodology/scripts/harness-init.sh --phase 1

# If using submodule (Option A above):
bash harness/scripts/harness-init.sh --phase 1

# Output (first run):
#   ✓  git hooks installed (prepare-commit-msg | post-merge | pre-push)
#   ✓  state.json (current_phase = 1)
#   ✓  CI workflow → .github/workflows/harness_quality_gate.yml

# Output (subsequent runs — all skipped, no side effects):
#   ↷  git hooks (already done)
#   ↷  state.json (already done)
#   ↷  .github/workflows/harness_quality_gate.yml (already done)
```

**Embed in project init scripts:**
```makefile
# Makefile
init:
	bash harness/scripts/harness-init.sh --phase 1
```
```bash
# setup.sh
bash "$(dirname "$0")/harness/scripts/harness-init.sh" --phase 1
```

### 12.2 Verify Setup

Run this end-to-end smoke-test after setup to confirm the full chain is healthy:

```bash
#!/usr/bin/env bash
# Run from your target project root.
# Exit 0 = all checks pass. Non-zero lines indicate what's broken.

# Auto-detect harness_cli.py (works for Option A / B / C):
if   [ -f "./harness_cli.py" ];             then HARNESS_CLI=./harness_cli.py
elif [ -f "./harness/harness_cli.py" ];     then HARNESS_CLI=./harness/harness_cli.py
elif [ -f "/opt/harness/harness_cli.py" ];  then HARNESS_CLI=/opt/harness/harness_cli.py
else HARNESS_CLI=""; fi
HARNESS_DIR=$(dirname "${HARNESS_CLI:-harness/harness_cli.py}")

echo "--- 1. git hooks ---"
ls .git/hooks/prepare-commit-msg .git/hooks/pre-push .git/hooks/post-merge \
  && echo "OK: hooks installed" || echo "FAIL: hooks missing — re-run harness-init.sh"

echo "--- 2. phase config ---"
python3 -c "import json; print(json.load(open('.methodology/state.json'))['current_phase'])" 2>/dev/null \
  && echo "OK: state.json found" || echo "FAIL: run python harness_cli.py init-project --phase 1 --project ."

echo "--- 3. harness_cli.py reachable ---"
if [ -n "$HARNESS_CLI" ]; then
  python3 "$HARNESS_CLI" --help > /dev/null 2>&1 \
    && echo "OK: harness_cli.py found ($HARNESS_CLI)" \
    || echo "FAIL: $HARNESS_CLI exists but failed — check install option (§12.1)"
else
  echo "FAIL: harness_cli.py not found in ./ harness/ /opt/harness/ — check install option (§12.1)"
fi

echo "--- 4. Python deps ---"
python3 -c "import yaml; print('OK: pyyaml')" 2>/dev/null \
  || echo "FAIL: pip install pyyaml"
python3 -c "from core.quality_gate.constitution.profile import GateConfig; print('OK: gate config')" \
  2>/dev/null || echo "FAIL: core/ not on path — check PYTHONPATH / submodule init"

echo "--- 6. SSI embedded ---"
python3 -c "import sys; sys.path.insert(0,'$HARNESS_DIR'); import ssi; print('OK: ssi importable')" \
  2>/dev/null || echo "WARN: SSI not importable — gate evaluation will fall back to static scoring"

echo "--- 7. HERMES_REVIEWER_TARGET ---"
  || echo "WARN: not set — A/B Agent B reviews degrade to Gemini/Claude fallback (P1+). export HERMES_REVIEWER_TARGET=telegram:YOUR_ID"

echo "--- done ---"
```

**Expected healthy output**:
```
OK: hooks installed
OK: state.json found
OK: harness_cli.py found (./harness/harness_cli.py)
OK: pyyaml
OK: gate config
OK: ssi importable
OK: HERMES_REVIEWER_TARGET=telegram:6308981865
```

Any `FAIL` line is a blocking issue. `WARN: SSI` is non-blocking (gates still run with reduced scoring).

### 12.3 Phase Transition

After advancing from Phase N → N+1, a single command handles everything:
```bash
# state.json is the single source of truth — hooks + CI both read it:
python harness_cli.py advance-phase --completed N --project .

# advance-phase does:
#   1. Writes .methodology/state.json (current_phase = N+1)
#   2. Syncs fr_progress.json phase
#   3. Regenerates HANDOVER.md
# All hooks and CI read from state.json — no other step needed.
```

> **Tip**: `harness_cli.py init-project` prints a reminder with the exact GitHub URL after setup. CI phase is auto-detected from `.methodology/state.json` — no manual variable sync required.

### 12.4 Blocked? How to Fix

所有 preflight 和 gate 失敗都必須修復後才能繼續，沒有 bypass 機制。

```bash
# 診斷 preflight 失敗原因:
python harness_cli.py run-phase --phase N --project .

# 診斷 advance-phase 失敗原因:
python harness_cli.py status --project .

# 診斷 deliverable 未 commit:
git status --short
git add <missing_file> && git commit
```

> **注意**：`--emergency-override`、`STAGE_PASS=1 git push`、`git commit --no-verify` 均已移除或停用。

> **Full reference**: [INTEGRATION.md](../INTEGRATION.md) — all 3 dependency options, CI YAML, drift monitor cron, environment variables, phase transition checklist.


## Appendix A — Gate Score Formula

Gate 2/3/4 composite score = weighted average of all dimension scores:

```
score = Σ (dimension_score × dimension_weight)

Example Gate 2 (10 dims):
  linting(90) × 0.12 + type_safety(88) × 0.12 + test_coverage(82) × 0.12
  + security(85) × 0.12 + secrets_scanning(100) × 0.08
  + license_compliance(100) × 0.08 + mutation_testing(72) × 0.20
  + integration_coverage(75) × 0.10 + test_assertion_quality(80) × 0.06
  = 84.38  ✅ (≥75)
```

Gate 1 uses per-dimension pass/fail, not composite score.

---

## Appendix B — Confidence Score（Script-Based, No LLM）

`core/quality_gate/confidence_scorer.py` 計算純工具信心分數，用於 HITL 自動放行。

### 指標定義（C1-C7）

| 指標 | 工具 | 評分公式 | 適用 Phase |
|------|------|---------|-----------|
| C1 artifact_completeness | phase_artifact_enforcer | present/total × 100 | 全部 |
| C2 test_coverage | pytest-cov | 直接取 percent_covered | P3-P8 |
| C3 linting | ruff | 100 - violations × 2，floor 0 | P3-P8 |
| C4 type_safety | pyright / mypy | 100 - errors × 2，floor 0 | P3-P8 |
| C5 test_pass_rate | pytest | passed/(passed+failed) × 100 | P3-P8 |
| C6 security | bandit | 100 - HIGH×20 - MED×5 - LOW×1，floor 0 | P3-P8 |
| C7 traceability | quality_manifest.json | FR gate pass 覆蓋率 | 全部 |

### 加權組合

- **P1/P2**（無程式碼）：C1（65%）+ C7（35%）
- **P3-P8**（含程式碼）：C1(15) C2(20) C3(20) C4(15) C5(15) C6(10) C7(5)

不可用的工具會從加權中排除（不計入分母）。

### 自動放行閾值

| 節點 | 條件 | 結果 |
|------|------|------|
| P1/P2 `push-checkpoint` | confidence ≥ 88 | 自動寫入 commit marker |
| P1/P2 `push-checkpoint` | confidence < 88 | 硬停，列出失敗指標 |

---

*Harness Methodology v2.0 | User Manual | [INTEGRATION.md](../INTEGRATION.md) | [SAD.md](../SAD.md)*
