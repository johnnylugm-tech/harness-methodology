# SKILL.md — harness-methodology

> **Version**: v6.50.0
> **Framework**: harness-methodology
> **Academic Benchmark**: 91/100
> **Architecture**: 3-layer (clean / integrated / optimized)

---

## 1. Skill Overview

`harness-methodology` is a structured 8-phase software development framework
for AI-assisted projects using A/B agent collaboration.

Key capabilities:
- **8-phase execution pipeline** (P1 Requirements → P8 Configuration)
- **4-gate quality architecture** with score thresholds (75/75/80/85)
- **A/B agent collaboration** enforced via HybridWorkflow (HR-04)
- **Constitution-as-Code** policy enforcement
- **Automated drift detection** (M2: UQLM EnsembleScorer)
- **Kill-switch circuit breaker** (M1: CLOSED/OPEN/HALF_OPEN)
- **Gap detection** against SPEC.md + AST scan (M3)

---

## 2. Phase Routing (§4)

| Phase | Name | Entry Score | Exit Gate | Key Artifact |
|-------|------|-------------|-----------|---------------|
| P1 | Requirements Specification | — | Human¹ | SRS.md |
| P2 | Architecture Design | Human¹ | Human¹ | SAD.md, ADR.md |
| P3 | Implementation | Human¹ | Gate2 (75) | code + tests |
| P4 | Testing | Gate2 | Gate3 (80) | TEST_RESULTS.md |
| P5 | Verification & Delivery | Gate3 | Gate3 (80) | BASELINE.md |
| P6 | Quality Assurance | Gate3 | Gate4 (85) | QUALITY_REPORT.md |
| P7 | Risk Management | Gate4 | Gate4 (85) | RISK_REGISTER.md |
| P8 | Configuration Management | Gate4 | Gate4 (85) | CONFIG_RECORDS.md |

> ¹ **Human¹** = human peer review of deliverables (reviewer reads + APPROVE/REJECT). This is NOT
> `harness run-gate --gate 1`. `run-gate --gate 1` only applies to code phases (P3, P4, P5, P7, P8)
> where linting/type_safety/test_coverage can be measured. P1/P2 produce documents, not code. P6 has
> no per-FR Gate 1 — it uses a single Gate 4 (12-dim full audit) at phase exit.

### Gate Definitions

| Gate | Phases | score_gate | Blocking |
|------|--------|------------|----------|
| Gate1 | P3, P4, P5, P7, P8 per-FR | 75 (each dim) | yes |
| Gate2 | P3 exit | 75 | yes |
| Gate3 | P4 exit | 80 | yes |
| Gate4 | P6 full | 85 | yes |

---

## 3. Modules

### M1: kill_switch
Circuit breaker protecting all phase transitions. Integrated into PhaseHooks monitoring and pipeline preflight.

```python
from kill_switch import KillSwitch, CircuitState
from kill_switch.models import MonitorConfig

ks = KillSwitch()
ks.start_monitoring("agent-a", MonitorConfig(agent_id="agent-a"))
if ks.is_agent_circuit_open("agent-a"):   # check circuit breaker state
    raise RuntimeError("Circuit OPEN")
state = ks.get_agent_state("agent-a")     # CLOSED | OPEN | HALF_OPEN
ks.stop_monitoring("agent-a")
```

### M2: detection (UQLM EnsembleScorer)
Scores agent output confidence; detects code/spec/phase drift.

```python
from detection import EnsembleScorer, DriftDetector, PatternMatcher

scorer = EnsembleScorer()
score = scorer.score(dev_result, expected_fr="FR-01")
# score.ensemble_confidence -> 0.0-1.0
# score.passed -> True if >= 0.70

detector = DriftDetector("/path/to/project")
results = detector.detect_all()      # sad + spec + phase drift

matcher = PatternMatcher()
match = matcher.match_text(code_text)
# match.passed -> False if FORBIDDEN hit or REQUIRED miss
```

### M3: gap_detector
Parses SPEC.md + scans AST to find requirement-implementation gaps. Integrated into pipeline Step 2.5 (P3+).

