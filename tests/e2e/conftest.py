"""Shared fixture for the black-box CLI journey suite (弱點強化 C3).

Every test here drives the REAL CLI as a subprocess against a REAL tmp git
repo — no monkeypatching of project internals (tests/test_patch_discipline.py
ratchets that style). The fixture builds a P1-complete methodology project
the way the real pipeline would leave it: deliverables per
scripts/phase_auditor.PHASE_SPEC[1], substantive Agent-B approvals per
core/quality_gate/agent_b_approvals, and a fresh trace attestation built by
the real CLI. Journeys assert on exit codes, output, git history and
on-disk state only.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS_CLI = Path(__file__).resolve().parents[2] / "harness_cli.py"

# Round 34 站2: the H1 of every anchored deliverable is now an invariant checked
# on every advance, so this golden-path fixture has to satisfy it the way a real
# project does. The anchors are interpolated from DELIVERABLE_ANCHORS rather
# than written out, so a registry change moves the fixture with it — the same
# reason spec_phase1.py stopped hand-writing its diskPrefix literals.
from core.quality_gate.legal_artifacts import anchor_for  # noqa: E402

_SRS = f"""{anchor_for("SRS.md")} — e2e fixture

## Functional Requirements

### FR-01: first requirement
The system shall do thing one. Logic Verification Method: unit test.

### FR-02: second requirement
The system shall do thing two. Logic Verification Method: unit test.

### FR-03: third requirement
The system shall do thing three. Logic Verification Method: unit test.
"""

_SPEC_TRACKING = f"""{anchor_for("SPEC_TRACKING.md")} — e2e fixture

| FR ID | Description | Status |
|---|---|---|
| FR-01 | first | Pending |
| FR-02 | second | Pending |
| FR-03 | third | Pending |
"""

_TRACEABILITY = f"""{anchor_for("TRACEABILITY_MATRIX.md")} — e2e fixture

| FR | Module |
|---|---|
| FR-01 | TBD |
| FR-02 | TBD |
| FR-03 | TBD |
"""

_P1_DELIVERABLE_IDS = (
    "SRS.md", "SPEC_TRACKING.md", "TRACEABILITY_MATRIX.md", "TEST_INVENTORY.yaml",
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
    )


def _cli(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HARNESS_CLI), *args, "--project", str(project)],
        capture_output=True, text=True, timeout=120,
    )


@pytest.fixture
def e2e_project(tmp_path):
    """P1-complete real methodology project, one advance away from Phase 2."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _git(proj, "init")
    _git(proj, "config", "user.email", "e2e@example.com")
    _git(proj, "config", "user.name", "e2e")
    _git(proj, "config", "core.hooksPath", ".git/hooks")

    meth = proj / ".methodology"
    meth.mkdir()
    (meth / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": 1}) + "\n",
        encoding="utf-8",
    )
    (meth / "phase1_plan.md").write_text("# Phase 1 plan\n", encoding="utf-8")
    (proj / "CLAUDE.md").write_text("# Project: e2e\n", encoding="utf-8")

    req = proj / "01-requirements"
    req.mkdir()
    (req / "SRS.md").write_text(_SRS, encoding="utf-8")
    (req / "SPEC_TRACKING.md").write_text(_SPEC_TRACKING, encoding="utf-8")
    (req / "TRACEABILITY_MATRIX.md").write_text(_TRACEABILITY, encoding="utf-8")
    (proj / "TEST_INVENTORY.yaml").write_text(
        f'{anchor_for("TEST_INVENTORY.yaml")} — e2e fixture\ntests: []\n',
        encoding="utf-8",
    )

    approvals = meth / "agent_b_approvals"
    approvals.mkdir()
    for did in _P1_DELIVERABLE_IDS:
        (approvals / f"{did}.json").write_text(json.dumps({
            "fr": did,
            "review_status": "APPROVE",
            "docs_embedded": ["SRS.md"],
            "confidence": 0.9,
            "reason": (
                "Reviewed against the SRS baseline: structure, FR coverage "
                "and status columns are complete and consistent."
            ),
            "citations": ["01-requirements/SRS.md:3"],
        }) + "\n", encoding="utf-8")

    _git(proj, "add", "-A")
    assert _git(proj, "commit", "-m", "baseline").returncode == 0

    # Round 39: cmd_advance_phase now calls _verify_entry_gate(2) before
    # _advance_fsm. Seed phase_completed[1] with the baseline SHA so the
    # gate passes naturally (mirror of an actual P1-complete project).
    # The SHA is overwritten by advance-phase's post-commit writer
    # afterwards, so this seed is invisible after the handover commit.
    _baseline_sha = _git(proj, "rev-parse", "HEAD").stdout.strip()
    sd = json.loads((meth / "state.json").read_text(encoding="utf-8"))
    sd["phase_completed"] = {
        "1": {
            "sha": _baseline_sha,
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
    }
    (meth / "state.json").write_text(
        json.dumps(sd) + "\n", encoding="utf-8",
    )
    _git(proj, "add", ".methodology/state.json")
    _git(proj, "commit", "-m", "seed phase_completed[1] (Round 39 fixture)")

    # Fresh trace attestation via the real CLI, AFTER all files exist so the
    # mtime probe (_trace_dirty_state) sees it as current.
    att = _cli(proj, "build-trace-attestation", "--write")
    assert att.returncode == 0, att.stdout + att.stderr
    return proj
