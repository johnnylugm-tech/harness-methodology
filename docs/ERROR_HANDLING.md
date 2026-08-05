# Error Handling — the block / degrade / warn taxonomy

老闆's question this round: after many rounds of debugging, the fault-tolerance
mechanism felt unfriendly and incomplete — errors got tolerated, but in ways
that landed in an unexpected handling path and made the actual problem hard
to find. This doc is the answer: **when to block, when to gracefully degrade,
and when NOT to block at all** (downgrade to a warning), plus the crash
boundary and self-repair loop that came out of the same diagnosis.

Five commits in this repo's history are the direct evidence for why this
doc exists: `3d6216b` (a harness parsing bug disguised as a hard BLOCK — the
agent had no way to fix it because the "defect" was in harness, not the
project), `bda76da` (harness's own state write tripped harness's own guard;
a real PASS was reported as a FAIL), `3278cc1` (an error-path rollback left
an orphaned commit because only the working tree was reverted, not the
commit object), plus the two infra incidents in `test_infra_fail_separation.py`
and `test_agent_spawner.py::TestPreflightSubstrate` (a phantom precondition
block wrote fake quality zeros; a permission-walled substrate burned 140
dispatches / ~2.5h before a human noticed). All five are the same root
shape: a failure that wasn't the class it was treated as.

## The five levels

