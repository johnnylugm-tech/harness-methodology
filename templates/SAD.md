# Software Architecture Document (SAD) — {Project Name}

<!-- harness:template-stub -->
<!-- Remove the sentinel line above once you start filling this SAD.
     While present, harness load-context emits a stub warning. -->

> On-demand Lazy Load template.

## 1. Architecture Overview
{High-level architecture description}

### 1.1 System Verification Target
> **Every exit gate (2, 3 and 4)**: the harness executes `make verify-system`. A
> non-zero exit fails the gate. The target name is fixed — the harness always calls
> `make verify-system`.
>
> This is the only check in the whole framework that runs the delivered system.
> Everything else reads your source text or runs your test suite, both of which
> your test doubles configure. Two rules follow, and the gate enforces both:
>
> 1. **At least one step must invoke the delivered entry point** — the program a
>    user would run (`python -m <your_package> …`, your console script, your
>    service). A target that chains `test lint coverage` re-runs dimensions the
>    gate has already scored and verifies nothing further.
> 2. **The step that does so must be able to fail.** `|| true`, a leading `-`,
>    and tool flags like `ruff --exit-zero` all keep a failure out of make's exit
>    code, which is the only thing the gate reads.
>
> Aim for a step that exercises a real acceptance criterion against real
> dependencies — a temporary database, a real file, the actual process — because
> the gate also measures which of your high-risk modules this target executed.
> Any module your test suite replaces with an `autouse` stand-in has to run for
> real here.
**Makefile target**: `verify-system`
**Exercises**: {which high-risk modules / acceptance criteria this target executes}

## 2. Module Design

### 2.1 Directory Structure Design Principles

> **CRG architecture score** (Phase 3+): the share of product communities —
> clusters the Code Review Graph finds in the call graph — that stay within
> 50 nodes. A community over 50 is a god cluster: one region of code doing too
> much. Cohesion is also computed and listed in the report, but it is **not
> scored** (Round 113): CRG counts every call into a library as an external
> edge, so the number measures how much a module calls its libraries, not how
> it is designed. Do not add calls between files to move it — coupling added
> for a metric is a cost the code then carries.

**Principle 1 — Use subdirectories to bound community size.** Explicit
subdirectories (`src/api/`, `src/service/`, `src/repository/`) give the graph
module boundaries to cluster along; a flat `src/` with 10+ files lets one
cluster grow past the cap. One responsibility per directory.

**Principle 2 — Size cap: a community stays within 50 nodes.** A node is
roughly one function or class. A directory that would hold more (about 4-6
modules of 8-12 functions each) is split along its responsibilities.

| Quick reference | check |
|----------------|-------|
| Each directory has one responsibility? | Yes |
| No flat directory of 10+ source files? | Yes |
| Every community ≤ 50 nodes? | Yes |

**Anti-patterns:**

```
❌ src/{main,models,cli,audio,storage,service,api,util,...}.py
   → one flat directory; its community grows past the cap
❌ src/service/engine.py with 60+ functions
   → one module is a god cluster on its own
✅ src/api/, src/service/, src/repository/ → bounded, one responsibility each
```

### 2.2 {Module Name}

| Attribute | Value |
|-----------|-------|
| Responsibility | {responsibility} |
| External Interface | {API} |
| Dependencies | {dependency modules} |

#### Logical Constraints
- {constraint 1}
- {constraint 2}

## 3. Error Handling
| Level | Handling Strategy |
|-------|------------------|
| Level 1 | Immediate return |
| Level 2 | Retry 3 times |
| Level 3 | Graceful degradation |

## 4. Technology Choices
| Technology | Rationale |
|------------|----------|
| {technology} | {reason} |

---

## 5. SAB Block (machine-readable — BINDING CONTRACT)

> **CONTRACT**: Field names, types, `sab:` root key, and `phase` as int must
> match `core/quality_gate/sab_parser.py:render_canonical_sab_template()`.
> Do NOT hand-write the YAML — paste from the canonical template and replace
> EXAMPLE values with your project's real values.
> Validate before committing: `python3 scripts/generate_sab.py --validate --project .`

