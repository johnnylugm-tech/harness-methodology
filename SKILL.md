---
name: harness-methodology
version: 2.9.0
constitution_version: 2.7
description: |
  全流程軟體開發管線編排與品質門禁。Phase 1-8、14 維度品質憲章。
  Use when: user asks to execute a phase, plan work, run quality gates, or implement FRs.
  Not applicable: one-shot scripts, quick fixes, non-software tasks.
---

# SKILL.md — harness-methodology

> **Version**: v2.9.0 | **Framework**: harness-methodology | **Academic Benchmark**: 91/100

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

**Crash recovery**: `python harness_cli.py generate-next-plan --project .` → open plan file → resume from next incomplete step.

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
| Before any phase work | Entry gate verify, FSM state, previous phase artifacts, kill-switch, drift, SAB, traceability, manifest integrity, FR-spec consistency, reliability lint, config liveness | `run-phase --phase N` |
| After each FR (P3/P4/P5/P7/P8) | Gate 1 per-FR (per-dim: linting ≥90, type_safety ≥85, test_coverage ≥80) | `run-gate --gate 1 --fr-id FR-XX` + evaluate + `finalize-gate` |
| Phase exit (P3→Gate2, P4→Gate3, P6→Gate4) | Gate score ≥ threshold + Phase Truth ≥ 90% (HR-11) | `run-gate --gate N` + evaluate + `finalize-gate` |
| P1/P2 exit | Human peer review (no automated gate) | Deliverables: SRS.md / SAD.md + ADR.md (see ⁴) |
| After crash | Current position + next checkpoint | `generate-next-plan` |

> ¹ **D4_SpecCoverage** (v2.6.0 unified): TEST_SPEC.md is the single source of truth for all test traceability. The previous two-check model (TEST_INVENTORY.yaml forward + TEST_SPEC.md backward) is retired. A single spec-coverage check runs at Gates 1-4 with thresholds: Gate1(per-FR)=40%, Gate2=60%, Gate3=80%, Gate4=90%. Use `python harness_cli.py spec-coverage-check --project . --threshold N`. (The deprecated `check-test-inventory` CLI has been removed.)

> ² **Auto-fix wiring (post-PR 5/9) + NFR enforcement**: the `core/auto_fix` engine has **one production caller** (`fix_missing_traceability`) dispatched from `PhaseHooks.preflight_traceability` at P5+ when a trace gap is detected. Dispatch policy: per-strategy allowlist (only problem_type=`missing_traceability`); bounded retry (max_rounds=1); escalation to HUMAN_REQUIRED with `.methodology/trace/proposed_fix.diff` on failure. The other 4 strategies (coverage, drift score, phase artifact chain, key keyword density) still emit stubs and are not production-wired — preflight/postflight failures from those dims block honestly. The engine + 5 guardrails are kept for a future redesign. SAB NFRs mapped to gate dimensions (`performance`/`security`/`readability`/`error_handling`/`test_assertion_quality`) raise a non-waivable `gate_score_overrides` floor; `deployability`/`scalability`/`usability` are advisory-only.

> ³ **Closed-loop wiring (PR 6-10, commit `dedf560` + audit fixes)**: the trace closed loop is fully tight end-to-end. Five pre-existing blind spots are now wired:
>
> | PR | Mechanism | File | Effect |
> |----|-----------|------|--------|
> | PR 6 | `_trace_dirty_state(project_path)` mtime probe (SAD.md + newest `tests/test_fr*.py` vs `attestation.json`) | `harness_cli.py:1345` | Pre-commit `git commit` is blocked if attestation is stale; sub-50ms, no rglob |
> | PR 7 | `PhaseHooks.preflight_fr_spec_consistency` — symmetric difference of SAD.md FRs vs `02-architecture/TEST_SPEC.md` FRs (`sad_only` / `spec_only` orphans) | `core/phase_hooks.py:479` | P3+ informational, P5+ blocking; prevents silent 4a/4b disagreement |
> | PR 8 | `make attest` target: `build-trace-attestation --project . && git add .methodology/trace/attestation.json` | `Makefile:61` | One-command refresh + stage of the git_sha-anchored attestation |
> | PR 9 | `_dispatch_trace_auto_fix(project_path, untested, uncoded)` — P5+ allowlist dispatch to `AutoFixEngine.fix(problem_type='missing_traceability', max_rounds=1)`, then re-verify | `core/phase_hooks.py:39`, called at `:426` | Trace gap auto-fills `core/auto_*.py` annotation + `tests/test_fr_*.py` stub; re-verifies; escalates to HUMAN_REQUIRED on failure |
> | PR 10 | `make setup-hooks` and `make setup` — install `scripts/setup-git-hooks.sh` `pre-push` hook that runs full preflight | `Makefile:69`, `:76` | `git push` runs `run-phase` preflight before remote receives the push |
>
> Regression tests: `tests/test_trace_dirty_state.py` (PR 6), `tests/test_preflight_fr_spec_consistency.py` (PR 7), `tests/test_makefile_attest_target.py` (PR 8), `tests/test_preflight_auto_fix_dispatch.py` (PR 9), `tests/test_makefile_setup_hooks_target.py` (PR 10). Manual end-to-end checks for each PR are in `~/.claude/plans/compressed-tinkering-mccarthy.md` §"End-to-end verification".

