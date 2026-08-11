"""Delivery scope — the one answer to "which files belong to this project".

Round 37. Every scorer that walks a consumer project's tree needs to know
what is part of the project and what is scratch. Before this module that
question was answered independently in five places, each with its own
denylist:

  core/utils/lang_patterns.py       SKIP_DIRS (node_modules, dist, build, …)
  core/traceability/scanner.py      SKIP_DIRS + a Python-only extras set
  harness/lang_scanners/treesitter_js.py   SKIP_DIRS
  core/traceability/auto_fix_propose.py    "node_modules" only
  scripts/spec_logic_checker.py            "venv"/"__pycache__" only

and a sixth, `harness/git_strategy.py`'s _GITIGNORE_ENTRIES, answered it for
git. A denylist is structurally one directory behind: `.venv/` was added
after the first incident, `.claude/worktrees/` after the second (taskq-renew,
2026-08-05), and the scanner side of that second fix was never made.

Measured cost of the gap: taskq-renew's trace attestation carried 32 FR->code
links pointing into `.claude/worktrees/agent-<id>/` — a Claude Code Agent-tool
scratch worktree present on the developer's disk and absent from a CI
checkout. Local re-derivation produced 80 links / content_sha256 94a71dc…;
CI produced 48 links / 3013d0f… and the ASPICE Traceability Check failed on
every push from Phase 3 onward.

The fix is not a longer denylist. CI checks out exactly what git tracks, so
git's own notion of the project is the only definition that cannot drift from
the delivered tree:

    git ls-files --cached --others --exclude-standard

  --cached                   already delivered
  --others --exclude-standard  written but not committed yet — Phase 3 TDD
                             writes the implementation before it commits it,
                             and a scope that could not see it would report
                             the FR as uncoded and block on work that is
                             sitting right there
  (everything .gitignore excludes is excluded)  .venv, .claude/worktrees,
                             and every future directory nobody has thought of

One line in .gitignore now drives git and every scanner at once.

A project that is not a git repository keeps the pre-Round-37 behaviour: the
SKIP_DIRS denylist. That path is unchanged on purpose — narrowing it would
alter projects that have nothing to do with the defect this closes.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Iterator

from core.utils.lang_patterns import SKIP_DIRS

__all__ = ["iter_delivered_files", "delivered_file_set", "delivered_tree_digest",
           "committed_tree_digest", "is_git_repo", "is_harness_volatile",
           "HARNESS_VOLATILE_PATHS", "HARNESS_VOLATILE_PREFIXES"]

_LS_FILES = ("git", "ls-files", "--cached", "--others", "--exclude-standard",
             "-z")
_TIMEOUT = 30


def is_git_repo(root: Path) -> bool:
    """True when *root* is inside a git work tree."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _git_listing(root_str: str) -> tuple[str, ...] | None:
    """`git ls-files` output for *root_str*, or None when git cannot answer.

    NUL-separated so a path containing a newline (legal on POSIX) cannot be
    read as two files.

    Deliberately NOT cached. The first draft of this module memoised the
    listing per root; core/auto_fix/strategies.py writes a new test file and
    immediately re-scans to verify the fix, and the cached answer — taken
    before the file existed — hid it. A stale view of the tree is the exact
    defect this module was written to remove, so it must not reintroduce one
    at a shorter timescale. `git ls-files` costs a few milliseconds.
    """
    try:
        proc = subprocess.run(
            [_LS_FILES[0], "-C", root_str, *_LS_FILES[1:]],
            capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return tuple(p for p in proc.stdout.split("\0") if p)


def _denylist_walk(root: Path) -> Iterator[Path]:
    """Pre-Round-37 behaviour, kept for non-git directories."""
    for path in sorted(root.rglob("*")):
        if path.is_file() and not (set(path.parts) & SKIP_DIRS):
            yield path


def iter_delivered_files(root: Path) -> Iterator[Path]:
    """Yield every file *root* delivers, sorted, as absolute paths.

    Extension filtering is deliberately NOT done here — callers know their
    own language (see core.utils.lang_patterns.source_extensions). Keeping
    this module extension-agnostic is also what keeps lang_patterns free to
    import it without a cycle.
    """
    root = Path(root)
    # One subprocess, not two: `git ls-files` already fails outside a work
    # tree, so a separate is_git_repo probe would only double the cost.
    listing = _git_listing(str(root))
    if listing is None:
        yield from _denylist_walk(root)
        return
    for rel in sorted(listing):
        path = root / rel
        # A gitlink (submodule) is listed as a single entry that is a
        # directory on disk; its contents belong to the submodule, not here.
        if path.is_file():
            yield path


def delivered_file_set(root: Path) -> set[str]:
    """`iter_delivered_files` as resolved absolute path strings."""
    return {str(p.resolve()) for p in iter_delivered_files(root)}


# ── which files, versus which version of them (Round 44 站1) ────────────────
#
# The module above answers "which files belong to this project". A gate
# verdict needs a second answer — "which version of them was I measured on" —
# and `delivered_tree_digest` was built on the first without the two being
# separated. Two consequences, both measured:
#
#   * `record_verdict` computed the digest and then appended its own line to
#     `.methodology/gate_verify.jsonl`, a delivered file, so the digest it
#     recorded could never match itself again (2245e64, 2026-08-11). The fix
#     there named two files in a frozenset. Every `.methodology/` file the
#     harness writes after taking a digest is the next instance of it: on
#     taskq-advance `verify-gate` ran three times in the six minutes before
#     its P3→P4 advance, each recording a PASS at one unchanged `git_sha`
#     against a different tree digest.
#   * `phase_completed[N].sha` names a commit while the checks that produced
#     it ran on the working tree, and nothing compared the two.
#
# `HARNESS_VOLATILE_*` is the declared set of paths the harness writes as a
# side effect of running: append-only ledgers, caches, locks and progress
# state. None of them is an input to any score, so leaving them out of a
# verdict's tree cannot change what that verdict means.
#
# It is deliberately NOT "all of `.methodology/`". Station 0 premise 1
# measured that `harness_config.json` carries `crg_excludes` and
# `crg_cohesion_healthy`, which decide what the architecture score is
# measured over (core/harness_config.py:317) while only `cohesion_healthy`
# reaches the gate result. Excluding the directory would let a scoring input
# move under a recorded PASS.
#
# It is a denylist, which this module's own docstring rules out for delivery
# scope — and the reason it is acceptable here is that its two failure
# directions are not symmetric. An unregistered volatile file makes a digest
# refuse to match, which is noise; an unregistered *config* file under a
# positive registry would make a verdict outlive a change to its own inputs,
# which is a wrong PASS. `tests/test_verdict_digest_scope.py` turns the noise
# into a red test at development time.
HARNESS_VOLATILE_PATHS: frozenset[str] = frozenset({
    ".methodology/state.json",            # last_update moves on every command
    ".methodology/heartbeat.json",
    ".methodology/last_block.md",
    ".methodology/gate_verify.jsonl",     # this digest's own ledger
    ".methodology/degradations.jsonl",
    ".methodology/gate_timestamps.jsonl",
    ".methodology/sessions_spawn.log",
    ".methodology/hooks.log",
    ".methodology/effort_metrics.db",
    ".methodology/fr_progress.json",      # per-FR progress, rewritten per step
    ".methodology/.gate1_scores.json",
    ".methodology/.txn_journal.json",
})

HARNESS_VOLATILE_PREFIXES: tuple[str, ...] = (
    ".methodology/decision_logs/",   # audit trail; left scoring in R21 站3
    ".methodology/lessons/",         # cross-round failure memory
    ".methodology/crash/",           # crash-triage dumps
    ".sessi-work/",                  # per-round scratch, deleted by advance
)


def is_harness_volatile(rel: str) -> bool:
    """True when *rel* (repo-relative, posix) is harness bookkeeping.

    Lock files are matched by suffix rather than listed: they are created and
    removed by name of whatever they guard, so enumerating them would be the
    one-directory-behind denylist this module exists to avoid.
    """
    if rel in HARNESS_VOLATILE_PATHS or rel.endswith(".lock"):
        return True
    return rel.startswith(HARNESS_VOLATILE_PREFIXES)


def _digest(entries: "Iterator[tuple[str, str]]") -> str:
    """sha256 over ``(repo-relative posix path, content fingerprint)`` pairs.

    One implementation for both digests below, so "the tree on disk" and "the
    tree git recorded" cannot drift into two different rulers (Round 33: one
    contract, one statement). Path *and* content, because content alone would
    miss a file being added or renamed — exactly how taskq-renew's graph fell
    behind its tree.
    """
    h = hashlib.sha256()
    for rel, fingerprint in entries:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(fingerprint.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _fingerprint(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def delivered_tree_digest(root: Path, *, exclude: frozenset[str] = frozenset()) -> str:
    """A sha256 over the delivered tree **as it stands on disk**.

    Round 38: a verdict has to carry the tree it was measured on. Round 37's
    lesson was that a number is only as good as the tree it was measured over;
    the same is true one level up, of the PASS that number produced. Without
    this, `advance-phase` can only ask "did a gate verdict exist?", which a
    verdict from before the last three edits answers just as well as a current
    one.

    Unreadable files contribute their error rather than being skipped —
    a tree we could only partly read must not digest the same as one we read
    completely (Round 32/35: an unmeasurable input is not a passing input).

    A symlink is digested by its target *path*, not by the bytes it points at,
    so this agrees with `committed_tree_digest` — git stores the target string
    — and so a link cannot be re-pointed without the digest noticing. taskq-api
    delivers 13 of them (its integration tests link to the unit tests).

    Round 44: `HARNESS_VOLATILE_*` paths are always omitted. `exclude` remains
    for callers with a further path of their own to drop.
    """
    root = Path(root)

    def _entries() -> "Iterator[tuple[str, str]]":
        for path in iter_delivered_files(root):
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:  # pragma: no cover - iter_delivered_files is rooted
                rel = path.as_posix()
            if rel in exclude or is_harness_volatile(rel):
                continue
            try:
                if path.is_symlink():
                    yield rel, _fingerprint(os.readlink(path).encode("utf-8"))
                else:
                    yield rel, _fingerprint(path.read_bytes())
            except OSError as exc:
                yield rel, f"<unreadable: {exc.__class__.__name__}>"

    return _digest(_entries())


def committed_tree_digest(root: Path, rev: str = "HEAD") -> str:
    """The same digest, over the tree **git recorded at** *rev*.

    Round 44 站1. `delivered_tree_digest` answers "what is on disk right now",
    which is the right question for a scanner and the wrong one for a
    milestone: `state.json::phase_completed[N].sha` names a commit, and a
    reader of that record needs to know whether the checks that produced it
    ran on what the commit contains. On taskq-advance they did not — the
    `@given` tests that unblocked its P3→P4 entry entered git fourteen
    minutes after the phase had turned over.

    Returns `""` when *rev* cannot be resolved (rebased away, garbage
    collected, not a git repo). A caller must treat that as "no measurement",
    never as a mismatch — Round 32/35.
    """
    root = Path(root)
    try:
        listing = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "-r", "-z", rev],
            capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if listing.returncode != 0:
        return ""

    wanted: list[tuple[str, str]] = []   # (object id, repo-relative path)
    for record in listing.stdout.split("\0"):
        if not record:
            continue
        meta, _, rel = record.partition("\t")
        parts = meta.split()
        if len(parts) < 3 or parts[1] != "blob":
            # `commit` entries are submodule gitlinks; their contents belong
            # to the submodule, matching iter_delivered_files' own skip.
            continue
        if is_harness_volatile(rel):
            continue
        wanted.append((parts[2], rel))

    if not wanted:
        return _digest(iter(()))

    contents = _batch_blob_fingerprints([oid for oid, _rel in wanted], root)
    if contents is None:
        return ""
    return _digest(
        (rel, contents.get(oid, "<unreadable: MissingBlob>"))
        for oid, rel in wanted
    )


def _batch_blob_fingerprints(
    object_ids: "list[str]", root: Path,
) -> "dict[str, str] | None":
    """`{object id: sha256 of its bytes}` via one `git cat-file --batch`.

    One subprocess for the whole tree; `git cat-file` streams
    ``<oid> <type> <size>\\n<payload>\\n`` per request. None on any failure,
    which the caller turns into "no measurement".
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "cat-file", "--batch"],
            input=("\n".join(object_ids) + "\n").encode("ascii"),
            capture_output=True, timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if proc.returncode != 0:
        return None

    out: bytes = proc.stdout
    result: dict[str, str] = {}
    pos = 0
    while pos < len(out):
        eol = out.find(b"\n", pos)
        if eol == -1:
            break
        header = out[pos:eol].decode("utf-8", "replace").split()
        if len(header) < 3:
            return None
        try:
            size = int(header[2])
        except ValueError:
            return None
        start = eol + 1
        result[header[0]] = _fingerprint(out[start:start + size])
        pos = start + size + 1   # trailing newline git appends after payload
    return result
