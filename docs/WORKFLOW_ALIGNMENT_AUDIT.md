# Workflow JS ↔ Plan Alignment Audit (Round 11, station 0)

> Audits the 8 phase workflow JS files under `.claude/workflows/` against
> their corresponding `phaseN_plan.md` (rendered fresh from the current
> HEAD `scripts/plangen` generator — see "Methodology" below for why a
> committed snapshot is never the baseline). Produced in response to a
> three-clause review request: (1) 100% alignment between the 8 JS files
> and the 8 plans; (2) methodology/framework design belongs in harness, not
> embedded in workflow JS; (3) any gap that survives must be justified by a
> genuine Claude Code Workflow runtime constraint, explicitly.
>
> Machine enforcement: `tests/test_workflow_plan_alignment.py`
> (`KNOWN_GAPS` / `RUNTIME_ONLY` registries) + `scripts/workflow_audit/extract.py`.
> This document is the human-readable rendering of those two registries
> plus narrative context the tests don't carry.

## Methodology

- **Plan-side baseline = current HEAD plangen generation, not a committed
  snapshot.** `integration-test/.methodology/phaseN_plan.md` (v2.12.0) was
  diffed against `scripts.generate_full_plan.generate_full_plan(N, ...,
  dynamic=True)` on a minimal fixture project — the only differences were
  the project-name substitution and one fixture-completeness artifact
  (`03-development/tests/` vs `tests/`, because the audit's minimal
  fixture has no `03-development/` directory — not a generator drift).
  **Conclusion: no drift to report** — HEAD generation and the committed
  copy agree. Using HEAD generation as the ongoing baseline (rather than a
  frozen copy) is still the correct long-term choice: a frozen external
  snapshot as a test baseline is exactly the failure mode that left
  `test_workflow_artifacts_commit_pattern.py` skipping silently for its
  entire life (see "Dead guard" below) — the moment the snapshot and
  reality diverge, a skip/import-path assumption hides it instead of
  failing loudly.

- **Command-set extraction, not text diff.** Each plan `- **[MARKER]**`
  step's "span" (marker bullet up to the next marker or heading) is
  scanned for genuine `harness_cli.py` subcommand invocations — a
  subcommand mention only counts when immediately preceded by the literal
  `harness_cli.py` or immediately followed by an argparse `--flag`. This
  precision rule exists because several registered subcommands
  (`status`, `effort`, `dispatch`, `doctor`, `manifest`) are also common
  English words that appear constantly in prose (`"dispatch as separate
  subagent"`, `"{status, files, confidence, ...}"` as a JSON shape) — a
  bare word-boundary substring scan false-positives on all of them. The
  same subcommand registry (`harness_cli.build_parser()`'s own argparse
  choices — 57 subcommands, read live, never hardcoded) and the same
  precision rule are applied to the JS side.

- **This is not a full-fidelity diff.** Prose-only plan steps with no CLI
  invocation (e.g. "write BASELINE.md with these 7 sections") aren't
  decidable this way; they're covered by human reading during each file's
  migration (stations 1-4), not by this audit's mechanical check.

## Alignment result (station 0 snapshot)

### `KNOWN_GAPS` — plan instructs a command, JS doesn't invoke it anywhere

| Phase | Marker | Missing command(s) | Disposition |
|---|---|---|---|
| P3 | ENV-CHECK | `finalize-env-check` | **Needs investigation** — see below |
| P4 | ENV-CHECK | `finalize-env-check` | Needs investigation |
| P5 | ENV-CHECK | `finalize-env-check` | Needs investigation |
| P5 | ORCH-POST | `amend-sab` | **Real gap** — add in migration |
| P6 | B-DISPATCH | `dispatch` | **Not a gap** — legitimate `agent()` substitution |
| P7 | ENV-CHECK | `finalize-env-check` | Needs investigation |
| P7 | ORCH-POST | `amend-sab`, `spec-coverage-check` | Real gap — add in migration |
| P7 | TDD-PRECHECK | `spec-coverage-check` | Cosmetic/UX gap only (see below) |
| P8 | ENV-CHECK | `finalize-env-check` | Needs investigation |
| P8 | ORCH-POST | `amend-sab`, `spec-coverage-check` | Real gap — add in migration |
| P8 | TDD-PRECHECK | `spec-coverage-check` | Cosmetic/UX gap only |

