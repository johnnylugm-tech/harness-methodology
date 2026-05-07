# Harness-Methodology: Phase 1 → Phase 8 End-to-End Flowchart

> **Source**: SKILL.md v6.50.0 + SAD.md v2.2
> **Generated**: 2026-05-05
> **Scope**: Full autonomous P1→P8 pipeline with all gates, checkpoints, and decision branches

---

## Legend

```
[FSM]       = State machine transition (INIT→RUNNING→PAUSED→FREEZE→DONE)
[M1]        = KillSwitch circuit breaker check (CLOSED/OPEN/HALF_OPEN)
[M2]        = Drift detection (UQLM EnsembleScorer)
[M3]        = Gap detection (SPEC.md ↔ AST scan)
[HR-NN]     = Hard rule enforcement (see SKILL.md §4)
[CHECKPOINT]= Git push checkpoint (recovery point)
[Human¹]    = Human peer review required (P1/P2 only)
[CRG]       = Code Review Graph reconnaissance
[ECC]       = Everything-Claude-Code hooks (pre-installed, global)
```

---

## Phase 1 — Requirements Specification

```
SESSION START (P1 entry — no upstream gate)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  [ECC] hooks active (pre:bash:dispatcher, suggest-compact)   │
│  Read SKILL.md                                               │
│  P1 routing: Entry=None, Exit=Human¹, artifact=SRS.md       │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  python harness_cli.py plan-phase --phase 1                 │
│    → generate_phase1_tasks()                                │
│    → P1 plan is static (no SAD.md dependency)               │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
╔═════════════════════════════════════════════════════════════╗
║  GENERATED PLAN — phase1_plan.md                           ║
║                                                             ║
║  ### Pre-Phase Preflight ✅                                 ║
║  python harness_cli.py run-phase --phase 1 --project $REPO  ║
║                                                             ║
║  ### FR Discovery (from user goals / project brief)         ║
║  > Agent A (PRODUCT_MANAGER): Draft SRS sections            ║
║  > Agent B (ARCHITECT): Review vs project goals             ║
║  > Iterate until Human¹-ready                               ║
║                                                             ║
║  ### CHECKPOINT: Human¹ Review — SRS.md                     ║
║  Human reads SRS.md → APPROVE / REJECT                      ║
╚═════════════════════════════════════════════════════════════╝
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  [PREFLIGHT] python harness_cli.py run-phase --phase 1      │
│    ├─ [FSM] INIT → RUNNING                                  │
│    ├─ [M1] KillSwitch check (must be CLOSED)                │
│    ├─ Constitution-as-Code validation                       │
│    └─ Tool registry check                                   │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
    ┌──────── REQUIREMENTS WORKFLOW ──────────────────────────┐
    │                                                          │
    │  Agent A (PRODUCT_MANAGER):                              │
    │    Draft SRS.md sections:                                │
    │      - Project overview + goals                          │
    │      - Functional Requirements (### FR-XX:)              │
    │      - Non-Functional Requirements (NFR-1..NFR-6)       │
    │      - Constraints + assumptions                         │
    │    Returns: {status, files, confidence, citations}       │
    │                                                          │
    │  Agent B (ARCHITECT):                                    │
    │    Review SRS.md against project brief                   │
    │    Check: completeness, consistency, testability         │
    │    Returns: {status, review_status, reason, confidence}  │
    │                                                          │
    │  [M2] DriftDetector: no code yet → score N/A             │
    │  [HR-01] A≠B enforced ✅                                 │
    │  [HR-04] HybridWorkflow mode=ON ✅                       │
    │  [HR-12] iteration guard (max 5 rounds)                  │
    │                                                          │
    └──────────────────────────────────────────────────────────┘
                   │
                   ▼
    ╔══════════════════════════════════════════════════════════╗
    ║  ### Human¹ CHECKPOINT: SRS.md Review                  ║
    ║                                                          ║
    ║  Human reads SRS.md:                                     ║
    ║    - Are all FR-XX sections present?                     ║
    ║    - Do NFRs cover the 6 required dimensions?            ║
    ║    - Is the scope clear and bounded?                     ║
    ║                                                          ║
    ║  CASE 1 APPROVE → git push → P1 COMPLETE ✅             ║
    ║  CASE 2 REJECT  → fix issues → re-submit for review     ║
    ║                                                          ║
    ║  SRS.md is THE foundation for all downstream phases.    ║
    ║  Without APPROVE, P2+ cannot proceed.                   ║
    ╚══════════════════════════════════════════════════════════╝
                   │ (APPROVE)
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  [FSM] Phase 1 → DONE (phase_complete)                      │
│  git push → SRS.md committed to repo                        │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
    ### Phase 1 → Phase 2: Architecture Design ✅
    python harness_cli.py plan-phase --phase 2
    → open phase2_plan.md
```

---

## Phase 2 — Architecture Design

