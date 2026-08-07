"""A simulation must not repair the thing it is simulating.

Round 43 站0/站1. `PhaseHooks.preview_next_phase_blocking`'s docstring says it
can ask "what would block if I entered P(N+1) right now" *"without mutating any
state"*. It runs `_do_preflight_all` under a sibling `PhaseHooks(phase=N+1)`,
and one of the fifteen preflights that loop runs was not a check:

    core/phase_hooks.py — preflight_traceability, before Round 43
        if blocking and not passed and (untested_list or uncoded_list):
            _dispatch_trace_auto_fix(...)      # AutoFixEngine writes files

`blocking` there is `phase >= 5`. So every `advance-phase --completed 4` (and
5, 6, 7) whose project still had an FR→code or FR→test gap dispatched a repair
against the real project tree while claiming to be a preview, and the
attestation refresh that follows wrote `attestation.json` as well.

Fourteen preflights read. One wrote. That asymmetry — not a flag on the
instance — was the defect: a check that repairs cannot be run for an answer.
The repair is legitimate and stays wired for `run-phase`; it moved to
`PhaseHooks.repair_traceability_gap`, which the command calls.

Two independent signals, both public: the repair entry point is not called,
and the project tree comes out byte-identical. The first alone could be
satisfied by a check that writes some other way; the second alone could pass
if the engine happened to write nothing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from core.phase_hooks import PhaseHooks


# The degradation ledger is an append-only audit log, not project state: when
# a preflight genuinely degrades during the simulation (e.g. the drift
# detector falling back to an empty architecture baseline), the degradation
# really happened and Round 13's rule is that it must not be silently
# forgotten. Recording it is not the class of write this test is about — the
# defect was a CHECK REPAIRING THE ARTIFACTS IT MEASURES. Whether a preview
# should append to the project's ledger at all is a separate question; it is
# recorded in docs/PROPOSAL_ADJUDICATIONS.md rather than narrowed silently.
_AUDIT_LOG_SUFFIXES = (".jsonl",)


def _tree_fingerprint(root: Path) -> "list[tuple[str, str]]":
    out: list[tuple[str, str]] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix in _AUDIT_LOG_SUFFIXES:
            continue
        out.append((
            str(p.relative_to(root)),
            hashlib.sha256(p.read_bytes()).hexdigest(),
        ))
    return out


def _traceability_gap_project(tmp_path) -> Path:
    """A real project with an FR that has code and no test.

    Same shape as tests/test_phase_hooks_adapter.py's overlay fixture: the
    scanner reads SAD.md for the FR list and the source tree for the links.
    """
    arch = tmp_path / "02-architecture"
    arch.mkdir(parents=True)
    (arch / "SAD.md").write_text(
        "FR-07: implemented\nFR-08: implemented, untested\n", encoding="utf-8",
    )
    src = tmp_path / "core"
    src.mkdir()
    (src / "feat.py").write_text('"""[FR-07]""" def feat(): pass\n', encoding="utf-8")
    (src / "pending.py").write_text('"""[FR-08]""" def pending(): pass\n', encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / ".methodology").mkdir()
    return tmp_path


class _RepairRecorder:
    """Stand-in for the public repair entry point."""

    def __init__(self):
        self.calls: list = []

    def __call__(self, _self, untested, uncoded):
        self.calls.append((list(untested), list(uncoded)))
        return False


def test_the_preview_dispatches_no_repair(tmp_path, monkeypatch):
    project = _traceability_gap_project(tmp_path)
    recorder = _RepairRecorder()
    monkeypatch.setattr(PhaseHooks, "repair_traceability_gap", recorder)

    before = _tree_fingerprint(project)
    hooks = PhaseHooks(str(project), phase=4, enable_kill_switch=False)
    hooks.preview_next_phase_blocking(5)

    assert recorder.calls == [], (
        f"preview_next_phase_blocking asked for a repair ({recorder.calls}). "
        "Its docstring promises the simulation mutates no state."
    )
    assert _tree_fingerprint(project) == before, (
        "preview_next_phase_blocking changed the project tree it was asked "
        "only to measure"
    )


def test_the_traceability_check_itself_dispatches_no_repair(tmp_path, monkeypatch):
    """The check reports; the caller repairs.

    `run-phase` keeps the bounded repair attempt — see
    `cli/phase_cmds.py::cmd_run_phase` — but it belongs to the command, not to
    the check whose answer three other callers read.
    """
    project = _traceability_gap_project(tmp_path)
    recorder = _RepairRecorder()
    monkeypatch.setattr(PhaseHooks, "repair_traceability_gap", recorder)

    before = _tree_fingerprint(project)
    hooks = PhaseHooks(str(project), phase=5, enable_kill_switch=False)
    result = hooks.preflight_traceability()

    assert result["passed"] is False, (
        "fixture premise: FR-08 has code and no test, blocking at P5"
    )
    assert recorder.calls == [], (
        "preflight_traceability repaired the tree it was asked to measure"
    )
    assert _tree_fingerprint(project) == before


def test_the_repair_entry_point_exists_and_is_public(tmp_path):
    """Guard the guard: patching a name that no longer exists reads as a pass."""
    assert callable(getattr(PhaseHooks, "repair_traceability_gap", None)), (
        "the tests above patch PhaseHooks.repair_traceability_gap; if the "
        "repair moves again they must move with it"
    )
