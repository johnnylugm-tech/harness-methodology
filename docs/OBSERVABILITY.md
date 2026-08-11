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

### `.methodology/degradations.jsonl`

**Round 27 站3 moved this out of `.sessi-work/.`** That directory is gitignored
and is cleaned between phases, so the ledger did not survive the run it
described: a measured run logged seven turn-budget exhaustions and the ledger
was simply absent afterwards — leaving no way to tell "never written" from
"written, then cleaned", which is the one question a ledger exists to answer. A
cross-run audit record has to outlive the work directory it records.
`LEDGER_RELPATH` in `core/degradation_ledger.py` is the single source; nothing
else should spell the path out.

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

**Round 43 站2** added the `obligation:<check_id>` family. When
`advance-phase` refuses because the preflight simulated at the phase being
entered reports blocking findings (exit
`EX_ADVANCE_ENTRY_OBLIGATIONS`), each finding gets one record: `component`
is `obligation:reliability_lint`, `obligation:artifact_consistency`, … and
`data` carries `target_phase`, `rule_id`, `file`, `line`. Before this, the
findings were rendered into a markdown table in HANDOVER.md that no
automated reader consumed — `grep -r "Entry Obligations"` returned one
producer and four test assertions — and the advance proceeded anyway. The
table is gone; this is where a programmatic reader now looks.

**Round 44 站2** added `milestone:uncommitted`, one record per file. When
`advance-phase` refuses because delivered files differ from HEAD (exit
`EX_ADVANCE_UNCOMMITTED_DELIVERABLES`), `data` carries `completed_phase` and
`file`. Same shape and same reason as the family above: the commit about to
record the phase would not contain content the phase's checks were measured
on. Measured on taskq-advance's P3→P4, the two files were
`03-development/tests/test_fr02.py` and `test_fr06.py`, and they entered git
fourteen minutes after the phase had turned over.

Harness bookkeeping and the files `advance-phase` rewrites itself are
excluded — see `core.utils.delivery_scope.is_harness_volatile` and
`cli/phase_cmds.py::_advance_commit_targets`, which are the two sources the
check reads rather than restating.

### `.methodology/crash/crash_<timestamp>_<pid>.json`

**Round 28 站4 moved this out of `.sessi-work/` for the same reason as the
ledger above** — Round 27 站3 moved the ledger and left the crash bundles
behind. A bundle is the *only* input to a harness-bug diagnosis (traceback,
`argv`, `repro_command`, `harness_git_sha`), and it was living in the directory
agents are instructed to clean up after themselves. `CRASH_DIR_RELPATH` is the
single source; `LEGACY_CRASH_DIR_RELPATH` is read, never written, so a project
that crashed under an older harness and then updated is not reported as clean
while its bundles sit on disk. `core/errors.crash_bundle_paths()` is the one
enumerator — `doctor`, `run-report` and `crash-triage` all go through it rather
than globbing the directory themselves.

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
- **Gate provenance**: per gate, the finalized verdict, composite and
  `enforcer_sha` — plus, since **Round 27 站3**, how many of that verdict's
  dimensions carry an `evidence_digest`, and an explicit WARN when the enforcer
  ends in `-dirty` (a verdict produced from an uncommitted tree corresponds to
  no commit and cannot be reproduced). A `0/N` line means the verdict cannot be
  re-checked: its evidence normally lives under the gitignored `.sessi-work/`
  and does not survive the run. Measured on a completed project, all four gates
  read `0/N` and one enforcer was `-dirty` — the finding printed by the tool
  rather than argued in prose.
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

## Two substrates, one ledger — partially closed (Round 26)

The harness dispatches agents two ways, and instrumented one:

| Substrate | Reaches | Records |
|---|---|---|
| `run-fr-step` → `core/agent_spawner.spawn()` | per-FR steps (P3, P4) | role, phase, fr_id, status, error_class, **cost, turns, usage, duration** |
| the workflow script's own `agent()` | everything else — all of P1/P2, every preflight, peer review, orchestration step | role, phase label, status (Round 26) |