```python
from gap_detector.parser import SpecParser
from gap_detector.scanner import CodeScanner
from gap_detector.detector import GapDetector

spec = SpecParser("SPEC.md").parse()
code = CodeScanner("/path/to/project").scan()
detector = GapDetector(spec, code, similarity_threshold=0.6)
gaps = detector.detect()
summary = detector.get_summary()  # GapSummary: total, missing, incomplete, orphaned, critical, major, minor
```

### core/
Execution orchestration.

| Module | Purpose |
|--------|---------|
| `phase_hooks.py` | Pre/monitoring/post-flight hooks |
| `hybrid_workflow.py` | A/B agent mode=ON enforcement |
| `verification_gate.py` | Gate scoring (75/80/85) |
| `task_splitter.py` | FR-level task decomposition |
| `agent_spawner.py` | Sub-agent dispatch |
| `subagent_isolator.py` | fresh_messages isolation |
| `requirement_traceability.py` | FR citation verification |
| `sessions_spawn_logger.py` | sessions_spawn.log writer |

### enforcement/
Policy and constitution enforcement.

| Module | Purpose |
|--------|---------|
| `policy_engine.py` | 7 default policies (BLOCK/WARN/LOG) |
| `execution_registry.py` | SQLite audit trail + SHA-256 chain |
| `constitution_as_code.py` | Constitution rule parser |
| `constitution_policy_sync.py` | Constitution → PolicyEngine sync |
| `framework_enforcer.py` | Multi-check enforcer (run level) |
| `server_enforcer.py` | Git hook + server-side enforcement |
| `agent_proof_hook.py` | pre-commit hook installer |

### agent_personas/
Six standard personas: ARCHITECT, DEVELOPER, REVIEWER, QA_ENGINEER, DEVOPS, PRODUCT_MANAGER.

```python
from agent_personas import get_persona
prompt = get_persona("DEVELOPER").to_prompt(task="FR-01 implementation")
```

### constitution/
HR compliance checkers (Hard Rules enforcement). Integrates with `steering/` for A/B iteration safeguards.

| Module | Purpose |
|--------|---------|
| `bvs_runner.py` | HR-03 phase-order checker: reads `.methodology/state.json`, validates phase prerequisites and FSM state |
| `citation_parser.py` | HR-07/09: extracts citation markers (`[FR-01]`, `[§3.2]`) and verifies traceability keywords |
| `verification_constitution_checker.py` | Bridges `steering/integrations.py` to `enforcement.constitution_as_code` (R001-R007) |

---

## 4. Hard Rules (HR)

| ID | Rule | Score Impact |
|----|------|--------------|
| HR-01 | A/B must be different Agents; self-review forbidden | -25 / Terminate |
| HR-02 | Quality Gate requires actual stdout output | -20 / Terminate |
| HR-03 | Phase order must be sequential; no skipping | -30 / Terminate |
| HR-04 | HybridWorkflow mode=ON mandatory | Terminate |
| HR-05 | harness-methodology wins all conflicts | Log |
| HR-06 | External frameworks outside spec forbidden | -20 / Terminate |
| HR-07 | DEVELOPMENT_LOG must record session_id | -15 |
| HR-08 | Phase end requires Quality Gate pass | -10 / Terminate |
| HR-09 | Claims Verifier citations must pass | -20 / Terminate |
| HR-10 | sessions_spawn.log must have A/B entries | -15 / Terminate |
| HR-11 | Phase Truth < 70% blocks phase advance (P3–P8) | Terminate |
| HR-12 | A/B review > 5 rounds triggers PAUSE | — |
| HR-13 | Phase execution > 3× estimate triggers PAUSE | — |
| HR-14 | Integrity < 40 triggers FREEZE | — |
| HR-15 | citations must include line numbers + artifact_verification | -15 |

---

## 5. Quality Gate Commands

> **Note**: `cli.py` below is the full parent-system CLI (requires 30+ external modules).
> For standalone harness operations, use `harness_cli.py` — see §2.3 in SAD.md.

