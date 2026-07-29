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
cross-artifact consistency checks, `run-report`, the failure-corpus export,
and `core/failure_modes.py`'s MAST classifier — all read via `.get()`, so new
fields are additive and safe.

> **Observation, not evidence** (Round 21 站3). Nothing scores this file, and
> nothing may. It is written by the agent whose work a gate judges, it is
> gitignored so neither review nor CI ever sees it, and appending a line costs
> one Bash call. `PhaseTruthVerifier` scored it at weight 0.20 until Round 21;
> taskq's Phase 6 carried six hand-written entries whose `role` and `phase`
> matched exactly what that check searched for.
>
> `core/doctor.py` runs an authenticity heuristic over the same entries — a
> completion whose `session_id` is neither empty nor a UUID **and** which
> carries none of the envelope fields below was produced by no dispatch. It
> emits a **WARN and is deliberately never scored**: whoever can forge an entry
> can forge the envelope too, so this finds forgery after the fact rather than
> preventing it. Promoting it to a gate term would rebuild the removed defect
> one layer out.
>
> When you need a signal that cannot be self-authored, use one the framework
> produces: gate sentinels, the finalize-sourced `gate_timestamps` rows
> (Round 20 站4), git history, or a tool re-run.

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
`lenient=True` path (Round 14 站2) is one writer: a corrupt
`state.json`/`quality_manifest.json` degrading to `{}` lands here instead
of vanishing into a silent `except: pass`. **Round 17 站2** added another:
`_abort_no_progress_with_self_doubt` records inescapable GATE1 fix-round
no-progress loops (`no_progress_count >= 2`) here before returning exit 2,
eliminating the blind spot where silent `return 2` previously left no trail
for `run-report`.

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
| `error_output` starts with `"Commit-required step"` | `commit_required_step_no_commit` | specification |
| `error_class == "STRUCTURAL"` | `structural_env_breakage` | infra |
| `error_class == "INFRA_ERROR"`, re-derived from `error_output` when the stamped value is the `EXECUTION_ERROR` fallback | `infra_error_transient` | infra |
| `status == "TIMEOUT"`, or `error_output` contains `error_max_turns` | `dispatch_timeout` | infra |
| (no rule matches) | `UNCLASSIFIED` | — |

> Two of those rows are corrections from Round 19 站1, and the reason they were
> wrong is worth keeping: `commit_required_step_no_commit` read `output`, and
> the log writes that text as `error_output`, so it never matched a real entry;
> `semantic_noop_termination` read `inner_status`, which `_log_dispatch` did not
> emit at all until that station. Both had been inert since Round 16 while their
> unit fixtures — written with the same mistaken field names — stayed green.

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
into the nearest bucket or silently dropped.

**Read `unclassified_failure_pct`, not `unclassified_pct`.** `summarize()`
reports both, and only the first is a defect signal. The second is computed
over every entry, and a SUCCESSFUL dispatch matches no failure rule by
construction — so it climbs when a run goes well. taskq's log read 95.6%
unclassified with 72 of its 91 entries being plain `complete`; the meaningful
figure, unexplained failures over failures, was 78.9%. (The "82.1%
UNCLASSIFIED" recorded in Round 16 is this same arithmetic and should be read
the same way.) `run-report` leads with the failure-scoped number and labels the
other as not-a-defect-signal.

### Feeding real failures back into the rules

The classifier's unit suite proves each rule has a hit fixture and a miss
fixture. Both are hand-authored by whoever wrote the rules, so that suite
cannot tell you whether real failures are covered — it is green either way.
Round 19 站1 closed that loop:

```bash
python harness_cli.py export-failure-corpus --project /path/to/project --out tests/fixtures/failure_corpus/<run-name>.jsonl
```

Read-only. It emits de-identified, de-duplicated failure **shapes** — the raw
signals a rule reads, and nothing else. No `session_id`, no prompt text, no
timestamps, no paths, and deliberately no `error_class`: that field is a verdict
stamped at dispatch time, and keeping it would make the corpus replay old
verdicts instead of exercising the current signature registry end to end.

To adopt a corpus: write the file, register it in `CORPORA` in
`tests/test_failure_corpus_coverage.py`, and run the suite.

**When the ratchet goes red**, a real failure shape matches no rule. The fix is
to add or widen a rule in `core/failure_modes.py` — not to raise
`MAX_UNCLASSIFIED`. Raise it only when a shape genuinely cannot be classified
deterministically, and justify that in the same commit (the same contract
`tests/test_file_size_ratchet.py` uses). A red ratchet after importing a new
run's corpus is the mechanism working.

