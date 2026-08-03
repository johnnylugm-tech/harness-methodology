#!/usr/bin/env python3
"""file_loader.py — deterministic file loader for workflow JS.

Root cause (F of 5-meta-pattern convergence plan): workflow JS used `agent()`
LLM calls for file I/O — `loadFileViaBash()`, `loadDeliverable()`. LLM is
non-deterministic; each call was a failure surface:
  - Bug #122, #125: load-ctx agent toolCalls=0 returned hallucinated JSON
  - Bug #134, #135: LLM paraphrased cat stdout into prose → balancedJsonAt
    threw PARSE_FAIL on valid file
  - Bug v8: loader returned fabricated "# SPEC.md Amendment Tracking Log"
    content for SPEC_TRACKING.md (no file matched, agent invented)
  - Bug v5: haiku fine-tuning bias to emit "Acknowledged" preamble after
    any tool call, regardless of prompt

Each fix added another defensive layer (validateBGaps, expectPrefix, etc.).
The fixes don't converge — every new LLM failure mode needs a new patch.
This module replaces LLM-as-parser with deterministic Python I/O.

This module provides:
  - `load_file(file_path, expect_prefix=None, min_length=0,
               max_length=None, include_content=False) -> dict`
    Returns a structured dict with one of the statuses:
      OK            — file exists, prefix matches, length within bounds
      MISSING       — file does not exist (or is not a file)
      PREFIX_MISMATCH — file exists but its first line does not START WITH
                        expect_prefix. This is an anchor, not a search: a first
                        line that merely contains the phrase does not pass.
                        (Round 33 站1 — this sentence used to say "contain",
                        the file_loader test's docstring said "substring", and
                        the Phase 1 prompt copied that wording to the agent
                        that writes the file. Three descriptions of one
                        `startswith`, all three wrong in the same direction.)
      TOO_SHORT     — file exists but shorter than min_length
      TOO_LONG      — file exists but longer than max_length (truncate)
      READ_ERROR    — I/O error (permission denied, encoding error, etc.)
    Returns metadata: content_sha256, line_count, first_line, byte_size.

CLI:
  python3 file_loader.py --file PATH [--expect-prefix STR]
                         [--min-length N] [--max-length N]
                         [--content | --content-out PATH]
                         [--json-out PATH]

Output JSON shape:
  {
    "status": "OK|MISSING|PREFIX_MISMATCH|TOO_SHORT|TOO_LONG|READ_ERROR",
    "file_path": "/abs/path",
    "content_sha256": "abc123..." | null,
    "line_count": 123 | null,
    "byte_size": 4567 | null,
    "first_line": "..." | null,
    "first_line_sha256": "def456..." | null,
    "content": "..." | null,       # only if --content
    "content_truncated": bool,     # true if max_length cut content
    "diagnostic": "..." | null,    # human-readable explanation on non-OK
    "checked_at": "ISO-8601 timestamp"
  }

Commonality: phase-agnostic. Used by all 8 phase workflow JS files.
Workflow JS pattern:
  bash('python3 file_loader.py --file X --expect-prefix "Y" --json-out /tmp/out.json')
  → JSON.parse(cat('/tmp/out.json'))

Note: SHA-256 is included so workflow JS can compare against prior fingerprint
to detect mid-loop edits (no need to re-read the whole file).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


CHUNK_SIZE = 65536
DEFAULT_MAX_BYTES = 8 * 1024 * 1024  # 8 MiB hard cap; protect against multi-GB files
TRUNCATION_SUFFIX = "\n...[truncated by file_loader]...\n"


def _sha256_bytes(data: bytes) -> str:
    """Return hex SHA-256 of bytes."""
    return hashlib.sha256(data).hexdigest()


def _first_line(text: str) -> str:
    """Return first line (without trailing newline). Empty if file is empty."""
    if not text:
        return ""
    for line in text.splitlines():
        return line
    return ""


def load_file(
    file_path: str | Path,
    expect_prefix: str | None = None,
    min_length: int = 0,
    max_length: int | None = None,
    include_content: bool = False,
) -> dict[str, Any]:
    """Read file_path, validate against constraints, return structured dict.

    See module docstring for full status enum and JSON shape.
    """
    p = Path(file_path).resolve()
    result: dict[str, Any] = {
        "status": None,
        "file_path": str(p),
        "content_sha256": None,
        "line_count": None,
        "byte_size": None,
        "first_line": None,
        "first_line_sha256": None,
        "content": None,
        "content_truncated": False,
        "diagnostic": None,
        "checked_at": _now_iso(),
    }

    # 1. Existence check
    if not p.exists():
        result["status"] = "MISSING"
        result["diagnostic"] = f"file does not exist: {p}"
        return result
    if not p.is_file():
        result["status"] = "MISSING"
        result["diagnostic"] = f"path is not a regular file: {p}"
        return result

    # 2. Read (with hard cap; refuse multi-GB to avoid OOM in workflow JS)
    try:
        byte_size = p.stat().st_size
        if byte_size > DEFAULT_MAX_BYTES:
            result["status"] = "READ_ERROR"
            result["diagnostic"] = (
                f"file size {byte_size} > DEFAULT_MAX_BYTES {DEFAULT_MAX_BYTES}; "
                "refusing to read. Use --max-length to cap explicitly."
            )
            return result
        raw = p.read_bytes()
    except OSError as e:
        result["status"] = "READ_ERROR"
        result["diagnostic"] = f"OSError reading {p}: {type(e).__name__}: {e}"
        return result
    except (UnicodeDecodeError, UnicodeError) as e:
        # Binary file — surface as READ_ERROR with hint
        result["status"] = "READ_ERROR"
        result["diagnostic"] = (
            f"UnicodeDecodeError reading {p}: {e}. File may be binary."
        )
        return result

    result["byte_size"] = len(raw)
    result["content_sha256"] = _sha256_bytes(raw)

    # 3. Decode for line/prefix checks
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        result["status"] = "READ_ERROR"
        result["diagnostic"] = f"UTF-8 decode failed: {e}"
        return result

    lines = text.splitlines()
    result["line_count"] = len(lines)

    first_line = _first_line(text)
    result["first_line"] = first_line
    result["first_line_sha256"] = _sha256_bytes(first_line.encode("utf-8")) if first_line else None

    # 4. Min length check
    if len(raw) < min_length:
        result["status"] = "TOO_SHORT"
        result["diagnostic"] = (
            f"file size {len(raw)} < min_length {min_length}"
        )
        return result

    # 5. Prefix check (against first line)
    if expect_prefix is not None and expect_prefix != "":
        if not first_line.startswith(expect_prefix):
            result["status"] = "PREFIX_MISMATCH"
            result["diagnostic"] = (
                f"first line {first_line[:80]!r} does not start with "
                f"expect_prefix {expect_prefix!r}"
            )
            return result

    # 6. Max length check (truncate content; status remains OK)
    content_text = text
    if max_length is not None and len(raw) > max_length:
        # Truncate to max_length bytes worth of text, respecting UTF-8 boundaries
        truncated_bytes = raw[:max_length]
        # Find last full UTF-8 boundary
        for i in range(len(truncated_bytes), max(0, len(truncated_bytes) - 4), -1):
            try:
                content_text = truncated_bytes[:i].decode("utf-8")
                break
            except UnicodeDecodeError:
                continue
        else:
            content_text = truncated_bytes.decode("utf-8", errors="replace")
        content_text = content_text + TRUNCATION_SUFFIX
        result["content_truncated"] = True

    if include_content:
        result["content"] = content_text

    result["status"] = "OK"
    return result


def _now_iso() -> str:
    """Return current time as ISO-8601 UTC string."""
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic file loader — replaces LLM-as-parser in workflow JS."
    )
    parser.add_argument("--file", required=True, help="Path to the file to load.")
    parser.add_argument(
        "--expect-prefix",
        default=None,
        help="If set, the file's first line must start with this string.",
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=0,
        help="Minimum byte size; below this returns TOO_SHORT.",
    )
    parser.add_argument(
        "--max-length",
        type=int,
        default=None,
        help="Maximum byte size; above this truncates content with a suffix.",
    )
    parser.add_argument(
        "--content",
        action="store_true",
        help="Include the (possibly truncated) content text in the JSON output.",
    )
    parser.add_argument(
        "--content-out",
        default=None,
        help="If set, also write content to this path (separate from JSON output).",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="If set, write the JSON result to this path; otherwise print to stdout.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the human-readable status line on stderr.",
    )
    args = parser.parse_args()

    result = load_file(
        file_path=args.file,
        expect_prefix=args.expect_prefix,
        min_length=args.min_length,
        max_length=args.max_length,
        include_content=args.content,
    )

    json_text = json.dumps(result, indent=2, ensure_ascii=False)

    if args.json_out:
        Path(args.json_out).write_text(json_text, encoding="utf-8")
    else:
        print(json_text)

    if args.content_out and result.get("content") is not None:
        Path(args.content_out).write_text(result["content"], encoding="utf-8")

    if not args.quiet:
        status = result["status"]
        sha = result["content_sha256"]
        sha_short = (sha[:12] + "...") if sha else "(none)"
        msg = (
            f"[file_loader] {status} "
            f"file={args.file} "
            f"sha256={sha_short} "
            f"bytes={result['byte_size']} "
            f"lines={result['line_count']}"
        )
        if status != "OK":
            msg += f" — {result['diagnostic']}"
        print(msg, file=sys.stderr)

    # Exit code: 0 OK, 1 missing/prefix/short/long (recoverable), 2 read error (fatal)
    if result["status"] == "OK":
        return 0
    if result["status"] in {"MISSING", "PREFIX_MISMATCH", "TOO_SHORT", "TOO_LONG"}:
        return 1
    return 2  # READ_ERROR


if __name__ == "__main__":
    sys.exit(_cli())
