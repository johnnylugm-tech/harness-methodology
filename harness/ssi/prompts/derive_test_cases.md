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
> - 每個 FR 只寫 1 個 happy path test，跳過 Q2-Q8
> - 複製上一個 FR 的 test case，改一個數字充數
> - 生成不符合 `test_{subject}_{condition_or_behavior}` 命名格式的名稱
> - 用描述性 id（如 `all_boundary_chars`）取代具體 `Inputs` 值，把選值/計數留給 P3
> - 在 Sub-assertion 的 `applies_to` 列入 predicate 對其 Inputs 不成立的 case
>
> ✅ **每個 FR 的 TEST_SPEC entry 必須滿足：**
> - 至少 1 個 `happy_path` test（Q1，必填）
> - 至少 1 個 `failure` 或 `validation` test（Q2，必填）
> - `derivation` 欄位必須引用 Q1-Q8、Step 2.5 或 NFR Pattern 編號（不得空白）
> - 所有從 SRS NFR 觸發的 pattern 必須出現（見 §2 NFR Pattern Table）
> - 每個 case 有具體 `Inputs`（真實值，非 pytest-id 形式）
> - Sub-assertion 表的每條 predicate 對其 `applies_to` 的 Inputs 自洽（P2 gate 會驗）

---

## Input Requirements

Before starting, read and confirm:
- [ ] `01-requirements/SRS.md` §2 (FRs) — full FR description for every entry
- [ ] `01-requirements/SRS.md` §3 (NFRs) — non-functional requirements list
- [ ] `TEST_INVENTORY.yaml` — P1 test naming conventions per FR (naming authority; use names from here where present)
- [ ] `02-architecture/SAD.md` §3 (Module Design) — module/class names (for precise test naming)

If SAD.md is not yet written, use provisional names from SRS FR descriptions.
If TEST_INVENTORY.yaml is absent or empty, derive all names via Q1-Q8 and note in the
TEST_SPEC.md header that no P1 naming authority was available.

---

## Step 0: Load Naming Conventions from TEST_INVENTORY.yaml

Read TEST_INVENTORY.yaml and extract the per-FR test function names from `fr_tests:`
and `cross_cutting:` sections. These are the naming AUTHORITY established in P1.

- For each FR-XX that has named test functions in TEST_INVENTORY.yaml:
  use those EXACT names. Prefer them over derived names.
- For FRs with partial coverage (e.g., only unit tests named, no integration tests):
  use the given names for the categories they cover, then fill gaps with Q1-Q8.
- For FRs not mentioned in TEST_INVENTORY.yaml:
  derive ALL names via the 8-Question Protocol.
- For cross_cutting names: place them in the Cross-Cutting section of TEST_SPEC.md.

This ensures bidirectional traceability: TEST_INVENTORY.yaml (P1 forward) →
TEST_SPEC.md (P2 single source of truth) → tests/ (P3 implementation).

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

## Step 1b: Architecture-Risk Triggers (v2.9 — MANDATORY, SAD/SAB-driven)

> Why: keyword matching on SRS prose misses implementation-implied risks.
> tts-new shipped a HALF_OPEN probe race and a dead cache path with Gate 4
> near 100 — its SRS never said "concurrency", so NP-13 never fired.
> Architecture facts, not prose, must force these patterns.

Scan SAD.md module descriptions (and SAB.json module layers if present). For
every module matching a risk trait below, the listed patterns become ACTIVE
for the FRs that module implements — **regardless of SRS keywords**:

| Module risk trait (from SAD/SAB) | Forced patterns | Required case shape |
|---|---|---|
| shared mutable state + async/threads (state machines, counters, pools, breakers) | NP-13 | `test_{fr}_{op}_state_transition_under_concurrent_load` — N concurrent callers crossing the SAME transition; assert the invariant (single probe, exact count), not just "no crash" |
| external process (subprocess / ffmpeg / shell) | NP-15 | `test_{fr}_{op}_subprocess_timeout_enforced` + orphan/cleanup assertion |
| network client / retry logic | NP-07 + NP-15 | `test_{fr}_{op}_{dependency}_unavailable_graceful` + `test_{fr}_retry_backoff_bounded` (assert retry × concurrency amplification is capped) |
| cache / optional dependency | NP-07 | `test_{fr}_cache_unavailable_fallback` + `test_{fr}_cache_recovers_after_transient_outage` + reachability: `test_{fr}_cache_actually_used_on_hit` (the dead-cache class: wiring exists, path unreachable) |

