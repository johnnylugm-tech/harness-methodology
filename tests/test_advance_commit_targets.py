"""Tests for _advance_commit_targets — advance-phase local-commit staging list.

Regression: advance-phase rewrites .methodology/fr_progress.json via _advance_fsm
but its explicit `git add` list omitted the file, leaving it unstaged after every
phase bump (verified on the P6->P7 advance, commit 116884c). The file must be
staged when present, but NOT added unconditionally: P1->P2 / P2->P3 run before any
Gate-1 event, so fr_progress.json may not exist yet, and an explicit `git add` of a
missing pathspec fails the whole commit.
"""
import harness_cli  # noqa: F401  entry-first load order (cli-first crashes until S5)
from cli.phase_cmds import _advance_commit_targets


def test_includes_fr_progress_when_present():
    targets = _advance_commit_targets(6, 7, manifest_regenerated=False, fr_progress_exists=True)
    assert ".methodology/fr_progress.json" in targets


def test_omits_fr_progress_when_absent():
    targets = _advance_commit_targets(1, 2, manifest_regenerated=False, fr_progress_exists=False)
    assert ".methodology/fr_progress.json" not in targets


def test_baseline_targets_always_present():
    targets = _advance_commit_targets(6, 7, manifest_regenerated=False, fr_progress_exists=True)
    assert ".methodology/state.json" in targets
    assert "HANDOVER.md" in targets
    assert "CLAUDE.md" in targets
    assert ".methodology/phase6_plan.md" in targets


def test_includes_gate_timestamps_when_present():
    # gate_timestamps.jsonl is functional state (read back for FR-gate verification)
    # and is appended by the DELTA fast-path within a phase; the advance commit must
    # sweep its tail so it does not linger unstaged (recurring P5/P6/P7 dirty tree).
    targets = _advance_commit_targets(
        6, 7, manifest_regenerated=False, fr_progress_exists=True,
        gate_timestamps_exists=True,
    )
    assert ".methodology/gate_timestamps.jsonl" in targets


def test_omits_gate_timestamps_when_absent():
    targets = _advance_commit_targets(
        1, 2, manifest_regenerated=False, fr_progress_exists=False,
        gate_timestamps_exists=False,
    )
    assert ".methodology/gate_timestamps.jsonl" not in targets


def test_manifest_added_only_when_regenerated():
    with_manifest = _advance_commit_targets(2, 3, manifest_regenerated=True, fr_progress_exists=False)
    without = _advance_commit_targets(2, 3, manifest_regenerated=False, fr_progress_exists=False)
    assert ".methodology/quality_manifest.json" in with_manifest
    assert ".methodology/quality_manifest.json" not in without


def test_phase8_config_files_only_when_entering_p8():
    p8 = _advance_commit_targets(7, 8, manifest_regenerated=False, fr_progress_exists=True)
    non_p8 = _advance_commit_targets(6, 7, manifest_regenerated=False, fr_progress_exists=True)
    assert "08-config/CONFIG_RECORDS.md" in p8
    assert "08-config/CONFIG_RECORDS.md" not in non_p8


def test_includes_attestation_when_present():
    """Regression 2026-07-11: push-checkpoint/push-milestone both refresh +
    stage .methodology/trace/attestation.json before every push (push_cmds.py
    comment: "so every push path is symmetric"), but advance-phase was the one
    caller that never did — so a handover commit lands with a stale
    attestation SHA, only caught (as blocking) at the P5+ pre-push.
    """
    targets = _advance_commit_targets(
        3, 4, manifest_regenerated=False, fr_progress_exists=True,
        attestation_exists=True,
    )
    assert ".methodology/trace/attestation.json" in targets


def test_omits_attestation_when_absent():
    targets = _advance_commit_targets(
        3, 4, manifest_regenerated=False, fr_progress_exists=True,
        attestation_exists=False,
    )
    assert ".methodology/trace/attestation.json" not in targets
