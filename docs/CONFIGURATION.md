# Configuration Reference

**The one home for tunables is `<project>/.methodology/harness_config.json`.**
Every default equals the behavior the framework ships with — an absent or
empty config changes nothing. Unknown keys and out-of-range values print a
one-line WARN and fall back to the default (never crash, never silently
no-op). `tests/test_configuration_doc.py` fails the build if a registry key
is added without a row here, or an env var is read without appearing in the
table below.

The values in the example below are illustrative **overrides**, not the
defaults — every default is in the tables that follow, and
`tests/test_configuration_doc.py` compares those tables against the registry
that decides them.

```json
{
  "version": 1,
  "features": {
    "mutation_testing": false,
    "cross_artifact_live_cov": false
  },
  "values": {
    "drift_threshold": 85.0,
    "timeouts": {"mutation": 7200},
    "step_max_turns": {"GATE1": 90}
  },
  "crg_cohesion_healthy": 0.2,
  "crg_excludes": [".claude/*", "*.mjs"]
}
```

## `features` — boolean switches

| Key | Default | Effect |
|---|---|---|
| `mutation_testing` | `true` | Enables the mutation_testing gate dimension (mutmut / Stryker). Default flipped from `false` by 47ec3fd; set it `false` to opt out, and the harness drops the dimension and re-normalises the composite score. |
| `phase4_llm_review` | `true` | Enables the adversarial_review dimension (Phase 4 LLM bug hunt). |
| `crg_architecture` | `true` | Enables the CRG-backed architecture dimension. |
| `cross_artifact_live_cov` | `false` | finalize-gate cross-artifact check re-runs live `pytest --cov` (up to ~120s) instead of reusing `.coverage` data. The `HARNESS_CROSS_ARTIFACT_COV` env var overrides this per invocation. |
| `security_design` | **`true`** | Enables `core.quality_gate.security_design`'s structural completeness check of SAD.md §6 (STRIDE-lite threat model — see §6 below). **The one deliberate exception to "default = pre-existing behavior"** (Round 10 gap-analysis response). This flag is a mechanism kill-switch for emergency rollback, not a per-project opt-out — a project with no real attack surface should declare `applicability: none` + a justification *inside* the SEC block instead of disabling the flag, keeping the structural discipline (an honest, reviewed statement) rather than silently opting out. |

Dimension flags are mapped by `core/harness_config.py::_DIM_TO_FEATURE` and
consumed via `is_dim_disabled()`. `security_design` is NOT a gate dimension
(no `_DIM_TO_FEATURE` entry) — it is a decidable preflight/CLI structural
check, not a scored dimension (see §6).

## `values` — tunable parameters

Precedence everywhere: **per-FR `fr_config` (quality_manifest.json) >
explicit CLI flag > `values` > built-in default.** (`fr_config` outranking
an explicit CLI flag is long-standing run-fr-step behavior, locked by
`tests/test_fr_cmds_values_wiring.py`.)

| Key | Default | Consumed by |
|---|---|---|
| `drift_threshold` | `85.0` | M2 drift ensemble score floor (0–100]; every production `PhaseHooks` construction (pre-commit-check, run-phase, finalize-gate, adapter). |
| `max_fix_rounds` | `3` | run-fr-step CODE-FIX/GATE1 retry budget. |
| `permission_mode` | `"bypassPermissions"` | permission mode for spawned sub-agents (every FR step). |
| `timeouts` | `{}` | per-key overlay onto `STALL_TIMEOUTS` (`subprocess`, `env_check`, `task_default`, `task_dev`, `fr_step`, `mutation`, `state_alert_min`, `gitleaks`); unknown keys WARN and are ignored. |
| `step_max_turns` | `{}` | per-step overlay onto run-fr-step's `_STEP_MAX_TURNS` (`TDD-RED`, `TDD-GREEN`, `TDD-IMPROVE`, `GATE1`, `GATE1-DELTA`, `CODE-FIX`, `TEST-FIX`, `INFRA-FIX`, `LINT-FIX`, `COVERAGE-FIX`); unknown steps WARN. |
| `phase_truth_threshold` | `90.0` | HR-11 Phase Truth score floor (migrated from enforcement.json `hr_overrides.HR-11_phase_truth_threshold`, which still works as a legacy fallback with a migration nudge). |
| `phase_truth_pytest_timeout` | `300` | Phase Truth pytest cap in seconds, floor 30 (migrated from enforcement.json `phase_truth.pytest_timeout_seconds`, same legacy fallback). |
| `checker_enforcement` | `{}` | Round 12 站3c per-checker enforcement overlay, e.g. `{"spec_unsatisfiable": "block"}`; values `block`/`warn`. Graduation policy: checkers consulting `get_checker_enforcement` ship at `warn` and are promoted to `block` only after one E2E run with zero false kills (the R5 unsatisfiable-tightening incident is why). Existing hard-coded-block checkers do not consult this overlay — it cannot weaken them. Current participants: `spec_unsatisfiable` (check-test-mirrors-spec). |

