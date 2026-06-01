---
name: harness-methodology
version: 2.7.0
constitution_version: 2.7
description: |
  全流程軟體開發管線編排與品質門禁。Phase 1-8、14 維度品質憲章。
  Use when: user asks to execute a phase, plan work, run quality gates, or implement FRs.
  Not applicable: one-shot scripts, quick fixes, non-software tasks.
---

# SKILL.md — harness-methodology

> **Version**: v2.7.0 | **Framework**: harness-methodology | **Academic Benchmark**: 91/100

---

## 0. Agent Behavioral Contract (READ FIRST — NOT Reference)

This section is **procedural, not descriptive**. It tells you (the main agent) what to DO when the user asks you to perform phase work. Reference material starts at §1.

### 0.1 Entry Procedure — Mandatory First Actions

When the user says "execute Phase N", "start P3", "implement FR-X", or any phase-work request:

```
0. ONE-TIME PROJECT SETUP (new project only — skip if already initialized):
   Detect: cat .methodology/state.json 2>/dev/null (or check for state.json existence)
   If MISSING (fresh project) → run:
     python harness_cli.py init-project --phase 1 --project .
     → Installs: git hooks, .github/workflows/harness_quality_gate.yml, state.json
   If state.json EXISTS → skip to step 1. Setup already done.

1. VERIFY PLAN EXISTS (always first action for a new phase):
   ls .methodology/phaseN_plan.md 2>/dev/null \
     || python harness_cli.py plan-all --project .
   → Plans are pre-generated at project init by `plan-all` (dynamic mode — see §0.6a).
     Run `load-context --phase N --json` at execution time to load FR IDs and module
     mappings into `.sessi-work/phaseN_ctx.json`.
   → `plan-phase` is kept for debugging only; normal workflow uses pre-generated dynamic plans.

2. PRESENT PLAN to user. Summarize: phase, FR count, preflight checks, checkpoints, estimated effort.
   Do NOT execute any work yet.

3. WAIT for user confirmation ("confirm", "execute", "proceed", "開始", "確認").
   NEVER start work without explicit user confirmation.

4. EXECUTE plan top-to-bottom. You are the ORCHESTRATOR, not Agent A or B:

   [PREFLIGHT]     → python harness_cli.py run-phase --phase N
   [A/B Work]      → Agent A: `harness_cli.py dispatch --role developer --fr-id FR-XX --prompt "..." --phase N`
                   → Agent B: `harness_cli.py dispatch --role reviewer --fr-id FR-XX --prompt "..." --phase N`
                   → sessions_spawn.log auto-written by AgentSpawner (non-blocking debug trail)
                   → NEVER role-play A or B yourself — dispatch them as separate sub-agent sessions (workflow requirement)
   [CHECKPOINT-K]  → run-gate → Claude evaluates inline → finalize-gate → git push

### 0.1a Pre-Execution Mandatory Checklist (Learn-Before-Process)

Before executing any phase work, Agent MUST confirm the following.
This mirrors garden-skills' "learn before you process" constraint —
do NOT start work until every item is checked.

- [ ] 已讀取 `constitution/CONSTITUTION.md` §2 了解當前 phase 的 gate threshold 與維度權重
- [ ] 已讀取 `core/quality_gate/constitution/profile.py` 了解當前 profile 的 dimension keywords
- [ ] 已讀取 `core/auto_fix/classifier.py` 了解 CLASSIFICATION_TABLE 的策略分類（31 entries）
- [ ] 已確認 phase 對應的 gate 編號、最低分數、所需維度數
- [ ] 已確認 `templates/plan_phase_template.md` 中的 CHECKPOINT 標記位置
- [ ] 已確認 WorkspaceManager 為每個 FR 建立了隔離的工作區（`.methodology/workspaces/phase_{N}/FR-XX/`）
- [ ] ⏭️ 以上全部確認後，才能開始執行

5. GATE FAIL? → fix the failing dimensions → re-run `run-gate` → `finalize-gate`. NEVER advance past a failing gate (HR-08).
   Follow the CASE 1–4 early-stop logic in the gate checkpoint (PASS / CONTINUE / PLATEAU / BLOCKED).
   After `max_rounds` without convergence → escalate to human (see SAD.md §3.18 for the 9 escalation conditions).

6. PHASE COMPLETE → Verify Phase Completion Checklist (§0.4) → advance to Phase N+1 (back to step 1).
```