> ⁴ **P2 sub-deliverable check + template-stub sentinel**: after ADR.md A/B review completes, the plan emits a single-file `check-constitution --phase 2 --file 02-architecture/adr/ADR.md` step (CONSTITUTION-CHECK-ADR). Run it before TEST_SPEC.md depends on the ADR. The end-of-phase directory-wide check (`CONSTITUTION-CHECK`, scans whole `02-architecture/`) still runs as the final defense. `init-project` copies `templates/ADR.md` with the `<!-- harness:template-stub -->` sentinel line — the runner skips scoring while that line is present (vacuous 100/100/100/100) and starts scoring normally the moment the author removes it. See §0.3.1 for the sentinel contract.

### 0.3.1 Template Stub Sentinel

Any template under `templates/` may contain the line `<!-- harness:template-stub -->` as its first content line. While present, `core.quality_gate.constitution.runner._scan_file_compliance` returns a vacuous `{correctness:100, security:100, maintainability:100, coverage:100}` dict for that file — it does not count toward the phase's aggregate score. The moment the author removes the line, the file is scored normally.

- **Sentinel literal**: `<!-- harness:template-stub -->` (lowercase, exact match).
- **Co-equal heuristic**: `_is_stub_template(content)` (counts `{placeholder}` patterns ≥ 8) still applies. A file may satisfy either, both, or neither.
- **Author contract**: remove the sentinel line as the first edit when you start filling the template. Leaving it in a real document is a bug (the file is silently exempted from quality scoring).
- **First shipped in**: `templates/ADR.md`. Other templates may adopt the same pattern.

### 0.3.2 SAB Authoring (P2 §5) — Self-Service Template

When writing `SAD.md` §5, do **NOT** hand-write the SAB YAML — get the canonical template from code so it can never drift:

```python
from core.quality_gate.sab_parser import render_canonical_sab_template
print(render_canonical_sab_template(project="my-project"))
```

The 14-field shape, `sab:` root key, `phase` as int (not string), and the 8 legal NFR type values (`performance`, `security`, `maintainability`, `reliability`, `testability` + `deployability`, `scalability`, `usability`) are all enforced by `SABSpec` + `validate_sab_block()`. Run `python3 scripts/generate_sab.py --validate --project .` to fail fast on a bad SAB block before committing.

### 0.4 Phase Completion Checklist (Mandatory — Every Phase)

Before advancing to Phase N+1, confirm ALL:

- [ ] HANDOVER.md written (auto on git push via GitStrategy)
- [ ] **(ALL) Retry on failure**: If push is blocked (any gate), read the error output,
      apply the suggested fix, and re-run `push-checkpoint` / `push-milestone`.
      Do NOT use `--no-verify` or `--skip-confidence` to bypass.
      Repeat until the push succeeds.
- [ ] **(P3+) push-milestone called before git push**: `python harness_cli.py push-milestone --type <type> --project .`
      Valid types: `p3-mid`, `p3-pre-gate2`, `p4-mid`, `p4-pre-gate3`, `p5-baseline`, `p7`, `p8`
      Writes `last_milestone_command` to `state.json` — CI `push-milestone-enforcement` blocks if absent.
