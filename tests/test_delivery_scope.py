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
import re
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

# Production modules that ENUMERATE a consumer project's tree themselves —
# the population that determines what a gate measures. Each was deciding
# independently what to exclude before Round 37: lang_patterns.py and
# treesitter_js.py via SKIP_DIRS, auto_fix_propose.py by excluding only
# "node_modules", spec_logic_checker.py by excluding only "venv" and
# "__pycache__".
#
# core/traceability/scanner.py is deliberately NOT here: it never walks a
# tree, it calls iter_source_files / iter_test_files, so it inherits the
# scope rather than stating one. Adding it would demand an import it has no
# use for — a rule satisfied by ceremony instead of by structure.
_PROJECT_TREE_WALKERS = (
    "core/utils/lang_patterns.py",
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


def test_the_scanner_delegates_its_scope_instead_of_stating_one() -> None:
    """The reason core/traceability/scanner.py is exempt above.

    If it ever grows its own walk, the exemption stops being justified and
    this fails — the exclusion cannot outlive its premise.
    """
    source = (REPO / "core/traceability/scanner.py").read_text(encoding="utf-8")
    assert "iter_source_files" in source
    assert ".rglob(" not in source and ".iterdir(" not in source, (
        "core/traceability/scanner.py now walks the tree itself — add it to "
        "_PROJECT_TREE_WALKERS and give it the scope SSOT"
    )


# ---------------------------------------------------------------------------
# Round 50 站0 — every path the framework writes has to be classified.
#
# HARNESS_VOLATILE_PATHS is a hand-maintained list of twelve filenames. A file
# absent from it is treated as a project deliverable, which means "nobody
# classified this yet" and "this is the project's work product" are the same
# state.
#
# Measured 2026-08-13 on a full P1–P8 run. `.methodology/workflow_blocks.jsonl`
# — created by Round 48 站2, six rounds after this list was written — is not on
# it, and neither is `.methodology/agent_b_approvals/`. Both are framework
# bookkeeping, and both stopped a milestone:
#
#     P6 exit blocked by .methodology/workflow_blocks.jsonl
#     P6 exit blocked by .methodology/agent_b_approvals/FINAL_SIGN_OFF.md.json
#     P2 exit blocked by .methodology/agent_b_approvals/ADR.md.json
#
# Round 44 站2's check is right to refuse a milestone over an uncommitted
# deliverable. It was handed the wrong set.
#
# The fix is not a longer list. It is that the two populations are declared,
# and adding a path to neither is a test failure rather than a default.
# ---------------------------------------------------------------------------

_METHODOLOGY_LITERAL = re.compile(r"^\.methodology/[A-Za-z0-9_.\-/]+$")


def _methodology_paths_written_by_the_framework() -> dict[str, set[str]]:
    """Every `.methodology/...` string literal in core/ and cli/.

    AST rather than grep so a path inside a comment or a docstring — where it
    is documentation, not a write — does not have to be classified.
    """
    found: dict[str, set[str]] = {}
    for package in ("core", "cli"):
        for path in sorted((REPO / package).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - a syntax error is another test's job
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if _METHODOLOGY_LITERAL.match(node.value):
                        rel = node.value.rstrip("/")
                        found.setdefault(rel, set()).add(
                            path.relative_to(REPO).as_posix())
    return found


def test_every_methodology_path_is_bookkeeping_or_a_deliverable() -> None:
    from core.utils.delivery_scope import (
        METHODOLOGY_DELIVERABLES,
        HARNESS_VOLATILE_PATHS,
        HARNESS_VOLATILE_PREFIXES,
    )

    volatile = {p.rstrip("/") for p in HARNESS_VOLATILE_PATHS}
    volatile_prefixes = tuple(p.rstrip("/") for p in HARNESS_VOLATILE_PREFIXES)
    deliverables = {p.rstrip("/") for p in METHODOLOGY_DELIVERABLES}

    def classified(rel: str) -> bool:
        if rel in volatile or rel in deliverables:
            return True
        return any(rel == p or rel.startswith(p + "/")
                   for p in volatile_prefixes + tuple(deliverables))

    unclassified = {
        rel: sorted(where)
        for rel, where in _methodology_paths_written_by_the_framework().items()
        if not classified(rel)
    }
    assert not unclassified, (
        "path(s) the framework writes under .methodology/ that are in neither "
        "registry. Silence here means the milestone check treats them as the "
        "project's work product:\n  "
        + "\n  ".join(f"{k}  <- {', '.join(v)}" for k, v in sorted(unclassified.items()))
    )


def test_the_two_registries_do_not_overlap() -> None:
    """A path cannot be both bookkeeping and a deliverable."""
    from core.utils.delivery_scope import (
        METHODOLOGY_DELIVERABLES,
        HARNESS_VOLATILE_PATHS,
    )

    both = {p.rstrip("/") for p in HARNESS_VOLATILE_PATHS} & {
        p.rstrip("/") for p in METHODOLOGY_DELIVERABLES}
    assert not both, both


def test_the_round_48_ledger_is_bookkeeping() -> None:
    """The specific regression: a ledger the framework created and forgot."""
    from core.utils.delivery_scope import is_harness_volatile

    assert is_harness_volatile(".methodology/workflow_blocks.jsonl")
    assert is_harness_volatile(
        ".methodology/agent_b_approvals/FINAL_SIGN_OFF.md.json")
