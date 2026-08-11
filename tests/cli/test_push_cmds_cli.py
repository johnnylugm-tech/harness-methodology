"""Tests for cli/push_cmds.py — push-checkpoint / push-milestone / P8 completion (split from tests/test_harness_cli.py, C1a)."""

from __future__ import annotations

import subprocess

import argparse
import json
from pathlib import Path

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports


class TestValidateP8Completion:
    """Tests for _validate_p8_completion pre-flight checks."""

    def test_all_ok_returns_empty_list(self, tmp_path):
        archive = tmp_path / ".methodology-archive"
        archive.mkdir()
        (archive / "phase8_plan.md").write_text("# P8 plan\n", encoding="utf-8")
        handover = tmp_path / "HANDOVER.md"
        handover.write_text("# Handover\n\nP8 complete. All phases done.\n")
        from cli.push_cmds import _validate_p8_completion
        errors = _validate_p8_completion(tmp_path)
        assert errors == []

    def test_missing_archive_autocreated(self, tmp_path):
        # Auto-create .methodology-archive/ when absent; report content error
        # (the directory was never populated, not just never created).
        from cli.push_cmds import _validate_p8_completion
        assert not (tmp_path / ".methodology-archive").exists()
        errors = _validate_p8_completion(tmp_path)
        assert (tmp_path / ".methodology-archive").exists(), "dir must be auto-created"
        assert any("methodology artifacts" in e for e in errors), (
            "empty auto-created archive must trigger a content error"
        )

    def test_phase9_reference_in_handover_is_legal(self, tmp_path):
        """Phase 9 (Maintenance) is a legal steady state — a P8-exit HANDOVER
        pointing at Phase 9 next steps must NOT be flagged."""
        archive = tmp_path / ".methodology-archive"
        archive.mkdir()
        (archive / "quality_manifest.json").write_text("{}", encoding="utf-8")
        handover = tmp_path / "HANDOVER.md"
        handover.write_text("# Handover\n\nNext: Begin Phase 9 tasks.\n")
        from cli.push_cmds import _validate_p8_completion
        errors = _validate_p8_completion(tmp_path)
        assert not any("Phase 9" in e or "phase 9" in e.lower() for e in errors)


# =============================================================================
# Finding #24: P8 archive copied .sessi-work/ instead of .methodology/
# =============================================================================

