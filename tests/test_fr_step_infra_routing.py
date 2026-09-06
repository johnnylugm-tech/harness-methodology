"""Round 13 站2a/2b — HARNESS_BUG/INFRA short-circuit in run-fr-step's
fix-round loop.

Before this round, a [HARNESS-BUG] banner or an R12 INFRA_FAIL
precondition-block signature surfacing in a sub-agent's GATE1 output was
invisible to `cmd_run_fr_step`'s routing decision — the failure fell
through to the generic MISSING_FEATURE/UNKNOWN classification and got a
CODE-FIX sub-agent dispatched at a problem no code change could resolve
(bda76da's incident shape: a harness bug misrouted as a code-quality
failure, burning the whole fix-round budget before a human ever saw the
real cause).

End-to-end cmd_run_fr_step tests would require mocking the entire agent
registry (see test_coverage_fix_fallback.py's precedent) — the unit-level
classifier + abort-helper are the pieces that matter here.
"""

from __future__ import annotations

from pathlib import Path

from cli.exit_codes import EX_FR_STEP_INFRA_ABORT, EX_HARNESS_BUG
from cli.fr_cmds import (
    _abort_dispatch_infra_or_harness_bug,
    _classify_infra_or_harness_bug,
)
from harness.harness_bridge import _INFRA_FAIL_EVIDENCE_SIGNATURES
# Round 100 站1: the new PHANTOM direction.


def test_classify_detects_harness_bug_banner():
    out = "some preamble\n[HARNESS-BUG] ValueError: something broke\nmore text"
    result = _classify_infra_or_harness_bug(out)
    assert result is not None
    cls, evidence = result
    assert cls == "HARNESS_BUG"
    assert "[HARNESS-BUG] ValueError: something broke" == evidence


def test_classify_detects_each_legacy_infra_fail_signature():
    """Round 100 站1. The legacy 4-tuple still classifies everything as either
    INFRA (UNREGISTERED direction) or PHANTOM (SAB→code direction). Each
    signature has its own class now — direction-specific substrings classify
    as PHANTOM, direction-ambiguous as INFRA. The legacy tuple is preserved
    for backward-compat consumers but the per-class semantics are now split."""
    # Direction-specific: classify as PHANTOM.
    for sig in ("phantom module", "Phantom modules"):
        out = f"GATE1 evaluation output:\n{sig}\nscore=0"
        result = _classify_infra_or_harness_bug(out)
        assert result is not None, f"signature {sig!r} was not detected"
        cls, evidence = result
        assert cls == "PHANTOM", f"signature {sig!r} now classifies as PHANTOM (was INFRA)"
        assert evidence == sig
    # Direction-specific UNREG: classify as UNREGISTERED.
    for sig in ("Unregistered modules detected",):
        out = f"GATE1 evaluation output:\n{sig}\nscore=0"
        result = _classify_infra_or_harness_bug(out)
        assert result is not None, f"signature {sig!r} was not detected"
        cls, evidence = result
        assert cls == "UNREGISTERED", f"signature {sig!r} classifies as UNREGISTERED, distinct from PHANTOM"
        assert evidence == sig
    # Direction-ambiguous AAP: classify as UNREGISTERED (fallback default
    # — both gates print it, so it does not disambiguate; UNREG is the more
    # common direction in the corpus).
    for sig in ("Architecture Amendment Protocol violation",):
        out = f"GATE1 evaluation output:\n{sig}\nscore=0"
        result = _classify_infra_or_harness_bug(out)
        assert result is not None, f"signature {sig!r} was not detected"
        cls, evidence = result
        assert cls == "UNREGISTERED"
        assert evidence == sig


def test_classify_returns_none_for_clean_output():
    assert _classify_infra_or_harness_bug("FR-01 GATE1: PASS") is None


def test_classify_returns_none_for_empty_string():
    assert _classify_infra_or_harness_bug("") is None


def test_classify_prioritizes_harness_bug_over_infra_when_both_present():
    out = "[HARNESS-BUG] RuntimeError: x\nUnregistered modules detected: {y}"
    result = _classify_infra_or_harness_bug(out)
    assert result is not None
    cls, _ = result
    assert cls == "HARNESS_BUG"


def test_abort_returns_infra_abort_exit_code(capsys):
    rc = _abort_dispatch_infra_or_harness_bug(
        "FR-01", "GATE1", 3, Path("/tmp/project"), "INFRA", "phantom module"
    )
    assert rc == EX_FR_STEP_INFRA_ABORT
    err = capsys.readouterr().err
    assert "[FATAL]" in err
    assert "not dispatching a fix agent" in err.lower() or "not a code-quality" in err.lower()