**ENV-CHECK / `finalize-env-check` (P3, P4, P5, P7, P8 — 5 files):**
`cmd_run_env_check` (`cli/gate_cmds.py`) spawns a `claude` sub-process
itself to evaluate the environment inline and writes a sentinel file; the
JS reads `run-env-check`'s own process exit code directly (comment:
"root-cause fix: CLI exit code reflects ready flag", Bug #127 / commit
`17d6d53`) and never calls `finalize-env-check`. `cmd_finalize_env_check`
performs a *separate* anti-fabrication check (the sentinel must exist and
not predate a stale result file) that `run-env-check`'s own exit code does
not necessarily perform. **Whoever migrates P3/P4/P5/P7/P8 must read both
functions in full and decide**: either Bug #127 made `finalize-env-check`
genuinely redundant (in which case the *plan's* wording is stale and
should be flagged to the user, not silently rewritten — see "明確不做" in
the Round 11 plan), or skipping it is a live anti-fabrication gap that
must be closed by calling it.

**ORCH-POST / `amend-sab` + `spec-coverage-check --threshold 40` (P5, P7,
P8):** the plan runs both commands immediately after each FR's
GATE1-DELTA passes. P3/P4's equivalent step already covers this (no gap);
P5/P7/P8 do not. Real, mechanical gap — add during migration.

**TDD-PRECHECK / `spec-coverage-check --threshold 90` (P7, P8):**
`advance-phase` itself enforces this internally (exit code 10) regardless
of workflow JS — **this is not a missing-enforcement gap**. P5's JS
additionally runs the same check *proactively* as an early "D4-GAP"
warning (catching the 80%-vs-90% threshold gap before wasting an
`advance-phase` retry round); P7/P8 lack this proactive UX step only.
Optional to add during migration, not required for correctness.

**B-DISPATCH / `dispatch` (P6) — not a real gap:** the plan's marker
instructs `harness_cli.py dispatch --role reviewer ...`, the SKILL.md
manual-orchestration mechanism for spawning a sub-session from *outside* a
running agent. Inside a workflow script, `agent()` already *is* that
mechanism — the JS's "Peer Review" phase calls `agent()` directly with an
equivalent reviewer prompt. Invoking `dispatch` a second time from inside
a workflow would nest a redundant CLI subprocess. Kept and documented, not
migrated.

### `RUNTIME_ONLY` — JS invents a phase with no plan counterpart

Verified by reading the JS body and its own code comments (not by title
matching — see the scope note in `tests/test_workflow_plan_alignment.py`
for why an exhaustive phase()-title classification would produce
confidently-wrong answers for titles like "Milestones"/"Release Docs"/"Tag
& Advance", which correspond to real plan content under different
wording, individually verified and excluded here):

