"""
Tests for init-project .gitignore bootstrap (bug #1 fix).

Bug discovered during integration-test E2E: init-project did not write
.gitignore entries for .venv/, __pycache__/, etc. When a fully-automated
pipeline runs `git add -A` after `pip install -r requirements.txt`, the
semgrep-core binary (197MB) was committed and tripped GitHub's GH001
large-file pre-receive hook. Re-running init-project with --overwrite
must produce a .gitignore that excludes these entries.

Reference: integration-test/AUDIT_LOG entry 2026-06-15-bug-1
"""
from pathlib import Path

from harness.git_strategy import GitStrategy, _GITIGNORE_ENTRIES


# Entries that MUST be present in the bootstrap-time .gitignore
# (covers the venv + Python cache + harness runtime).
_REQUIRED_ENTRIES = {
    ".venv/",
    "venv/",
    "ENV/",
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".coverage",
    "htmlcov/",
    ".mutmut-cache/",
    ".code-review-graph/",
    ".sessi-work/",
    ".methodology/last_block.md",
    ".methodology/heartbeat.json",
    ".methodology/steering_history.json",
    ".methodology/trace/attestation.latest.json",
    # Pure debug/trace logs: appended every phase run, read only from the working
    # tree (HR-10 A/B audit), never functionally required from git history. Tracking
    # them produced a perpetually-dirty tree fixed by manual chore(e2e-collect) commits.
    ".methodology/sessions_spawn.log",
    ".harness/traces/",
}


def test_gitignore_entries_constant_contains_required() -> None:
    """Framework constant must enumerate pipeline-mode blockers."""
    missing = _REQUIRED_ENTRIES - set(_GITIGNORE_ENTRIES)
    assert not missing, (
        f"_GITIGNORE_ENTRIES missing required entries: {missing}. "
        f"Without these, automated pipelines commit semgrep-core and "
        f"trip GH001."
    )


def test_ensure_gitignore_creates_file_with_all_entries(tmp_path: Path) -> None:
    """Cold-start: no .gitignore exists → ensure_gitignore creates one with all entries."""
    git = GitStrategy(tmp_path, enabled=True, push=False)
    git.ensure_gitignore()
    gi = tmp_path / ".gitignore"
    assert gi.exists(), "ensure_gitignore must create .gitignore on cold start"
    content = gi.read_text(encoding="utf-8")
    for entry in _REQUIRED_ENTRIES:
        assert entry in content, f"Missing required entry: {entry}"


def test_ensure_gitignore_idempotent(tmp_path: Path) -> None:
    """Two consecutive calls must not duplicate entries (line-based check)."""
    git = GitStrategy(tmp_path, enabled=True, push=False)
    git.ensure_gitignore()
    git.ensure_gitignore()
    content = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    lines = [ln.strip() for ln in content.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    # Each entry should appear on its own line exactly once
    for entry in _REQUIRED_ENTRIES:
        assert lines.count(entry) == 1, (
            f"Entry {entry!r} appears {lines.count(entry)} times in .gitignore lines: {lines}"
        )


def test_ensure_gitignore_preserves_user_entries(tmp_path: Path) -> None:
    """User's pre-existing .gitignore must NOT be clobbered."""
    user_gi = tmp_path / ".gitignore"
    user_gi.write_text("# my custom comment\nmy_secret.key\n", encoding="utf-8")
    git = GitStrategy(tmp_path, enabled=True, push=False)
    git.ensure_gitignore()
    content = user_gi.read_text(encoding="utf-8")
    assert "my_secret.key" in content, "User entries must be preserved"
    assert content.startswith("# my custom comment"), "User content must come first"


def test_ensure_gitignore_disabled_no_op(tmp_path: Path) -> None:
    """enabled=False must skip ensure_gitignore entirely."""
    git = GitStrategy(tmp_path, enabled=False, push=False)
    git.ensure_gitignore()
    assert not (tmp_path / ".gitignore").exists()