**Crash recovery**: `python harness_cli.py generate-next-plan --project .` → open plan file → resume from next unchecked item.

### 0.2 Source of Truth — One Authority Per Moment

| Moment | Authority | Action |
|--------|-----------|--------|
| Phase entry (new phase) | SKILL.md §1–§2 | Check routing, gates, hard rules |
| Inside a phase | `.methodology/phaseN_plan.md` | Follow checklist top-to-bottom |
| After crash / context reset | `generate-next-plan` | Get position report, then resume plan |

> Do NOT re-read SKILL.md mid-phase for task details — the plan file is the authority.

### 0.3 Verify At Each Boundary

| Boundary | What to verify | CLI |
|----------|---------------|-----|
| Before any phase work | Entry gate verify, FSM state, previous phase artifacts, constitution, kill-switch, drift, SAB, traceability, gap analysis, CI readiness | `run-phase --phase N` |
| After each FR (P3/P4/P5/P7/P8) | Gate 1 per-FR (per-dim: linting ≥90, type_safety ≥85, test_coverage ≥80) | `run-gate --gate 1 --fr-id FR-XX` + evaluate + `finalize-gate` |
| Phase exit (P3→Gate2, P4→Gate3, P6→Gate4) | Gate score ≥ threshold + Phase Truth ≥ 90% (HR-11) | `run-gate --gate N` + evaluate + `finalize-gate` |
| P1/P2 exit | Human peer review (no automated gate) | Deliverables: SRS.md / SAD.md + ADR.md |
| After crash | Current position + next checkpoint | `generate-next-plan` |

> ¹ **D4_SpecCoverage** (v2.6.0 unified): TEST_SPEC.md is the single source of truth for all test traceability. The previous two-check model (TEST_INVENTORY.yaml forward + TEST_SPEC.md backward) is retired. A single spec-coverage check runs at Gates 1-4 with thresholds: Gate1(per-FR)=40%, Gate2=60%, Gate3=80%, Gate4=90%. Use `python harness_cli.py spec-coverage-check --project . --threshold N`. The `check-test-inventory` CLI is deprecated and delegates to `spec-coverage-check`.

> ² **Auto-fix NOT wired + NFR enforcement**: the `core/auto_fix` engine has **no production caller**. The `run-phase` preflight and `finalize-gate` postflight wirings (and the `_run_auto_fix_loop` driver) were removed after end-to-end verification showed every strategy only emits an empty stub / appends a comment, which never clears the checks they target — coverage, drift score and phase artifact chain are substantive gaps needing real development. Preflight/postflight failures now block honestly; the engine + 5 guardrails are kept for a future redesign with strategies that produce real implementation. SAB NFRs mapped to gate dimensions (`performance`/`security`/`readability`/`error_handling`/`test_assertion_quality`) raise a non-waivable `gate_score_overrides` floor; `deployability`/`scalability`/`usability` are advisory-only.

### 0.4 Phase Completion Checklist (Mandatory — Every Phase)

Before advancing to Phase N+1, confirm ALL:

- [ ] All checkpoints in plan marked done (`- [ ]` → `- [x]` in phaseN_plan.md)
- [ ] HANDOVER.md written (auto on git push via GitStrategy)
- [ ] **(ALL) Retry on failure**: If push is blocked (any gate), read the error output,
      apply the suggested fix, and re-run `push-checkpoint` / `push-milestone`.
      Do NOT use `--no-verify` or `--skip-confidence` to bypass.
      Repeat until the push succeeds.
- [ ] **(P3+) push-milestone called before git push**: `python harness_cli.py push-milestone --type <type> --project .`
      Valid types: `p3-mid`, `p3-pre-gate2`, `p4-mid`, `p4-pre-gate3`, `p5-baseline`, `p7`, `p8`
      Writes `last_milestone_command` to `state.json` — CI `push-milestone-enforcement` blocks if absent.
- [ ] **(P3+) Phase End Audit passed**: `.methodology/audit_gaps_{N}.md` has no CRITICAL gaps
      Verify: `python3 scripts/phase_end_audit.py --phase N --project .`