## Top-level CRG calibration

| Key | Default | Effect |
|---|---|---|
| `crg_cohesion_healthy` | unset (scorer default 0.3) | per-project cohesion floor for a healthy community, float in (0, 1]. |
| `crg_excludes` | `[]` | fnmatch globs; majority-matched communities are excluded from architecture scoring. |

## Security Design (SAD.md §6 — threat-model-as-code)

Gated by `features.security_design` (default `true`). `core.quality_gate.
security_design.check_security_design()` decidably validates a `<!-- SEC:
START/END -->` YAML block in SAD.md §6 — no keyword-density scoring (that
approach was proven to false-positive-fail honest tool-type projects; see
Bug #35 and `ConstitutionProfile`'s P1/P3/P4 security-dimension removal).
Canonical template: `core.quality_gate.security_design.
render_canonical_security_template()` — never hand-write the YAML.

`applicability: none` + a `justification` (≥20 chars) is a fully valid,
honest declaration for projects with no real attack surface — it passes
every rule from R4 onward. `applicability: full` requires ≥1
`trust_boundaries` entry and ≥1 `threats` entry per boundary; each threat
declares a STRIDE `category`, `owner_module` (must be declared in the SAD
§5 SAB block), an optional `nfr` (must exist in SRS.md), and a
`verified_by` test name. Every SAB NFR typed `security` must be referenced
by ≥1 threat's `nfr`. From Phase 5, every `verified_by` name must exist in
the test suite (structural existence, not content evaluation). Threats
also seed `bug-hunt-targets`' `threat_model` source and force NP test
patterns in `derive_test_cases.md` regardless of SRS keywords (Step 1c).

## Other per-project configuration (different files, on purpose)

- **`state.json`** `language` / `test_runner`: operational identity set once
  by `init-project` — lives with the FSM state, not hand-tuned.
- **`quality_manifest.json` `fr_config`**: per-FR overrides
  (`timeout`, `max_fix_rounds`, `code_fix_max_turns`) — scoped to one FR,
  reviewed with the manifest; global defaults for the same knobs live in
  `values`.
- **`enforcement.json`**: mostly legacy. Its `hr_overrides`/`phase_truth`
  keys migrated to `values.phase_truth_*` (old location still honored as a
  fallback); the `constitution` key remains a live override layer for the
  on-demand constitution profile (`core/quality_gate/constitution/profile.py::load_profile`).
  doctor WARNs on any other key in the file.

## Environment variables

Per-invocation switches stay env vars (they differ run-to-run); persistent
per-project policy belongs in `values`/`features`. Every env var the
framework reads is registered here — `tests/test_configuration_doc.py`
scans the source and fails on unregistered additions.

| Variable | Kind | Effect |
|---|---|---|
| `HARNESS_NO_GIT` | harness | Skip git commit/push side effects for this invocation (advance-phase, FR steps, git strategy). |
| `HARNESS_CROSS_ARTIFACT_COV` | harness | `"1"` forces live `pytest --cov` in the cross-artifact check for this invocation; any other set value forces it off; unset defers to `features.cross_artifact_live_cov`. |
| `CRG_METRICS_PATH` | harness | Override the CRG metrics JSON path read by the SSI scorer. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | external | Standard OpenTelemetry exporter endpoint (observability). |
| `OTEL_EXPORTER` | external | OpenTelemetry exporter selector (observability). |
| `VIRTUAL_ENV` | system | Read to locate the active interpreter's tooling during env checks. |
| `USER` | system | Default operator id for kill-switch audit logging. |
| `CI` | system | When set, `run-phase` skips the spawn-substrate preflight probe (Round 29) — CI never dispatches an interactive per-FR loop, so there is no sub-agent substrate to validate, and the probe (which requires the `claude` CLI) can only ever fail there. |
| `GITHUB_ACTIONS` | system | Same effect as `CI` above — GitHub Actions sets this even when the generic `CI` var is absent. |
| `METHODOLOGY_CONSTITUTION_PROFILE` | harness | JSON string overriding the on-demand constitution profile (read via a variable name in `constitution/profile.py`, so the AST literal scan can't see it — registered by hand; the scanner's known limit). |

## Deliberately NOT configurable (anti-backdoor)

Values that guard gate integrity must not become knobs — a configurable
floor is a backdoor. This list is policy, enforced by review:

- Gate 1 per-FR coverage floor (100% of the FR's owned source).
- Milestone entry-gate evidence requirements (`_MILESTONE_ENTRY_GATES`).
- Ghost-detection / dispatch diff-budget heuristics (`agent_spawner`).
- Deterministic-failure signature registry (`_STRUCTURAL_FAILURE_SIGNATURES`).
- FR-token regex forms and the foreign-project token registry (parity-locked).
- ScoringProfile dimension keywords (framework calibration, not user policy).
- Workflow JS driver constants (driver layer owns its own pacing; pass args).