```bash
# Harness-standalone equivalents (harness_cli.py) — two-phase gate evaluation
# Phase 1: prepare + print evaluation instructions
python harness_cli.py run-gate --gate 1 --phase 3 --fr-id FR-01
# (Claude evaluates and writes .sessi-work/gate1_result.json)
# Phase 2: check thresholds and commit
python harness_cli.py finalize-gate --gate 1 --phase 3 --fr-id FR-01
python harness_cli.py status

# Additional harness_cli.py audit & analysis commands
python harness_cli.py run-gap-analysis  # M3: SPEC.md ↔ codebase gap detection
python harness_cli.py audit-phase       # 8-dimension phase audit (PhaseAuditor)
python harness_cli.py verify-spec       # 6-dimension spec compliance check
python harness_cli.py check-logic       # Logic correctness (output/branch/lazy-init/semantic)

# Full parent-system CLI (requires 30+ external modules, not standalone)
python cli.py quality-gate --phase <N>
python cli.py stage-pass --phase <N>
python cli.py verify-artifact --phase <N>
python cli.py phase-verify --phase <N>
python cli.py trace-check --phase <N>
python cli.py steering run --phase <N>
python cli.py auto-research --project /path --phase <N>
```

### Phase Truth Weights (HR-11)

Weights vary by phase — must match `PhaseTruthVerifier.verify()`:

| Phase Range | FrameworkEnforcer BLOCK | Sessions_spawn.log | pytest pass | coverage |
|-------------|------------------------|-------------------|-------------|----------|
| P1–P2 | 60% | 40% | — | — |
| P3–P4 | 35% | 25% | 25% | 15% |
| P5–P8 | 60% | 40% | — | — |

> Phase Truth score ≥ 70% required to advance. See `core/quality_gate/phase_truth_verifier.py`.

---

## 6. A/B Collaboration Protocol

```
Agent A (role: DEVELOPER / architect / tester / devops / qa / risk)
  |-- [TDD-1] Write failing test for FR requirement (RED — confirm test fails first)
  |-- [TDD-2] Implement FR until test passes (GREEN)
  |-- [TDD-3] Refactor without breaking tests (IMPROVE)
  |-- returns JSON: {status, files, confidence, citations, summary}
  |
Agent B (role: REVIEWER / architect)
  |-- reviews Agent A output against SRS + SAD
  |-- returns JSON: {status, review_status, reason, confidence, citations, summary}
  |
[Constitution Check]  -- BVS + HR-09 validation
[CQG]                 -- Linter + Complexity + Coverage
[HR-12]               -- iteration guard (max 5 rounds)
```

### FORBIDDEN in any agent output
- `app/infrastructure/` imports (deprecated)
- `@covers: L1 Error` annotations
- `@type: edge` test type
- Docstrings without `[FR-XX]` reference
- Docstrings without `Citations:` section with line numbers

---

## 7. sessions_spawn.log Format (HR-10)

Two entries per FR (developer + reviewer):

```json
{"timestamp": "2026-04-26T10:00:00", "fr_id": "FR-01", "role": "developer",
 "session_id": "dev-abc123", "status": "success", "confidence": 8}
{"timestamp": "2026-04-26T10:05:00", "fr_id": "FR-01", "role": "reviewer",
 "session_id": "rev-def456", "status": "success", "review_status": "APPROVE"}
```

---

## 8. State Machine (FSM)

```
INIT -> RUNNING -> PAUSED -> RUNNING
                -> FREEZE  (Integrity < 40)
                -> DONE    (all phases complete)
RUNNING -> OPEN   (KillSwitch triggered)
OPEN    -> HALF_OPEN -> CLOSED  (recovery)
```

State stored in `.methodology/state.json`:
```json
{
  "current_phase": 3,
  "state": "RUNNING",
  "last_update": "2026-04-26T10:00:00"
}
```

---

## 9. CLI Entry Points

