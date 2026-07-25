#!/usr/bin/env python3
"""build_trace_attestation.py — git-anchored FR trace attestation (PR 3).

Re-derives the traceability matrix from live artifacts, computes a
content-addressed SHA-256 over the canonical JSON form, and anchors it to
the current git commit SHA. Writes:

  - .methodology/trace/attestation.json         (committed; canonical)
  - .methodology/trace/attestation.latest.json (gitignored; CI regen)

CI's `verify-trace` step re-derives the matrix at the same git SHA and
compares SHA-256; mismatch means the matrix drifted from the committed
attestation (someone forgot to regenerate). Exit 0/1/2/3 per the verifier.

Usage:
    python3 scripts/build_trace_attestation.py --project . --write
    python3 scripts/build_trace_attestation.py --project . --json
"""  # noqa: D401 (imperative mood)
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent))

from scripts.build_traceability import build_traceability  # noqa: E402
from core.traceability.overlay import (  # noqa: E402
    atomic_to_dict,
    load_overlay,
    merge_overlay,
    validate_overlay,
)


ATTESTATION_SCHEMA = "harness/traceability/attestation/v1"
DEFAULT_TRACE_DIR = Path(".methodology/trace")
COMMITTED_NAME = "attestation.json"
LATEST_NAME = "attestation.latest.json"


def _canonical_json(data: dict) -> str:
    """Stable JSON form: sorted keys, no whitespace, UTF-8, ensure_ascii=False."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _git_sha(project: Path) -> str:
    """Return current HEAD SHA, or 'unknown-<pid>' if not in a git repo."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=project, stderr=subprocess.DEVNULL, text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return f"unknown-{project.name}"


def build_attestation(
    project: Path,
    overlay_path: Optional[Path] = None,
) -> dict:
    """Re-derive the matrix, merge overlay, compute SHA, return the attestation dict.

    The `matrix` field is the merged (atomic + overlay) plain-dict form.
    `content_sha256` is `sha256(canonical_json(matrix))` — what the verifier
    re-derives and compares. `git_sha` anchors the attestation to a commit.
    """
    rt = build_traceability(project)
    atomic = atomic_to_dict(rt)
    if overlay_path is None:
        overlay_path = project / "TRACEABILITY_MATRIX.overlay.yaml"
    overlay = load_overlay(overlay_path)
    overlay_errors = validate_overlay(overlay) if overlay else []
    merged = merge_overlay(atomic, overlay) if not overlay_errors else atomic

    canonical = _canonical_json(merged)
    content_sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "schema": ATTESTATION_SCHEMA,
        "git_sha": _git_sha(project),
        "content_sha256": content_sha,
        "tool": "scripts/build_trace_attestation.py",
        "overlay_used": str(overlay_path) if overlay_path.exists() else None,
        "overlay_errors": overlay_errors,
        "matrix": merged,
    }


def _attested_content(attestation: dict) -> dict:
    """The part of an attestation that says something about the matrix.

    Everything except `git_sha`. `git_sha` records WHEN the file was written,
    not WHAT was attested, and it necessarily differs from the commit that
    carries it (writing the file is what creates the next commit) — so
    including it in a freshness comparison guarantees a永 mismatch.
    """
    return {k: v for k, v in attestation.items() if k != "git_sha"}