- [ ] **(P3+) Phase End Audit passed**: `.methodology/audit_gaps_{N}.md` has no CRITICAL gaps
      Verify: `python3 harness_cli.py audit-phase --phase N --project .`
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
- **NEVER modify files inside `harness/` (the methodology submodule) from the project side.** Bugs found in harness-methodology must be reported to the harness-methodology repo; hotfixes applied directly in the submodule create divergence and are invisible to the upstream. The only permitted change to the submodule is `git submodule update --remote`.

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
| Phase End Audit (P3+) | `python3 harness_cli.py audit-phase --phase N --project .` |
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
| P9: open a Change Request | `python harness_cli.py cr-open --type bug\|feat --title "..." --project .` |
| P9: update/advance a CR | `python harness_cli.py cr-update --cr CR-NN --set field=value --status S` |
| P9: list/inspect CRs | `python harness_cli.py cr-status [--cr CR-NN] --project .` |
| P9: close a CR (fail-closed checklist) | `python harness_cli.py cr-close --cr CR-NN --project .` |
| P9: push closed-CR milestone | `python harness_cli.py push-milestone --type cr-close --cr CR-NN --project .` |

### 0.6a Dynamic Plan Workflow (plan-all → load-context)

Plans are pre-generated at project init via `plan-all` and use **dynamic mode**:
FR IDs and module mappings are loaded at execution time, not baked into the plan.

**Project init (once):**
```bash
python harness_cli.py init-project --phase 1 --project .
python harness_cli.py plan-all --project .
# → Generates all 9 phase plans in .methodology/phaseN_plan.md
#   (phase9_plan.md is the maintenance playbook — static, CR-driven)
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

### 0.6b P9 Maintenance Workflow (Change Requests — ASPICE SUP.9/SUP.10)

After `advance-phase --completed 8`, the project sits permanently at Phase 9.
All further work is ticket-driven; NO code change without a CR.

**CR-BUG (bug fix — SUP.9 problem resolution):**
```bash
python harness_cli.py cr-open --type bug --title "..." --severity high --project .
# 1. failing repro test FIRST, then record it + root cause:
python harness_cli.py cr-update --cr CR-NN --set repro_test=tests/test_crNN_repro.py \
    --set root_cause="..."
python harness_cli.py cr-update --cr CR-NN --status ANALYZED   # → APPROVED → IN_PROGRESS
# 2. fix code (keep [FR-XX] annotations); repro green; full suite green
# 3. Gate 1 on touched FRs (untouched: --delta):
python harness_cli.py run-gate --gate 1 --fr-id FR-XX --phase 9 --project .
python harness_cli.py finalize-gate --gate 1 --fr-id FR-XX --phase 9 --project .
# 4. evidence + close:
python harness_cli.py cr-update --cr CR-NN --set affected_frs=FR-XX \
    --set resolution.fix_commit=<sha> --status VERIFIED
python harness_cli.py cr-close --cr CR-NN --project .
python harness_cli.py push-milestone --type cr-close --cr CR-NN --project .
```

**CR-FEAT (feature add/change — SUP.10 change request):**
Same lifecycle, but APPROVED requires `--set approval.approved_by=... --set
approval.justification=...`, and the spec chain is written back IN PLACE before
TDD: SRS.md `### FR-XX:` → SAD.md module row (new module → `amend-sab`) →
TEST_SPEC.md section + TEST_INVENTORY.yaml → `run-fr-step --phase 9` TDD loop.

**Invariants (cr-close enforces, fail-closed):** resolution.fix_commit present;
CR-BUG repro_test exists on disk; every affected FR has a passing Gate 1 record
in quality_manifest (surgical append — NEVER regenerate the manifest);
`build-trace-attestation --write` re-run and `verify-trace` clean; spec/SAD
drift clean. Closed CRs append to `09-maintenance/MAINTENANCE_LOG.md`.

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
| P9 | Maintenance (steady state) | Gate4 + P8 done³ | Never exits³ | MAINTENANCE_LOG.md |