class TestP8ArchiveContentCheck:
    """Regression tests for Finding #24: P8 plan's archive step said
    'cp -r .sessi-work/ .methodology-archive/' which copies the gitignored
    runtime scratch dir (not the methodology artifacts the archive name
    implies). Fix: P8 plan now says 'cp -r .methodology/ .methodology-archive/'
    and the validator surfaces an actionable error if the archive ends up
    empty or wrong-sourced.
    """

    def test_archive_with_methodology_passes(self, tmp_path):
        """Archive contains .methodology/ contents → no error.

        `cp -r .methodology/ .methodology-archive/` (trailing slash on source,
        dest already exists from mkdir) copies the CONTENTS of .methodology/
        directly into .methodology-archive/ — no "methodology/" subdirectory.
        """
        from cli.push_cmds import _validate_p8_completion

        archive = tmp_path / ".methodology-archive"
        archive.mkdir(parents=True)
        (archive / "phase8_plan.md").write_text("# P8 plan\n", encoding="utf-8")
        handover = tmp_path / "HANDOVER.md"
        handover.write_text("# Handover\n\nP8 complete.\n", encoding="utf-8")

        errors = _validate_p8_completion(tmp_path)
        assert errors == [], (
            f"Valid archive should produce no errors; got: {errors}"
        )

    def test_archive_with_only_manifest_passes(self, tmp_path):
        """Archive with quality_manifest.json (no phase plan) still passes."""
        from cli.push_cmds import _validate_p8_completion

        archive = tmp_path / ".methodology-archive"
        archive.mkdir(parents=True)
        (archive / "quality_manifest.json").write_text(
            '{"fr_ids": ["FR-01"]}', encoding="utf-8"
        )
        handover = tmp_path / "HANDOVER.md"
        handover.write_text("# Handover\n", encoding="utf-8")

        errors = _validate_p8_completion(tmp_path)
        assert errors == [], (
            f"Archive with quality_manifest.json should pass; got: {errors}"
        )

    def test_archive_with_sessi_work_only_fails(self, tmp_path):
        """Archive contains .sessi-work/ (the Finding #24 typo) → error.

        Old plan said `cp -r .sessi-work/ .methodology-archive/` which produced
        .methodology-archive/sessi-work/ with only transient runtime scratch
        (crg_metrics.json, gate result JSONs, etc.). The validator must catch
        this and point to the correct command.
        """
        from cli.push_cmds import _validate_p8_completion

        archive = tmp_path / ".methodology-archive" / "sessi-work"
        archive.mkdir(parents=True)
        (archive / "crg_metrics.json").write_text("{}", encoding="utf-8")
        handover = tmp_path / "HANDOVER.md"
        handover.write_text("# Handover\n", encoding="utf-8")

        errors = _validate_p8_completion(tmp_path)
        assert any("methodology" in e.lower() for e in errors), (
            f"Validator should catch the .sessi-work/ typo; got: {errors}"
        )
        # Error must point to the correct command
        err_text = " ".join(errors)
        assert "cp -r .methodology/" in err_text, (
            f"Error must show the correct cp command; got: {err_text}"
        )
        assert "Finding #24" in err_text, (
            f"Error must reference Finding #24 for traceability; got: {err_text}"
        )

    def test_empty_archive_fails(self, tmp_path):
        """.methodology-archive/ exists but is empty (mkdir ran, cp never did) → error."""
        from cli.push_cmds import _validate_p8_completion

        # Do NOT pre-create the archive dir; the validator creates it automatically.
        # Result: .methodology-archive/ exists but contains no plan files or manifest.
        handover = tmp_path / "HANDOVER.md"
        handover.write_text("# Handover\n", encoding="utf-8")

        errors = _validate_p8_completion(tmp_path)
        assert any("methodology artifacts" in e for e in errors), (
            f"Validator should catch empty archive; got: {errors}"
        )

    def test_phase8_plan_no_longer_says_sessi_work(self):
        """Static check: phase8_plan.md archive step says .methodology/, not .sessi-work/.

        Guards against regression if someone re-touches the P8 plan template.
        Path resolved relative to the test file so the test runs in any
        environment (CI runner, dev container, integration-test repo).
        """
        # The .methodology/ dir lives next to the harness root, which is the
        # test file's parents[2] (tests/cli/ → tests/ → harness-methodology/).
        harness_root = Path(__file__).resolve().parents[2]
        plan = harness_root / ".methodology" / "phase8_plan.md"
        assert plan.exists(), f"Plan file not found at {plan} (test path assumption wrong?)"
        text = plan.read_text(encoding="utf-8")
        # The buggy command must be gone
        assert "cp -r .sessi-work/ .methodology-archive/" not in text, (
            "P8 plan still contains the Finding #24 typo "
            "('cp -r .sessi-work/ .methodology-archive/')"
        )
        # The correct command must be present
        assert "cp -r .methodology/ .methodology-archive/" in text, (
            "P8 plan should instruct the agent to copy .methodology/, not .sessi-work/"
        )

    def test_phase9_plan_reference_is_legal(self, tmp_path):
        """phase9_plan.md references are legal now that P9 exists."""
        archive = tmp_path / ".methodology-archive"
        archive.mkdir()
        (archive / "quality_manifest.json").write_text("{}", encoding="utf-8")
        handover = tmp_path / "HANDOVER.md"
        handover.write_text("See phase9_plan.md for next steps.\n")
        from cli.push_cmds import _validate_p8_completion
        errors = _validate_p8_completion(tmp_path)
        assert not any("Phase 9" in e or "phase9" in e.lower() for e in errors)

    def test_no_handover_file_is_ok(self, tmp_path):
        archive = tmp_path / ".methodology-archive"
        archive.mkdir()
        (archive / "phase8_plan.md").write_text("# P8\n", encoding="utf-8")
        # No HANDOVER.md — should not raise
        from cli.push_cmds import _validate_p8_completion
        errors = _validate_p8_completion(tmp_path)
        assert errors == []


