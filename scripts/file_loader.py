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

  - `relay=True` wraps the returned `content` in the relay envelope and,
    for a file above `relay_max_bytes`, replaces it with an INDEX. The
    workflow JS moves file content through an LLM (it has no fs), and Bash
    stdout above ~30KB is replaced by a preview — so a large file arrived
    truncated and passed both JS-side checks. See RELAY_MAX_BYTES.

CLI:
  python3 file_loader.py --file PATH [--expect-prefix STR]
                         [--min-length N] [--max-length N]
                         [--content | --content-out PATH]
                         [--relay [--relay-max-bytes N]]
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
    "content": "..." | null,       # only if --content or --relay
    "content_truncated": bool,     # true if max_length cut content
    "relay_mode": "content"|"index"|null,   # only under --relay
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
import re
import sys
from pathlib import Path
from typing import Any


CHUNK_SIZE = 65536
DEFAULT_MAX_BYTES = 8 * 1024 * 1024  # 8 MiB hard cap; protect against multi-GB files
TRUNCATION_SUFFIX = "\n...[truncated by file_loader]...\n"

#: Largest file the workflow-JS relay may carry as CONTENT. Above this the
#: relay carries an INDEX instead.
#:
#: The relay is `loadFileViaPython`: a sub-agent runs `read-file`, `cat`s the
#: content file, and re-emits it as its final message. Measured 2026-09-02:
#: 27,009 bytes of Bash stdout reached the caller intact; 35,300 and 49,300
#: were both replaced by a 2KB preview plus a persisted-file path. Above the
#: cliff the agent never sees the content, and the JS-side checks it faces
#: (`length >= 50`, first-line anchor) both pass on a truncated prefix — which
#: is why this ceiling has to exist rather than being discovered per run.
#:
#: 24,576 sits 9% under the confirmed-good point. Bytes, not characters: for
#: UTF-8 bytes >= chars, so a byte ceiling is the conservative one for a CJK
#: spec (omnibot-new's SPEC.md is 11.7% CJK). The ~210 bytes of envelope the
#: relay adds on top of the payload fit inside the same margin.
RELAY_MAX_BYTES = 24_576

#: The relay envelope. The END line is the point of the whole thing: a
#: truncation anywhere loses it, so the JS can tell a short relay from a short
#: file — which is the distinction it could not make before. It does NOT
#: authenticate the relay: an agent that never read the file could emit a
#: consistent pair of invented shas. JS has no crypto and no out-of-band
#: channel, so that gap closes only with a second dispatch (see the Round 86
#: entry in docs/PROPOSAL_ADJUDICATIONS.md).
RELAY_VERSION = "v1"
RELAY_BEGIN_FMT = (
    "<<<HARNESS-RELAY {version} mode={mode} sha256={sha} bytes={size} lines={lines}>>>"
)
RELAY_END_FMT = "<<<HARNESS-RELAY-END sha256={sha}>>>"

#: Heading depth the index starts at. `### FR-NN:` / `### NFR-NN:` are level 3,
#: so level 3 is the shallowest depth that still names every requirement.
RELAY_INDEX_MAX_LEVEL = 3


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