> ¹ **Agent B¹** = Agent B peer review of deliverables (Phase 1-2 only). Phase 3+ replaces A/B with automated Phase End Audit. NOT `run-gate --gate 1`. Gate 1 only applies to code phases (P3–P5, P7, P8 per-FR; P9 per-CR touched FRs) where linting/type_safety/test_coverage can be measured. P6 has no per-FR Gate 1 — it uses a single Gate 4 (14-dim full audit) at phase exit.
>
> ³ **P9 Maintenance** = re-entrant steady state entered via `advance-phase --completed 8`; `advance-phase --completed 9` is always BLOCKED. Work is Change-Request-driven: CR-BUG (ASPICE SUP.9) / CR-FEAT (ASPICE SUP.10) via `cr-open` → `cr-update` → `cr-close` (fail-closed re-entry checklist: evidence + Gate 1 per touched FR + trace attestation + drift). See §0.6b.
>
> ¹ **None¹** (P5) = Phase Truth check only (HR-11: ≥90%); no separate exit gate evaluation.
>
> ² **None²** (P7/P8) = Cleared by P6 Gate 4; Phase Truth check only (HR-11: ≥90%); no re-evaluation.
>
> † Entry gate: `_verify_entry_gate()` in `harness_cli.py` checks git log for human APPROVE (P2/P3) or `quality_manifest.json` gate PASS (P4+).

### Gate Definitions

| Gate | Phases | score_gate | Dims | Blocking |
|------|--------|------------|------|----------|
| Gate1 | P3, P4, P5, P7, P8 per-FR; P9 per-CR touched FRs | per-dim (linting≥90, type_safety≥85, test_coverage≥80; no composite) | 3 (Tier 1) | yes |
| Gate2 | P3 exit | 75 | 10 (Tier 1+2 + traceability) | yes |
| Gate3 | P4 exit | 80 | 16 (all tiers + traceability + adversarial_review) | yes |
| Gate4 | P6 full | 85 | 15 (all tiers + traceability) | yes |

> Gate 3 adds **adversarial_review** (v2.9): a framework-owned bug-hunt verdict
> that blocks until confirmed critical/high findings are resolved or refuted.
> See §7.6 and `docs/ADVERSARIAL_QUALITY_LAYER.md`.

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
| HR-07 | ~~DEVELOPMENT_LOG must record session_id~~ **REMOVED** — agent-writable, not tamper-evident; same rationale as HR-10 | — |
| HR-08 | Phase end requires Quality Gate pass | -10 / Terminate |
| HR-09 | Claims Verifier citations must pass | -20 / Terminate |
| HR-10 | ~~sessions_spawn.log must have A/B entries~~ **REMOVED** — log is agent-writable, not tamper-evident; A/B quality enforced by the deliverable review + tool-scored gates | — |
| HR-11 | Phase Truth < 90% blocks phase advance (P3–P8) | Terminate |
| HR-12 | A/B review > 5 rounds triggers PAUSE | — |
| HR-13 | Phase execution > 3× estimate triggers PAUSE | — |
| HR-14 | Integrity < 40 triggers FREEZE | — |
| HR-15 | citations must include line numbers + artifact_verification | -15 |
| HR-16 | trace dimension (4a=100% over IN_PROGRESS+VERIFIED FRs at G2/G3/G4) must pass. `gate_score_overrides` is a **threshold floor** (raises, not lowers) per `sab_parser.derive_gate_score_overrides` — it cannot bypass a failing trace dim. The only remediation paths are: (a) fix the underlying code/FRs to reach 100%, (b) accept the gate block and re-architect, or (c) escalate to human. There is no automated override. | Terminate |
| HR-17 | **NEVER modify files inside `harness/` (methodology submodule) from the project side.** Bugs found in harness-methodology must be reported upstream; hotfixes in the submodule create divergence invisible to the upstream repo. The only permitted submodule change is `git submodule update --remote`. | Terminate |

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

> **P1 ingestion machine-backstop**: when `PROJECT_BRIEF.md` declares a `canonical_spec`, the LLM A/B review is backstopped by a decidable gate — `python harness_cli.py check-spec-alignment --project .`. It fails if any canonical `### FR-NN` is dropped from SRS.md or any SRS FR is invented (no canonical counterpart), mechanically enforcing the R-CANONICAL-INTERP-001 "100% transcribe" rule that A/B otherwise uphold by hand. It runs inside every `run-phase` pre-flight (informational at P1, blocking from P2). Elicitation mode (no canonical_spec) has no ground truth → N/A.

