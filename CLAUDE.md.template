<!-- CLAUDE.md.template — harness-methodology v1.0 -->
<!-- Copy to your project root as CLAUDE.md and fill in the placeholders -->
# Project: {PROJECT_NAME}

## Methodology Handoff
- Framework: harness-methodology v1.0
- Quality Manifest: .methodology/quality_manifest.json
- Active Phase: {CURRENT_PHASE}  <!-- e.g. P3 -->
- Last Gate: {LAST_GATE} (Score: {LAST_GATE_SCORE})  <!-- e.g. Gate2 (Score: 78) -->
- Hermes Reviewer Target: ${HERMES_REVIEWER_TARGET}

## FR Registry
{FR_TABLE}
<!-- | FR ID | Description | Status | Gate 1 Score | -->
<!-- | FR-001 | ... | COMPLETE | 94 | -->

## Architecture Constraints
{ARCH_CONSTRAINTS}
<!-- Auto-populated from quality_manifest.json architecture_constraints -->

## High-Risk Modules
{HIGH_RISK_MODULES}
<!-- Auto-populated from quality_manifest.json high_risk_modules -->

## Open Issues (Top Priority)
{TOP_OPEN_ISSUES}
<!-- Auto-populated from issue_tracker_ext.py — critical + high severity -->

## NFR → Dimension Mapping
{NFR_DIM_MAP}
<!-- e.g. NFR-PERF-01 -> performance, NFR-SEC-01 -> security -->

## Agent Interaction Model
```
Johnny: "執行 Phase N"
  → Agent: plan-phase N       (generates Plan_Phase_N.md)
  → Johnny: reviews plan
  → Agent: run-phase N        (executes plan)
  → POST-FLIGHT: gate check + Hermes reviewer
```

## Gate Status
| Gate | Trigger | Score | Status |
|------|---------|-------|--------|
| Gate 1 | P3/P5/P7/P8 per-FR | — | {GATE1_STATUS} |
| Gate 2 | P3 exit | {GATE2_SCORE} | {GATE2_STATUS} |
| Gate 3 | P4 exit | {GATE3_SCORE} | {GATE3_STATUS} |
| Gate 4 | P6 full | {GATE4_SCORE} | {GATE4_STATUS} |
