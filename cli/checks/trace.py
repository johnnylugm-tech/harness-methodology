"""check commands: the trace attestation — build it, verify it, migrate it.

Split out of cli/check_cmds.py in R49-B. Three commands over one artifact,
`.methodology/trace/attestation.json`, in the three tenses it has: written,
re-derived and checked, and carried forward from an older overlay format.

Round 18 站3 made the attestation content-addressed rather than mtime-based;
these are the commands that produce and read what it addresses.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def cmd_migrate_trace_overlay(args: argparse.Namespace) -> int:
    """Wrap a sentinel-less TRACEABILITY_MATRIX.md in AUTO-GEN sentinels.

    Idempotent. Re-running on an already-migrated file is a no-op. The
    migration does NOT extract manual rows into the overlay — that's a
    per-project human task; this tool only makes the file forward-compatible
    with `build_traceability.py`'s regeneration (which wipes non-sentinel
    content on subsequent runs).
    """
    from core.traceability.overlay import migrate_existing_matrix

    project = Path(args.project).resolve()
    # Round 33 站2 (F6): these were `project / "TRACEABILITY_MATRIX.md"` and a
    # sibling overlay at the repo root, while the deliverable lives at
    # 01-requirements/ (ProjectLayout.traceability_matrix_path) and
    # build_traceability's regeneration defaults its overlay to
    # `output_path.parent`. Running this command therefore migrated a file
    # that is not the deliverable and wrote an overlay the regenerator never
    # reads. Same path-SSOT rule as Round 20 站2.
    from core.utils.project_layout import ProjectLayout

    matrix_path = ProjectLayout(project).traceability_matrix_path
    overlay_path = matrix_path.parent / "TRACEABILITY_MATRIX.overlay.yaml"

    result = migrate_existing_matrix(
        matrix_path, overlay_path, dry_run=args.dry_run
    )
    print(f"\nmigrate-trace-overlay  project={project}")
    print(f"  status: {result['status']}")
    if result["status"] == "wrapped":
        verb = "would wrap" if args.dry_run else "wrapped"
        print(f"  {verb} {result['matrix']} (+{result['lines_added']} sentinel lines)")
        if result["overlay_created"]:
            print(f"  created empty overlay {result['overlay']}")
    elif result["status"] == "already-migrated":
        print("  no-op: AUTO-GEN sentinels already present")
    elif result["status"] == "missing":
        print(f"  {result['matrix']} not found; nothing to migrate")
    return 0


def cmd_build_trace_attestation(args: argparse.Namespace) -> int:
    """Re-derive the matrix and write a git-anchored SHA-256 attestation."""
    from scripts.build_trace_attestation import build_attestation, write_attestation

    project = Path(args.project).resolve()
    overlay = Path(args.overlay).resolve() if args.overlay else None
    trace_dir = Path(args.trace_dir)
    attestation = build_attestation(project, overlay_path=overlay)
    if not args.write:
        # Build-only mode (matches scripts/build_trace_attestation.py --no-write).
        # Default (no flag) is write — CLI is the canonical writer; --write is a
        # no-op alias kept for plan-template compatibility (Bug #109).
        print(f"build-trace-attestation  project={project}  (--no-write, dry build)")
        print(f"  git_sha:         {attestation['git_sha']}")
        print(f"  content_sha256:  {attestation['content_sha256']}")
        return 0
    canonical, latest = write_attestation(project, attestation, trace_dir)
    print(f"\nbuild-trace-attestation  project={project}")
    print(f"  git_sha:         {attestation['git_sha']}")
    print(f"  content_sha256:  {attestation['content_sha256']}")
    print(f"  wrote canonical: {canonical}")
    print(f"  wrote latest:    {latest}  (gitignored)")
    if attestation.get("overlay_errors"):
        for err in attestation["overlay_errors"]:
            print(f"  overlay error: {err}", file=sys.stderr)
    return 0


def cmd_verify_trace(args: argparse.Namespace) -> int:
    """Verify committed attestation matches re-derived matrix.

    Exit codes (must match scripts/verify_trace_attestation.py):
      0 clean / 1 mismatch / 2 missing / 3 schema error.
    """
    from scripts.verify_trace_attestation import verify_attestation

    project = Path(args.project).resolve()
    overlay = Path(args.overlay).resolve() if args.overlay else None
    trace_dir = Path(args.trace_dir)
    code, msg = verify_attestation(project, overlay, trace_dir)
    gate_tag = f" [gate {args.gate}]" if getattr(args, "gate", None) else ""
    print(f"\nverify-trace{gate_tag}  project={project}")
    print(f"  {msg}")
    return code
