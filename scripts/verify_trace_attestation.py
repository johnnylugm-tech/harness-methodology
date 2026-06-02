#!/usr/bin/env python3
"""verify_trace_attestation.py — CI verifier for trace attestation (PR 3).

Re-derives the traceability matrix at the same git SHA and compares the
SHA-256 hash against the committed attestation. The committed attestation
is the "claim"; the re-derivation is the "proof".

Exit codes (must match harness_cli.cmd_verify_trace wrapper):
  0 — attestation matches re-derived matrix (clean)
  1 — SHA mismatch (code changed since last attestation; re-run
      `python harness_cli.py build-trace-attestation --write`)
  2 — attestation.json not found (first run; prompt user to run
      `build-trace-attestation --write`)
  3 — schema error or re-derive failure (framework-side issue)

Usage:
    python3 scripts/verify_trace_attestation.py --project .
    python3 scripts/verify_trace_attestation.py --project . --gate 3
"""  # noqa: D401 (imperative mood)
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent))

from scripts.build_trace_attestation import (  # noqa: E402
    ATTESTATION_SCHEMA,
    DEFAULT_TRACE_DIR,
    COMMITTED_NAME,
    build_attestation,
)


EXIT_CLEAN = 0
EXIT_MISMATCH = 1
EXIT_MISSING = 2
EXIT_SCHEMA = 3


def verify_attestation(
    project: Path,
    overlay_path: Optional[Path] = None,
    trace_dir: Path = DEFAULT_TRACE_DIR,
) -> tuple[int, str]:
    """Return (exit_code, message). See module docstring for exit codes."""
    canonical_path = project / trace_dir / COMMITTED_NAME
    if not canonical_path.exists():
        return EXIT_MISSING, (
            f"{canonical_path} not found. "
            f"Run: python harness_cli.py build-trace-attestation "
            f"--project {project} --write"
        )

    try:
        stored = json.loads(canonical_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return EXIT_SCHEMA, f"attestation.json is malformed: {e}"

    if stored.get("schema") != ATTESTATION_SCHEMA:
        return EXIT_SCHEMA, (
            f"attestation schema {stored.get('schema')!r} != "
            f"{ATTESTATION_SCHEMA!r}"
        )

    try:
        rederived = build_attestation(project, overlay_path=overlay_path)
    except Exception as e:
        return EXIT_SCHEMA, f"re-derive failed: {e}"

    if stored.get("content_sha256") != rederived["content_sha256"]:
        return EXIT_MISMATCH, (
            f"SHA mismatch — code changed since last attestation.\n"
            f"  stored:  {stored.get('content_sha256')}\n"
            f"  current: {rederived['content_sha256']}\n"
            f"Re-run: python harness_cli.py build-trace-attestation "
            f"--project {project} --write"
        )

    return EXIT_CLEAN, "trace attestation matches re-derived matrix"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify git-anchored FR trace attestation (PR 3)."
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--overlay", default=None)
    parser.add_argument("--gate", type=int, default=None,
                        help="Gate number (informational; for CI log tagging)")
    parser.add_argument("--trace-dir", default=str(DEFAULT_TRACE_DIR))
    args = parser.parse_args(argv)

    project = Path(args.project).resolve()
    if not project.is_dir():
        print(f"ERROR: not a directory: {project}", file=sys.stderr)
        return 2
    overlay_path = Path(args.overlay).resolve() if args.overlay else None
    trace_dir = Path(args.trace_dir)

    code, msg = verify_attestation(project, overlay_path, trace_dir)
    if args.gate is not None:
        print(f"[gate {args.gate}] {msg}", file=sys.stderr)
    else:
        print(msg, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