- [ ] Git pushed to remote (confirmed push output, no "push skipped")
- [ ] Next phase plan exists (pre-generated by `plan-all` at project init; verify with `ls .methodology/phase$((N+1))_plan.md`)
- [ ] state.json updated: `python3 harness_cli.py advance-phase --completed N --project .` (updates FSM state)
- [ ] Git tag pushed (Gate 4 only): `harness-v4-YYYYMMDD-scoreXX`
    > P6 quality report review: Phase End Audit 取代原 Agent B (ARCHITECT) 審查。
    > 確認 QUALITY_REPORT.md 內容、Gate 4 ≥ 85、所有 FR 已合併。
- [ ] **(P8 only) `.methodology-archive/` exists and HANDOVER.md has no Phase 9 references** (enforced by CI `p8-archive-check`)

### 0.5 NEVER

- Start coding without reading the phase plan (pre-generated by `plan-all` at project init)
- Execute before user confirms the plan
- Skip preflight (`run-phase`)
- Advance phase after gate failure (HR-08)
- Mix manual mode and automated execution in the same phase
- Re-read SKILL.md for task details mid-phase (use plan file)
- **Role-play both Agent A and Agent B in the same session — dispatch them as separate sub-agent sessions (HR-01 workflow; Phase 1-2)**
- **Send Agent B file paths as input (Phase 1-2 only) — Agent B is stateless, embed content in prompt**
- **Treat evaluate_dimension.md as reference — it is the mandatory tool-execution protocol. Skipping tool steps, using wrong LLM tiers, or fabricating scores without tool output = HR violation. score.py enforces this at machine level.**

### 0.6 Quick Reference — CLI Entry Points

| Intent | Command |
|--------|---------|
| Generate all 8 plans (project init) | `python harness_cli.py plan-all --project .` |
| Load phase context (execution time) | `python harness_cli.py load-context --phase N --project . --json > .sessi-work/phaseN_ctx.json` |
| Plan a new phase (debug only) | `python harness_cli.py plan-phase --phase N --project . --output .methodology/phaseN_plan.md` |
| Run preflight for a phase | `python harness_cli.py run-phase --phase N` |
| Run a gate evaluation | `python harness_cli.py run-gate --gate N --phase P [--fr-id FR-XX]` |
| Finalize a gate | `python harness_cli.py finalize-gate --gate N --phase P` |
| **Push P3+ milestone (required before git push)** | `python harness_cli.py push-milestone --type p3-mid\|p3-pre-gate2\|p4-mid\|p4-pre-gate3\|p5-baseline\|p7\|p8` |
| Phase End Audit (P3+) | `python3 scripts/phase_end_audit.py --phase N --project .` |
| Dispatch Agent A/B (P1/P2) | `python harness_cli.py dispatch --role developer\|reviewer --fr-id <ID> --phase 1\|2 --project . --prompt "..."` |
| Dispatch with long prompt (P1/P2) | `python harness_cli.py dispatch --role reviewer --fr-id SRS.md --phase 1 --prompt-file /tmp/prompt.txt` |
| Dispatch holistic review (P1/P2) | `python harness_cli.py dispatch --role reviewer --fr-id P1_HOLISTIC --phase 1 --skip-deliverable-validation --prompt-file /tmp/review.txt` |
| Initialize a new project | `python harness_cli.py init-project --project /path/to/target --phase 1` |
| Advance to next phase | `python harness_cli.py advance-phase --completed N --project .` |
| Generate manifest for FRs | `python harness_cli.py manifest --fr-ids FR-01 FR-02 --sad SAD.md` |
| Run M3 gap analysis | `python harness_cli.py run-gap-analysis --project .` |
| Audit structure | `python harness_cli.py audit-structure --project .` |
| Git hook pre-commit check | `python harness_cli.py pre-commit-check --phase N` |
| Recover from crash | `python harness_cli.py generate-next-plan --project .` |
| Audit a completed phase | `python harness_cli.py audit-phase --phase N --repo .` |

### 0.6a Dynamic Plan Workflow (plan-all → load-context)

Plans are pre-generated at project init via `plan-all` and use **dynamic mode**:
FR IDs and module mappings are loaded at execution time, not baked into the plan.

