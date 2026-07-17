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
| **FATAL** | `[FATAL]` | The environment/substrate is broken — a tool can't run, dispatch is structurally broken, a spawned agent can't execute pytest/git. No code change fixes this | `23` dispatch structurally broken, `24` substrate preflight failed, `25` FR-step infra/harness-bug abort (new this round) | stdout | STOP. Do not retry, do not attempt a code fix. Escalate to a human. |
| **HARNESS-BUG** | `[HARNESS-BUG]` | harness-methodology's own code crashed — an uncaught exception reached the top level. Not a problem with the target project at all | `70` | **stderr only** — must never look like a project-quality signal on the agent-facing stdout channel | STOP. Do not retry, do not modify project code. Report the banner verbatim. A crash bundle is waiting in `.sessi-work/crash/` for `crash-triage`. |
| **DEGRADE** | `[DEGRADED]` | A fallback fired that changes downstream behavior (discarded tracked history, an empty baseline, a silently-skipped record) — but the run can legitimately continue | none (the run's own exit code is unaffected) | stderr + appended to `.sessi-work/degradations.jsonl` | Continue. If a downstream result looks off, check the ledger first — it names exactly what changed and why. |
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
  `.sessi-work/crash/crash_<timestamp>_<pid>.json` (full traceback, argv,
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
  `.sessi-work/crash/` — a confirmed harness bug nobody has looked at yet.

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
