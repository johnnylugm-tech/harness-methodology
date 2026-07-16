# SAD — Harness Methodology v2.7.0 (As-Built — Audit: 2026-06-12)

> **Sync guarantee**: This document is reverse-engineered from the live codebase.
> Any change to the code **must** be reflected here, and vice-versa.
> Verification: an engineering team can rebuild the repo from this document alone.
>
> **Audit history**:
> - **2026-05-16** — Initial reverse-engineering pass.
> - **2026-06-12** — Full SAD ↔ code alignment audit. ~95% match rate.
>   - 20 forward drifts fixed in this revision (code is the source of truth — see diff lines
>     flagged with `v2.7 audit 2026-06-12` comments).
>   - 5 stale `.bak` files detected in `core/quality_gate/`, `enforcement/`, `kill_switch/`,
>     `steering/` (all older revisions; safe to delete, listed in §10 Open Items).
>   - 1 latent bug found during audit: `AuditLogger.log_event` in
>     `kill_switch/audit_logger.py:39` used a positional-only signature, but
>     `InterruptEngine._log_event` calls it with kwargs (would raise `TypeError`
>     when an external audit logger is injected via `KillSwitch(audit_logger=...)`).
>     **Fixed in subsequent commit `91733db`** — see §8.5.4.
>   - 3 reverse-direction items deferred for human review (see §8.5.1: R-1
>     `check-checklist`, R-2 `should_auto_approve_gate4`, R-3 `scripts/CLAUDE.md`).

---

## 1. Architectural Drivers

The architecture is driven by five strict non-functional requirements defined in the SRS.