class TestPushCheckpointAgentBGate:
    """push-checkpoint: pure git record — all quality gates live in advance-phase."""

    def test_commit_message_no_longer_says_human_review(self, tmp_path, monkeypatch):
        """Commit notes must NOT say 'human review' (gates moved to advance-phase)."""
        from harness_cli import cmd_push_checkpoint

        commit_calls: list[dict] = []
        class _FakeGit:
            def ensure_gitignore(self): pass
            def commit_and_push_p1(self, **kw):
                commit_calls.append(kw)
                return True

        monkeypatch.setattr("cli._shared._make_git", lambda *_, **__: _FakeGit())

        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_ids="FR-01",
            dry_run=False, no_push=False,
        )
        rc = cmd_push_checkpoint(args)
        assert rc == 0
        assert commit_calls, "commit_and_push_p1 should have been called"
        background = commit_calls[0].get("background", "")
        notes = commit_calls[0].get("notes", [])
        assert "human review" not in background.lower(), "must not claim human review"
        assert all("human review" not in n.lower() for n in notes), "notes must not claim human review"


# =============================================================================
# Phase 8 bug regressions (B1 / B2 / B3)
# =============================================================================

# B1: commit_and_push_p8 must pass resume_phase=8 so HandoverGenerator does
# not compute _target = 9 and embed phase9_plan.md references.
def test_p8_commit_handover_uses_resume_phase_8(tmp_path, monkeypatch):
    """B1: commit_and_push_p8 must write HANDOVER.md with resume_phase=8.

    Without resume_phase=8, HandoverGenerator._target = phase + 1 = 9,
    causing it to embed Phase 9 plan references that break _validate_p8_completion.
    """
    from harness.git_strategy import GitStrategy

    captured: dict = {}

    def fake_write(
        self,
        checkpoint_id,
        phase,
        background,
        status,
        steps,
        notes,
        extra=None,
        plan_override=None,
        deliverables=None,
        resume_phase=None,
    ):
        captured["resume_phase"] = resume_phase
        captured["phase"] = phase

    monkeypatch.setattr("harness.git_strategy.GitStrategy._write_handover", fake_write)
    monkeypatch.setattr(
        "harness.git_strategy.GitStrategy._commit_and_push",
        lambda self, msg: True,
    )

    gs = GitStrategy(project=tmp_path, enabled=True)
    gs.commit_and_push_p8()

    assert captured.get("phase") == 8, (
        f"Expected phase=8 in _write_handover call, got {captured.get('phase')}"
    )
    assert captured.get("resume_phase") == 8, (
        f"Expected resume_phase=8, got {captured.get('resume_phase')!r}. "
        f"Without resume_phase=8, HandoverGenerator computes _target=9 and "
        f"embeds phase9_plan.md refs."
    )