> **P2 optional property gate**: an FR with an algebraic invariant (round-trip / idempotence / monotonicity / conservation) MAY declare a `**Properties**` table in TEST_SPEC.md. `python harness_cli.py check-property-spec` then (a) self-consistency-checks each invariant against its cases via the same red_assertion engine, and (b) from P4 requires an executing property test (`hypothesis @given` / fast-check). Opt-in per FR; property strength is measured by the existing `mutation_testing` dimension, not a new score. Runs inside `run-phase` pre-flight.

> **Cross-run failure memory (auto)**: every `finalize-gate` BLOCK distils the failing dimensions into `.methodology/lessons/<key>.md` (idempotent, human-inspectable). At phase entry, `load-context` auto-injects a `lessons` block of the most relevant past failures for that phase's FRs — relevance-gated (by FR overlap) and capped at 5, so it cannot pollute the prompt. The agent sees "known failure modes to avoid" from prior runs without any manual step. To record a confirmed bug-hunt finding into the same memory, call `core.lessons.record_lesson`.

> **Traceability views are render-only (auto-refreshed)**: `TRACEABILITY_MATRIX.md` and `SPEC_TRACKING.md`'s Status column are NOT authoritative — the authoritative FR status is `build_traceability`'s live code/test scan (what the gate `traceability` dimension uses) and `quality_manifest.json`. `advance-phase` re-renders both from that scan (TRACEABILITY_MATRIX via `generate_markdown_matrix`; SPEC_TRACKING via in-place Status refresh), staging each only if changed. Never hand-edit the Status column / AUTO-GEN block — it is overwritten on the next advance; put manual traceability rows in `TRACEABILITY_MATRIX.overlay.yaml` and semantic SPEC_TRACKING columns in the table body.

> **Artifact-consistency gate (P2/P3, auto)**: `python harness_cli.py check-artifact-consistency` (also in `run-phase` pre-flight) machine-catches two agent hallucinations: (a) a forward-reference to an invented filename — every `NN-stage/FILE.md` reference in a P1/P2 artifact must name a real deliverable (e.g. the P2 architecture doc is `SAD.md`, never `ARCHITECTURE.md`), blocking from P2; (b) an SRS NFR dropped from ADR.md's traceability *table* (a prose mention does not count), blocking from P3. Decidable, no LLM.

> Phase 3-5, 7-8 不再使用 A/B 協作，改以自動化 Phase End Audit 替代（見 §0.4 完成檢查表）。
>
> **P6 Gate 4 注意**：Phase 6 (Gate 4) 重新引入 Agent B 審查機制，確保所有發布文件與品質數據（包含 `QUALITY_REPORT.md`, `RELEASE_NOTES.md`, `FINAL_SIGN_OFF.md`, 與 `quality_manifest.json`）經過交叉核實。

> Phase 1, 2, and 6: Agent A ≠ Agent B (HR-01 workflow — dispatched as separate sub-agent sessions). `sessions_spawn.log` is written as a non-blocking debug trail (the HR-10 log-count audit was removed — agent-writable, not tamper-evident).
> Phase 3-5, 7-8: no A/B requirement. Phase End Audit runs at phase completion.

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
- `HANDOVER.md`
- `*STAGE_PASS.md`