```
SESSION START (P2 entry — Human¹ APPROVE from P1 is precondition)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Read SKILL.md                                               │
│  P2 routing: Entry=Human¹, Exit=Human¹, artifact=SAD.md+ADR │
│  SRS.md MUST exist in repo before P2 begins                 │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  python harness_cli.py plan-phase --phase 2                 │
│    → generate_phase2_tasks()                                │
│    → Reads SRS.md FR-XX sections as input                   │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
╔═════════════════════════════════════════════════════════════╗
║  GENERATED PLAN — phase2_plan.md                           ║
║                                                             ║
║  ### Pre-Phase Preflight ✅                                 ║
║  python harness_cli.py run-phase --phase 2 --project $REPO  ║
║                                                             ║
║  ### Architecture Design Tasks                              ║
║  > Agent A (ARCHITECT): Draft SAD.md + ADR.md              ║
║  > Agent B (REVIEWER/ARCHITECT): Review vs SRS             ║
║                                                             ║
║  ### CHECKPOINT: Human¹ Review — SAD.md + ADR.md            ║
║  Human reads deliverables → APPROVE / REJECT                ║
║  SAD.md MUST include: architecture drivers, patterns,       ║
║  module map, CLI design, gate filter spec                   ║
╚═════════════════════════════════════════════════════════════╝
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  [PREFLIGHT] python harness_cli.py run-phase --phase 2      │
│    ├─ [FSM] RUNNING (carried from P1)                       │
│    ├─ [M1] KillSwitch check                                 │
│    ├─ SRS.md existence verified                             │
│    └─ Constitution check                                    │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
    ┌──────── ARCHITECTURE WORKFLOW ──────────────────────────┐
    │                                                          │
    │  Agent A (ARCHITECT):                                    │
    │    Draft SAD.md:                                         │
    │      §1 — Architectural Drivers (5 NFRs → decisions)    │
    │      §2 — Macro Architecture (Pipe & Filter, patterns)  │
    │      §3 — Module Map (core/, enforcement/, harness/)    │
    │      §4 — Gate Filter Spec (G1/G2/G3/G4 thresholds)    │
    │      §5 — Integration Layer (CRG, Hermes, GitHub)       │
    │    Draft ADR.md (Architecture Decision Records):         │
    │      Each decision: context → options → decision →       │
    │      consequences                                         │
    │    Returns: {status, files, confidence, citations}       │
    │                                                          │
    │  Agent B (REVIEWER / ARCHITECT):                         │
    │    Cross-reference SAD.md against SRS.md:                │
    │      - Does each NFR have a corresponding decision?      │
    │      - Are all 8 phases represented in the pipe?         │
    │      - Do gate thresholds match SKILL.md?                │
    │      - Are module responsibilities clear?                │
    │    Returns: {status, review_status, reason, confidence}  │
    │                                                          │
    │  [HR-01] A≠B enforced ✅                                 │
    │  [HR-04] HybridWorkflow mode=ON ✅                       │
    │  [HR-12] iteration guard (max 5 rounds)                  │
    │                                                          │
    └──────────────────────────────────────────────────────────┘
                   │
                   ▼
    ╔══════════════════════════════════════════════════════════╗
    ║  ### Human¹ CHECKPOINT: SAD.md + ADR.md Review         ║
    ║                                                          ║
    ║  Human reads SAD.md + ADR.md:                            ║
    ║    - Architecture decisions justified?                   ║
    ║    - All SRS FRs traceable to modules?                   ║
    ║    - Gate thresholds correct (75/80/85)?                 ║
    ║    - Patterns appropriate for the stack?                 ║
    ║                                                          ║
    ║  CASE 1 APPROVE → git push → P2 COMPLETE ✅             ║
    ║    → quality_manifest.json committed                    ║
    ║    → P3+ dynamic planning NOW possible                  ║
    ║  CASE 2 REJECT  → fix issues → re-submit                ║
    ╚══════════════════════════════════════════════════════════╝
                   │ (APPROVE)
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  python harness_cli.py manifest --fr-ids FR-01... \        │
│      --sad SAD.md                                            │
│    → quality_manifest.json written                           │
│    → FR IDs extracted for P3+ dynamic planning               │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  [FSM] Phase 2 → DONE                                       │
│  git push → SAD.md + ADR.md + quality_manifest.json         │
│                                                              │
│  ⚠️  CRITICAL MILESTONE: SAD.md now exists.                 │
│  From P3 onward, all plans are generated DYNAMICALLY        │
│  from quality_manifest.json (FR IDs from SAD.md).           │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
    ### Phase 2 → Phase 3: Implementation ✅
    python harness_cli.py plan-phase --phase 3
    → open phase3_plan.md
```

---

## Phase 3 — Implementation