class TestPushMilestoneDryRun:
    """Bug #112: push-milestone --dry-run disables git operations."""

    def test_help_lists_dry_run(self):
        from harness_cli import build_parser
        parser = build_parser()
        for action in parser._actions:
            sub_parsers = getattr(action, "choices", None) or {}
            if not sub_parsers:
                continue
            for sub_parser in sub_parsers.values():
                for sub_action in sub_parser._actions:
                    if sub_action.dest == "dry_run":
                        assert "--dry-run" in sub_action.option_strings
                        return
        raise AssertionError("push-milestone parser has no --dry-run flag")

    def test_dry_run_disables_git(self, tmp_path, monkeypatch):
        """When --dry-run is set, GitStrategy must be constructed with enabled=False."""
        from cli._shared import _make_git
        captured = {"enabled": None}
        class FakeGit:
            def __init__(self, project, enabled):
                captured["enabled"] = enabled
            def ensure_gitignore(self): pass
        monkeypatch.setattr("harness.git_strategy.GitStrategy", FakeGit)
        args = argparse.Namespace(
            project=str(tmp_path), no_git=False, dry_run=True,
        )
        _make_git(args, tmp_path)
        assert captured["enabled"] is False

    def test_dry_run_false_keeps_git_enabled(self, tmp_path, monkeypatch):
        from cli._shared import _make_git
        captured = {"enabled": None}
        class FakeGit:
            def __init__(self, project, enabled):
                captured["enabled"] = enabled
        monkeypatch.setattr("harness.git_strategy.GitStrategy", FakeGit)
        args = argparse.Namespace(
            project=str(tmp_path), no_git=False, dry_run=False,
        )
        _make_git(args, tmp_path)
        assert captured["enabled"] is True

    def test_dry_run_exits_0(self, tmp_path, monkeypatch, capsys):
        """Finding #1: --dry-run must exit 0, not 1.

        Pre-fix: dry-run printed the notice but fell through to git.commit_and_push_*()
        which returned False (git disabled), causing `return 0 if ok else 1` → exit 1.
        Post-fix: return 0 immediately after the notice.
        """
        from harness_cli import cmd_push_milestone
        class FakeGit:
            def __init__(self, project, enabled): pass
            def ensure_gitignore(self): pass
            def commit_and_push_p3_mid(self, *a, **kw): return False
        monkeypatch.setattr("harness.git_strategy.GitStrategy", FakeGit)
        args = argparse.Namespace(
            project=str(tmp_path), type="p3-mid", fr_ids="FR-01",
            fr_done=3, fr_total=6, no_git=False, dry_run=True,
        )
        result = cmd_push_milestone(args)
        assert result == 0, "dry-run must exit 0 (Finding #1)"
        captured = capsys.readouterr()
        assert "[dry-run]" in captured.out


# =============================================================================
# state.json write-after-push family (P8 E2E 2026-07-04)
# Bug: cmd_push_milestone / _cmd_finalize_gate_impl gate-4 / cmd_push_checkpoint
# wrote audit fields to .methodology/state.json AFTER commit_and_push_* returned,
# so the write never landed in the pushed commit. Tests below prove the order
# (write-before-push) and the on-disk content.
# =============================================================================


