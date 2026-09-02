"""Round 72 站1 — advance-phase may not gate on the record it is about to write.

Two of this framework's own fixes met inside `cmd_advance_phase` and deadlocked:

  * Round 24 站4a made advance-phase the writer of
    `state.json.phase_completed[N]`, and wrote it AFTER the handover commit so
    the entry's `sha` can be that commit (every consumer passes it to
    `git merge-base --is-ancestor`).
  * Round 53 站5c gave the P4+ entry gate a second condition: phase N must have
    left a `phase_completed` record before phase N+1 may be entered.

`cmd_advance_phase` calls `_verify_entry_gate(project, completed_phase + 1)`
before it commits, so from `--completed 3` onward the gate asks for a record
this same call has not written yet. It is absent on every FIRST advance out of
a phase, and the command exits 10.

Measured on taskq-new, the only project to run P4+ after Round 53 站5c landed
(2026-08-16). It shipped SIX hand-written `phase_completed` entries to get
past it — c15966f, 236591b, 2e47de6, 5e2dee8, 7c3542b, 8187dd4 — the last of
which still reads:

    "8": {"sha": "PLACEHOLDER_WILL_BE_REPLACED_ON_ADVANCE",
          "delivered_tree_sha256": "PLACEHOLDER"}

`delivered_tree_sha256` is Round 44 站2's invariant — WHICH TREE the checks
read — satisfied here by a literal string, which is why the second half of
this station checks the record's CONTENT and not merely its presence.

Nothing caught it because the suites that reach this code path seed the record
themselves: tests/test_handover_generator.py:1411 and :1551 write
`{n: {"sha": "0"*40} for n in range(1, 4)}` while completing phase 3 — the
range's upper bound is the phase being completed — under a comment reading
"Seeded rather than exercised". The framework's tests performed the same
forgery taskq-new did by hand.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from cli import phase_cmds

pytestmark = [pytest.mark.core]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
    )


@pytest.fixture
def project_leaving_phase_3(tmp_path, monkeypatch):
    """A tmp repo that has just finished phase 3 and has never advanced.

    `phase_completed` is EMPTY on purpose: that is the state every project is
    in the first time it leaves a phase, and the state the deadlock needs.
    Prechecks and the next-phase obligation preview are stubbed for the same
    reason tests/test_advance_commit_rollback.py stubs them — they are not
    what this file is about. `_verify_entry_gate` is NOT stubbed; it is.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    _git(proj, "init")
    _git(proj, "config", "user.email", "t@example.com")
    _git(proj, "config", "user.name", "t")
    _git(proj, "config", "core.hooksPath", ".git/hooks")

    meth = proj / ".methodology"
    meth.mkdir()
    (meth / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": 3,
                    "last_gate": 2, "phase_truth_passed": True}) + "\n"
    )
    (meth / "quality_manifest.json").write_text(
        json.dumps({"gate_results": {"gate2": {"quality_complete": True,
                                               "score": 96.5}}}) + "\n"
    )
    (proj / "CLAUDE.md").write_text("# Project\n")
    _git(proj, "add", "-A")
    assert _git(proj, "commit", "-m", "baseline").returncode == 0

    # advance-phase refuses to leave a phase whose exit gate has no recorded
    # PASS for the tree in hand (Round 38). Written through the real recorder
    # so the tree digests it stores are the ones the check will compare.
    from core.quality_gate.gate_verify import record_verdict
    record_verdict(proj, 2, 3, {"last_gate_ok": True, "spec_coverage_rc": 0,
                                "crg_rc": 0}, "PASS")

    monkeypatch.setattr(phase_cmds, "_advance_prechecks", lambda *_a, **_k: 0)
    monkeypatch.setattr(
        "core.phase_hooks.PhaseHooks.preview_next_phase_blocking",
        lambda _self, _next_phase: [],
    )
    monkeypatch.delenv("HARNESS_NO_GIT", raising=False)
    return proj