```bash
# Start phase
python3 cli.py run-phase --phase <N> --goal "<goal>"

# Resume
python3 cli.py run-phase --resume

# Update step
python3 cli.py update-step --step <N>

# End phase (triggers Quality Gate)
python3 cli.py end-phase --phase <N>

# Plan phase
python3 cli.py plan-phase --phase <N>

# Generate full FR plan
python3 scripts/generate_full_plan.py --phase <N> --repo /path/to/project

# Setup git hooks
bash scripts/setup-git-hooks.sh
```

---

## 10. Adapter Interfaces

```python
# PhaseHooks via adapter (external/CLI context)
from core.adapters.phase_hooks_adapter import PhaseHooksAdapter

adapter = PhaseHooksAdapter("/path/to/project", phase=3)
adapter.preflight()                           # FSM + Constitution + ToolRegistry
adapter.before_dev("FR-01")
adapter.after_dev("FR-01", dev_result_dict)
adapter.before_rev("FR-01")
adapter.after_rev("FR-01", rev_result_dict)
passed = adapter.hr12_check("FR-01", iteration=3)
adapter.postflight()                          # Constitution + state update + summary

# Or full lifecycle in one call
result = adapter.run_phase_lifecycle(fr_results_list)
```

---

## 11. Agent Execution Loop

The main agent has **exactly one source of truth at any moment**:

| Moment | Source of truth | What the agent does |
|--------|----------------|---------------------|
| Session start / phase entry | **SKILL.md** | Read framework rules, phase routing, gate protocol |
| Inside a phase | **phase plan file** | Follow the plan step-by-step (do NOT re-read SKILL.md for task details) |
| After a crash / context reset | **`generate-next-plan`** | Get position report, then resume plan |

### Two Execution Modes — Pick One Per Phase, Never Mix

| Mode | Command | When to use |
|------|---------|-------------|
| **Manual (default)** | Follow `phaseN_plan.md` checklist top-to-bottom | Normal autonomous execution |
| **Automated** | `harness_cli.py run-pipeline` | Pipeline automation; pauses (exit 10) when gate result missing |

> **Rule**: choose one mode per phase. Running `run-pipeline` while also manually executing a phase plan checklist creates double-execution and duplicate gate evaluations.

### Execution Loop (per phase) — Manual Mode

```
1. ENTER PHASE
   python harness_cli.py plan-phase --phase N --repo $REPO \
       --output $REPO/.methodology/phaseN_plan.md
   → ONE command. `plan-phase` calls generate_full_plan.py internally.
   → Plan is THE complete authority for phase N (preflight + A/B dev + gates + advance)

2. FOLLOW PLAN
   Execute checklist items top-to-bottom. Key block types:
     [PREFLIGHT]    run-phase --phase N   (FSM + Constitution check)
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
> `generate-next-plan` reads HANDOVER.md + state.json to produce the recovery position report.

### Recovery (after crash or context reset)

```bash
# Where am I?
python harness_cli.py generate-next-plan --project $REPO

# Output example:
#   Phase      : 3 (Implementation)
#   Plan file  : .methodology/phase3_plan.md  ← open this file
#   Last ckpt  : CHECKPOINT-2 (Gate 1 / FR-02) ✓ PASS
#   Next ckpt  : CHECKPOINT-3 (Gate 1 / FR-03)
#   [ACTION]     search plan for "CHECKPOINT-3", resume from there

# Then: open plan file, search "### 🔒 CHECKPOINT-3", follow from there.
```

### Decision Rules

- **SKILL.md governs**: phase order, gate thresholds, hard rules (HR-01–HR-15), A/B protocol.
- **Plan governs**: task sequence within a phase; specific file paths; CLI commands.
- **Conflict**: SKILL.md wins on rules; plan wins on task order / phase-specific steps.
- **Never skip checkpoints**: If a gate fails, fix and re-run — never advance without PASS.
- **A/B is mandatory**: HR-01 (A≠B), HR-04 (HybridWorkflow ON), HR-10 (sessions_spawn.log) apply to every FR in every phase.

---

## 12. Autonomous Execution Protocol (Claude Code)

Claude Code can run the **full P1→P8 pipeline autonomously** using the Bash tool.
Humans are required at only **3 checkpoints**.

### One-Prompt Launch

> **Prerequisite**: SRS.md must exist with `### FR-XX:` sections defining each functional requirement.
> SAD.md must document architecture decisions. Both are **human-provided preconditions** —
> the pipeline pauses at P1/P2 exit (code 10) until these files are present.
> P1/P2 are NOT auto-generated by the agent; the human creates or provides them,
> then re-runs `run-pipeline --phase-from 1` to proceed.