def _heading_rows(lines: list[str]) -> list[tuple[int, int, int, str]]:
    """Return (start_line, end_line, level, title) for every Markdown heading.

    `end_line` is the last line of the section: the line before the next
    heading of the same-or-shallower level, or EOF. Depth is not filtered
    here — a level-3 section's extent has to account for the level-4 headings
    inside it, which is what makes the range usable with `sed -n`.
    """
    found: list[tuple[int, int, str]] = []
    for n, line in enumerate(lines, 1):
        m = re.match(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", line)
        if m:
            found.append((n, len(m.group(1)), m.group(2)))

    rows: list[tuple[int, int, int, str]] = []
    for i, (start, level, title) in enumerate(found):
        end = len(lines)
        for nxt_start, nxt_level, _ in found[i + 1:]:
            if nxt_level <= level:
                end = nxt_start - 1
                break
        rows.append((start, end, level, title))
    return rows


def _index_header(path: Path, byte_size: int, line_count: int, first_line: str) -> str:
    """The two fields the JS reads out of an index payload, plus orientation.

    `FIRST-LINE:` is what keeps the playbook §8.2 anchor guard alive in index
    mode — without it the JS would have nothing to apply `expect_prefix` to
    and the hallucination check would silently stop existing for exactly the
    files too large to verify any other way.
    """
    return (
        f"FILE: {path}   ({byte_size} bytes, {line_count} lines)\n"
        f"FIRST-LINE: {first_line}\n"
    )


def _render_index(
    path: Path, text: str, byte_size: int, line_count: int, first_line: str,
    ceiling: int, budget: int,
) -> str | None:
    """Render an index for a file too large to relay. None if none fits.

    Two shapes, chosen by what the file actually is rather than by its name:
    a heading index when it has Markdown headings, and a head-of-file excerpt
    when it does not. The second is not hypothetical — `srs_vs_spec_diff.json`
    is a DOC in the Phase 1 SRS review and reached 27,762 bytes on taskq-new
    with no heading in it.

    `ceiling` is the number the reader is told; `budget` is what the payload
    may occupy once the envelope is on it. Keeping them apart is the point:
    the first version of this function fitted the payload to the ceiling and
    shipped a 24,776-byte relay under a 24,576-byte limit.
    """
    header = _index_header(path, byte_size, line_count, first_line)
    # splitlines(), not split("\n"): a file ending in a newline gives one extra
    # empty element under split(), and the last section's range would name a
    # line the file does not have — the off-by-one `buildBPrompt` explicitly
    # tells Agent B never to write in a citation.
    rows = _heading_rows(text.splitlines())

    if rows:
        note = (
            f"This file exceeds the {ceiling}-byte relay ceiling, so this is its\n"
            f"heading index, not its content. Read any range with:\n"
            f"  sed -n 'START,ENDp' {path}\n"
            "LINES         LVL  HEADING\n"
        )
        for level in range(RELAY_INDEX_MAX_LEVEL, 0, -1):
            body = "".join(
                f"{f'{s}-{e}':<13} {lv:>3}  {t[:80]}\n"
                for s, e, lv, t in rows if lv <= level
            )
            out = header + note + body
            if len(out.encode("utf-8")) <= budget:
                return out
        return None

    def excerpt_note(shown: int, chars: int) -> str:
        return (
            f"This file exceeds the {ceiling}-byte relay ceiling and has no Markdown\n"
            f"headings to index, so what follows is its FIRST {chars} characters\n"
            f"(lines 1-{shown} of {line_count}). Read the rest with:\n"
            f"  sed -n '{shown},{line_count}p' {path}\n"
            "--- HEAD OF FILE ---\n"
        )

    # The note's length depends on numbers derived from the excerpt, so it is
    # reserved at its upper bound (every count at its widest) instead of being
    # guessed. A flat 300-byte reserve overshot the ceiling by 46 bytes on a
    # long path with six-digit counts — caught by this module's own test.
    excerpt_budget = (
        budget
        - len(header.encode("utf-8"))
        - len(excerpt_note(line_count, byte_size).encode("utf-8"))
    )
    if excerpt_budget <= 0:
        return None
    raw = text.encode("utf-8")[:excerpt_budget]
    for i in range(len(raw), max(0, len(raw) - 4), -1):
        try:
            excerpt = raw[:i].decode("utf-8")
            break
        except UnicodeDecodeError:
            continue
    else:
        excerpt = raw.decode("utf-8", errors="replace")
    shown = excerpt.count("\n") + 1
    return header + excerpt_note(shown, len(excerpt)) + excerpt


def _wrap_relay(mode: str, sha: str, byte_size: int, line_count: int, payload: str) -> str:
    begin = RELAY_BEGIN_FMT.format(
        version=RELAY_VERSION, mode=mode, sha=sha, size=byte_size, lines=line_count,
    )
    return f"{begin}\n{payload.rstrip(chr(10))}\n{RELAY_END_FMT.format(sha=sha)}\n"


def load_file(
    file_path: str | Path,
    expect_prefix: str | None = None,
    min_length: int = 0,
    max_length: int | None = None,
    include_content: bool = False,
    relay: bool = False,
    relay_max_bytes: int = RELAY_MAX_BYTES,
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
        "relay_mode": None,
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

    # 7. Relay mode: `content` is the enveloped payload, so the JSON field and
    #    the --content-out file are one statement rather than two formats of
    #    the same read. Implies include_content: a relay with nothing to relay
    #    is a caller bug, not a mode.
    if relay:
        if len(raw) <= relay_max_bytes:
            payload, mode = content_text, "content"
        else:
            overhead = len(
                _wrap_relay(
                    "index", result["content_sha256"], len(raw), len(lines), "",
                ).encode("utf-8")
            )
            index = _render_index(
                p, text, len(raw), len(lines), first_line,
                relay_max_bytes, relay_max_bytes - overhead,
            )
            if index is None:
                result["status"] = "READ_ERROR"
                result["diagnostic"] = (
                    f"file size {len(raw)} exceeds the relay ceiling "
                    f"{relay_max_bytes} and no index fits under it either — even "
                    "at heading level 1. A file whose top-level headings alone "
                    "exceed the ceiling cannot be relayed; split it or raise "
                    "--relay-max-bytes with a measurement behind the number."
                )
                return result
            payload, mode = index, "index"
        result["relay_mode"] = mode
        result["content"] = _wrap_relay(
            mode, result["content_sha256"], len(raw), len(lines), payload,
        )
        result["status"] = "OK"
        return result

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
        "--relay",
        action="store_true",
        help="Wrap content in the relay envelope; index it if it exceeds the ceiling.",
    )
    parser.add_argument(
        "--relay-max-bytes",
        type=int,
        default=RELAY_MAX_BYTES,
        help=f"Relay content ceiling in bytes (default {RELAY_MAX_BYTES}).",
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
        relay=args.relay,
        relay_max_bytes=args.relay_max_bytes,
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