That suite also enforces the structural rule that would have caught both dead
rules on day one: **every entry field a rule reads must appear in real corpus
entries**, or be registered in `FIELDS_ABSENT_FROM_CORPUS` with a reason. Its
authority is the corpus, not a hand-written list, so it cannot drift the way
the rules did.

The MAST paper's own reported category split (specification 41.77% /
inter-agent 36.94% / verification 21.30%) is a statistic about its own study
population, not a prediction about this framework's failure distribution —
do not read this framework's future `summarize()` output against those
percentages as a target or baseline.

## `.methodology/env_contract.json` — classification, once

Round 20 站1. env-check used to answer two questions with one LLM pass on every
run:

| | decided by | when |
|---|---|---|
| **classification** — does this project run without `FOO`? | the project's docs | changes when the docs change |
| **verification** — is `FOO` exported right now? | the environment | changes constantly, and is computable |

Only the first needs judgement, and asking for it repeatedly is what let the
same var be classified two different ways in two runs against identical project
state (Round 24 / `37adc43`). The classification now lives in a
version-controlled contract; readiness is computed from it every run by
`core/quality_gate/env_verify.py`'s probes. No LLM output decides `ready`.

```
.methodology/env_contract.json
  source_sha256   sha256 of the SAD + SRS + docker-compose excerpts the
                  classifier was shown — the staleness trigger
  env_vars        mandatory   -> absence blocks readiness
                  has_default -> documented default; absence is fine
                  dev_opt_in  -> test/dev opt-in; absence is the INTENDED state
  cli_tools       required tool names, probed via PATH / venv / import
  enforcer_sha    which harness commit classified (Round 19 站3)
```

`run-env-check` computes the fingerprint first. Unchanged and a contract
exists → **no sub-agent is spawned at all**. Changed, absent, or
`--force-reclassify` → classify as before, then store the classification and
discard the measurements.

**Reviewing a contract is the point.** It is a normal file in git: read the diff
when it changes, and correct a wrong classification by editing it. A mistake
that persists in a reviewable file is strictly better than one that reappears at
random — which was the previous behaviour.

**When the environment's requirements change without the documents changing**
(a new service in CI, say), the fingerprint will not move. Use
`--force-reclassify`. This is the known limit of a document-derived trigger and
is deliberate: verification still runs every time, so a var that is *classified*
correctly but *missing* is caught on the next run regardless.

## Dispatch cost — what a phase actually spends

A sub-agent dispatch is the unit of cost in a workflow run, and
`scripts/workflowgen/js_src/sim_runner.mjs` counts them exactly: every
`agent()` call lands in `events.agents` with its label and phase, so a
scenario can assert how many dispatches a phase spends and how that number
moves with the FR count. No live run is needed.

The shape to hold onto: **per-phase work belongs outside the FR loop.** Each
turn of an FR loop is a full sub-agent — a step that runs a project-wide,
idempotent command once per FR pays N times for one answer. Before Round 22
that was 80 of the ~203 dispatches a 20-FR P3–P8 run spent, on a
fire-and-report step with no verdict gate, so nothing failed and nothing
flagged it.

Measured on the testbed (happy path, all FRs fast-path PASS):

| | FR=5 | FR=20 |
|---|---|---|
| before Round 22 | 113 | 203 |
| after | 80 | 110 |

P4/P5/P6/P7/P8 are now flat in the FR count. P3 is not, and should not be:
its per-FR cost is a TDD orchestrator plus an independent verify, which is
the work itself.

When reviewing a workflow change, the question to ask is not "is this check
useful?" but **"does this cost scale with something it does not depend on?"**

### run-all vs. eight launches (Round 23)

`run-all.js` inlines all eight phase bodies. Measured the same way, P1–P8:

| | FR=5 | FR=20 |
|---|---|---|
| eight files launched in sequence | 148 | 178 |
| `run-all.js` | 143 | 173 |

The −5 is exactly `−6 Sync + 1 state.json cursor read`: six phases fold their
`git push` into `advance-phase --push`, and run-all reads the starting phase
once. A seventh saving — `resolveRepo` running once instead of eight times —
is real but conditional on `args.repo` being absent, so it does not appear
here (the sim always supplies it).

Read that table the right way round. After Round 22 the cross-phase
redundancy in a single run was already down to ~10% of the total, so **run-all
buys unattended determinism across the whole methodology, not throughput.**
Its per-phase dispatch sequences are pinned equal to the standalone files'
(`sim_runner.test.mjs` §11) — which is evidence about dispatches, not about
final artifacts; only a live E2E run can speak to those.

## One time base — and one thing it cannot fix (Round 24 站3)