```
SESSION START (P3 entry — Human¹ APPROVE from P2 is precondition)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Read SKILL.md                                               │
│  P3 routing: Entry=Human¹, Exit=Gate2(75), artifact=code    │
│  quality_manifest.json MUST exist (FR IDs from SAD.md)      │
│  Gate 1 applies per-FR; Gate 2 is phase exit                │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  python harness_cli.py plan-phase --phase 3                 │
│    → generate_phase3_tasks() [DYNAMIC — reads manifest]     │
│    → FR IDs from quality_manifest.json                      │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
╔═════════════════════════════════════════════════════════════╗
║  GENERATED PLAN — phase3_plan.md (DYNAMIC)                 ║
║                                                             ║
║  > Checkpoint Index:                                        ║
║  > - CHECKPOINT-1: Gate 1 / FR-01                          ║
║  > - CHECKPOINT-2: Gate 1 / FR-02                          ║
║  > - ...                                                    ║
║  > - CHECKPOINT-N: Gate 1 / FR-NN                          ║
║  > - CHECKPOINT-N+1: Gate 2 (Phase 3 Exit)                 ║
║                                                             ║
║  ### Pre-Phase Preflight ✅                                 ║
║  python harness_cli.py run-phase --phase 3 --project $REPO  ║
╚═════════════════════════════════════════════════════════════╝
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  [PREFLIGHT] python harness_cli.py run-phase --phase 3      │
│    ├─ [FSM] RUNNING (carried from P2)                       │
│    ├─ [M1] KillSwitch check (CLOSED required)               │
│    ├─ [M2] DriftDetector initialized                        │
│    ├─ Constitution check                                    │
│    ├─ quality_manifest.json verified                        │
│    └─ Tool registry: CRG, pytest, ruff, mypy confirmed      │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
    ┌──────── FR LOOP (per FR from quality_manifest.json) ─────┐
    │                                                          │
    │  #### FR-XX: Implementation                             │
    │                                                          │
    │  ┌─ A/B Work (HR-01, HR-04, HR-10) ──────────────────┐  │
    │  │  Agent A (DEVELOPER):                               │  │
    │  │    [TDD-1] Write failing test (RED)                │  │
    │  │    [TDD-2] Implement FR until test passes (GREEN)  │  │
    │  │    [TDD-3] Refactor without breaking tests         │  │
    │  │    Returns: {status, files, confidence,            │  │
    │  │              citations, summary}                   │  │
    │  │                                                     │  │
    │  │  Agent B (REVIEWER):                                │  │
    │  │    Review vs SRS §FR-XX + SAD                      │  │
    │  │    Check: correctness, style, edge cases           │  │
    │  │    Returns: {status, review_status, reason,        │  │
    │  │              confidence, citations, summary}       │  │
    │  │                                                     │  │
    │  │  [Constitution Check] — BVS + HR-09 validation     │  │
    │  │  [CQG] — Linter + Type Check + Coverage            │  │
    │  │  [HR-12] — iteration guard (max 5 rounds)          │  │
    │  └────────────────────────────────────────────────────┘  │
    │                                                          │
    │  [LOG] → sessions_spawn.log ✅                          │
    │    {"fr_id": "FR-XX", "role": "developer", ...}         │
    │    {"fr_id": "FR-XX", "role": "reviewer", ...}          │
    │                                                          │
    │  ┌─ ### 🔒 CHECKPOINT-K: Gate 1 — FR-XX ───────────┐   │
    │  │                                                    │   │
    │  │  G1a: run-gate --gate 1 --phase 3 --fr-id FR-XX  │   │
    │  │    → Prints evaluation instructions               │   │
    │  │                                                    │   │
    │  │  G1b: EVALUATE (Claude inline)                    │   │
    │  │    DIMENSIONS (each must score ≥75):              │   │
    │  │      linting         (ruff)                       │   │
    │  │      type_safety     (mypy)                       │   │
    │  │      test_coverage   (pytest --cov, ≥80%)         │   │
    │  │    Claude writes:                                 │   │
    │  │    .sessi-work/gate1_result.json                  │   │
    │  │                                                    │   │
    │  │  G1c: finalize-gate --gate 1 --phase 3 \         │   │
    │  │         --fr-id FR-XX                             │   │
    │  │    → Reads gate1_result.json                      │   │
    │  │    → Checks each dim ≥ its threshold              │   │
    │  │    → Updates quality_manifest.json                │   │
    │  │                                                    │   │
    │  │    CASE 1 PASS → G1d ✅                           │   │
    │  │    CASE 2 FAIL → fix failing dim(s) → repeat G1a  │   │
    │  │      (max auto-fix-rounds, default 3)             │   │
    │  │      [CRG] CRGBridge.check_impact() before each   │   │
    │  │      fix round — validates blast radius safety    │   │
    │  │                                                    │   │
    │  │  G1d: git push → CHECKPOINT-K saved ✅           │   │
    │  └────────────────────────────────────────────────────┘  │
    │                                                          │
    │  [M2] DriftDetector.detect_all() after push              │
    │    → sad_drift + spec_drift + phase_drift check          │
    │                                                          │
    └────── next FR ───────────────────────────────────────────┘
                   │ (all FRs complete)
                   ▼
    ╔══════════════════════════════════════════════════════════╗
    ║  ### 🔒 CHECKPOINT-N+1: Gate 2 — Phase 3 Exit         ║
    ║                                                          ║
    ║  G2a: run-gate --gate 2 --phase 3 --project $REPO      ║
    ║    → Prints evaluation instructions                     ║
    ║                                                          ║
    ║  G2b: EVALUATE (Claude inline)                          ║
    ║    DIMENSIONS: 7 dims                                    ║
    ║    score_gate ≥ 75                         ║
    ║    quality_complete must be true                        ║
    ║    Claude writes: .sessi-work/gate2_result.json         ║
    ║                                                          ║
    ║  G2c: finalize-gate --gate 2 --phase 3                 ║
    ║    CASE 1 PASS → G2d ✅                                ║
    ║    CASE 2 CONTINUE → fix → repeat G2a ✅               ║
    ║    CASE 3 PLATEAU → deferred_fixes.md → push ✅        ║
    ║    CASE 4 BLOCKED → GateBlockedError →                  ║
    ║      → .methodology/last_block.md written              ║
    ║      → per-dimension diagnosis emitted                 ║
    ║      → escalate to human                               ║
    ║                                                          ║
    ║  [Phase Truth] HR-11 check:                             ║
    ║    FrameworkEnforcer BLOCK    40%                       ║
    ║    Sessions_spawn.log         20%                       ║
    ║    pytest actual pass         20%                       ║
    ║    Test coverage threshold    20%                       ║
    ║    → < 90% blocks phase advance                         ║
    ║                                                          ║
    ║  G2d: git push → CHECKPOINT saved ✅                   ║
    ║    → DEVELOPMENT_LOG updated with session_id            ║
    ║    → [FSM] Phase 3 → DONE                               ║
    ╚══════════════════════════════════════════════════════════╝
                   │ (PASS)
                   ▼
    ### Phase 3 → Phase 4: Testing ✅
    python harness_cli.py plan-phase --phase 4
    → open phase4_plan.md
```

---

## Phase 4 — Testing