class TestPushMilestoneStateJsonWriteBeforePush:
    """Site 1: cmd_push_milestone must write state.json BEFORE
    git.commit_and_push_p8() so the audit fields land in the pushed commit.
    """

    def _setup(self, tmp_path, monkeypatch, milestone_type="p8", exists=True):
        import harness_cli as hc  # noqa: F401  entry-first load order

        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        state_path = meth / "state.json"
        if exists:
            state_path.write_text(json.dumps({"existing": True}), encoding="utf-8")
        # minimal quality_manifest so fr_ids auto-populate is safe
        (meth / "quality_manifest.json").write_text(
            # gate4 evidence: p8 milestones are entry-gated (E2E C-1/C-2 fix)
            json.dumps({"fr_ids": [],
                        "gate_results": {"gate4": {"quality_complete": True}}}),
            encoding="utf-8",
        )

        call_order: list[str] = []

        class FakeGit:
            def ensure_gitignore(self): pass
            def commit_and_push_p8(self):
                call_order.append("commit_and_push_p8")
                return True

        monkeypatch.setattr("cli._shared._make_git", lambda *_a, **_k: FakeGit())
        # bypass p8 preflight (we don't have real artifacts)
        monkeypatch.setattr("cli.push_cmds._validate_p8_completion", lambda _p: [])

        from core.atomic_io import atomic_write_json as _orig_atomic

        def _spy(path, data, **_kw):
            if Path(path).name == "state.json":
                call_order.append("atomic_write_json(state.json)")
            _orig_atomic(path, data)

        # S1: push commands (cli/push_cmds) bind atomic_write_json directly
        # from core.atomic_io — patch both namespaces with the same spy.
        from cli import push_cmds as _pushc
        monkeypatch.setattr(_pushc, "atomic_write_json", _spy)
        return call_order, state_path, hc

    def test_state_json_written_before_commit_and_push_p8(self, tmp_path, monkeypatch):
        call_order, state_path, hc = self._setup(tmp_path, monkeypatch)
        args = argparse.Namespace(
            project=str(tmp_path), type="p8", fr_ids="",
            fr_done=None, fr_total=None, no_git=False, dry_run=False,
        )
        rc = hc.cmd_push_milestone(args)
        assert rc == 0

        # 1. on-disk content
        sd = json.loads(state_path.read_text(encoding="utf-8"))
        assert sd["last_milestone_command"] == "push-milestone --type p8"
        # Round 45 站6 removed last_milestone_at: written only here, read
        # by nothing, and left permanently stale by advance-phase updating
        # its sibling without it.
        assert "last_milestone_at" not in sd
        assert sd["existing"] is True  # pre-existing keys preserved

        # 2. ordering: state.json write must precede commit_and_push_p8
        idx_write = call_order.index("atomic_write_json(state.json)")
        idx_push = call_order.index("commit_and_push_p8")
        assert idx_write < idx_push, (
            f"state.json write must precede commit_and_push_p8; got order: {call_order}"
        )

    def test_skip_when_state_json_missing(self, tmp_path, monkeypatch):
        call_order, _state_path, hc = self._setup(tmp_path, monkeypatch, exists=False)
        args = argparse.Namespace(
            project=str(tmp_path), type="p8", fr_ids="",
            fr_done=None, fr_total=None, no_git=False, dry_run=False,
        )
        rc = hc.cmd_push_milestone(args)
        assert rc == 0
        # push still happened, write was skipped
        assert "commit_and_push_p8" in call_order
        assert "atomic_write_json(state.json)" not in call_order

    def test_reverted_on_p8_validation_failure(self, tmp_path, monkeypatch):
        """P8 preflight failure must revert the optimistic audit write —
        ci_state_helper.cmd_is_p8 trusts last_milestone_command alone."""
        import harness_cli as hc  # noqa: F401  entry-first load order

        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        state_path = meth / "state.json"
        state_path.write_text(json.dumps({"existing": True}), encoding="utf-8")
        (meth / "quality_manifest.json").write_text(
            # gate4 evidence: p8 milestones are entry-gated (E2E C-1/C-2 fix)
            json.dumps({"fr_ids": [],
                        "gate_results": {"gate4": {"quality_complete": True}}}),
            encoding="utf-8",
        )

        class FakeGit:
            def ensure_gitignore(self): pass
            def commit_and_push_p8(self):
                raise AssertionError("commit_and_push_p8 must not be called on preflight failure")

        monkeypatch.setattr("cli._shared._make_git", lambda *_a, **_k: FakeGit())
        monkeypatch.setattr("cli.push_cmds._validate_p8_completion", lambda _p: ["missing artifact"])

        args = argparse.Namespace(
            project=str(tmp_path), type="p8", fr_ids="",
            fr_done=None, fr_total=None, no_git=False, dry_run=False,
        )
        rc = hc.cmd_push_milestone(args)
        assert rc == 1

        sd = json.loads(state_path.read_text(encoding="utf-8"))
        assert "last_milestone_command" not in sd
        assert sd["existing"] is True

    def test_reverted_on_push_failure(self, tmp_path, monkeypatch):
        """commit_and_push_p8 returning False must revert the optimistic write."""
        import harness_cli as hc  # noqa: F401  entry-first load order

        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        state_path = meth / "state.json"
        state_path.write_text(
            json.dumps({"existing": True, "last_milestone_command": "push-milestone --type p7"}),
            encoding="utf-8",
        )
        (meth / "quality_manifest.json").write_text(
            # gate4 evidence: p8 milestones are entry-gated (E2E C-1/C-2 fix)
            json.dumps({"fr_ids": [],
                        "gate_results": {"gate4": {"quality_complete": True}}}),
            encoding="utf-8",
        )

        class FakeGit:
            def ensure_gitignore(self): pass
            def commit_and_push_p8(self):
                return False

        monkeypatch.setattr("cli._shared._make_git", lambda *_a, **_k: FakeGit())
        monkeypatch.setattr("cli.push_cmds._validate_p8_completion", lambda _p: [])

        args = argparse.Namespace(
            project=str(tmp_path), type="p8", fr_ids="",
            fr_done=None, fr_total=None, no_git=False, dry_run=False,
        )
        rc = hc.cmd_push_milestone(args)
        assert rc == 1

        sd = json.loads(state_path.read_text(encoding="utf-8"))
        # reverted to the prior (pre-p8-attempt) value, not left at the failed attempt's value
        assert sd["last_milestone_command"] == "push-milestone --type p7"
        assert sd["existing"] is True


