# Observability — run-artifact map (Round 14)

A Gap Analysis Report submitted this round argued harness-methodology
lacked observability: no structured logging, no trace propagation, no
cost/token/latency metrics. Verified claim by claim before acting (see
Round 14's plan): the diagnoses were mostly accurate but the prescriptions
were mismatched — this is a local CLI where stdout is a strict
agent-facing protocol surface, not a backend service, so a global
structured-JSON-logger migration or OTEL traceparent propagation across
subprocess boundaries would cost real breakage for zero additional
consumers. What was actually missing was narrower: two cost/token fields
sitting unused in an envelope this code already parses, and a reader for
data several artifacts already collect. This doc maps what exists, what
reads it today, and where the deliberate gaps still are.

## The four run artifacts

### `.methodology/sessions_spawn.log`

One JSON object per line, written by `core/sessions_spawn_logger.py`'s
`SessionsSpawnLogger.log_spawn()`/`log_update()` on every sub-agent
dispatch. Existing consumers: `core/quality_gate/gate1_evidence.py`,
`core/quality_gate/phase_truth_verifier.py`, cross-artifact consistency
checks — all read via `.get()`, so new fields are additive and safe.

| Field | Type | Present when |
|---|---|---|
| `timestamp` | str (ISO) | always |
| `role`, `task`, `session_id`, `status` | str | always |
| `confidence` | int | when passed |
| `phase` | int | when passed by the caller |
| `fr_id` | str \| null | when passed by the caller |
| `regression_flags` | dict | when passed by the caller |
| `error_output` | str (~500 char cap) | `status` is `ERROR`/`TIMEOUT`/`REGRESSION_GUARD` |
| `exit_code` | int \| null | non-zero subprocess exit |
| `total_cost_usd` | float | **Round 14 站0** — lifted from the `claude -p --output-format json` envelope; absent on TIMEOUT/non-zero-exit/non-JSON-stdout entries and on every log line written before this station |
| `num_turns` | int | same presence rule as `total_cost_usd` |
| `duration_api_ms` | int | same presence rule (distinct from the pre-existing `duration_seconds`, which is measured locally via `time.monotonic()` and was already present) |
| `usage` | dict (`input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`) | same presence rule |

Envelope fields were confirmed live against an installed `claude` build
before writing the capture code, not assumed from training knowledge —
`duration_ms` was deliberately NOT captured (duplicates the pre-existing
`duration_seconds`), and `server_tool_use`/`modelUsage`/etc. are out of
scope (no current consumer).

### `.sessi-work/degradations.jsonl`

One JSON object per line, written by
`core/degradation_ledger.record_degradation(project, component, what, why)`
(Round 13 站1). Fields: `ts` (float, `time.time()`), `component`, `what`,
`why`. Also prints one `[DEGRADED] component: what (why)` line to stderr
the first time a given `(component, what)` pair fires in a process — see
`docs/ERROR_HANDLING.md`'s DEGRADE row. `core/state_io.py`'s
`lenient=True` path (Round 14 站2) is the newest writer: a corrupt
`state.json`/`quality_manifest.json` degrading to `{}` now lands here
instead of vanishing into a silent `except: pass`.

### `.sessi-work/crash/crash_<timestamp>_<pid>.json`

Written by `core/errors.write_crash_bundle()` when `harness_cli.py`'s
`_dispatch()` crash boundary catches an uncaught exception (Round 13
站0 — see `docs/ERROR_HANDLING.md`). Fields: `timestamp`, `exc_type`,
`exc_message`, `traceback` (full), `argv`, `cwd`, `project`,
`harness_git_sha`, `python_version`, `repro_command`,
`maintenance_prompt`. A `.triaged` sidecar (same stem, see
`cli.cr_cmds.triaged_marker` — public since Round 14 站1 specifically so
`cli/report_cmds.py` could reuse the naming rule instead of duplicating
it) marks a bundle as filed; `harness_cli.py crash-triage` groups bundles
by signature (deepest traceback frame + exception class) and
`--open-cr` files one CR-BUG per unfiled signature.

### `.harness/traces/agent_trajectory.jsonl`

OTEL spans, one JSON object per line, written by
`core/observability.py`'s `JsonFileSpanExporter` (the default exporter —
see that module's `init_tracer()` for the OTLP/console alternatives,
selected via `OTEL_EXPORTER_OTLP_ENDPOINT`/`OTEL_EXPORTER` env vars).
Schema per line: `name`, `context.{trace_id, span_id}`, `start_time`,
`end_time` (both nanosecond-epoch ints — confirmed against the installed
`opentelemetry-sdk`), `attributes`, `events`. Five span names currently
emitted: `run_phase` (`cli/phase_cmds.py`), `run_gate` and `finalize_gate`
(`cli/gate_cmds.py`), `phase_{N}_preflight` and `phase_{N}_postflight`
(`core/phase_hooks.py`).

