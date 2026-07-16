# Convergence Audit — 2026-07-16 (Round 12)

**Question asked**: integration-test (driven by the 8 workflow files) keeps
hitting problems. Is it the refactoring? Are pre-existing problems failing
to converge? Are there structural/root-cause defects or redundant steps?

**Answer in one line**: refactoring is NOT the driver (twice disproven);
the root structural defect was an INVERTED VERIFICATION BOUNDARY — the only
environment that exercised the full chain (workflow JS × sandbox × spawn ×
permissions × prompts) was a live production E2E run, so every fix shipped
blind and every run found the next boundary bug, producing a flat ~10
fixes/day treadmill.

All numbers below were measured on 2026-07-16 from this repo's git history
and integration-test's `.methodology/sessions_spawn.log` (read-only).

---

## A. Quantified findings

| # | Finding | Evidence |
|---|---|---|
| 1 | Fix rate flat: 819/1418 commits (58%) are `fix`; ~10 fixes/day for a month with no downward trend | `git log` per-day counts 06-21→07-16 |
| 2 | Current E2E run thrash: 461 dispatches, 147 ERROR + 15 TIMEOUT (35% failure), FR-01 alone consumed 140 dispatches, ~3.8 agent-hours total | sessions_spawn.log |
| 3 | Top error classes: 76× CLI startup banner captured as the ONLY error output (real cause invisible); 62× "Commit-required step returned empty commit"; 15× 600s timeouts | sessions_spawn.log TOP ERRORS |
| 4 | Spawned agents hit permission walls AND followed the user's interactive-collaboration protocol (waiting for a confirmation that cannot come in a headless session) | agents' own replies in error_output |
| 5 | Three-way contract contradiction: prompts instructed `pytest tests/ -q`; the target's allowlist covers only `python3 *` / `git add/commit/push` forms; measured `--setting-sources "project"` ALSO loads the user's global CLAUDE.md | fr_cmds sites + settings.local.json + live probe matrix |
| 6 | fix-on-fix within 6 hours: v2.13.2 (LLM verdict → spawnSync) → v2.13.3 (sandbox has no Node API → ReferenceError). Root cause self-named: "the test harness has no coverage for the dynamic-workflow execution substrate" | commits 567a72e / cef32c4 |
| 7 | Ritual cost: 37 "trace: regen attestation before Gate N" commits on integration-test; same-FR TDD-RED committed 5×; 9 resets | integration-test git log |
| 8 | The anti-gaming police is a leading failure source: core/quality_gate ≈8.7k lines; red_assertion_check went through an R1→R8 fix series including a tightening that was mathematically unsatisfiable for correct code; a phantom-module precondition block was recorded as three quality zeros | commit history + spawn log |
| 9 | OLD hotspots HAVE converged: harness_cli.py (257 fix-touches) / generate_full_plan.py (125) / harness_bridge.py (63) were strangled/split in Rounds 1–6 and stopped bleeding. Live hotspots moved to agent_spawner / fr_cmds / quality_gate checkers | 60-day fix-touch counts |
| 10 | Refactoring disproven as driver (2nd time; Round 8 was the 1st): the v2.13.x incidents were NEW-design defects; the Round-11 workflowgen migration is golden-locked and its 3 escaped regressions (see below) were caught by THIS round's testbed, not by production | commit forensics |

## B. Root causes (ranked) and what Round 12 shipped against each

**S1 — Inverted verification boundary (root of roots).** 5,200+ unit tests
green while the orchestration system itself only ever ran in production.
→ 站1: `scripts/workflowgen/js_src/sim_runner.mjs` executes all 8 REAL
generated workflow files under mocked runtime globals; 23 scenario tests
(happy ×8 through every declared phase, null-agent ×8, hallucinated
verdicts both directions, JSON-less A/B crash pin, schema-missing-field,
2 regression pins). **First run caught 3 live defects** the Round-11
equivalence pins could not see: phase4's dropped `p4MidPushed` declarations
(ReferenceError at ≥50% FR progress), phase6's missing
`MAX_OUTER_ATTEMPTS` (ReferenceError at first approval write), phase1's
three null-unsafe `.slice(-800)` error paths.
→ 站0b: `AgentSpawner.preflight_substrate()` + run-phase wiring — one ≤90s
probe proves spawned agents can execute python3/pytest/git before the
per-FR loop starts (the 140-dispatch FR-01 thrash becomes one FATAL with a
three-surface diagnosis). Honest boundary: sim covers JS logic; the probe
covers the real OS/permission substrate; live LLM behavior remains E2E's.

**S5 — Unattended-agent execution contract (three-way contradiction).**
→ 站0a: all agent-executed commands in prompts use allowlist-compatible
forms (`python3 -m pytest/ruff/coverage/…`, `git commit -m '…'`), guarded
by tests/test_dispatch_prompt_command_forms.py.
→ 站0d: measured `--setting-sources` matrix ("" → no CLAUDE.md; "user" →
user's; **"project" → project AND user's both**; "local" → none).
`_resolve_phase3_context` now pins `""`; every spawn appends
`_UNATTENDED_PREAMBLE` at the system-prompt layer.
→ 站0c: CLI startup banner stripped from error capture
(`_extract_dispatch_error`: result-JSON error first, denoised stderr
second); the banner is removed from the STRUCTURAL signature registry —
Fix H-G's own 4/5-retry-succeeds data had already disproven the fatal-env
theory, and 76/461 banner-only entries buried real causes.

