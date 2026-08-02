"""Round 30 站4 — enforcer_surface, and the reader Round 29 did not give it.

Round 19 站3 made every verdict record the harness commit that produced it.
That identifier is MUTABLE. taskq-advance's 8 Gate 1 results, its Gate 2 result
and both `state.json.phase_completed` entries all cite `01bb3bb4`; a rebase of
the harness submodule on 2026-08-02 left it reachable from nothing:

    git merge-base --is-ancestor 01bb3bb4 main  → NO
    git branch -a --contains 01bb3bb4           → (empty)

Round 29 站4 added `enforcer_surface` for exactly this — git object IDs for the
three paths that produce verdicts, which survive a rebase. Measured on the real
history:

    commit      core/quality_gate  harness_bridge.py  gate_configs
    01bb3bb4    99ba0a38           1c5a000f           6800e4b4   (orphaned)
    7154768     99ba0a38           1c5a000f           6800e4b4   (its replacement)
    c5971cd     36d32b5d           1c5a000f           6800e4b4   (pre-fix base)

Identical across the rebase, and correctly different from the base. (The naive
alternative, the commit's own tree hash, does NOT survive: 7f19c4f4 vs 9e72df80
for the same two commits, because the rebase moved them onto a different base.)

Round 29 wrote the field into gate results and state.json and gave it no reader
and no test — `grep -rn enforcer_surface tests/` returned nothing, so its own
listed counter-proof ("remove enforcer_surface → the reconciliation test goes
red") could not fire. This file is that test.
"""
from __future__ import annotations

import json

import pytest

import harness_cli  # noqa: F401  entry-first load order
from core.doctor import _check_enforcer_provenance, _enforcer_shas_in  # noqa: E402
from core.harness_provenance import (  # noqa: E402
    ENFORCER_SURFACE_PATHS,
    enforcer_surface,
)

pytestmark = [pytest.mark.core]

# A 40-hex SHA that git will never resolve.
_ORPHANED = "0" * 39 + "1"


def _method(tmp_path):
    (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ── the surface itself ──────────────────────────────────────────────────

def test_surface_covers_the_paths_that_produce_verdicts():
    surface = enforcer_surface()
    assert set(surface) == set(ENFORCER_SURFACE_PATHS)
    assert surface, "an empty surface answers nothing"


def test_surface_ids_are_git_object_ids_not_the_commit():
    """Object IDs, so they track CONTENT. The commit SHA is what goes stale."""
    from core.harness_provenance import enforcer_sha

    surface = enforcer_surface()
    resolved = [v for v in surface.values() if v != "unknown"]
    assert resolved, "no path resolved — the surface would be decorative"
    for value in resolved:
        assert len(value) == 40 and all(c in "0123456789abcdef" for c in value)
        assert value != enforcer_sha().removesuffix("-dirty"), (
            "a path's object ID equal to the commit SHA means it is tracking "
            "the commit, which is the mutable thing this exists to avoid"
        )


# ── the walker ──────────────────────────────────────────────────────────

def test_walker_finds_shas_at_every_nesting_depth():
    """state.json nests them under phase_completed.<n>; gate results are flat."""
    assert _enforcer_shas_in({"enforcer_sha": "aaa"}) == ["aaa"]
    assert _enforcer_shas_in(
        {"phase_completed": {"1": {"enforcer_sha": "bbb"},
                             "2": {"enforcer_sha": "ccc"}}}
    ) == ["bbb", "ccc"]
    assert _enforcer_shas_in([{"enforcer_sha": "ddd"}]) == ["ddd"]
    assert _enforcer_shas_in({"unrelated": 1}) == []


# ── the reader ──────────────────────────────────────────────────────────

def test_doctor_is_silent_when_the_enforcer_still_resolves(tmp_path):
    from core.harness_provenance import enforcer_sha

    project = _method(tmp_path)
    (project / ".methodology" / "gate2_result.json").write_text(
        json.dumps({"gate": 2, "enforcer_sha": enforcer_sha()}), encoding="utf-8"
    )
    assert _check_enforcer_provenance(project) == []


def test_doctor_warns_when_the_enforcer_commit_is_unreachable(tmp_path):
    """The taskq-advance shape: a verdict naming a commit a rebase orphaned."""
    project = _method(tmp_path)
    (project / ".methodology" / "gate2_result.json").write_text(
        json.dumps({"gate": 2, "enforcer_sha": _ORPHANED}), encoding="utf-8"
    )
    findings = _check_enforcer_provenance(project)
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "WARN", (
        "an unreachable enforcer does not make the verdict wrong — it makes the "
        "question the field was added for unanswerable"
    )
    assert _ORPHANED[:12] in f.message
    assert "gate2_result.json" in f.message
    assert "enforcer_surface" in f.message, "the message must name the way out"


def test_doctor_reads_state_json_phase_completed_too(tmp_path):
    """Both taskq-advance phase_completed entries carry the orphaned SHA."""
    project = _method(tmp_path)
    (project / ".methodology" / "state.json").write_text(
        json.dumps({"phase_completed": {"1": {"enforcer_sha": _ORPHANED}}}),
        encoding="utf-8",
    )
    assert len(_check_enforcer_provenance(project)) == 1


def test_doctor_ignores_unknown_and_dirty_suffixes(tmp_path):
    """`unknown` means git was unavailable when the verdict was written — not a
    stale reference. `-dirty` is stripped before lookup (Round 19 站3)."""
    from core.harness_provenance import enforcer_sha

    project = _method(tmp_path)
    (project / ".methodology" / "a.json").write_text(
        json.dumps({"enforcer_sha": "unknown"}), encoding="utf-8"
    )
    (project / ".methodology" / "b.json").write_text(
        json.dumps({"enforcer_sha": enforcer_sha().removesuffix("-dirty") + "-dirty"}),
        encoding="utf-8",
    )
    assert _check_enforcer_provenance(project) == []


def test_doctor_is_silent_on_a_project_with_no_verdicts(tmp_path):
    assert _check_enforcer_provenance(_method(tmp_path)) == []
    assert _check_enforcer_provenance(tmp_path / "nope") == []


def test_run_doctor_actually_runs_the_check(tmp_path):
    """Through the real entry point, not the helper.

    Every test above calls `_check_enforcer_provenance` directly, so deleting
    its one line from `run_doctor` leaves them all green — verified: the
    counter-proof kept 46/46 passing until this test existed. That is the same
    gap that let Round 29 ship `enforcer_surface` with no reader at all.
    """
    from core.doctor import run_doctor

    project = _method(tmp_path)
    (project / ".methodology" / "gate2_result.json").write_text(
        json.dumps({"gate": 2, "enforcer_sha": _ORPHANED}), encoding="utf-8"
    )
    checks = [f.check for f in run_doctor(project)]
    assert "provenance" in checks, (
        "run_doctor no longer reports unresolvable enforcer references — the "
        "field is recorded on every verdict and read by nobody again"
    )