```
SESSION START (Gate2 from P3 PASS is precondition)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Read SKILL.md                                               │
│  P4 routing: Entry=Gate2, Exit=Gate3(80), 12 dims           │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  python harness_cli.py plan-phase --phase 4                 │
│    → generate_phase4_tasks()                                │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
╔═════════════════════════════════════════════════════════════╗
║  GENERATED PLAN — phase4_plan.md                           ║
║                                                             ║
║  > Checkpoint Index:                                        ║
║  > - CHECKPOINT-1: Gate 1 / FR-01                          ║
║  > - CHECKPOINT-N: Gate 1 / FR-NN                          ║
║  > - CHECKPOINT-N+1: Gate 3 (Phase 4 Exit)                 ║
║                                                             ║
║  ### Pre-Phase Preflight ✅                                 ║
║  python harness_cli.py run-phase --phase 4 --project $REPO  ║
╚═════════════════════════════════════════════════════════════╝
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  [PREFLIGHT] python harness_cli.py run-phase --phase 4      │
│    ├─ [FSM] RUNNING                                         │
│    ├─ [M1] KillSwitch check                                 │
│    ├─ [M2] DriftDetector initialized                        │
│    ├─ [M3] GapDetector initialized (SPEC.md ↔ AST scan)     │
│    └─ Constitution check                                    │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
    ┌──────────────────────────────────────────────────────┐
    │  [test_plans 存在?]                                   │
    ├── YES ──→ Show TEST_PLAN.md items                    │
    │           then FR loop from manifest                  │
    └── NO  ──→ FR loop from SRS or manifest               │
    └──────────────────────────────────────────────────────┘
                   │
                   ▼
    ┌──────── FR LOOP (per FR from manifest/SRS) ──────────┐
    │                                                        │
    │  #### FR-XX: Test Execution                           │
    │                                                        │
    │  A/B Work (HR-01, HR-04, HR-10) ✅                    │
    │  Agent A (QA_ENGINEER):                               │
    │    Write integration/edge cases → execute →           │
    │    record results → verify ≥80% coverage              │
    │  Agent B (ARCHITECT): Review vs SRS + SAD             │
    │  [LOG] → sessions_spawn.log ✅                        │
    │                                                        │
    │  ### 🔒 CHECKPOINT-N: Gate 1 — FR-XX ✅               │
    │  G1a: run-gate --gate 1 --phase 4 --fr-id FR-XX      │
    │  G1b: evaluate (linting/type_safety/test_coverage)    │
    │  G1c: finalize-gate → FAIL? fix → repeat G1a         │
    │  G1d: git push → CHECKPOINT-N saved ✅               │
    │                                                        │
    └────── next FR ──────────────────────────────────────┘
                   │
                   ▼
    ╔══════════════════════════════════════════════════════╗
    ║  ### 🔒 CHECKPOINT-N+1: Gate 3 — Phase 4 Exit ✅    ║
    ║  12 dims, score_gate ≥ 80                            ║
    ║  [CRG] recon triggered inside run-gate ✅            ║
    ║  G3a: run-gate --gate 3 --phase 4                   ║
    ║  G3b: evaluate all 12 dims                           ║
    ║  G3c: finalize-gate                                  ║
    ║    CASE 1 PASS → G3d ✅                              ║
    ║    CASE 2 CONTINUE → fix → repeat G3a ✅            ║
    ║    CASE 3 PLATEAU → deferred_fixes.md → push ✅     ║
    ║    CASE 4 BLOCKED → GateBlockedError → escalate ✅  ║
    ║  G3d: git push → CHECKPOINT-N+1 = phase exit saved  ║
    ║  [Phase Truth] HR-11 (≥90% required)                ║
    ║  OUTPUT: TEST_RESULTS.md ✅                          ║
    ╚══════════════════════════════════════════════════════╝
                   │ (PASS)
                   ▼
    ### Phase 4 → Phase 5: Verification & Delivery ✅
    python harness_cli.py plan-phase --phase 5
    → open phase5_plan.md
```

---

## Phase 5 — Verification & Delivery

```
SESSION START (Gate3 from P4 PASS is precondition)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Read SKILL.md                                               │
│  P5 routing: Entry=Gate3, Exit=None (Phase Truth only),     │
│  artifact=BASELINE.md                                       │
│  Gate 1 applies per-FR; no exit gate evaluation              │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  python harness_cli.py plan-phase --phase 5                 │
│    → generate_phase5_tasks() [DYNAMIC]                      │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
╔═════════════════════════════════════════════════════════════╗
║  GENERATED PLAN — phase5_plan.md                           ║
║                                                             ║
║  > Checkpoint Index:                                        ║
║  > - CHECKPOINT-1: Gate 1 / FR-01 (verification)           ║
║  > - CHECKPOINT-N: Gate 1 / FR-NN                          ║
║  > - CHECKPOINT-N+1: Phase 5 Exit (Phase Truth only)      ║
║                                                             ║
║  ### Pre-Phase Preflight ✅                                 ║
║  python harness_cli.py run-phase --phase 5 --project $REPO  ║
╚═════════════════════════════════════════════════════════════╝
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  [PREFLIGHT] python harness_cli.py run-phase --phase 5      │
│    ├─ [FSM] RUNNING                                         │
│    ├─ [M1] KillSwitch check                                 │
│    ├─ [M2] DriftDetector.detect_all() — full scan           │
│    ├─ [M3] GapDetector.run() — gap report                   │
│    ├─ Constitution check                                    │
│    └─ TEST_RESULTS.md from P4 verified                      │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
    ┌──────── VERIFICATION LOOP (per FR) ─────────────────────┐
    │                                                          │
    │  #### FR-XX: Verification & Delivery Check              │
    │                                                          │
    │  A/B Work (HR-01, HR-04, HR-10) ✅                      │
    │  Agent A (DEVOPS / QA_ENGINEER):                        │
    │    Verify FR-XX against acceptance criteria              │
    │    Run integration / E2E tests                           │
    │    Check deployment readiness                            │
    │    Verify documentation completeness                     │
    │  Agent B (ARCHITECT):                                    │
    │    Review: does verified output match SRS intent?        │
    │    Cross-check NFR compliance (NFR-1..NFR-6)            │
    │  [LOG] → sessions_spawn.log ✅                          │
    │                                                          │
    │  ### 🔒 CHECKPOINT-K: Gate 1 — FR-XX ✅                 │
    │  G1a: run-gate --gate 1 --phase 5 --fr-id FR-XX        │
    │  G1b: evaluate (linting/type_safety/test_coverage)      │
    │  G1c: finalize-gate                                     │
    │  G1d: git push → CHECKPOINT saved ✅                    │
    │                                                          │
    └────── next FR ───────────────────────────────────────────┘
                   │ (all FRs verified)
                   ▼
    ╔══════════════════════════════════════════════════════════╗
    ║  ### 🔒 Phase 5 Exit — Phase Truth Only                 ║
    ║                                                          ║
    ║  [Phase Truth] HR-11 (≥90% required)                    ║
    ║                                                          ║
    ║  No separate exit gate evaluation.                      ║
    ║  P5 exit is cleared by Phase Truth ≥90%.                ║
    ║                                                          ║
    ║  Phase Truth PASS → BASELINE.md generated               ║
    ║  Phase Truth FAIL → escalate                            ║
    ║                                                          ║
    ║  git push → CHECKPOINT saved ✅                         ║
    ║    → [FSM] Phase 5 → DONE                               ║
    ╚══════════════════════════════════════════════════════════╝
                   │ (PASS)
                   ▼
    ### Phase 5 → Phase 6: Quality Assurance ✅
    python harness_cli.py plan-phase --phase 6
    → open phase6_plan.md
```

