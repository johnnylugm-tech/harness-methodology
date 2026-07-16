"""Round 13 站0 — top-level crash boundary.

Covers core/errors.py (crash bundle + banner) and harness_cli.py's
_dispatch() (the try/except wrapper around args.func(args) that gives an
uncaught harness exception its own exit code and signal instead of a bare
traceback indistinguishable from exit 1's normal "hard failure" meaning).
"""

from __future__ import annotations

import argparse
import json

import pytest

import harness_cli
from core.errors import format_harness_bug_banner, write_crash_bundle


# ---- core/errors.py: write_crash_bundle -----------------------------------

def test_write_crash_bundle_creates_expected_file(tmp_path):
    argv = ["run-phase", "--phase", "3", "--project", str(tmp_path)]
    try:
        raise ValueError("boom")
    except ValueError as exc:
        bundle_path = write_crash_bundle(exc, argv)

    assert bundle_path is not None
    assert bundle_path.exists()
    assert bundle_path.parent == tmp_path / ".sessi-work" / "crash"
    data = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert data["exc_type"] == "ValueError"
    assert data["exc_message"] == "boom"
    assert "ValueError: boom" in data["traceback"]
    assert data["argv"] == argv
    assert data["project"] == str(tmp_path)
    assert "harness_cli.py" in data["repro_command"]
    assert "boom" in data["maintenance_prompt"]


def test_write_crash_bundle_falls_back_to_cwd_without_project_arg(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    try:
        raise RuntimeError("no --project given")
    except RuntimeError as exc:
        bundle_path = write_crash_bundle(exc, ["status"])
    assert bundle_path is not None
    assert bundle_path.parent == tmp_path / ".sessi-work" / "crash"


def test_write_crash_bundle_never_raises_on_write_failure(tmp_path, capsys):
    # Point --project at a FILE (not a dir) so `mkdir` under it raises
    # NotADirectoryError — the write must degrade to a printed WARN, never
    # a second exception escaping on top of the one being reported.
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    try:
        raise ValueError("boom")
    except ValueError as exc:
        result = write_crash_bundle(exc, ["--project", str(blocker)])
    assert result is None
    captured = capsys.readouterr()
    assert "failed to write crash bundle" in captured.err


# ---- core/errors.py: format_harness_bug_banner ----------------------------

def test_banner_first_line_is_machine_readable():
    banner = format_harness_bug_banner(ValueError("something broke"), None)
    lines = banner.splitlines()
    assert lines[0] == "[HARNESS-BUG] ValueError: something broke"


def test_banner_instructs_agent_not_to_retry_or_edit_project():
    banner = format_harness_bug_banner(RuntimeError("x"), None)
    assert "do not retry" in banner.lower()
    assert "do not modify project code" in banner.lower() or "not modify" in banner.lower()
    assert "harness-methodology itself" in banner


def test_banner_includes_bundle_path_when_given(tmp_path):
    p = tmp_path / "crash_1.json"
    banner = format_harness_bug_banner(ValueError("x"), p)
    assert str(p) in banner


def test_banner_omits_bundle_line_when_none():
    banner = format_harness_bug_banner(ValueError("x"), None)
    assert "Crash bundle" not in banner


# ---- harness_cli._dispatch --------------------------------------------

def _ns(func):
    return argparse.Namespace(func=func)


def test_dispatch_passes_through_normal_return():
    assert harness_cli._dispatch(_ns(lambda a: 0), []) == 0
    assert harness_cli._dispatch(_ns(lambda a: 6), []) == 6


def test_dispatch_keyboard_interrupt_returns_130(capsys):
    def raiser(a):
        raise KeyboardInterrupt()
    rc = harness_cli._dispatch(_ns(raiser), [])
    assert rc == 130
    assert "INTERRUPTED" in capsys.readouterr().err


def test_dispatch_leaked_gate_blocked_error_returns_1_with_warn(capsys):
    from harness.harness_bridge import GateBlockedError, GateResult

    result = GateResult(gate_num=2, score=50.0, dimensions=[], open_critical=0, open_high=0)

    def raiser(a):
        raise GateBlockedError(2, result)
    rc = harness_cli._dispatch(_ns(raiser), [])
    assert rc == 1
    err = capsys.readouterr().err
    assert "[WARN]" in err
    assert "GateBlockedError" in err
    assert "leaked to top-level" in err


def test_dispatch_kill_switch_blocked_error_returns_1_with_warn(capsys):
    from core.phase_hooks import KillSwitchBlockedError

    def raiser(a):
        raise KillSwitchBlockedError("circuit open")
    rc = harness_cli._dispatch(_ns(raiser), [])
    assert rc == 1
    assert "KillSwitchBlockedError" in capsys.readouterr().err


def test_dispatch_generic_exception_returns_70_with_harness_bug_banner(tmp_path, capsys):
    def raiser(a):
        raise ValueError("unexpected internal bug")
    argv = ["status", "--project", str(tmp_path)]
    rc = harness_cli._dispatch(_ns(raiser), argv)
    assert rc == 70
    err = capsys.readouterr().err
    assert "[HARNESS-BUG] ValueError: unexpected internal bug" in err
    assert "Traceback" in err  # full traceback still printed for human debugging
    bundle_dir = tmp_path / ".sessi-work" / "crash"
    assert bundle_dir.exists()
    assert list(bundle_dir.glob("crash_*.json")), "crash bundle must be written as a side effect"


def test_dispatch_generic_exception_bundle_write_failure_does_not_mask_original_error(tmp_path, capsys):
    blocker = tmp_path / "blocked"
    blocker.write_text("x")

    def raiser(a):
        raise ValueError("original bug")
    rc = harness_cli._dispatch(_ns(raiser), ["--project", str(blocker)])
    assert rc == 70
    err = capsys.readouterr().err
    assert "[HARNESS-BUG] ValueError: original bug" in err


@pytest.mark.parametrize("code", [0, 1, 2, 5, 6, 8, 10, 11, 12, 14, 17, 18, 19, 20, 21, 22, 23, 24])
def test_dispatch_preserves_every_known_exit_code(code):
    assert harness_cli._dispatch(_ns(lambda a, c=code: c), []) == code