```
"Build [description]. Repo: [path]. Tech: [stack].
Run harness-methodology P1→P8 autonomously.
Gate 4 needs my Telegram APPROVE — handle everything else."
```

### Full Pipeline Command

```bash
# P3+ plan is generated dynamically after SAD.md exists (P2 output)
# Pipeline pauses (exit 10) when a gate result is missing — evaluate then resume
python harness_cli.py run-pipeline \
  --phase-from 1 --phase-to 8 \
  --project /path/to/project

# Resume after human provides SRS.md (P1) or SAD.md (P2)
python harness_cli.py run-pipeline --phase-from 3 --project /path/to/project
```

### Step-by-Step — Automated Mode (run-pipeline)

> **Note**: prefer the Manual Mode (§11 plan checklist). Use run-pipeline only for pipeline
> automation. The phase plan is self-contained — it includes preflight, A/B steps, and all gates.

```bash
# 1. Generate plan (plan-phase calls generate_full_plan.py internally — ONE command only)
python harness_cli.py plan-phase --phase $N --repo $PROJECT \
  --output $PROJECT/.methodology/phase${N}_plan.md

# 2. Preflight (FSM + Constitution + M1 kill-switch + M2 drift detection)
python harness_cli.py run-phase --phase $N --project $PROJECT

# 2.5. M3 Gap Analysis (P3+ — SPEC.md ↔ codebase, writes .methodology/gap_report.json)
python harness_cli.py run-gap-analysis --project $PROJECT

# 3. Per-FR Gate 1 (P3/P4/P5/P7/P8) — FR IDs from quality_manifest.json
python harness_cli.py run-gate --gate 1 --phase $N --project $PROJECT --fr-id FR-XX
# (Claude evaluates inline — writes gate1_result.json, then:)
python harness_cli.py finalize-gate --gate 1 --phase $N --project $PROJECT --fr-id FR-XX

# 4. Phase exit gate (P3→Gate2, P4→Gate3, P6→Gate4)
python harness_cli.py run-gate --gate $G --phase $N --project $PROJECT
# (Claude evaluates inline, then:)
python harness_cli.py finalize-gate --gate $G --phase $N --project $PROJECT

# 5. Phase Truth (P3–P8 — HR-11 ≥ 70%) + Recovery position report
python harness_cli.py generate-next-plan --project $PROJECT --phase $N

# 6. Confirm
python harness_cli.py status --project $PROJECT
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

> All phases: Agent A ≠ Agent B (HR-01). Both write to `sessions_spawn.log` (HR-10).
> P3/P4/P5/P7/P8: 2 entries per FR. P1/P2/P6: 2 entries per phase.

### Step-by-Step: Two-Phase Gate Evaluation

SSI is a Claude Code skill — Claude IS the evaluation engine. Each gate uses a two-step CLI flow:

```bash
# Step 1: Prepare (loads config, triggers CRG recon, prints evaluation instructions)
python harness_cli.py run-gate --gate $G --phase $N --project $PROJECT [--fr-id FR-XX]

# Step 2: Evaluate (Claude reads the prompt, evaluates dimensions, writes result JSON)
#   → Claude writes: $PROJECT/.sessi-work/gate${G}_result.json
#   → Schema: harness/ssi/schemas/harness_gate_result.schema.json

