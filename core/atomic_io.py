"""
Atomic JSON / text file write helpers for state files in `.methodology/`.

Background (CV-3 from robustness audit):
  Direct `path.write_text(...)` is not crash-safe. Mid-write Ctrl-C,
  OOM kill, disk-full, or NFS hiccup leaves the file truncated or empty.
  `.methodology/state.json`, `fr_progress.json`, `sessions_spawn.log`,
  and `kill_switch/*.json` are all read by hooks, CI workflows, and the
  CLI — a truncated file blocks the entire pipeline.

The pattern below is the standard POSIX atomic-write recipe:
  1. Write the full payload to a temp file in the SAME directory
     (cross-device rename is not atomic).
  2. fsync the temp file (so the data is durable on disk before rename).
  3. os.replace(tmp, target) — atomic on POSIX and Win NTFS.
  4. Best-effort fsync the parent dir so the rename itself is durable.

Cross-process file locking is also provided via `locked_state_update()` —
a context manager using fcntl.flock that serializes read-modify-write
on a single project's state files. See SG-12 in the robustness audit.

Both helpers are no-ops on platforms without fcntl (Windows): the lock
falls through silently. On macOS/Linux they block until the lock is
released (typically <1ms in practice).
"""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

try:
    import fcntl  # POSIX only
except ImportError:  # pragma: no cover  (Windows)
    fcntl = None  # type: ignore[assignment]


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write `content` to `path` atomically.

    Uses a temp file in the same directory + os.replace. Safe against
    mid-write crashes; reader processes never observe a half-written file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # NamedTemporaryFile with delete=False so we control the rename ourselves.
    # dir=path.parent ensures os.replace is on the same filesystem (atomic).
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            # fsync the data to disk before the rename — without this, a
            # crash between rename and disk flush could still leave the
            # target empty on power loss (rare but possible).
            try:
                os.fsync(f.fileno())
            except OSError:  # pragma: no cover  (some filesystems unsupported)
                pass
        os.replace(tmp_path, path)
        # Best-effort: fsync the parent directory so the rename itself is durable.
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:  # pragma: no cover  (Windows / restricted environments)
            pass
    except Exception:
        # Cleanup: if anything failed before os.replace, remove the temp file
        # so we don't leave orphaned `.statefile.XXXX.tmp` files lying around.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass
        raise


def atomic_write_json(path: Path, data: Any, *, indent: int = 2, ensure_ascii: bool = False) -> None:
    """Serialize `data` as JSON and write atomically to `path`.

    Convenience wrapper around `atomic_write_text`. Use this for every
    state file under `.methodology/` (state.json, fr_progress.json, etc.)
    """
    content = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii, sort_keys=False)
    if not content.endswith("\n"):
        content += "\n"
    atomic_write_text(Path(path), content)


@contextmanager
def file_lock(lock_path: Path, *, blocking: bool = True) -> Iterator[Optional[int]]:
    """Cross-process exclusive lock on `lock_path` using fcntl.flock.

    Usage:
        from core.utils.project_layout import ProjectLayout
        lock_file = ProjectLayout(project).methodology_dir / ".state.lock"
        with file_lock(lock_file):
            # read state.json, mutate, write
            ...

    On Windows (no fcntl) this is a no-op — the yield happens immediately.
    POSIX systems hold the lock for the duration of the with-block; another
    process calling file_lock on the same path will block until release.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is None:  # pragma: no cover  (Windows fallback)
        # No locking available — yield None so callers still work, but
        # concurrent writers will race. SG-12 mitigation only works on POSIX.
        yield None
        return

    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        fcntl.flock(fd, flags)
        try:
            yield fd
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:  # pragma: no cover
                pass
    finally:
        try:
            os.close(fd)
        except OSError:  # pragma: no cover
            pass


def state_lock_path(project_root: Path) -> Path:
    """Conventional lock-file location for a project's state writes."""
    from core.utils.project_layout import ProjectLayout
    return ProjectLayout(project_root).methodology_dir / ".state.lock"


