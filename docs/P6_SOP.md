# Phase 6 — Quality Assurance (P6 SOP)
<!-- COMPLETE REPLACEMENT of original P6 SOP -->
<!-- Input: All Phase 1-5 artifacts + quality_manifest.json -->
<!-- Output: Gate 4 report -->

## Step 6.1 — Gate 4 (14-dim full harness)
```bash
python harness_cli.py run-gate --gate 4 --phase 6
# 14 dims, All Tiers, score_gate=85, max_rounds=3
# 新增維度: integration_coverage (0.05), test_assertion_quality (0.02)
# mutation_testing: objective_primary=true (tool_score 優先於 llm_score)
# CRG: full integration (Point 1-4)
# mutation_testing: median_runs=3
```

## Step 6.2 — Finalize Gate 4
```bash
python harness_cli.py finalize-gate --gate 4 --phase 6 --project .
```

## Exit Conditions
```
Gate 4 score >= 85 (score_gate)
AND critical_open == 0
AND Phase 6 truth >= 90% (PhaseTruthVerifier)
```

---

## Agent A Dispatch Template (P6 — per phase)

Orchestrator: copy this when spawning Agent A for P6.

```
[TASK]
Phase: 6 — Quality Assurance | FR-ID: n/a (per-phase task)
Role: QA_ENGINEER
Deliverable: QUALITY_REPORT.md (14-dim full audit)

SRS (all FRs):
> {paste relevant sections from docs/SRS.md — embed, not file path}

SAD constraints:
> {paste SAB block + architecture constraints from docs/SAD.md — embed}

All previous gate results:
> {paste quality_manifest.json gate_results — embed}

14 quality dimensions:
1. linting | 2. type_safety | 3. test_coverage | 4. security
5. secrets_scanning | 6. license_compliance | 7. mutation_testing
8. architecture | 9. readability | 10. error_handling
11. documentation | 12. performance | 13. integration_coverage
14. test_assertion_quality

Expected output:
- QUALITY_REPORT.md (per-dimension score + overall Gate 4 score)
- RELEASE_NOTES.md (summary of all FRs delivered)
- JSON: {"status": "success",
         "files": ["QUALITY_REPORT.md", "RELEASE_NOTES.md"],
         "confidence": N, "per_dimension": {...},
         "overall_score": N, "citations": [...], "summary": "..."}
```
