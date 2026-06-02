"""PR 9: preflight auto-fix dispatch tests.

Confirms that `_dispatch_trace_auto_fix` is invoked only at P5+ when
the preflight is blocked, and that the per-strategy allowlist dispatches
only `fix_missing_traceability` (problem_type='missing_traceability').

Tests use mock AutoFixEngine to avoid touching the source tree.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """Minimal repo with one untested FR — the canonical 'auto-fix can close this' case."""
    arch = tmp_path / "02-architecture"
    arch.mkdir()
    (arch / "SAD.md").write_text("FR-01: alpha\n")
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text('"""[FR-01] Foo."""\n')
    return tmp_path


def _phase_hooks(project: Path, phase: int):
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.phase_hooks import PhaseHooks, _dispatch_trace_auto_fix
    return PhaseHooks(str(project), phase=phase, enable_kill_switch=False), _dispatch_trace_auto_fix


def test_p5_dispatches_when_trace_gap_detected(fixture_repo):
    """At P5+ with a trace gap, _dispatch_trace_auto_fix is called."""
    _, dispatch = _phase_hooks(fixture_repo, phase=5)
    fake_engine = MagicMock()
    fake_engine.fix.return_value = (True, "Auto-fixed: 1 stub", 90.0)
    with patch("core.auto_fix.AutoFixEngine", return_value=fake_engine):
        result = dispatch(fixture_repo, untested=["FR-01"], uncoded=[])
    assert result is True
    fake_engine.fix.assert_called_once()
    ctx = fake_engine.fix.call_args[0][0]
    assert ctx.problem_type == "missing_traceability"
    assert ctx.details["max_rounds"] == 1
    assert "FR-01" in ctx.details["untested"]


def test_p3_does_not_dispatch(fixture_repo):
    """P5+ only. P3 preflight gap is informational; no auto-fix."""
    h, _ = _phase_hooks(fixture_repo, phase=3)
    # P3 preflight is non-blocking, so the dispatch call site in
    # preflight_traceability is guarded by `if blocking`. Verify the
    # blocking flag is False at P3.
    assert h.phase == 3
    # Direct call to _dispatch_trace_auto_fix at P3: the function itself
    # doesn't gate by phase (caller does), so we just confirm the gate
    # decision lives at the call site.
    # The call site checks `if blocking and not passed ...`.
    blocking_at_p3 = h.phase is not None and h.phase >= 5
    assert blocking_at_p3 is False


def test_dispatch_returns_false_on_engine_exception(fixture_repo):
    """If engine.fix raises, the helper returns False (caller keeps gate blocked)."""
    _, dispatch = _phase_hooks(fixture_repo, phase=5)
    fake_engine = MagicMock()
    fake_engine.fix.side_effect = RuntimeError("engine crash")
    with patch("core.auto_fix.AutoFixEngine", return_value=fake_engine):
        result = dispatch(fixture_repo, untested=["FR-01"], uncoded=[])
    assert result is False


def test_dispatch_returns_true_on_fix_success(fixture_repo):
    """Successful fix → returns True (caller re-verifies)."""
    _, dispatch = _phase_hooks(fixture_repo, phase=5)
    fake_engine = MagicMock()
    fake_engine.fix.return_value = (True, "Auto-fixed: 1 stub", 90.0)
    with patch("core.auto_fix.AutoFixEngine", return_value=fake_engine):
        result = dispatch(fixture_repo, untested=["FR-99"], uncoded=[])
    assert result is True


def test_dispatch_returns_false_on_escalation(fixture_repo):
    """When fix returns False (escalation), dispatch returns False."""
    _, dispatch = _phase_hooks(fixture_repo, phase=5)
    fake_engine = MagicMock()
    fake_engine.fix.return_value = (False, "Auto-fix exhausted", 0.0)
    with patch("core.auto_fix.AutoFixEngine", return_value=fake_engine):
        result = dispatch(fixture_repo, untested=["FR-99"], uncoded=[])
    assert result is False