### Driver 1 — Reviewer Independence (NFR-4)
- **Requirement**: Eliminate confirmation bias from "AI reviewing itself."
- **Decision**: `ReviewerRouter` dispatches Agent B as a **stateless Claude sub-agent** with a distinct
  reviewer persona and structured checklist. Independence comes from session isolation (Agent B has
  zero access to Agent A's reasoning) and tool-enforced scoring (`score.py R4`: `score == tool_score`),
  not from a separate model vendor. (v3.0 removed the earlier Hermes/Gemini MCP backends.)

### Driver 2 — Traceability & Auditability (NFR-3, NFR-6)
- **Requirement**: Every development step and decision must be traceable and post-hoc auditable.
- **Decision**: 8-Phase Pipe-and-Filter macro architecture with standardized artifacts flowing between stages. `quality_manifest.json` (gate results) and `DecisionLogWriter` (YAML per-decision) are the audit trail backbone.

### Driver 3 — Reliability & Reproducibility (NFR-1)
- **Requirement**: Quality assessment must be stable and not over-dependent on LLM stochasticity.
- **Decision**: Three mechanisms:
  1. **Pure tool-based scoring**: `score = tool_score` — LLM is not used for scoring. Tier 1/2 `tool_score=null` is rejected by score.py R8; evaluation is SUSPENDED until the tool is installed.
  2. **CRG integration**: Code Review Graph provides graph-theory-based reproducible structural metrics for architecture/error-handling dimensions.
  3. **Tool pre-flight**: `init-project` blocks on missing Tier 1 tools; `run-gate` checks tools before printing the evaluation prompt — prevents LLM self-evaluation via tool-absent fallback.

### Driver 4 — Security (NFR-2)
- **Requirement**: AI agents must be blocked from destructive operations or vulnerability introduction.
- **Decision**: Standalone `KillSwitch` module (circuit-breaker pattern) + Pre-fix Impact Analysis inside the Gate improvement loop (`CRGBridge.check_impact` before each fix round).

### Driver 5 — Maintainability (NFR-5)
- **Requirement**: The framework itself must be easy to understand, extend, and maintain.
- **Decision**: Lazy-Loading Factory (in parent-system CLI) and Bridge pattern (in `harness/`). Lazy loading decouples subsystems; Bridge separates core workflow from quality-gate implementations and CRG tooling.

---

## 2. Macro Architecture & Design Patterns

### 2.1 8-Phase Pipe & 4-Gate Filter

The system uses this macro architecture:

- **Pipe**: 8 software development phases (P1–P8) form the main pipeline.
- **Filter**: 4 quality gates intercept at specific phase exits:
  - **Gate 1**: Per-FR check at P3/P4/P5/P7/P8 (trigger: `per_fr_completion`)
  - **Gate 2**: Phase exit at P3 end (trigger: `phase_exit`, phase: 3)
  - **Gate 3**: Phase exit at P4 end (trigger: `phase_exit`, phase: 4)
  - **Gate 4**: Phase exit at P6 end (trigger: `phase_exit`, phase: 6)
- `harness/harness_bridge.py` is the gate lifecycle controller implementing this filter logic.

### 2.2 Key Design Patterns

| Pattern | Applied In | Purpose |
|---|---|---|
| Lazy-Loading Factory | parent-system CLI | Deferred subsystem init across 30+ modules |
| Bridge Pattern | `harness/` directory | Decouple methodology flow from quality tools |
| Façade Pattern | `harness_cli.py` (standalone) | Minimal harness-only CLI facade |
| Dispatcher | `harness/reviewer_router.py` | Decompose + route review tasks to Claude sub-agents |
| Circuit Breaker | `kill_switch/` | Safety backstop independent of main flow |
| Graceful Degradation | `harness/crg_bridge.py` | All CRG methods no-op if CRG not installed |
| LLM-as-Judge | `steering/steering_loop.py` | Objective A/B output evaluation via LLM (default: NoopProvider; requires `STEERING_PROVIDER_TYPE=anthropic` to activate) |
| Auto-Fix Engine | `core/auto_fix/` | Proactive detect→classify→auto_fix→verify→loop with human escalation guardrails |

### 2.3 CLI Architecture: Single Entry Point

| Entry Point | File | Scope | Runnable Standalone |
|---|---|---|---|
| **Harness CLI** | `harness_cli.py` | Only uses modules present in this repo (`core/`, `harness/`) | ✅ Runnable standalone |

The full-system CLI (`cli.py`) lives in the parent system that contains harness-methodology as a sub-component. It requires 30+ external modules (`progress_dashboard`, `gantt_chart`, `sprint_planner`, `enterprise_hub`, `steering`, etc.) and is not part of this repository. Any work within harness-methodology uses `harness_cli.py`.

**`harness_cli.py` commands** (39 sub-commands + `main`):

The original 33-command spec list was expanded during the v2.6 → v2.9 development window
to add traceability + check-constitution paths. The current `harness_cli.py` has 39
`cmd_*` functions registered as sub-commands:

**Spec-mandated (31 present in code, 1 removed from earlier spec — see REMOVED below):**
```
python harness_cli.py plan-phase        --phase 3 [--project .] [--output plan.md]
python harness_cli.py plan-all          [--project .] [--output plan.md]
python harness_cli.py run-phase         --phase 3 [--project .]
python harness_cli.py pre-commit-check  --phase 3 [--project .]   # git hook only (FSM/constitution/kill-switch)
python harness_cli.py run-gate          --gate 2 --phase 3 [--project .] [--fr-id FR-01] [--skip-preflight] [--delta]
python harness_cli.py finalize-gate     --gate 2 --phase 3 [--project .] [--fr-id FR-01] [--no-git]
python harness_cli.py run-env-check     [--project .] [--phase N] [--fr-id FR-XX]
python harness_cli.py finalize-env-check [--project .]   # A2: re-verifies cli_tools/env_vars claimed-present (PATH / os.environ); claimed-but-missing → BLOCK
python harness_cli.py generate-next-plan [--project .] [--phase N]
python harness_cli.py push-checkpoint   --phase 1|2 [--project .] [--fr-ids FR-01,FR-02] [--no-git]
python harness_cli.py manifest          --fr-ids FR-01 FR-02 [--sad SAD.md] [--no-git]
python harness_cli.py status            [--project .] [--json] [--full]
python harness_cli.py effort            [--phase 3] [--project .]
python harness_cli.py reload-policy     [--policy-file enforcement/enforcement.json]
python harness_cli.py run-gap-analysis  [--project .] [--spec SPEC.md] [--similarity 0.6]
python harness_cli.py audit-phase       --phase 3 --repo owner/repo [--branch main]
                                        [--output markdown|json] [--save FILE]
python harness_cli.py verify-spec       [--project .] [--fix]  # --fix shows suggestions (no auto-fix)
python harness_cli.py check-logic       [--project .] [--srs SRS.md]
python harness_cli.py init-project      --project /path/to/target [--phase 3] [--overwrite] [--ci-only]
                                        # Step 10/10: checks all Tier 1 gate tools (ruff,mypy,pytest-cov,gitleaks,scancode,mutmut); BLOCKS if any missing
python harness_cli.py advance-phase     --completed N [--project .] [--force]  # --force bypasses CV-2 FSM check
python harness_cli.py kill-switch       trigger|reset|status [--project .] [--reason "..."]
python harness_cli.py push-milestone    --type p3-mid|p3-pre-gate2|p4-mid|p4-pre-gate3|p5-baseline|p7|p8 [--project .] [--fr-ids FR-01,FR-02] [--fr-done N] [--fr-total N] [--no-git]
python harness_cli.py dispatch          --role developer|reviewer --fr-id FR-01 --prompt "..." [--phase 3] [--project .] [--timeout 300] [--max-turns 20]
python harness_cli.py verify-agent-b-approvals --phase N [--fr-ids FR-01,FR-02] [--project .]
python harness_cli.py audit-structure   [--project .] [--json]
python harness_cli.py spec-coverage-check  [--project .] [--threshold N] [--fr-id FR-XX]  # D4 unified (v2.6.0); replaces removed check-test-inventory
python harness_cli.py gate4-tag         [--project .] [--fr-id FR-XX] [--no-git]
python harness_cli.py load-context      [--project .] [--output FILE]
python harness_cli.py run-fr-step       --fr-id FR-XX [--phase N] [--project .]
python harness_cli.py resume-fr-phase   --fr-id FR-XX [--phase N] [--project .]
```

**Spec drift — REMOVED** (was in earlier revisions, not implemented in current code):
- ~~`check-checklist` — verify phase plan mandatory items [x]~~ — no `cmd_check_checklist` function, no sub-parser.

**Spec drift — ADDED** (added since the 33-command baseline; in code, not in earlier spec text):
```
python harness_cli.py bug-hunt-targets        --project . [--json]            # v2.9 — surface bug-hunt candidates
python harness_cli.py crg-arch-check          --project . [--json]            # v2.8 — CRG architecture check
python harness_cli.py check-test-spec-consistency --project . [--strict]      # v2.9 — FR↔test spec consistency
python harness_cli.py check-test-mirrors-spec --project . [--strict]          # v2.9 — test-mirrors-spec check
python harness_cli.py check-constitution      --project . [--phase N]          # v2.9 — on-demand constitution pre-check
python harness_cli.py migrate-trace-overlay   --project .                      # v2.9 — overlay migration helper
python harness_cli.py build-trace-attestation --project . [--output FILE]      # v2.9 — PR 3 trace attestation writer
python harness_cli.py verify-trace            --project . [--strict]            # v2.9 — CI verifier for trace attestation
```

**Gate evaluation (two-phase)**: `run-gate` prepares context and prints evaluation instructions; Claude evaluates inline and writes `.sessi-work/gate{N}_result.json`; `finalize-gate` reads the result and checks thresholds. SSI assets are embedded in `harness/ssi/`.

**Manual step-by-step flow per phase**:
```
[phase.1]   plan-phase         — Generate execution plan
[phase.2]   run-phase          — FSM + kill-switch + artifacts + drift + SAB + traceability
[phase.3]   Gate 1 per-FR      — Per-FR quality gate evaluation (phases 3,4,5,7,8)
[phase.4]   Phase exit gate    — Composite gate evaluation (G2 at P3, G3 at P4, G4 at P6)
[phase.5]   Phase Truth        — HR-11 ≥ 90% verification (built into advance-phase)
[phase.6]   advance-phase      — Advance FSM, HANDOVER.md, commit, push
```

M1 kill-switch circuit state is checked before each phase. M3 gap analysis runs for phases ≥ 3.

**Gate BLOCKED diagnostic** (`finalize-gate` exit 1): The command emits a structured per-dimension diagnosis on block. Output includes: composite score, open_critical/high counts, per-failing-dimension score/threshold/gap and a fix hint, passing dimension summary, auto-fix round count (if auto-fix ran), and copy-pasteable resume commands. Full report written to `.methodology/last_block.md`. Fix hints cover all 14 dimension names: `linting`, `type_safety`, `test_coverage`, `security`, `secrets_scanning`, `license_compliance`, `mutation_testing`, `architecture`, `readability`, `error_handling`, `documentation`, `performance`, `integration_coverage`, `test_assertion_quality`. Implemented in `_format_block_diagnostic()` (module-level helper in `harness_cli.py`); the dict `_DIMENSION_HINTS` maps dimension name → actionable fix string.

**Auto-fix wiring — partially removed**: the main preflight (`cmd_run_phase`) and postflight (`cmd_finalize_gate`) wirings, plus the `_run_auto_fix_loop` driver, were mostly removed after end-to-end verification proved the strategies cannot clear the checks they target — emitting empty stubs or appending comments. Preflight/postflight failures now generally block honestly. However, **one exception remains fully wired**: `fix_missing_traceability` is dispatched via `_dispatch_trace_auto_fix` during preflight for a bounded attempt. `core/auto_fix` (engine + 5 guardrails) is retained for this specific workflow — see §3.18.

**ECC hooks (Claude Code session layer)**: `~/.claude/hooks/hooks.json` runs ECC hooks across all Claude Code sessions. Harness-provided hooks:
- `pre:bash:dispatcher` — blocks `git --no-verify` (prevents HR violation from bypassing hooks), push reminders
- `stop:cost-tracker` — tracks token/cost per session

Installation: `bash scripts/setup-ecc-hooks.sh` (or `init-project` auto-checks).
(The former `preflight_ci_readiness()` advisory re-check was removed in 減法 T2 —
it could never block and cost a network round-trip on every push; `init-project`
remains the setup/verification point.)

**GitHub branch protection (server-side — bypass-proof)**: `init-project` auto-detects `gh` CLI
and configures branch protection on `main` (block force pushes + deletions, no PR requirement
per the direct-push model). Without `gh`, prints a manual setup guide. Configured and
verified at `init-project` time (the per-push advisory re-check was removed in 減法 T2).
This is the **only truly bypass-proof** layer — even `--no-verify` + missing ECC hooks
cannot bypass GitHub's server-side enforcement.

**TDD mandate** (SKILL.md §3): The Sub-Agent must follow the **RED → GREEN → IMPROVE** cycle per FR before returning results.

- **[TDD-1 RED]** Write a failing test first (`tests/test_frNN.py`). Commit before any implementation: `git commit -m "test(RED): failing test for FR-NN"`. `harness_cli.py finalize-gate` enforces **D1-RED**: the test file's git commit timestamp must predate the source file's commit timestamp; the gate blocks if implementation was committed first. D1-RED is bypass-proof — timestamps are read from `.git/` directly, not from agent-reported metadata.
- **[TDD-2 GREEN]** Implement the FR until the test passes: `git commit -m "feat(FR-NN): GREEN"`. Every implemented function must carry a `[FR-NN]` docstring tag and a `Citations:` block with line-number references to SRS.md and SAD.md (HR-15).
- **[TDD-3 IMPROVE]** Refactor without breaking tests: `git commit -m "refactor(FR-NN): IMPROVE"` if any changes were made.

Gate 1 `test_coverage` dimension verifies outcomes; implementations without prior failing tests score lower on `mutation_testing`.

**Three server-side enforcement mechanisms** (bypass-proof — operate at GitHub Actions layer, not git hooks):

| Mechanism | CLI command | State written | CI job |
|-----------|-------------|---------------|--------|
| Push-milestone sentinel | `push-milestone --type <type>` | `state.json:.last_milestone_command` | `push-milestone-enforcement` — blocks push to `main` if P3+ and field absent |
| Agent B approval gate | `verify-agent-b-approvals --phase N` | `.methodology/agent_b_approvals/FR-XX.json` (per-FR, `review_status=APPROVE`, `docs_embedded=[SRS.md,SAD.md]`, `reason` ≥40 chars, non-empty `citations[]`) | `agent-b-approval-check` (A1 guard) — blocks push if any FR missing APPROVE **or** an APPROVE carries no review rationale / `citations[]` (shell-approval guard; same structural check mirrored in `phase_auditor` C3) |
| P8 archive check | `push-milestone --type p8` (pre-flight in CLI) | `.methodology-archive/` directory + HANDOVER.md clean | `p8-archive-check` — blocks push if archive absent or HANDOVER references Phase 9 |

These three mechanisms were added to address the class of failures where an agent uses `git push --no-verify` to bypass local hooks. GitHub Actions CI cannot be bypassed by hook-skip flags. `init-project --ci-only` installs all three jobs into the target project's `.github/workflows/harness_quality_gate.yml`.

---

### 2.4 Conformance Matrix (RFC 2119)

This section uses normative language per **RFC 2119**:

| Keyword | Meaning |
|---------|---------|
| **MUST** / **SHALL** | Absolute requirement. Violation = conformance failure. |
| **SHOULD** / **RECOMMENDED** | Expected behavior. May be violated with documented rationale. |
| **MAY** / **OPTIONAL** | Truly optional; implementation may choose either way. |

#### 2.4.1 Hard Rules (HR-01 ~ HR-15)

| Rule | RFC 2119 | Enforcement Module | Verification |
|------|----------|--------------------|-------------|
| HR-01: A≠B (dispatched as separate sub-agent sessions) | **SHOULD** (workflow) | `harness_cli.py` dispatch & `PhaseTruthVerifier` | Validated by `PhaseTruthVerifier` via `sessions_spawn.log` (verifies JSONL format and A/B reviewer separation for phases 1, 2, and 6). |
| HR-02: Cannot skip phases | **MUST** | `harness_cli.py` FSM | `state.json` phase ordering |
| HR-03: Kill switch blocks dispatch | **MUST** | `kill_switch/kill_switch.py` | `kill_switch.status()` check before dispatch |
| HR-04: HybridWorkflow mode=ON for P2+ | **MUST** | `core/hybrid_workflow.py` | `HybridWorkflow.mode` / `should_review()` (mode != OFF) |
| HR-05: P2 must exist before P3+ | **MUST** | `core/quality_gate/phase_artifact_enforcer.py` | `quality_manifest.json` existence |
| HR-06: No secrets in codebase | **MUST** | `harness/gate_configs/` (`secrets_scanning` dim) | `gitleaks` scan at Gate 2/3/4 (threshold 100 = zero leaks) |
| HR-07: Constitution score ≥ phase threshold | **MUST** | `core/quality_gate/constitution/runner.py` | `run_constitution_check()` |
| HR-08: Gate must pass before phase advance | **MUST** | `harness/harness_bridge.py` | `finalize_gate()` threshold check |
| HR-09: Claims verifier checks A/B authenticity | **MAY** | `core/quality_gate/stage_pass_generator.py` (standalone script only) | `verify_sessions_spawn_log()` — not wired into the active FSM/finalize-gate path |
| HR-10: `sessions_spawn.log` entries required | **MUST** | `core/quality_gate/phase_truth_verifier.py` | `PhaseTruthVerifier` checks for existence and JSONL validity. Log must not be empty. |
| HR-11: Phase Truth ≥90% | **MUST** | `core/quality_gate/phase_truth_verifier.py` | `PhaseTruthVerifier.verify()` |
| HR-12: A/B review ≤5 rounds | **MUST** | `steering/steering_loop.py` | Round counter in iteration loop |
| HR-13: Auto-fix timeout enforcement | **MUST** | `core/auto_fix/__init__.py` | `check_escalation()` HR-13 condition |
| HR-14: No integrity violations after auto-fix | **MUST** | `core/auto_fix/guardrails.py` | `post_fix_drift_check()` |
| HR-15: Citations must include line numbers | **MUST** | `detection/ensemble_scorer.py` / `pattern_matcher.py` / `steering` | Grep confirmation in review |
| HR-16: `gate_score_overrides` acts as a Threshold Floor | **MUST** | `core/quality_gate/sab_parser.py` | No automated override; fix code, re-architect, or escalate |

#### 2.4.2 Gate Pass Criteria

| Gate | Phase | Score Threshold | Dimensions | RFC 2119 |
|------|-------|----------------|------------|----------|
| Gate 1 (per-FR) | P3+ | Per-dim: linting ≥90, type_safety ≥85, test_coverage ≥80 | 3 (linting, type_safety, test_coverage) | **MUST** pass for each FR |
| Gate 2 (P3 exit) | P3 | ≥75 (composite) | 9 dimensions | **MUST** pass before P4 |
| Gate 3 (P4 exit) | P4 | ≥80 (composite) | 14 dimensions (incl. 4 tier3) | **MUST** pass before P5 |
| Gate 4 (P6 full) | P6 | ≥85 (composite) | 14 dimensions | **MUST** pass before release |

#### 2.4.3 Phase Entry / Exit Conformance

| Transition | RFC 2119 | Condition |
|------------|----------|-----------|
| P1 → P2 | **MUST** | SRS.md + SPEC_TRACKING.md + TRACEABILITY_MATRIX.md + TEST_INVENTORY.yaml exist |
| P2 → P3 | **MUST** | SAD.md + quality_manifest.json + TEST_SPEC.md exist; `plan-phase --phase 3` succeeds |
| P3 → P4 | **MUST** | Gate 2 ≥75 + Phase Truth ≥90% + all FRs have Gate 1 PASS |
| P4 → P5 | **MUST** | Gate 3 ≥80 + coverage ≥80% + TEST_RESULTS.md |
| P5 → P6 | **MUST** | VERIFICATION_REPORT.md |
| P6 → P7 | **MUST** | Gate 4 ≥85 + QUALITY_REPORT.md |
| P7 → P8 | **SHOULD** | RISK_STATUS_REPORT.md + RISK_REGISTER.md |
| Phase advance | **SHALL NOT** | Skip phases; each phase MUST complete before next |

#### 2.4.4 Auto-Fix Escalation Conditions

Per SAD.md §3.18, the AutoFixEngine **MUST** escalate to human (HUMAN_REQUIRED) when any of 9 conditions trigger. Five conditions are checked in `check_escalation()`; the remaining four (hardcoded secrets, hard rule violations, kill-switch OPEN, Gate 4 BLOCKED) are checked in `_human_condition_for()` during the `HUMAN_REQUIRED` classification path. The conditions are **MUST**-level normative requirements for safe autonomous operation.

### 2.5 GitHub Integration & Automation Layer

Full integration guide: **[INTEGRATION.md](INTEGRATION.md)**. Summary:

| Mechanism | File | Context | Purpose |
|---|---|---|---|
| **GitHub Actions CI** | `.github/workflows/harness_ci.yml` | This repo (framework self-test) | Mutation testing (median-3, threshold ≥70, requires `pytest.mark.mutation_oracle` scoped testing via `setup.cfg`) + `pytest tests/` on push/PR to `main` |
| **Git Hooks installer** | `scripts/setup-git-hooks.sh` | Target project | Installs `prepare-commit-msg` (block commit), `post-merge` (warn), `pre-push` (block push) keyed on `.methodology/state.json` `current_phase`. Skips checks for `chore(harness):` commits |
| **Drift Monitor cron** | `scripts/cron_drift_monitor.py` | Target project (crontab) | ~~Hourly architecture drift detection; alert via log / email / Slack. Path via `DRIFT_PROJECT_PATH` env var~~ **REMOVED** (減法 T4) |
| **On-demand scripts** | `scripts/*.py` | Target project | FR audit, phase audit, spec compliance, FR mapping — see INTEGRATION.md §3.4 |

**Key rule**: `setup-git-hooks.sh` must run inside the target project (not inside this repo). Hooks call `core.quality_gate` — the `core/quality_gate/` module must be importable from the target project root (submodule, pip, or copy).

---

## 3. Detailed Module Design

> **v2.8.0 — Language toolchain layer**: `harness/toolchains/` (ToolSpec registry,
> DIMENSION_TOOLS per language, detection + state.json persistence),
> `harness/lang_scanners/` (in-process scanners: `python_ast.py`, `treesitter_js.py` —
> same output schemas per dimension so scorers are shared), and
> `core/utils/lang_patterns.py` (single source for source/test file patterns and the
> state.json language reader; core-side because core must not import harness).
> Gate YAML `tool:` fields stay Python defaults; non-python projects resolve
> dimension→tool via the registry. R8 completeness is enforced by
> `tests/test_toolchain_registry.py`. Adding a language: `docs/ADDING_LANGUAGE_SUPPORT_SOP.md`.

### §3.1 — `harness/harness_bridge.py` — Gate Controller & Bridge

**Responsibility**: Manages quality gate lifecycle. Core bridge between the methodology workflow and the embedded SSI evaluation skill.

**Architectural note**: SSI is a Claude Code skill — Claude IS the evaluation engine. Gates use a two-phase API instead of subprocess IPC. SSI assets are embedded in `harness/ssi/`.

**Data structures** (all in this file):

```python
@dataclass
class DimResult:
    name: str
    score: float
    threshold: float
    issues: list[dict] = field(default_factory=list)

@dataclass
class GateResult:
    gate_num: int
    score: float
    dimensions: list[DimResult] = field(default_factory=list)
    open_critical: int = 0
    open_high: int = 0
    quality_complete: bool = False
    rounds_used: int = 0

@dataclass
class GateContext:
    gate_num: int; config: dict; project_root: str; phase: int; fr_id: str | None
    ssi_scripts_dir: str; ssi_prompts_dir: str; ssi_schemas_dir: str; work_dir: str
    auto_fix_rounds: int = 0
    sab_data: dict = field(default_factory=dict)     # architecture constraints, high-risk modules, NFR dim mapping from quality_manifest.json
    tier3_context: dict = field(default_factory=dict) # CRG Point 2 per-dimension structural context for Tier 3 evaluation
    def evaluation_prompt(self) -> str: ...  # Returns evaluation instructions for Claude

    # Note: code uses `GateConfig | dict` where `GateConfig` is a `@dataclass` defined
    # in `core/quality_gate/constitution/profile.py` matching the gate YAML schema
    # (§5.1). GateConfig is not re-exported — callers see dict at the harness_cli level.

class GateBlockedError(Exception):
    def __init__(self, gate_num: int, result: GateResult): ...
    # message: "Gate {n} BLOCKED — score={:.1f}, critical={c}, high={h}"
```

**Public API** (two-phase gate evaluation):

```python
class HarnessBridge:
    def __init__(self):
        self.crg = CRGBridge()       # graceful degradation if unavailable
        self._log = DecisionLogWriter()
        self._effort = EffortTracker()

    def prepare_gate(
        self,
        gate_num: int,
        project_root: str,
        phase: int,
        fr_id: str | None = None,
        auto_fix_rounds: int = 0,
    ) -> GateContext:
        """Phase 1: load config, CRG recon, return context for Claude evaluation."""

    def finalize_gate(self, ctx: GateContext) -> GateResult:
        """Phase 2: read gate{N}_result.json, check thresholds, update manifest."""

    def generate_quality_manifest(self, fr_ids: list[str], sad_path: str) -> Path: ...
```

**`prepare_gate` execution order**:
1. `_load_config(gate_num)` — loads `harness/gate_configs/gate{n}_*.yaml` via PyYAML
2. If `config["crg"]["reconnaissance"]` is set → `self.crg.run_reconnaissance(project_root)`
3. Resolves `ssi_dir = Path(__file__).parent / "ssi"` (embedded assets)
4. Creates `.sessi-work/` work directory
5. Returns `GateContext` with all paths and config

**`finalize_gate` execution order**:
1. Reads `.sessi-work/gate{N}_result.json` (raises `FileNotFoundError` if missing)
2. Parses JSON into `GateResult` (accepts both `overall_score`/`score` and `open_critical_count`/`open_critical` field names)
3. `self._update_quality_manifest(gate_num, fr_id, result)` — writes to `.methodology/quality_manifest.json`
4. Applies framework overrides (traceability/CRG). Override helpers return `tuple[list[DimResult], bool]`; when the `bool` is `True`, caller sets the local `_crg_overrides_applied` flag. When that flag is `True`, the overall composite score is dynamically recomputed via weighted-average from the corrected dims rather than using the agent-reported value.
5. `self._effort.record(EffortRecord(phase, gate_num, "GATE", "gate_finalize", ...))`
6. `self._log.write(DecisionLogEntry(... decision="GATE_PASS"|"GATE_BLOCK" ...))`
7. **Blocking logic**:
   - Gate 1: `raise GateBlockedError` if any `d.score < d.threshold` in `result.dimensions`
     **OR** `result.score < _gate1_score_gate` (per-gate composite floor — added as a
     defensive tightening; not in earlier spec revisions). `_gate1_score_gate` is loaded
     from `harness/gate_configs/gate1_per_fr.yaml` if defined, else falls back to 0.
   - Gate 2/3/4: `raise GateBlockedError` if `result.score < config["score_gate"]` OR `not result.quality_complete`
8. Gate 4 only: Runs validation checks to ensure quality completeness.
9. Return `GateResult`

**Result file contract** (`.sessi-work/gate{N}_result.json`):
```json
{
  "overall_score": 85.5,
  "meets_target": true,
  "quality_complete": true,
  "open_critical_count": 0,
  "open_high_count": 0,
  "breakdown": {
    "linting": {"score": 92.0, "threshold": 90.0, "passed": true, "issues": []}
  }
}
```
Schema: `harness/ssi/schemas/harness_gate_result.schema.json`

**`_require_hermes_approve`** (Gate 4 only):
- **REMOVED in v2.6.0** — Gate 4 no longer blocks on Hermes APPROVE.

**`generate_quality_manifest` logic**:
- Called at P2 exit
- Calls `from scripts.generate_sab import parse_sad` — functional (added in fix ②+⑤)
- `parse_sad` returns `{nfr_dim_map, constraints, high_risk, ...}` from SAD.md SAB block
- **NFR enforcement (`sab_parser`)**: `nfr_traceability` `type` → real gate dimension via
  `HarnessBridge._nfr_type_to_dim(nfr_type: str) -> str` (asymmetric, keyword-substring match).
  The full mapping table (as implemented in `harness/harness_bridge.py:2346-2366`):

  | NFR type keyword (substring) | Maps to dimension |
  |---|---|
  | `performance` / `latency` / `throughput` / `response` | `performance` |
  | `security` / `auth` / `access control` / `encryption` | `security` |
  | `reliability` / `availability` / `uptime` / `recovery` | `reliability` |
  | `deploy` / `deployability` / `docker` / `container` / `rollout` | `deployability` |
  | `maintainability` / `modularity` / `extensibility` | `maintainability` |
  | `test` / `coverage` / `quality` | `test_coverage` |
  | `traceability` / `tracking` / `audit` | `traceability` |
  | `clarity` / `documentation` / `readability` | `clarity` |
  | (default fallback) | `correctness` |

  Note: this mapping is **asymmetric** (NFR dim name ≠ gate dim name in several rows) and uses
  keyword-substring matching rather than a fixed enum. Mapped dimensions auto-derive
  `gate_score_overrides` (threshold floor, only-raise) via `derive_gate_score_overrides()`;
  the fallback `correctness` dimension has no scoring tool → effectively `advisory_only`.
  `_load_manifest_sab` feeds `gate_score_overrides` into `finalize_gate` (applied as a
  non-waivable floor).
- Writes JSON to `.methodology/quality_manifest.json` with schema:
  ```json
  {
    "schema_version": "1.0",
    "generated_at_phase": 2,
    "fr_ids": [...],
    "nfr_dimension_mapping": {"NFR-01": "performance", ...},
    "nfr_fr_mapping": {"NFR-01": ["FR-04", "FR-05"], ...},
    "nfr_traceability": {"NFR-01": {"type": "performance", "target": "p95 < 3s", "module": "app.pipeline"}, ...},
    "architecture_constraints": [],
    "high_risk_modules": [],
    "gate_score_overrides": {},
    "gate_results": {
      "gate1": {},
      "gate2": null,
      "gate3": null,
      "gate4": null
    }
  }
  ```

**`_update_quality_manifest` logic**:
- Reads existing `.methodology/quality_manifest.json`
- Gate 1 (with `fr_id`): `manifest["gate_results"]["gate1"][fr_id] = payload`
- Gate 2/3/4: `manifest["gate_results"]["gate{n}"] = payload`
- `payload = {score, quality_complete, rounds_used, open_critical, open_high}`

---

### §3.2 — `harness/reviewer_router.py` — Reviewer Proxy (v3.0)

**Responsibility**: Routes review requests to a **Claude sub-agent** (single backend, no MCP
dependencies). Supports **dependency-ordered decomposition** of large/complex tasks with
**parallel-wave A/B execution** (`_execute_parallel_waves`, a `ThreadPoolExecutor`): subtasks
with no pending dependencies form a wave and run **concurrently**; each wave completes before
the next dependent wave begins (a single subtask is just a one-item wave). Setup requires only
the `claude` CLI — no env vars.

> **v3.0 change**: The earlier Hermes MCP → Gemini CLI MCP → sub-agent priority chain was
> removed. Score integrity is tool-enforced (`score.py R4`: `score == tool_score`), so the
> reviewing LLM never affects gate scores. Collapsing to Claude-only removes two external MCP
> dependencies and the `HERMES_REVIEWER_TARGET`/`REVIEWER_CHAIN` configuration burden.

**Module-level constants** (all overridable via env vars):

| Constant | Env Var | Default | Purpose |
|---|---|---|---|
| `TASK_SIZE_THRESHOLD` | `TASK_SIZE_THRESHOLD` | `2000` | Chars above which task is auto-decomposed |
| `SUBTASK_MAX_SIZE` | `SUBTASK_MAX_SIZE` | `800` | Target chars per subtask (paragraph fallback) |
| `MAX_CONTEXT_LINES` | `MAX_CONTEXT_LINES` | `6` | Approved-subtask summaries injected as context |

```python
@dataclass
class SubTask:
    content: str          # subtask text
    label: str            # e.g. "Phase 3", "FR-01", "§3.2", "para_1"
    dependencies: list[str] = field(default_factory=list)  # other labels this depends on
    index: int = 1        # 1-based position in execution order
    total: int = 1        # total subtask count

def get_reviewer_model(phase: int, role: str = "reviewer") -> str:
    """All phases use the Claude sub-agent."""
    return "claude"
```

**Public API**:

```python
class ReviewerRouter:
    def __init__(self, project_path: "Path | None" = None):
        # No backend configuration required — Claude sub-agent is the sole reviewer.

    def review(
        self,
        role: str,
        prompt: str,
        phase: int,
        fr_id: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict:
        # Returns: {"review_status": "APPROVE|REJECT", "confidence": 0-1,
        #           "violations": [], "summary": "",
        #           "_reviewer_used": "subagent",
        #           "_stopped_at": str,        # label of REJECT subtask (multi-subtask only)
        #           "_completed_subtasks": int,
        #           "_total_subtasks": int}
```

**Dispatch** (`_try_chain`):
```
1. If cancel_event is set (sibling REJECTed in same wave) → return CANCELLED.
2. AgentSpawner.spawn(model="claude") — stateless sub-agent, always returns a result
   (lazy import to avoid circular dep). Emergency fallback APPROVE@0.3 only if spawn raises.
```

**Task decomposition** (`_decompose_with_deps`):
```
IF len(prompt) <= TASK_SIZE_THRESHOLD (2000 chars):
    → no decomposition, returns [SubTask(prompt, label="full")]

Detection pipeline (tried in order):
  1. _extract_phase_sections(): regex "Phase N" / "PX" headers (SRS.md style)
  2. _extract_fr_sections(): FR-XXX boundary split
  3. _extract_heading_sections(): §X.Y / "X.Y Title" numbered headings (SAD.md style)
  4. _paragraph_subtasks(): fallback — paragraph split targeting SUBTASK_MAX_SIZE (800 chars)
                            sequential deps: para_N depends on para_(N-1)

Dependency graph (_build_dep_graph):
  - Cross-reference scan: if label B appears in label A's content → A depends on B
  - Implicit phase ordering: Phase-N → depends on Phase-(N-1)

Execution order (_topological_sort):
  - Kahn's algorithm (BFS on in-degree=0 nodes)
  - Cycle-safe: remaining nodes appended in original order if cycle detected
  - Result: list[SubTask] in dependency-safe execution order with index/total set
```

**`review()` execution sequence** (v2.1+ — parallel-wave A/B with dep-ordered execution):
```python
subtasks = self._decompose_with_deps(prompt, role)  # topologically sorted

if len(subtasks) == 1:
    # Single subtask — no wave coordination needed
    return self._try_chain(role, subtasks[0].content, phase, fr_id, timeout_ms,
                           task_idx=1, task_total=1)

# Multi-subtask path: parallel waves
return self._execute_parallel_waves(subtasks, role, phase, fr_id, timeout_ms)
    # Waves = groups of subtasks with no pending inter-dependencies.
    # Within a wave, subtasks run concurrently via ThreadPoolExecutor.
    # Each wave completes before the next dependent wave begins.
    # A REJECT in any subtask of a wave → cancel_event → sibling subtasks
    # return CANCELLED → early exit (`wait=False` semantics).
    # `approved_context` (last MAX_CONTEXT_LINES=6 ✅ summaries) is lazily
    # captured inside `_submit_subtask` closure to avoid an "eager snapshot"
    # bug — summaries from sibling threads are visible to subsequent waves.
    # When a wave's approved_context is being mutated by sibling threads,
    # a per-`ReviewerRouter` lock serialises the write.
```

**`_build_prompt(role, prompt, phase, fr_id=None, task_idx=1, task_total=1) -> str`**:
```
[Harness Reviewer | Phase {phase}{ | FR {fr_id}}]
Role: {role}

{prompt}

Output JSON: {"review_status": "APPROVE|REJECT", "confidence": 0-1, "violations": [], "summary": ""}
```

**`_parse_response(raw: str) -> dict`**:
- Extracts first JSON object via `re.search(r"\{.*\}", raw, re.DOTALL)`
- On success: `json.loads(match.group())`
- On failure: `{"review_status": "REJECT", "confidence": 0.0, "violations": ["parse_error"], "summary": raw[:200]}`

**Backend**: Claude sub-agent only (`_try_subagent` → `AgentSpawner.spawn(model="claude")`).
No MCP imports — the module has no optional external dependencies.

---

### §3.3 — `harness/crg_bridge.py` — Deterministic Analysis Bridge

**Responsibility**: Wraps CRG MCP tools (`mcp__code_review_graph__*`) for structural analysis. All interaction via direct MCP tool calls. Gracefully degrades if MCP tools not available in runtime (returns empty dicts / False).

**Module-level availability** (set at import time):
```python
_CRG_AVAILABLE = True  # True if mcp__code_review_graph__* imports succeed
```

**Public API**:

| Method | Implementation | Return |
|---|---|---|
| `_check_available() -> bool` | Returns module-level `_CRG_AVAILABLE` (set at import time) and warns once on failure | bool (no per-instance cache; immutable global) |
| `run_reconnaissance(project_root) -> dict` | `_crg_build(full_rebuild=True)` + reads `.sessi-work/crg_reconnaissance.json` | dict or {} |
| `get_minimal_context(project_root, dimension) -> dict` | `_crg_minimal_context(task=dimension)` | dict or {} |
| `check_impact(project_root, ref="HEAD", threshold=0.7) -> bool` | `_crg_detect_changes()` — `risk_score >= threshold` | bool |
| `check_drift(project_root, threshold=0.4, base="HEAD~1") -> bool` | `_crg_detect_changes(detail_level="minimal")` — `risk_score >= threshold` (does NOT read crg_metrics.json) | bool |
| `load_metrics(project_root) -> dict` | reads `.sessi-work/crg_metrics.json` | full metrics dict (6 formula-driven signals) |

**Environment dependency**:
- The `SSI_ROOT` env var / `_ssi_root()` helper that earlier spec revisions described
  is **not implemented** in the current `crg_bridge.py`. Instead, the bridge falls back
  to `harness.crg_api` (in-process import of CRG tool functions) when the direct MCP
  backend (`mcp__code_review_graph__*`) is unavailable. SSI assets remain embedded
  at `harness/ssi/` (set by `Path(__file__).parent / "ssi"` in `harness_bridge.py`),
  consumed inline during the two-phase gate flow rather than as a subprocess cwd.

**Graceful degradation**: If `_check_available()` is `False`, all methods return `{}` or `False` immediately.

**CRG integration points** (§6.5):
1. **Point 1 — Structural Reconnaissance** (Gate 3/4 entry): `prepare_gate()` calls `run_reconnaissance` — builds CRG graph, seeds structural data.
2. **Point 2 — Tier 3 Guidance** (before each Tier 3 eval): `prepare_gate()` calls `get_minimal_context` for each Tier 3 dimension; results exposed via `GateContext.tier3_context` and surfaced in `evaluation_prompt()`.
3. **Point 3 — Pre-fix Safety Gate** (before each improvement round): `HarnessBridge.check_pre_fix_safety()` — calls `check_impact`; defers fix if risky.
4. **Point 4 — Post-round Drift Check** (after each improvement round): `HarnessBridge.check_post_round_drift()` — calls `check_drift`; triggers revert protocol if structural drift > threshold.

---

### §3.4 — `harness/decision_log.py` — Decision Audit Log Writer

**Responsibility**: Write per-decision YAML audit entries. Implements the traceability requirement (NFR-3/NFR-6).

**Data structures** (refactored in `quality-improvement-round-3`):

```python
@dataclass
class DecisionContext:
    """Groups identity/time metadata to reduce DecisionLogEntry parameter count."""
    agent_id: str
    phase: int
    fr_id: str | None = None
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

@dataclass
class DecisionLogEntry:
    ctx: DecisionContext
    decision: str           # "APPROVE" | "REJECT" | "GATE_PASS" | "GATE_BLOCK" | "REVIEWER_REJECT"
    reasoning: str
    scores: dict[str, float] = field(default_factory=dict)  # e.g. {"gate_score": 87.5}
    metadata: dict = field(default_factory=dict)

    # Backward-compat properties
    @property
    def agent_id(self): return self.ctx.agent_id
    @property
    def phase(self): return self.ctx.phase
```

**`DecisionLogWriter`**:

```python
class DecisionLogWriter:
    def __init__(self, log_root: str = ".methodology/decision_logs"):
        self.log_root = Path(log_root)

    def write(self, entry: DecisionLogEntry) -> Path:
        # Output: log_root/{YYYY-MM-DD}/{agent_id}_{phase}_{seq:03d}.yaml
        # seq = count of matching files in today's dir + 1

    def read_phase(self, phase: int) -> list[dict]:
        # Returns all YAML entries matching *_{phase}_*.yaml (recursive glob)
```

- Creates parent directories automatically.
- YAML serialized via `dataclasses.asdict(entry)` → nested: `ctx: {agent_id, phase, fr_id, trace_id, timestamp}`.
- Callers access phase/agent from deserialized dict as `data["ctx"]["phase"]` (not flat `data["phase"]`).
- Falls back to `json.dumps` if PyYAML not installed.

---

### §3.5 — `harness/effort_tracker.py` — Gate Effort Metrics

**Responsibility**: SQLite-backed gate effort tracking for performance monitoring.

**Data structures**:

```python
@dataclass
class EffortRecord:
    phase: int
    agent_id: str
    operation: str          # "gate_run" | "tier1_eval" | "tier3_eval" | "fix_round" | "review"
    duration_s: float
    gate_num: int | None = None
    token_in: int = 0
    token_out: int = 0
    fr_id: str | None = None
    # Note: created_at is NOT a dataclass field — managed by SQLite DEFAULT (datetime('now'))
```

**`EffortTracker`**:

```python
class EffortTracker:
    def __init__(self, db_path: str = ".methodology/effort_metrics.db"):
        # Auto-creates DB and schema on init

    def record(self, r: EffortRecord) -> None:
        # INSERT into SQLite table `effort` (not `effort_records`)

    def summary(self, phase: int | None = None) -> dict:
        # Returns: {total_operations, total_duration_s, total_tokens}
        # If phase given: delegates to query_phase_summary(phase)

    def query_phase_summary(self, phase: int) -> dict:
        # Per-operation breakdown: {operation: {duration_s, total_tokens}}

    def query_gate_summary(self, gate_num: int) -> dict:
        # {runs, total_duration_s, total_tokens}
```

- DB + schema created lazily on first use (`_ensure_db()` invoked from
  `record()` / `summary()` / `query_*()`). `__init__` only stores the db path
  and sets `_initialized = False` — this avoids polluting the project root
  with `.methodology/effort_metrics.db` when the tracker is constructed but unused.
- SQLite table name: **`effort`** (not `effort_records`).
- Schema: `(id, phase, gate_num, agent_id, operation, duration_s, token_in, token_out, fr_id, created_at)`

---

### §3.6 — `harness/issue_tracker_ext.py` — FR-Tagged Issue Tracker

**Responsibility**: Extends `software_self_improvement`'s `IssueTracker` with per-FR tagging. Addresses Gap G5. Refactored in `quality-improvement-round-3` to use `FindingData` parameter object.

**Import guard** (inline stub if SSI not installed):
```python
try:
    from software_self_improvement.scripts.issue_tracker import IssueTracker
except ImportError:
    class IssueTracker:  # minimal stub
        def __init__(self): self._issues: list[dict] = []
        def add_finding(self, dimension, severity, file, line, message, evidence) -> str: ...
        def open_issues(self) -> list[dict]: ...
```

**`FindingData` (parameter object, reduces `add_finding` parameter count)**:
```python
@dataclass
class FindingData:
    dimension: str
    severity: str
    file: str
    line: int
    message: str
    evidence: str
    fr_id: str | None = None
```

**`IssueTrackerExt(IssueTracker)`**:

```python
def add_finding_data(self, data: FindingData) -> str:
    # Primary method — calls super().add_finding(), then tags issue with data.fr_id

def add_finding(
    self, dimension, severity, file, line, message, evidence,
    fr_id: str | None = None,
) -> str:
    # Legacy compatibility wrapper — delegates to add_finding_data(FindingData(...))

def get_findings_by_fr(self, fr_id: str) -> list[dict]:
    # Returns open issues where fr_id in issue["fr_ids"]

def fr_coverage_summary(self) -> dict[str, int]:
    # Returns {fr_id: open_finding_count} — only includes FRs that HAVE open findings
    # (FRs with zero findings are absent from the result dict)
    # No `fr_ids` argument — auto-aggregates from all open issues
```

> **Removed in `quality-improvement-round-3`**: `fr_saturation_check(fr_id, current_finding_ids, threshold)` — removed entirely. Tests that relied on it are skipped.

---

### §3.7 — `core/agent_spawner.py` — Agent Spawner

**Responsibility**: Spawns developer and reviewer agents via the Claude Code headless CLI.
Applies need-to-know isolation: each agent receives only its persona + current-phase SOP + task.

**Round 12 站0 additions**: (a) `preflight_substrate()` — one ≤90s probe spawn proving the spawned environment can actually execute `python3 -m pytest` / `git commit --dry-run` / a runtime-assembled canary, dispatched with the SAME mcp_config/setting_sources/permission_mode as real pipeline dispatches; wired at `run-phase` entry for per-FR phases (`cli/phase_cmds._run_substrate_probe`, FATAL exit 24 before the per-FR loop, 6h success cache, `--skip-substrate-probe` escape). (b) `_extract_dispatch_error()` — failed-spawn error capture prefers the CLI result-JSON error, then startup-banner-denoised stderr, then stdout tail (the banner had buried the real cause in 76/461 production log entries). (c) `_UNATTENDED_PREAMBLE` appended via `--append-system-prompt` on every spawn — measured `--setting-sources` semantics ("" → no CLAUDE.md memory; "project" → project AND the user's global CLAUDE.md BOTH load) mean interactive-collaboration rules can leak into headless agents; the system-prompt layer neutralizes them. `_STRUCTURAL_FAILURE_SIGNATURES` is now an empty registry (mechanism retained; the connectors banner was disproven as a failure cause).

**File layout helpers** (module-level):
```python
def _load_persona(role: str) -> str:
    # Reads agent_personas/{ROLE.upper()}.md — empty string if missing

def _load_phase_sop(phase: int) -> str:
    # Reads docs/P{phase}_SOP.md — empty string if missing
```

**`AgentSpawner`**:

```python
class AgentSpawner:
    def spawn(
        self,
        role: str,
        prompt: str,
        context: dict,
        model: str = "claude",   # always 'claude' — only Claude CLI supported
        task_timeout: int = 300,
        phase: int = 0,
        fr_id: str | None = None,
        # --- extra kwargs (v2.x additions, default sensibly) ---
        max_turns: int = 20,
        phase_sop_override: str | None = None,   # bypass docs/P{N}_SOP.md auto-load
        persona_override: str | None = None,     # bypass agent_personas/{ROLE}.md
        mcp_config: str | None = None,           # --mcp-config flag
        setting_sources: str = "",               # --setting-sources flag
        permission_mode: str = "acceptEdits",    # --permission-mode flag
    ) -> dict: ...
```

**`spawn()` routing**:
```
All spawns → claude -p --output-format json --no-session-persistence ...
             raises RuntimeError if claude CLI not found on PATH
```
Reviewer A/B dispatch goes through `ReviewerRouter.review()`, which itself calls back into
`AgentSpawner.spawn(model="claude")` for each subtask (single backend, all phases).

**`_build_prompt(role, prompt, context, phase) -> str`** — constructs:
```
[PERSONA]
{persona content}

[SOP]
{phase SOP content}

[TASK]
{prompt}

[CONTEXT]
  {key}: {value}   # excludes "phase" key
```

**`_parse_result(result) -> dict`**: If result is already dict, return as-is; else `{"output": str(result), "status": "complete"}`.

---

### §3.8 — `core/phase_hooks.py` — Phase Execution Hooks Framework

**Responsibility**: Pre-flight, monitoring, and post-flight hooks for agent phase execution.

**`PhaseHooks(project_path: str, phase: int = None, enable_kill_switch: bool = True, drift_threshold: float = 85.0)`**:

File paths used:
- `.methodology/state.json` — FSM state
- `.methodology/run-phase.log` — append-only run log
- `docs/` — for constitution checks

**Pre-flight hooks** (`preflight_all() -> dict` calls all eleven):

| Method | Check | Blocks if |
|---|---|---|
| `preflight_fsm_check()` | reads `state.json` | state in `{"FREEZE", "PAUSED"}` or phase regression |
| `preflight_bvs_phase_order()` | runs `constitution.bvs_runner.BVSRunner` to check phase prerequisites | HR-03 phase prerequisites violated |
| `preflight_constitution(check_mode="preflight")` | calls `quality_gate.constitution.run_constitution_check` | violations found |
| `preflight_kill_switch()` | verifies M1 kill-switch is operational | skipped if `enable_kill_switch=False` |
| `preflight_previous_phase_artifacts()` | runs `PhaseArtifactRegistry.verify_phase_chain()` for ASPICE traceability | P2+ only; blocks if previous phase artifacts missing |
| `preflight_drift_detection()` | runs M2 `DriftDetector.detect_all()` (SAD + spec + phase + SAB) | ensemble score < `drift_threshold` |
| `preflight_sab_check()` | validates SAB.json layer integrity, module presence | P3+ only; blocks if SAB.json missing or violations found |
| `preflight_traceability()` | runs `check_spec_trace.check_traceability()` for FR→code→test coverage | P3 info-only, P4+ blocks if gaps exist |
| `preflight_tool_registry()` | checks `ToolRegistry.list_tools()` | skipped if not installed |
| `preflight_fr_spec_consistency()` | cross-checks each FR's [FR-XX] docstring/code references against SAD.md | never blocks (advisory only) |
| `preflight_reliability_lint()` | v2.9 A2 — checks error-handling pattern coverage, exception-use hygiene | blocks if score below phase threshold |
| `preflight_config_liveness()` | v2.9 A3 — re-verifies cli_tools/env_vars claimed-present at runtime (PATH / os.environ); claimed-but-missing → BLOCK | blocks if any claimed tool/var missing |

**Monitoring hooks** (append to `self.monitoring_events` + write to `run-phase.log`; M1 kill-switch circuit check on before_* calls):

| Method | Signature | Records |
|---|---|---|
| `monitoring_before_dev` | `(fr_id, agent_id="agent-a")` | `{"type": "before_dev", "fr_id": ..., "agent_id": ...}` |
| `monitoring_after_dev` | `(fr_id, result=None, agent_id="agent-a")` | `status`, `confidence` from result |
| `monitoring_before_rev` | `(fr_id, agent_id="agent-b")` | `{"type": "before_rev", "fr_id": ..., "agent_id": ...}` |
| `monitoring_after_rev` | `(fr_id, result=None, agent_id="agent-b")` | `review_status`, `status`, `confidence` |
| `monitoring_hr12_check` | `(fr_id, iteration, max_iterations=5)` | Returns `False` if `iteration >= max_iterations` |

**Post-flight hooks** (`postflight_all() -> dict` calls all seven):

| Method | Action |
|---|---|
| `postflight_constitution()` | Re-runs constitution check with `check_mode="postflight"` |
| `postflight_bvs_invariants()` | Re-runs BVS invariants from sessions_spawn.log |
| `postflight_steering_summary()` | Generates summary of LLM-as-Judge steering operations |
| `postflight_drift_check()` | Re-runs M2 drift detection; blocks if score < `drift_threshold` |
| `postflight_artifact_links()` | Verifies ASPICE traceability (current phase artifacts cite predecessors) |
| `postflight_update_state(success=True)` | Advances `state.json` current_phase if `self.phase > old_phase` |
| `postflight_summary()` | Returns `{total_frs, approved, fr_results, monitoring_events}` |

**Success condition for `postflight_all`**: `constitution.passed AND bvs_invariants.passed AND drift check passed AND artifact_links passed AND all FR results have review_status == "APPROVE"`.

**OpenTelemetry span wrapping** (v2.7.0+): `PhaseHooks` lazily imports `core.observability.init_tracer` during `__init__` and stores it as `self.tracer`. Both `preflight_all()` and `postflight_all()` wrap their execution in a named OTel span (`phase_{N}_preflight` / `phase_{N}_postflight`) with `phase` and `all_passed`/`success` attributes. Tracing is a no-op when `core.observability` is unavailable (import guarded by `try/except`).

---

### §3.9 — `core/hybrid_workflow.py` — Smart-Routing Workflow

**Responsibility**: Three-mode workflow controller that auto-routes between single-agent and A/B review based on change size/type.

**Enums & dataclasses**:

```python
class WorkflowMode(Enum):
    OFF = "off"        # Single agent, no review
    HYBRID = "hybrid"  # Smart routing
    ON = "on"          # Forced A/B review always

class ChangeType(Enum):
    SMALL = "small"
    LARGE = "large"

@dataclass
class ChangeAnalysis:
    type: ChangeType
    lines_changed: int
    files_affected: int
    is_security_related: bool
    is_new_feature: bool
    reason: str
```

**`HybridWorkflow(mode=HYBRID, small_change_threshold=10, large_change_threshold=30)`**:

```python
def analyze_change(self, diff: str) -> ChangeAnalysis:
    # Security keywords: ['auth','password','token','permission','security']
    # New-feature keywords: ['def new_','class new_','# new']
    # LARGE if: is_security OR is_new_feature OR total_changes > large_threshold
    # SMALL if: total_changes < small_threshold
    # SMALL otherwise (medium change auto-passes)

def should_review(self, analysis: ChangeAnalysis) -> bool:
    # OFF  → False (auto-approve)
    # ON   → True  (always review)
    # HYBRID → True if ChangeType.LARGE, False if SMALL

def execute(self, diff: str, code_func: Callable) -> dict:
    # Returns {"status": "needs_review", ...} or {"status": "auto_approved", "result": ..., ...}

def get_stats(self) -> dict:
    # Returns {total_tasks, auto_approved, review_required, auto_approve_rate, review_rate}
```

---

### §3.10 — `core/subagent_isolator.py` — Need-to-Know Isolation

**Responsibility**: Enforces On-Demand / Need-to-Know isolation for subagent spawning. Each subagent receives a fresh message context with only the artifacts relevant to its task.

**Key classes**:

```python
@dataclass
class ArtifactSpec:
    path: str
    role: str          # "input" | "output" | "reference"
    required: bool = True
    description: str = ""

@dataclass
class SubagentContext:
    task: str
    role: str
    artifacts: List[ArtifactSpec]
    persona_prompt: str
    metadata: Dict[str, Any]
    messages: List[Dict]    # Always fresh (enforced empty at creation)
    isolation_id: str       # SHA-256[:16] of {task, role}
```

**`SubagentIsolator`**:

```python
class SubagentIsolator:
    def create_context(task, role, artifacts, persona_prompt, metadata) -> SubagentContext:
        # messages=[] enforced — no prior conversation history

    def validate(ctx) -> None:
        # Raises ArtifactValidationError if any required input artifact missing

    def validate_outputs(ctx) -> dict:
        # Returns {complete, produced, missing} after subagent completes

    def verify_isolation(ctx) -> None:
        # Raises IsolationViolationError if messages[] is non-empty

    def spawn(task, role, artifacts, persona_prompt, metadata, validate=True) -> dict:
        # High-level: create_context → validate → return to_spawn_config()

    def release(isolation_id) -> None:
        # Free context reference after subagent completes
```

**Convenience factory**:
```python
def create_isolated_spawn(task, role, input_paths, output_paths, persona_prompt) -> dict:
    # One-shot: build + validate + return spawn config
```

---

### §3.11 — (removed) `core/verification_gate.py`

Removed in Round 9 站0: the GateRemediationReport / `_GATE_THRESHOLDS` module had zero production importers (its HANDOVER crash-recovery wiring never landed), making its per-gate thresholds a zombie configuration surface — editing them changed nothing.

---

### §3.12 — `steering/` — AB Workflow Steering Engine

**Responsibility**: LLM-as-judge A/B iteration control. Drives the AB Workflow component referenced by the full-system CLI. Used when two candidate outputs must be compared and converged toward a winner.

#### `steering/steering_loop.py` — Core Iteration Engine

**Key classes**:

```python
class IterationStage(Enum):
    EXPLORATION = "exploration"   # first N rounds, free competition
    COMPETITION = "competition"   # middle rounds, score differences emerge
    CONVERGENCE = "convergence"   # final rounds, converging to winner

@dataclass
class SteeringConfig:
    max_iterations: int = 5
    min_iterations: int = 3
    exploration_rounds: int = 2
    convergence_threshold: float = 0.05   # delta < this = converged
    quality_threshold: float = 0.85
    weights: Dict[str, float] = {
        "quality": 0.4, "efficiency": 0.2, "clarity": 0.2, "consistency": 0.2
    }

class LLMJudgeScorer:
    # Lazy CRG cache — avoids one CRGBridge instantiation per score() call
    _crg_bridge: Any = None   # set on first successful import of crg_bridge
    _crg_tried: bool = False  # True after first attempt (prevents repeated import errors)

    def score(output_a, output_b) -> {"A": {scores}, "B": {scores}}:
        # Dimensions: correctness, completeness, consistency, concision, maintainability
        # Fallback: pessimistic 0.5 across all dims on parse failure

    def score_with_critic_debate(output_a, output_b) -> {"A": {scores}, "B": {scores}}:
        # Multi-round debate: critic identifies gaps → A defends → B defends → consensus decider rescores
        # Consumes .methodology/runtime_trace.json (pytest failure trace) once if present
        # Falls back to score() on any LLM failure

    def generate_feedback(output_a, output_b, scores_a, scores_b, winner) -> dict:
        # Returns: {winner_advantages, loser_improvements, actionable_guidance}
```

**Module-level constants** (dynamic activation thresholds):
```python
DEBATE_DELTA_THRESHOLD = 0.15      # any module: trigger debate when score delta < this
SENSITIVE_DEBATE_THRESHOLD = 0.30  # sensitive modules: trigger debate when delta < this
```

**`SteeringLoop(provider, config=None, history_path="", project_root: Optional[Path] = None)`**:

```python
def iterate(output_a, output_b, changed_modules: Optional[List[str]] = None) -> IterationResult:
    # 1. Update stage (EXPLORATION → COMPETITION → CONVERGENCE)
    # 2. Score both outputs; if _should_activate_debate() → score_with_critic_debate()
    # 3. Compute weighted total (quality_score*w["quality"] + clarity_score*w["clarity"] + consistency_score*w["consistency"] + efficiency_score*w["efficiency"])
    # 4. Determine winner, update best_output

    # 5. Generate feedback
    # 6. Compute convergence score (avg delta of last 3 rounds)
    # 7. Persist history to .methodology/steering_history.json
    # → Returns IterationResult

def _should_activate_debate(initial_delta: float, changed_modules: Optional[List[str]]) -> bool:
    # True if delta < DEBATE_DELTA_THRESHOLD (any module), OR
    # True if any changed module path contains a SENSITIVE_MODULES prefix
    #   AND delta < SENSITIVE_DEBATE_THRESHOLD
    # Sensitive prefixes: steering/, enforcement/, core/auto_fix/, core/fsm/

def should_continue() -> (bool, str):
    # Returns False if: max_iterations reached, quality_threshold met,
    # or CONVERGENCE stage + delta <= convergence_threshold
    # Resolves HR-12 conflict: "no >5 meaningless iterations" via early stop

def run_until_converge(get_next_pair_fn, max_rounds=None) -> IterationResult:
    # Drives iteration loop until should_continue() is False
```

**Three defects fixed in current implementation**:
- Defect A: Scoring was fake (hard-coded 0.5) → replaced with LLM-as-judge
- Defect B: Efficiency logic inverted → fixed to quality/tokens ratio
- Defect C: Convergence logic inverted → delta < threshold means converged (not diverged)

#### `steering/integrations.py` — Integration Adapters **(REMOVED)**

**Responsibility**: Connects `SteeringLoop` to BVS, Constitution, CQG, and HR-12 enforcement systems.

| Class | Integrates With | Key Method |
|---|---|---|
| `SteeringBVSIntegrator` | BVS Runner (HR-03 phase invariants) | `check_phase_invariants(steering_result, context)` |
| `SteeringConstitutionIntegrator` | Constitution Checker (HR-07/09/15) | `check_output_compliance(output, phase)` |
| `SteeringCQGIntegrator` | CQG code quality checker | `measure_code_quality(output) -> {quality, complexity, readability}` |
| `HR12Resolution` | HR-12 conflict resolver | `should_stop(current_round, score_delta) -> (bool, reason)` |
| `SteeringIntegrator` | Unified facade | `iterate_with_full_check(output_a, output_b, run_bvs=True, run_constitution=True, run_cqg=False) -> (IterationResult, [IntegrationResult])` |

> **Implementation note**: `SteeringIntegrator.bvs_integrator` and
> `SteeringIntegrator.should_continue` are exposed as Python `@property` attributes, not
> plain methods as some earlier spec revisions described. Callers read them as
> `integrator.bvs_integrator` / `integrator.should_continue`, not `integrator.bvs_integrator()`.
> Behaviour is otherwise equivalent to the documented semantics.

**Import behavior**: Constitution module imports (`from constitution.bvs_runner`, `from constitution.citation_parser`, `from constitution.verification_constitution_checker`) are available directly. The top-level `constitution/` package (v2.0.0) provides the full API including BVS, claims verification, invariant engine, and compiled constitution. Graceful degradation via try/except is preserved for environments where optional BVS dependencies are unavailable.

**HR-12 Resolution**:
- HR-12 says "no >5 rounds of ineffective iteration" (negative constraint)
- `SteeringLoop.max_iterations=5` is positive upper bound
- Not contradictory: `should_continue()` terminates early when convergence met, satisfying HR-12 intent without contradicting the cap

#### `steering/__init__.py` — Package Re-exporter

Re-exports all public classes from `steering_loop` and `integrations` so callers can use `from steering import SteeringLoop, SteeringIntegrator` without knowing the submodule layout.

```python
__all__ = [
    "SteeringLoop", "SteeringConfig", "IterationStage",
    "ScoredOutput", "IterationResult", "LLMJudgeScorer",      # from steering_loop
    "SteeringBVSIntegrator", "SteeringConstitutionIntegrator",  # from integrations
    "SteeringCQGIntegrator", "SteeringIntegrator", "HR12Resolution",
]
```

---

### §3.13 — `kill_switch/` — Agent Safety Kill Switch

**Responsibility**: Circuit-breaker safety system. Monitors agent health, trips circuit on threshold violation, issues interrupt events, and logs all actions for audit.

**Module structure** (10 files, all relative imports — self-contained):

| File | Class/Purpose |
|---|---|
| `kill_switch.py` | `KillSwitch` — main facade |
| `circuit_breaker.py` | `CircuitBreaker` — trip/reset logic |
| `health_monitor.py` | `HealthMonitor` — per-agent metric collection |
| `interrupt_engine.py` | `InterruptEngine` — interrupt event lifecycle |
| `state_manager.py` | `StateManager` — persistent agent state (killed/active) |
| `audit_logger.py` | `AuditLogger` — interrupt audit trail |
| `models.py` | `InterruptEvent`, `MonitorConfig`, `HealthMetrics`, `CircuitBreakerState` — dataclasses |
| `enums.py` | `CircuitState`, `KillReason`, `KillSwitchEventType`, `InterruptOutcome` — enumerations |
| `exceptions.py` | `InterruptInProgressError` |
| `__init__.py` | Package exports |

**`KillSwitch` public API**:

```python
class KillSwitch:
    def __init__(self, audit_logger=None, state_manager=None):
        # Composes: HealthMonitor, CircuitBreaker, StateManager, InterruptEngine

    def start_monitoring(agent_id: str, config: MonitorConfig = None) -> None:
        # Starts health monitoring + initializes circuit for agent

    def stop_monitoring(agent_id: str) -> None

    def is_agent_circuit_open(agent_id: str) -> bool:
        # True if state_manager.is_agent_killed OR circuit_breaker.is_open

    def get_agent_state(agent_id: str) -> CircuitState

    def manual_trigger(agent_id, reason, operator_id) -> InterruptEvent:
        # Immediate human-initiated kill

    def evaluate_and_trigger(agent_id, config: MonitorConfig) -> bool:
        # Auto-evaluation: check metrics → record failure → open circuit if threshold exceeded
        # Returns True if interrupt was triggered

    def re_enable(agent_id, operator_id, acknowledgment) -> bool:
        # Clear killed state, reset circuit, stop monitoring
        # Returns True if successfully re-enabled (or already active)

    def get_interrupt_history(agent_id=None, limit=100) -> List[InterruptEvent]
```

**`MonitorConfig` fields** (from `kill_switch/models.py`):
- `agent_id: str`
- `failure_threshold: int` — consecutive failures before circuit trips
- `cooldown_seconds: int` — circuit open duration before half-open retry

---

### §3.14 — `enforcement/` — Policy Enforcement Framework

**Responsibility**: Enforces behavioral policies on agents and commits. 7 files covering hook installation, constitution-as-code enforcement, policy evaluation, and server-side enforcement.

| File | Class | Purpose |
|---|---|---|
| `agent_proof_hook.py` | `AgentProofHook` | Git commit-msg hook that agents cannot bypass; validates commit message task ID format |
| `constitution_as_code.py` | `ConstitutionAsCode` | Constitution rules expressed as executable Python checks |
| `constitution_policy_sync.py` | `ConstitutionPolicyGenerator` | Synchronizes policy definitions with constitution document |
| `execution_registry.py` | `ExecutionRegistry` | Registry tracking all executed enforcement actions |
| `framework_enforcer.py` | `FrameworkEnforcer` | Main enforcement orchestrator; coordinates all enforcement subsystems |
| `policy_engine.py` | `PolicyEngine` | Evaluates named policies against runtime context |

**`AgentProofHook` key behavior**:
- Installs to `.git/hooks/commit-msg` (thin wrapper) + `.methodology/agent_hook_core.py` (core logic)
- Core logic: validates commit messages contain `[TASK-123]` pattern; detects `--no-verify` bypass attempts

- Attempts Unix immutable attribute (`chattr +i`) on hook file
- Commands: `install`, `verify`, `uninstall`

---

### §3.15 — `core/quality_gate/` — Quality Gate Implementations

**Responsibility**: Concrete quality check implementations used by the gate evaluation pipeline. Subdirectory of `core/`.

**Business logic modules:**

| File | Class | Purpose |
|---|---|---|
| `ab_enforcer.py` | `ABEnforcer` | **REMOVED**. Fully deleted from the codebase. The HR-10 log-count audit is no longer enforced via this module. |
| `phase_truth_verifier.py` | `PhaseTruthVerifier` | Verifies phase completion truth via independently-reproducible signals — framework BLOCK, pytest, coverage subprocess, previous-phase artifacts, cross-artifact checks, and `sessions_spawn.log` format/A-B coverage checks. |
| `phase_artifact_enforcer.py` | `PhaseArtifactRegistry`, `Phase` | ASPICE traceability chain enforcement; validates phase artifact dependencies (P2+) |
| `spec_tracking_checker.py` | `SpecTrackingChecker` | Tracks SPEC_TRACKING.md completeness. Delegates parsing to `parsers.SpecTrackingParser` |
| `stage_pass_generator.py` | `IntegratedStagePassGenerator` | Generates stage pass certificates; integrates FrameworkEnforcer + ClaimsVerifier |
| `feedback_hook.py` | `AutoQualityGateWithFeedback` | AutoQualityGate subclass that submits feedback on gate completion |
| `constitution/__init__.py` | — | Constitution sub-package (used by `preflight_constitution`) |
| `cross_artifact.py` | — | D3 cross-artifact fabrication detector: phase title mismatch, FR coverage mismatch, coverage number mismatch; entry point `run_cross_artifact_checks(project_root, phase)` → `{"passed": bool, "violations": [...], "checks_ran": int}`. Runs during Phase Truth verification and `finalize-gate` postflight. |
| `security_design.py` | — | Round 10: STRIDE-lite threat-model completeness check for SAD.md §6's `<!-- SEC:START/END -->` block — decidable structural rules (boundary/threat/NFR/test-existence cross-references), never keyword-density scoring (the class of check `security` was removed from constitution P1/P3/P4 scoring for — see Bug #35). Entry point `check_security_design(project, phase)` → `list[Violation]`; factory `render_canonical_security_template()`. Feature-gated by `features.security_design` (default `true`). Wired into `preflight_artifact_consistency` and `cmd_check_artifact_consistency`. |

**Support modules (stubs/config — crg-003 additions):**

| File | Purpose |
|---|---|
| `claims_verifier.py` | `ClaimsVerifier` + `ClaimsVerifyResult` — verifies sessions_spawn.log A/B role claims |
| `confidence_scorer.py` | `compute_confidence()`, `should_auto_approve_p1p2()` — script-based C1-C7 scoring (no LLM); drives HITL auto-skip for P1/P2 |

| `phase_config.py` | `PHASE_CONFIG` dict — per-phase config consumed by `IntegratedStagePassGenerator` |
| `phase_paths.py` | `PHASE_ARTIFACT_PATHS` — artifact path registry per phase |

**`parsers/` sub-package** (crg-003 refactor, v2.1 — breaks coupling with test-parsing community; extracted from `ab_enforcer.py` and `spec_tracking_checker.py`):

| File | Class | Extracted from | Methods |
|---|---|---|---|
| `parsers/spec_tracking_parser.py` | `SpecTrackingParser` | `spec_tracking_checker.py` | `has_table`, `has_update_log`, `find_entries_without_status`, `count_status` |
| `parsers/spec_assertion_parser.py` | `SpecAssertionParser` | (added v2.6 — TEST_SPEC.md assertion-level) | `parse_spec_file`, `extract_sub_assertions` |

> **Removed since v2.0.0**: `parsers/development_log_parser.py` was extracted from
> the long-since-deleted `ab_enforcer.py`. With `ab_enforcer.py` removed (see §3.15
> table), this parser was also removed. `__pycache__/` may still contain stale
> `.pyc` artifacts — these are safe to delete.

> **Design invariant**: All regex / Markdown parsing lives in `parsers/`. Checker classes contain only orchestration and business logic — zero `re.search()` calls.

---

### §3.16 — `detection/` — Anomaly & Drift Detection

**Responsibility**: Detects code drift, scoring anomalies, and suspicious patterns across evaluation rounds.

| File | Class | Purpose |
|---|---|---|
| `drift_detector.py` | `DriftDetector` | Detects structural drift between evaluation rounds; feeds `CRGBridge.check_drift` |
| `ensemble_scorer.py` | `EnsembleScorer` | Combines multiple scoring signals into ensemble score |
| `pattern_matcher.py` | `PatternMatcher` | Pattern-based detection of known anti-patterns |

---

### §3.17 — `gap_detector/` — Specification Gap Detection

**Responsibility**: Detects gaps between requirements specification and implementation. Used to surface uncovered FRs or missing artifacts.

| File | Class | Purpose |
|---|---|---|
| `detector.py` | `GapDetector` | Core gap identification logic |
| `parser.py` | `SpecParser` | Parses spec documents to extract expected coverage |
| `reporter.py` | `GapReporter` | Formats gap findings for reporting |
| `scanner.py` | `CodeScanner` | Scans codebase for coverage evidence |

### §3.18 — `core/auto_fix/` — Proactive Auto-Repair Engine (v2.6.0)

**Responsibility**: Transforms the system from detect→block→wait_for_human into detect→classify→auto_fix→verify→loop. Provides a unified AutoFixEngine that sits between detection modules and the pipeline loop. Reference: methodology-v2 SKILL.md "fail → FIX + RETRY" execution protocol.

**Design pattern**: Strategy + Circuit Breaker. Each problem type maps to a FixStrategy (AUTO_FIX / AUTO_FIX_WITH_VERIFICATION / HUMAN_REQUIRED). The engine applies fixes and re-checks, escalating to human only when 9 strict conditions are met.

| File | Class / Purpose |
|------|----------------|
| `__init__.py` | `AutoFixEngine`, `FixResult`, `FixStrategy`, `FixContext`, `EscalationCondition`; MVC integration: passes `mvc_text`/`allowed_node_name` from `segment_slicing` into fix context |
| `classifier.py` | 31-entry classification table; `classify()` → (strategy, confidence, max_rounds, problem_type) |
| `strategies.py` | 12 strategy functions in `STRATEGY_REGISTRY` (stub generation, keyword injection, test scaffolding, etc.) |
| `guardrails.py` | `pre_fix_safety_check()`, `post_fix_drift_check()`, `regression_check()`, `rollback_if_unsafe()`, `ast_mutation_guard()` |
| `runtime_tracer.py` | pytest plugin (`pytest_exception_interact` hook); captures locals from failing frames; writes `.methodology/runtime_trace.json` (consumed once by `score_with_critic_debate`) |
| `segment_slicing.py` | `extract_minimal_viable_context(file_path, error_line, crg_bridge, project_root) -> (mvc_text, allowed_node_name)`; ODD: AST-based Minimal Viable Context extraction; only activated when `error_line` is known; optional CRG integration for richer context |

**FixStrategy enum**:
- `AUTO_FIX` — fully automatic, no verification needed (e.g., missing stub generation, keyword density)
- `AUTO_FIX_WITH_VERIFICATION` — fix then re-check (e.g., constitution score, coverage, gate failures)
- `HUMAN_REQUIRED` — never auto-fix; escalate immediately (e.g., hardcoded secrets, kill-switch OPEN, Gate 4)

**Human escalation — 9 exact conditions** (all others auto-fix):
1. HR-12: >5 fix rounds exhausted
2. HR-13: Phase runs >3× estimate timeout
3. HR-14: Integrity drops below 40
4. Actual hardcoded secrets found
5. Gate score < 60 after 3 auto-fix rounds
6. Hard Rule violations (R001-R007)
7. Auto-fix confidence < 70% after 3 attempts
8. Kill-switch circuit OPEN (M1)
9. Gate 4 BLOCKED (cannot meet threshold after auto-fix rounds)

Thresholds are configurable via `AutoFixEngine.__init__` parameters:
`max_rounds=5`, `max_phase_time_multiplier=3.0`, `integrity_threshold=40.0`,
`gate_min_score=60.0`, `gate_min_rounds=3`, `confidence_threshold=70.0`.

**Integration points**:
- `harness/harness_bridge.py`: `GateContext.auto_fix_rounds` field + `prepare_gate(auto_fix_rounds=...)` parameter only set `config.max_rounds`; they do **not** invoke `AutoFixEngine.fix()` directly.
- `harness_cli.py`: Only invokes `AutoFixEngine.fix()` via `_dispatch_trace_auto_fix` during preflight to handle `missing_traceability`. Other preflight/postflight wirings were removed.
- `orchestration/__init__.py`: exports `run_constitution_check_with_feedback`, `run_enforcement_check_with_feedback`, `run_policy_check_with_feedback` — retry-aware wrappers that would delegate to AutoFixEngine, but they have **no production caller** (dead since the wirings were removed).

**`ast_mutation_guard(file_path, pre_content, post_content, allowed_node_name) -> bool`** (guardrails.py):
Validates that a fix touched only the AST node named `allowed_node_name`. Dumps invariants of all _other_ top-level nodes (FunctionDef, ClassDef, AsyncFunctionDef) and compares pre/post. For nested methods inside a ClassDef, deep-copies the parent class, strips the target method, then dumps the remainder — so decorator/base-class changes on the parent class are also caught. Returns `False` (unsafe) if: post-fix has a syntax error, or any out-of-bounds node changed. Returns `True` unconditionally for non-.py files or when `allowed_node_name` is None.

**Auto-fix loop** (the engine's intended detect→fix→re-verify shape — ONLY wired for traceability):
```
check fail → AutoFixEngine.fix() → re-check → loop (up to N rounds)
```
> The preflight/postflight wirings for general dimensions were removed after end-to-end verification
> (most strategies emit a stub/comment that never clears the substantive checks). Currently, **only
> the `fix_missing_traceability` strategy is called in production** via `_dispatch_trace_auto_fix`
> in `cmd_run_phase` preflight. The manual gate flow for other dimensions is: fix failing
> dimensions → re-run `run-gate` → `finalize-gate` (see the CASE 1–4 early-stop logic per
> gate checkpoint).
(Removed in v2.6.0: the `run-pipeline` autonomous pipeline was unstable for the current maturity level.)

### §3.21 — `scripts/check_spec_trace.py` — FR Spec Trace Validator (v2 Content-Level)

**Responsibility**: Validates bidirectional FR→code→test traceability using the `RequirementTraceability` model populated from live artifacts. Upgraded from v1 (file-existence-only) to v2 (content-level `[FR-XX]` annotation scanning). Called at P4 Gate 3 entry to enforce 100% trace coverage.

**Usage**:
```bash
python3 scripts/check_spec_trace.py --project . [--sad SAD.md] [--block] [--json] [--export report.json]
# --block: exit 1 if untraced FRs found (for CI/gate use)
# --json:  output report as JSON
```

**Checks**:
- Extracts all `\bFR-\d+\b` IDs from SAD.md
- Scans Python source for `[FR-XX]` docstring annotations (code coverage)
- Scans `tests/test_*.py` files for FR references in filename + content (test coverage)
- Reports untested AND uncoded FRs
- Populates `RequirementTraceability` model with bidirectional links

**Integration**: `PhaseHooks.preflight_traceability()` calls `check_traceability()` at preflight (P3 info, P4+ blocking). `harness_bridge.prepare_gate(gate_num=3)` triggers the `--block` variant at Gate 3 entry.

---

### §3.31 — `scripts/build_traceability.py` — ASPICE Traceability Matrix Builder

**Responsibility**: Populates the `RequirementTraceability` model from live artifacts (SAD.md, `[FR-XX]` annotations, test files) and auto-generates `TRACEABILITY_MATRIX.md` with ASPICE SWE.3 compliance reporting.

**Usage**:
```bash
python3 scripts/build_traceability.py --project . [--sad SAD.md] [--json] [--export report.json]
```

**Scan pipeline**:
1. Extract FR IDs from SAD.md (source of truth)
2. Scan Python source for `[FR-XX]` docstring annotations → FR→code mapping
3. Scan test files for FR references (filename + content) → FR→test mapping
4. Populate `RequirementTraceability` model with status per FR (VERIFIED / IN_PROGRESS / PENDING)
5. Generate `TRACEABILITY_MATRIX.md` with ASPICE SWE.3 BP1-BP3 compliance table

**Output**: `TRACEABILITY_MATRIX.md` in project root, containing:
- ASPICE compliance summary (code/test coverage %)
- SWE.3 BP1/BP2/BP3 pass/fail status
- Detailed per-FR matrix (status, code files, test files, SAD modules)
- Gap report (FRs without code, FRs without test)


### §3.32 — `core/fsm/` — FSM State Validation (v2.3)

**Responsibility**: Validates and normalizes FSM state strings for phase lifecycle management. Enforces the valid state set and provides auto-correction for deprecated states.

**Files**:

| File | Purpose |
|---|---|
| `core/fsm/__init__.py` | Package marker; re-exports `validate_fsm_state`, `is_valid_fsm_state`, `FSMError` |
| `core/fsm/fsm.py` | Core validation logic (91 lines) |

**Valid states**: `INIT`, `RUNNING`, `PAUSED`, `FREEZE`, `DONE`, `OPEN`, `HALF_OPEN`, `CLOSED`

**Deprecated auto-correction**: `ACTIVE` → `RUNNING` (when `auto_correct=True`)

**Public API**:

```python
def validate_fsm_state(state: str, auto_correct: bool = True) -> str:
    """Validate, normalize case, strip whitespace, auto-correct deprecated states.
    Raises FSMError on invalid state or non-string input."""

def is_valid_fsm_state(state: str) -> bool:
    """Boolean check — never raises. Returns False for invalid/deprecated states."""

class FSMError(Exception):
    """Raised by validate_fsm_state() on invalid input."""
```

**Integration**: Called by `harness_cli.py:_advance_fsm()` before phase advancement.


### §3.33 — `core/lifecycle_hooks.py` — Lifecycle Hook System

**Responsibility**: Symphony-inspired hook phases for phase/gate/FR lifecycle events. Hook definitions loaded from `.methodology/hooks.json` (or defaults). Each hook has a timeout and required/optional failure semantics.

**Hook events** (`HookEvent` enum):
- `BEFORE_PHASE` — before a phase starts
- `AFTER_GATE_PASS` — after a gate passes
- `ON_GATE_FAIL` — on gate failure
- `ON_ESCALATE` — on auto-fix escalation
- `AFTER_FR_COMPLETE` — after an FR completes
- `BEFORE_PHASE_ADVANCE` — before phase advance

**`HookDefinition` dataclass**: `name`, `event`, `command`, `timeout_seconds` (default 60).


### §3.34 — `core/workspace_manager.py` — Per-FR Workspace Isolation

**Responsibility**: Symphony-inspired per-FR workspace isolation with path safety enforcement. Creates isolated working directories per FR with three mandatory safety invariants:
1. Agent operates only inside its assigned workspace directory
2. Workspace path stays within workspace root (prefix check + symlink resolution)
3. Workspace key sanitized — only `[A-Za-z0-9._-]` allowed

**Public API**:

```python
class WorkspaceManager:
    def __init__(self, project_root: Path, phase: int)  # phase is required, no default
    def create_workspace(self, fr_id: str) -> Path
    def validate_path(self, path: Path, fr_id: str) -> None   # raises WorkspaceViolationError
    def cleanup_workspace(self, fr_id: str) -> None
```


### §3.35 — `core/adapters/phase_hooks_adapter.py` — PhaseHooks External Adapter

**Responsibility**: Dict-in/dict-out adapter for integrating PhaseHooks with external systems (CLI runners, MCP hooks, remote triggers) that cannot directly import from `core/`. Provides a simplified interface: callers need zero knowledge of PhaseHooks internals.

**Public API**:

```python
class PhaseHooksAdapter:
    def __init__(self, project_path: str, phase: int)
    def preflight(self) -> dict           # returns {"all_passed": bool, "checks": {...}}
    def before_dev(self, fr_id: str) -> None
    def after_dev(self, fr_id: str, result: dict) -> None
    def before_rev(self, fr_id: str) -> None
    def after_rev(self, fr_id: str, result: dict) -> None
    def hr12_check(self, fr_id: str, iteration: int) -> bool
```


### §3.36 — `steering/provider.py` — Steering LLM Provider

**Responsibility**: Factory + NoopProvider for zero-dependency steering fallback. Provides the LLM interface that `SteeringLoop.__init__` expects (`provider.chat(messages) -> str`).

**Classes**:

| Class | Purpose |
|---|---|
| `SteeringProvider` (ABC) | Abstract base defining `chat(messages: list[dict]) -> str` |
| `NoopProvider` | Zero-dependency fallback returning a structured JSON envelope: `{"A": {<5 dims @ 0.5>}, "B": {<same>}, "reason": "noop provider — LLM not configured"}`. This lets downstream scoring degrade to pessimistic tie-breaking rather than crashing on empty string. |
| `SubprocessProvider` | Subprocess-based Claude CLI provider. **Renamed from `ClaudeProvider`** (the earlier name; both refer to the same implementation). Callers should import `SubprocessProvider` directly. |

**Factory**:

```python
def create_steering_provider(provider: str | None = None) -> SteeringProvider:
    """Env-var driven factory. Defaults to NoopProvider if no provider configured."""
```

---

### §3.37 — `harness/tool_runners.py` — Independent Tool Execution Engine

**Responsibility**: Runs quality tools (ruff, mypy, pytest-cov, bandit, radon, etc.) as harness-owned subprocesses — not agent-owned — so scores cannot be fabricated by writing stub files. Results are written to `.sessi-work/harness_verification/` as an audit trail. This is the "Solution B" (independent subprocess) half of cross-validation; "Solution A" is content-level validation inside `core/quality_gate/`.

**Public API**:

```python
def run_tool(
    tool: str,
    project_root: str,
    *,
    timeout_override: Optional[int] = None,
) -> tuple[str, int]:
    """Execute tool against project_root. Returns (stdout+stderr, returncode).
    Negative return codes indicate harness-internal conditions (not tool exit codes):
      ("", -1)                 — tool is on the skip list (mutmut, scancode)
      ("TIMEOUT…", -2)         — subprocess timed out
      ("Tool not found…", -3)  — executable missing
      ("Error: …", -4)         — unexpected exception
    """

def compute_tool_score(tool: str, output: str, returncode: int) -> float | None:
    """Parse tool output and return a normalised 0.0–1.0 score.
    Returns None when the tool is skipped or unavailable."""
```

**Skip list**: `mutmut` and `scancode` are excluded from *inline subprocess* cross-validation (too slow / complex). They are **not** treated as a free pass: `HarnessBridge._run_harness_cross_validation()` requires each skip-list dimension to point at a committed, non-empty `tool_output` file that passes `_validate_tool_content()` (a real tool report, not inline agent text). A missing/empty/malformed file → BLOCK. **Data-only Exclusion**: For `mutmut`, data-only files (e.g., config, constants, Pydantic models with no logic statements) must be explicitly excluded via `paths_to_exclude` in `setup.cfg` to prevent surviving mutants from artificially diluting the kill rate.

**In-process pseudo-tools** (computed by the harness itself, not external CLIs — replace former "fake" tool mappings that re-used pytest pass-rate):
- `ast-assertions` (`test_assertion_quality`) — AST scan of test functions; `score = 100 × asserted/total`. A test with no substantive assertion (`assert` / `self.assertXxx` / `pytest.raises`) is "zero-assert" and lowers the score — what pytest pass-rate cannot detect.
- `ast-error-handling` (`error_handling`) — file-level coverage `100 × files_with_try_except / total_source_files` over `03-development/src` / `src`. Replaces the CRG flow `has_error_handler` path (that field does not exist in the installed code-review-graph package).
- `ast-docstrings` (`documentation`) — public-API docstring coverage `100 × public_defs_with_docstring / total_public_defs` over `03-development/src` / `src` (`def`/`class` whose name is not `_`-prefixed; `ast.get_docstring`). Replaces the former `pydocstyle`/`interrogate` style-existence proxy that an agent could satisfy with one-line stubs. `total_public == 0` → 100 (no public API to document). S4 re-runs this AST scan independently → not fabricable.
- `pytest-cov-integration` (`integration_coverage`) — runs `pytest 03-development/tests/integration --cov=…` and scores the real coverage `TOTAL%` (not pass-rate). Missing suite → exit 4/5 → score 0 → cross-validation blocks.

> `architecture` and `traceability` are NOT scored here — they are framework-owned (`architecture` via independent CRG run, `traceability` verified via traceability matrices/attestation) and overridden in `finalize_gate`. The agent must NOT report scores for them in `gate_result.json` for Gates 2, 3, and 4. Cross-validation skips them.

**Cross-validation return-code handling** (in `_run_harness_cross_validation`, when the agent-reported score ≥ threshold):
- `-1` skip-list → must supply a verifiable committed `tool_output` file (above), else BLOCK.
- `-2` timeout → **BLOCK**. A passing score must be independently reproducible; a timeout means the harness could not verify it, which is treated as a fabrication risk (reduce test runtime / fix the tool, then re-finalize).
- `5` (pytest-family "no tests/benchmarks collected") → **BLOCK**. A passing score for a dimension whose verifying suite (`pytest-benchmark` / integration tests) does not exist is unverifiable.
- `-3` tool-not-found / `-4` harness error → warn-only (S2 `_verify_gate_tools` already gates tool availability pre-finalize; `-4` is a framework-side fault, not agent-controllable).

**Default timeouts** (seconds): `ruff` 30; `mypy`/`pyright` 60; `pytest`/`pytest-cov` 120; `gitleaks` 30; `bandit` 60; `radon-cc`/`radon-mi` 30; `ast-docstrings` 30; `ast-error-handling` 30.

**Integration**: Called by `HarnessBridge` during `run_gate()` preflight to produce harness-owned tool scores. Results are compared against agent-reported scores in `cross_artifact.py` (§3.15) and logged to `.sessi-work/harness_verification/` for audit.

**Companion — `harness/crg_independent.py` (framework-owned `architecture`)**: The CRG Python API lives under its own interpreter (the `code-review-graph` console-script shebang), not the harness interpreter. `run_independent_crg(project_root, work_dir)` drives `code-review-graph build` + `postprocess` as subprocesses, dumps communities via `crg_dump_communities.py` (run under CRG's interpreter), reuses `crg_analysis.compute_community_cohesion_score`, and **writes** `.sessi-work/crg_metrics.json` itself. `finalize_gate` then overrides `architecture ← community_cohesion.score` — the agent never produces this value. CRG is a hard dependency: a missing binary or failed run raises `CrgIndependentError` → gate BLOCKED (no graceful degradation). `_verify_all_gate_tools` (run at each `run-phase`) surfaces a missing `code-review-graph` at project setup.

---

### §3.38 — `core/observability.py` — Agentic Trajectory Tracing

**Responsibility**: OpenTelemetry-based trajectory logging for harness agent execution. Exports span data to a local JSONL file for offline time-travel debugging.

**Classes**:

| Class | Purpose |
|---|---|
| `JsonFileSpanExporter(log_dir: Path)` | OTel `SpanExporter` that appends span records to `.harness/traces/agent_trajectory.jsonl`. Records: name, trace_id, span_id, start_time, end_time, attributes, events. |

**Module-level state**:
```python
_HARNESS_TRACER_INITIALIZED: bool = False  # module flag; prevents double-init even if third-party libs set a TracerProvider
```

**Public API**:
```python
def init_tracer(project_root: Path) -> trace.Tracer:
    """Idempotent. Sets up TracerProvider + BatchSpanProcessor + JsonFileSpanExporter.
    Export path: project_root/.harness/traces/agent_trajectory.jsonl
    Returns trace.get_tracer("harness_agent")."""

def get_tracer() -> trace.Tracer:
    """Returns the already-configured tracer (call after init_tracer)."""
```

**Dependencies** (`pyproject.toml`):
```toml
"opentelemetry-api>=1.20.0",
"opentelemetry-sdk>=1.20.0",
```

**Activation**: `PhaseHooks.__init__` lazy-imports `init_tracer` and stores the tracer as `self.tracer`. Any downstream code calling `preflight_all()` or `postflight_all()` automatically emits spans. `get_tracer()` is available for custom instrumentation in other modules.

**Span attribute namespace**: all harness-specific attributes use the `harness.*` prefix
(`harness.phase`, `harness.gate`, `harness.fr_id`, `harness.score`, `harness.blocked`, etc.)
following OTel's custom-namespace convention. The `gen_ai.*` namespace is reserved for
LLM inference calls (model, tokens, finish_reason) per GenAI Semantic Conventions v1.37.

**Exporter selection** (`core/observability.py:init_tracer`):

| Environment variable | Exporter | Requirement |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT=http://host:4318` | OTLP HTTP → OTel Collector | `pip install opentelemetry-exporter-otlp-proto-http` |
| `OTEL_EXPORTER=console` | ConsoleSpanExporter (stdout) | built-in, no extras |
| (neither set) | Local JSONL at `<project>/.harness/traces/agent_trajectory.jsonl` | default, zero deps |

Graceful fallback: if `OTEL_EXPORTER_OTLP_ENDPOINT` is set but the OTLP package is not
installed, harness silently falls back to JSONL — the gate pipeline is never blocked by a
missing observability package.

**Collector reference config**: `otel-collector.yml` in the repository root shows a
minimal Docker-based setup (OTLP HTTP receiver → debug stdout + file exporter).
Swap the `file` exporter for `otlpdatadog`, `otlphttp` (Grafana Cloud), or an
Elastic APM endpoint to connect to a production backend.

---

## 4. Core Workflow Sequences

### 4.1 Gate Run (e.g. Gate 2, P3 exit) — Two-Phase Flow

```
Phase 1 — Prepare:
  Operator -> HarnessBridge.prepare_gate(gate_num=2, project_root, phase=3)
    │
    ├─ 1. _load_config(2) → reads harness/gate_configs/gate2_p3_exit.yaml
    ├─ 2. [gate2: no crg.reconnaissance configured — skipped]
    ├─ 3. ssi_dir = harness/ssi/  (embedded, no external repo needed)
    ├─ 4. work_dir = project_root/.sessi-work/  (created if absent)
    └─ 5. return GateContext(gate_num=2, config=..., ssi_scripts_dir=..., ...)
    [GateContext.evaluation_prompt() prints full evaluation instructions for Claude]

Phase 2 — Claude Evaluation (not a subprocess, Claude IS the engine):
  Claude reads harness/ssi/prompts/evaluate_dimension.md
  Claude uses harness/ssi/scripts/ for static analysis
  Claude writes project_root/.sessi-work/gate2_result.json

Phase 3 — Finalize:
  Operator -> HarnessBridge.finalize_gate(ctx)
    │
    ├─ 1. reads .sessi-work/gate2_result.json → GateResult
    │      [FileNotFoundError if Claude did not write result file]
    ├─ 2. _update_quality_manifest(2, None, result)
    ├─ 3. EffortTracker.record(...)
    ├─ 4. DecisionLogWriter.write(decision="GATE_PASS|GATE_BLOCK")
    ├─ 5. if result.score < 75 or not result.quality_complete → raise GateBlockedError
    │      [CLI layer catches GateBlockedError → _format_block_diagnostic() →
    │       structured stdout + writes .methodology/last_block.md]
    └─ 6. return GateResult
```

### 4.2 A/B Review via Claude Sub-agent (v3.0 — Dep-Ordered + Parallel Waves)

```
AgentSpawner.spawn(model="claude", role="Reviewer", prompt, context, phase, fr_id)
  │
  └─ ReviewerRouter.review(role, full_prompt, phase, fr_id)
       │
       ├─ 1. _decompose_with_deps(prompt) → list[SubTask] (topologically sorted)
       │
       │      P1/P2: ALWAYS single subtask (whole-deliverable review, no decomposition)
       │      Else IF len(prompt) <= 2000 chars: → [SubTask(prompt, label="full_task")]
       │
       │      Detection pipeline (tried in order):
       │        A. _extract_phase_sections(): "Phase N" / "PX" (SRS.md style)
       │        B. _extract_fr_sections(): FR-XXX boundaries
       │        C. _extract_heading_sections(): §X.Y / "X.Y Title" (SAD.md style)
       │        D. _paragraph_subtasks(): para-split ≤800 chars (sequential deps)
       │
       │      _build_dep_graph(): cross-ref scan + implicit Phase-N → Phase-(N-1)
       │      _topological_sort(): Kahn's BFS, cycle-safe
       │
       ├─ 2. Wave execution (independent subtasks run in parallel; waves are dep-ordered):
       │
       │      approved_context = []
       │      FOR EACH wave (set of subtasks with satisfied deps):
       │        run wave subtasks concurrently via ThreadPoolExecutor:
       │          enriched = subtask.content + last 6 ✅ approved summaries
       │          result = _try_chain(role, enriched, phase, fr_id, cancel_event)
       │          │
       │          _try_chain → _try_subagent → AgentSpawner.spawn(model="claude")
       │            (stateless Claude sub-agent; emergency APPROVE@0.3 only if spawn raises)
       │        │
       │        IF any result == REJECT:
       │          cancel_event.set()  → sibling subtasks return CANCELLED
       │          STOP → _merge_results(completed so far)    ← early exit, fast (wait=False)
       │        ELSE:
       │          approved_context += [f"✅ [{label}] {summary}" for each]
       │
       └─ 3. _merge_results([subtask_results]):
              ├─ Any REJECT → return REJECT (with _stopped_at, _completed_subtasks)
              ├─ confidence = min(all subtask confidences)
              ├─ violations = union(all violations)
              └─ summary = " | ".join(all summaries)

Result keys: review_status, confidence, violations, summary,
             _reviewer_used="subagent",
             _stopped_at (REJECT only), _completed_subtasks, _total_subtasks
```

### 4.3 Quality Manifest Generation (P2 exit)

```
HarnessBridge.generate_quality_manifest(fr_ids=["FR-01","FR-02"], sad_path="SAD.md")
  │
  ├─ from scripts.generate_sab import parse_sad  ← functional (fix ②+⑤ applied)
  │  sab = parse_sad(sad_path)  → {nfr_dim_map, constraints, high_risk, ...}
  │
  ├─ manifest = {schema_version, generated_at_phase=2, fr_ids,
  │              nfr_dimension_mapping (auto-derived from nfr_traceability),
  │              nfr_fr_mapping (from SRS §2 cross-reference table),
  │              nfr_traceability (verbatim from SAD nfr_traceability block),
  │              architecture_constraints, high_risk_modules, gate_score_overrides={},
  │              gate_results={gate1:{}, gate2:null, gate3:null, gate4:null}}
  │
  └─ Path(".methodology/quality_manifest.json").write_text(json.dumps(manifest, indent=2))
```

### 4.4 PhaseHooks Pre/Post Flight

```
PhaseHooks("/path/to/project", phase=3)
  │
  ├─ preflight_all()
  │    ├─ preflight_fsm_check()        → reads .methodology/state.json
  │    ├─ preflight_constitution()     → quality_gate.constitution.run_constitution_check(...)
  │    ├─ preflight_kill_switch()      → verifies M1 circuit breaker operational
  │    ├─ preflight_previous_phase_artifacts() → PhaseArtifactRegistry.verify_phase_chain() (P2+)
  │    ├─ preflight_drift_detection()  → M2 DriftDetector.detect_all() (score ≥ drift_threshold)
  │    ├─ preflight_sab_check()        → validates SAB.json layers + deps (P3+ only)
  │    ├─ preflight_tool_registry()    → ToolRegistry.list_tools() (skipped if not installed)
  │    └─ preflight_traceability()     → check_spec_trace.check_traceability() (P3 info, P4+ block)
  │
  ├─ [per-FR development loop]
  │    ├─ monitoring_before_dev(fr_id, agent_id="agent-a")   → M1 circuit check + start monitoring
  │    ├─ [developer executes]
  │    ├─ monitoring_after_dev(fr_id, result, agent_id="agent-a")   → M1 stop monitoring
  │    ├─ monitoring_before_rev(fr_id, agent_id="agent-b")   → M1 circuit check + start monitoring
  │    ├─ [reviewer executes]
  │    ├─ monitoring_after_rev(fr_id, result, agent_id="agent-b")   → M1 stop monitoring
  │    └─ monitoring_hr12_check(fr_id, iteration)  → False if iteration >= 5
  │
  └─ postflight_all()
       ├─ postflight_constitution()          → re-check with check_mode="postflight"
       ├─ postflight_drift_check()           → M2 re-check; blocks if score < drift_threshold
       ├─ postflight_update_state(success)   → advance state.json current_phase
       └─ postflight_summary()               → {total_frs, approved, fr_results, ...}
```

---

## 5. Appendix A — Configuration Schemas

### 5.1 `harness/gate_configs/*.yaml` — Gate Configuration

Four files: `gate1_per_fr.yaml`, `gate2_p3_exit.yaml`, `gate3_p4_exit.yaml`, `gate4_p6_full.yaml`.

**Schema** (all fields):

| Field | Type | Required | Description |
|---|---|---|---|
| `gate` | `int` | Yes | Gate number (1–4) |
| `trigger` | `str` | Yes | `per_fr_completion` or `phase_exit` |
| `phase` | `int` | Conditional | Required when `trigger: phase_exit` |
| `scope` | `str` | Yes | `single_fr`, `full_phase`, or `full_project` |
| `dimensions` | `list[dict]` | Yes | Scoring dimensions (see below) |
| `dimensions[].name` | `str` | Yes | Dimension name |
| `dimensions[].tier` | `int` | Yes | LLM tier: 1=fastest, 3=most capable |
| `dimensions[].model` | `str` | Yes | `claude` (all dims use Claude sub-agent) |
| `dimensions[].threshold` | `int` | Yes | Pass threshold (0–100) |
| `dimensions[].weight` | `float` | Yes | Weight in composite score (must sum to 1.0) |
| `blocking` | `bool` | Yes | Whether gate is blocking |
| `score_gate` | `int` | Gate 2/3/4 only | Composite score threshold |
| `max_rounds` | `int` | Yes | Max auto-fix iterations |
| `early_stop` | `bool` | Yes | Stop eval after first issue found |
| `saturation_rounds` | `int` | Gate 2/3/4 only | Consecutive no-new-issue rounds → saturated |
| `mutation_testing` | `dict` | Gate 2/3/4 only | `{median_runs: int, timeout_per_run: int}` |
| `crg` | `dict` | Gate 2/3/4 only | CRG integration settings (see below) |
| `crg.enabled` | `bool` | Gate 3/4 | Enable CRG |
| `crg.reconnaissance` | `bool` | Gate 3/4 | Run structural recon at gate entry |
| `crg.tier3_guidance` | `bool` | Gate 3/4 | Get CRG guidance before each Tier 3 eval |
| `crg.impact_check` | `bool` | Gate 2 | Run impact analysis before each fix round |
| `crg.impact_threshold` | `float` | Gate 2/3/4 | Risk score threshold (default: 0.7) |
| `crg.drift_threshold` | `float` | Gate 3/4 | Structural drift threshold (default: 0.4) |
| `replaces` | `str` | Yes | Legacy SOP component this gate replaces |

**Actual gate configurations**:

**Gate 1** — `gate1_per_fr.yaml` (per-FR at P3/P4/P5/P7/P8):
```yaml
gate: 1
trigger: per_fr_completion
scope: single_fr
dimensions:
  - { name: linting,       tier: 1, model: claude, threshold: 90, weight: 0.33 }
  - { name: type_safety,   tier: 1, model: claude, threshold: 85, weight: 0.33 }
  - { name: test_coverage, tier: 1, model: claude, threshold: 80, weight: 0.34 }
blocking: true
early_stop: false
max_rounds: 1
replaces: check_fr_full_layer3
```
> Note: No `score_gate` — Gate 1 uses per-dimension thresholds only. No auto-iteration on failure; developer must manually fix and re-run.
> **TDD semantic**: `test_coverage(80)` in Gate 1 requires that TDD test stubs for the FR were
> committed **before** implementation (P3 Step 0). Coverage is measured against FR-specific
> acceptance criteria, not just line coverage.

**Gate 2** — `gate2_p3_exit.yaml` (P3 exit — all FRs complete):
```yaml
gate: 2
trigger: phase_exit
phase: 3
scope: full_phase
dimensions:
  - { name: linting,            tier: 1, model: claude, threshold: 90,  weight: 0.15 }
  - { name: type_safety,        tier: 1, model: claude, threshold: 85,  weight: 0.15 }
  - { name: test_coverage,      tier: 1, model: claude, threshold: 80,  weight: 0.15 }
  - { name: security,           tier: 2, model: claude, threshold: 80,  weight: 0.15 }
  - { name: secrets_scanning,   tier: 1, model: claude, threshold: 100, weight: 0.10 }
  - { name: license_compliance, tier: 1, model: claude, threshold: 100, weight: 0.10 }
  - { name: mutation_testing,   tier: 1, model: claude, threshold: 70,  weight: 0.20 }
blocking: true
score_gate: 75
max_rounds: 3
early_stop: true
saturation_rounds: 3
mutation_testing: { median_runs: 3, timeout_per_run: 120 }
crg: { impact_check: true, impact_threshold: 0.7 }
replaces: auto_research_p3
```

**Gate 3** — `gate3_p4_exit.yaml` (P4 exit — testing complete, first full 12-dim eval):
```yaml
gate: 3
trigger: phase_exit
phase: 4
scope: full_phase
dimensions:
  - { name: linting,            tier: 1, model: claude, threshold: 90,  weight: 0.10 }
  - { name: type_safety,        tier: 1, model: claude, threshold: 85,  weight: 0.10 }
  - { name: test_coverage,      tier: 1, model: claude, threshold: 80,  weight: 0.10 }
  - { name: security,           tier: 2, model: claude, threshold: 80,  weight: 0.10 }
  - { name: secrets_scanning,   tier: 1, model: claude, threshold: 100, weight: 0.08 }
  - { name: license_compliance, tier: 1, model: claude, threshold: 100, weight: 0.07 }
  - { name: mutation_testing,   tier: 1, model: claude, threshold: 70,  weight: 0.10 }
  - { name: architecture,       tier: 3, model: claude,       threshold: 80,  weight: 0.10 }
  - { name: readability,        tier: 3, model: claude,       threshold: 80,  weight: 0.07 }
  - { name: error_handling,     tier: 3, model: claude,       threshold: 80,  weight: 0.10 }
  - { name: documentation,      tier: 3, model: claude,       threshold: 75,  weight: 0.03 }
  - { name: performance,        tier: 3, model: claude,       threshold: 75,  weight: 0.05 }
blocking: true
score_gate: 80
max_rounds: 3
early_stop: true
saturation_rounds: 3
mutation_testing: { median_runs: 3, timeout_per_run: 120 }
crg:
  enabled: true
  reconnaissance: true
  tier3_guidance: true
  impact_threshold: 0.7
  drift_threshold: 0.4
replaces: auto_research_p4
```

**Gate 4** — `gate4_p6_full.yaml` (P6 exit — final gate, full project):
```yaml
gate: 4
trigger: phase_exit
phase: 6
scope: full_project
dimensions:
  - { name: linting,            tier: 1, model: claude, threshold: 90,  weight: 0.08 }
  - { name: type_safety,        tier: 1, model: claude, threshold: 85,  weight: 0.08 }
  - { name: test_coverage,      tier: 1, model: claude, threshold: 80,  weight: 0.08 }
  - { name: security,           tier: 2, model: claude, threshold: 80,  weight: 0.10 }
  - { name: secrets_scanning,   tier: 1, model: claude, threshold: 100, weight: 0.07 }
  - { name: license_compliance, tier: 1, model: claude, threshold: 100, weight: 0.07 }
  - { name: mutation_testing,   tier: 1, model: claude, threshold: 70,  weight: 0.08 }
  - { name: architecture,       tier: 3, model: claude,       threshold: 80,  weight: 0.14 }
  - { name: readability,        tier: 3, model: claude,       threshold: 80,  weight: 0.08 }
  - { name: error_handling,     tier: 3, model: claude,       threshold: 80,  weight: 0.10 }
  - { name: documentation,      tier: 3, model: claude,       threshold: 75,  weight: 0.07 }
  - { name: performance,        tier: 3, model: claude,       threshold: 75,  weight: 0.05 }
blocking: true
score_gate: 85
max_rounds: 3
early_stop: true
saturation_rounds: 3
mutation_testing: { median_runs: 3, timeout_per_run: 120 }
crg:
  enabled: true
  reconnaissance: true
  tier3_guidance: true
  impact_threshold: 0.7
  drift_threshold: 0.4
replaces: p6_sop_entirely
```

---

### 5.2 `.methodology/quality_manifest.json`

Schema defined by `schemas/quality_manifest.schema.json` (JSON Schema Draft 7). Structure:

```json
{
  "schema_version": "1.0",
  "generated_at_phase": 2,
  "fr_ids": ["FR-01", "FR-02"],
  "nfr_dimension_mapping": {"NFR-01": "performance", "NFR-02": "security"},
  "nfr_fr_mapping": {"NFR-02": ["FR-04", "FR-05"]},
  "nfr_traceability": {
    "NFR-01": {"type": "performance", "target": "p95 < 3s", "module": "app.processing.pipeline"},
    "NFR-02": {"type": "security",    "target": "reject unsigned",  "module": "app.security.signature"}
  },
  "architecture_constraints": [],
  "high_risk_modules": [],
  "gate_score_overrides": {},
  "gate_results": {
    "gate1": {
      "FR-01": {
        "score": 92.5,
        "quality_complete": true,
        "rounds_used": 1,
        "open_critical": 0,
        "open_high": 0
      }
    },
    "gate2": null,
    "gate3": null,
    "gate4": null
  }
}
```

- `nfr_dimension_mapping` — auto-derived from SAD `nfr_traceability.type`；或 SRS §3 pipe-table fallback
- `nfr_fr_mapping` — 從 SRS §2 FR Cross-Reference 表格 "NFR Association" 欄反向建立
- `nfr_traceability` — 直接來自 SAD `nfr_traceability` block（含 type / target / module）
- `gate1` stores a dict keyed by `fr_id` (per-FR)
- `gate2`/`gate3`/`gate4` store a single payload dict (full-phase/full-project)

---

### 5.3 `.methodology/decision_logs/{date}/{agent}_{phase}_{seq:03d}.yaml`

Written by `DecisionLogWriter.write()`:

```yaml
agent_id: GATE
phase: 3
fr_id: FR-01
decision: GATE_PASS
reasoning: "Gate 2: score=81.3, critical=0, high=0, rounds=2"
gate_score: 81.3
created_at: "2026-04-27T14:32:01.123456"
```

---

### 5.4 `.methodology/effort_metrics.db` — SQLite Schema

```sql
CREATE TABLE IF NOT EXISTS effort (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    phase      INTEGER,
    gate_num   INTEGER,
    agent_id   TEXT,
    operation  TEXT,
    duration_s REAL,
    token_in   INTEGER DEFAULT 0,
    token_out  INTEGER DEFAULT 0,
    fr_id      TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```

---

## 6. SAB Block (machine-readable, v1.2 — As-Built)

> Updated from v1.2. Added to Layer 1: `harness/handover_generator.py`, `harness/fr_progress_tracker.py`, `harness/retry_utils.py`.

<!-- SAB:START -->
```json
{
  "version": "1.2",
  "created_at": "2026-04-28",
  "project": "harness-methodology",
  "layers": [
    {
      "name": "0_Entrypoint_Facade",
      "description": "CLI entrypoints. harness_cli.py is the standalone harness entrypoint. cli.py is the full-system entrypoint (requires 30+ external modules, lives in parent system — not runnable in this repo alone).",
      "modules": ["harness_cli.py"],
      "allowed_dependencies": ["1_Integration_Bridge", "2_Core_Orchestration"]
    },
    {
      "name": "1_Integration_Bridge",
      "description": "Bridge layer connecting methodology workflow to external tools: Quality Gates, CRG, Claude sub-agent reviewer, git strategy, handover, and audit/metrics sinks.",
      "modules": [
        "harness/harness_bridge.py",
        "harness/reviewer_router.py",
        "harness/crg_bridge.py",
        "harness/decision_log.py",
        "harness/handover_generator.py",
        "harness/fr_progress_tracker.py",
        "harness/retry_utils.py",
        "harness/effort_tracker.py",
        "harness/issue_tracker_ext.py",
        "harness/git_strategy.py",
        "harness/ssi/"
      ],
      "allowed_dependencies": ["2_Core_Orchestration", "3_Quality_Features", "4_Base_Utilities"]
    },
    {
      "name": "2_Core_Orchestration",
      "description": "Manages agent lifecycle, phase execution, workflow routing, task decomposition, AB steering, session logging, subagent isolation, FSM state, and auto-fix engine.",
      "modules": [
        "core/agent_spawner.py",
        "core/phase_hooks.py",
        "core/hybrid_workflow.py",
        "core/task_splitter.py",
        "core/sessions_spawn_logger.py",
        "core/subagent_isolator.py",
        "core/lifecycle_hooks.py",
        "core/workspace_manager.py",
        "core/atomic_io.py",
        "core/observability.py",
        "core/pre_flight.py",
        "core/traceability/",
        "core/utils/",
        "core/fsm/",
        "core/auto_fix/",
        "core/adapters/",
        "steering/",
        "orchestration/"
      ],
      "allowed_dependencies": ["3_Quality_Features", "4_Base_Utilities"]
    },
    {
      "name": "3_Quality_Features",
      "description": "Concrete quality check implementations, safety features, gap detection, enforcement hooks, constitution compliance, and quality monitoring dashboard.",
      "modules": [
        "core/quality_gate/",
        "core/requirement_traceability.py",
        "detection/",
        "gap_detector/",
        "kill_switch/",
        "enforcement/",
        "constitution/",
        "agent_personas/"
      ],
      "allowed_dependencies": ["4_Base_Utilities"]
    },
    {
      "name": "4_Base_Utilities",
      "description": "Cross-cutting concerns: schemas, configuration, templates, and CLI prompts.",
      "modules": [
        "schemas/",
        "core/cli_phase_prompts.py",
        "templates/"
      ],
      "allowed_dependencies": []
    }
  ],
  "dependencies": {
    "0_Entrypoint_Facade": ["1_Integration_Bridge", "2_Core_Orchestration"],
    "1_Integration_Bridge": ["2_Core_Orchestration", "3_Quality_Features", "4_Base_Utilities"],
    "2_Core_Orchestration": ["3_Quality_Features", "4_Base_Utilities"],
    "3_Quality_Features": ["4_Base_Utilities"],
    "4_Base_Utilities": []
  },
  "_sab_v1_2_additions": {
    "Layer_1_extras": [
      "harness/crg_api.py",
      "harness/crg_independent.py",
      "harness/tool_runners.py",
      "harness/audit/",
      "harness/gate_configs/",
      "harness/lang_scanners/",
      "harness/toolchains/"
    ],
    "Layer_2_extras": [
      "core/atomic_io.py",
      "core/observability.py",
      "core/pre_flight.py",
      "core/traceability/",
      "core/utils/",
      "orchestration/"
    ]
  },
  "quality_targets": {
    "max_complexity": 20,
    "min_coverage": 80,
    "max_coupling": 0.35
  }
}
```
<!-- SAB:END -->

---

### §3.19 — `constitution/` (HR Compliance Package)

**Purpose**: Real implementations of the HR-compliance interfaces (note: `steering` was removed in 減法 T4).

| File | Class | Role |
|---|---|---|
| `constitution/__init__.py` | — | Package marker; re-exports the most-imported classes (`BVSRunner`, `CitationParser`) for convenience. The other 5 (`ClaimVerifier`, `ClaimExtractor` (functions), `ExecutionLogger`, `InferentialSensor`, `InvariantEngine`) are reachable via the submodule path only. |
| `constitution/bvs_runner.py` | `BVSRunner` | HR-03 phase-order checker: reads `.methodology/state.json`, validates phase prerequisites and FSM state |
| `constitution/citation_parser.py` | `CitationParser` | HR-07/09: regex extraction of citation markers (`[FR-01]`, `[§3.2]`, etc.) and obligation-verb claims; `verify_claim()` checks traceability keywords |
| `constitution/claim_extractor.py` | (module-level functions + `Claim` data class) | Module exposes `extract_claims()`, `claims_to_dict()` (and the helper `_extract_keywords()`) as **module-level functions**, plus a `Claim` dataclass (line 23) that holds individual claim records. There is **no `ClaimExtractor` class** — callers import the functions directly, not a `ClaimExtractor` instance. |
| `constitution/claim_verifier.py` | `ClaimVerifier` | Verifies extracted claims against codebase evidence |
| `constitution/execution_logger.py` | `ExecutionLogger` | Logs constitution check execution for audit trail |
| `constitution/inferential_sensor.py` | `InferentialSensor` | Inference-based compliance sensing for non-explicit violations |
| `constitution/invariant_engine.py` | `InvariantEngine` | Evaluates phase invariants (hard rules) against runtime state |
| `constitution/verification_constitution_checker.py` | `VerificationConstitutionChecker` | ~~Bridges `steering/integrations.py` to `enforcement.constitution_as_code` (R001-R007); gracefully degrades to pass-through if `enforcement/` unavailable~~ **REMOVED** (減法 T3) |

**Imports**: stdlib only (`re`, `json`, `pathlib`). No external dependencies.  
**Integration**: `SteeringIntegrator.bvs_integrator` property and `iterate_with_full_check()` now call real code instead of hitting `ImportError`.

**Constitution keyword scan exclusions** (v2.6.0): Meta-documents
(`HANDOVER.md`, `*STAGE_PASS.md`) are excluded from
keyword-density scanning by default. These files contain operational content
that inherently scores zero on constitution keywords, diluting the aggregate
score below phase
thresholds. Exclusions use glob patterns via `ConstitutionProfile.is_excluded()`;
configurable through `.methodology/constitution_profile.json` → `exclude_patterns`
(global) and `phases.N.exclude_patterns` (per-phase, additive).


### §3.20 — `scripts/` Directory Overview

Full inventory of the `scripts/` directory (30 items). Grouped by role:

#### Phase Lifecycle & Planning

| Script | Size | Purpose |
|---|---|---|
| `phase_auditor.py` | 65KB | Deep-audit a completed phase: validates artifacts, gate results, FR coverage; produces ASPICE-grade Markdown report |
| `generate_full_plan.py` | 56KB | Generates a full FR-level execution plan for a phase from SAD.md; outputs `.methodology/phase{N}_plan.md` |
| `generate_fr_mapping.py` | 6KB | Builds an FR→file mapping from SAD.md and codebase scan; consumed by `phase_auditor.py` and gate pre-flight |
| `generate_sab.py` | 3KB | CLI wrapper around `sab_parser.extract_sab_from_sad`; also exposes `parse_sad()` called by `HarnessBridge.generate_quality_manifest()` |
| `phase_auditor.py` | 60KB | Phase 3-8 deliverable verifier (replaces A/B collaboration checks for P3+): C1-C12 audit covering plan completion, deliverables, gate results, git log milestones, traceability, and constitution compliance; invoked by `harness_cli.py audit-phase` and `finalize-gate` for phases ≥3 |

**`phase_auditor.py` usage** (via `harness_cli.py audit-phase`):
```bash
python harness_cli.py audit-phase --phase 3 --repo owner/repo [--branch main] [--output markdown|json] [--save FILE]
```

**`generate_full_plan.py` usage** (called by `harness_cli.py plan-phase`):
```bash
python scripts/generate_full_plan.py --phase 3 --repo /path/to/project \
  --output .methodology/phase3_plan.md
```

#### FR Verification

| Script | Size | Purpose |
|---|---|---|
| `check_fr_full.py` | 7KB | Full bidirectional FR audit: SRS → SAD → code → test chain completeness |
| `check_fr_quality.py` | 4KB | FR-level quality scoring: docstring coverage, citation format, `[FR-XX]` tag presence |
| `check_spec_trace.py` | 9KB | Content-level FR→code→test traceability validator; populates RequirementTraceability model; Gate 3 pre-flight |
| `build_traceability.py` | 10KB | ASPICE traceability matrix builder: scans SAD.md + `[FR-XX]` + tests → auto-generates TRACEABILITY_MATRIX.md |

#### Integration & Automation

| Script | Size | Purpose |
|---|---|---|
| `setup-git-hooks.sh` | 9KB | Installs `prepare-commit-msg` / `post-merge` / `pre-push` hooks in a **target project** |
| `cron_drift_monitor.py` | 5KB | ~~Hourly drift detection cron; reads `DRIFT_PROJECT_PATH` env var; alerts via log + optional Slack webhook / SMTP email (both env-var configurable)~~ **REMOVED** (減法 T4) |
| `cron_docs_optimizer.py` | 9KB | Scheduled docs quality optimizer; runs against stale documentation |
| `drift_crontab.example` | 780B | ~~Example crontab configuration for `cron_drift_monitor.py`~~ **REMOVED** (減法 T4) |
| `DRIFT_CRON_SETUP.md` | 2KB | Setup guide for drift cron monitoring |
| `harness-init.sh` | 5KB | Bootstrap script: creates `.methodology/` skeleton, copies config templates to a new target project |

#### Compliance & Diagnostics

| Script | Size | Purpose |
|---|---|---|
| `spec_logic_checker.py` | 10KB | Validates spec logic consistency (no contradictory requirements, all FRs have priorities). Also invocable via `harness_cli.py check-logic`. |
| `dev_log_checker.py` | 12KB | ~~Validates `DEVELOPMENT_LOG.md` format and HR-07 session_id presence~~ **REMOVED** (HR-07 removed; see SKILL.md) |
| `verify_spec_compliance.py` | 7KB | End-to-end spec compliance: code must implement every FR declared in SAD.md. Also invocable via `harness_cli.py verify-spec`. |
| `verify_path_consistency.py` | 3KB | ~~Confirms path references match filesystem~~ **REMOVED** (superseded by tests/test_no_hardcoded_paths.py + topology anchor tests) |
| `state_monitor.py` | 6KB | ~~Reads state.json; reports FSM state~~ **REMOVED** (zero callers; `status` / `doctor` commands cover it) |
| `ci_state_helper.py` | 4KB | CI Safe State Extractor: Replaces unsafe bare json inline parses in CI workflows |
| `rotate_decision_logs.py` | 5KB | Decision Log Archiver: Prevents unbounded growth of decision logs; archives directories older than retention days |

#### Release Management

| Script | Size | Purpose |
|---|---|---|
| `bump_version.py` | 2KB | Semantic version bumper: reads/writes version across SKILL.md, CONSTITUTION.md, and manifest.json |
| `create_release.sh` | 2KB | Release tagging script: creates annotated tag with changelog, validates version consistency |
| `generate_quality_report.py` | 12KB | Generates QUALITY_REPORT.md from quality_manifest.json and gate result files; 12-dimension assessment, per-FR Gate 1 summary, defect summary, ASPICE traceability |
| `generate_release_notes.py` | 4KB | Generates RELEASE_NOTES.md from git log and quality_manifest.json; features, bug fixes, quality scores, known issues |
| `list-modules.py` | 4KB | Module inventory scanner; `--validate` flag checks all manifest.json + SKILL.md frontmatter integrity |
| `validate_cross_refs.py` | 3KB | Cross-reference integrity checker: CLASSIFICATION_TABLE ↔ STRATEGY_REGISTRY consistency, dead code detection |

#### Package & Config

| File | Size | Purpose |
|---|---|---|
| `__init__.py` | <1KB | Package marker for `scripts/` |

> **Not in `scripts/`**: `scripts/CLAUDE.md` was removed (see earlier revisions). Per-script
> usage lives in the script's own `--help` and in this SAD's `§3.20` table.
>
> **Project-level build/CI artifacts** (not in `scripts/`): `pyproject.toml` (root, pip wheel config), `.github/workflows/release.yml` (tag-driven release workflow).

---

### §3.22 — `core/task_splitter.py` — Task Decomposer

**Responsibility**: Decomposes a high-level development goal into a DAG of ordered `Task` objects with dependencies, priorities, and estimated hours. Used by `harness_cli.py plan-phase` and `AgentSpawner` for FR-level work breakdown.

**Key classes**:

```python
class TaskStatus(Enum):
    PENDING | RUNNING | COMPLETED | FAILED | BLOCKED

class TaskPriority(Enum):
    LOW(1) | MEDIUM(2) | HIGH(3) | CRITICAL(4)

@dataclass
class Task:
    id: str                          # "task-001", "task-002", ...
    name: str
    description: str
    status: TaskStatus = PENDING
    priority: TaskPriority = MEDIUM
    dependencies: List[str]          # list of task ids this depends on
    assignee: Optional[str]
    estimated_hours: float
    actual_hours: float
    output: Any
    error: Optional[str]
    created_at: str                  # ISO datetime
    completed_at: Optional[str]
```

**`TaskSplitter`**:

```python
class TaskSplitter:
    def create_task(name, description, priority, estimated_hours) -> Task
    def add_dependency(task_id, depends_on) -> None
    def split_from_goal(goal: str) -> List[Task]
        # Keyword-maps goal text → standard phase tasks:
        # research → Design → Development → Testing → Documentation → Deployment
        # Sequentially chained (each depends on previous)
    def get_ready_tasks() -> List[Task]     # PENDING with all deps COMPLETED
    def get_execution_order() -> List[Task] # topological sort
    def get_dag() -> Dict                   # {nodes: [...], edges: [...]}
    def get_summary() -> Dict               # counts by status + total_estimated_hours
```

---

### §3.23 — `core/sessions_spawn_logger.py` — Spawn Event Logger

**Responsibility**: Records agent spawn events to `.methodology/sessions_spawn.log`. This log is consumed by `PhaseTruthVerifier` to enforce HR-10 and HR-01. Supports two-phase write (PENDING → COMPLETED/FAILED via `log_update()`).

**Public API**:

```python
class SessionsSpawnLogger:
    LOG_FILENAME = ".methodology/sessions_spawn.log"

    def __init__(self, repo_path: Path)

    def log_spawn(
        role: str, task: str, session_id: str,
        confidence: Optional[int] = None,
        status: str = "SPAWNED",
        **kwargs,
    ) -> Dict                     # appends JSON line to log

    def log_update(session_id: str, **updates) -> Optional[Dict]
        # Finds entry by session_id, applies updates in-place
        # Used to flip PENDING → COMPLETED/FAILED after spawn completes

    def validate() -> Dict        # {valid: bool, count: int, errors: []}
    def get_summary() -> Dict     # {total_entries, role_counts, fr_tasks, status_counts, valid}
```

**Log format** (each line is a JSON object):
```json
{"timestamp": "...", "role": "developer", "task": "FR-01", "session_id": "dev-abc123", "status": "SPAWNED", "confidence": 8}
```

**Convenience function**:
```python
def log_spawn_event(repo_path, role, task, session_id, **kwargs) -> Dict
    # One-shot: SessionsSpawnLogger(repo_path).log_spawn(...)
```

**Note**: `validate()` checks every entry has `role` + `session_id` fields. This is advisory only — no gate/finalize path blocks on the log's contents (HR-10 removed).

---

### §3.24 — `core/requirement_traceability.py` — ASPICE Traceability Manager

**Responsibility**: FR → SRS → Code → Test bidirectional traceability. ASPICE SWE.3/SYS.4 compliant. Tracks which requirements are implemented, which code components satisfy them, and which test files verify the implementation.

**Population**: The model is populated from live artifacts by two scripts:
- `scripts/build_traceability.py` — full scan: SAD.md → `[FR-XX]` annotations → test files → model → `TRACEABILITY_MATRIX.md`
- `scripts/check_spec_trace.py` — preflight scan: populates model and returns gap report for Gate 3

**CLI usage**:
```bash
# Populate from artifacts (recommended):
python3 scripts/build_traceability.py --project . [--json] [--export report.json]

# Direct CLI (manual population):
python core/requirement_traceability.py --project-id <id> [--verify] [--export report.json] [--format aspice]
```

**Key dataclasses**:

```python
class LinkType(Enum):
    FR_TO_SRS | SRS_TO_CODE | CODE_TO_TEST | TEST_TO_QUALITY | QUALITY_TO_AUDIT | BIDIRECTIONAL

@dataclass
class Requirement:       # req_id, title, description, priority, status, srs_section
@dataclass
class CodeComponent:     # file_path, functions, classes, fr_id, test_files, coverage
@dataclass
class TestCoverage:      # test_file, test_functions, fr_id, coverage_percentage, status
@dataclass
class TraceLink:         # link_id, source_type/id, target_type/id, link_type, bidirectional
```

**`RequirementTraceability` public API**:

```python
class RequirementTraceability:
    def __init__(self, project_id: str)
    def add_requirement(req_id, title, srs_section=None, ...) -> Requirement
    def add_code_component(file_path, fr_id=None, ...) -> CodeComponent
    def add_test_coverage(test_file, fr_id=None, ...) -> TestCoverage
    def add_link(source_type, source_id, target_type, target_id, link_type, ...) -> TraceLink
    def get_downstream(req_id) -> {srs: [], code: [], test: [], quality: []}
    def get_upstream(component_id) -> {fr: [], srs: [], code: []}
    def verify_completeness() -> {
        total_requirements, srs_coverage, code_coverage,
        test_coverage, verification_rate, total_links,
        missing_mappings: {fr_without_srs, fr_without_code, fr_without_test}
    }
    def get_traceability_matrix() -> list[dict]   # one row per FR
    def export_report(format="standard"|"aspice") -> dict
    def save(filepath) -> None
```

**ASPICE compliance checks** (when `format="aspice"`):
- `SWE_3_B_SP1`: 100% SRS coverage
- `SWE_3_B_SP2`: 100% code coverage
- `SWE_3_B_SP3`: 100% test coverage

**CLI usage**:
```bash
python core/requirement_traceability.py --project-id <id> [--verify] [--export report.json] [--format aspice]
```

---

### §3.25 — `agent_personas/` — Role Persona Library

**Responsibility**: Provides preset agent persona documents loaded by `AgentSpawner._load_persona(role)`. Each ``.md`` file supplies a role description and personality traits injected into the agent prompt ``[PERSONA]`` block.

**Package structure**:

| File | Purpose |
|---|---|
| `__init__.py` | Package docstring — documents available persona files |
| `ARCHITECT.md` | Narrative persona document (read by `_load_persona("ARCHITECT")`) |
| `DEVELOPER.md` | Developer persona |
| `REVIEWER.md` | Reviewer persona |
| `QA_ENGINEER.md` | QA Engineer persona |
| `DEVOPS.md` | DevOps persona |
| `PRODUCT_MANAGER.md` | Product Manager persona |

**Persona types (codebase)**: architect, business_analyst, developer, devops, product_manager,
qa_engineer, requirements_engineer, reviewer, tech_lead (9 personas; matches §10 per-phase
table). Earlier spec revisions listed only 6 — the additional 3 (BUSINESS_ANALYST,
REQUIREMENTS_ENGINEER, TECH_LEAD) were added to satisfy the per-phase A/B role matrix.

**Note**: `AgentSpawner._load_persona(role)` reads `agent_personas/{ROLE.upper()}.md` directly — the Markdown files are the sole authoritative persona source.

---

### §3.26 — `core/cli_phase_prompts.py` — Layer 4 Utilities

**`core/cli_phase_prompts.py`** (24KB): Phase-specific prompt templates used by `harness_cli.py plan-phase` and the pipeline to generate FR execution prompts. Contains one prompt template per phase (P1–P8), formatted for the sub-agent that will execute the FR.

(`core/enforcement_config.py` was removed in Round 9 站0 — its `EnforcementConfig` dataclass had zero importers anywhere, including `enforcement/`; the claim that `FrameworkEnforcer.__init__()` loaded it was documentation drift. The two keys anything actually reads from `.methodology/enforcement.json` — `hr_overrides`, `phase_truth` — are consumed by `core/quality_gate/phase_truth_verifier.py` via raw JSON.)

---

### §3.27 — `harness/git_strategy.py` — Gate-Aligned Git Strategy (10-Push)

**Responsibility**: Implements the **10-Push Gate-Aligned commit + push policy** for harness pipelines. Each push auto-generates `HANDOVER.md` at the repo root via `HandoverGenerator` (§3.28). All operations are no-ops when `--no-git` is passed or `HARNESS_NO_GIT=1` is set. Git failures are logged as warnings — they never block the pipeline.

**Push schedule**:

| Push | ID | Trigger | Commit Message Pattern |
|---|---|---|---|
| ① | `P1-exit-YYYYMMDD` | P1 exit: task plan complete | `phase1(human-review): SRS + P1 deliverables; N FR(s) [list]` |
| ② | `P2-exit-YYYYMMDD` | P2 exit: `manifest` command success | `phase2(human-review): SAD + ADR + quality manifest complete [fr_ids=...]` |
| ③ | `P3-mid-YYYYMMDD` | P3 mid: FR completion ratio ≥ 50% | `feat(P3-mid): N/T FR(s) Gate1 PASS [list]` |
| ④ | `P3-pre-gate2-YYYYMMDD` | P3 pre-Gate2: all FRs at Gate 1 PASS | `feat(P3-pre-gate2): all N FR(s) Gate1 PASS; ready for Gate 2` |
| ⑤ | `P3-gate2-YYYYMMDD` | Gate 2 PASS (P3 exit, score ≥75) | `feat(P3): Gate2 PASS score=XX — N FR(s) implemented` |
| ⑥ | `P4-gate3-YYYYMMDD` | Gate 3 PASS (P4 exit, score ≥80) | `test(P4): Gate3 PASS score=XX — full test suite` |
| ⑦ | `P5-baseline-YYYYMMDD` | P5: BASELINE.md written | `docs(P5): BASELINE.md — review baseline checkpoint` |
| ⑧ | `P6-gate4-YYYYMMDD` | Gate 4 APPROVE (P6, score ≥85) | `release(P6): Gate4 PASS score=XX — pipeline complete` + `git tag gate4-YYYYMMDD-scoreXX` |
| ⑨ | `P7-exit-YYYYMMDD` | P7 completion | `docs(P7): risk register complete` |
| ⑩ | `P8-exit-YYYYMMDD` | P8 completion | `docs(P8): config records — pipeline complete` |

**Local commit policy** (no push): Each Gate 1 PASS per FR commits locally:
```
feat(FR-01): Gate1 PASS — score=88.0 [phase=3]
```
`commit_fr_gate1()` also calls `FRProgressTracker.record_gate1_pass()` (§3.29) to persist progress to `.methodology/fr_progress.json`.

**HANDOVER.md auto-generation**: Every push calls `_write_handover(checkpoint_id, phase, background, status, steps, notes, extra)`, which invokes `HandoverGenerator.write()` (§3.28). The written HANDOVER.md includes a `/compact` prompt for the next Claude session. Failures are silently swallowed — never block the pipeline.

**`.gitignore` auto-maintenance** (`ensure_gitignore()`): Called once per pipeline run. Appends missing entries for harness runtime artifacts:
```
.sessi-work/
.methodology/last_block.md
.methodology/steering_history.json
```

**Public API**:

```python
class GitStrategy:
    def __init__(self, project: Path, enabled: bool = True, push: bool = True)

    def ensure_gitignore() -> None
    def commit_fr_gate1(fr_id, score, phase) -> bool        # Gate 1 local commit + FRProgressTracker

    # PUSH methods (each writes HANDOVER.md before push)
    def commit_and_push_p1(fr_ids, background="", notes=None) -> bool        # PUSH ①
    def commit_and_push_p2(fr_ids, background="", notes=None) -> bool   # PUSH ②
    def commit_and_push_p3_mid(fr_done, fr_total, fr_ids,
                               background="", notes=None) -> bool       # PUSH ③
    def commit_and_push_p3_pre_gate2(fr_ids, background="",
                                     notes=None) -> bool                    # PUSH ④
    def commit_and_push_p4_mid(fr_done, fr_total, fr_ids,
                               background="", notes=None) -> bool       # P4 mid checkpoint (Gate 1 re-eval)
    def commit_and_push_p4_pre_gate3(fr_ids, background="",
                                     notes=None) -> bool                    # P4 pre-Gate3 checkpoint
    def commit_and_push_gate(gate_num, phase, score, n_frs=0,
                             background="", notes=None) -> bool         # PUSH ⑤⑥⑧ + tag
    def commit_and_push_p5_baseline(background="", notes=None) -> bool  # PUSH ⑦
    def commit_and_push_p7(background="", notes=None) -> bool           # PUSH ⑨
    def commit_and_push_p8(background="", notes=None) -> bool           # PUSH ⑩
    def commit_and_push_final(phases: list[int]) -> bool  # deprecated → p7/p8
```

**Helpers** (private):
- `_write_handover(checkpoint_id, phase, background, status, steps, notes, extra)` — calls `HandoverGenerator`, prints checkpoint_id, never raises.
- `_cp(label) -> str` — builds checkpoint_id: `{label}-{YYYYMMDD}` (UTC).
- `_fr_summary(fr_ids) -> str` — compact FR list string, max 5 shown.

**CLI flag**: `--no-git` added to `run-gate`, `finalize-gate`, and `manifest` subcommands.
Env override: `HARNESS_NO_GIT=1` disables git across all commands without a flag.

---

### §3.28 — `harness/handover_generator.py` — Session Handover Writer

**Responsibility**: Renders `HANDOVER.md` at the project root after each significant push checkpoint. Provides the next Claude session with task background, current execution status, next steps, and notes (including `/compact` prompt).

**Module-level constants**:

```python
DEFAULT_NOTES: list[str] = [
    "100% follow SKILL.md",
    "Do NOT commit .sessi-work/ or .methodology/ runtime artifacts",
    "Git failures are warnings — never block the pipeline",
]
```

**Public API**:

```python
class HandoverGenerator:
    def __init__(self, project: Path)

    def write(
        self,
        checkpoint_id: str,   # e.g. "P3-pre-gate2-20260504"
        phase: int,
        task_background: str,
        current_status: str,
        next_steps: List[str],
        notes: List[str] | None = None,   # prepended AFTER DEFAULT_NOTES
        extra: Dict | None = None,        # optional freeform section
        plan_override: str | None = None,       # override plan path in "quick start" section
        deliverables: list[str] | None = None,  # renders "交付物清單" section if non-empty
        resume_phase: int | None = None,        # phase to suggest for resume command
    ) -> Path
```

**Rendered HANDOVER.md structure**:
```markdown
# Harness Methodology — Session Handover

> ⚠️  **開始下一個工作階段前，請先執行 `/compact` 壓縮上下文**，再從「接下來的工作」繼續。

**Checkpoint**: {checkpoint_id} | **Phase**: {phase} | **Generated**: {UTC ISO timestamp}

## ▶ 立即開始（兩步）
1. Clone repo / enter project directory (the `claude` CLI is the only prerequisite)
2. Read the plan: `.methodology/phase{N}_plan.md`

## 快速接手指令（詳細）
Alternative git details and metadata table (phase, plan path, resume command, etc.)

## 任務背景
{task_background}

## 目前執行狀況
{current_status}

## 交付物清單  ← only if deliverables is non-empty
- {deliverable item}
...

## 接下來的工作
1. {next_steps[0]}
...

## 注意事項
- 100% follow SKILL.md
- Do NOT commit .sessi-work/ or .methodology/ runtime artifacts
- Git failures are warnings — never block the pipeline
- {caller-supplied notes...}

## 附加資訊  ← only if extra provided
{extra key-value pairs}

---
*由 HandoverGenerator 自動生成。下次 push 時此檔案將被覆寫。*
```

**Behavior**: Each call **overwrites** the single `HANDOVER.md` at project root. The checkpoint_id (including date suffix) identifies which push created the file.

---

### §3.29 — `harness/fr_progress_tracker.py` — FR Gate-1 Progress Persistence

**Responsibility**: Persists FR Gate-1 PASS/FAIL status across sessions to `.methodology/fr_progress.json`. Enables mid-P3 session recovery — the next session can resume from the last known checkpoint without re-running completed FRs.

**Public API**:

```python
class FRProgressTracker:
    def __init__(self, project: Path, phase: int = 3)

    def record_gate1_pass(self, fr_id: str, score: float, phase: int = None) -> None
    def record_gate1_fail(self, fr_id: str, score: float,
                          phase: int = None, reason: str = "") -> None
    def advance_phase(self, phase: int) -> None     # updates phase in fr_progress.json
    def reset(self) -> None                  # deletes fr_progress.json
    def load(self) -> dict                   # returns scaffold if missing or corrupt

    # Query methods
    def passed_fr_ids(self) -> List[str]     # sorted alphabetically
    def failed_fr_ids(self) -> List[str]
    def pending(self, all_fr_ids: List[str]) -> List[str]   # preserves input order
    def completion_ratio(self, total: int) -> float          # 0.0 if total == 0
    def summary(self, total: Optional[int] = None) -> str    # "2/5 Gate1 PASS"
    def to_status_string(self, total: Optional[int] = None) -> str  # includes failed FRs + "retry"
```

**Persistence schema** (`.methodology/fr_progress.json`):
```json
{
  "phase": 3,
  "updated_at": "2026-05-04T10:00:00+00:00",
  "frs": {
    "FR-001": {
      "status": "gate1_pass",   // "gate1_pass" | "gate1_fail"
      "score": 82.5,
      "phase": 3,
      "timestamp": "2026-05-04T10:00:00+00:00",
      "reason": ""              // only for gate1_fail
    }
  }
}
```

**Resilience**: `load()` returns `{"frs": {}}` scaffold on missing file or corrupt JSON. `_write()` auto-creates `.methodology/` directory. `record_gate1_pass()` and `record_gate1_fail()` overwrite any existing entry for the same `fr_id`.

**Integration with GitStrategy**: `commit_fr_gate1()` (§3.27) calls `FRProgressTracker.record_gate1_pass()` after each successful Gate 1 commit. Wrapped in `try/except` — tracker failure never blocks the git commit.

---

### §3.30 — `harness/retry_utils.py` — Exponential Backoff Utility

**Responsibility**: Generic retry decorator / wrapper with configurable exponential backoff + jitter.

**Public API**:

```python
def retry_with_backoff(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: float = 0.25,          # ±25% random jitter on computed delay
    retryable: Optional[Callable[[Exception], bool]] = None,
                                   # None → retries (OSError, TimeoutError) only
    on_retry: Optional[Callable[[int, Exception, float], None]] = None,
                                   # on_retry(attempt, exc, wait_secs)
) -> T

def _compute_delay(attempt: int, base: float, cap: float, jitter: float) -> float
    # delay = min(base * 2^(attempt-1), cap) * (1 ± jitter)
    # attempt=1 → base; attempt=2 → 2×base; capped at max_delay
```

**Behavior**:
- `max_attempts < 1` → raises `ValueError` immediately.
- Non-retryable exception (not matched by `retryable` predicate) → re-raised on first occurrence; never retried.
- Exhausted all attempts → re-raises the last exception.
- `on_retry` is `None` → prints `"Retry {attempt}/{max_attempts} after {wait:.1f}s: {exc}"`.

**Integration**: Used by `AgentDrivenAutoResearch` to wrap subprocess calls with retry logic and fallback.

---

### §3.31 — `core/atomic_io.py` — Atomic File Write Helpers

**Responsibility**: Crash-safe write primitives for all `.methodology/` state files. Prevents truncated-file corruption from mid-write Ctrl-C, OOM kill, disk-full, or NFS hiccup (CV-3).

**Pattern**: write full payload → temp file in same directory → `fsync` → `os.replace` (atomic on POSIX + NTFS) → best-effort `fsync` parent dir.

**Public API**:

```python
def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None
def atomic_write_json(path: Path, data: Any, *, indent: int = 2, ensure_ascii: bool = False) -> None

@contextmanager
def file_lock(lock_path: Path, *, blocking: bool = True) -> Iterator[Optional[int]]
    # Cross-process exclusive lock via fcntl.flock. No-op on Windows (no fcntl).

def state_lock_path(project_root: Path) -> Path
    # Returns project_root / ".methodology" / ".state.lock"
```

**Lock scope (SG-12)**: All reads AND writes of `state.json` inside `advance-phase` and `_update_state_checkpoint` are serialized via `file_lock(state_lock_path(project))`. `fr_progress.json` advance is executed inside the same lock to keep both files in sync atomically.

**Windows fallback**: `fcntl` import fails silently; `file_lock` yields immediately without locking. Atomic rename via `os.replace` still works (NTFS).

---

### §3.38 — `scripts/ci_state_helper.py` — CI Safe State Extractor

**Responsibility**: Replaces unsafe bare `python3 -c "import json; ..."` one-liners in CI workflows (CV-5). Returns sensible defaults on missing / unreadable / malformed `state.json` instead of raising an uncaught traceback that blocks every push.

**Public API** (CLI):

```
python3 scripts/ci_state_helper.py get <field> [--state-file .methodology/state.json] [--default ""]
python3 scripts/ci_state_helper.py is-p8 [--state-file .methodology/state.json]
```

**Exit codes**: `0` — value printed to stdout; `10` — invalid usage.

**Behavior**: `get` prints the field value or `--default` on any failure. `is-p8` prints `true` iff `current_phase > 8`, or `current_phase == 8` and `last_milestone_command` contains `"p8"`. Errors emit `[WARN]` to stderr; never raises.

---

### §3.39 — `scripts/rotate_decision_logs.py` — Decision Log Archiver

**Responsibility**: Prevents unbounded growth of `.methodology/decision_logs/` (SG-8). Archives date-named subdirectories older than `--retention-days` (default 30) to `<dir>.tar.gz`, then removes the source directory.

**Public API** (CLI):

```
python3 scripts/rotate_decision_logs.py [--project .] [--retention-days 30] [--dry-run]
```

**Exit codes**: `0` — rotation completed (may be a no-op); `1` — hard error.

**Safety**: `KeyboardInterrupt` during tar creation cleans up the partial archive so a subsequent run can retry successfully (B6 fix). Non-date subdirectories are left untouched. `--dry-run` reports what would be archived without modifying the filesystem.

---

### §3.40 — `scripts/workflowgen/` — Workflow JS Generator (render-from-SSOT)

**Responsibility**: Generates the 8 phase Claude Code Workflow scripts (`.claude/workflows/phaseN-*.js`) from a single Python source, mirroring `scripts/plangen`'s architecture. Before this module existed, the 8 files were hand-maintained with shared logic (`resolveRepo()`, the A/B review machine, verdict schemas) copy-pasted across every file — the Claude Code Workflow runtime forbids `import`/`require`/`fs.*`/`process.*` in shipped scripts (docs/WORKFLOW_PLAYBOOK.md §4), so cross-file sharing at the JS level is architecturally impossible; a bug fix (`58f8b2f`, submodule-worktree exclusion in `resolveRepo()`) required 8 separate edits. `workflowgen` moves the shared logic to Python, where normal function reuse applies, and generates the constrained, self-contained JS as a build step.

**Package layout**:

| Module | Role |
|---|---|
| `js_blocks.py` | Shared JS block renderers — `resolveRepo`/budget-guard/verdict schemas/`checkManifestIntegrity`/the A-B review machine (`render_generic_ab_loop`, `render_build_b_prompt`, `render_persist_approval`, …)/`render_rule_prose` (re-exports `scripts.plangen.blocks._load_rule`, keeping `@rule`-tagged prompt text single-sourced with the plan generator) |
| `phase_specs.py` | One `generate_phaseN()` per phase — declarative step sequences, milestone type, artifacts allowlist; genuine per-phase differences (e.g. phase1's `runSubTask` vs phase2's `abLoop` — confirmed non-unifiable by full diff, see module comments) are kept as phase-local verbatim blocks rather than forced into a shared parameter surface |
| `js_src/*.mjs` | Pure JS helper functions (`balancedJsonAt`, `extractLastJson`, `parseAgentJson`) as real `.mjs` source, directly testable with `node --test`; `export` is stripped at generation time when inlined into a phase file |
| `generate_workflows.py` | Facade: `--write` renders all migrated phases to `.claude/workflows/`, `--check` diffs generator output against the on-disk files (exit 1 on drift) |

**Verification model**: harness-methodology cannot execute a Claude Code Workflow run itself (that requires the Workflow tool's runtime, exercised from the integration-test consumer side). Three layers substitute: (1) `tests/test_workflowgen_golden.py` — byte-equal lock between generator output and the committed `.claude/workflows/*.js`, so a hand-edit or silent generator drift both fail loudly; (2) `tests/test_workflow_js_conventions.py` — playbook §4 runtime-construct lint (`scripts/workflow_audit/js_lint.py`) plus a `node --check` syntax gate; (3) `tests/test_workflow_plan_alignment.py` / `tests/test_workflowgen_equivalence.py` — the generated JS's command set and `phase()`/`agent()` structure stay aligned with the corresponding `phaseN_plan.md` and with each migration's pre-image. See docs/WORKFLOW_ALIGNMENT_AUDIT.md and docs/WORKFLOW_PLAYBOOK.md §13 for the full migration record and the submodule consumption/hand-patch-prohibition (HR-17) rules.

**Simulation testbed (Round 12 站1)**: `js_src/sim_runner.mjs` executes the REAL generated files in Node with the workflow runtime globals mocked (`agent`/`phase`/`log`/`args`/`budget`; top-level await + top-level return reproduced by async-function wrapping). `makeHappyResponder()` synthesizes passing replies from each call's declared schema shape plus ordered regex overrides for the text-verdict protocols. `sim_runner.test.mjs` (bridged by `tests/test_workflow_sim.py`, pass-count floor ≥ 23) drives every file end-to-end through happy/null-agent/hallucination/garbage-JSON/missing-field scenarios — closing the "no coverage for the dynamic-workflow execution substrate" gap commit cef32c4 named as the v2.13.2 crash's root cause. Honest boundary: the sim covers workflow-JS LOGIC and API shape only; the OS sandbox/permission substrate is covered by `AgentSpawner.preflight_substrate()`'s live probe at run-phase entry (§3.7), and live LLM behavior only by real E2E runs. `tests/test_workflow_dispatch_registry.py` (Round 12 站2c) additionally classifies every `agent()` dispatch label by determinism class and verdict anchor — a new dispatch fails CI until classified.

---

## 7. Runtime Prerequisites & Dependencies

### 7.1 SSI — Embedded Evaluation Engine

Gate evaluation assets (prompts, scripts, schemas) are **embedded** in `harness/ssi/` — no
external package install required.

| Asset | Path | Purpose |
|---|---|---|
| Evaluation prompt | `harness/ssi/prompts/evaluate_dimension.md` | Per-dimension scoring instructions |
| Verification prompt | `harness/ssi/prompts/verify_round.md` | Round verification |
| Scripts | `harness/ssi/scripts/` | `score.py`, `issue_tracker.py`, `verify_tools.py`, etc. |
| Schema | `harness/ssi/schemas/harness_gate_result.schema.json` | Result validation |

The only remaining reference to `software_self_improvement` is in
`harness/issue_tracker_ext.py`, which imports `IssueTracker` with an inline stub
fallback if the package is absent.

### 7.2 Three-Phase Gate Evaluation (no subprocess)

Gates use a three-phase API. Claude **is** the evaluation engine — no subprocess runner:

```
Phase 1 — prepare_gate(gate_num, project_root, phase [, fr_id])
  ├─ Loads gate config from harness/gate_configs/gate{N}_*.yaml
  ├─ CRG reconnaissance (Gate 3/4) or refresh (Gate 2)
  ├─ CRG Tier 3 per-dim context (Gate 3/4)
  ├─ Reads SAB baseline from quality_manifest.json
  └─ Returns GateContext with evaluation_prompt()

Phase 2 — Claude evaluates inline
  ├─ Reads ctx.evaluation_prompt() + harness/ssi/prompts/evaluate_dimension.md
  ├─ Runs tool checks via harness/ssi/scripts/score.py
  └─ Writes ctx.work_dir/gate{N}_result.json

Phase 3 — finalize_gate(ctx)
  ├─ Reads gate{N}_result.json
  ├─ Tool execution evidence enforcement (S3)
  ├─ Threshold check → raise GateBlockedError on failure
  ├─ Updates quality_manifest.json
  └─ Records DecisionLog + EffortTracker
```

### 7.3 Workspace Layout

```text
.sessi-work/
  gate{N}_result.json          ← Claude writes (harness reads)
  issue_registry.json          ← persistent across rounds (score.py, issue_tracker.py)
  round_{k}/scores/*.json      ← per-round evaluator scores
  crg_metrics.json             ← crg_bridge + crg_analysis.py write
  crg_reconnaissance.json      ← CRG recon protocol output (Gate 3/4)
  harness_verification/        ← tool_runners.py audit trail

.methodology/
  gate{N}_result.json          ← Persistent gate passed evidence (copied on finalize-gate pass)
  quality_manifest.json        ← Comprehensive project quality manifest
  state.json                   ← FSM state persistence
```

### 7.4 External Dependencies (All Resolved)

No external package dependencies remain. All gate evaluation logic is self-contained
within this repository.

### Previously Fixed Stubs (resolved in this version)

| Fix | Location | Resolution |
|---|---|---|
| ① SSI runner stub | `harness_bridge._invoke_harness()` (removed) | Replaced `NotImplementedError` with subprocess call, then subprocess replaced with two-phase inline API (prepare_gate → Claude eval → finalize_gate). `_invoke_harness()` no longer exists. |
| ② `parse_sad` import failure | `harness_bridge.generate_quality_manifest()` | Fixed by adding `parse_sad()` to `scripts/generate_sab.py` |
| ③ P7/P8 Claude routing not wired | `core/agent_spawner.spawn()` | `get_reviewer_model(phase, role)` now checked before Hermes dispatch; P7/P8 auto-route to Claude |
| ④ ~~Gate 4 Hermes APPROVE not enforced~~ | ~~`harness_bridge.run_gate()`~~ | **REMOVED in v2.6.0** — `run_gate()` deleted; `finalize_gate()` does NOT require Hermes APPROVE (Gate 4 is score + quality_complete only). |
| ⑤ `parse_sad` alias missing | `scripts/generate_sab.py` | Added `parse_sad()` function wrapping `extract_sab_from_sad`, with correct key mapping |


---

## 8. Future Work — Score Roadmap & Open Items

> **Baseline score (v2.0)**: 92/100 (Academic Benchmark, 7-dimension framework).
> **v2.1 delta**: Sequential A/B + dep-ordered decomposition — no score regression; improves A/B reliability for large docs (SRS.md, SAD.md).
> Target ceiling ~96/100; remaining 8 points have concrete unlock conditions below.

### 8.1 Score Roadmap (post-v2.0 unlocks)

| Priority | Action | Score Delta | Unlock Condition | Dimension |
|---|---|---|---|---|
| **P1** | ~~SSI result field name verification~~ | ✅ **DONE (v2.0.2)** | `_parse_result()` dual-fallback handles both `open_critical_count`/`open_critical` field name variants — see §8.2. | A |
| **P1** | ~~`constitution/` package stub or real impl~~ | ✅ Done (v2.0.1) | `constitution/` implemented — `BVSRunner`, `CitationParser` deployed. (`VerificationConstitutionChecker` removed later in T3) | A |
| **P2** | ~~`harness_bridge` empirical project validation~~ | ✅ **DONE** | omnibot-full project (Gate 4 score 89.6, 2026-05-14). Tier 1 deterministic scoring stable, subprocess call chain works end-to-end. | A (20→21) |
| **P2** | CRG activation + empirical data | **+1 -> 94** | First real project run with CRG MCP available. Validates `min(tool, llm)` floor and `crg_metrics.json` structural signals. Currently `CRGBridge._check_available()` returns `False` in standalone mode | E (10->11) |
| **P3** | ASPICE full traceability matrix (Phase E docs) | ✅ **DONE** | `scripts/build_traceability.py` populates `RequirementTraceability` model from SAD.md + `[FR-XX]` annotations + test files, auto-generates `TRACEABILITY_MATRIX.md` with ASPICE SWE.3 compliance. `scripts/check_spec_trace.py` upgraded to v2 content-level. `PhaseHooks.preflight_traceability()` blocks at P4+. | C (15→16) |
| **P4** | Developer-side deterministic tooling | **+1-2 -> 96** | Replace or augment Claude developer agent with static analysis pipeline (mypy strict, semgrep, complexity checker). Reduces D-dimension LLM dependency from 13/15 to 15/15 | D (13->15) |

### 8.2 Open Integration Items (Ready, No External Blockers)

| Item | File | Status | Action |
|---|---|---|---|
| SSI output field mismatch | `harness/harness_bridge.py` | ✅ **Resolved (v2.0.2)** — `_parse_result()` now uses dual-fallback: `raw.get("open_critical", raw.get("open_critical_count", 0))` and `raw.get("open_high", raw.get("open_high_count", 0))`. Accepts both SSI runner field name variants. | — |
| `constitution.*` graceful degrade | `steering/integrations.py` | ✅ **Resolved (v2.0.1)** (Note: `steering` and `VerificationConstitutionChecker` later removed in T3/T4) | See §3.19 |
| HR-12 real limiter not wired | `steering/integrations.py` | ✅ **Resolved (v2.0.2)** (Note: `steering` later removed in T4) | — |
| Gate 4 Hermes approval timeout | `harness/harness_bridge.py` | ✅ **Resolved (v2.0.2, updated v2.2)** — `HarnessBridge.GATE4_HERMES_TIMEOUT_MS = int(os.environ.get("HERMES_TIMEOUT_MS", "120000"))` (default 120 s); `ReviewerRouter.HERMES_TIMEOUT_MS` reads same env var (default 120 s). Both sync via env var — set `HERMES_TIMEOUT_MS` to override globally. | — |
| `enforcement.json` policy hot-reload | `enforcement/policy_engine.py` | ✅ **Resolved (v2.0.2)** — `PolicyEngine.reload_policy(json_path)` hot-reloads policies by ID from `enforcement.json`; `PolicyEngine.from_json(json_path)` classmethod for fresh engine. `harness_cli.py reload-policy` command exposes this as CLI (7th command). | — |
| Sequential A/B + dep-ordered decomposition | `harness/reviewer_router.py` | ✅ **Resolved (v2.1)** — `_decompose_with_deps()` replaces `_maybe_decompose()`; `review()` sequential for-loop replaces list comprehension; `_enrich_with_context()` injects `approved_context`; `_topological_sort()` ensures dependency-safe order; `SubTask` dataclass tracks label/deps/index/total. | See §3.2, §4.2 |
| crg-003: high coupling quality_gate ↔ tests-parse | `core/quality_gate/` | ✅ **Resolved** — `parsers/` sub-package extracted: `DevelopmentLogParser` from `ab_enforcer.py`, `SpecTrackingParser` from `spec_tracking_checker.py`. All regex in `parsers/`; checkers contain zero `re.search()` calls. | See §3.16 |
| crg-004: test coverage <80% | `core/quality_gate/`, all scoped modules | ✅ **Resolved (v2.2)** — W0-W11 TDD waves: coverage 16% → **90.19%** (threshold met, exceeded). 986 tests (983 passed + 3 skipped). W7 added 92 tests (C+D); rounds 9-11 added 219 further tests. Production bugfix: `ab_enforcer.verify_qa_not_developer` returned string not bool (Python `and` short-circuit); wrapped with `bool()`. Scoped via `.coveragerc` to core business logic only. | See §8.4 |

### 8.4 Coverage Gap Analysis — v2.2 current state (rounds 9–11 complete)

**Current scoped coverage: 90.19%** (487 stmts uncovered / 4963 total). Rounds 9-11 closed remaining Category B/C gaps via targeted mocking.

#### History

| Version | Coverage | Tests | Notes |
|---|---|---|---|
| v1.7 baseline | 80.12% | — | 929 stmts uncovered / 4673 total |
| v1.8 (W7) | 84.16% | 767 | Category C+D closed via unit tests with tmp_path |
| v2.2 (rounds 9–11) | **90.19%** | **986** | Categories B/C further reduced; bugfix: `ab_enforcer.verify_qa_not_developer` bool coercion |

#### Category A — Intentionally Untestable (Excluded from priority)
| File | Miss | Reason |
|---|---|---|
| `core/adapters/phase_hooks_adapter.py` | 73 | Thin adapter over external `PhaseHooksRunner`; zero business logic — 100% subprocess delegation |
| `core/cli_phase_prompts.py` | 6 | Pure string constants (prompt templates); no executable logic |

**Verdict**: 0% is acceptable. Do not add tests.

#### Category B — Subprocess/External-API Bound (Residual)
| File | Miss (current) | Blocking reason |
|---|---|---|
| `enforcement/framework_enforcer.py` | ~94 | `run()`, `check_*()` paths call `subprocess.run(git)` + multi-file I/O; integration-test territory |
| `harness/harness_bridge.py` | 26 (78%) | Gates 2–4 require a real SSI subprocess; cannot stub without full integration env |
| `core/quality_gate/stage_pass_generator.py` | ~104 | `git_push()`, `_log_to_development_log()` — require real git repo + subprocess chain |

**Verdict**: Block behind `@pytest.mark.integration`; run in CI with full Docker env.

#### Category C — Business Logic (Residual — partially closed in rounds 9-11)
| File | Miss (current) | Status |
|---|---|---|
| `core/quality_gate/phase_truth_verifier.py` | 11 (93%) | Substantially closed (was 71/57%) |
| `core/quality_gate/spec_tracking_checker.py` | 19 (81%) | Partially closed (was 54/47%) |
| `core/quality_gate/ab_enforcer.py` | 6 (93%) | Substantially closed (was 70/33%) |
| `enforcement/constitution_policy_sync.py` | 0 (100%) | ✅ Fully closed (was 105/26%) |

#### Summary

| Category | Current Strategy |
|---|---|
| A — Untestable constants/adapters | Accept 0% — never |
| B — Integration/subprocess bound | `@pytest.mark.integration` + CI Docker env |
| C — Business logic residual | Closed 90%+; remainder (<20 stmts each) deferred |

### 8.3 Technical Debt (Lower Priority)

| Item | File | Notes |
|---|---|---|
| ~~Chinese-language comments~~ | `core/cli_phase_prompts.py`, `core/quality_gate/ab_enforcer.py`, `core/quality_gate/phase_truth_verifier.py`, `core/quality_gate/spec_tracking_checker.py` | ✅ **Resolved** — all 4 files confirmed 0 CJK characters (translated in Apr 2026 batch) |
| ~~`cli.py` standalone boundary~~ | `cli.py` (288KB, v6.102.0) | ✅ **Resolved** — README line 30 already documents `harness_cli.py` as "Standalone CLI entry point (plan-phase, run-gate, finalize-gate, etc.)" |
| `phase_auditor.py` | `scripts/phase_auditor.py` | ✅ Documented in §3.20 |
| ~~`EnsembleScorer` threshold calibration~~ | `detection/ensemble_scorer.py` | ✅ **Resolved** — `PASS_THRESHOLD = 0.65` removed; threshold is now a per-call parameter with caller-provided calibration |

### 8.5 Open Items from 2026-06-12 SAD ↔ Code Audit

> **Audit policy**: code is treated as the source of truth. Spec drift (where code has
> moved ahead of SAD) is fixed by editing SAD. Reverse-direction items (where SAD documents
> a feature the code does not implement) are **NOT auto-fixed**; they are listed below
> for human review in the next round.

#### 8.5.1 Reverse-direction (SAD ahead of code — deferred for human confirmation)

| # | Item | Spec source | Code status | Action |
|---|---|---|---|---|
| R-1 | `check-checklist` CLI command | §2.3 line 97 (earlier revision) | Not implemented (no `cmd_check_checklist`, no sub-parser) | Removed from §2.3. To reinstate: re-introduce the spec'd `verify phase plan mandatory items [x]` behaviour. |
| R-2 | `should_auto_approve_gate4()` function | §3.15 line 1194 (earlier revision) | Not implemented (only `should_auto_approve_p1p2` exists in `core/quality_gate/confidence_scorer.py`) | Marked as not implemented in §3.15. To reinstate: add a C1-C7-thresholded gate4 auto-skip function. |
| R-3 | `scripts/CLAUDE.md` | §3.20 line 2110 (earlier revision) | File does not exist | Removed from §3.20. To reinstate: write the 3KB script-level usage doc. |

#### 8.5.2 Forward-direction (code ahead of SAD) — applied in 2026-06-12 revision

| # | Item | Section touched | What changed |
|---|---|---|---|
| F-1 | NFR→dimension mapping table | §3.1 | Replaced spec's 3-row table (maintainability→readability, reliability→error_handling, testability→test_assertion_quality) with the actual 9-row keyword-substring mapping from `HarnessBridge._nfr_type_to_dim()` |
| F-2 | `GateConfig` is a `@dataclass`, not a `TypedDict` | §3.1 | Fixed the misleading "TypedDict" wording in the GateContext note |
| F-3 | `review()` uses parallel-wave execution, not sequential for-loop | §3.2 | Rewrote the execution sequence pseudocode to describe `_execute_parallel_waves` + `ThreadPoolExecutor` + `cancel_event` early-exit |
| F-4 | `crg_bridge` does not implement `_ssi_root()` / `SSI_ROOT` env var | §3.3 | Noted the divergence: code uses `harness.crg_api` fallback; `SSI_ROOT` is not referenced |
| F-5 | `_check_available()` has no per-instance cache | §3.3 | Corrected to "bool (no per-instance cache; immutable global)" |
| F-6 | `EffortTracker` uses lazy DB init | §3.5 | Replaced the "DB auto-created on `__init__`" line with the lazy `_ensure_db()` description |
| F-7 | `AgentSpawner.spawn()` has 6 extra kwargs | §3.7 | Added the `max_turns, phase_sop_override, persona_override, mcp_config, setting_sources, permission_mode` kwargs to the spec signature |
| F-8 | `finalize_gate` Gate 1 blocking adds a per-gate composite floor | §3.1 | Documented the `_gate1_score_gate` check as a defensive tightening not in earlier revisions |
| F-9 | 3 extra `preflight_*` methods | §3.8 | Added `preflight_fr_spec_consistency`, `preflight_reliability_lint`, `preflight_config_liveness` to the table |
| F-10 | `parsers/development_log_parser.py` no longer exists | §3.15 | Removed from the parsers table; the `ab_enforcer.py` it was extracted from was already removed |
| F-11 | `ClaimExtractor` is module-level functions, not a class | §3.19 | Changed the row to "(module-level functions)" with import instructions |
| F-12 | `constitution/__init__.py` re-exports only 3/8 classes | §3.19 | Replaced "re-exports all public classes" with the actual 3-of-8 enumeration |
| F-13 | `scripts/CLAUDE.md` no longer exists | §3.20 | Removed from the table; pointed to script `--help` instead |
| F-14 | `WorkspaceManager.__init__` does not default `phase=3` | §3.34 | Removed the misleading `= None` default; signature is `phase: int` (required positional) |
| F-15 | `NoopProvider.chat()` returns 0.5-JSON envelope, not empty string | §3.36 | Rewrote the row with the actual envelope payload |
| F-16 | `ClaudeProvider` was renamed to `SubprocessProvider` | §3.36 | Replaced `ClaudeProvider` row with `SubprocessProvider` row + rename note |
| F-17 | `SteeringIntegrator.bvs_integrator` and `should_continue` are `@property` | §3.12 | Added an implementation note documenting the property-vs-method deviation |
| F-18 | `agent_personas/` has 9 roles (3 added beyond §3.25's original 6) | §3.25 | Replaced the persona list with the full 9; reconciled with §10 per-phase table |
| F-19 | `harness_cli.py` has 39 sub-commands (not 33) | §2.3 | Listed 31 spec-mandated commands (the 32nd was `check-checklist`, moved to REMOVED), marked `check-checklist` as REMOVED, added 8 new commands under "ADDED" (the two originally duplicated in ADDED — `check-test-inventory` and `spec-coverage-check` — are spec-mandated, not added) |
| F-20 | SAB §6 omits extras (`harness/audit/`, `core/atomic_io.py`, `orchestration/`, etc.) | §6 | Added `_sab_v1_2_additions` key in the SAB JSON listing Layer-1 and Layer-2 extras; added the missing modules to Layer 2's `modules` array |

#### 8.5.3 Stale artifacts (not in any spec section — cleanup candidates)

| Path | Size | Status |
|---|---|---|
| `core/quality_gate/cross_artifact.py.bak` | 10.4 KB | ✅ **Deleted 2026-06-12** (moved to macOS Trash, recoverable) |
| `core/quality_gate/phase_truth_verifier.py.bak` | 21.9 KB | ✅ **Deleted 2026-06-12** (moved to macOS Trash, recoverable) |
| `enforcement/policy_engine.py.bak` | 14.3 KB | ✅ **Deleted 2026-06-12** (moved to macOS Trash, recoverable) — was byte-identical to current `policy_engine.py` (pure dead duplicate) |
| `kill_switch/circuit_breaker.py.bak` | 5.2 KB | ✅ **Deleted 2026-06-12** (moved to macOS Trash, recoverable) |
| `steering/steering_loop.py.bak` | 23.9 KB | ✅ **Deleted 2026-06-12** (moved to macOS Trash, recoverable) |

Verified safe to delete: zero non-`.bak` files in the repository reference any of the
5 paths (grep `*.bak` against all `*.py` and config files returns no results).
Post-deletion smoke test: all 5 affected modules (`kill_switch`, `steering`, `enforcement`,
`core.quality_gate.{phase_truth_verifier,cross_artifact}`) still import cleanly, and
all 172 kill_switch-related tests still pass.

#### 8.5.4 Latent bug found during audit (not in any spec section — fix candidates)

| Path | Issue | Fix recommendation |
|---|---|---|
| `kill_switch/interrupt_engine.py:40-50` | `self._audit_logger.log_event(event_type=..., agent_id=..., ...)` is called with kwargs, but `AuditLogger.log_event(entry: AuditEntry)` in `kill_switch/audit_logger.py:66` (post-fix) takes a single positional `AuditEntry` object — pre-fix at line 39 the method was a `pass` stub with positional-only signature. When an external audit logger was passed to `KillSwitch(audit_logger=...)`, the call raised `TypeError: log_event() got an unexpected keyword argument`. The class check at `kill_switch/interrupt_engine.py:36-38` (`hasattr(audit_logger, 'log_event') or hasattr(audit_logger, 'log_escalation')`) routed to the wrong interface. | ✅ **Fixed in commit `91733db` (2026-06-12)** — `AuditLogger.log_event()` now accepts **both** call forms (object form + kwarg form), synthesises an `AuditEntry` from kwargs when needed, and the method body is no longer a `pass` stub — it now actually writes a JSONL audit log to `{log_dir}/audit.log` and maintains a 1024-entry in-memory ring buffer for `query()`. Added `log_escalation()` alias and `read_log_file()` for post-mortem. Follow-up commit `19251e3` adds a regression test reproducing the original kwarg call path, a `tmp_path` fixture for the existing `test_log_event_is_stub` to avoid polluting global tempdir, and fixes a dead-code branch in the OSError handler (`except OSError` widened to `except Exception` so the `isinstance(exc, (TypeError, ValueError))` re-raise actually fires on programmer errors). Post-fix: 3 `TestAuditLoggerExtra` tests pass (incl. new kwarg-form regression test), 20 `kill_switch` audit/interrupt tests pass, no regressions. |

---

## 9. Agent Execution Loop

The agent has **exactly one source of truth at any moment**:

| Moment | Source of truth | What the agent does |
|--------|----------------|---------------------|
| Session start / phase entry | **SKILL.md** | Read framework rules, phase routing, gate protocol |
| Inside a phase | **phase plan file** | Follow the plan step-by-step (do NOT re-read SKILL.md for task details) |
| After a crash / context reset | **`generate-next-plan`** | Get position report, then resume plan |

### Execution Mode — Manual Only

| Mode | Command | When to use |
|------|---------|-------------|
| **Manual (default)** | Follow `phaseN_plan.md` checklist top-to-bottom | Normal autonomous execution |
> The `run-pipeline` automated mode was removed in v2.6.0 (unstable at current maturity level).

### Execution Loop (per phase) — Manual Mode

```
1. ENTER PHASE
   python harness_cli.py plan-phase --phase N --project $REPO \
       --output $REPO/.methodology/phaseN_plan.md
   → ONE command. plan-phase calls generate_full_plan.py internally.
   → Plan is THE complete authority for phase N (preflight + A/B dev + gates + advance)

2. FOLLOW PLAN
   Execute checklist items top-to-bottom. Key block types:
     [PREFLIGHT]    run-phase --phase N   (FSM + Constitution + kill-switch + drift)
     [ENV-CHECK]    run-env-check → finalize-env-check (one-time preamble, P3+ FR-loop only)
     [A/B Work]     Agent A develops → Agent B reviews → sessions_spawn.log
     [CHECKPOINT-K] run-gate → evaluate inline → finalize-gate → git push

3. GATE FAIL?
   Gate 1: fix failing dim(s) → repeat G1a→G1b→G1c until PASS → then G1d push
   Gate 2/3/4: fix → repeat G{N}a until CASE 1 PASS or CASE 3 PLATEAU

4. CHECKPOINT SAVED
   After every git push: continue to next checklist item.
   Do NOT call generate-next-plan unless recovering from a crash.

5. PHASE COMPLETE
   Follow "Phase N → Phase N+1" section at end of plan (back to step 1).
```

### Phase Completion Checklist (Mandatory — Every Phase)

Before advancing to the next phase, the agent MUST confirm ALL of the following:

| # | Step | How | Applies to |
|---|------|-----|------------|
| 1 | All checkpoints ✓ | Review plan — every `CHECKPOINT-K` marked done | All phases |
| 2 | HANDOVER.md written | `harness_cli.py` writes it automatically via GitStrategy on push | All phases |
| 3 | Git pushed to remote | Confirmed push output (no "push skipped" message) | All phases |
| 4 | Next phase plan exists | `plan-phase --phase N+1` must have been run | P1–P7 |
| 5 | state.json updated | Phase advanced in `.methodology/state.json` | All phases |
| 6 | Git tag (Gate 4 only) | `harness-v4-YYYYMMDD-scoreXX` pushed to origin | P6 exit |

> **HANDOVER.md** is written to the project root at every phase-boundary push.
> It contains: checkpoint_id, phase, background, current status, next steps.
> After a crash, read HANDOVER.md first — it tells you where you were and what to do next.

### Recovery (after crash or context reset)

```bash
# Where am I?
python harness_cli.py generate-next-plan --project $REPO

# Output example:
#   Phase      : 3 (Implementation)
#   Plan file  : .methodology/phase3_plan.md  <- open this file
#   Last ckpt  : CHECKPOINT-2 (Gate 1 / FR-02) PASS
#   Next ckpt  : CHECKPOINT-3 (Gate 1 / FR-03)
#   [ACTION]     search plan for "CHECKPOINT-3", resume from there

# Then: open plan file, search "### CHECKPOINT-3", follow from there.
```

### Decision Rules

- **SKILL.md governs**: phase order, gate thresholds, hard rules (HR-01–HR-15), A/B protocol.
- **Plan governs**: task sequence within a phase; specific file paths; CLI commands.
- **Conflict resolution**: SKILL.md wins on rules; plan wins on task sequence.
- **Do NOT re-read SKILL.md** mid-phase for task details — the plan is the authority.
- **generate-next-plan** is for recovery only; do not call it during normal execution.

---

## 10. Autonomous Execution Protocol

Claude Code can run the **full P1→P8 pipeline autonomously** using the Bash tool.
The pipeline executes 100% autonomously with zero mandatory human checkpoints, fully relying on automated Quality Gates and Phase End Audits for progression control.

### One-Prompt Launch

> **Prerequisite**: SRS.md must exist with `### FR-XX:` sections defining each functional requirement.
> SAD.md must document architecture decisions. Both are **human-provided preconditions** —
> the manual flow pauses at P1/P2 exit until these files are present.
> P1/P2 are NOT auto-generated by the agent; the human creates or provides them,
> then proceeds with `plan-phase`, `run-phase`, and `advance-phase`.

```
"Build [description]. Repo: [path]. Tech: [stack].
Run harness-methodology P1→P8 step by step.
Gate 4 needs my Telegram APPROVE — handle everything else."
```

### Per-Phase A/B Work Content

| Phase | Agent A Role | Agent B Role | Agent A Task | Agent B Task |
|-------|------------|------------|--------------|--------------|
| **P1** | REQUIREMENTS_ENGINEER | BUSINESS_ANALYST | Draft SRS.md with `### FR-XX:` sections per requirement | Review SRS.md against business goals; verify all FR-IDs are traceable |
| **P2** | ARCHITECT | TECH_LEAD | Design architecture (SAD.md); write ADR.md for key decisions | Review SAD.md for feasibility, consistency, and SRS alignment |
| **P3** | DEVELOPER | REVIEWER | TDD: RED (write failing test) → GREEN (implement FR) → REFACTOR | Review code against SRS/SAD; verify tests pass; check citations |
| **P4** | QA_ENGINEER | ARCHITECT | Execute TEST_PLAN.md per FR; verify branch coverage ≥ 80%; run regression suite | Review test results; confirm coverage gaps are documented; validate test traceability to FRs |
| **P5** | DEVELOPER | REVIEWER | Verify each FR's acceptance criteria against SRS.md; confirm deliverable completeness | Review acceptance verification; cross-check BASELINE.md against SRS + Gate 2/3 results |
| **P6** | QA_ENGINEER | ARCHITECT | Generate QUALITY_REPORT.md (12-dim audit); prepare RELEASE_NOTES.md | Review quality report; confirm all FRs are merged and Gate 4 score ≥ 85 |
| **P7** | DEVOPS | ARCHITECT | Assess risk per FR (impact × likelihood); draft mitigation plans; populate RISK_REGISTER.md | Review risk assessments; verify mitigation plans are actionable; check RISK_STATUS_REPORT.md |
| **P8** | DEVOPS | ARCHITECT | Document config per FR (env vars, feature flags, secrets); populate CONFIG_RECORDS.md | Review config records; verify environment parity (dev/staging/prod); confirm no secret leaks |

> All phases: Agent A ≠ Agent B (HR-01 workflow — dispatched as separate sub-agent sessions).
> `sessions_spawn.log` is verified by `PhaseTruthVerifier` to ensure JSONL structure and A/B role coverage.

### Pipeline Exit Codes

| Code | Meaning | Action |
|---|---|---|
| 0 | All phases complete | Done |
| 1 | Hard error | Diagnose |
| 2 | Critical gaps detected (M3 run-gap-analysis) | Fix gaps, re-run |
| 10 | PAUSE — gate evaluation needed | Run-gate → evaluate → finalize-gate → re-run pipeline |
| 11 | Phase Truth < 90% (HR-11) | Fix and re-run with `--phase-from N` |

---

## 11. Phase E2E Flow

Full Mermaid diagram: [`docs/superpowers/plans/harness_phase_flowchart.md`](docs/superpowers/plans/harness_phase_flowchart.md)

### Phase Entry/Exit Matrix

| Phase | Entry Check | Exit Gate | Exit Score | Structure | Key Artifacts |
|-------|---|---|---|---|---|
| **P1** | None | Human peer review | N/A | Static | SRS.md + sessions_spawn.log |
| **P2** | Human (P1 APPROVE) | Human peer review | N/A | Static | SAD.md, ADR.md, quality_manifest.json + sessions_spawn.log |
| **P3** | Human (P2 APPROVE) | Gate 2 | ≥ 75 | Per-FR Loop | Code + sessions_spawn.log |
| **P4** | Gate 2 (P3) | Gate 3 | ≥ 80 | Per-FR Loop | TEST_RESULTS.md + sessions_spawn.log |
| **P5** | Gate 3 (P4) | None¹ | N/A | Per-FR Loop | BASELINE.md + sessions_spawn.log |
| **P6** | Gate 3 (P5) | **Gate 4** | ≥ 85 | **No FR loop** | QUALITY_REPORT.md, RELEASE_NOTES.md + sessions_spawn.log |
| **P7** | Gate 4 (P6) | None² | N/A | Per-FR Loop | RISK_REGISTER.md + sessions_spawn.log |
| **P8** | Gate 4 (P6) | None² | N/A | Per-FR Loop | CONFIG_RECORDS.md + sessions_spawn.log |

> ¹ P5: Phase Truth check only (HR-11 ≥ 90%); no separate gate evaluation.
>
> ² P7/P8: "Cleared by P6 Gate 4" — no separate gate evaluation; Phase Truth check only (HR-11: ≥ 90%).

### Critical Notes

**P5: Phase Truth Only**
P5 has NO exit gate evaluation. `_PHASE_EXIT_GATES = {3: 2, 4: 3, 6: 4}` — P5 is not in the map. Exit is governed solely by Phase Truth (HR-11 ≥ 90%).

**P6: No Per-FR Loop**
P6 does NOT have a per-FR loop. Gate 4 evaluates all 14 dimensions across the entire project at once.

**Phase Truth (HR-11/TH-15, P1–P8)**
`PhaseTruthVerifier` runs automatically after each phase exit gate. Requires ≥ 90%:
> **Sync Note**: If weights in `phase_truth_verifier.py` are modified, this table **must** be updated synchronously.

| Phase | FrameworkEnforcer | pytest pass | coverage | previous_phase_artifacts | Cross-artifact |
|---|----|----|----|----|----|
| P1–P2 | 70% | — | — | 30% | — |
| P3–P4 | 28% | 24% | 16% | 14% | 18% |
| P5–P8 | 70% | — | — | 30% | — |

**Preflight Hooks (all phases)**
`run-phase` runs before each phase work loop: FSM state check → KillSwitch status → Previous phase artifacts (ASPICE chain, P2+) → SAB check (P3+) → Traceability check (P3 info, P4+ block) → Tool registry → DriftDetector (P3+, now includes SAB drift) → GapDetector (P4+).

**SAB Architecture Baseline (P2 → P3–P8)**
The SAB (Software Architecture Baseline) is a machine-readable architecture contract generated at P2 exit from SAD.md §6. It flows through four integration lines:

| Line | Mechanism | Phase | What it does |
|------|-----------|-------|-------------|
| SAB.json generation | `scripts/generate_sab.py --project .` | P2 exit | Extracts layers, modules, dependencies, quality_targets from SAD.md §6 → `.methodology/SAB.json` |
| Manifest embedding | `harness_bridge.generate_quality_manifest()` | P2 exit | SAB fields written to `quality_manifest.json`: `nfr_dimension_mapping` (auto-derived from `nfr_traceability.type`), `nfr_traceability` (verbatim), `nfr_fr_mapping` (from SRS §2 xref), `architecture_constraints`, `high_risk_modules` |
| Gate architecture dimension | `harness_bridge.prepare_gate()` | P3–P8 | SAB data injected into `GateContext.sab_data`; appears in `evaluation_prompt()` for architecture dimension validation |
| SAB drift detection | `DriftDetector.detect_sab_drift()` | P3–P8 preflight | Compares actual import dependencies against SAB `allowed_dependencies`; flags new files not in SAB layers; flags missing SAB-registered files |
| SAB constitution check | `PhaseHooks.preflight_sab_check()` | P3–P8 preflight | Validates SAB.json existence, layer integrity, module presence; blocks if critical violations found |

**Entry Gate Checks (P2–P8)**
- P2: git log contains `phase1(human-review): Phase 1 deliverables APPROVED`
- P3: git log contains `phase2(human-review): Phase 2 deliverables APPROVED`
- P4–P5: `quality_manifest.json` exists + predecessor Gate PASS
- P6: `quality_manifest.json` exists + Gate 3 PASS
- P7–P8: `quality_manifest.json` exists + Gate 4 PASS

---

## 12. Gate Evaluation Protocol

SSI is a Claude Code skill — Claude IS the evaluation engine. Gates are evaluated inline, not via subprocess.

### Two-Phase CLI Flow

| Step | Command | What happens |
|---|---|---|
| **1. Prepare** | `run-gate --gate N --phase P [--fr-id FR-XX]` | Loads config, triggers CRG recon if available, prints evaluation prompt to stdout |
| **2. Evaluate** | *(Claude reads stdout)* | Claude evaluates each dimension → writes result JSON to `.sessi-work/gate{N}_result.json` |
| **3. Finalize** | `finalize-gate --gate N --phase P [--fr-id FR-XX]` | Reads result JSON, checks thresholds, updates manifest, commits |

```bash
python harness_cli.py run-gate     --gate 2 --phase 3 --project .
# (Claude evaluates inline)
python harness_cli.py finalize-gate --gate 2 --phase 3 --project .
```

### Result File Contract

**Location**: `$PROJECT/.sessi-work/gate{N}_result.json`

**Schema**: `harness/ssi/schemas/harness_gate_result.schema.json`

```json
{
  "overall_score": 85.0,
  "meets_target": true,
  "quality_complete": true,
  "open_critical_count": 0,
  "open_high_count": 0,
  "breakdown": {
    "dimension_name": {"score": 90.0, "threshold": 80.0, "passed": true, "issues": []}
  }
}
```

> Note: `_parse_result()` accepts both `open_critical_count` and `open_critical` (dual-fallback) — see §8.2.

### SSI Assets Location

| Asset | Path (submodule) | Purpose |
|---|---|---|
| Evaluation prompt | `harness/ssi/prompts/evaluate_dimension.md` | Per-dimension scoring instructions |
| Verification prompt | `harness/ssi/prompts/verify_round.md` | Round verification |
| Scripts | `harness/ssi/scripts/` | `score.py`, `issue_tracker.py`, etc. |
| Schema | `harness/ssi/schemas/harness_gate_result.schema.json` | Result validation |

### Gate Thresholds

| Gate | Trigger | Composite threshold | Blocking condition |
|---|---|---|---|
| Gate 1 | Per-FR completion (P3/P4/P5/P7/P8) | Each dimension ≥ individual threshold | Any dimension below threshold |
| Gate 2 | P3 phase exit | ≥ 75 AND `quality_complete=True` | Score or completeness fail |
| Gate 3 | P4/P5 phase exit | ≥ 80 AND `quality_complete=True` | Score or completeness fail |
| Gate 4 | P6 full project | ≥ 85 AND `quality_complete=True` | Score or completeness fail |

> Authoritative threshold source: `constitution/CONSTITUTION.md` §2.