Override via `.methodology/constitution_profile.json`:
```json
{
  "exclude_patterns": ["HANDOVER.md", "*STAGE_PASS.md"],
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
- **Phase 1-2, 6**: A/B mandatory — HR-01 (A≠B, separate sub-agent sessions) + HR-04 apply (HR-10 log-count audit removed).
- **Phase 3-5, 7-8**: No A/B. Phase End Audit runs at advance-phase / push-milestone.

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

## 7.5 Language Support (v2.8.0+)

支援語言:**python**(預設)、**javascript**、**typescript**。一專案一語言
(混語言 monorepo 不支援),由 `init-project` 偵測(`tsconfig.json` → ts;
`package.json` → js;歧義時必須 `--language` 明示)並持久化到
`.methodology/state.json`(`language` + `test_runner`)。中途不可改語言。

| 機制 | 規則 |
|------|------|
| 工具解析 | gate YAML `tool:` = Python 預設;js/ts 經 `harness/toolchains/registry.py` 的 DIMENSION_TOOLS 解析(vitest/jest 變體按 `test_runner`)。R8 之下任何已註冊語言必須 14 維全覆蓋。 |
| JS/TS 測試命名(強制) | 測試標題必須 `it('test_frNN_xxx', ...)` / `test('test_frNN_xxx', ...)` — D4 spec-coverage 與 P1 Naming Authority 靠名字集合匹配,標題即名字。檔案放 `tests/`,命名 `test_frNN_*.test.<ext>` 或 `*.spec.<ext>`。 |
| 純 JS 的 type_safety | 強制 JSDoc 型別 + `tsc -p tsconfig.checkjs.json --noEmit`(R8 禁止跳過維度);TS 用 `tsc --noEmit`。 |
| TEST_SPEC 謂詞 | spec 層 sub-assertion 謂詞**一律 Python 表達式語法**(如 `len(result) == 4`),與實作語言無關。P3 mirror gate 對 js/ts 為 structure-only(謂詞對齊 → needs_review,人審)。 |
| 工具來源 | js/ts 工具一律專案內釘版 devDependencies(`templates/js_toolchain/package.json`)+ `npx --no-install`;先 `npm ci`。security 用 vendored semgrep 規則集。 |
| error_handling 豁免 | pragma 字串跨語言同一句:`pragma: no error-handling`(py 用 `#`、js 用 `//` 註解)。 |
| 新增語言 | 完整 SOP:`docs/ADDING_LANGUAGE_SUPPORT_SOP.md`(R8 前置閘 + 逐層 checklist + 校準協議)。 |

## 7.6 Adversarial Quality Layer (v2.9.0+)

工具維度量「結構存在」不量「語意正確」(`except BaseException` 反而給
error_handling 加分),是 tts-new 過 Gate 4 後仍有 50 bugs 的根因。v2.9 加四層
對抗式防護。完整說明:`docs/ADVERSARIAL_QUALITY_LAYER.md`。

| 機制 | 規則 |
|------|------|
| error_handling 質化 | scorer = `100×(handled/total) − 5×anti_patterns`;反模式:`except BaseException`(即使 re-raise)、bare except 無 re-raise、broad swallow(`except Exception: pass`)、JS empty catch。窄型 except-pass 不罰。 |
| Reliability preflight | `preflight_reliability_lint`(P4+ blocking):vendored `py_reliability.yaml`(subprocess/requests 無 timeout、time.sleep in async、mkstemp 無 try/finally、TOCTOU、create_task unreferenced)。 |
| Config liveness preflight | `preflight_config_liveness`(P4+ blocking):代碼讀的 env key(`os.getenv`/`process.env`)必須在 `.env.example`/compose/deployment/README 宣告;否則 orphan(測試走 default 永遠綠的死設定)。無宣告來源 → skip。 |
| 架構風險測試觸發 | `derive_test_cases.md` Step 1b:SAD 模組特徵強制 NP-13/15/07(不靠 SRS 關鍵字),case 落 `tests/integration/`,D4 + mirror gate 自動 enforce。 |
| **adversarial_review(Gate 3)** | framework-owned 維度(threshold 100, weight 0, `requires_tool_execution: false`)。讀 `.methodology/bug_hunt_report.json`,confirmed critical/high 未 resolved/refuted → BLOCK。協議:`hunt_bugs.md`;靶向:`bug-hunt-targets` CLI。**hunt 只在 Gate 3 跑一次**,用與開發不同的模型。 |
| 不重複報告 | 靜態可確定的(preflight 已攔)不進 hunt;mutation survivors 作為 hunt 輸入(`bug_hunt_targets.json`),非獨立 gate 項。 |

## 8. Agentic Trajectory Tracing (v2.7.0+)

Harness emits OpenTelemetry spans for preflight/postflight execution. Spans land in `.harness/traces/agent_trajectory.jsonl` — one JSON object per line, time-travel-debuggable offline.

