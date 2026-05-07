# QUALITY_REPORT.md - {Project Name}

> On-demand Lazy Load template.

## 1. Quality Metrics Overview

| Metric | Phase {N-1} Baseline | Phase {N} Actual | Delta |
|--------|----------------------|-----------------|-------|
| Constitution | {previous} | {current} | {delta} |
| Coverage | {previous} | {current} | {delta} |

## 2. ASPICE Compliance

| Phase | Status |
|-------|--------|
| Phase 1-2 | PASS/FAIL |
| Phase 3-4 | PASS/FAIL |
| Phase 5-6 | PASS/FAIL |

## 3. Gate 4 Dimensions (12 dims, score_gate ≥ 85)

| Tier | Dimension | Threshold | Score | Status |
|------|-----------|-----------|-------|--------|
| 1 | D1_Linting | ≥ 90 | {value} | PASS/FAIL |
| 1 | D2_TypeSafety | ≥ 85 | {value} | PASS/FAIL |
| 1 | D3_TestCoverage | ≥ 80 | {value} | PASS/FAIL |
| 1 | D5_SecretsScanning | = 100 | {value} | PASS/FAIL |
| 1 | D6_LicenseCompliance | = 100 | {value} | PASS/FAIL |
| 1 | D7_MutationTesting | ≥ 70 | {value} | PASS/FAIL |
| 2 | D4_Security | ≥ 80 | {value} | PASS/FAIL |
| 3 | D8_Architecture | ≥ 80 | {value} | PASS/FAIL |
| 3 | D9_Readability | ≥ 80 | {value} | PASS/FAIL |
| 3 | D10_ErrorHandling | ≥ 80 | {value} | PASS/FAIL |
| 3 | D11_Documentation | ≥ 75 | {value} | PASS/FAIL |
| 3 | D12_Performance | ≥ 75 | {value} | PASS/FAIL |
| — | **Composite Score** | **≥ 85** | {value} | **PASS/FAIL** |

## 4. P6 Exit Conditions

| Condition | Threshold | Actual | Status |
|-----------|-----------|--------|--------|
| Gate 4 composite score | ≥ 85 | {value} | PASS/FAIL |
| Critical issues open | = 0 | {value} | PASS/FAIL |
| Hermes reviewer status | APPROVE | {value} | PASS/FAIL |

## 5. Root Cause Analysis

| Issue | Severity | Root Cause | Resolution |
|-------|----------|------------|------------|
| {issue} | {severity} | {root cause} | {resolution} |

## 6. Improvement Recommendations
1. {recommendation 1}
2. {recommendation 2}