Every machine-readable timestamp the harness writes comes from
`core.utils.timefmt.utc_now_iso()`: UTC, ISO 8601, offset always present.
Enforced by `tests/test_timestamp_convention.py`, an AST scan with **no
allowlist** (the fix is always one call).

Before this, a single run left three unalignable clocks:
`sessions_spawn.log` and `last_block.md` in naive LOCAL time, `state.json` and
`fr_progress.json` in offset-aware UTC, `gate_timestamps.jsonl` in epoch
floats. Auditing the run-all-by-workflow P1-P8 artifacts, a spawn-log "15:44"
was compared against a state.json "07:43+00:00" and read as an eight-hour
stall. The real gap was 1h18m. **An observability layer whose own timestamps
cannot be lined up answers questions incorrectly rather than declining to
answer them.**

`gate_timestamps.jsonl` keeps `ts` as an epoch float — `core/doctor.py` does
arithmetic on it, and a format swap would break every existing project's file.
It gains an `iso` field alongside; rows written before this station have no
`iso` and still read.

## Liveness — `.methodology/heartbeat.json` (Round 24 站5a, PARTIAL)

`harness_cli.py::_dispatch` records `{command, utc}` after every subcommand,
success or failure, in a `finally` — the single funnel every CLI entry passes
through, so a new subcommand cannot forget to participate. `doctor` WARNs past
`core.heartbeat.STALL_THRESHOLD_MINUTES` (45) and names the last command.

**What it cannot see, stated plainly because a partial solution that reads as
complete is worse than none:** an agent that is alive but not calling the
harness — thinking, waiting on an LLM, or stuck inside a sub-agent dispatch.
The workflow runtime exposes no heartbeat API. A stale heartbeat is evidence
of a stall, not proof (a legitimately long single dispatch looks identical);
a fresh one is not proof of health. The boundary is pinned by
`tests/test_heartbeat.py::test_heartbeat_cannot_see_an_agent_that_never_calls_the_harness`.

The gap it does close is the one that bit: Phase 6 of the run-all-by-workflow
run reached `Gate4 PASS 97.4` and made no further progress for 1h18m with
nothing noticing. The liveness judgement available at that moment was "the
journal has had no new entry for 3 minutes, treat it as dead" — improvised,
with no mechanism behind it.

## Two ledgers, not one — the workflow dispatch gap

`run-report` covers **harness-side spawns only** (`core/agent_spawner.py` →
`sessions_spawn.log`). It does NOT cover the workflow runtime's own `agent()`
dispatches. Measured on the run-all-by-workflow P1-P8 run:

| Ledger | What it saw |
|---|---|
| `sessions_spawn.log` (run-report) | 78 entries, $35.75, 1224 turns |
| workflow runtime (self-reported) | ~143 `agent()` dispatches; 1,473,527 subagent tokens for the P6-P8 segment alone |

These are different books and cannot be summed. Workflow JS is hermetic (no
`fs`, no `process`) and its `agent()` calls never touch the harness, so there
is no correct way for the harness to observe them — only fragile ones (reading
the runtime's internal journal path). Recorded as a known boundary rather than
closed with a workaround; see `docs/PROPOSAL_ADJUDICATIONS.md` Round 24.

## advance-phase's cost model (Round 25)

Measured on the run-all-by-workflow evidence project, warm caches, same tree,
before and after Round 25:

| completed | before | after | test-suite executions |
|---|---|---|---|
| 1 | 1.0s | **0.76s** | 0 → 0 |
| 2 | 1.1s | **0.50s** | 0 → 0 |
| 3 | 55.8s | **12.9s** | 5 → 1 |
| 4 | 45.6s | **13.4s** | 5 → 1 |
| 5 / 7 / 8 | 23.9s each | **~13.1s each** | 2 → 1 each |
| **P1–P8 total** | **~187s** | **~78s** | **18 → 6** |

Two facts worth keeping in view when reading these numbers:

* **Essentially all of advance-phase's wall time is the test suite.** Every
  non-test check in the command — manifest integrity, gate score variance,
  ghost paper-trail, finalize sentinels, phase auditor, traceability regen, SAB
  drift, spec-coverage, gitleaks, ruff, mypy, STAGE_PASS — sums to about **2
  seconds**. Proposals to speed advance-phase up by removing checks are
  optimising a 2-second budget; the 185 seconds were one suite run counted five
  times over.
* **The suite now runs at the first consumer that needs it**, not at a fixed
  point. At P3 that is the Gate 1 live-coverage check; at P5–P8 it is Phase
  Truth's framework block. Every later consumer reads the same measurement.
  `core.quality_gate.test_suite_run.run_suite` is the only place it executes;
  the memo is per-process, with a content fingerprint over source, tests and
  test configuration as a tripwire so a mid-run edit cannot be served stale.

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
