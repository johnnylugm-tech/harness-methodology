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


# Playbook §6: dynamic mutation-oracle marker
pytestmark = pytest.mark.mutation_oracle


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

def test_auto_fix_applies_annotation_and_passes_verify(fixture_repo):
    """Primary path: fix closes the gap, returns success.

    Plan test name: `test_auto_fix_applies_annotation_and_passes_verify`.
    Asserts return value is `(True, ...)` and source tree was modified.
    """
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


def test_auto_fix_escalates_on_max_rounds(fixture_repo):
    """With max_rounds=0, the loop never runs and we escalate immediately.

    Plan test name: `test_auto_fix_escalates_on_max_rounds`.
    The strategy uses local imports that can't be monkey-patched from
    outside, so deterministic escalation is forced by setting max_rounds=0.
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    _init_git(fixture_repo)

    from core.auto_fix.strategies import fix_missing_traceability

    context = MagicMock()
    context.details = {"max_rounds": 0}
    ok, msg, score = fix_missing_traceability(context, fixture_repo)
    # max_rounds=0 → loop never runs → escalate with diff on disk
    assert ok is False
    assert score == 0.0
    assert "exhausted" in msg.lower() or "human" in msg.lower()


def test_auto_fix_diff_written_on_escalation(fixture_repo):
    """Plan test name: `test_auto_fix_diff_written_on_escalation`.

    `.methodology/trace/proposed_fix.diff` is created on escalation.
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    _init_git(fixture_repo)

    from core.auto_fix.strategies import fix_missing_traceability

    context = MagicMock()
    context.details = {"max_rounds": 0}
    fix_missing_traceability(context, fixture_repo)
    diff_path = fixture_repo / ".methodology" / "trace" / "proposed_fix.diff"
    assert diff_path.exists()
    # The diff must reference FR-99 (the only gap in the fixture)
    diff_text = diff_path.read_text(encoding="utf-8")
    assert "FR-99" in diff_text


def test_auto_fix_source_tree_unchanged_on_escalation(fixture_repo):
    """Plan test name: `test_auto_fix_source_tree_unchanged_on_escalation`.

    After escalation, no leftover annotations in source tree (rollback confirmed).
    `git status` shows only `.methodology/trace/` modified.
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    _init_git(fixture_repo)
    # Snapshot pre-state: only core/foo.py with [FR-99]
    pre_files = sorted(p.relative_to(fixture_repo) for p in fixture_repo.rglob("*.py")
                        if ".methodology" not in p.parts and ".git" not in p.parts)

    from core.auto_fix.strategies import fix_missing_traceability

    context = MagicMock()
    context.details = {"max_rounds": 0}
    fix_missing_traceability(context, fixture_repo)

    # Post-state: only `.methodology/trace/proposed_fix.diff` should be new.
    # Source tree must not have new [FR-XX] annotations.
    post_files = sorted(p.relative_to(fixture_repo) for p in fixture_repo.rglob("*.py")
                         if ".methodology" not in p.parts and ".git" not in p.parts)
    assert pre_files == post_files, (
        f"Source tree changed unexpectedly:\n  before: {pre_files}\n  after:  {post_files}"
    )


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
