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


def write_attestation(
    project: Path,
    attestation: dict,
    trace_dir: Path = DEFAULT_TRACE_DIR,
) -> tuple[Path, Path]:
    """Write both committed and CI-regen attestation files. Returns (canonical, latest)."""
    canonical_path = project / trace_dir / COMMITTED_NAME
    latest_path = project / trace_dir / LATEST_NAME
    canonical_path.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(attestation, indent=2, ensure_ascii=False)
    canonical_path.write_text(payload, encoding="utf-8")
    latest_path.write_text(payload, encoding="utf-8")
    return canonical_path, latest_path


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
    args = parser.parse_args(argv)

    project = Path(args.project).resolve()
    if not project.is_dir():
        print(f"ERROR: not a directory: {project}", file=sys.stderr)
        return 2
    overlay_path = Path(args.overlay).resolve() if args.overlay else None
    trace_dir = Path(args.trace_dir)

    attestation = build_attestation(project, overlay_path=overlay_path)

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