def _advance(proj: Path, completed: int) -> int:
    return phase_cmds.cmd_advance_phase(
        argparse.Namespace(project=str(proj), completed_phase=completed)
    )


def test_the_first_advance_out_of_a_phase_is_not_blocked_by_its_own_record(
    project_leaving_phase_3,
):
    """The entry gate may not demand what this very call is about to write."""
    proj = project_leaving_phase_3
    rc = _advance(proj, 3)
    assert rc == 0, (
        "advance-phase refused to leave phase 3 because "
        "state.json.phase_completed[3] is absent — but writing that entry is "
        f"this command's own job, and it happens after the commit. rc={rc}"
    )


def test_the_record_it_writes_names_the_commit_and_the_tree(
    project_leaving_phase_3,
):
    """Round 24 站4a's sha and Round 44 站2's digest, both real."""
    from core.utils.delivery_scope import committed_tree_digest

    proj = project_leaving_phase_3
    assert _advance(proj, 3) == 0
    entry = json.loads(
        (proj / ".methodology" / "state.json").read_text()
    )["phase_completed"]["3"]
    # Round 90: the record is committed by a second commit right after the
    # handover, so HEAD is no longer the commit the entry names. Both facts
    # are unchanged — they are asserted against that commit by name.
    handover = _git(proj, "log", "-1", "--format=%H",
                    "--grep=^handover: advance to Phase").stdout.strip()
    assert handover, "no handover commit found"
    assert entry["sha"] == handover
    assert entry["delivered_tree_sha256"] == committed_tree_digest(proj, handover)


def test_a_placeholder_record_is_named_as_a_defect(project_leaving_phase_3):
    """taskq-new's phase-8 entry, verbatim. Presence is not truth."""
    from core.harness_provenance import phase_record_defects

    proj = project_leaving_phase_3
    defects = phase_record_defects(
        proj,
        {"sha": "PLACEHOLDER_WILL_BE_REPLACED_ON_ADVANCE",
         "delivered_tree_sha256": "PLACEHOLDER"},
    )
    assert defects, (
        "a record whose sha is the word PLACEHOLDER satisfied every check — "
        "Round 44 站2's invariant is which TREE the checks read, and a literal "
        "string answers that question with nothing"
    )
    # Both fields are wrong and the report must say so about each of them
    # SEPARATELY. The first version of this assertion was
    # `"sha" in joined and "delivered_tree_sha256" in joined`, which the
    # counter-proof showed passes with the sha branch switched off entirely:
    # the substring "sha" is inside "delivered_tree_sha256".
    assert len(defects) == 2, f"expected one defect per field, got {defects}"
    assert any(d.startswith("sha=") for d in defects), defects
    assert any(d.startswith("delivered_tree_sha256=") for d in defects), defects


def test_a_genuine_record_has_no_defects(project_leaving_phase_3):
    """The counter-direction: the entry advance-phase itself writes is clean."""
    from core.harness_provenance import phase_record_defects

    proj = project_leaving_phase_3
    assert _advance(proj, 3) == 0
    entry = json.loads(
        (proj / ".methodology" / "state.json").read_text()
    )["phase_completed"]["3"]
    assert phase_record_defects(proj, entry) == []


def test_run_phase_still_refuses_a_phase_that_left_no_record(
    project_leaving_phase_3,
):
    """Round 53 站5c's purpose survives: the OTHER callers still enforce it.

    taskq-super reached Phase 9 with no entry for phase 5. `cmd_run_phase`,
    `cmd_pre_commit_check` and advance's re-verify mode all ask about a phase
    that is already finished, so for them the record's absence is a real
    finding — one step later than advance-phase, and still a stop.
    """
    proj = project_leaving_phase_3
    result = phase_cmds._verify_entry_gate(proj, 4)
    assert result["passed"] is False
    assert "phase_completed[3]" in result["reason"]