| `phase()` title | Files | Why the plan has nothing like it |
|---|---|---|
| **Manifest Integrity** | P3, P4, P5, P6, P7, P8 | 2026-07-02 incident: a sub-agent action (e.g. a stray `pytest` leaking the harness's own test CWD) can corrupt `quality_manifest.json` mid-run — checked both at phase entry AND immediately before the phase-exit push so corruption is never baked into a milestone commit (commit `3198402` shipped exactly that failure once, before this check existed). A human running the plan by hand has no equivalent because a human doesn't dispatch sub-agents that can race the manifest file the way a workflow's `agent()` calls can. |
| **Artifacts Commit** | P4, P5, P7, P8 | The plan's milestone push sweeps the tree wholesale at the very end — fine for a human who only reaches that point after everything else succeeded. A workflow script can exit *early* (a gate/handoff FAIL returns before the milestone push is ever reached), stranding that phase's already-deterministic artifacts (e.g. `BASELINE.md`) uncommitted on a dirty tree. This phase commits those specific paths early via an explicit allowlist (never `git add -A` mid-workflow — mirrors phase4's original `d4f4724` fix). |
| **Sync** | P1–P8 (all 8) | `advance-phase` deliberately commits the phase handover *locally* without pushing (next milestone push publishes it) — fine for a human continuing to the next phase's plan file by hand. A workflow *script* ends immediately after Advance with no next-phase push queued in the same run, stranding the handover commit until something else happens to push it (Bug A, 2026-07-07). This phase publishes it immediately via `git push origin main`. |

Sub-phase-granularity runtime-only mechanisms that don't warrant their own
registry entry (they live *inside* an existing phase(), not as a separate
box) but are worth naming for stations 1-4's benefit: the **DELTA
fast-path** (per-FR GATE1-DELTA classified in one batched agent call
before falling back to the full per-FR loop — turns N-already-passing FRs
into 1 spawn instead of 2N), the **schema verdict proxy** pattern (a heavy
narrative orchestrator's prose is never parsed for PASS/FAIL; a separate
flat-schema agent reads the harness's own artifact — manifest
`quality_complete`, `state.json` `current_phase`, a CLI exit code — v4
playbook rule, direct response to the #126/#134/#135/#136/ENV_CHECK_RC
paraphrase-incident class), and the **session-limit guard** (a `null`/
too-short agent return is distinguished from a real gate FAIL, so a
rate-limit mid-run is never misreported as a code-quality failure).

## Duplication inventory (context for stations 1-4)

| Function / concept | Copies | Files |
|---|---|---|
| `resolveRepo()` | 8/8 | every phase file — one bug (`58f8b2f`, submodule-worktree walk-up) required 8 separate fixes |
| `checkManifestIntegrity()` | 7/8 | P3–P8 (not P1/P2 — no manifest yet) |
| Verdict schemas (`VERDICT_SCHEMA`/`RC_SCHEMA`/`CTX_SCHEMA`/`PHASE_SCHEMA`) | 5-6/8 | most phase files |
| A/B review machinery (`balancedJsonAt`, `extractLastJson`, `parseAgentJson`, `buildBPrompt`, `structuredBReview`, `persistApproval`, `loadFileViaPython`, `hasHighGap`, `safePrevB`, `summarizeVerify`, `runPeerReview`/`abLoop`) | 2 full copies | P1 (11 functions), P2 (11 functions, near-identical) |
| JSON balanced-brace parser (3rd independent copy) | 1 more | P6 (`balancedJsonAt`/`extractLastJson`/`parseAgentJson`, not shared with P1/P2's copies) |
| `agent()` call sites (unique labels) | 19/17/18/22/15/10/15/15 | P1…P8 respectively — 131 total, each a candidate for shared prompt-fragment extraction where the surrounding SCOPE RULES/verdict-schema text repeats |
| Prompt/methodology text embedded as JS string literals | ~880 lines of `+ '...'` continuation lines across 8 files | parallel truth alongside the plangen-generated plan text — the actual Round 11 rationale for the `workflowgen` render-from-SSOT approach (stations 1-4) |

## Dead guard fixed this station

`tests/test_workflow_artifacts_commit_pattern.py` (9 tests) had silently
skipped since the day it was written: its `REPO` default pointed at
`integration-test/.claude/workflows/`, a path that has never existed (the
actual consumed copy lives at `integration-test/harness/.claude/workflows/`
— reached via the git submodule, not a plain directory).
`pytest.mark.skipif` made the missing path invisible instead of failing.
Fixed by defaulting `REPO` to this repo's own root (`.claude/workflows/`
is a first-party build artifact of *this* repository, not something that
needs to be found in a sibling checkout) — all 9 tests now run and pass.
`INTEGRATION_TEST_DIR` env-var override is preserved for anyone who wants
to point the check at a different checkout.

## Methodology document gap

`workflow-playbook.md` (814 lines — the Claude Code Workflow runtime
constraints, script API, and 11-incident postmortem log this whole audit
draws on) exists only in `integration-test/.methodology/`, generated
per-project by `init-project`. harness-methodology itself — the framework
that *authors* these 8 workflow files — has no copy. Station 5 adopts it
into `docs/WORKFLOW_PLAYBOOK.md` as the harness-side SSOT.

## Forward plan

Stations 1-4 migrate the 8 files to `workflowgen`-generated output (shared
JS blocks + prompt text sourced from the same `scripts/plangen` SSOT the
plans themselves render from), closing every "real gap" `KNOWN_GAPS` entry
above and locking every `RUNTIME_ONLY` entry with its accident-numbered
justification into the generator itself. Station 5 adopts the playbook,
adds a runtime-convention lint (import/fs/process bans, 512KB cap,
meta-first-statement), and registers the guards.

## Dispatch determinism registry (Round 12, station 2c)

Every `agent()` dispatch in the 8 generated files is classified in
`tests/test_workflow_dispatch_registry.py` (the machine-checked SSOT — a
new dispatch label fails CI until classified; this section is the
human-readable rendering). Three classes:

- **carrier** — the agent runs a FIXED command and transports its output;
  the LLM contributes no judgment. Carriers cannot be "sunk" further: the
  dynamic-workflow runtime has no direct exec API (playbook §4), so a Bash
  sub-agent is the only bridge from workflow JS to a deterministic tool.
  The carrier IS the sunk form — what matters is the verdict anchor.
- **judgment** — the LLM output is the actual work product (authoring
  deliverables, fixing code, scoring gate dimensions, reviewing).
- **mixed** — fixed command skeleton plus fix-on-BLOCKED reaction loops.

And three verdict anchors, strongest → weakest:

| anchor | meaning | hallucination cost |
|---|---|---|
| `js-regex` | JS regex/startsWith on a CANONICAL string printed by a deterministic tool | must rewrite echoed stdout |
| `schema` | AJV-validated StructuredOutput transcription of tool output | mistype a field (Bug #122 class) |
| `text-token` | JS regex on the LLM's OWN prose (`/SYNC:\s*PASS/` …) | just write the token |

Station-2 changes moved the highest-traffic verdicts up this ladder:

- `gate1-verify-*` (P3 TDD loop + the P4/P5/P7/P8 `render_per_fr_delta`
  family) now dispatches `harness/scripts/verify_gate1_qc.py` and derives
  the verdict from the echoed canonical stdout ONLY — the LLM's schema
  `pass` boolean is ignored. v2.13.3's prose claimed this while its code
  still ANDed the boolean; the sim testbed pinned the contradiction and
  station 2a closed it (wf_53d055ce-d0b hallucination class).
- The TRACE-PRECHECK ritual (agent-side attestation regen+commit before
  Gate 2/3/4 and the P2 push — 37 ritual commits on integration-test) is
  DELETED from all prompts: `finalize-gate`, `push-checkpoint`, and
  `push-milestone` now self-heal a stale attestation before their own
  commit (`cli/_shared.ensure_fresh_attestation`, same freshness probe
  the prepare-commit-msg hook uses).

Remaining `text-token` rows are hardening candidates, in priority order
(traffic × blast radius): `sync*` (a git-log proxy carrier would be
canonical), P1/P2 `preflight*`/`constitution*`/`forward-ref-check`
(prose-token gates on fix loops), `push-*`/`milestone-*`/`advance*`
(mitigated — their authoritative verdicts already come from the paired
`*-verify-r` carriers; the prose token only steers retry pacing).
`aci-post-sab` is a grandfathered carrier-on-prose exception, asserted
as exactly-one in the registry test.