**Project init (once):**
```bash
python harness_cli.py init-project --phase 1 --project .
python harness_cli.py plan-all --project .
# → Generates all 8 phase plans in .methodology/phaseN_plan.md
```

**Each phase entry:**
```bash
python harness_cli.py load-context --phase N --project . --json > .sessi-work/phaseN_ctx.json
# → Provides fr_ids, fr_details, modules for the current project state
```

**Plan format:** Static structure (preflight, gates, checkpoints, ASPICE) + dynamic
`{FR-ID}` template blocks that reference `load-context` output at execution time.

**Rule:** `plan-all` output MUST NOT be overwritten by `plan-phase` (debug-only).
Dynamic plans contain `Mode: Dynamic` in the header.

> Full dynamic plan spec: `docs/superpowers/plans/2026-05-05-ssi-merge-into-harness.md`

---

## 1. Phase Routing

| Phase | Name | Entry Score | Exit Gate | Key Artifact |
|-------|------|-------------|-----------|---------------|
| P1 | Requirements Specification | — | Agent B¹ | SRS.md |
| P2 | Architecture Design | Auto (git log)† | Agent B¹ | SAD.md, ADR.md, TEST_SPEC.md |
| P3 | Implementation | Auto (git log)† | Gate2 (75) | code + tests |
| P4 | Testing | Gate2 | Gate3 (80) | TEST_RESULTS.md |
| P5 | Verification & Delivery | Gate3 | None¹ | BASELINE.md |
| P6 | Quality Assurance | Gate3 | Gate4 (85) | QUALITY_REPORT.md |
| P7 | Risk Management | Gate4 | None² | RISK_REGISTER.md |
| P8 | Configuration Management | Gate4 | None² | CONFIG_RECORDS.md |

> ¹ **Agent B¹** = Agent B peer review of deliverables (Phase 1-2 only). Phase 3+ replaces A/B with automated Phase End Audit. NOT `run-gate --gate 1`. Gate 1 only applies to code phases (P3–P5, P7, P8) where linting/type_safety/test_coverage can be measured. P6 has no per-FR Gate 1 — it uses a single Gate 4 (14-dim full audit) at phase exit.
>
> ¹ **None¹** (P5) = Phase Truth check only (HR-11: ≥90%); no separate exit gate evaluation.
>
> ² **None²** (P7/P8) = Cleared by P6 Gate 4; Phase Truth check only (HR-11: ≥90%); no re-evaluation.
>
> † Entry gate: `_verify_entry_gate()` in `harness_cli.py` checks git log for human APPROVE (P2/P3) or `quality_manifest.json` gate PASS (P4+).

### Gate Definitions

| Gate | Phases | score_gate | Dims | Blocking |
|------|--------|------------|------|----------|
| Gate1 | P3, P4, P5, P7, P8 per-FR | per-dim (linting≥90, type_safety≥85, test_coverage≥80; no composite) | 3 (Tier 1) | yes |
| Gate2 | P3 exit | 75 | 9 (Tier 1+2) | yes |
| Gate3 | P4 exit | 80 | 14 (all tiers) | yes |
| Gate4 | P6 full | 85 | 14 (all tiers) | yes |

---

## 2. Hard Rules (HR)

| ID | Rule | Score Impact |
|----|------|--------------|
| HR-01 | A/B are dispatched as separate sub-agent sessions (workflow; the log-count audit was removed — not independently verifiable) | Workflow |
| HR-02 | Quality Gate requires actual stdout output | -20 / Terminate |
| HR-03 | Phase order must be sequential; no skipping | -30 / Terminate |
| HR-04 | HybridWorkflow mode=ON mandatory | Terminate |
| HR-05 | harness-methodology wins all conflicts | Log |
| HR-06 | External frameworks outside spec forbidden | -20 / Terminate |
| HR-07 | DEVELOPMENT_LOG must record session_id | -15 |
| HR-08 | Phase end requires Quality Gate pass | -10 / Terminate |
| HR-09 | Claims Verifier citations must pass | -20 / Terminate |
| HR-10 | ~~sessions_spawn.log must have A/B entries~~ **REMOVED** — log is agent-writable, not tamper-evident; A/B quality enforced by the deliverable review + tool-scored gates | — |
| HR-11 | Phase Truth < 90% blocks phase advance (P3–P8) | Terminate |
| HR-12 | A/B review > 5 rounds triggers PAUSE | — |
| HR-13 | Phase execution > 3× estimate triggers PAUSE | — |
| HR-14 | Integrity < 40 triggers FREEZE | — |
| HR-15 | citations must include line numbers + artifact_verification | -15 |