| Level | Marker | Meaning | Exit code | Channel | Agent action |
|---|---|---|---|---|---|
| **BLOCK** | `[BLOCKED]` | A real, fixable quality/precondition failure — bad code, a missing artifact, an unmet gate | most of `cli/exit_codes.py`'s `REGISTRY` (the oldest, largest category — see that file, not a hand-copied list here) | stdout (the agent-facing protocol surface) | Read the message as the fix instruction and act on it verbatim. `tests/test_blocked_message_contract.py` guarantees every `[BLOCKED]` on the four agent-facing hot paths (advance-phase / finalize-gate / run-fr-step / push-checkpoint / push-milestone) carries a concrete remediation element. |
| **FATAL** | `[FATAL]` | The environment/substrate is broken — a tool can't run, dispatch is structurally broken, a spawned agent can't execute pytest/git, or a project data file exists but isn't readable. No code change fixes this | `23` dispatch structurally broken, `24` substrate preflight failed, `25` FR-step infra/harness-bug abort, `26` `.methodology/state.json`/`quality_manifest.json` corrupt (Round 14 — see `core/state_io.py`'s `StateCorruptError`) | stdout | STOP. Do not retry, do not attempt a code fix. Escalate to a human. Exit `26` specifically: `git restore` the corrupt file if it's tracked, or run `harness_cli.py doctor` — this is project data corruption, not a code defect. |
| **HARNESS-BUG** | `[HARNESS-BUG]` | harness-methodology's own code crashed — an uncaught exception reached the top level. Not a problem with the target project at all | `70` | **stderr only** — must never look like a project-quality signal on the agent-facing stdout channel | STOP. Do not retry, do not modify project code. Report the banner verbatim. A crash bundle is waiting in `.methodology/crash/` for `crash-triage`. |
| **DEGRADE** | `[DEGRADED]` | A fallback fired that changes downstream behavior (discarded tracked history, an empty baseline, a silently-skipped record) — but the run can legitimately continue | none (the run's own exit code is unaffected) | stderr + appended to `.methodology/degradations.jsonl` | Continue. If a downstream result looks off, check the ledger first — it names exactly what changed and why. |
| **WARN** | `[WARN]` | Worth a human's attention but changes nothing about how the run proceeds | none | stderr for new code (see note below) | No action required to proceed. |

**Existing markers outside this taxonomy**: `[ERROR]` (41 sites) is used
loosely for a range of severities predating this doc — new code should pick
one of the five levels above rather than add another `[ERROR]` print.
`[INFO]` / `[OK]` / `[SKIP]` are plain status output, not failure signals.

**On `[WARN]`'s channel**: 151 pre-existing `[WARN]` print sites exist across
`cli/`/`core/`/`harness/`/`scripts/`/`detection/`; 121 of them print to
stdout, not stderr (verified by direct grep). This round does not retarget
them — moving a pre-existing print to stderr risks breaking whatever
downstream regex treats stdout as the agent-facing protocol surface, for a
purely cosmetic gain. New `[WARN]`/`[DEGRADED]` output added from this round
onward goes to stderr; the stdout/stderr split is a property of *when the
line was added*, not a universal rule enforced retroactively.

## Choosing a level for new code

1. **Did harness-methodology's own code just throw an exception you didn't
   deliberately raise?** → **HARNESS-BUG**. You don't usually write this by
   hand — `harness_cli.py`'s `_dispatch()` catches it automatically at the
   top level of every command. Only call `core.errors.format_harness_bug_banner`
   / `write_crash_bundle` directly if you're building another top-level
   entry point outside `harness_cli.py`.
2. **Is what failed the *environment* — a tool won't run, a spawned agent
   can't execute a command, a dependency is structurally unreachable —
   rather than the project's code?** → **FATAL**. Abort before dispatching
   any fix agent; see `cli/fr_cmds.py`'s `_abort_dispatch_infra_or_harness_bug`
   for the pattern (checked *before* the normal fix-round dispatch, not as
   a fallback after one fails).
3. **Is there a real, fixable quality/precondition problem, and can the
   reader (agent or human) actually act on this message right now?** →
   **BLOCK**. Write the fix instruction INTO the message — a bare
   "`[BLOCKED]` X failed" with no remediation fails
   `tests/test_blocked_message_contract.py` on any of the four hot paths.
4. **Did a fallback fire that changes what happens next** (not just "retry
   politely") **— lost data, an empty baseline, a silently-skipped file?**
   → **DEGRADE**. Call
   `core.degradation_ledger.record_degradation(project, component, what, why)`.
5. **Otherwise**, if it's worth a visible line but changes nothing → **WARN**.
   A plain `print(..., file=sys.stderr)` is enough.

If BLOCK vs. DEGRADE is unclear: ask "can the reader of this message do
something about it right now?" Yes → BLOCK, with the instruction. No, but
they should know it happened → DEGRADE.

## Raise vs. return an error-dict (Round 14)

Two different signaling mechanisms coexist in this codebase; new code
should pick based on where the boundary sits, not by copying whichever
one the nearest function happens to use:

- **Across a process boundary** (a CLI command's own success/failure, or
  a spawned sub-agent's outcome) — an **exit code** plus the marker+
  message on stdout is the contract. This is what `cli/exit_codes.py`'s
  `REGISTRY` and the five-level taxonomy above are for.
- **Between functions within one process, when the failure is
  exceptional** — prefer a narrow, specific exception type
  (`StateCorruptError`, `GateBlockedError`, `KillSwitchBlockedError`,
  etc.) over a generic one. The caller decides whether to catch it, let
  it propagate to the crash boundary, or convert it into one of the five
  levels above. Catching `Exception` broadly just to inspect a string
  message is exactly the shape `tests/test_exception_swallow_ratchet.py`
  exists to catch.
- **`{"error": ...}`-shaped dicts** are not one universal convention —
  each is the established return shape of a specific existing family,
  and that family's own callers already know how to read it:
  `core/agent_spawner.py`'s sub-agent dispatch envelope;
  `core/phase_hooks.py`'s `PhaseHooks` check methods, which always pair
  `"error"` with the method's own primary signal (typically `"passed"`)
  because the caller needs a pass/fail decision even when the check
  itself partially failed; and a couple of small, self-contained
  integration helpers (e.g. `harness/ssi/scripts/crg_integration.py`).
  New code extending one of these families should match its existing
  shape. New code that isn't extending one of them should raise instead
  of inventing a fourth error-dict convention.

This is descriptive, not a new enforced rule — none of the existing
exception hierarchy, error-dict sites, or `except` blocks are being
rewritten this round (see "what this round deliberately did not do"
below). This section exists so the *next* piece of new code has a
documented default to reach for, instead of guessing from whichever
nearby function it happens to be editing.

## The exception-swallow ratchet — silence is never free

`tests/test_exception_swallow_ratchet.py` AST-scans `cli/`, `core/`,
`harness/`, `scripts/`, `detection/` for a broad `except Exception` / bare
`except:` whose handler has no `log`/`print`/`raise` call anywhere in its
body AND ends in one of three fail-open shapes:

- returns a success-shaped value (`True` / `None` / `[]` / `{}` / a
  `(True, ...)` tuple),
- ends in `continue` (silently skips the current loop iteration),
- is a silent fallthrough (every statement is an assignment/expression/
  `pass` — control just continues past the `try` with no visible trace at
  all).

**Zero allowlist, by design.** The fix is always available and always free:
one diagnostic line (a `print`, a `record_degradation` call, or — nine
times out of the fifteen-plus times this scan has run across this
project's history — an actual live bug the missing log was hiding). A
narrowly-typed `except (SpecificError, AnotherError)` is entirely outside
this ratchet's scope; it only targets the broad catch-all shape, because a
deliberately narrow catch is already a documented decision, not a silent
one.

## The crash boundary + self-repair loop (nice-to-have, done safely)

`harness_cli.py`'s `main()` routes every command through `_dispatch()`:

- `KeyboardInterrupt` → exit `130`, no bundle written.
- A known control-flow exception (`GateBlockedError`, `KillSwitchBlockedError`)
  leaking past the site that should have caught it → `[WARN] <ClassName>
  leaked to top-level` + exit `1` — visible, not alarming: it means a call
  site forgot to catch something it owns, not that harness crashed.
- Anything else → `core.errors.write_crash_bundle()` writes
  `.methodology/crash/crash_<timestamp>_<pid>.json` (full traceback, argv,
  cwd, harness's own git SHA, a ready-to-run repro command, and a
  ready-to-paste maintenance prompt), the `[HARNESS-BUG]` banner prints to
  stderr, and the process exits `70`.

This is the "nice-to-have automatic self-repair" evaluated for this round,
implemented in its safe form. A production run does **not** auto-patch
harness's own code — that would fight harness's own guards and ratchets
from the inside and break the trust chain a fixed, reviewed harness version
gives you. Instead, capture and triage are automatic; the actual fix stays
a reviewed, human-in-the-loop maintenance session:

```
harness_cli.py crash-triage --project <project>              # list bundles, grouped by cause
harness_cli.py crash-triage --project <project> --open-cr    # file each unfiled cause as a CR-BUG
```

- Bundles are grouped by **signature** (deepest traceback frame's
  `file:line` + exception class) — the same underlying bug produces the
  same signature across repeated crashes even though timestamp and pid
  differ every time.
- `--open-cr` always files into **harness's own**
  `.methodology/change_requests/` (`cli.cr_cmds.harness_repo_root()`),
  never the target project's — the bug is in harness's code regardless of
  which project's run triggered it. This is deliberate-trigger only: a
  production run never calls `--open-cr` automatically.
- Every bundle in a signature's group gets a `.triaged` sidecar once that
  signature is filed (not just the newest one), so re-running `--open-cr`
  is idempotent, and a bundle that arrives later for an already-filed
  signature reuses the existing CR instead of opening a duplicate.
- `harness_cli.py doctor` WARNs when an untriaged bundle is sitting in
  `.methodology/crash/` — a confirmed harness bug nobody has looked at yet.

## Why a gate blocked — the seven causes (Round 24 站1)

`harness_bridge.finalize_gate` raises `GateBlockedError` from ten sites. Nine
attach a `details` dict whose key names the cause; the tenth is the generic
`not _gate_passes` path, which blocks on the result itself.

Both consumers of a block event — the agent-facing diagnostic + `last_block.md`
(`cli/gate_cmds.py::_format_block_diagnostic`) and cross-run failure memory
(`core/lessons.py::record_gate_block`) — read
`core.quality_gate.block_reason.derive_block_reasons`. It is the only model of
"why did this gate block". Before Round 24 they each carried a private copy of
one filter ("a dimension is below threshold") and never read `details`, so six
of the seven causes rendered as an EMPTY failure list whose only advice was to
run the gate again.

| `details` key | Cause | The fix is NOT "re-run" |
|---|---|---|
| `tool_score_fabrication` | A claimed dimension score the harness could not reproduce by running the tool itself | Correct — re-running re-rolls the same judgement. Make the claim true or withdraw it |
| `tool_evidence_missing` | Passing score with no `tool_evidence` / `evidence_file` | Attach the evidence, then re-run finalize-gate |
| `infra_fail` | Dimension scored zero because its tool could not run | Environment failure, not a code defect — never route to CODE-FIX |
| `malformed_gate_result` | Gate result file truncated / off-schema | Re-run run-gate to regenerate; do not hand-repair |
| `crg_independent_failed` | The harness's own CRG measurement failed | Persistent failure is a harness defect (`crash-triage --open-cr`) |
| `architecture_regression` | Architecture score regressed vs the previous exit gate's baseline | A waiver does not clear a regression |
| `da_waiver` | Waiver rejected on adjudication — its premise did not match the framework's numbers | Fix the dimension, or rewrite the premise |
| *(no details)* | Failing dimension, `open_critical`/`open_high` > 0, or composite below the score gate | `derive_block_reasons` falls back to the result itself, so an all-dimensions-passing block still names its cause |

Adding a raise site with a new key without registering it in
`block_reason._DETAIL_REGISTRY` fails
`tests/test_block_reason_registry.py`. At runtime an unknown key does NOT
raise — it renders with a "no remediation registered, file with crash-triage"
banner, because turning a gate block into a harness crash on the one path
where the agent most needs information is strictly worse.

## Exit 9 has two causes, and the message says which (Round 25)

`_advance_prechecks` returns 9 for a test/coverage shortfall. Until Round 25
that verdict was rendered by pytest itself (`--cov-fail-under=100`), so a red
suite and a green suite at 99.9% produced the same nonzero exit and the same
message. The comparison is now explicit, against the exact coverage percentage
from the shared suite run, and the `[BLOCKED] TDD test/coverage failure` block
names which of the two happened:

```
[BLOCKED] TDD test/coverage failure.
  Tests did not pass (see output above).        <- red suite
  Coverage 99.95% < 100%.                       <- green suite, short coverage
```

The pytest output itself is printed immediately above, so the failing test
names are in front of the agent either way. Same exit code, because the
remediation channel is the same (fix the project's tests); different first
line, because the two are not the same problem.

## A dispatch's error_class, and what each one routes to (Round 26)

`core.agent_spawner._classify_dispatch_error` labels every failed dispatch, and
`cli/fr_cmds.py` decides what to do next from that label. Round 26 added two
classes because the fix loop had been guessing at both.

| `error_class` | Means | Routes to |
|---|---|---|
| `STRUCTURAL` | Deterministic environment breakage; retry can never succeed | abort with remediation |
| `INFRA` | The sub-agent reported a precondition blocker (`INFRA_BLOCKED`) — the tools never ran | abort; **never** CODE-FIX, never an identical re-dispatch |
| `TURN_BUDGET` | Cut off at its max-turns ceiling; the agent was working, the budget ended | re-dispatch the SAME step once at double the ceiling, recorded in the degradation ledger |
| `INFRA_ERROR` | Network / auth / rate-limit / model-unavailable | caller's retry policy |
| `EXECUTION_ERROR` | Everything else | the ordinary fix loop |

Two rules this table encodes, both bought with real incidents:

**A diagnostic may not replace the evidence it describes.** `_validate_inner_json`
used to overwrite `output` with a one-line synthetic message. Every downstream
safety net string-matches that field, so the rewrite silently blinded them:
measured on one real log entry, the agent's own reply classifies as
`('INFRA', 'Architecture Amendment Protocol violation')` and aborts the fix loop,
while the synthetic replacement classifies as `None` and the loop dispatches
CODE-FIX at healthy code. The diagnostic is now additive — diagnostic first
(humans and the MAST rules key off its phrasing), raw reply after it.

**`INFRA_BLOCKED` is a first-class inner status.** `cli/fr_prompts/gate.py` orders
a Gate 1 evaluator to report it when run-gate prints `[BLOCKED]`. Until Round 26
that word existed in exactly one place in the codebase — the prompt asking for it
— so the report fell through to the commit-required branch, where classification
depended on whether the agent had volunteered a `"pass": false` key: with it, a
real blocker was waved through as progress; without it, the same blocker became an
EXECUTION_ERROR. Same blocker, two outcomes, decided by an optional key.

## A dimension's score_source, and who is allowed to say "not applicable" (Round 27)

A gate-result breakdown entry may carry `score: null`. Round 27 站1 made that
mean one specific thing, recorded in a sibling `score_source` field written by
the framework — never by an agent.

| `score_source` | Written when | Effect on the verdict |
|---|---|---|
| *(absent)* | The agent reported a number, or reported `null` and nothing checked it | A number is judged normally. **An unchecked `null` fails its floor** — it was verified by nobody |
| `framework` | The agent reported `null`; S4 ran the tool itself and got a score | The framework's number is written back into the breakdown and judged |
| `framework_na` | The agent reported `null`; S4 ran the tool and it too produced no score (`pytest --benchmark-only` exit 5, a scorer returning None) | Genuinely not applicable — excluded from the composite and vacuously passes, with `na_verified_by` naming the tool and returncode |

The rule behind the table: **`null` used to mean "nobody has to check this" and
now means "the framework has to check this".** It was previously waved through by
five layers at once — S3 accepted any prose ≥10 characters as evidence, S4's
`if agent_score < threshold: continue` skipped it, the weighted average dropped
it from the denominator (which *raised* the composite by redistributing its
weight onto the perfect dimensions), and `_all_dims_pass` treated it as
"vacuously satisfying its own per-dim floor". A measured Gate 4 shipped
`mutation_testing: null` with the evidence "NFR-08 satisfied contractually
(harness surface exists)" and `performance: null` with "dimension N/A per
protocol (**not free 100**)". That parenthetical is the whole finding: the agent
knew a claimed score gets cross-validated, and picked the door that did not.

The strictness is scoped to dimensions S4 can actually verify — the gate config
declares them, they name a tool, they require tool execution. A `null` outside
that set still passes vacuously, because being strict where the framework cannot
check is exactly what once pushed a Gate 3 agent into fabricating a performance
score (`test_finalize_gate_null_breakdown_score_does_not_block`). Too lax lets a
declared N/A through unchecked; too strict manufactures the fabrication it means
to prevent.

## Exit codes

Single source of truth: **`cli/exit_codes.py`'s `REGISTRY` dict.**
`tests/test_exit_code_registry.py` enforces, in both directions, that every
exit code actually returned anywhere in `cli/*.py` appears in `REGISTRY`,
and that `harness_cli.py`'s docstring "Exit codes" section matches it
exactly. Read the registry directly rather than trusting a copy here — a
hand-duplicated list is exactly the kind of drift this round exists to
close.

Four numbers are deliberately overloaded — `12`, `17`, `18`, `19` each mean
two unrelated preconditions depending on which one fired. This is
documented as a known inconsistency in the registry module's own
docstring rather than renumbered: renumbering is a larger
compatibility-risk change than a documentation pass, and every site also
prints an identifying `[BLOCKED]`/`[FATAL]` message, so the exit code alone
was never the only signal a caller has.

## What this round deliberately did not do

- No auto-patching of harness's own code (see the crash-boundary section
  above).
- No rewrite of the 14-class exception hierarchy into one base class, and
  no rewrite of the ~750 existing `except` blocks — `GateBlockedError`'s
  lifecycle was already healthy; the edges (crash boundary, routing,
  message contract, swallow-site logging) needed closing, not the
  interior.
- No change to any existing BLOCK threshold or checker judgment logic —
  this round is about the *shape* failures take on their way out, not
  which failures exist.
- No retargeting of the 105+ pre-existing `[WARN]` print sites (see the
  channel note above).
- `cli/gate_cmds.py`'s `--skip-preflight` CLI flag is registered but never
  read anywhere — a sibling dead artifact of the same abandoned "Symphony
  Item 9" preflight feature `PreflightBlockedError` was removed from
  (Round 13 站3). Left in place: fixing it wasn't part of this round's
  scope, and it's inert (a flag nothing reads cannot change behavior),
  but it's a known loose end for a future pass.
- (Round 14) No unification of the several existing `{"error": ...}`-dict
  conventions (`agent_spawner`'s dispatch envelope, `phase_hooks`'s check
  methods, `crg_integration.py`'s helpers) into one shape — each already
  has callers that know its specific fields; merging them would touch
  every call site for a stylistic gain with no behavior change.

---

## Abstaining is not passing (Round 30)

A check that could not run must not return the value that means "I ran and found
nothing". This is the sharpest form of the pattern this repo keeps paying for,
and it has now recurred inside the commit that was fixing it.

**The rule.** A checker has three possible answers, and the framework must be
able to tell them apart:

| Answer | Shape | Who decides next |
|---|---|---|
| ran, clean | empty violations | the gate proceeds |
| ran, found something | violations, with remediation | the agent or the operator fixes it |
| **could not run** | a violation naming the reason, **or** an exception | never silently the first row |

**How to pick between the last two:**

- **Missing framework-owned asset → BLOCK with a diagnostic.** The four gate
  YAMLs are tracked by `git ls-files`; their absence means the checkout is
  broken, not that the project has no configuration. Same for anything else the
  framework ships and depends on.
- **Caller-contract violation → let it raise.** `gate_num` outside 1-4 comes
  from the framework's own call sites, never from user input. Round 29 caught
  that `ValueError` in three places and returned `[]` / `(True, [])` — a
  programming error reported as "no fabrication found". The Round 28 crash
  boundary exists for exactly this and names the caller in a crash bundle.
- **A genuine environment gap the run can survive → degrade AND record.**
  `record_degradation`, not a `logging.debug` nobody reads. "We could not check"
  is a fact about the verdict, and the ledger is where the next reader looks.

**Never an environment variable.** `63b9399` added
`if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"): return True, []`
to `verify_gate_tools` to unbreak harness's own CI, citing `phase_cmds.py`'s
substrate-probe CI skip as precedent. The justification does not transfer: that
probe needs an interactive `claude` CLI which GitHub Actions genuinely cannot
have and the CI workflow's own comments call "always local". Tool availability
is different — CI can install ruff, and a consumer's CI-run gate scoring without
its tools verified is precisely what S2 exists to prevent. It was also settable
by anyone: `CI=1` disabled tool verification for a whole local run.

When a check breaks the framework's own test suite, the skip belongs in the test
layer (monkeypatch the checker), not in a production branch. Guarded by
`tests/test_abstain_is_not_a_pass.py`, which pins all four gate-config consumers
behaviourally: given a config they cannot read, do they block or do they pass?

**Not turned into a tree-wide ratchet, and why.** The scan run for this rule
found 179 `if not <path>.exists(): return <empty>` / `except: return <empty>`
sites across `cli/ core/ harness/ scripts/`. Most are correct — a `_read_json`
returning `{}` for a missing file is a *reader*, and its caller decides what an
empty result means. The distinction between a reader and a checker is semantic;
AST cannot make it, and a ratchet would be 179 false positives that get silenced
within a round. Re-open when there is a mechanical discriminator.

## `enforcer_surface` — provenance that survives a rebase (Round 29/30)

`enforcer_sha` (Round 19 站3) records the harness commit that produced a verdict.
It is a **mutable** identifier. taskq-advance's 8 Gate 1 results, its Gate 2
result and both `state.json.phase_completed` entries all name `01bb3bb4`; a
rebase of the harness submodule left that commit reachable from nothing.

`enforcer_surface` records `git rev-parse HEAD:<path>` for the three paths that
actually produce verdicts — `core/quality_gate`, `harness/harness_bridge.py`,
`harness/gate_configs`. Measured across the real rebase:

| commit | `core/quality_gate` | `harness_bridge.py` | `gate_configs` |
|---|---|---|---|
| `01bb3bb4` (orphaned) | `99ba0a38` | `1c5a000f` | `6800e4b4` |
| `7154768` (replacement) | `99ba0a38` | `1c5a000f` | `6800e4b4` |
| `c5971cd` (pre-fix base) | `36d32b5d` | `1c5a000f` | `6800e4b4` |

Identical across the rebase, and correctly different from the base. The commit's
own tree hash does **not** work (`7f19c4f4` vs `9e72df80` for the same pair) —
a rebase moves the commit onto a different base, so the whole-tree hash changes
even when the enforcing code did not.

Both writers record both fields; `core.doctor._check_enforcer_provenance` is the
reader, and WARNs (never ERRORs) when a recorded `enforcer_sha` no longer
resolves. An unreachable enforcer does not make a verdict wrong — it makes the
question the field was added for unanswerable, which is worth exactly one WARN.

## A parse failure is not an absence (Round 31)

Round 30 ruled that a checker which cannot run must not return the value that
means "ran, clean". Round 31 found the same shape one level down, in parsers:

> **`None` / `[]` / `0.0` out of a parser means "this text carries no such
> information", never "the information is that there is none."**

Three parsers of `mutmut` output were measured against real inputs:

| parser | expected | real input |
|---|---|---|
| `_count_mutmut_results` (sqlite) | `Mutant.status` | correct |
| `_parse_mutmut_survivors` | `10, 24` | **0 of 308** — mutmut prints ranges |
| `_extract_mutmut_kill_rate` | `Killed 240` | **None** on all three real formats |
| `tool_runners._score_mutmut` | `Killed: 240` | dead — nothing emits it |

The consequences were invisible precisely because each abstention looked like a
result. A run whose own banner read `Survived 🙁 (308)` wrote `survivor_count: 0`
into `mutation_survivors.json`, and Gate 3's bug-hunt manifest read that zero as
"no leads".

Three rules follow.

1. **One module per external format.** `core/quality_gate/mutmut_report.py`
   holds every mutmut format, with the formatter beside the parser so a
   round-trip test binds producer to consumer. Four parsers in three files is
   how a system stops being able to read itself.
2. **Record what the tool said next to what you parsed.**
   `mutation_survivors.json` now carries `reported_total` — the count mutmut
   printed — alongside the list. `survivor_count: 0` beside a raw banner saying
   308 is self-refuting, and nobody could see it because only one of the two
   numbers was a field.
3. **Never compare against a format you do not produce.** The framework's own
   `mutation-test-score` prints `killed=N survived=N score=X`, and the
   framework's own anti-fabrication check could not read it. If a check has
   never fired on a real input, it is not a check.

### The measurer's failure is not the measured party's fault

The same round found S4 running `pyright {root}` — 4917 files, of which 4344
were a committed `.venv` — against a prompt telling the agent to scan `src/`.
It timed out, and the timeout was reported to the agent as
`tool_score_fabrication` with the remediation "Install 'pyright'", for a tool
that was installed.

> When the framework cannot complete its own measurement, the block must say
> so in the framework's own voice and route to the framework's own budget
> (`harness_config values.timeouts`). Return code `-2` (timed out) and `-3`
> (not found) are different findings and no longer share a sentence.

## A tool the harness could not run is not a failing tool (Round 32)

Round 31 ruled that a parser abstaining must not look like a result. Round 32
found the consequence one layer up, where the abstention reaches a verdict:

> **"the harness could not measure this" and "the harness measured, and the
> agent's number was false" are different findings with opposite remedies, and
> they must not share a block-reason key.**

Measured on a live P4 Gate 1, `.methodology/last_block.md`:

```
1. tool_score_fabrication
   - test_coverage: fabrication detected — harness ran 'pytest-cov' and
     scored 0.0 (below threshold 80.0), but agent reported 100.0
   - architecture_constraints: fabrication detected — harness ran
     'import-linter' and scored 0.0 ...
```

Neither tool had judged anything. `import-linter` printed `Could not find
package 'X' in your Python path` and exited 1, because `run_tool` gave
PYTHONPATH only to `pytest`. The registered remediation for
`tool_score_fabrication` reads *"Do NOT re-run the gate — the score, not the
run, is what failed"*, so the agent was told to make a true claim true.

Three rules follow.

1. **A scorer returns `None`, not `0.0`, when it cannot read a result.**
   `_score_pytest` returned 0.0 when the run collected no tests;
   `_score_exit_code_binary` returned 0.0 for every non-zero exit, including
   the ones where the tool says in words that it never started;
   `_score_pytest_benchmark` returned **100.0** on a collection error, because
   its scoring only ever subtracts and a crash has no rows to subtract for.
   `compute_tool_score` has meant `None` = "cannot score" since it was
   written; these three never reached it.

2. **An abstention still blocks — under `infra_fail`.** Making the scorers
   honest without changing the verdict layer would have traded a false
   accusation for a silent pass, which is Round 30's rule broken from the
   other side. The `harness_score is None` branch records a degradation and
   raises, and `s4_block_details` maps it to `infra_fail` — a key that already
   existed for the symmetric case (an agent recording an INFRA-polluted zero)
   and already carries the right instruction: repair the tool run, do not touch
   the score. Round 13's routing keeps it out of a CODE-FIX round.

3. **The framework's own inability is the framework's bill.** If the harness
   cannot reproduce a measurement, the finding names the harness — the audit
   file, the invocation, the scorer — not the party being measured.

## A citation we could not parse is not a file that does not exist (Round 33)

`unresolvable_citations` (core/quality_gate/agent_b_approvals.py) had one
fallback for everything its regex did not match: treat the whole string as a
path, fail to resolve it, and report

    <the whole string> (no such file)

So a reviewer who wrote a perfectly good citation in a shape the regex had not
learned yet was told the file was missing — about a file that was right there.
Measured: all 4/4 Phase 1 approvals on a live run blocked on

    SRS.md:972 (FR-05 §10 verification array missing AC-05-6)

while SRS.md sat on disk with 1116 lines and line 972 well in range.

Two prior rounds fixed this one shape at a time — Round 26 taught the regex
`path:N-M`, `4bdc0fb` taught it a trailing `(annotation)` — and each left the
branch that manufactures the false reason. So the class survived both fixes.

**The rule.** A string carrying `:NNN` was written as a line spec. When it
does not parse, the finding says exactly that and lists the accepted forms:

    <text> (unparseable citation format — a `path:NNN` line spec was intended
            but not recognised. Accepted: `path:N`, `path:N-M`, `path:N:M`,
            any of those with a trailing `(annotation)`, or a bare path for a
            whole-file reference)

A string with no line spec is still a whole-file reference, so `no such file`
keeps meaning what it says. Three distinct reasons — unparseable, missing,
out of range — stay three distinct reasons.

This is Round 24 站1's rule (a block states the cause it actually has) applied
to the one message this validator emits. The regex will need widening again;
what changes is that the next widening is prompted by a message naming the
real problem rather than by a hunt for a file that was never missing.

## The requirements block is found by its content (Round 33)

`srs_machine_block` (scripts/plangen/artifact_parsers.py) used to locate
SRS.md's machine-readable block by looking for a `<!-- FR:START -->` sentinel
pair, then for a `## Appendix A` / `## FR Block` heading. Both are
agent-authored decoration. Measured on a live SRS (1116 lines, 8 FRs and 12
NFRs under `## 10. AC ↔ Module Traceability (machine-readable)`, no sentinels
anywhere): both paths missed it, and the parser returned `{}` **in silence**,
so every consumer read the file as declaring no FR metadata. That was the
fourth abstention of the class Round 30 cleared three of.

Widening the heading match was tried and reverted: on a later snapshot of the
same project it matched the project's *unfilled template stub* two sections
earlier and handed downstream a placeholder FR-01. **A parser that finds the
wrong block is worse than one that finds none.**

**The rule.** Every fenced JSON object is parsed; the one carrying
`functional_requirements` is the block. Three ways of guessing where the block
lives, replaced by the one property that identifies it. Two consequences the
heading scan did not have:

- the unfilled template example carries the key too, so it is filtered by
  content (`{project_name}` as the project, or every FR description still a
  `{placeholder}`). A block with **no** descriptions is not a stub — real
  blocks often carry only ids and module lists.
- two filled candidates means two answers. That returns `None` with a
  diagnostic rather than taking the first.

Every outcome — not found, not JSON, ambiguous, stub-only — says so on stderr.

## A red build is a build failure, not a push failure (Round 37)

`push succeeded` and `the build is green` are two propositions. taskq-renew
pushed 52 times, 48 of them onto a red build, and the pipeline reported PASS
throughout because only the first proposition had an enforcer. `verify-ci`
(exit 31) is the second; see docs/OBSERVABILITY.md for the verdict model.

Classification: a red CI run is a **project quality failure** — the CODE-FIX
route. A verdict that could not be obtained (exit 32) is **INFRA**: no `gh`,
no network, no run yet. It never becomes a pass, and it never routes to
CODE-FIX, because nothing about the project's code caused it.

## An install that fails must stop the job (Round 37)

Eight `pip install ... || true` sites — five in the CI template every consumer
project ships, three in this repo's own workflows — let a failed dependency
install continue. When `code-review-graph` was pinned into `requirements.txt`
and pip hit a `ResolutionImpossible`, nothing installed, `|| true` swallowed
it, and the failure surfaced three steps later as `ModuleNotFoundError: No
module named 'yaml'` inside `sab_parser.py`.

That is an infrastructure failure wearing the face of a content failure —
precisely the routing this document forbids everywhere else. The swallows are
gone; `tests/test_ci_install_steps_hard_fail.py` keeps them gone.

## A waiver request is a CODE-FIX or a CONFIG-FIX, never a pass (Round 38)

`da_waiver` in a gate result is refused, not granted. Classification:

| the finding | route | what to do |
|---|---|---|
| a community is genuinely oversized or low-cohesion | **CODE-FIX** | split it, or reduce cross-package coupling so CRG detects sub-communities |
| CRG misreads an intentional layout (workflow tooling scored as product code, small-package Leiden over-fragmentation) | **CONFIG-FIX** | calibrate `crg_excludes` / `crg_cohesion_healthy` in `.methodology/harness_config.json` |
| the threshold feels wrong for this project | *not a route* | the floor lives in `harness/gate_configs/*.yaml` and is the same one CI applies |

Why there is no waiver route: a waiver was read by `finalize_gate` and by
nothing else. `crg-arch-check` — which CI runs on every push from phase 3, and
which the workflow ANDs into `gate{N}Pass` — never knew waivers existed, so a
granted waiver produced a local PASS and a red build, and the gate loop then
spent its three rounds on a remedy that could not clear the check. Calibration
is written in a committed file, so every enforcer applies it.

## A gate verdict we cannot show was produced is not a pass (Round 38)

`advance-phase` blocks (exit 34) when the exit gate has no PASS in
`.methodology/gate_verify.jsonl` for the tree being advanced. This is the same
rule as Round 32's "a tool the harness could not run is not a failing tool" and
Round 35's "a number we could not measure is not a passing number", applied to
the verdict rather than to the measurement: a verdict recorded against a
different tree answers a different question.

Route: **not INFRA and not CODE-FIX** — run `verify-gate` against the tree you
are about to advance. The block message carries the command.
