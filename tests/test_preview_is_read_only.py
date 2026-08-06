"""A simulation must not repair the thing it is simulating.

Round 43 站0. `PhaseHooks.preview_next_phase_blocking`'s docstring says it can
ask "what would block if I entered P(N+1) right now" *"without mutating any
state"*. It runs `_do_preflight_all` under a sibling `PhaseHooks(phase=N+1)`,
and one of the fifteen preflights that loop runs is not a check:

    core/phase_hooks.py — preflight_traceability
        if blocking and not passed and (untested_list or uncoded_list):
            _fixed = _dispatch_trace_auto_fix(...)   # AutoFixEngine writes files

`blocking` there is `phase >= 5`. So every `advance-phase --completed 4` (and
5, 6, 7) whose project still has an FR→code or FR→test gap dispatches a
repair against the real project tree while claiming to be a preview, and the
attestation refresh that follows writes `attestation.json` as well.

Fourteen preflights read. One writes. That asymmetry — not the flag on the
instance — is the defect: a check that repairs cannot be run for an answer.
The repair itself is legitimate and stays wired for `run-phase`; it just does
not belong inside the function whose result the preview reads.

The test drives the preview, not `preflight_traceability`, because the
contract being pinned is the preview's — a later refactor that keeps the
repair in the check but hides it behind a flag would still be a check that
writes, and this test would still be the one that has to pass.
"""

from __future__ import annotations

import core.phase_hooks as ph
from core.phase_hooks import PhaseHooks


def _traceability_gap_project(tmp_path, monkeypatch):
    """A project whose traceability scan reports an unclosed FR→test gap."""
    (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)

    report = {
        "total": 2,
        "untested": ["FR-02"],
        "uncoded": [],
        "completeness": {"code_coverage": "100%", "test_coverage": "50%"},
        "ghost_frs": [],
    }
    monkeypatch.setattr(
        "core.traceability.scanner.check_traceability",
        lambda _p: ({}, report),
    )
    monkeypatch.setattr(
        "core.traceability.scanner._find_sad",
        lambda _p: tmp_path / "02-architecture" / "SAD.md",
    )
    return tmp_path


def test_the_preview_dispatches_no_repair(tmp_path, monkeypatch):
    """preview_next_phase_blocking must not call the auto-fix engine."""
    project = _traceability_gap_project(tmp_path, monkeypatch)

    dispatched: list = []

    def _recording_dispatch(project_path, untested, uncoded, phase=None):
        dispatched.append({"untested": list(untested), "phase": phase})
        return False

    monkeypatch.setattr(ph, "_dispatch_trace_auto_fix", _recording_dispatch)

    hooks = PhaseHooks(str(project), phase=4, enable_kill_switch=False)
    hooks.preview_next_phase_blocking(5)

    assert dispatched == [], (
        "preview_next_phase_blocking dispatched an auto-fix "
        f"({dispatched}). Its docstring promises the simulation mutates no "
        "state; AutoFixEngine writes annotations and test stubs into the "
        "project tree."
    )


def test_the_traceability_check_itself_dispatches_no_repair(tmp_path, monkeypatch):
    """The check reports; the caller repairs.

    `run-phase` keeps the bounded repair attempt — see
    `cli/phase_cmds.py::cmd_run_phase` — but it belongs to the command, not to
    the check whose answer three other callers read.
    """
    project = _traceability_gap_project(tmp_path, monkeypatch)

    dispatched: list = []
    monkeypatch.setattr(
        ph, "_dispatch_trace_auto_fix",
        lambda *a, **k: (dispatched.append(a), False)[1],
    )

    hooks = PhaseHooks(str(project), phase=5, enable_kill_switch=False)
    result = hooks.preflight_traceability()

    assert result["passed"] is False
    assert dispatched == [], (
        "preflight_traceability repaired the tree it was asked to measure"
    )
