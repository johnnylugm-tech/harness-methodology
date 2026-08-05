"""The scan scope must be the delivery scope (Round 37 站0/站1).

Measured defect, taskq-renew 2026-08-05: the traceability scanner walked
`.claude/worktrees/agent-<id>/` — a Claude Code Agent-tool scratch worktree
that exists on the developer's disk and does NOT exist in a CI checkout. The
attestation it produced carried 32 phantom FR->code links (80 total links,
content_sha256 94a71dc…); CI re-derived 48 links and 3013d0f… and the
`ASPICE Traceability Check` / `gate-check` jobs failed on every push from
Phase 3 onward (48 red runs out of 52).

The root cause is that "what belongs to this project" was stated twice with
two different answers:

  - `harness/git_strategy.py` `_GITIGNORE_ENTRIES` — has `.claude/worktrees/`
  - `core/utils/lang_patterns.SKIP_DIRS`             — does not

A denylist is structurally one directory behind (`.venv` was added after the
first incident, `.claude/worktrees/` after the second). The scope git uses is
the scope CI checks out, so git is the only authority that cannot drift from
the delivered tree.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A git repo shaped like the taskq-renew failure: an ignored agent
    worktree holding a full copy of the source tree."""
    _git("init", "-q", cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=tmp_path)
    _git("config", "user.name", "t", cwd=tmp_path)

    (tmp_path / ".gitignore").write_text(
        ".venv/\n.claude/worktrees/\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "real.py").write_text("# [FR-01]\ndef real():\n    pass\n",
                                 encoding="utf-8")
    _git("add", ".gitignore", "src/real.py", cwd=tmp_path)
    _git("commit", "-q", "-m", "seed", cwd=tmp_path)

    scratch = tmp_path / ".claude" / "worktrees" / "agent-deadbeef" / "src"
    scratch.mkdir(parents=True)
    (scratch / "real.py").write_text("# [FR-01]\ndef real():\n    pass\n",
                                     encoding="utf-8")
    return tmp_path


def test_scan_scope_excludes_gitignored_paths(repo: Path) -> None:
    """The agent worktree copy is on disk but is not delivered."""
    from core.utils.delivery_scope import iter_delivered_files

    found = {str(p.relative_to(repo)) for p in iter_delivered_files(repo)}

    assert "src/real.py" in found
    assert not [p for p in found if "worktrees" in p], (
        "a .gitignore'd agent worktree is not part of the delivered tree; "
        f"got {sorted(p for p in found if 'worktrees' in p)}"
    )


def test_uncommitted_new_work_is_still_in_scope(repo: Path) -> None:
    """Negative control for the test above.

    Excluding gitignored paths must NOT collapse into "tracked files only":
    during Phase 3 TDD the implementation file exists and is not committed
    yet. If it fell out of scope, the FR would read as uncoded and the gate
    would block on work that is sitting right there.
    """
    from core.utils.delivery_scope import iter_delivered_files

    (repo / "src" / "brand_new.py").write_text(
        "# [FR-02]\ndef fresh():\n    pass\n", encoding="utf-8")

    found = {str(p.relative_to(repo)) for p in iter_delivered_files(repo)}
    assert "src/brand_new.py" in found


def test_non_git_directory_still_yields_its_sources(tmp_path: Path) -> None:
    """A project that is not a git repo keeps the pre-Round-37 behaviour."""
    from core.utils.delivery_scope import iter_delivered_files

    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.py").write_text("y = 2\n",
                                                      encoding="utf-8")

    found = {str(p.relative_to(tmp_path)) for p in iter_delivered_files(tmp_path)}
    assert "mod.py" in found
    assert "node_modules/dep.py" not in found


# --------------------------------------------------------------------------
# Meta-test: every walker of a consumer project's source tree uses the SSOT
# --------------------------------------------------------------------------

# Production modules that enumerate a CONSUMER PROJECT's source tree — the
# population that determines what a gate measures. Each was independently
# deciding what to exclude before Round 37: scanner.py and lang_patterns.py
# via SKIP_DIRS, treesitter_js.py via the same set, auto_fix_propose.py by
# excluding only "node_modules", spec_logic_checker.py by excluding only
# "venv"/"__pycache__".
_PROJECT_TREE_WALKERS = (
    "core/utils/lang_patterns.py",
    "core/traceability/scanner.py",
    "core/traceability/auto_fix_propose.py",
    "harness/lang_scanners/treesitter_js.py",
    "scripts/spec_logic_checker.py",
)

_SSOT_MODULE = "delivery_scope"


def _imports_names(path: Path) -> set[str]:
    """Every dotted module name imported anywhere in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    return names


@pytest.mark.parametrize("rel", _PROJECT_TREE_WALKERS)
def test_every_project_tree_walker_uses_the_shared_scope(rel: str) -> None:
    path = REPO / rel
    assert any(_SSOT_MODULE in name for name in _imports_names(path)), (
        f"{rel} enumerates a consumer project's source tree but does not read "
        f"core.utils.delivery_scope — that is a second, drifting answer to "
        f"'what belongs to this project' (Round 37 D1)."
    )


def test_the_walker_list_names_only_files_that_walk_a_tree() -> None:
    """Negative control: a stale entry must not become a free pass.

    If a module is renamed or stops walking the tree, the parametrised test
    above would still pass by importing delivery_scope for some other reason
    — or vanish silently. This pins both ends.
    """
    walk_calls = {"rglob", "glob", "iterdir", "walk"}
    for rel in _PROJECT_TREE_WALKERS:
        path = REPO / rel
        assert path.is_file(), f"{rel} is listed as a tree walker but is gone"
        source = path.read_text(encoding="utf-8")
        assert any(f".{c}(" in source or f"{c}(" in source for c in walk_calls) \
            or _SSOT_MODULE in source, (
                f"{rel} no longer walks a tree and no longer reads the scope "
                f"SSOT — drop it from _PROJECT_TREE_WALKERS"
            )