---

## Phase 6 — Quality Assurance

```
SESSION START (Phase Truth from P5 PASS is precondition)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Read SKILL.md                                               │
│  P6 routing: Entry=Gate3, Exit=Gate4(85), 12 dims           │
│  QUALITY_REPORT.md is the key deliverable                    │
│  Hermes APPROVE required (Human Checkpoint #2)               │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  python harness_cli.py plan-phase --phase 6                 │
│    → generate_phase6_tasks()                                │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
╔═════════════════════════════════════════════════════════════╗
║  GENERATED PLAN — phase6_plan.md                           ║
║                                                             ║
║  > Quality Dimensions (12 dim audit):                       ║
║  >   1. linting          2. type_safety                     ║
║  >   3. test_coverage    4. security                        ║
║  >   5. secrets_scanning 6. license_compliance              ║
║  >   7. mutation_testing 8. architecture                    ║
║  >   9. readability     10. error_handling                  ║
║  >  11. documentation   12. performance                     ║
║                                                             ║
║  > CHECKPOINT-1: Gate 4 (Phase 6 Exit) — score ≥ 85        ║
║                                                             ║
║  ### Pre-Phase Preflight ✅                                 ║
║  python harness_cli.py run-phase --phase 6 --project $REPO  ║
╚═════════════════════════════════════════════════════════════╝
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  [PREFLIGHT] python harness_cli.py run-phase --phase 6      │
│    ├─ [FSM] RUNNING                                         │
│    ├─ [M1] KillSwitch check                                 │
│    ├─ [M2] DriftDetector — full drift audit                 │
│    ├─ [M3] GapDetector — gap report (SPEC ↔ code)          │
│    ├─ CRG reconnaissance triggered                          │
│    ├─ Constitution full audit                               │
│    └─ BASELINE.md from P5 verified                          │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
    ┌──────── 12-DIMENSION QUALITY AUDIT ────────────────────┐
    │                                                          │
    │  Per dimension:                                          │
    │    Round 1: Read evaluate_dimension.md prompt            │
    │    Round 2: Run static analysis tools:                   │
    │      linting          → ruff check                       │
    │      type_safety      → mypy --strict                    │
    │      test_coverage    → pytest --cov + coverage.json    │
    │      security         → bandit -r                        │
    │      secrets_scanning → detect-secrets / gitleaks        │
    │      license_compliance → pip-licenses / licensecheck    │
    │      mutation_testing → mutmut / mutpy (median-3 ≥70)   │
    │      architecture     → [CRG] community cohesion +       │
    │                          coupling analysis               │
    │      readability      → radon mi / maintainability index │
    │      error_handling   → [CRG] error path flow analysis   │
    │      documentation    → pydocstyle / doc coverage        │
    │      performance      → radon cc / lizard complexity     │
    │    Round 3: Score dimension (0–100) vs threshold         │
    │    Round 4: Record issues → issue_tracker.py             │
    │                                                          │
    │  [M2] DriftDetector — code/spec/phase drift scores       │
    │  [M3] GapDetector — unimplemented FR detection           │
    │  [CRG] detect_changes(base=HEAD~1) — impact analysis     │
    │                                                          │
    │  Each failing dimension enters IMPROVEMENT LOOP:         │
    │    → fix → evaluate → fix → evaluate                     │
    │    → [CRG] CRGBridge.check_impact() before each fix      │
    │    → max auto-fix-rounds (default 3)                     │
    │    → [M1] KillSwitch monitors failure rate               │
    │                                                          │
    └──────────────────────────────────────────────────────────┘
                   │
                   ▼
    ╔══════════════════════════════════════════════════════════╗
    ║  ### 🔒 CHECKPOINT-1: Gate 4 — Phase 6 Exit            ║
    ║                                                          ║
    ║  G4a: run-gate --gate 4 --phase 6 --project $REPO      ║
    ║    → Prints full 12-dim evaluation instructions         ║
    ║    → [CRG] full graph rebuild + reconnaissance          ║
    ║                                                          ║
    ║  G4b: EVALUATE (Claude inline)                          ║
    ║    All 12 dimensions scored 0–100                       ║
    ║    score_gate ≥ 85 required                              ║
    ║    quality_complete must be true                        ║
    ║    Claude writes: .sessi-work/gate4_result.json         ║
    ║                                                          ║
    ║  G4c: finalize-gate --gate 4 --phase 6                 ║
    ║    CASE 1 PASS → G4d ✅                                ║
    ║    CASE 2 CONTINUE → fix → repeat G4a                  ║
    ║    CASE 3 PLATEAU → deferred_fixes.md → push           ║
    ║    CASE 4 BLOCKED → GateBlockedError                    ║
    ║      → .methodology/last_block.md                      ║
    ║      → per-dimension fix hints emitted                 ║
    ║                                                          ║
    ║  ┌─ Human Checkpoint #2: Hermes APPROVE ─────────────┐ ║
    ║  │  Hermes MCP sends APPROVE request to Telegram     │ ║
    ║  │  Human clicks APPROVE / REJECT                    │ ║
    ║  │  ⚠️  Gate 4 CANNOT pass without Hermes APPROVE    │ ║
    ║  │  (pipeline waits at exit 10 if no response)       │ ║
    ║  └───────────────────────────────────────────────────┘ ║
    ║                                                          ║
    ║  [Phase Truth] HR-11 (≥90% required)                    ║
    ║                                                          ║
    ║  G4d: git push → CHECKPOINT saved ✅                   ║
    ║    → QUALITY_REPORT.md generated                        ║
    ║    → [FSM] Phase 6 → DONE                               ║
    ╚══════════════════════════════════════════════════════════╝
                   │ (PASS + Hermes APPROVE)
                   ▼
    ### Phase 6 → Phase 7: Risk Management ✅
    python harness_cli.py plan-phase --phase 7
    → open phase7_plan.md
```

