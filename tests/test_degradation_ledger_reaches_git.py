"""Round 113 站8 — the degradation ledger is evidence, so the advance commits it.

`core/degradation_ledger.py` says the ledger lives "beside the other
.methodology artefacts a consuming project commits", and
`harness/ssot_manifest.unfinished_scaffolded_manifest` reads it as its first
proof that the harness — not the project — wrote `requirements.txt`. Yet the
advance commit staged it nowhere: on taskq-sol the P2 exit wrote two rows
(21:20:03, 21:20:29), the `handover: advance to Phase 3` commit landed at
21:20:29, and the ledger stayed `??` in the working tree. A clone had no rows.

The fix follows `.methodology/gate_timestamps.jsonl`, the existing precedent:
stay volatile (an append must not move the delivered-tree digest — 2245e64
measured that a verdict otherwise never matches itself) and be staged by the
advance commit when the file exists (an explicit `git add` of a missing path
fails the whole commit; 8 of 23 corpus projects have no ledger).
"""

from __future__ import annotations

import json

from cli.phase_cmds import _advance_commit_targets
from core.utils.delivery_scope import is_harness_volatile
from tests.test_advance_commit_rollback import _advance, _git, advance_project  # noqa: F401

_LEDGER = ".methodology/degradations.jsonl"


def test_the_ledger_is_staged_when_it_exists():
    targets = _advance_commit_targets(
        2, 3, manifest_regenerated=False, fr_progress_exists=False,
        degradation_ledger_exists=True,
    )
    assert _LEDGER in targets


def test_a_missing_ledger_is_not_staged():
    targets = _advance_commit_targets(
        1, 2, manifest_regenerated=False, fr_progress_exists=False,
        degradation_ledger_exists=False,
    )
    assert _LEDGER not in targets


def test_the_ledger_stays_volatile():
    # Staging it must not mean digesting it: 2245e64.
    assert is_harness_volatile(_LEDGER)


def test_an_advance_commits_rows_written_before_it(advance_project):  # noqa: F811
    proj = advance_project
    row = {"ts": 1.0, "component": "test", "what": "a row before the advance"}
    (proj / _LEDGER).write_text(json.dumps(row) + "\n")

    assert _advance(proj) == 0

    tracked = _git(proj, "ls-files", _LEDGER).stdout.strip()
    assert tracked == _LEDGER, "the advance left the ledger out of git"
    at_head = _git(proj, "show", f"HEAD:{_LEDGER}").stdout
    assert "a row before the advance" in at_head
