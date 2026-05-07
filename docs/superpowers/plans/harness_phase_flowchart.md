# Harness-Methodology: Phase 1–8 E2E Flow (Mermaid)

```mermaid
flowchart TD
    START([Session Start]) --> P1[<b>Phase 1: Requirements</b><br/>Artifact: SRS.md]
    
    P1 --> P1_ENTRY["Entry: None<br/>(no predecessor)"]
    P1_ENTRY --> P1_WORK["💼 A/B Work:<br/>REQUIREMENTS_ENGINEER<br/>BUSINESS_ANALYST<br/><br/>📝 Output: SRS.md"]
    P1_WORK --> P1_GATE["🔒 Exit: Human¹<br/>Review SRS.md<br/>APPROVE / REJECT"]
    
    P1_GATE -->|APPROVE| P1_PUSH["✅ git push<br/>phase1 COMPLETE"]
    P1_GATE -->|REJECT| P1_WORK
    P1_PUSH --> P2_START["📋 Next: Phase 2<br/>plan-phase --phase 2"]
    
    P2_START --> P2[<b>Phase 2: Architecture</b><br/>Artifact: SAD.md + ADR.md]
    
    P2 --> P2_ENTRY["Entry: Human¹ APPROVE<br/>(from P1)"]
    P2_ENTRY --> P2_WORK["💼 A/B Work:<br/>ARCHITECT<br/>TECH_LEAD<br/><br/>📝 Output: SAD.md<br/>ADR.md"]
    P2_WORK --> P2_GATE["🔒 Exit: Human¹<br/>Review SAD.md<br/>APPROVE / REJECT"]
    
    P2_GATE -->|APPROVE| P2_MANIFEST["📊 Generate<br/>quality_manifest.json"]
    P2_GATE -->|REJECT| P2_WORK
    P2_MANIFEST --> P2_PUSH["✅ git push<br/>phase2 COMPLETE<br/>sad_path now exists"]
    P2_PUSH --> P3_START["📋 Next: Phase 3<br/>plan-phase --phase 3"]
    
    P3_START --> P3[<b>Phase 3: Implementation</b><br/>Per-FR TDD loop]
    
    P3 --> P3_ENTRY["Entry: Automated preflight<br/>(git log human APPROVE from P2)"]
    P3_ENTRY --> P3_CHECK["🔍 Entry gate check<br/>Confirm P2 human approval<br/>via git log"]
    P3_CHECK --> P3_PRE["🔧 Preflight<br/>FSM→RUNNING, KillSwitch<br/>Constitution, DriftDetector init"]
    P3_PRE --> P3_WORK["💼 A/B Loop (per FR):<br/>DEVELOPER<br/>REVIEWER<br/><br/>📝 TDD: RED → GREEN<br/>📝 sessions_spawn.log (2 entries)"]
    P3_WORK --> P3_G1["🔒 Gate 1 (per FR):<br/>linting(90)<br/>type_safety(85)<br/>coverage(80)<br/>score ≥ 75"]
    P3_G1 -->|PASS| P3_PUSH1["✅ git push<br/>CHECKPOINT-K"]
    P3_G1 -->|FAIL| P3_FIX["🔧 Fix dimension<br/>[max 3 rounds]"]
    P3_FIX --> P3_G1
    
    P3_PUSH1 --> P3_NEXT_FR{All FRs<br/>complete?}
    P3_NEXT_FR -->|No| P3_WORK
    P3_NEXT_FR -->|Yes| P3_G2["🔒 Gate 2: Phase Exit<br/>7 dims<br/>score_gate ≥ 75"]
    
    P3_G2 -->|PASS| P3_TRUTH["⚠️ Phase Truth<br/>HR-11 ≥90%"]
    P3_G2 -->|CONTINUE| P3_FIX2["🔧 Fix"]
    P3_G2 -->|PLATEAU| P3_DEFER["📌 deferred_fixes.md"]
    P3_G2 -->|BLOCKED| P3_ESCALATE["⚠️ GateBlockedError<br/>escalate to human"]
    
    P3_TRUTH -->|PASS| P3_G2D["✅ git push<br/>phase3 COMPLETE<br/>[FSM] → DONE"]
    P3_TRUTH -->|FAIL| P3_ESCALATE
    
    P3_FIX2 --> P3_G2
    P3_DEFER --> P3_G2D
    P3_ESCALATE --> P3_G2D
    
    P3_G2D --> P4_START["📋 Next: Phase 4<br/>plan-phase --phase 4"]
    
    P4_START --> P4[<b>Phase 4: Testing</b><br/>Per-FR test execution]
    
    P4 --> P4_ENTRY["Entry: Automated preflight<br/>(Gate 2 PASS from P3)"]
    P4_ENTRY --> P4_CHECK["🔍 Entry gate check<br/>Verify P3 Gate 2 ≥75"]
    P4_CHECK --> P4_PRE["🔧 Preflight<br/>FSM, KillSwitch, Constitution<br/>DriftDetector, GapDetector init"]
    P4_PRE --> P4_WORK["💼 A/B Loop (per FR):<br/>QA_ENGINEER<br/>ARCHITECT<br/><br/>📝 Execute TEST_PLAN.md<br/>📝 Branch coverage ≥80%<br/>📝 sessions_spawn.log (2 entries)"]
    P4_WORK --> P4_G1["🔒 Gate 1 (per FR):<br/>linting/type_safety<br/>coverage ≥ 80%<br/>score ≥ 75"]
    P4_G1 -->|PASS| P4_PUSH1["✅ git push<br/>CHECKPOINT-K"]
    P4_G1 -->|FAIL| P4_FIX["🔧 Fix"]
    P4_FIX --> P4_G1
    
    P4_PUSH1 --> P4_NEXT_FR{All FRs<br/>complete?}
    P4_NEXT_FR -->|No| P4_WORK
    P4_NEXT_FR -->|Yes| P4_G3["🔒 Gate 3: Phase Exit<br/>12 dims<br/>score_gate ≥ 80<br/>[CRG recon]"]
    
    P4_G3 -->|PASS| P4_TRUTH["⚠️ Phase Truth<br/>HR-11 ≥90%"]
    P4_G3 -->|CONTINUE| P4_FIX2["🔧 Fix"]
    P4_G3 -->|PLATEAU| P4_DEFER["📌 deferred_fixes.md"]
    P4_G3 -->|BLOCKED| P4_ESCALATE["⚠️ escalate"]
    
    P4_TRUTH -->|PASS| P4_G3D["✅ git push<br/>TEST_RESULTS.md<br/>phase4 COMPLETE"]
    P4_TRUTH -->|FAIL| P4_ESCALATE
    
    P4_FIX2 --> P4_G3
    P4_DEFER --> P4_G3D
    P4_ESCALATE --> P4_G3D
    
    P4_G3D --> P5_START["📋 Next: Phase 5<br/>plan-phase --phase 5"]
    
    P5_START --> P5[<b>Phase 5: Verification</b><br/>Per-FR acceptance check]
    
    P5 --> P5_ENTRY["Entry: Gate 3 PASS<br/>(from P4)"]
    P5_ENTRY --> P5_CHECK["🔍 Entry gate check<br/>Verify P4 Gate 3 ≥80"]
    P5_CHECK --> P5_PRE["🔧 Preflight<br/>FSM, KillSwitch, Constitution<br/>Full DriftDetector scan"]
    P5_PRE --> P5_WORK["💼 A/B Loop (per FR):<br/>DEVELOPER<br/>REVIEWER<br/><br/>📝 Verify acceptance criteria<br/>📝 Confirm SRS compliance<br/>📝 sessions_spawn.log (2 entries)"]
    P5_WORK --> P5_G1["🔒 Gate 1 (per FR)<br/>score ≥ 75"]
    P5_G1 -->|PASS| P5_PUSH1["✅ git push"]
    P5_G1 -->|FAIL| P5_FIX["🔧 Fix"]
    P5_FIX --> P5_G1
    
    P5_PUSH1 --> P5_NEXT_FR{All FRs<br/>complete?}
    P5_NEXT_FR -->|No| P5_WORK
    P5_NEXT_FR -->|Yes| P5_NOTE["⚠️ Phase Truth Check<br/>(HR-11: ≥90% required)"]
    
    P5_NOTE --> P5_EXIT["🔒 Phase Exit<br/>Phase Truth only<br/>(no separate Gate evaluation)"]
    
    P5_EXIT -->|PASS| P5_G3D["✅ git push<br/>BASELINE.md<br/>phase5 COMPLETE"]
    P5_EXIT -->|FAIL| P5_ESCALATE["⚠️ escalate<br/>Phase Truth < 90%"]
    
    P5_ESCALATE --> P5_G3D
    
    P5_G3D --> P6_START["📋 Next: Phase 6<br/>plan-phase --phase 6"]
    
    P6_START --> P6[<b>Phase 6: QA</b><br/>NO per-FR loop]
    
    P6 --> P6_ENTRY["Entry: Gate 3 PASS<br/>(from P5)"]
    P6_ENTRY --> P6_PRE["🔧 Preflight<br/>FSM state, Constitution<br/>Drift detection init"]
    P6_PRE --> P6_WORK["💼 A/B Work:<br/>QA_ENGINEER<br/>ARCHITECT<br/><br/>📝 Prepare quality report<br/>📝 sessions_spawn.log (2 entries)"]
    P6_WORK --> P6_G4["🔒 Gate 4 ONLY<br/>Full project (12 dims)<br/>score_gate ≥ 85<br/>[CRG recon]<br/>[Hermes APPROVE ⏱120s]"]
    
    P6_G4 -->|PASS| P6_TRUTH["⚠️ Phase Truth<br/>HR-11 ≥90%"]
    P6_G4 -->|CONTINUE| P6_FIX["🔧 Fix dimension<br/>re-run G4a"]
    P6_G4 -->|PLATEAU| P6_DEFER["📌 deferred_fixes.md<br/>escalate"]
    P6_G4 -->|BLOCKED| P6_ESCALATE["⚠️ GateBlockedError<br/>manual review"]
    
    P6_TRUTH -->|PASS| P6_G4D["✅ git push<br/>QUALITY_REPORT.md<br/>RELEASE_NOTES.md<br/>phase6 COMPLETE"]
    P6_TRUTH -->|FAIL| P6_ESCALATE
    
    P6_FIX --> P6_G4
    P6_DEFER --> P6_G4D
    P6_ESCALATE --> P6_G4D
    
    P6_G4D --> P7_START["📋 Next: Phase 7<br/>plan-phase --phase 7"]
    
    P7_START --> P7[<b>Phase 7: Risk Mgmt</b><br/>Per-FR Gate 1]
    
    P7 --> P7_ENTRY["Entry: Gate 4 PASS<br/>(from P6)"]
    P7_ENTRY --> P7_CHECK["🔍 Entry gate check<br/>Confirm P6 Gate 4 PASSED"]
    P7_CHECK --> P7_PRE["🔧 Preflight<br/>FSM state, Constitution<br/>Drift detection"]
    P7_PRE --> P7_WORK["💼 A/B Loop (per FR):<br/>DEVOPS<br/>ARCHITECT<br/><br/>📝 Risk assessment<br/>📝 Mitigation plans<br/>📝 sessions_spawn.log (2 entries)"]
    P7_WORK --> P7_G1["🔒 Gate 1 (per FR)<br/>score ≥ 75"]
    P7_G1 -->|PASS| P7_PUSH1["✅ git push<br/>CHECKPOINT-K"]
    P7_G1 -->|FAIL| P7_FIX["🔧 Fix"]
    P7_FIX --> P7_G1
    
    P7_PUSH1 --> P7_NEXT_FR{All FRs<br/>complete?}
    P7_NEXT_FR -->|No| P7_WORK
    P7_NEXT_FR -->|Yes| P7_NOTE["⚠️ Phase Truth Check<br/>(HR-11: ≥90% required)"]
    
    P7_NOTE --> P7_G4["🔒 Phase Exit<br/>Cleared by P6 Gate 4<br/>No re-evaluation"]
    
    P7_G4 --> P7_G4D["✅ git push<br/>RISK_REGISTER.md<br/>RISK_STATUS_REPORT.md<br/>phase7 COMPLETE"]
    
    P7_G4D --> P8_START["📋 Next: Phase 8<br/>plan-phase --phase 8"]
    
    P8_START --> P8[<b>Phase 8: Config Mgmt</b><br/>Per-FR Gate 1]
    
    P8 --> P8_ENTRY["Entry: Gate 4 PASS<br/>(from P7)"]
    P8_ENTRY --> P8_CHECK["🔍 Entry gate check<br/>Confirm P6 Gate 4 PASSED"]
    P8_CHECK --> P8_PRE["🔧 Preflight<br/>FSM state, Constitution<br/>Drift detection"]
    P8_PRE --> P8_WORK["💼 A/B Loop (per FR):<br/>DEVOPS<br/>ARCHITECT<br/><br/>📝 Config management<br/>📝 Environment setup<br/>📝 sessions_spawn.log (2 entries)"]
    P8_WORK --> P8_G1["🔒 Gate 1 (per FR)<br/>score ≥ 75"]
    P8_G1 -->|PASS| P8_PUSH1["✅ git push<br/>CHECKPOINT-K"]
    P8_G1 -->|FAIL| P8_FIX["🔧 Fix"]
    P8_FIX --> P8_G1
    
    P8_PUSH1 --> P8_NEXT_FR{All FRs<br/>complete?}
    P8_NEXT_FR -->|No| P8_WORK
    P8_NEXT_FR -->|Yes| P8_NOTE["⚠️ Phase Truth Check<br/>(HR-11: ≥90% required)"]
    
    P8_NOTE --> P8_G4["🔒 Phase Exit<br/>Cleared by P6 Gate 4<br/>No re-evaluation"]
    
    P8_G4 --> P8_G4D["✅ git push<br/>CONFIG_RECORDS.md<br/>phase8 COMPLETE"]
    
    P8_G4D --> P8_END["🎉 Pipeline Complete<br/>Archive .methodology/"]
    
    P8_END --> END([End])
    
    style P1 fill:#e1f5ff
    style P2 fill:#f3e5f5
    style P3 fill:#fff3e0
    style P4 fill:#fce4ec
    style P5 fill:#f1f8e9
    style P6 fill:#ede7f6
    style P7 fill:#e0f2f1
    style P8 fill:#fbe9e7
    
    style P1_GATE fill:#ffccbc
    style P2_GATE fill:#ffccbc
    style P3_G1 fill:#ffb74d
    style P3_G2 fill:#ff8a65
    style P4_G1 fill:#ffb74d
    style P4_G3 fill:#ff5722
    style P5_G1 fill:#ffb74d
    style P6_G1 fill:#ffb74d
    style P6_G4 fill:#c62828
    style P7_G1 fill:#ffb74d
    style P8_G1 fill:#ffb74d
```