---

## 3. A/B Collaboration Protocol (Phase 1-2 only)

```
Agent A (DEVELOPER / architect / tester / devops / qa / risk)
  |-- [TDD-1] Write failing test for FR requirement (RED)
  |-- [TDD-2] Implement FR until test passes (GREEN)
  |-- [TDD-3] Refactor without breaking tests (IMPROVE)
  |-- returns JSON: {status, files, confidence, citations, summary}
  |
Agent B (REVIEWER / architect)
  |-- reviews Agent A output against SRS + SAD
  |-- returns JSON: {status, review_status, reason, confidence, citations, summary}
  |
[Constitution Check]  -- BVS + HR-09 validation
[HR-12]               -- iteration guard (max 5 rounds)
```

### Per-Phase A/B Roles

| Phase | Agent A Role | Agent B Role | Agent A Task | Agent B Task |
|-------|------------|------------|--------------|--------------|
| P1 | REQUIREMENTS_ENGINEER | BUSINESS_ANALYST | Draft SRS.md with `### FR-XX:` sections | Review SRS.md against business goals; verify FR-ID traceability |
| P2 | ARCHITECT | TECH_LEAD | Design SAD.md; write ADR.md; generate TEST_SPEC.md via `derive_test_cases.md` skill | Review SAD.md, ADR.md, and TEST_SPEC.md for completeness, coverage, and SRS alignment |

> Phase 3-8 不再使用 A/B 協作，改以自動化 Phase End Audit 替代（見 §0.4 完成檢查表）。
>
> **P6 Gate 4 注意**：原 Agent B (ARCHITECT) 負責審查 QUALITY_REPORT.md 並確認所有 FR 已合併且 Gate 4 ≥ 85。
> A/B 移除後此責任由 **Phase End Audit** 分擔（確認 quality_manifest.json 中 Gate 4 分數與所有 FR 的合併狀態，以及 QUALITY_REPORT.md 內容完整性）。

> Phase 1-2 only: Agent A ≠ Agent B (HR-01 workflow — dispatched as separate sub-agent sessions). `sessions_spawn.log` is written as a non-blocking debug trail (the HR-10 log-count audit was removed — agent-writable, not tamper-evident).
> Phase 3-8: no A/B requirement. Phase End Audit runs at phase completion.

### FORBIDDEN in any agent output

- `app/infrastructure/` imports (deprecated)
- `@covers: L1 Error` annotations
- `@type: edge` test type
- Docstrings without `[FR-XX]` reference
- Docstrings without `Citations:` section with line numbers

---

## 4. sessions_spawn.log & Agent B Approval Format (Phase 1-2 only — non-blocking debug trail)

Two entries per FR/deliverable (developer + reviewer). This log is no longer
enforced at finalize-gate (HR-10 removed); it remains a useful dispatch trace:

```json
{"timestamp": "2026-04-26T10:00:00", "fr_id": "FR-01", "role": "developer",
 "session_id": "dev-abc123", "status": "success", "confidence": 8}
{"timestamp": "2026-04-26T10:05:00", "fr_id": "FR-01", "role": "reviewer",
 "session_id": "rev-def456", "status": "success", "review_status": "APPROVE"}
```

### 4.1 Agent B Approval Files (P1/P2 deliverable-level)

P1/P2 dispatching writes per-deliverable approval JSONs to
`.methodology/agent_b_approvals/<deliverable_id>.json`.
Deliverable IDs are the deliverable file basenames:

| Phase | Deliverable IDs |
|-------|----------------|
| P1 | `SRS.md`, `SPEC_TRACKING.md`, `TRACEABILITY_MATRIX.md`, `TEST_INVENTORY.yaml` |
| P2 | `SAD.md`, `ADR.md`, `TEST_SPEC.md` |

Each approval JSON **MUST** include:

```json
{
  "review_status": "APPROVE",
  "docs_embedded": ["SRS.md"],
  "confidence": 0.9,
  "summary": "..."
}
```

- `docs_embedded` **MUST** list every source document the reviewing agent had in its
  prompt context. P1 reviews require `["SRS.md"]`; P2 reviews require
  `["SRS.md", "SAD.md"]`. Missing entries cause `verify-agent-b-approvals` to block.
- `review_status` **MUST** be `"APPROVE"` (not `"success"` or any other value).

The authoritative deliverable ID registry is `_PHASE_DELIVERABLES` in `harness_cli.py`.
Dispatch with an unrecognized `--fr-id` in P1/P2 is rejected.

### 4.2 Constitution Scan Exclusions

The constitution keyword-density scanner skips meta-documents that inherently
contain zero constitution vocabulary — operational logs, handover files,
and stage-pass certificates. These files are mandatory for the phase auditor
but should not be scored for keyword density.

Default exclusion patterns (glob, matched against file basename):
- `DEVELOPMENT_LOG.md`
- `HANDOVER.md`
- `*STAGE_PASS.md`

Override via `.methodology/constitution_profile.json`:
```json
{
  "exclude_patterns": ["DEVELOPMENT_LOG.md", "HANDOVER.md", "*STAGE_PASS.md"],
  "phases": {
    "3": {"exclude_patterns": ["migration_log.md"]}
  }
}
```

Per-phase patterns are additive with global patterns. See
`core/quality_gate/constitution/profile.py` for the authoritative default list.

---

## 5. State Machine (FSM)

```
INIT -> RUNNING -> PAUSED -> RUNNING
                -> FREEZE  (Integrity < 40)
                -> DONE    (all phases complete)
RUNNING -> OPEN   (KillSwitch triggered)
OPEN    -> HALF_OPEN -> CLOSED  (recovery)
```

State stored in `.methodology/state.json`:
```json
{"current_phase": 3, "state": "RUNNING", "last_gate": 1, "last_fr": "FR-03",
 "last_update": "2026-04-26T10:00:00"}
```

---

## 6. Decision Rules

- **SKILL.md governs**: phase order, gate thresholds, hard rules (HR-01–HR-15), Phase End Audit.
- **Plan governs**: task sequence within a phase; specific file paths; CLI commands.
- **Conflict**: SKILL.md wins on rules; plan wins on task order / phase-specific steps.
- **Never skip checkpoints**: If a gate fails, fix and re-run — never advance without PASS.
- **Phase 1-2**: A/B mandatory — HR-01 (A≠B, separate sub-agent sessions) + HR-04 apply (HR-10 log-count audit removed).
- **Phase 3-8**: No A/B. Phase End Audit runs at advance-phase / push-milestone.

---

## 7. On-Demand Reference

| Need | Where |
|------|-------|
| Module API (kill_switch, detection, gap_detector, core/, enforcement/) | `SAD.md` §3–§6 |
| Agent execution loop, modes, phase completion checklist, recovery | `SAD.md` §9 |
| Autonomous pipeline, human checkpoints | `SAD.md` §10 |
| Phase E2E flow, entry/exit matrix, preflight hooks, Phase Truth weights | `SAD.md` §11 |
| Gate evaluation CLI flow, result file schema, evaluation assets | `SAD.md` §12 |
| CLI commands (plan-all, load-context, run-gate, etc.) | `harness_cli.py --help` |
| Gate thresholds & quality dimensions | `constitution/CONSTITUTION.md` §2 |
| Full Mermaid phase flowchart | `docs/superpowers/plans/harness_phase_flowchart.md` |
| Integration setup (git hooks, CI, submodule, init-project) | `INTEGRATION.md` |
| Crash recovery position | `python harness_cli.py generate-next-plan` |
| Constitution rule parser & HR compliance | `constitution/` directory |
| Constitution keyword scan exclusions | `.methodology/constitution_profile.json` → `exclude_patterns` |
| A/B agent personas | `agent_personas/` directory |

## 8. Agentic Trajectory Tracing (v2.7.0+)

Harness emits OpenTelemetry spans for preflight/postflight execution. Spans land in `.harness/traces/agent_trajectory.jsonl` — one JSON object per line, time-travel-debuggable offline.