**S4/S6 — Deterministic work dispatched to LLMs; rituals in prompts.**
→ 站2a: the P4/P5/P7/P8 GATE1 verify family now dispatches
`verify_gate1_qc.py` (cef32c4's deferred migration), and the verdict is
derived from the echoed canonical stdout ONLY — v2.13.3's code had still
ANDed the LLM's `pass` boolean against its own "the pass field is ignored"
comment; a hallucinated `pass:false` could veto a PASS manifest.
→ 站2b: `cli/_shared.ensure_fresh_attestation` — finalize-gate /
push-checkpoint / push-milestone self-heal a stale attestation before
their own commits; all 7 prompt-side TRACE-PRECHECK ritual lines deleted
(the 37-ritual-commit choreography retired).
→ 站2c: `tests/test_workflow_dispatch_registry.py` classifies all 67
dispatch labels (carrier / judgment / mixed × verdict anchor js-regex /
schema / text-token / none); a new dispatch fails CI until classified;
carriers may not gate on LLM prose (1 grandfathered exception asserted).
Remaining text-token rows are the hardening queue (sync* first).

**S3 — Checker false positives are asymmetrically expensive.**
→ 站3a: spec-satisfiability probe — provably-impossible spec constraints
(missing case ids; shared-predicate overlapping-unequal trigger scopes)
downgrade to `spec_unsatisfiable` warnings naming the SPEC defect. The
probe itself is built conservative (output-shaped predicates skipped) —
its first draft false-killed a satisfiable fixture and was caught by the
existing test suite, which is the R5 lesson applied to the R5 fix.
→ 站3b: `_check_infra_fail_pollution` — zero scores carrying run-gate
PRECONDITION-block signatures are rejected at finalize-gate as INFRA_FAIL
(navigation: fix the precondition, not the source); the GATE1 STOP RULE
gained the matching INFRA_BLOCKED branch (write NO zeros).
→ 站3c: `values.checker_enforcement` + graduation policy — new
checkers/tightenings ship at "warn" and earn "block" after one clean E2E
run; existing block checkers cannot be weakened by config.

## C. Redundant steps (asked directly; disposition)

1. ~60 verify-class LLM dispatches carrying deterministic strings →
   reclassified as **carriers** (the sunk form under the runtime's
   no-exec-API constraint); what was actually wrong was the verdict
   anchor, now moved up the ladder (站2a) and registry-enforced (站2c).
2. TRACE-PRECHECK ritual (37 commits) → deleted; tools self-heal (站2b).
3. nohup/poll/PID dances in gate prompts → still present; they exist to
   dodge the runtime's 180s stall watchdog. Correct fix is a CLI-side
   background-job primitive — **deferred** (next-round candidate), not
   quietly half-patched here.
4. Gate 2/3/4 mega-prompts (5 chained steps, any failure reruns the whole
   prompt) → step 0 removed (站2b); further splitting deferred with #3.

## D. Known allowlist-incompatible commands (deliberate, documented)

JS/TS-path tools (`npx eslint/tsc/vitest/jest`, `node benchmarks/run.mjs`,
`semgrep`, `gitleaks`) have no `python3 -m` form; if a JS/TS-language
target project runs spawned gates under a restricted allowlist, its
`.claude/settings.local.json` needs entries for them. The NON_CODE_FR
`echo` pseudo-command is likewise cosmetic-only. Python-path commands are
fully covered by `python3 *`.

## E. Convergence metrics — measure the NEXT E2E run against these

| Metric | 2026-07-16 baseline | Mechanism that should move it |
|---|---|---|
| Spawn failure rate (ERROR+TIMEOUT / dispatches) | 35% (162/461) | 站0a/0c/0d contract+observability fixes |
| Dispatches per FR to Gate-1 PASS | 140 (FR-01) | 站0b probe FATALs before loops thrash |
| Banner-only error_output entries | 76 | 站0c → should be 0 |
| Ritual attestation commits per run | up to 37 (cumulative) | 站2b → should be 0 |
| Harness fixes/day during a run | ~10 | 站1 testbed catches JS-logic classes pre-ship |
| Workflow crashes (ReferenceError/TypeError class) | 3 latent found | sim suite floor (≥23 tests) keeps them at 0 |

If after one full E2E run the spawn failure rate is still >15% or the
fixes/day is still ~10, the next suspect is the per-FR gate choreography
itself (方向丙 from the Round 12 plan — redesign, deliberately not
attempted this round).

## F. What was checked and did NOT need fixing

- Old god-file hotspots (harness_cli / generate_full_plan): already
  strangled, no longer bleeding (fix-touch counts collapsed post-split).
- Round-11 migration methodology: golden byte-equal + equivalence pins
  held; the 3 escapes were in dimensions those pins deliberately did not
  cover (declarations, null guards) — now covered by the sim testbed.
- The FR-01 `test_coverage 71.4 < 80` block that ended the last run: a
  GENUINE quality gap (last_block.md), correctly identified by the
  pipeline once infra noise cleared. Not the harness's bug to fix.