class TestPushCheckpointStateJsonWriteBeforePush:
    """Site 2: cmd_push_checkpoint must write state.json BEFORE
    git.commit_and_push_p1() so phase_completed[N].sha lands in the push.
    """

    def test_state_json_written_before_commit_and_push_p1_with_phase_completed_sha(
        self, tmp_path, monkeypatch
    ):
        import harness_cli as hc  # noqa: F401  entry-first load order

        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        state_path = meth / "state.json"
        state_path.write_text(json.dumps({"existing": True}), encoding="utf-8")

        # stub attestation refresh (no-op)
        import scripts.build_trace_attestation as bat
        monkeypatch.setattr(bat, "build_attestation", lambda _p: {})
        monkeypatch.setattr(bat, "write_attestation", lambda _p, _a: None)

        # stub subprocess.run: rev-parse returns fake SHA; other calls no-op
        fake_sha = "deadbeefcafebabe1234567890abcdef12345678"

        def _fake_run(cmd, **_kw):
            class _R:
                returncode = 0
                stdout = fake_sha if "rev-parse" in cmd else ""
                stderr = ""
            return _R()

        monkeypatch.setattr(subprocess, "run", _fake_run)

        call_order: list[str] = []

        class FakeGit:
            def ensure_gitignore(self): pass
            def commit_and_push_p1(self, **_kw):
                call_order.append("commit_and_push_p1")
                return True

        monkeypatch.setattr("cli._shared._make_git", lambda *_a, **_k: FakeGit())

        from core.atomic_io import atomic_write_json as _orig_atomic

        def _spy(path, data, **_kw):
            if Path(path).name == "state.json":
                call_order.append("atomic_write_json(state.json)")
            _orig_atomic(path, data)

        # S1: cli/push_cmds binds atomic_write_json directly — patch both.
        from cli import push_cmds as _pushc
        monkeypatch.setattr(_pushc, "atomic_write_json", _spy)

        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_ids="FR-01",
            dry_run=False, no_git=False, no_push=False,
        )
        rc = hc.cmd_push_checkpoint(args)
        assert rc == 0

        # 1. on-disk content
        sd = json.loads(state_path.read_text(encoding="utf-8"))
        # Round 24 站4b: last_push_checkpoint / last_push_checkpoint_phase were
        # written here and read nowhere; removed. phase_completed stays — it has
        # three live readers (_verify_entry_gate, _fr_step_lineage_boundary,
        # constitution/runner.py).
        assert "last_push_checkpoint" not in sd
        assert "last_push_checkpoint_phase" not in sd
        assert sd["phase_completed"]["1"]["sha"] == fake_sha
        assert "timestamp" in sd["phase_completed"]["1"]
        assert sd["existing"] is True

        # 2. ordering
        idx_write = call_order.index("atomic_write_json(state.json)")
        idx_push = call_order.index("commit_and_push_p1")
        assert idx_write < idx_push, (
            f"state.json write must precede commit_and_push_p1; got: {call_order}"
        )

    def test_skip_when_state_json_missing(self, tmp_path, monkeypatch):
        import harness_cli as hc  # noqa: F401  entry-first load order

        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        # do NOT write state.json
        import scripts.build_trace_attestation as bat
        monkeypatch.setattr(bat, "build_attestation", lambda _p: {})
        monkeypatch.setattr(bat, "write_attestation", lambda _p, _a: None)

        call_order: list[str] = []

        class FakeGit:
            def ensure_gitignore(self): pass
            def commit_and_push_p1(self, **_kw):
                call_order.append("commit_and_push_p1")
                return True

        monkeypatch.setattr("cli._shared._make_git", lambda *_a, **_k: FakeGit())

        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_ids="",
            dry_run=False, no_git=False, no_push=False,
        )
        rc = hc.cmd_push_checkpoint(args)
        assert rc == 0
        assert "commit_and_push_p1" in call_order
        # no atomic_write_json was called for state.json (it doesn't exist)
        # Filter call_order for state.json entries
        state_writes = [e for e in call_order if "state.json" in e]
        assert not state_writes

    def test_reverted_on_push_failure(self, tmp_path, monkeypatch):
        """_verify_entry_gate reads state.json's live content directly, so a
        failed commit_and_push_p1 must revert the optimistic checkpoint write
        — otherwise a local push failure still satisfies the Human1 gate."""
        import harness_cli as hc  # noqa: F401  entry-first load order

        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        state_path = meth / "state.json"
        state_path.write_text(json.dumps({"existing": True}), encoding="utf-8")

        import scripts.build_trace_attestation as bat
        monkeypatch.setattr(bat, "build_attestation", lambda _p: {})
        monkeypatch.setattr(bat, "write_attestation", lambda _p, _a: None)

        fake_sha = "deadbeefcafebabe1234567890abcdef12345678"

        def _fake_run(cmd, **_kw):
            class _R:
                returncode = 0
                stdout = fake_sha if "rev-parse" in cmd else ""
                stderr = ""
            return _R()

        monkeypatch.setattr(subprocess, "run", _fake_run)

        class FakeGit:
            def ensure_gitignore(self): pass
            def commit_and_push_p1(self, **_kw):
                return False

        monkeypatch.setattr("cli._shared._make_git", lambda *_a, **_k: FakeGit())

        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_ids="FR-01",
            dry_run=False, no_git=False, no_push=False,
        )
        rc = hc.cmd_push_checkpoint(args)
        assert rc == 1

        sd = json.loads(state_path.read_text(encoding="utf-8"))
        assert "last_push_checkpoint" not in sd
        assert "last_push_checkpoint_phase" not in sd
        assert "phase_completed" not in sd or "1" not in sd["phase_completed"]
        assert sd["existing"] is True