**Activation**: automatic when `PhaseHooks` is instantiated with a valid `project_path`. No configuration required.

**Dependencies** (already in `pyproject.toml`):
```
opentelemetry-api>=1.20.0
opentelemetry-sdk>=1.20.0
```

**SteeringLoop tracing**: pass `project_root=Path(".")` to `SteeringLoop(...)` to enable CRG-lazy-cache integration in `LLMJudgeScorer`. The `score_with_critic_debate()` method fires a multi-round critic debate when score deltas are close (< `DEBATE_DELTA_THRESHOLD=0.15`) or when sensitive modules are changed and delta < `SENSITIVE_DEBATE_THRESHOLD=0.30`. Sensitive module prefixes: `steering/`, `enforcement/`, `core/auto_fix/`, `core/fsm/`.

**Span names**: `phase_{N}_preflight`, `phase_{N}_postflight`

---

## 9. CRG Integration Layer

CRG (Code Review Graph) is **mandatory** (same tier as ruff/mypy/pytest). It provides
structural analysis — call graphs, community detection, flow analysis, dead code detection.

### 9.1 CRG Injection Points (HarnessBridge)

| Point | When | API | Gates |
|-------|------|-----|-------|
| 1 Reconnaissance | `prepare_gate()` | `crg.run_reconnaissance()` | 3, 4 |
| 2 Tier 3 Guidance | `prepare_gate()` | `crg.get_minimal_context(dim)` | 3, 4 |
| 3 Pre-fix Safety | Before each fix round | `bridge.check_pre_fix_safety()` | 2, 3, 4 |
| 4 Drift Check | After each fix round | `bridge.check_post_round_drift()` | 3, 4 |

### 9.2 Deep Integration Points (Deterministic)

| # | Signal | Formula | Where |
|---|--------|---------|-------|
| 1 | `risk_score` | `eval_depth` gate | `evaluate_dimension.md` |
| 2 | `community_cohesion` | Architecture score — framework-owned (`crg_independent.py`, overrides agent) | `harness_bridge.finalize_gate` |
| 3 | ~~`flow_coverage`~~ | **Removed** — `error_handling` is now `ast-error-handling` (file-level try/except coverage); CRG had no `has_error_handler` field | `tool_runners.py` |
| 4 | `dead_code_ratio` | Escalate severity if > 5% | `improvement_plan.md` |
| 5 | `hub_risk_map` | Severity bucket by fan-in | `evaluate_dimension.md` |
| 6 | `suggested_questions` | Auto-seed issue registry | `crg_reconnaissance.md` |

### 9.3 Key CRG MCP Tools

| Tool | Use |
|------|-----|
| `build_or_update_graph` | Gate 3/4 entry, post-edit auto-update |
| `get_minimal_context` | Tier 3 per-dim context (~100 tokens) |
| `detect_changes` | Pre-fix safety, post-round drift |
| `get_hub_nodes` / `get_bridge_nodes` | Structural reconnaissance |
| `list_communities` / `get_community` | Cohesion scoring |
| `get_knowledge_gaps` | Untested hotspot detection |
| `query_graph` | Callers/callees/tests tracing |
| `semantic_search_nodes` | Codebase exploration |
| `find_large_functions` | Readability evaluation |
| `refactor_tool` | Dead code detection |

### 9.4 Gate-CRG Configuration

| Gate | CRG Scope |
|------|----------|
| Gate 1 (per-FR) | None — 3 dims, Tier 1 only |
| Gate 2 (P3 exit) | Graph refresh + impact check |
| Gate 3 (P4 exit) | Full: recon + tier3 + impact + drift |
| Gate 4 (P6 full) | Full + mandatory B3 recon check |

### 9.5 Verifying CRG

```bash
python3 scripts/verify_tools.py          # CRG is now in CORE section
code-review-graph status                  # Quick status check
cat .sessi-work/crg_status.json          # Session-level status
cat .sessi-work/crg_reconnaissance.json  # Recon output (Gate 3/4)
cat .sessi-work/crg_metrics.json         # Metrics for scoring
```

Full reference: `docs/CRG_DEEP_INTEGRATION.md`

---

*harness-methodology v2.7.0 — Academic Benchmark 91/100*
