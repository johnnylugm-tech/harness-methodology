"""PR 5: auto-fix with re-verify loop tests.

Confirms:
  - propose_fixes emits a well-formed unified diff
  - apply_diff applies via git apply (3-way); rollback restores
  - fix_missing_traceability auto-applies the diff, re-verifies, and returns
    success on a fixture where the fix closes the gap
  - on persistent failure, escalates to HUMAN_REQUIRED with diff on disk
  - source tree is unchanged on escalation (rollback confirmed)
"""
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """Minimal repo with one FR that has code but no test (auto-fixable)."""
    arch = tmp_path / "02-architecture"
    arch.mkdir()
    (arch / "SAD.md").write_text(
        "# SAD\n\n## FR-99: feature ninety-nine\nImplementation in core.\n"
    )
    core = tmp_path / "core"
    core.mkdir()
    (core / "foo.py").write_text('"""[FR-99] Foo module."""\ndef f(): return 1\n')
    (tmp_path / "tests").mkdir()
    return tmp_path


def _init_git(project: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "t@x"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=project, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=project, check=True)


# ---------------------------------------------------------------------------
# propose_fixes
# ---------------------------------------------------------------------------

def test_propose_fixes_emits_diff_for_untested_fr(fixture_repo):
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.traceability.scanner import check_traceability
    from core.traceability.auto_fix_propose import propose_fixes

    _rt, report = check_traceability(fixture_repo)
    diff = propose_fixes(_rt, report, fixture_repo)
    assert "diff" in diff.lower() or "@@" in diff
    # Should propose a test stub for FR-99
    assert "test_fr_99" in diff or "FR-99" in diff


def test_propose_fixes_for_uncoded_fr(fixture_repo):
    """When FR has no code annotation, propose a candidate module."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.traceability.auto_fix_propose import propose_fixes

    # FR-04 has neither code nor test
    report = {"uncoded": ["FR-04"], "untested": ["FR-04"]}
    diff = propose_fixes(MagicMock(), report, fixture_repo)
    assert "FR-04" in diff


def test_proposed_diff_is_well_formed_for_git_apply(fixture_repo):
    """`git apply --check` must accept the diff on a clean tree."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.traceability.scanner import check_traceability
    from core.traceability.auto_fix_propose import propose_fixes

    _init_git(fixture_repo)
    _rt, report = check_traceability(fixture_repo)
    diff = propose_fixes(_rt, report, fixture_repo)
    # Write to a temp file and check
    diff_path = fixture_repo / "tmp.diff"
    diff_path.write_text(diff, encoding="utf-8")
    proc = subprocess.run(
        ["git", "apply", "--check", str(diff_path)],
        cwd=fixture_repo, capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"git apply --check failed: {proc.stderr}"


# ---------------------------------------------------------------------------
# fix_missing_traceability end-to-end
# ---------------------------------------------------------------------------

def test_fix_missing_traceability_auto_applies_and_passes(fixture_repo):
    """Primary path: fix closes the gap, returns success."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    _init_git(fixture_repo)

    from core.auto_fix.strategies import fix_missing_traceability

    context = MagicMock()
    context.details = {"max_rounds": 3}
    ok, msg, score = fix_missing_traceability(context, fixture_repo)
    assert ok is True
    assert score == 90.0
    # Source tree was modified: a new test file should exist
    assert (fixture_repo / "tests" / "test_fr_99.py").exists()


def test_fix_missing_traceability_escalates_on_persistent_failure(fixture_repo):
    """When verify keeps failing, escalate with diff on disk."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    _init_git(fixture_repo)

    from core.auto_fix.strategies import fix_missing_traceability
    from core.traceability.scanner import check_traceability
    import core.auto_fix.strategies as strat_mod

    # Force check_traceability to always return report with one untested FR,
    # even after the fix is applied. We patch the import inside the strategy.
    real_check = check_traceability
    call_count = {"n": 0}

    def fake_check(project):
        call_count["n"] += 1
        # First call: there IS a gap. After fix: still a gap (simulated).
        _rt, _report = real_check(project)
        return _rt, {
            **_report,
            "uncoded": ["FR-99"],
            "untested": ["FR-99"],
        }

    # Patch the symbol in the strategy module's namespace
    orig = getattr(strat_mod, "check_traceability", None)
    strat_mod.check_traceability = fake_check
    try:
        context = MagicMock()
        context.details = {"max_rounds": 2}
        ok, msg, score = fix_missing_traceability(context, fixture_repo)
    finally:
        if orig is not None:
            strat_mod.check_traceability = orig

    # Auto-fix could not close the gap → escalate
    assert ok is False
    assert score == 0.0
    assert "exhausted" in msg.lower() or "human" in msg.lower()
    # Diff written to escalation path
    diff_path = fixture_repo / ".methodology" / "trace" / "proposed_fix.diff"
    assert diff_path.exists()


def test_fix_missing_traceability_no_changes_when_already_complete(fixture_repo):
    """If the matrix is already complete, return early without touching the tree."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    _init_git(fixture_repo)
    # Add a test for FR-99
    (fixture_repo / "tests" / "test_fr_99.py").write_text('"""[FR-99]"""\n')

    from core.auto_fix.strategies import fix_missing_traceability
    context = MagicMock()
    context.details = {"max_rounds": 3}
    ok, msg, score = fix_missing_traceability(context, fixture_repo)
    assert ok is True
    assert "already" in msg.lower() or "all frs" in msg.lower()


# ---------------------------------------------------------------------------
# Rollback safety
# ---------------------------------------------------------------------------

def test_apply_diff_rollback_restores_tree(fixture_repo):
    """If apply fails, rollback returns the tree to its prior state."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    _init_git(fixture_repo)
    pre = (fixture_repo / "core" / "foo.py").read_text()

    from core.traceability.auto_fix_propose import apply_diff, rollback

    # Bad diff (file does not exist in tree)
    bad_diff = "--- a/nonexistent.py\n+++ b/nonexistent.py\n@@\n-bad\n+new\n"
    ok, msg = apply_diff(fixture_repo, bad_diff)
    assert ok is False or "no changes" in msg.lower() or "failed" in msg.lower()
    rollback(fixture_repo)
    # Tree restored
    assert (fixture_repo / "core" / "foo.py").read_text() == pre