# Step 3: Finalize (reads result, checks thresholds, updates manifest, commits)
python harness_cli.py finalize-gate --gate $G --phase $N --project $PROJECT [--fr-id FR-XX]
```

### Recovery Position Report (crash / context reset only)

After a crash or context reset, use this to find your position:

```bash
python harness_cli.py generate-next-plan --project $PROJECT --phase $N
```

Output: current phase, plan file path, last completed checkpoint, next checkpoint to resume from.
**Do NOT call this during normal execution** — the phase plan's advance section already tells you what to do next.

### Mandatory Human Checkpoints

**Manual mode** (§11 plan checklist — 3 human checkpoints):

| # | Phase | When | Required Action |
|---|---|---|---|
| 1 | P1 exit | SRS.md ready | Human reads SRS.md → APPROVE / REJECT |
| 2 | P2 exit | SAD.md + ADR.md ready | Human reads deliverables → APPROVE / REJECT |
| 3 | P6 exit | Gate 4 evaluation done | Click APPROVE on Telegram (Hermes MCP) |

**Automated pipeline mode** (`run-pipeline` — the pipeline treats P1+P2 outputs as preconditions):

| Checkpoint | When | Required Action |
|---|---|---|
| P1+P2 outputs | Before pipeline can plan P3+ | Provide `SRS.md` with `### FR-XX:` sections and `SAD.md` |
| Gate 4 — Final APPROVE | P6 exit | Click APPROVE on Telegram (Hermes MCP) |
| PAUSE (exit code 10) | Result file missing | Run `run-gate`, evaluate, then `finalize-gate` |

### Pipeline Exit Codes

| Code | Meaning | Action |
|---|---|---|
| 0 | All phases complete | Done |
| 1 | Hard error (manifest missing) | Diagnose |
| 10 | PAUSE — evaluation needed | Run-gate → evaluate → finalize-gate → re-run pipeline |

---

## §13. Gate Evaluation Protocol

SSI is a Claude Code skill. Gates are evaluated inline by Claude — not via subprocess.

### Two-Phase CLI Flow

| Phase | Command | What happens |
|---|---|---|
| **1. Prepare** | `run-gate --gate N --phase P` | Loads config, CRG recon (if needed), prints evaluation prompt to stdout |
| **2. Evaluate** | *(Claude reads stdout)* | Claude reads the printed prompt → evaluates each dimension using `harness/ssi/prompts/evaluate_dimension.md` → writes result JSON to `.sessi-work/gate{N}_result.json` |
| **3. Finalize** | `finalize-gate --gate N --phase P` | Reads result JSON, checks thresholds, updates manifest, commits |

> **Handoff**: `run-gate` output is the SSI trigger. The agent must read the printed evaluation
> instructions, execute the evaluation (scoring each dimension per the SSI prompt), and write
> the result JSON. Then `finalize-gate` picks up the JSON and enforces thresholds.

### Evaluation Loop (per dimension)

| Round | Action |
|---|---|
| 1 | Read `harness/ssi/prompts/evaluate_dimension.md` |
| 2 | Use `harness/ssi/scripts/` tools (score.py, issue_tracker.py, etc.) for static analysis |
| 3 | Score each dimension (0–100) against its threshold |
| 4 | Write `.sessi-work/gate{N}_result.json` using schema |

### Result File Contract

**Location**: `$PROJECT/.sessi-work/gate{N}_result.json`

**Schema**: `harness/ssi/schemas/harness_gate_result.schema.json`

Required fields:
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

### SSI Assets Location

| Asset type | Path |
|---|---|
| Evaluation prompts | `harness/ssi/prompts/evaluate_dimension.md` |
| Verification prompts | `harness/ssi/prompts/verify_round.md` |
| Scripts | `harness/ssi/scripts/` (score.py, issue_tracker.py, etc.) |
| Schema | `harness/ssi/schemas/harness_gate_result.schema.json` |

### Gate Thresholds

| Gate | Trigger | Threshold logic |
|---|---|---|
| Gate 1 | Per-FR completion | Each dimension ≥ its individual threshold |
| Gate 2 | P3 phase exit | composite ≥ 75 AND quality_complete=True |
| Gate 3 | P4 phase exit | composite ≥ 80 AND quality_complete=True |
| Gate 4 | P6 full project | composite ≥ 85 AND quality_complete=True AND Hermes APPROVE |

---

*harness-methodology v6.50.0 — Academic Benchmark 91/100*