---

## Phase 7 — Risk Management

```
SESSION START (Gate4 from P6 PASS is precondition)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Read SKILL.md                                               │
│  P7 routing: Entry=Gate4 (from P6), Exit=None (Phase Truth  │
│  only), artifact=RISK_REGISTER.md                           │
│  Gate 1 applies per-FR; cleared by P6 Gate 4                │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  python harness_cli.py plan-phase --phase 7                 │
│    → generate_phase7_tasks() [DYNAMIC]                      │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
╔═════════════════════════════════════════════════════════════╗
║  GENERATED PLAN — phase7_plan.md                           ║
║                                                             ║
║  > Checkpoint Index:                                        ║
║  > - CHECKPOINT-1: Gate 1 / RISK-01 (security audit)       ║
║  > - CHECKPOINT-2: Gate 1 / RISK-02 (dependency audit)     ║
║  > - CHECKPOINT-N: Gate 1 / RISK-NN                        ║
║  > - CHECKPOINT-N+1: Phase 7 Exit (Phase Truth only)       ║
║                                                             ║
║  ### Pre-Phase Preflight ✅                                 ║
║  python harness_cli.py run-phase --phase 7 --project $REPO  ║
╚═════════════════════════════════════════════════════════════╝
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  [PREFLIGHT] python harness_cli.py run-phase --phase 7      │
│    ├─ [FSM] RUNNING                                         │
│    ├─ [M1] KillSwitch health check                          │
│    │    → Verify no OPEN state from prior phases            │
│    │    → Reset failure counters for P7                     │
│    ├─ [M2] DriftDetector — security drift scan              │
│    ├─ QUALITY_REPORT.md from P6 reviewed                    │
│    ├─ Constitution check                                    │
│    └─ Tool registry: bandit, safety, pip-audit confirmed    │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
    ┌──────── RISK ASSESSMENT LOOP (per risk category) ───────┐
    │                                                          │
    │  #### RISK-XX: Risk Assessment                          │
    │                                                          │
    │  A/B Work (HR-01, HR-04, HR-10) ✅                      │
    │  Agent A (DEVOPS / SECURITY):                           │
    │    Security scan: bandit -r → findings                  │
    │    Dependency audit: safety check / pip-audit           │
    │    Secrets scan: detect-secrets / gitleaks              │
    │    License compliance audit                             │
    │    Risk register update: likelihood × impact            │
    │  Agent B (ARCHITECT):                                   │
    │    Review risk findings vs architecture                  │
    │    Assess blast radius of identified risks              │
    │    Verify mitigation strategies                         │
    │  [LOG] → sessions_spawn.log ✅                          │
    │                                                          │
    │  ### 🔒 CHECKPOINT-K: Gate 1 — RISK-XX ✅               │
    │  G1a: run-gate --gate 1 --phase 7 --fr-id RISK-XX      │
    │  G1b: evaluate (linting/type_safety/test_coverage)      │
    │  G1c: finalize-gate                                     │
    │  G1d: git push → CHECKPOINT saved ✅                    │
    │                                                          │
    │  [M1] KillSwitch.record_failure() if risk is critical   │
    │    → OPEN if threshold exceeded                         │
    │    → HALF_OPEN → CLOSED on recovery                     │
    │                                                          │
    └────── next risk category ────────────────────────────────┘
                   │ (all risk categories assessed)
                   ▼
    ╔══════════════════════════════════════════════════════════╗
    ║  ### 🔒 Phase 7 Exit — Phase Truth Only                 ║
    ║                                                          ║
    ║  [Phase Truth] HR-11 (≥90% required)                    ║
    ║  [M1] KillSwitch state check before exit                ║
    ║    → OPEN state blocks phase advance                    ║
    ║                                                          ║
    ║  No separate exit gate evaluation.                      ║
    ║  P7 exit is cleared by P6 Gate 4 + Phase Truth.         ║
    ║                                                          ║
    ║  Phase Truth PASS → git push ✅                         ║
    ║    → RISK_REGISTER.md generated                         ║
    ║    → [FSM] Phase 7 → DONE                               ║
    ╚══════════════════════════════════════════════════════════╝
                   │ (PASS)
                   ▼
    ### Phase 7 → Phase 8: Configuration Management ✅
    python harness_cli.py plan-phase --phase 8
    → open phase8_plan.md
```

---

## Phase 8 — Configuration Management

