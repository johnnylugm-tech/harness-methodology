"""Round 22 — _advance_prechecks's [BLOCKED] pragma guidance must bind the
same SSOT preflight_reliability_lint enforces (core.phase_hooks.
PRAGMA_NO_COVER_ALLOWLIST / PRAGMA_NO_COVER_GUIDANCE), the same class of
drift test_prompt_gate_parity.py's #18B binding already closed for the
GATE1 COVERAGE-FIX prompt (cli/fr_prompts/fix.py). That fix never covered
this second call site: _advance_prechecks's own pytest-coverage [BLOCKED]
message (invoked by cmd_advance_phase) told agents "add # pragma: no
cover" with no allowlist mention, so an agent following it verbatim (as
the advance-loop prompt explicitly instructs) produces a pragma that
passes advance-phase but gets blocked by preflight_reliability_lint at
push time — see taskq Phase 4 workflow wf_8b3a3f79-12b, SYNC: FAIL on
breaker.py's two `except OSError` pragmas.
"""




def test_advance_prechecks_blocked_message_binds_pragma_allowlist_ssot():
    """Delegation check (same style as test_prompt_gate_parity.py's
    _check_spec_parser): the [BLOCKED] message must be BUILT FROM the SSOT
    symbols, not a hand-copied string that can independently drift from
    what preflight_reliability_lint actually enforces.
    """
    # Round 81 站6: the precheck pipeline is `_advance_prechecks` plus the
    # `_precheck_*` helpers extracted from it. Reading only the caller now
    # answers a question this test never meant to ask.
    from tests.support.pipeline import pipeline_source
    src = pipeline_source("cli/phase_cmds.py", "_advance_prechecks",
                          helper_prefix="_precheck_")
    assert "PRAGMA_NO_COVER_GUIDANCE" in src, (
        "_advance_prechecks's TDD test/coverage [BLOCKED] message must render "
        "core.phase_hooks.PRAGMA_NO_COVER_GUIDANCE, not a hand-written pragma "
        "hint — a hand-written hint drifts from the allowlist "
        "preflight_reliability_lint actually enforces (#18B bug class, "
        "second occurrence)."
    )
    assert "PRAGMA_NO_COVER_ALLOWLIST" in src, (
        "_advance_prechecks's [BLOCKED] message must render "
        "core.phase_hooks.PRAGMA_NO_COVER_ALLOWLIST, not a hand-written list "
        "of exemptions — any future change to the allowlist must propagate "
        "here automatically."
    )
