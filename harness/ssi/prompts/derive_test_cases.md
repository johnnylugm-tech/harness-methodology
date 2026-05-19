# Derive Test Cases Protocol

Systematically derive a complete named test-case catalog (TEST_SPEC.md) from SRS
functional requirements and non-functional requirements.

**This skill is executed in Phase 2 by Agent A.**  
Output is validated by Agent B before P2 exit.  
P3 Agent A implements tests FROM this catalog — not ad-hoc.

---

## Execution Contract (強制，執行前確認)

> **行為紅線宣告，不可跳過。違反任一項，輸出視為無效。**
>
> ❌ **禁止行為：**
> - 在未閱讀每個 FR 完整描述的情況下生成 test names
> - 生成 derivation 欄位為空或為 "N/A" 的 test case
> - 每個 FR 只寫 1 個 happy path test，跳過 Q2-Q7
> - 複製上一個 FR 的 test case，改一個數字充數
> - 生成不符合 `test_{subject}_{condition_or_behavior}` 命名格式的名稱
>
> ✅ **每個 FR 的 TEST_SPEC entry 必須滿足：**
> - 至少 1 個 `happy_path` test（Q1，必填）
> - 至少 1 個 `failure` 或 `validation` test（Q2，必填）
> - `derivation` 欄位必須引用 Q1-Q7 或 NFR Pattern 編號（不得空白）
> - 所有從 SRS NFR 觸發的 pattern 必須出現（見 §2 NFR Pattern Table）

---

## Input Requirements

Before starting, read and confirm:
- [ ] `01-requirements/SRS.md` §2 (FRs) — full FR description for every entry
- [ ] `01-requirements/SRS.md` §3 (NFRs) — non-functional requirements list
- [ ] `02-architecture/SAD.md` §3 (Module Design) — module/class names (for precise test naming)

If SAD.md is not yet written, use provisional names from SRS FR descriptions.

---

## Step 1: NFR Pattern Extraction

Before processing any FR, scan SRS §3 NFRs and build the **Active Pattern Set** for this project.

For each keyword found in NFRs, activate the corresponding test pattern:

| NFR Keyword (SRS §3) | Pattern ID | Auto-injected test template |
|---|---|---|
| `authentication` / `auth` / `token` | NP-01 | `test_{fr}_{op}_unauthenticated_returns_401` |
| `authorization` / `permission` / `role` / `rbac` | NP-02 | `test_{fr}_{op}_insufficient_permission_returns_403` |
| `rate limit` / `throttle` / `rate_limit` | NP-03 | `test_{fr}_{op}_rate_limited_returns_429` |
| `validation` / `sanitize` / `input` | NP-04 | `test_{fr}_{op}_invalid_input_returns_422` |
| `idempotent` / `at-least-once` / `dedup` | NP-05 | `test_{fr}_{op}_idempotent_on_repeat` |
| `performance` / `latency` / `p95` / `sla` | NP-06 | `test_nfr_{metric}_within_{threshold}` |
| `availability` / `resilience` / `fault` | NP-07 | `test_{fr}_{op}_{dependency}_unavailable_graceful` |
| `security` / `injection` / `attack` / `pii` | NP-08 | `test_security_{attack_type}_blocked` |
| `audit` / `log` / `trace` | NP-09 | `test_{fr}_{event}_audit_log_written` |
| `data integrity` / `persist` / `round.trip` | NP-10 | `test_{fr}_data_round_trip_consistent` |
| `backward compat` / `version` / `migration` | NP-11 | `test_phase{n_minus_1}_contract_satisfied_in_phase{n}` |
| `pagination` / `limit` / `offset` | NP-12 | `test_{fr}_list_pagination_correct` + `test_{fr}_list_limit_boundary` |
| `concurrency` / `thread` / `async` | NP-13 | `test_{fr}_{op}_concurrent_requests_isolated` |
| `encryption` / `tls` / `tde` | NP-14 | `test_{fr}_data_at_rest_encrypted` |
| `timeout` / `deadline` | NP-15 | `test_{fr}_{op}_timeout_returns_degraded_response` |

