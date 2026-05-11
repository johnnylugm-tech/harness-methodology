# Phase 6 — Quality Assurance (P6 SOP)
<!-- COMPLETE REPLACEMENT of original P6 SOP (Gap G4) -->
<!-- Input: All Phase 1-5 artifacts + quality_manifest.json -->
<!-- Output: Gate 4 report + Hermes APPROVE -->

## Step 6.1 — Gate 4 (12-dim full harness)
```bash
python harness_cli.py run-gate --gate 4 --phase 6
# 12 dims, All Tiers, score_gate=85, max_rounds=3
# CRG: full integration (Point 1-4)
# mutation_testing: median_runs=3
```

## Step 6.2 — Hermes Reviewer (Gap G2)
```python
from core.agent_spawner import AgentSpawner
review = AgentSpawner().spawn(
    role="reviewer",
    prompt=f"Gate 4 Score: {result.score}/100. Open issues: {result.open_critical} critical.",
    context={"phase": 6},
    model="hermes",
    phase=6,
)
# Reviewer reads agent_personas/REVIEWER.md + Gate 4 results
# Returns: {review_status: APPROVE|REJECT, confidence, violations, summary}
```

## Exit Conditions
```
Gate 4 score >= 85 (score_gate)
AND critical_open == 0
AND Hermes Reviewer review_status == "APPROVE"
```

---

## Agent A Dispatch Template (P6 — per phase)

Orchestrator: copy this when spawning Agent A for P6.

```
[TASK]
Phase: 6 — Quality Assurance | FR-ID: n/a (per-phase task)
Role: QA_ENGINEER
Deliverable: QUALITY_REPORT.md (12-dim full audit)

SRS (all FRs):
> {paste relevant sections from docs/SRS.md — embed, not file path}

SAD constraints:
> {paste SAB block + architecture constraints from docs/SAD.md — embed}

All previous gate results:
> {paste quality_manifest.json gate_results — embed}

12 quality dimensions (from constitution/CONSTITUTION.md §2):
1. linting | 2. type_safety | 3. test_coverage | 4. documentation
5. readability | 6. error_handling | 7. security | 8. performance
9. architecture | 10. maintainability | 11. traceability | 12. integrity

Expected output:
- QUALITY_REPORT.md (per-dimension score + overall Gate 4 score)
- RELEASE_NOTES.md (summary of all FRs delivered)
- JSON: {"status": "success",
         "files": ["QUALITY_REPORT.md", "RELEASE_NOTES.md"],
         "confidence": N, "per_dimension": {...},
         "overall_score": N, "citations": [...], "summary": "..."}
```

## Agent B Dispatch Template (P6 — per phase)

Orchestrator: copy this when spawning Agent B for P6.

```
[TASK]
Phase: 6 — Quality Assurance | FR-ID: n/a (per-phase task)
Role: ARCHITECT (reviewer)

Quality report to review:
> {paste full QUALITY_REPORT.md — embed, not file path.
   Agent B is STATELESS (§0.5).}

Gate 4 result:
> {paste gate4_result.json — embed}

Review criteria:
1. All 12 dimensions evaluated? (no skipped dimensions)
2. Per-dimension scores consistent with evidence?
3. open_critical == 0 confirmed?
4. All FRs represented in the quality report?
5. RELEASE_NOTES.md accurate and complete?

Expected output:
- JSON: {"status": "success", "review_status": "APPROVE|REJECT",
         "confidence": N, "violations": [...], "citations": [...],
         "summary": "..."}
```