Rules:
- Record each Step-1b activation in the TEST_SPEC "Pattern Activation" table
  with its trigger source: `SAD: <module>` (vs `SRS: <keyword>` from Step 1).
- **Integration variant required**: every Step-1b-forced case MUST live under
  `tests/integration/` (declared in TEST_SPEC like any other case). D4
  spec-coverage and the P3 mirror gate then enforce existence and fidelity —
  the spec IS the enforcement; no new machinery.
- A module with a risk trait and NO forced case in TEST_SPEC is an Agent B
  REJECT (see checklist).

---

## Step 1c: SEC-Block Threat Triggers (Round 10 — MANDATORY, SAD §6-driven)

> Why: NP-01/02/03/08/09/14 (auth, authz, rate-limit, attack, audit,
> encryption) are exactly the patterns Bug #35 stripped from automated
> keyword scoring (SRS/SAD prose keyword density false-positive-failed
> honest tool-type projects, so `security` was removed from constitution
> P1/P3/P4 scoring). Without an independent forcing mechanism, a project
> that DOES have a real attack surface but writes non-keyword prose
> ("reject bad input" instead of "input validation") gets zero of these
> tests. SAD §6's `threats[]` are structured data, not prose — they force
> the pattern deterministically, the same way Step 1b forces patterns from
> SAD/SAB facts.

Read SAD.md §6's `threats[]` (only present when `applicability: full`). For
every threat, its `category` forces the listed pattern(s) for the FR that
owns `owner_module` — **regardless of SRS keywords**:

| Threat category (STRIDE) | Forced patterns |
|---|---|
| spoofing | NP-01 |
| elevation_of_privilege | NP-02 |
| denial_of_service | NP-03 |
| tampering | NP-04 + NP-08 |
| information_disclosure | NP-08 + NP-14 |
| repudiation | NP-09 |

**Required case shape**: the threat's own `verified_by` name (already
declared in SAD.md §6) IS the forced case — do not author a second,
redundant test. Add ONE row to the owning FR's TEST_SPEC table using that
exact name, `Type: nfr_pattern`, `Derivation: Q6/1c/NP-XX`.

Rules:
- Record each Step-1c activation in the TEST_SPEC "Pattern Activation" table
  with trigger source `SEC: <threat-id>` (vs `SAD: <module>` from Step 1b,
  `SRS: <keyword>` from Step 1).
- A threat whose category maps here and has NO corresponding row in the
  owning FR's TEST_SPEC entry is an Agent B REJECT (see checklist).
- `check-artifact-consistency` (`core.quality_gate.security_design`, rule
  R8) independently enforces that every `verified_by` name exists as a real
  test from Phase 5 — Step 1c is what gets it written into TEST_SPEC during
  P2 so P3 implements it on schedule, not a last-minute P5 scramble.

---

## Step 1d: NFR Acceptance-Criteria Disposition (MANDATORY — every declared AC-id must be citable)