---

## Legend

| Symbol | Meaning |
|--------|---------|
| `💼 A/B Work` | Agent A + Agent B collaboration |
| `🔒 Gate` | Automated quality gate evaluation |
| `🔧 Fix` | Auto-fix loop (max 3 rounds) |
| `✅` | Checkpoint push / approval |
| `⚠️` | Escalation to human |
| `📝` | Output artifact |
| `📊` | Generated manifest |
| `📋` | Next phase command |
| `🎉` | Pipeline complete |

---

## Phase Entry/Exit Matrix

| Phase | Entry Check | Exit Gate | Exit Score | Structure | Artifacts |
|-------|---|---|---|---|---|
| **P1** | None | Human¹ | N/A | Static | SRS.md + sessions_spawn.log |
| **P2** | Human¹ (P1) | Human¹ | N/A | Static | SAD.md, ADR.md, quality_manifest.json + sessions_spawn.log |
| **P3** | Human¹ (P2)* | Gate 2 | ≥ 75 | Per-FR Loop | Code + sessions_spawn.log |
| **P4** | Gate 2 (P3)* | Gate 3 | ≥ 80 | Per-FR Loop | TEST_RESULTS.md + sessions_spawn.log |
| **P5** | Gate 3 (P4)* | None¹ | N/A | Per-FR Loop | BASELINE.md + sessions_spawn.log |
| **P6** | Gate 3 (P5)* | **Gate 4** | ≥ 85 | **NO FR Loop** | QUALITY_REPORT.md, RELEASE_NOTES.md + sessions_spawn.log |
| **P7** | **Gate 4 (P6)*** | None² | N/A | Per-FR Loop | RISK_REGISTER.md + sessions_spawn.log |
| **P8** | **Gate 4 (P6)*** | None² | N/A | Per-FR Loop | CONFIG_RECORDS.md + sessions_spawn.log |