class StateTransaction:
    """Multi-file staged state commit: stage → journal → rename in order.

    Point-fixing individual write sites (#104, #118, 28864f7, dd9129b)
    kept rediscovering the same bug class: a command writes file A, then
    fails while producing file B, leaving the project half-advanced
    (state.json said P9, HANDOVER.md was never regenerated). This class
    makes the write set all-or-nothing-visible:

      with StateTransaction(project) as txn:
          txn.stage_text(handover_path, handover_text)
          txn.stage_json(state_path, state)      # authoritative file LAST
          txn.commit()

    Guarantees:
    - Nothing is visible before commit(); staging writes only `*.txn.tmp`
      siblings. An exception before commit aborts and removes them.
    - commit() first writes a journal (`.methodology/.txn_journal.json`)
      listing every pending rename, then renames in staging order, then
      deletes the journal. A crash mid-commit leaves the journal + the
      un-renamed tmps on disk — `harness doctor` reports the interrupted
      transaction instead of the project silently running on half-state.
    - Stage the highest-authority file (state.json) LAST so a partial
      commit can never claim more progress than the artifacts support.
    """

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.journal_path = self.project_root / ".methodology" / ".txn_journal.json"
        self._staged: list[tuple[Path, Path]] = []  # (tmp, target) in stage order

    def stage_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.txn.tmp")
        with open(tmp, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:  # pragma: no cover  (some filesystems unsupported)
                pass
        self._staged.append((tmp, path))

    def stage_json(self, path: Path, data: Any, *, indent: int = 2,
                   ensure_ascii: bool = False) -> None:
        content = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
        if not content.endswith("\n"):
            content += "\n"
        self.stage_text(path, content)

    def commit(self) -> None:
        if not self._staged:
            return
        atomic_write_json(self.journal_path, {
            "pending": [
                {"tmp": str(tmp), "target": str(target)}
                for tmp, target in self._staged
            ],
        })
        # A failure past this point deliberately KEEPS the journal and any
        # un-renamed tmps: they are the evidence doctor reports. Cleaning
        # them here would turn a detectable interruption into silent
        # half-state — the exact bug class this class exists to kill.
        for tmp, target in self._staged:
            os.replace(tmp, target)
        self.journal_path.unlink(missing_ok=True)
        self._staged.clear()

    def abort(self) -> None:
        """Discard everything staged (pre-commit failures only)."""
        for tmp, _target in self._staged:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:  # pragma: no cover
                pass
        self._staged.clear()

    def __enter__(self) -> "StateTransaction":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None and not self.journal_path.exists():
            # Pre-commit failure: nothing published, clean the tmps.
            # Mid-commit failure (journal on disk) keeps its evidence.
            self.abort()


class FileSnapshot:
    """Capture file states up front; restore() puts every file back exactly
    as captured (content restored byte-for-byte, files absent at capture are
    deleted). Idempotent.

    The reusable primitive for write→git-op→revert-on-failure flows: state
    files are written BEFORE a git commit/push so the committed bytes carry
    them, and must be reverted when the git operation fails so local state
    never claims more progress than git history records (the split-brain
    class). push-milestone (dd9129b), push-checkpoint (dd9129b) and
    finalize-gate 4 (28864f7) each hand-rolled this pattern before this
    class existed; advance-phase didn't revert at all (ghost state).

    Deliberately NOT a context manager: every call site decides failure from
    a boolean result (commit_and_push_* returning False), not an exception —
    an explicit .restore() in the failure branch matches that shape.
    """

    def __init__(self, paths: Iterable[Path]):
        self._snapshot: list[tuple[Path, Optional[bytes]]] = []
        for p in paths:
            p = Path(p)
            try:
                content: Optional[bytes] = p.read_bytes()
            except FileNotFoundError:
                content = None
            self._snapshot.append((p, content))

    def restore(self) -> None:
        for path, content in self._snapshot:
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(f".{path.name}.snap.tmp")
                tmp.write_bytes(content)
                os.replace(tmp, path)