**Active Pattern Set** = patterns whose keywords appear in SRS §3.
Record this set. Every FR will be checked against it in Step 2 Q-Probe 6.

---

## Step 2: Per-FR Derivation (7-Question Protocol)

Repeat for each `FR-XX` in SRS §2:

```
=== FR-XX: {description} ===

PRE-STEP: Classify FR type (one or more):
  □ API_ENDPOINT     — exposes HTTP interface
  □ DATA_ENTITY      — creates/manages persistent data
  □ ALGORITHM        — pure logic / computation / transformation
  □ STATE_MACHINE    — stateful lifecycle (FSM, session, queue item)
  □ INTEGRATION      — calls or adapts an external system
  □ SECURITY_CONTROL — enforces access, sanitizes, detects threats
  □ INFRASTRUCTURE   — config, deployment, migration, background job

Classification drives which questions generate mandatory vs optional test cases.
```

### Q1: HAPPY PATH (mandatory for ALL types)

> What is the minimal observable evidence that FR-XX works correctly?

- Describe the successful outcome in one sentence
- Name: `test_{module}_{fr_behavior}` or `test_fr{nn}_{short_behavior}`
- Type: `happy_path`
- Derivation: `Q1`

**Minimum**: 1 test. Complex FRs may have 2-3 distinct success scenarios.

### Q2: FAILURE PATHS (mandatory for ALL types)

> What inputs, states, or conditions cause FR-XX to fail?  
> For each failure mode, what is the expected error response?

For each failure mode identified:
- Invalid / missing required fields → `test_{fr}_missing_{field}_returns_422`
- Wrong type / format → `test_{fr}_invalid_{field}_format_rejected`
- Not found → `test_{fr}_{entity}_not_found_returns_404`
- Duplicate / conflict → `test_{fr}_duplicate_{entity}_rejected`
- Precondition not met → `test_{fr}_{precondition}_not_met_blocked`

Type: `validation` or `failure`  
Derivation: `Q2`

**Minimum**: 1 test. List every distinct failure mode separately.

### Q3: BOUNDARY CONDITIONS (conditional — applies when FR has numeric/size/time limits)

> Does the FR description or related NFR mention: counts, lengths, sizes, time windows,
> thresholds, capacity limits, or value ranges?

If YES: for each boundary B:
- At B: `test_{fr}_{entity}_at_{B}_accepted`
- Above B: `test_{fr}_{entity}_above_{B}_rejected`
- Zero / empty: `test_{fr}_{entity}_empty_handled`

Type: `boundary`  
Derivation: `Q3`

If NO boundaries identified: skip (no test generated).

### Q4: STATE TRANSITIONS (conditional — applies to STATE_MACHINE type)

> Does FR-XX manage state (FSM, lifecycle, session, queue status)?

If YES: for each valid transition T(state_a → state_b):
- `test_{fr}_transition_{state_a}_to_{state_b}_valid`

For each invalid transition (state_a → state_c where c ≠ valid_next):
- `test_{fr}_transition_{state_a}_to_{invalid}_rejected`

Invariant: initial state test:
- `test_{fr}_initial_state_is_{state_0}`

Type: `state_transition`  
Derivation: `Q4`

### Q5: EXTERNAL DEPENDENCY FAULTS (conditional — applies to INTEGRATION type)

> Does FR-XX call an external system (DB, cache, API, queue, filesystem)?

If YES: for each dependency D:
- D unavailable: `test_{fr}_{D}_unavailable_{fallback_behavior}`
  (e.g., `test_fr07_db_unavailable_returns_empty_list`)
- D returns error: `test_{fr}_{D}_error_propagates_gracefully`
- D slow / timeout: handled only if NFR-15 (timeout) is active

Type: `fault_injection`  
Derivation: `Q5`

### Q6: ACTIVE NFR PATTERNS (conditional — driven by Active Pattern Set from Step 1)

> Which patterns from the Active Pattern Set apply to FR-XX?

For each active pattern NP-XX that is relevant to FR-XX's behavior, generate the
template test case, substituting `{fr}` and `{op}` with concrete FR-specific names.