def test_a_harness_bug_in_the_subagent_output_exits_as_a_harness_bug(capsys):
    """Round 70 站2. This function computes `cls` — "HARNESS_BUG" or "INFRA" —
    and then discarded it, returning 25 for both. The two have opposite
    remedies: INFRA is a project-state problem the operator fixes with
    `amend-sab` and re-runs, a HARNESS_BUG is this framework's own defect and
    the run must stop. A caller branching on the integer could not tell them
    apart, which is a fifth entry for the "one code, two meanings" list
    `cli/exit_codes.py`'s own docstring keeps.

    70 is not a new code: it is what `harness_cli.py`'s crash boundary already
    returns for a [HARNESS-BUG] banner it printed itself. A banner arriving via
    a sub-agent's output is the same fact reported by a different route."""
    rc = _abort_dispatch_infra_or_harness_bug(
        "FR-01", "GATE1", 3, Path("/tmp/project"), "HARNESS_BUG", "[HARNESS-BUG] x"
    )
    assert rc == EX_HARNESS_BUG, (
        f"a HARNESS_BUG class returned {rc} — the same code an INFRA "
        "precondition failure returns, and the two need opposite responses"
    )
    err = capsys.readouterr().err
    assert "[FATAL]" in err
    assert "a bug in harness-methodology itself" in err


def test_the_two_classes_do_not_share_one_exit_code(capsys):
    """The property, stated once: whatever the codes are, they differ."""
    infra = _abort_dispatch_infra_or_harness_bug(
        "FR-01", "GATE1", 3, Path("/tmp/p"), "INFRA", "phantom module"
    )
    harness_bug = _abort_dispatch_infra_or_harness_bug(
        "FR-01", "GATE1", 3, Path("/tmp/p"), "HARNESS_BUG", "[HARNESS-BUG] x"
    )
    capsys.readouterr()
    assert infra != harness_bug


def test_abort_message_distinguishes_infra_from_harness_bug(capsys):
    _abort_dispatch_infra_or_harness_bug(
        "FR-01", "GATE1-DELTA", 4, Path("/tmp/project"), "INFRA", "phantom module"
    )
    err = capsys.readouterr().err
    assert "infrastructure precondition failure" in err
    assert "phantom module" in err


def test_abort_message_includes_resume_command(capsys):
    _abort_dispatch_infra_or_harness_bug(
        "FR-02", "GATE1", 5, Path("/tmp/project"), "HARNESS_BUG", "[HARNESS-BUG] y"
    )
    err = capsys.readouterr().err
    assert "resume-fr-step" in err
    assert "--phase 5" in err
    assert "--fr-id FR-02" in err


# Round 100 站1: PHANTOM direction is its own code (45) and its own
# remediation channel (`amend-sab --resolve-phantom ... --reason`).
def test_a_phantom_class_exits_with_a_distinct_code(capsys):
    """Round 100 站1. PHANTOM and UNREG are now distinct classes producing
    distinct codes (45 vs 25) — Round 25's rule that opposite remediation
    channels get distinct codes holds. HARNESS_BUG stays 70."""
    from cli.exit_codes import EX_FR_STEP_PHANTOM_ABORT
    phantom = _abort_dispatch_infra_or_harness_bug(
        "FR-01", "GATE1", 3, Path("/tmp/p"), "PHANTOM", "Phantom modules"
    )
    err = capsys.readouterr().err
    assert phantom == EX_FR_STEP_PHANTOM_ABORT, (
        f"PHANTOM class returned {phantom}, expected {EX_FR_STEP_PHANTOM_ABORT} (45)"
    )
    assert "[FATAL]" in err
    assert "PHANTOM" in err
    assert "not a code-quality problem" in err


def test_the_three_classes_have_three_distinct_codes(capsys):
    """Same property as `test_the_two_classes_do_not_share_one_exit_code`,
    extended to three classes: HARNESS_BUG / UNREGISTERED / PHANTOM get
    codes 70 / 25 / 45."""
    harness_bug = _abort_dispatch_infra_or_harness_bug(
        "FR-01", "GATE1", 3, Path("/tmp/p"), "HARNESS_BUG", "[HARNESS-BUG] x"
    )
    unregistered = _abort_dispatch_infra_or_harness_bug(
        "FR-01", "GATE1", 3, Path("/tmp/p"), "UNREGISTERED", "Unregistered modules detected: foo"
    )
    phantom = _abort_dispatch_infra_or_harness_bug(
        "FR-01", "GATE1", 3, Path("/tmp/p"), "PHANTOM", "Phantom modules declared"
    )
    capsys.readouterr()
    assert harness_bug != unregistered != phantom
    assert harness_bug != phantom


def test_phantom_abort_message_names_resolve_phantom_channel(capsys):
    """The PHANTOM abort message names the operator command that resolves
    it, distinct from the plain `amend-sab` of the UNREG channel."""
    _abort_dispatch_infra_or_harness_bug(
        "FR-01", "GATE1", 3, Path("/tmp/project"), "PHANTOM", "Phantom modules declared"
    )
    err = capsys.readouterr().err
    assert "--resolve-phantom" in err
    assert "--reason" in err
    assert "amend-sab" in err