> \* Entry checks via git log verification (`_entry_gate_check`)
> 
> \*\* P7/P8: Entry verified to be Gate 4 from P6 (not P7 itself)
>
> ¹ P5: Phase Truth check only (HR-11 ≥90%); no separate exit gate evaluation
>
> ² P7/P8: Cleared by P6 Gate 4; Phase Truth check only (HR-11 ≥90%); no separate exit gate evaluation

---

## Critical Notes for Agent Execution

### Gate 1: Per-Dimension Thresholds
- **IMPORTANT**: Gate 1 uses per-dimension thresholds as primary gate criteria (linting≥90, type_safety≥85, coverage≥80).
- The composite "score ≥ 75" shown in flowcharts is an informational summary — the YAML config (`gate1_per_fr.yaml`) has no `score_gate` field.
- A dimension below its individual threshold fails Gate 1 regardless of the composite score.

### P6: No Per-FR Loop
- **IMPORTANT**: P6 does NOT have a per-FR loop. It is a single Gate 4 evaluation of the entire project.
- Gate 4 evaluates all 12 dimensions across all FRs at once (not per-FR).
- `sessions_spawn.log` required (2 entries: QA_ENGINEER + ARCHITECT per phase).

### Hermes APPROVE (P6 Gate 4)
- **Trigger**: `messages_send` to HERMES_REVIEWER_TARGET env var (e.g., `telegram:user_id`)
- **Timeout**: 120 seconds (`GATE4_HERMES_TIMEOUT_MS=120000`; ReviewerRouter.HERMES_TIMEOUT_MS defaults to 90s for general Hermes ops)
- **Approval**: Reviewer sends "APPROVE" reply → Gate 4 proceeds
- **Timeout Fallback**: If no reply in 120s, code does cold-read (`messages_read`) and checks for latest message
- **Failure**: If Hermes unavailable or reviewer rejects, escalate to human