Measured on taskq-plus P1–P3: **42 spawn-log entries, all of them phase 3**, while
`.harness/traces/agent_trajectory.jsonl` recorded `phase_1_preflight` and
`phase_2_preflight` spans right beside them. So `run-report`'s "42 dispatches,
failure rate 9.52%" was a P3/P4 number presented as the run's — a wrong
denominator, not a missing detail.

### What Round 26 closed, and what it could not

Every generated workflow now routes its dispatches through a `dispatch()` wrapper
(injected by `scripts/workflowgen/generate_workflows.py`, 118 call sites in
run-all). The wrapper buffers a record per dispatch and hands the buffer to the
NEXT dispatch as a one-line bookkeeping preamble, which calls
`harness_cli.py log-dispatch --batch`.

**Recovered: the denominator.** Dispatch counts and failure rate now span P1–P8.

**Not recoverable on that substrate: cost, turns, duration.** The Workflow script
sandbox has no filesystem, no shell, and no clock (`Date.now()` throws), and
`agent()` returns text rather than the CLI envelope. `loadFileViaPython` — which
dispatches an entire SHELL WRAPPER AGENT to read one file — is that constraint
made visible. `run-report` already prints `N/M entries have cost data`; that
fraction is now the honest statement of the split, not an accident.

### Retraction

This section previously read: *"there is no correct way for the harness to observe
them — only fragile ones."* That was too strong, and Round 26 disproved it. There
was a correct way: make the wrapper — which already observes every outcome — pass
its records to an agent that does have a shell. What is genuinely impossible is
the cost model, not the observation.

### Properties worth keeping

- **Zero extra dispatches.** A per-call CLI write needs a shell the script lacks;
  a per-call wrapper agent would double the dispatch count.
- **No dispatch reports its own outcome.** Records are written by a *different*
  agent than the one they describe (its successor) — the R21 站3 principle about
  audit trails the audited party writes.
- **Failures are recorded**, because the wrapper observes the outcome rather than
  the dead sub-agent.
- **The final dispatch of a run is never flushed** — nothing follows it to carry
  the record. The phase-level `run_phase` trajectory spans remain the crash-safe
  floor.

Guards: `tests/test_workflow_js_conventions.py` fails on a raw `await agent(`
beyond the wrapper's own call, on a wrapper declared more than once, and on a
catch block that rethrows without recording;
`scripts/workflowgen/js_src/sim_runner.test.mjs` asserts the buffered records
actually ride along on a later prompt.

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

---

## Round 30 additions

**A wall-clock timeout escalates its budget, once.** Round 29 made a timeout
visible (`error_class: "TIMEOUT"` plus a ledger line) and left the retry at the
identical ceiling. taskq-advance P3: 12 of 18 failed dispatches were 600.0s
timeouts, four consecutive on FR-02 at ten-minute intervals, each re-dispatched
into the same wall — two hours of wall time producing the same failure four
times. `cli/fr_cmds.py` now mirrors the turn-budget pair that had been next door
since Round 26: `_timeout_for` / `_note_wallclock_kill`, doubled once per step,
ledger entry in the same words (`task_timeout escalated A -> B`). A second
timeout at the doubled budget aborts normally — the bound is as load-bearing as
the escalation.

**The mutation scope travels with the score.** `compute_mutation_score`'s
message carries the effective `paths_to_mutate`. taskq-advance recorded
`mutation_testing 0` three times with no artifact anywhere stating that 3384
lines were mutated against a SPEC limiting the dimension to 1846 — the number
that explains the verdict was the one number the verdict did not carry. The
scope itself is now derived from the SAB at the P2→P3 handoff and written into
`setup.cfg`, so it is also in a commit a human can review; a scope that has to
fall back to the whole tree records a degradation naming what was missing.

**The exclusion list that moves a score is fingerprinted into the verdict.**
`.gitleaksignore` being tracked by git (Round 29) says nothing about *which
version* of it produced this verdict. Its digest now goes into `evidence_digest`
beside the tool outputs, keyed `<dimension>::<file>`, so two verdicts scored
under different exemption lists are distinguishable from the artifacts alone.
`DIMENSION_EXCLUSION_FILES` records `license_compliance: None` positively —
scancode takes its exclusions on the command line, and saying so is what stops
the next reader treating it as an omission.

## `.methodology/mutation_score.json` — the framework's own mutation number (Round 31)