> Why: Steps 1/1b/1c only get an NFR a test case when one of its keywords,
> SAD module traits, or SAD §6 threats trips a pattern (NP-01..NP-15). An
> NFR whose dimension is `documentation` / `license_compliance` /
> `architecture_constraints` / `mutation_testing` / `integration_coverage` /
> `readability` — verified by static tooling (docstring scanners,
> `pip-licenses`, `import-linter`, `mutmut`, coverage line count, `radon`)
> rather than a request/response case — trips none of them. Left silent,
> Agent A has no instruction for what to do with that NFR's declared
> `AC-Nx.y` identifiers, and no instruction means no citation: `harness
> check_ac_test_spec_coverage` (Round 51/55/62) requires every declared
> AC-id from SRS.md to be disposed of somewhere in TEST_SPEC.md, FR and NFR
> alike, with zero classification-based exemption — so an NFR that
> legitimately needs no test case still needs its AC-ids written down
> somewhere, or the gate reads it as a dropped requirement at P2 exit.
> Measured: an Agent-A-invented but instruction-free convention ("Active
> Patterns: none... deferred to downstream phases") read as reasonable
> prose to a human and to Agent B, but cited zero AC-ids and produced
> dozens of avoidable P2→P3 blocking obligations.
>
> **A `Deferred:` line is NOT coverage** (Round 69 站5). The checker reads
> three states, not two: cited by a case, deferred to a named verifier, or
> uncited. A deferral is non-blocking and it is not free — each one is an
> `ac_deferred` finding and a `gate:ac-deferred` row in the degradation
> ledger, on record as a criterion nothing in this phase verified. Do not
> reach for it to make a gate go quiet; reach for it when the criterion
> genuinely has no request/response case.

For every NFR whose declared `AC-Nx.y` identifiers were **not** covered by a
Step 1 / 1b / 1c pattern activation (in whole or in part — a partially
activated NFR still needs this for its uncovered ids):

- Write ONE line per NFR, in its TEST_SPEC.md section, in exactly this shape
  (quoted verbatim from `artifact_consistency.ac_deferral_shape()`, which is
  what the checker matches):

  ```
  Deferred: AC-Nx.y[, AC-Nx.z, ...] — <which downstream phase or which tool verifies this>, not a TEST_SPEC case.
  ```

- **List every uncovered AC-id verbatim, individually.** A summary sentence
  with no ids (`"all unit-layer; deferred to downstream phases"`) does NOT
  satisfy this step — the gate scans for the literal id text, not the
  sentence's intent.
- The "which tool verifies this" clause must name a real, already-declared
  mechanism (e.g. "D5 docstring-coverage scanner", "`pip-licenses` SBOM
  check (NFR-07)", "`mutmut run` mutation score (NFR-08)", "`lint-imports`
  layer contract (NFR-06)") — never invent a placeholder tool, and never
  fabricate a TEST_SPEC test-function row just to force a citation; a
  disposition line is not a test case and must not be dressed as one.
- This is a citation-completeness step, not a new test-authoring
  obligation — it costs one line per NFR, not a Q1-Q8 pass.

---

## Step 2: Per-FR Derivation (8-Question Protocol)

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

**NAMING RULE (v2.6.1)** — TEST_INVENTORY.yaml is naming authority ONLY, not a coverage exemption:
1. If YAML supplies a name for a category Q1-Q8 would generate, **use that exact name**.
2. YAML names **do not satisfy** the `failure` / `boundary` / `negative` / `integration` / `state_transition` / `fault_injection` / `nfr_pattern` requirements for that FR.
3. **You MUST still execute the entire Q1-Q8 protocol** for every FR, generating any missing test cases for the categories the YAML did not cover.
4. Do NOT stop after applying the YAML name — re-running the protocol is the only way YAML-vs-derivation gaps surface.
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

**Step-1b-forced patterns may NOT be skipped.** If this FR is implemented by a
module with an architecture-risk trait, its forced cases (concurrent-load /
subprocess-timeout / backoff-bounded / cache-reachability) are mandatory and
their integration variants go in the `tests/integration/` section.

Type: `nfr_pattern`  
Derivation: `Q6/NP-{ID}` (SRS-triggered) or `Q6/1b/NP-{ID}` (SAD-triggered)

### Q7: CROSS-FR INTEGRATION (conditional — applies when FR-XX interacts with other FRs)

> Does FR-XX take output from another FR as input, or does its output feed into
> another FR? Name the specific FRs.

For each interaction pair (FR-XX ↔ FR-YY):
- `test_fr{xx}_output_feeds_fr{yy}_correctly`
- Or more descriptively: `test_{fr_xx_module}_with_{fr_yy_module}_pipeline`

Type: `integration`  
Derivation: `Q7/FR-{YY}`

### Q8: NEGATIVE CONSTRAINTS / EXCLUSIONS

> Does the specification explicitly forbid a behavior or contain negative constraints (e.g., "Must not", "Do not", "禁止", "不可", "避免")?

If YES: for each negative constraint C:
- Generate a failure injection or boundary test asserting this exact exclusion: `test_{fr}_must_not_{C_slug}`
- Type: `negative_constraint`
- Derivation: `Q8`

**C_slug rule** (mandatory — Python identifier must be ASCII): `C_slug = re.sub(r'[^a-z0-9_]+', '_', C.lower()).strip('_')`.
- CJK only (`禁止快取`): fall back to a sequential per-FR suffix: `c1`, `c2`, ... and document the mapping in `TEST_SPEC.md` FR entry's "Sub-assertions" table (`rule_id: q8_禁止快_cache_disabled`).
- Mixed: ASCII portion kept, CJK portion gets the `c{n}` suffix.
- Examples: `"cache GET"` → `cache_get`; `"禁止快取"` → `c1`; `"Must not log PII"` → `must_not_log_pii`.

---

## Step 2.5: Public Interface Derivation (MANDATORY)

Do not rely solely on FR-XX tags. Scan the specification for any explicitly defined Public Interfaces / Contracts (e.g., HTTP Endpoints, CLI Commands, GraphQL Mutations, Public SDK Methods).
For EVERY interface listed:
- Generate an explicit contract test (e.g., `test_api_get_health_returns_200` or `test_cli_generate_command_success`).
- Type: `interface_contract`
- Derivation: `Step 2.5`

---

## Step 3: Write TEST_SPEC.md Entry

For each FR, write an entry in the following format:

```markdown
### FR-XX: {one-line description from SRS}

**Classification**: {type(s) from pre-step}  
**Active Patterns**: {NP-XX, NP-YY, ...} (or "none")

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_frXX_{behavior}` | x="colour"; expected="color" | happy_path | Q1 |
| 2 | `test_frXX_{error_condition}` | x="" | validation | Q2 |
| 3 | `test_frXX_{field}_boundary` | x="lorem-ipsum"; expected="lorem ipsum" | boundary | Q3 |
| 4 | `test_frXX_unauthenticated_returns_401` | token="" | nfr_pattern | Q6/NP-01 |
| 5 | `test_frXX_with_frYY_pipeline` | x="…" | integration | Q7/FR-YY |

**Sub-assertions** (predicate over a case's Inputs / the production `result`):

| rule_id | predicate | applies_to |
|---|---|---|
| {rule-id} | `<python bool expression over Inputs/result>` | 3 |
```

**Inputs column** (mandatory): concrete declared values as `key="value"`,
semicolon-separated, in the TRUE form — `expected="lorem ipsum"` with a real
space, never the pytest-id form `lorem_ipsum`. A descriptive id is NOT a
substitute for a value: a case like `all_boundary_chars` with no `text_input="…"`
lets P3 invent the input and mis-count it.

**Sub-assertions table**: list a case under `applies_to` only if the predicate
is genuinely true for that case's Inputs. The P2 gate
`check-test-spec-consistency` evaluates every predicate against every
`applies_to` case and FAILS on a contradiction (e.g. `" " in "color"` is False,
so case 1 must NOT be in a `" " in expected` group; `len(result)==4` with a
5-char input is unsatisfiable). Correctness is locked here, in P2.

**Naming convention**:
- Prefer: `test_{module}_{behavior}` using actual module names from SAD.md
- Acceptable: `test_fr{nn}_{behavior}` when module not yet named
- No generic names: `test_it_works`, `test_basic`, `test_case_1` → rejected by Agent B

---

## Step 4: Cross-FR Section

After all individual FRs, add a cross-cutting section:

```markdown
## Cross-Cutting Test Cases

### Infrastructure & Middleware Integration (MANDATORY if applicable)
If the specification includes ANY Infrastructure Components, Caches, Databases, Message Queues, or Middleware:
- You MUST generate a cross-cutting E2E integration test starting from the main System Entrypoint (e.g., API Router, CLI Main), asserting that the component is physically wired and invoked.
- Isolated unit tests are insufficient for these components.

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
| By type: negative_constraint | N |
| By type: interface_contract | N |
| Active NFR patterns applied | NP-XX, ... |
```

---

## Agent B Validation Checklist

Agent B must verify before APPROVE. If any of the following are true, you MUST REJECT:

- [ ] **YAML Exemption Check**: Did Agent A skip generating `failure`/`boundary` tests for an FR just because it had a YAML name? (If yes -> REJECT)
- [ ] **Interface Completeness**: Are there Public Interfaces (Endpoints, CLI commands) listed in the spec that are MISSING from the test catalog? (If yes -> REJECT)
  - **Interface ↔ NFR cross-check** (mandatory): for every Interface listed, verify the Active Pattern Set from Step 1 (especially NP-01 auth, NP-02 authz, NP-04 validation, NP-12 pagination) is applied to that interface. A `POST /users` endpoint with `authentication` active in NFRs MUST have an `NP-01 unauthenticated_returns_401` test. (If missing → REJECT)
- [ ] **Negative Constraint Check**: Are there explicit "Must not" or "禁止" constraints in the spec that DO NOT have a corresponding `negative_constraint` (Q8) test? (If yes -> REJECT)
- [ ] **Wiring Check**: Is there an Infrastructure component (e.g., DB, Cache) in the spec that lacks an Entrypoint-to-Infrastructure E2E test in the Cross-Cutting section? (If yes -> REJECT)

Standard Verification:
- [ ] Every FR from SRS §2 has an entry in TEST_SPEC.md
- [ ] Every FR has at least 1 `happy_path` + 1 `failure`/`validation` test
- [ ] Every active NFR pattern (from Step 1) appears in at least one FR entry
- [ ] **Architecture-risk coverage (Step 1b)**: every SAD module with a risk
      trait (shared mutable state / external process / network retry / cache)
      has its forced cases in TEST_SPEC, with `SAD: <module>` trigger recorded
      and integration variants under `tests/integration/`. A risky module with
      zero forced cases → REJECT
- [ ] **Threat coverage (Step 1c)**: every SAD §6 threat (when
      `applicability: full`) has its `verified_by` test as a row in the
      owning FR's TEST_SPEC entry, with `SEC: <threat-id>` trigger recorded.
      A threat whose forced NP pattern has zero corresponding row → REJECT
- [ ] **NFR AC-id citation completeness (Step 1d)**: grep TEST_SPEC.md's
      full text for every `AC-Nx.y` identifier SRS.md declares under an
      NFR heading. Every one must appear — either inside a Step-1/1b/1c
      derived test case, or inside a `Deferred: AC-Nx.y — ...` line.
      A declared NFR AC-id that appears in NEITHER shape → REJECT. A
      `Deferred` line with no AC-id, or with a subset of the NFR's ids
      silently missing the rest → REJECT (Step 1d requires every id,
      individually).
- [ ] No test function names are generic (`test_basic`, `test_case_1`, etc.)
- [ ] All derivation fields are non-empty and cite a Q-number or NP-number
- [ ] Module names in test functions match SAD.md module/class names where available
- [ ] Cross-cutting section is present with at least 1 deployment smoke test
- [ ] Test names from TEST_INVENTORY.yaml are preserved as-is (not re-derived)

**If any item fails: return REJECT with specific FR numbers and missing items.**

---

## Anti-Bias Rules

1. Derive tests from FR text + NFRs — never from implementation details
2. Do not skip Q7 just because "it seems obvious" — explicitly state "no interaction" if so
3. If a boundary is implicit (not stated numerically), note it as `ASSUMED: {value}` in derivation
4. State machine tests: enumerate ALL valid and invalid transitions, not just the happy ones
5. NFR patterns: if NP-XX is active but you skip it for a specific FR, state the reason inline

*Protocol version: v1.1 | Updated in harness-methodology v2.6.0*