class TestPushMilestonePostPushDirtyWarn:
    """Site 1: cmd_push_milestone should warn (NOT fail) when post-push
    tree is dirty."""

    def _setup(self, tmp_path, monkeypatch, dirty_paths):
        import harness_cli as hc  # noqa: F401  entry-first load order

        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        (meth / "state.json").write_text(json.dumps({"existing": True}), encoding="utf-8")
        # gate4 evidence: p8 milestones are entry-gated (E2E C-1/C-2 fix)
        (meth / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": [],
                        "gate_results": {"gate4": {"quality_complete": True}}}),
            encoding="utf-8",
        )

        call_order: list[str] = []

        class FakeGit:
            def ensure_gitignore(self): pass
            def commit_and_push_p8(self):
                call_order.append("commit_and_push_p8")
                return True

        monkeypatch.setattr("cli._shared._make_git", lambda *_a: FakeGit())
        # Bypass P8 pre-flight validation — needs real .methodology-archive.
        monkeypatch.setattr("cli.push_cmds._validate_p8_completion", lambda _p: [])
        # Stub the new helper so the test does NOT need a real git repo.
        monkeypatch.setattr("cli._shared._post_push_self_check",
            lambda _p: list(dirty_paths),
        )
        return call_order

    def test_warns_on_post_push_dirty(self, tmp_path, monkeypatch, capsys):
        import harness_cli as hc
        call_order = self._setup(
            tmp_path, monkeypatch, dirty_paths=[".methodology/state.json"],
        )
        args = argparse.Namespace(
            project=str(tmp_path), type="p8", fr_ids="",
            fr_done=None, fr_total=None, no_git=False, dry_run=False,
        )
        rc = hc.cmd_push_milestone(args)
        out = capsys.readouterr().out
        assert rc == 0  # warn-only, NOT fail-fast
        assert "[WARN] post-push dirty tree" in out
        assert "state.json" in out
        assert "commit_and_push_p8" in call_order

    def test_silent_on_clean(self, tmp_path, monkeypatch, capsys):
        import harness_cli as hc
        call_order = self._setup(tmp_path, monkeypatch, dirty_paths=[])
        args = argparse.Namespace(
            project=str(tmp_path), type="p8", fr_ids="",
            fr_done=None, fr_total=None, no_git=False, dry_run=False,
        )
        rc = hc.cmd_push_milestone(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "[WARN] post-push dirty tree" not in out
        assert "commit_and_push_p8" in call_order


class TestPushCheckpointPostPushDirtyWarn:
    """Site 2: cmd_push_checkpoint should warn (NOT fail) when post-push
    tree is dirty."""

    def _setup(self, tmp_path, monkeypatch, dirty_paths):
        import harness_cli as hc  # noqa: F401  entry-first load order

        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        (meth / "state.json").write_text(json.dumps({"existing": True}), encoding="utf-8")

        call_order: list[str] = []

        class FakeGit:
            def ensure_gitignore(self): pass
            def commit_and_push_p1(self, **_kw):
                call_order.append("commit_and_push_p1")
                return True

        monkeypatch.setattr("cli._shared._make_git", lambda *_a: FakeGit())
        monkeypatch.setattr("cli._shared._post_push_self_check",
            lambda _p: list(dirty_paths),
        )

        import subprocess
        _orig_run = subprocess.run
        def _fake_run(cmd, *args, **kwargs):
            if cmd and cmd[0] == "git" and "rev-parse" in cmd:
                class FakeRes:
                    stdout = "fakesha12345\n"
                return FakeRes()
            return _orig_run(cmd, *args, **kwargs)
        monkeypatch.setattr(subprocess, "run", _fake_run)

        # Bypass attestation refresh (irrelevant to this assertion).
        import scripts.build_trace_attestation as _bta_mod
        monkeypatch.setattr(_bta_mod, "build_attestation", lambda _p: {})
        monkeypatch.setattr(_bta_mod, "write_attestation", lambda _p, _a: None)
        return call_order

    def test_warns_on_post_push_dirty(self, tmp_path, monkeypatch, capsys):
        import harness_cli as hc
        call_order = self._setup(
            tmp_path, monkeypatch, dirty_paths=[".methodology/HANDOVER.md"],
        )
        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_ids="FR-01,FR-02",
        )
        rc = hc.cmd_push_checkpoint(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "[WARN] post-push dirty tree" in out
        assert "HANDOVER.md" in out
        assert "commit_and_push_p1" in call_order

    def test_silent_on_clean(self, tmp_path, monkeypatch, capsys):
        import harness_cli as hc
        call_order = self._setup(tmp_path, monkeypatch, dirty_paths=[])
        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_ids="FR-01,FR-02",
        )
        rc = hc.cmd_push_checkpoint(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "[WARN] post-push dirty tree" not in out
        assert "commit_and_push_p1" in call_order