**Before Round 14, this file had zero consumers** — spans were written
and nothing ever read them back. `run-report`'s trajectory section (below)
is the first reader. Trace propagation across the `claude -p` subprocess
boundary (a `traceparent` env var / header into the spawned agent) was
evaluated and deliberately NOT built: it would instrument a pipe with
still no destination to correlate the child span against, for a real
engineering cost (subprocess env plumbing, agent-side SDK support). If a
genuine cross-process correlation need shows up, build the propagation
then, against that concrete requirement — not speculatively now.

## `run-report` — the reader

```
python3 harness_cli.py run-report --project <path> [--json]
```

Read-only; writes nothing. Aggregates all four artifacts above, each
independently optional — a missing or empty artifact reports
`"available": false` for that section rather than crashing or padding
output with null-filled noise. `--project` pointing at a nonexistent path
prints a `[WARN]` (not a `[BLOCKED]`) and shows an empty report: every
section already degrades to n/a on a missing artifact, and a nonexistent
project root is just the limiting case of the same rule, not a distinct
failure class (exit 0 either way — see `cli/report_cmds.py`'s
`cmd_run_report` docstring for the full reasoning).

What it reports, per section:
- **Spawn dispatches**: total count, status distribution, `error_class`
  distribution, failure rate, dispatches-per-FR (top 10, with per-FR cost
  and input/output token sums), total cost, total tokens, average
  wall-clock and API duration. Cost/token sums report their denominator
  (`cost_entries_with_data` / `cost_entries_total`) alongside the total so
  a log mixing pre- and post-station-0 entries never silently reads as
  zero cost.
- **Degradations**: total count, grouped by `(component, what)`.
- **Crash bundles**: total count, untriaged count (prompts
  `crash-triage --open-cr` when non-zero).
- **Agent trajectory**: total span count, wall-clock time summed per span
  name (answers "which Gate/phase step is slowest" directly from data
  already being collected, reading at most the last 50,000 lines to bound
  memory on a long-lived project).

### Reproducing the Round 12 convergence metrics

Round 12's audit computed "dispatches per FR went from ~140 to ~10" by
hand, from a manual read of `sessions_spawn.log`. The same number is now
one command: `run-report --json` and read
`.spawn_log.dispatches_per_fr_top10`. Any future round auditing dispatch
volume, cost, or per-Gate wall-time should start from this command's
output, not a fresh manual pass over the raw log.

## Failure modes (MAST-aligned)