Skip a pattern if it clearly does not apply (e.g., NP-06 latency for a pure in-memory
computation with no NFR latency constraint).

Type: `nfr_pattern`  
Derivation: `Q6/NP-{ID}`

### Q7: CROSS-FR INTEGRATION (conditional — applies when FR-XX interacts with other FRs)

> Does FR-XX take output from another FR as input, or does its output feed into
> another FR? Name the specific FRs.

For each interaction pair (FR-XX ↔ FR-YY):
- `test_fr{xx}_output_feeds_fr{yy}_correctly`
- Or more descriptively: `test_{fr_xx_module}_with_{fr_yy_module}_pipeline`

Type: `integration`  
Derivation: `Q7/FR-{YY}`

---

## Step 3: Write TEST_SPEC.md Entry

For each FR, write an entry in the following format:

```markdown
### FR-XX: {one-line description from SRS}

**Classification**: {type(s) from pre-step}  
**Active Patterns**: {NP-XX, NP-YY, ...} (or "none")

| # | Test Function | Type | Derivation |
|---|---|---|---|
| 1 | `test_frXX_{behavior}` | happy_path | Q1 |
| 2 | `test_frXX_{error_condition}` | validation | Q2 |
| 3 | `test_frXX_{field}_boundary` | boundary | Q3 |
| 4 | `test_frXX_unauthenticated_returns_401` | nfr_pattern | Q6/NP-01 |
| 5 | `test_frXX_with_frYY_pipeline` | integration | Q7/FR-YY |
```

**Naming convention**:
- Prefer: `test_{module}_{behavior}` using actual module names from SAD.md
- Acceptable: `test_fr{nn}_{behavior}` when module not yet named
- No generic names: `test_it_works`, `test_basic`, `test_case_1` → rejected by Agent B

---

## Step 4: Cross-FR Section

After all individual FRs, add a cross-cutting section:

```markdown
## Cross-Cutting Test Cases

### NFR Integration
{List any NFR tests that apply to the whole system rather than a single FR}

### Backward Compatibility
{If project has multiple phases: test_phase{N-1}_contract_satisfied_in_phase{N}}

### Deployment Smoke
{At minimum: test_app_starts_and_health_endpoint_returns_200}
{Add more based on deployment NFRs: docker, k8s, backup}
```

---

## Step 5: Summary Table

Append at the end of TEST_SPEC.md:

```markdown
## Summary

| Metric | Count |
|---|---|
| FRs covered | N |
| Total test cases | N |
| By type: happy_path | N |
| By type: validation/failure | N |
| By type: boundary | N |
| By type: state_transition | N |
| By type: fault_injection | N |
| By type: nfr_pattern | N |
| By type: integration | N |
| Active NFR patterns applied | NP-XX, ... |
```

---

## Agent B Validation Checklist

Agent B must verify before APPROVE:

- [ ] Every FR from SRS §2 has an entry in TEST_SPEC.md
- [ ] Every FR has at least 1 `happy_path` + 1 `failure`/`validation` test
- [ ] Every active NFR pattern (from Step 1) appears in at least one FR entry
- [ ] No test function names are generic (`test_basic`, `test_case_1`, etc.)
- [ ] All derivation fields are non-empty and cite a Q-number or NP-number
- [ ] Module names in test functions match SAD.md module/class names where available
- [ ] Cross-cutting section is present with at least 1 deployment smoke test

**If any item fails: return REJECT with specific FR numbers and missing items.**

---

## Anti-Bias Rules

1. Derive tests from FR text + NFRs — never from implementation details
2. Do not skip Q7 just because "it seems obvious" — explicitly state "no interaction" if so
3. If a boundary is implicit (not stated numerically), note it as `ASSUMED: {value}` in derivation
4. State machine tests: enumerate ALL valid and invalid transitions, not just the happy ones
5. NFR patterns: if NP-XX is active but you skip it for a specific FR, state the reason inline

*Protocol version: v1.0 | Added in harness-methodology v2.5.0*