```
SESSION START (Gate4 from P6 PASS is precondition)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Read SKILL.md                                               │
│  P8 routing: Entry=Gate4 (from P6), Exit=None (Phase Truth  │
│  only), artifact=CONFIG_RECORDS.md                          │
│  Gate 1 applies per-FR; cleared by P6 Gate 4                │
│  FINAL PHASE — pipeline completion on PASS                  │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  python harness_cli.py plan-phase --phase 8                 │
│    → generate_phase8_tasks() [DYNAMIC]                      │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
╔═════════════════════════════════════════════════════════════╗
║  GENERATED PLAN — phase8_plan.md                           ║
║                                                             ║
║  > Checkpoint Index:                                        ║
║  > - CHECKPOINT-1: Gate 1 / CFG-01 (env verification)      ║
║  > - CHECKPOINT-2: Gate 1 / CFG-02 (CI/CD audit)           ║
║  > - CHECKPOINT-N: Gate 1 / CFG-NN                         ║
║  > - CHECKPOINT-N+1: Phase 8 Exit (Phase Truth only — FINAL) ║
║                                                             ║
║  ### Pre-Phase Preflight ✅                                 ║
║  python harness_cli.py run-phase --phase 8 --project $REPO  ║
╚═════════════════════════════════════════════════════════════╝
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  [PREFLIGHT] python harness_cli.py run-phase --phase 8      │
│    ├─ [FSM] RUNNING                                         │
│    ├─ [M1] KillSwitch final health check                    │
│    ├─ RISK_REGISTER.md from P7 verified                     │
│    ├─ All prior artifacts present:                          │
│    │    SRS.md → SAD.md → code → TEST_RESULTS.md →          │
│    │    BASELINE.md → QUALITY_REPORT.md → RISK_REGISTER.md  │
│    ├─ Constitution full audit (final)                       │
│    └─ Tool registry: all CI/CD tools confirmed              │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
    ┌──────── CONFIGURATION AUDIT LOOP ───────────────────────┐
    │                                                          │
    │  #### CFG-XX: Configuration Audit                       │
    │                                                          │
    │  A/B Work (HR-01, HR-04, HR-10) ✅                      │
    │  Agent A (DEVOPS):                                      │
    │    Environment verification (dev/staging/prod parity)    │
    │    Dependency version audit + lock file check            │
    │    CI/CD pipeline verification (.github/workflows/)     │
    │    Git hooks verification (setup-git-hooks.sh)          │
    │    Deployment manifest audit                             │
    │    Configuration drift detection                         │
    │  Agent B (ARCHITECT):                                   │
    │    Review: all configurations traceable to ADR?          │
    │    Verify SAD ↔ actual deployment topology               │
    │    Cross-check environment variables + secrets mgmt      │
    │  [LOG] → sessions_spawn.log ✅                          │
    │                                                          │
    │  ### 🔒 CHECKPOINT-K: Gate 1 — CFG-XX ✅                │
    │  G1a: run-gate --gate 1 --phase 8 --fr-id CFG-XX       │
    │  G1b: evaluate (linting/type_safety/test_coverage)      │
    │  G1c: finalize-gate                                     │
    │  G1d: git push → CHECKPOINT saved ✅                    │
    │                                                          │
    └────── next config category ──────────────────────────────┘
                   │ (all config categories audited)
                   ▼
    ╔══════════════════════════════════════════════════════════╗
    ║  ### 🔒 Phase 8 Exit — Phase Truth Only (FINAL)        ║
    ║                                                          ║
    ║  [Phase Truth] HR-11 (≥90% required) — FINAL           ║
    ║  [M1] KillSwitch final state: must be CLOSED            ║
    ║  [M2] Final drift check: all drift scores < threshold   ║
    ║  [M3] Final gap check: 0 critical gaps                  ║
    ║                                                          ║
    ║  No separate exit gate evaluation.                      ║
    ║  P8 exit is cleared by P6 Gate 4 + Phase Truth.         ║
    ║                                                          ║
    ║  Phase Truth PASS → git push ✅                         ║
    ║    → CONFIG_RECORDS.md generated                        ║
    ║    → Git tag: release-v<score> (via _tag_release)       ║
    ║    → [FSM] RUNNING → DONE                               ║
    ║    → Pipeline exit code: 0 (ALL PHASES COMPLETE)       ║
    ╚══════════════════════════════════════════════════════════╝
                   │ (PASS)
                   ▼
    ╔══════════════════════════════════════════════════════════╗
    ║  🏁  PIPELINE COMPLETE — P1→P8 DONE                    ║
    ║                                                          ║
    ║  Artifacts delivered:                                    ║
    ║    P1: SRS.md                                            ║
    ║    P2: SAD.md, ADR.md, quality_manifest.json             ║
    ║    P3: code + tests + sessions_spawn.log                 ║
    ║    P4: TEST_RESULTS.md                                   ║
    ║    P5: BASELINE.md                                       ║
    ║    P6: QUALITY_REPORT.md + Hermes APPROVE                ║
    ║    P7: RISK_REGISTER.md                                  ║
    ║    P8: CONFIG_RECORDS.md                                 ║
    ║                                                          ║
    ║  Audit trail: .methodology/state.json                    ║
    ║               quality_manifest.json                      ║
    ║               enforcement/execution_log.db               ║
    ║               .sessi-work/gate{1,2,3,4}_result.json     ║
    ║               DEVELOPMENT_LOG                            ║
    ║                                                          ║
    ║  [FSM] State: DONE ✅                                   ║
    ║  [M1] KillSwitch: CLOSED ✅                             ║
    ║  Pipeline exit code: 0                                   ║
    ╚══════════════════════════════════════════════════════════╝
```

---

## Cross-Cutting Concerns (All Phases)