Written by `compute_mutation_score` (i.e. by `harness_cli.py mutation-test-score`),
read by S4 and by `_patch_mutation_score` at finalize. Before this existed, the
`mutation_testing` score in every gate result was a number an agent typed.

| field | why it is there |
|---|---|
| `score` / `killed` / `survived` | the verdict, straight out of the sqlite cache |
| `paths_to_mutate` | the scope the number was taken on |
| `paths_to_exclude` | basenames dropped from the mutant pool — written by the party being scored |
| `mutated_files` | the denominator as a count, so a shrinking scope is visible without re-deriving it |
| `cache_sha256` | which `.mutmut-cache` produced this |
| `enforcer_sha` | which harness computed it (Round 19) |
| `generated_at` | UTC, per Round 24 站3 |

A missing or unreadable artifact BLOCKS a passing `mutation_testing` claim; a
present one overrides whatever the gate result said, marked
`framework_override: true` — the same shape the trace dimension has used since
PR 4.

`setup.cfg` is registered in `DIMENSION_EXCLUSION_FILES`, so its digest travels
into the verdict beside the tool outputs and an untracked one is an S6
violation. The `[mutmut]` section carries both halves of the denominator.

`.methodology/mutation_survivors.json` gained `reported_total`: what mutmut said,
beside what the framework parsed. See ERROR_HANDLING.md, "A parse failure is not
an absence".

## Finalize receipts and the evidence they must agree with (Round 32)

`.sessi-work/sentinels/g{gate}_p{phase}_{key}.finalized` used to contain a bare
ISO timestamp. It now contains a receipt, and `advance-phase` and `doctor` both
read it through `gate1_evidence.verify_finalize_evidence`.

| field | meaning |
|---|---|
| `schema` | receipt format version; anything else parses as "not a receipt" |
| `gate` / `phase` / `fr_id` | which verdict this attests |
| `score` | the composite the gate finalized at; must match `.gate1_scores.json` |
| `result_sha256` | digest of the `gate{N}_result.json` the verdict was taken on |
| `enforcer_sha` | the harness commit that enforced it (`core/harness_provenance`) |
| `ts` | when finalize-gate wrote it — **last**, after both registries |

The reconciliation rule is one-directional, because `GATE1-DELTA already done
→ skip` legitimately writes a timestamp row and no receipt:

```
receipt present  =>  a `finalize` gate_timestamps row for the same
                     gate/phase/FR exists, AND (gate 1) a .gate1_scores.json
                     entry exists whose score matches the receipt's
timestamp only   =>  legal
receipt + empty registries  =>  no producer. This is the forgery fingerprint.
```

Old-format sentinels are rejected outright. A legacy channel that still clears
the check is the same hole with a longer name; the cost is that a project must
re-run its gates once.

### Other Round 32 additions

- `run-report` → `degradations.turn_ceiling_escapes`: how many steps did not
  fit their configured `max_turns` and were re-dispatched with a raised one.
  Measured on a live P4: four of eight FRs. The default is unchanged — this is
  the number that would justify changing `values.step_max_turns`.
- `doctor` → `testpaths-drift` (WARN): test files the project's own effective
  pytest config leaves out of its default run, against what the framework
  collects. Reports, never rewrites. The declaring file is fingerprinted into
  the verdict via `DIMENSION_EXCLUSION_FILES["test_coverage"]`.
- `.methodology/last_block.md` is deleted when the gate/phase/FR it names
  subsequently passes, so a resolved BLOCK report cannot sit beside a state
  that says the phase completed.

## `verify-ci` — what the push produced (Round 37)

The framework pushed at every milestone and never looked at the result.
Measured on taskq-renew: **52 GitHub Actions runs, 48 red**, red on every push
from Phase 3 onward, while the local pipeline declared every phase and gate
PASS and advanced `state.json` to Phase 9. A full-tree search of `core/`
`cli/` `harness/` `scripts/` and `.claude/workflows/` found no reader of a
workflow run's conclusion; `scripts/phase_auditor.py`'s `GitHubFetcher` reads
the repo tree only.

`core/ci_verdict.py` asks `gh run list --commit <sha>` and answers one of:

| status | meaning | exit code |
|---|---|---|
| `green` | every run for the SHA concluded successfully | 0 |
| `red` | at least one run failed — names the jobs and their URLs | 31 |
| `unavailable` | the verdict could not be obtained | 32 |

`unavailable` is never `green` — the rule Round 32 and Round 35 applied to
mutation scoring. It splits in two, and only one half is worth waiting for:

- **retryable** — the run has not appeared yet, or is still in progress.
  `await_ci_verdict` polls (300s default, `--wait` overrides).
- **structural** — no `gh`, no network, a `gh` error. Returned immediately:
  no amount of waiting makes an origin remote appear.

Consumers:

- `cli/_shared.post_push_ci_gate` — runs at both push sites and returns
  non-zero on red, so a red build stops the pipeline. Skipped when git is
  disabled (`--no-git` / `--dry-run`): nothing was pushed. A project with **no
  origin remote** gets `not applicable` and 0 — there is no CI to be red.
- `harness_cli.py verify-ci --project . [--sha X] [--wait N]` — ask directly.

Deliberately NOT a `doctor` check: a `gh run list` per invocation would make
that offline at-rest reconciliation network-bound. The reasoning is left in
`core/doctor.py` at the point where the check would have gone.

## CRG metrics carry their own denominator (Round 37)

`.sessi-work/crg_metrics.json` now records `_graph_files`, `_source_files` and
`_build_type` beside `architecture_score`. taskq-renew's Phase 6 baseline said
77.8 and nobody could tell that it had been measured over 11 of 47 delivered
files; a full build on a clean clone of the same commit gives 57.1, which is
what CI reported while failing every push. The denominator travels with the
number, as it already does for mutation scope (Round 30/31).

## `verify-gate` — the gate verdict, on disk (Round 38)

`.methodology/gate_verify.jsonl` is append-only, one JSON object per line,
alongside `gate_timestamps.jsonl` and `degradations.jsonl`.

```json
{"ts": 1785…, "iso": "2026-08-06T…+00:00", "gate": 4, "phase": 6,
 "git_sha": "9f17ece…", "delivered_tree_sha256": "3a91…",
 "checks": {"last_gate_ok": true, "spec_coverage_rc": 0, "crg_rc": 0},
 "verdict": "PASS"}
```

| field | why it is there |
|---|---|
| `checks` | the raw per-check outcome, not a summary. The summary is what the workflow acts on; these are what makes a later "which of these two is wrong?" answerable. |
| `delivered_tree_sha256` | `sha256` over (repo-relative path, content) for `iter_delivered_files`, **as it stands on disk**, minus `core.utils.delivery_scope.is_harness_volatile`. A PASS is only a PASS for the tree it was measured on. |
| `head_tree_sha256` | the same digest over the tree git recorded at HEAD (Round 44 站1). Equal to the field above means the verdict was measured on committed content; different means it was not. Recorded, never blocking — a gate legitimately runs mid-Phase-3 on a dirty tree. `advance-phase` is where the difference becomes a refusal (exit 38), because that is where a commit starts claiming to be the phase. |
| `git_sha` | HEAD at verify time — for correlating with CI, which checks out a commit rather than a working tree. |

`delivered_tree_sha256` excludes harness bookkeeping from Round 44 onward.
Verdicts recorded before that carry a digest over a different set, so **they
are not comparable with one computed now** — a reader must use
`head_tree_sha256`'s presence to tell the two populations apart, exactly as
`core/doctor.py::_check_milestone_tree_matches_verdict` does. The first draft
of that check compared across the two and produced a finding that looked
right and proved nothing.

Before this file existed, `crg_rc` returned **zero hits** across taskq-renew's
entire `.methodology/` after a complete P1–P8 run. That run's P6 recorded a CRG
baseline of 77.8 — below the floor of 80 its own gate config states — while
`gate4-verify` passed on the first round, which requires `crg_rc === 0`. One of
the two is wrong and nothing survived that could say which.

Read it with:

- `harness_cli.py verify-gate --project . --gate N --phase P --spec-threshold T`
  — runs the three checks and appends the verdict.
- `advance-phase` re-derives the digest and refuses an exit gate with no
  matching PASS (exit 34). `verify-gate` itself exits 33 when a check fails.
