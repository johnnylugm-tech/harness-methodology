# SKILL.md — harness-methodology

> **Version**: v6.49.0
> **Framework**: harness-methodology
> **Academic Benchmark**: 91/100
> **Architecture**: 3-layer (clean / integrated / optimized)

---

## 1. Skill Overview

`harness-methodology` is a structured 8-phase software development framework
for AI-assisted projects using A/B agent collaboration.

Key capabilities:
- **8-phase execution pipeline** (P1 Requirements → P8 Configuration)
- **4-gate quality architecture** with score thresholds (75/80/85)
- **A/B agent collaboration** enforced via HybridWorkflow (HR-04)
- **Constitution-as-Code** policy enforcement
- **Automated drift detection** (M2: UQLM EnsembleScorer)
- **Kill-switch circuit breaker** (M1: CLOSED/OPEN/HALF_OPEN)
- **Gap detection** against SPEC.md + AST scan (M3)

---

## 2. Phase Routing (§4)

| Phase | Name | Entry Score | Exit Gate | Key Artifact |
|-------|------|-------------|-----------|---------------|
| P1 | Requirements Specification | — | Gate1 (75) | SRS.md |
| P2 | Architecture Design | Gate1 | Gate1 (75) | SAD.md, ADR.md |
| P3 | Implementation | Gate1 | Gate2 (75) | code + tests |
| P4 | Testing | Gate2 | Gate3 (80) | TEST_RESULTS.md |
| P5 | Verification & Delivery | Gate3 | Gate3 (80) | BASELINE.md |
| P6 | Quality Assurance | Gate3 | Gate4 (85) | QUALITY_REPORT.md |
| P7 | Risk Management | Gate4 | Gate4 (85) | RISK_REGISTER.md |
| P8 | Configuration Management | Gate4 | Gate4 (85) | CONFIG_RECORDS.md |

### Gate Definitions

| Gate | Phases | score_gate | Blocking |
|------|--------|------------|----------|
| Gate1 | P3, P5, P7, P8 entry | 75 | yes |
| Gate2 | P3 exit | 75 | yes |
| Gate3 | P4 exit | 80 | yes |
| Gate4 | P6 full | 85 | yes |

---

## 3. Modules

### M1: kill_switch
Circuit breaker protecting all phase transitions.

```python
from kill_switch import KillSwitch, CircuitState
ks = KillSwitch()
ks.record_failure("quality_gate")   # triggers OPEN after threshold
state = ks.get_state()               # CLOSED | OPEN | HALF_OPEN
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
Parses SPEC.md + scans AST to find requirement-implementation gaps.

```python
from gap_detector import GapDetector
gd = GapDetector("/path/to/project")
report = gd.run()
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
| HR-11 | Phase Truth < 70% blocks phase advance | Terminate |
| HR-12 | A/B review > 5 rounds triggers PAUSE | — |
| HR-13 | Phase execution > 3× estimate triggers PAUSE | — |
| HR-14 | Integrity < 40 triggers FREEZE | — |
| HR-15 | citations must include line numbers + artifact_verification | -15 |

---

## 5. Quality Gate Commands

```bash
# Check phase gate
python cli.py quality-gate --phase <N>

# Stage pass (end of phase)
python cli.py stage-pass --phase <N>

# Verify artifacts
python cli.py verify-artifact --phase <N>

# Check Phase Truth score
python cli.py phase-verify --phase <N>

# SAB drift detection
python cli.py trace-check --phase <N>

# Steering loop
python cli.py steering run --phase <N>

# Auto research
python cli.py auto-research --project /path --phase <N>
```

### Phase Truth Weights
```
FrameworkEnforcer BLOCK  40%
Sessions_spawn.log        20%
pytest actual pass        20%
Test coverage threshold   20%
```

---

## 6. A/B Collaboration Protocol

```
Agent A (role: DEVELOPER / architect / tester / devops / qa / risk)
  |-- implements FR-XX
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

*harness-methodology v6.49.0 — Academic Benchmark 91/100*