def attestation_is_current(project: Path, attestation: dict,
                           trace_dir: Path = DEFAULT_TRACE_DIR) -> bool:
    """True when the on-disk attestation already attests this same content."""
    canonical_path = project / trace_dir / COMMITTED_NAME
    try:
        on_disk = json.loads(canonical_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(on_disk, dict) and (
        _attested_content(on_disk) == _attested_content(attestation)
    )


def write_attestation(
    project: Path,
    attestation: dict,
    trace_dir: Path = DEFAULT_TRACE_DIR,
) -> tuple[Path, Path]:
    """Write both committed and CI-regen attestation files. Returns (canonical, latest).

    Idempotent on the committed file: when the on-disk attestation already
    attests the same content, only its mtime is bumped, leaving the bytes
    (and therefore `git status`) untouched.

    Why (Round 18 站3): staleness is probed by mtime
    (cli.phase_cmds._trace_dirty_state), and git does not preserve mtimes — a
    pull or checkout rewrites them, so a clean tree reads as stale. Rewriting
    the file to clear that would change `git_sha`, producing a real diff that
    has to be committed, whose commit makes `git_sha` stale again. Six
    consecutive `chore: refresh attestation post-pull` commits landed that way,
    all six carrying content_sha256 932e6844… — the matrix never actually
    changed once. Touching instead of rewriting breaks the loop: the mtime
    probe is satisfied and there is nothing to commit.
    """
    canonical_path = project / trace_dir / COMMITTED_NAME
    latest_path = project / trace_dir / LATEST_NAME
    canonical_path.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(attestation, indent=2, ensure_ascii=False)
    if attestation_is_current(project, attestation, trace_dir):
        os.utime(canonical_path, None)
    else:
        canonical_path.write_text(payload, encoding="utf-8")
    # latest is gitignored (CI regen target) — always written in full so a
    # verifier reading it sees the git_sha of the run that produced it.
    latest_path.write_text(payload, encoding="utf-8")
    return canonical_path, latest_path


def refresh_attestation(project: Path) -> bool:
    """Re-derive and write attestation.json in place; never raises.

    The shared pre-push refresh ritual: deliverables may have been written
    since attestation.json was last built, which would fail the
    `_trace_dirty_state` pre-commit probe mid-push. Every push path —
    push-checkpoint, push-milestone, advance-phase — must call this before
    its commit/push flow triggers the hook. That "every push path is
    symmetric" invariant used to live in comments alone and broke once
    (90e35b2 bug 4: advance-phase skipped the refresh, stranding a handover
    commit whose stale attestation SHA only surfaced as a blocking failure
    at the next P5+ push); tests/test_push_path_symmetry.py now asserts
    every registered push path references this helper.

    phase_hooks' F-2.5 auto-fix block re-derives the same files but is NOT
    a push path (different reporting contract: success line on stdout,
    failure to stderr) — deliberately not migrated here.

    Returns True on success; on failure prints the WARN and returns False.
    """
    try:
        attestation = build_attestation(project)
        write_attestation(project, attestation)
        return True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"  [WARN] attestation pre-refresh failed: {exc}")
        return False


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build git-anchored FR trace attestation (PR 3)."
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--overlay", default=None,
                        help="Overlay YAML path (default: <project>/TRACEABILITY_MATRIX.overlay.yaml)")
    parser.add_argument("--write", action="store_true",
                        help="Write attestation.json to .methodology/trace/")
    parser.add_argument("--trace-dir", default=str(DEFAULT_TRACE_DIR))
    parser.add_argument("--json", action="store_true",
                        help="Print attestation JSON to stdout")
    parser.add_argument("--dry-run", action="store_true",
                        help="Check for differences without writing (prevents overwriting manual edits)")
    args = parser.parse_args(argv)

    project = Path(args.project).resolve()
    if not project.is_dir():
        print(f"ERROR: not a directory: {project}", file=sys.stderr)
        return 2
    overlay_path = Path(args.overlay).resolve() if args.overlay else None
    trace_dir = Path(args.trace_dir)

    attestation = build_attestation(project, overlay_path=overlay_path)

    if args.dry_run:
        existing_path = project / trace_dir / COMMITTED_NAME
        if existing_path.exists():
            try:
                existing = json.loads(existing_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                # Bug M21 fix: previously a malformed existing attestation
                # file would raise an uncaught JSONDecodeError or KeyError
                # (when 'matrix' key was missing). Now treat the existing
                # file as having an empty matrix and warn the operator.
                print(
                    f"WARNING: failed to parse existing {COMMITTED_NAME}: {exc}",
                    file=sys.stderr,
                )
                existing = {}
            if existing.get("matrix") != attestation["matrix"]:
                print(f"WARNING: {COMMITTED_NAME} differs from generated matrix.", file=sys.stderr)
                print("Manual edits will be overwritten if you run with --write.", file=sys.stderr)

    if args.write:
        canonical, latest = write_attestation(project, attestation, trace_dir)
        print(f"Wrote {canonical} (committed) and {latest} (CI regen)",
              file=sys.stderr)

    if args.json:
        print(json.dumps(attestation, indent=2, ensure_ascii=False))
    else:
        c = attestation["matrix"].get("completeness", {})
        print(f"git_sha: {attestation['git_sha']}")
        print(f"content_sha256: {attestation['content_sha256']}")
        if c:
            print(f"total_requirements: {c.get('total_requirements', '?')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
