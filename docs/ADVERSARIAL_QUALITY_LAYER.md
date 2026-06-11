# Adversarial Quality Layer (v2.9.0)

## Why this layer exists

`tts-new` passed Gate 4 near-perfect (error_handling 100, test_coverage 100,
mutation 97.4) and an LLM bug-hunt then found **50 confirmed bugs (4
Critical)**. Root cause was five structural gaps:

1. **Tool dimensions measure structure, not semantics.** `error_handling`
   scored "has a try/except" — so `except BaseException:` (a Critical) was a
   *positive* signal.
2. **Same-source blind spot.** spec → test-name → test → impl is one agent
   pipeline; the mirror gate locks tests to a spec whose scenarios are
   incomplete. 342/342 green proved consistency, not correctness.
3. **Line coverage is blind to scenarios.** The `if len(chunks)==2:` short
   circuit executed (coverage 100) but no test asserted its output.
4. **No adversarial dimension** among the 14 (mutation testing is the
   closest, but it is threshold-based and produces no concurrency mutants).
5. **A/B review stopped at P2** — code had no reviewer; gates 1-4 replaced
   review with tool scores.

This layer closes the gaps with one principle: **deterministic where
possible, LLM only where required, and every output a verifiable artifact.**

## The four workstreams

| WS | What | Where | Cost |
|----|------|-------|------|
| **A** Static battery | `error_handling` presence→quality; reliability semgrep + config-liveness preflights | `lang_scanners/python_ast.py`, `tool_runners.py`, `semgrep_rules/py_reliability.yaml`, `phase_hooks.py` | zero LLM, every run |
| **B** Spec scenarios | architecture-risk pattern triggers (NP-13/15/07 forced by SAD module traits, not SRS prose); 15-pattern table sync | `derive_test_cases.md` Step 1b, `templates/TEST_SPEC.md` | P2, prevention |
| **C** Adversarial gate | `adversarial_review` Gate-3 dimension (framework-owned verdict over a bug-hunt report); targeting manifest; survivor persistence; hunt protocol | `bug_hunt_verifier.py`, `gate3_p4_exit.yaml`, `harness_bridge.py`, `hunt_bugs.md`, `bug-hunt-targets` CLI | 1 LLM hunt at Gate 3 |
| **D** Docs + calibration | this doc, SKILL/README/SOP updates, backtest | — | — |

### Division of labor (no double-reporting)

- **Statically determinable** bugs (subprocess no-timeout, TOCTOU,
  `except BaseException`, dead config keys) are caught by **WS-A before any
  gate**. Hunters must not re-report them.
- **Semantic / concurrency** bugs (lost output, probe races, unreachable
  paths) are caught only by **WS-C's Gate-3 hunt**.
- **mutation survivors** become **WS-C hunt input** (targeting manifest),
  not a separate gate item.

## Gate-3 flow (operator view)

```
# 1. targeting manifest (CRG hubs + mutation survivors + integration gaps)
python harness_cli.py bug-hunt-targets --project .

# 2. run the hunt (different model from the one that wrote the code)
#    protocol: harness/ssi/prompts/hunt_bugs.md
#    reference workflow: templates/workflows/hunt-bugs.js
#    → writes .methodology/bug_hunt_report.json + 03-development/.audit/*.md

# 3. resolve each confirmed critical/high: fix (commit/repro_test) or refute
#    (evidence) — edit resolution.status in the report

# 4. finalize — adversarial_review is computed by the framework from the report
python harness_cli.py finalize-gate --gate 3 --phase 4 --project .
```

`adversarial_review` (tier 2, threshold 100, weight 0.00,
`requires_tool_execution: false`) blocks Gate 3 until every confirmed
critical/high finding is `resolved` (with `fix_commit` or an **existing**
`repro_test`) or `refuted` (with `refute_evidence`). Medium/low and
unconfirmed findings never block.

---

## Appendix — v2.9.0 calibration backtest

Run against `tts-new @ dcc9f0b^` (the pre-fix tree the 50-bug hunt scanned).
WS-A is fully deterministic, so the backtest is reproducible.

| Check | Caught (pre-fix) | Maps to confirmed bug | Effect |
|-------|------------------|------------------------|--------|
| **A1** error_handling | `circuit_breaker.py:116 except_base_exception` | **circuit_breaker #13 (Critical)** | dimension 100 → **95** (was a free 100) |
| **A2** reliability lint | `audio_converter.py:80 subprocess-no-timeout` | **audio_converter #15 (critical-equivalent: hangs worker pool)** | Gate-blocked (P4+) |
| **A2** | `audio_converter.py:72,73 mkstemp-outside-try` | audio_converter fd-leak (#16 class) | Gate-blocked |
| **A2** | `audio_converter.py:96 toctou-exists-then-remove` | audio_converter #19/#20 (TOCTOU) | Gate-blocked |
| **A3** config liveness | `XXKOKORO_BACKEND_URLXX` + 5 undeclared keys | **config #46 (env typo → dead config)** | Gate-blocked (when a declaration source exists) |

**Deterministic interception**: ~6 distinct confirmed bugs (1 Critical +
1 critical-equivalent + 4 High/Medium) caught with **zero LLM cost, before
any gate** — matching the design estimate that the statically-determinable
subset of the 50 is ~6-8 (many of the 50 are the same root seen through
different lenses). The remaining Criticals (synthesis two-chunk drop,
HALF_OPEN race, dead cache) are semantic/concurrency and are the designed
target of WS-C's Gate-3 hunt.

**Two bugs found in the harness itself during this backtest** (the backtest
earning its keep):

1. **A3 missed multi-line env reads.** The canonical `XXKOKORO_BACKEND_URLXX`
   typo lived in `os.environ.get(\n  "KEY", …)`; the per-line scan never saw
   it. Fixed to scan whole-file with offset-derived line numbers
   (regression: `test_multiline_env_read_is_scanned`).
2. **A1 re-raise exemption was too broad.** The actual tts-new Critical was
   `except BaseException: self._on_failure(); raise` — the re-raise did not
   stop the breaker from miscounting `CancelledError`. `BaseException` is now
   flagged unconditionally; the bare-raise exemption applies only to
   non-BaseException handlers (regression:
   `test_base_exception_flagged_even_with_reraise`).

**Coefficients**: error_handling −5/anti-pattern matches the type-error curve;
no threshold changed. A genuine `tts-new` re-run would also surface the
as-shipped meta-gap that **the project had no `.env.example` at all** — A3
skips when no declaration source exists, so the first remediation is to add
one (then the typo blocks).