<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  created_at: "{YYYY-MM-DD}"
  phase: 2  # MUST be int, NOT a string — parser raises on 'phase: "2"'
  project: "{project_name}"

  layers:  # EXAMPLE — replace with your project's layers
    - name: api
      modules:
        - name: "app.api.webhooks"
          implemented_in: "app.main"  # OPTIONAL — Use if consolidated into another file
      allowed_dependencies: ["service"]
    - name: service
      modules: ["app.service.handlers"]
      allowed_dependencies: []

  allowed_dependencies:
    - from: api
      to: service

  quality_targets:
    max_complexity: 15  # EXAMPLE — replace
    min_coverage: 80  # EXAMPLE — replace
    max_coupling: 0.3  # EXAMPLE — replace

  nfr_dimension_mapping: {}  # OPTIONAL — auto-derived from nfr_traceability.type

  nfr_traceability:
    NFR-01:
      # type MUST be one of 14 legal values listed below:
      # Enforceable (mapped to gate dim):
      #   documentation, integration, layering, licensing, maintainability, mutation, performance, reliability, security, testability, verifiability
      # Advisory (no scoring tool, auto-added to advisory_only):
      #   deployability, scalability, usability
      type: performance
      # dimension: OPTIONAL and PREFERRED — the gate dimension this NFR
      #   is scored by, copied verbatim from SPEC.md's own `dimension:`
      #   for this NFR. Outranks the type guess above. `none` = no
      #   automated scorer. A name no gate scores is REFUSED (the error
      #   lists the legal names), never silently dropped.
      target: "p95 < 200ms"  # use ">=N" or "≥N" to raise the gate floor
      module: app.processing.pipeline

  advisory_only: []  # AUTO-FILLED by parser — omit or leave []

  gate_score_overrides: {}  # AUTO-DERIVED by parser — omit or leave {}

  fr_module_traceability:  # EXAMPLE — one entry per FR
    # If an FR owns MULTIPLE modules, use a YAML list instead of a single
    # string, e.g. FR-02: ["app.a", "app.b"] — both forms are supported.
    FR-01: "app.api.webhooks"

  architecture_constraints: []
  # For deterministic parity use mappings with: id, executor:
  # import-linter, contract_type, optional contract_name,
  # source_modules, and forbidden_modules for forbidden contracts.
  # Legacy free-form strings remain advisory/backward-compatible.
  decision_issues: []
  # Register every SRS FR-XX-deferred/NFR-XX-deferred id here.
  # Each row: {id, status: open|resolved, blocks_phase,
  # resolution_ref}. A resolved ref is path:line, and that line
  # names the id as resolved: `FR-01-deferred: resolved — <decision>`.

  high_risk_modules:
    - "app.api.webhooks"

  required_artifacts:  # repo-relative paths + explicit lifecycle deadline
    # Checked against the delivered tree at every gate. A path that
    # is absent, or that ships somewhere other than where it is
    # declared, blocks and the message says which. Omit or leave []
    # if the spec names no mandatory files.
    - {path: ".env.example", required_by_phase: 3}
```
<!-- SAB:END -->

Note: Fill in the YAML above — it is used for Drift Detection and gate scoring.
Generate: `python3 scripts/generate_sab.py --project . [--overwrite]`

---

## 6. Security Design (STRIDE-lite — machine-readable, BINDING CONTRACT)

> **CONTRACT**: Field names and the `security_design:` root key are parsed
> by `core/quality_gate/security_design.py:extract_security_block()`.
> Do NOT hand-write the YAML — paste from the canonical template and
> replace EXAMPLE values with your project's real values.
> Validate: `python3 harness_cli.py check-artifact-consistency --project .`
>
> `applicability: none` is a fully valid, honest declaration for a project
> with no real attack surface (e.g. a pure CLI formatting tool) — it
> requires a `justification` (>=20 chars) and skips the rest of this
> block. This is a decidable structural check, not a keyword scorer: an
> honest `none` always passes.

<!-- SEC:START -->
```yaml
security_design:
  version: "1.0"
  applicability: full   # full | none — none REQUIRES justification and skips the rest
  justification: ""     # required (>=20 chars) when applicability: none
  trust_boundaries:     # EXAMPLE — replace with your project's real boundaries
    - id: TB-01
      name: "external HTTP input"
      description: "requests crossing from unauthenticated clients into the API layer"
  threats:              # STRIDE-lite — every boundary needs >=1 threat
    - id: T-01
      boundary: TB-01
      category: tampering   # spoofing|tampering|repudiation|information_disclosure|denial_of_service|elevation_of_privilege
      description: "malformed payload mutates task state without validation"
      mitigation: "schema validation + reject on unknown fields"
      owner_module: "app.api.webhooks"   # MUST be a module declared in the SAB block (§5)
      nfr: NFR-02                        # optional — MUST exist in SRS when present
      verified_by: "test_sec_t01_malformed_payload_rejected"   # single test name only — NOT "test_a, test_b"; split multi-test threats into separate T-NN entries
```
<!-- SEC:END -->

Note: `owner_module` must name a module declared in the §5 SAB block;
`nfr` (optional) must exist in SRS.md; `verified_by` names the test that
proves the mitigation — from Phase 5 onward, `check-artifact-consistency`
blocks if that test doesn't exist yet. Threats also seed
`bug-hunt-targets`' adversarial-review targeting and force NFR-pattern
test cases in `derive_test_cases.md` Step 1c regardless of SRS keywords.