```
┌─────────────────────────────────────────────────────────────┐
│  [ECC] Everything-Claude-Code Hooks (always active)         │
│  ├─ pre:bash:dispatcher — blocks git --no-verify            │
│  ├─ pre:edit-write:suggest-compact — context limit guard    │
│  └─ stop:cost-tracker — token/cost per session              │
├─────────────────────────────────────────────────────────────┤
│  [M1] KillSwitch (all phase transitions)                     │
│  ├─ CLOSED  → normal operation                              │
│  ├─ OPEN    → blocks all destructive ops                    │
│  └─ HALF_OPEN → recovery probe; → CLOSED or → OPEN         │
├─────────────────────────────────────────────────────────────┤
│  [M2] DriftDetector (continuous)                             │
│  ├─ sad_drift    — architecture deviation                    │
│  ├─ spec_drift   — requirement deviation                     │
│  └─ phase_drift  — process deviation                         │
├─────────────────────────────────────────────────────────────┤
│  [M3] GapDetector (P4+ phases)                               │
│  ├─ Parses SPEC.md for FR items                              │
│  ├─ Scans AST for implementations                            │
│  └─ Reports gaps: unimplemented / under-tested FRs           │
├─────────────────────────────────────────────────────────────┤
│  [HR-01..HR-15] Hard Rules (all phases)                      │
│  ├─ HR-01: A≠B (self-review forbidden)                      │
│  ├─ HR-04: HybridWorkflow mode=ON                            │
│  ├─ HR-10: sessions_spawn.log A/B entries                   │
│  ├─ HR-11: Phase Truth ≥ 90% to advance                     │
│  ├─ HR-12: Max 5 A/B review rounds → PAUSE                  │
│  └─ HR-14: Integrity < 40 → FREEZE                          │
├─────────────────────────────────────────────────────────────┤
│  [CRG] Code Review Graph (P3+ phases)                        │
│  ├─ build_or_update_graph_tool() — incremental or full       │
│  ├─ detect_changes_tool() — blast radius analysis            │
│  ├─ get_minimal_context_tool() — 100-token context           │
│  └─ get_impact_radius_tool() — dependency graph traversal    │
└─────────────────────────────────────────────────────────────┘
```

---

## Gate Summary Matrix

| Gate | Trigger | Phases | Threshold | Dimensions | Human? |
|------|---------|--------|-----------|------------|--------|
| **Gate 1** | per-FR completion | P3, P4, P5, P7, P8 | linting≥90, type_safety≥85, coverage≥80 | 3 dims (lint/type/cov) | No |
| **Gate 2** | P3 phase exit | P3 | score_gate ≥ 75 + quality_complete | 7 dims | No |
| **Gate 3** | P4 phase exit | P4 | score_gate ≥ 80 + quality_complete | 12 dims | No |
| **Gate 4** | P6 phase exit | P6 | score_gate ≥ 85 + quality_complete | 12 dims | **Yes** — Hermes APPROVE |

---

## Human Checkpoints

### Manual Mode (§11 plan checklist)

| # | Phase | Trigger | Action |
|---|-------|---------|--------|
| 1 | P1 exit | SRS.md ready | Human reads SRS.md → APPROVE / REJECT |
| 2 | P2 exit | SAD.md + ADR.md ready | Human reads deliverables → APPROVE / REJECT |
| 3 | P6 exit | Gate 4 evaluation done | Click APPROVE on Telegram (Hermes MCP) |

### Automated Pipeline Mode (run-pipeline)

P1+P2 outputs (SRS.md + SAD.md) are preconditions — the pipeline starts from P3:

| Checkpoint | When | Action |
|---|---|---|
| P1+P2 outputs | Before pipeline can plan P3+ | Provide SRS.md + SAD.md |
| Gate 4 — Final APPROVE | P6 exit | Click APPROVE on Telegram (Hermes MCP) |
| PAUSE (exit code 10) | Result file missing | Run run-gate, evaluate, then finalize-gate |

---

## Recovery Protocol (any phase, after crash/context reset)

```
CRASH / CONTEXT RESET
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  python harness_cli.py generate-next-plan --project $REPO   │
│                                                              │
│  Output:                                                     │
│    Phase      : N (Name)                                    │
│    Plan file  : .methodology/phaseN_plan.md                 │
│    Last ckpt  : CHECKPOINT-K (Gate G / FR-XX) ✓ PASS       │
│    Next ckpt  : CHECKPOINT-K+1 (Gate G / FR-YY)            │
│    [ACTION]     search plan for "CHECKPOINT-K+1", resume    │
│                                                              │
│  → Open .methodology/phaseN_plan.md                         │
│  → Search "### 🔒 CHECKPOINT-K+1"                           │
│  → Resume from that checkpoint                              │
│  → Do NOT re-read SKILL.md for task details                 │
│  → Plan is THE authority inside a phase                     │
└─────────────────────────────────────────────────────────────┘
```

---

## FSM State Transitions (Across All Phases)

```
                    ┌──────────┐
                    │   INIT   │  (session start, before P1)
                    └────┬─────┘
                         │ run-phase --phase 1
                         ▼
                    ┌──────────┐
              ┌─────│ RUNNING  │─────┐
              │     └────┬─────┘     │
              │          │           │
    HR-14     │          │           │ HR-12 (5+ rounds)
    Integrity │          │           │ HR-13 (3× estimate)
    < 40      │          │           │
              ▼          │           ▼
         ┌────────┐      │      ┌────────┐
         │ FREEZE │      │      │ PAUSED │
         └────────┘      │      └───┬────┘
                         │          │ resume / fix
                         │          ▼
                         │     ┌──────────┐
                         │     │ RUNNING  │ (resumed)
                         │     └────┬─────┘
                         │          │
              ┌──────────┤          │
              │          │          │
    [M1]      │          │          │ all phases
    KillSwitch│          │          │ complete
    triggered │          │          │
              ▼          │          ▼
         ┌────────┐      │     ┌────────┐
         │  OPEN  │      │     │  DONE  │ ← P8 exit (PASS)
         └───┬────┘      │     └────────┘
             │           │
             │ recovery  │
             ▼           │
         ┌────────┐      │
         │HALF_OPEN│     │
         └───┬────┘      │
             │ probe OK   │
             ▼           │
         ┌────────┐      │
         │ CLOSED │◄─────┘
         └────────┘
```

---

*Generated from SKILL.md v6.50.0 + SAD.md v2.2 — harness-methodology autonomous pipeline specification.*
