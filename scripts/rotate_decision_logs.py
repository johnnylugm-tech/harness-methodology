#!/usr/bin/env python3
"""
Rotate / archive `.methodology/decision_logs/` to prevent unbounded growth.

Background (SG-8 from robustness audit):
  `.methodology/decision_logs/` accumulates one yaml per gate evaluation.
  Long-running projects can reach 10k+ files. There was no retention policy
  so the directory grew without bound, slowing `ls`, git operations, and
  IDE indexers.

This script:
  * Groups logs by `YYYY-MM-DD/` subdirectory (the writer's existing layout).
  * Tars+gzips any date directory older than --retention-days (default 30).
  * Removes the source directory after successful archive.
  * Skips archive directories (`.tar.gz`) and the current/recent days.

The default keeps a 30-day rolling window of raw yaml — enough for
incident investigation — while older history is compressed but still
inspectable (zcat / tar -xzf).

Usage:
  python3 scripts/rotate_decision_logs.py --project . [--retention-days 30] [--dry-run]

Exit codes:
  0  — rotation completed (may be a no-op if nothing to archive)
  1  — hard error (project missing, decision_logs/ not found in a bad way)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import shutil
import sys
import tarfile
from pathlib import Path

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date(name: str) -> _dt.date | None:
    """Return parsed date from a `YYYY-MM-DD` directory name, or None."""
    if not _DATE_RE.match(name):
        return None
    try:
        return _dt.date.fromisoformat(name)
    except ValueError:
        return None


def _archive_dir(date_dir: Path, *, dry_run: bool) -> tuple[bool, str]:
    """Create `<date_dir>.tar.gz` alongside `date_dir`, then remove the source."""
    archive_path = date_dir.with_suffix(".tar.gz")
    if archive_path.exists():
        return False, f"archive already exists: {archive_path.name}"
    if dry_run:
        return True, f"[dry-run] would archive {date_dir.name} → {archive_path.name}"
    try:
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(date_dir, arcname=date_dir.name)
    except (OSError, tarfile.TarError) as exc:
        # Clean up partial archive on failure so a retry can succeed.
        try:
            archive_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False, f"tar failed for {date_dir.name}: {exc}"
    try:
        shutil.rmtree(date_dir)
    except OSError as exc:
        return False, f"archive created but source removal failed for {date_dir.name}: {exc}"
    return True, f"archived {date_dir.name} → {archive_path.name}"


def rotate(project: Path, *, retention_days: int, dry_run: bool) -> int:
    """Archive date directories older than `retention_days`. Returns exit code."""
    logs_dir = project / ".methodology" / "decision_logs"
    if not logs_dir.exists():
        print(f"[INFO] {logs_dir} does not exist — nothing to rotate.")
        return 0
    if not logs_dir.is_dir():
        print(f"[ERROR] {logs_dir} exists but is not a directory.", file=sys.stderr)
        return 1

    cutoff = _dt.date.today() - _dt.timedelta(days=retention_days)
    archived = 0
    skipped = 0
    errors: list[str] = []

    for entry in sorted(logs_dir.iterdir()):
        if not entry.is_dir():
            continue
        d = _parse_date(entry.name)
        if d is None:
            # Non-date directories are left alone (operator-managed).
            skipped += 1
            continue
        if d >= cutoff:
            skipped += 1
            continue
        ok, msg = _archive_dir(entry, dry_run=dry_run)
        if ok:
            archived += 1
            print(f"  {msg}")
        else:
            errors.append(msg)
            print(f"  [WARN] {msg}", file=sys.stderr)

    print(
        f"\n[rotate-decision-logs] retention={retention_days}d, cutoff={cutoff.isoformat()}, "
        f"archived={archived}, skipped={skipped}, errors={len(errors)}"
    )
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--project", default=".", help="Project root (default: .)")
    p.add_argument(
        "--retention-days", type=int, default=30,
        help="Keep raw yaml for this many days; older directories are tar.gz'd (default 30).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be archived without modifying the filesystem.",
    )
    args = p.parse_args(argv)
    project = Path(args.project).resolve()
    if not project.exists():
        print(f"[ERROR] project not found: {project}", file=sys.stderr)
        return 1
    return rotate(project, retention_days=args.retention_days, dry_run=args.dry_run)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