Round 16 station2 added `core/failure_modes.py`, a deterministic classifier
that reclassifies `sessions_spawn.log` entries against the 3-category shape
from ["Why Do Multi-Agent LLM Systems Fail?"](https://arxiv.org/abs/2503.13657)
(MAST, arXiv:2503.13657, NeurIPS 2025 spotlight — 14 failure modes across
specification / inter-agent misalignment / task verification), plus a 4th
INFRA bucket of our own for environment/network/model failures MAST doesn't
cover (it studies MAS reasoning failures, not infrastructure outages). This
replaces the previous 3-crude-bucket view (`error_class` alone:
`STRUCTURAL`/`INFRA_ERROR`/`EXECUTION_ERROR`) with named, individually-tested
modes.

Signal → mode → MAST category map (station-2a reconnaissance found real,
grounded signal for these six only — see `core/failure_modes.py`'s module
docstring and this station's commit message for the field-by-field trace):

| Signal (spawn-log entry field) | mode_id | MAST category |
|---|---|---|
| `regression_flags` non-empty (destructive edit / XX-mutator-marker) | `destructive_edit_or_mutator_marker` | specification |
| `inner_status` in `{AWAITING_CONFIRMATION, NOTHING_TO_DO}` | `semantic_noop_termination` | specification |
| `output` starts with `"Commit-required step"` | `commit_required_step_no_commit` | specification |
| `error_class == "STRUCTURAL"` | `structural_env_breakage` | infra |
| `error_class == "INFRA_ERROR"` | `infra_error_transient` | infra |
| `status == "TIMEOUT"` | `dispatch_timeout` | infra |
| (no rule matches) | `UNCLASSIFIED` | — |

**Honest gap, not an oversight**: `inter_agent` and `verification` currently
have zero rules. B-review's `escalation_action`
(`approve`/`retry`/`escalate_human` — `core/review_schema_validator.py`) is
never persisted onto a spawn-log entry, and gate PASS/FAIL verdicts live in
per-gate result files, not per-dispatch records — there is no artifact today
to classify against for either category. `tests/test_failure_modes.py`'s
`test_inter_agent_and_verification_have_no_rules_yet` pins this fact so a
future round that adds such a rule does so deliberately. **Re-open
condition**: once escalation outcomes or gate verdicts are persisted
per-dispatch (a logging change, not a classifier change), extend
`FAILURE_MODE_RULES` with the corresponding rules.

`UNCLASSIFIED` is a floor, not a bug: any entry matching no rule keeps its
original `error_class`/`status` for human triage rather than being force-fit
into the nearest bucket or silently dropped. `core.failure_modes.summarize()`
reports `unclassified_pct` alongside the mode/category counts — a high
percentage means the ruleset's coverage is thin for that dataset, not that
the classifier is malfunctioning.

The MAST paper's own reported category split (specification 41.77% /
inter-agent 36.94% / verification 21.30%) is a statistic about its own study
population, not a prediction about this framework's failure distribution —
do not read this framework's future `summarize()` output against those
percentages as a target or baseline.

## What this round deliberately did not build

- A global structured (JSON) logger, or a `print`→`logging` migration.
  121 of 151 existing `[WARN]` sites print to stdout, which is the
  agent-facing protocol surface several regex-based checks parse —
  restructuring that output format for a logging-hygiene gain risks
  breaking the actual contract for no consumer benefit. See
  `docs/ERROR_HANDLING.md`'s channel note for the fuller reasoning.
- `traceparent` propagation into spawned `claude -p` subprocesses (see
  the trajectory section above).
- An OTLP collector / dashboard. No component in this local-CLI
  architecture runs as a long-lived service, so there is nothing to scrape
  a `/metrics` endpoint from and no operator watching a live dashboard —
  the JSONL-plus-`run-report` shape matches how this tool is actually
  operated (one run, then inspect the artifacts it left behind).
- Removal of the existing OTEL span-writing code — it was already
  installed, optional-guarded, and (as of this round) has its first real
  reader, so tearing it out would cost a dependency/documentation pass to
  undo something now providing value.

A second Gap Analysis Report (Round 15) re-raised the `print`-interception
and `traceparent`-propagation ideas verbatim; both were re-verified and
re-declined as repeat proposals with unmet re-open conditions — see
`docs/PROPOSAL_ADJUDICATIONS.md` for the adjudication ledger any future
report should check first.
