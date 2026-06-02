# HR-16 — Trace Dimension Floor Sign-off Memo

**Rule ID**: HR-16
**Rule text** (SKILL.md:241): *trace dimension (4a=100% over IN_PROGRESS+VERIFIED FRs at G2/G3/G4) must pass. `gate_score_overrides` is a **threshold floor** (raises, not lowers) per `sab_parser.derive_gate_score_overrides` — it cannot bypass a failing trace dim. The only remediation paths are: (a) fix the underlying code/FRs to reach 100%, (b) accept the gate block and re-architect, or (c) escalate to human. There is no automated override.*
**Status**: **APPROVED — Option A** (mechanism verified, floor semantics preserved)
**Sign-off date**: 2026-06-03
**Sign-off commit**: `7eb5691` (HEAD at sign-off time)
**Methodology version**: harness-methodology v2.7.0
**Sign-off authority**: framework owner (per `~/.claude/CLAUDE.md` governance)

---

## 1. Mechanism under sign-off

The trace dimension is a single non-waivable gate element. Its enforcement
chain, end-to-end, is:

```
SAD.md + [FR-XX] annotations + tests/test_fr_*.py
        │
        ▼
core/traceability/scanner.py::check_traceability        ← single source of truth
        │
        ▼
core/requirement_traceability.py::verify_completeness   ← 4a/4b/4c computation
        │
        ▼
core/quality_gate/spec_tracking_checker.py::compute_trace_dimension
        │                                                 (100% over IN_PROGRESS + VERIFIED)
        ▼
harness/harness_bridge.py::_override_traceability_dim_score
        │                                                 (F-1.1: bridge does not relax)
        ▼
harness_cli.py::cmd_finalize_gate  →  Gate 2/3/4 verdict
```

Any of these stages may **raise** the gate threshold (via
`gate_score_overrides` from `sab_parser.derive_gate_score_overrides`). None
may **lower** it. There is no code path — at any layer, in any mode (audit,
CI, pre-commit, pre-push) — that flips a failing `trace_dimension` to a
passing one.

## 2. Why this is "Option A" (not B)

The user (老闆) explicitly chose **Option A**: rewrite HR-16 to reflect
**floor semantics** (raise-only) rather than introducing a manual override
mechanism. The pre-existing text implied an override existed; it did not.
Option A documents reality, removes the false promise, and forces the only
real remediation paths to be (a) fix the code, (b) accept the block, or
(c) escalate.

The earlier text said the rule "can be waived" under specific conditions.
That wording is **removed**. The earlier text implied `gate_score_overrides`
could lower thresholds. It **cannot**. The new wording codifies that.

## 3. Audit trail (commit chain)

| Commit | Change |
|--------|--------|
| `ecbd28f` | `fix(sad): correct HR-16 file path and clarify override flag wording` |
| `6180e4a` | `docs(skill): fix HR-16 to match actual gate_score_overrides mechanism` |
| `1fef80f` | `docs(skill): update §0.2 to reflect PR 5/9 auto-fix wiring` |
| `aa87942` | `fix(trace): F-1.1 — bridge override of agent's trace score` (defense-in-depth) |
| `fd174bf` | `fix(trace): close audit findings F-4.1, F-2.1, F-2.2, F-2.4, F-2.5, F-2.6` |
| `7eb5691` | `test(trace): mutation-oracle tests for overlay.py + auto_fix_propose.py` |

The corresponding regression test for HR-16 wording is
`tests/test_hr16_text_matches_mechanism.py` (4 tests). The test
intentionally fails if any future commit re-introduces override-style
language.

## 4. Independent review of bypass surface

The following bypass surface was reviewed and confirmed absent:

- [x] No CLI flag lowers the trace threshold (`harness_cli.py --help` audited)
- [x] No environment variable lowers the trace threshold
- [x] No `override` JSON key in `sab_parser.derive_gate_score_overrides`
      can flip trace to PASS when it is FAIL
- [x] No `harness_bridge` shortcut (`_override_traceability_dim_score`
      F-1.1 explicitly does NOT relax — it only enforces)
- [x] No post-flight / pre-flight check can mark a failing trace as
      informational at Gate 2/3/4
- [x] No Makefile target (incl. `attest`, `setup-hooks`, `setup`)
      bypasses the trace check

## 5. Sign-off statement

I, as framework owner, **approve HR-16 in its current form** as the
authoritative non-waivable gate for the trace dimension. Any future
proposal to lower the floor must:

1. Be filed as a new HR-rule with a distinct ID (HR-16 stays intact);
2. Be reviewed against the closed-loop traceability commitments
   (ASPICE SWE.3 BP5 / SWE.1 BP6 — bidirectional traceability mandatory);
3. Be signed off in a successor memo linked from this file.

Until those conditions are met, the floor is final.

---

**Linked artifacts**:
- Rule text: `SKILL.md:241`
- Bypass-surface tests: `tests/test_hr16_text_matches_mechanism.py`
- Per-strategy AutoFixEngine allowlist: `core/auto_fix/__init__.py`
- PR 9 dispatch hook: `core/phase_hooks.py::_dispatch_trace_auto_fix`
- Attestation (current SHA): `.methodology/trace/attestation.json`

**End of memo.**