**Activation**: automatic when `PhaseHooks` is instantiated with a valid `project_path`. No configuration required.

**Dependencies** (already in `pyproject.toml`):
```
opentelemetry-api>=1.20.0
opentelemetry-sdk>=1.20.0
```

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
| 2 | `community_cohesion` | Architecture base score — framework-owned (`crg_independent.py`) | `harness_bridge.finalize_gate` |
| 3 | ~~`flow_coverage`~~ | **Removed** — `error_handling` is now `ast-error-handling` (file-level try/except) | `tool_runners.py` |
| 4 | `find_large_functions` (≥500 lines) | **Phase 1 gatekeeper** — architecture_score = cohesion − (count×5, cap 20). Subprocess path: always deterministic. | `crg_independent.run_independent_crg` |
| 5 | `get_hub_nodes` (fan_in≥15) | Architecture findings HIGH (evidence only) | `harness_bridge._crg_enrich_gate_findings` |
| 6 | `refactor_tool(dead_code)` | Architecture findings MEDIUM if >10 items (evidence only) | `harness_bridge._crg_enrich_gate_findings` |
| 7 | `get_review_context` | `crg_review_context` injected into gate_result.json | `harness_bridge._crg_enrich_gate_findings` |
| 8 | `get_impact_radius` | `crg_impact_radius` injected into gate_result.json | `harness_bridge._crg_enrich_gate_findings` |
| 9 | `get_affected_flows` | `crg_affected_flows` injected into gate_result.json | `harness_bridge._crg_enrich_gate_findings` |
| 10 | `get_knowledge_gaps` | test_coverage findings MEDIUM (untested critical paths) | `harness_bridge._crg_enrich_gate_findings` + `prepare_gate` |
| 11 | `list_flows` (criticality) | error_handling findings LOW + `crg_critical_flows` in gate_result | `harness_bridge._crg_enrich_gate_findings` |
| 12 | `query_graph(tests_for)` | **Phase 2 gatekeeper** — test_coverage score -= (untested_hubs×3, cap 15). MCP path; CRG mandatory. | `harness_bridge._crg_enrich_gate_findings` |
| 13 | `semantic_search(fr_id)` | TDD-RED prompt enriched with related existing code | `harness_cli._build_fr_step_prompt` |
| 14 | `generate_wiki_tool` | .code-review-graph/wiki/ auto-generated on P3+ advance | `harness_cli.cmd_advance_phase` |
| 15 | `list_graph_stats_tool` | Graph health displayed in run-gate preflight | `harness_cli.cmd_status` |
| 16 | `suggested_questions` | Auto-seed issue registry | `crg_reconnaissance.md` |

### 9.3 Key CRG MCP Tools

| Tool | Use |
|------|-----|
| `build_or_update_graph` | Gate 3/4 entry, post-edit auto-update |
| `get_minimal_context` | Tier 3 per-dim context (~100 tokens) |
| `detect_changes` | Pre-fix safety, post-round drift |
| `get_hub_nodes` | Architecture findings enrichment (fan_in≥15 → HIGH issue) |
| `list_communities` / `get_community` | Cohesion scoring |
| `get_knowledge_gaps` | test_coverage findings (untested hotspots → MEDIUM issue) |
| `query_graph(tests_for)` | test_coverage findings (untested hub functions → HIGH issue) |
| `semantic_search_nodes` | TDD-RED prompt enrichment (related existing code) |
| `find_large_functions` | Architecture findings enrichment (≥300 lines → WARN issue) |
| `refactor_tool(dead_code)` | Architecture findings enrichment (>10 dead items → MEDIUM issue) |
| `get_review_context` | finalize-gate: `crg_review_context` in gate_result.json |
| `get_impact_radius` | finalize-gate: `crg_impact_radius` in gate_result.json |
| `get_affected_flows` | finalize-gate: `crg_affected_flows` in gate_result.json |
| `list_flows` | finalize-gate: error_handling context + `crg_critical_flows` |
| `list_graph_stats` | run-gate preflight graph health display |
| `generate_wiki_tool` | cmd_advance_phase P3+: auto-generate .code-review-graph/wiki/ |

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

*harness-methodology v2.9.0 — Academic Benchmark 91/100*