### Phase Truth Check (P3–P8)

After each phase exit gate (Gate 2/3/4), `PhaseTruthVerifier` runs automatically (HR-11 ≥90% required).
Weights vary by phase:

- **P1**: `FrameworkEnforcer(60%) + Sessions_spawn(40%)`
- **P2**: `FrameworkEnforcer(50%) + Sessions_spawn(35%) + PreviousPhaseArtifacts(15%)`
- **P3–P4**: `FrameworkEnforcer(30%) + Sessions_spawn(22%) + pytest(22%) + coverage(13%) + PreviousPhaseArtifacts(13%)`
- **P5–P8**: `FrameworkEnforcer(50%) + Sessions_spawn(35%) + PreviousPhaseArtifacts(15%)`

- **If Truth ≥ 90%**: Phase advance proceeds
- **If Truth < 90%**: Phase advance BLOCKED (exit code 11); manual intervention required

### P7/P8: Entry/Exit Notes
- **Entry**: Verified to be Gate 4 from P6 (not P7 itself)
- **Exit**: "Cleared by P6 Gate 4" — **no separate exit gate evaluation**

### Entry Gate Checks (P2-P8)
Each phase verifies predecessor completion before starting work:
- **P2**: git log contains `phase1(human-review): Phase 1 deliverables APPROVED`
- **P3-P5**: quality_manifest.json exists + predecessor Gate PASS
- **P6-P8**: quality_manifest.json exists + Gate 4 from P6 exists

### Preflight Hooks (all phases)
Before each phase's work loop, `run-phase` executes:
- FSM state check (INIT→RUNNING)
- KillSwitch status (must be CLOSED, not OPEN)
- Previous phase artifacts (ASPICE chain, P2+)
- Constitution validation (phase-appropriate check_type)
- SAB check (P3+)
- Traceability check (P3 info, P4+ block)
- Tool registry verification
- DriftDetector initialization (P3+)
- GapDetector initialization (P3+)
- CI readiness (advisory)

### sessions_spawn.log
**Required for all phases P1–P8** (HR-10):
- 2 entries per phase (Agent A + Agent B)
- Format: JSON with role, status, citations, confidence
- Commit at each CHECKPOINT push

---

## Auto-Generated

- Source: `scripts/generate_full_plan.py`
- Format: Mermaid Flowchart (machine-readable)
- Last Updated: 2026-05-07 (P5 exit gate corrected, P7_G3D→P7_G4D, Gate 1 per-dim note, Matrix table fixed)
- Validation: See `tests/test_harness_phase_flowchart.py` (12 test cases)
- **Audit Result**: Full audit vs codebase — P5 exit gate removed, P8 entry corrected, Gate 1 clarified
