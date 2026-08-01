"""Evidence digests — a gate verdict carries proof of what it read.

Round 27 站3. S3 (`_check_tool_evidence`) already verifies, at the moment of
judgement, that every tool-scored dimension's `tool_output` file exists and
looks like real output. What nothing did was make that verification outlive the
judgement.

Measured on taskq-plus's Gate 4: **13 of 14** dimensions cite a `tool_output`
under `.sessi-work/`, which is in `.gitignore` and has since been cleaned. The
verdict itself — `composite_score: 98.707`, `verdict: PASS` — is committed and
permanent. So the gate that S3 checked cannot be re-checked by anyone, ever: the
judgement was version-controlled and its evidence was not.

Two dimensions there even cite the same file as each other (`documentation` and
`error_handling` both point at `error_handling_doc.txt`; `integration_coverage`
and `performance` both at `performance.txt`), which no reader could notice once
the files were gone.

A digest is deliberately not a copy. Copying `coverage.json` into the repo every
round would grow the consuming project without bound. What the digest preserves
is the ability to answer "was this the file the gate read?" — sha256 + size +
the opening lines — which is what a later reader actually needs to decide
whether to trust the verdict or re-run it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# The head is a human's first look at the evidence, not a second copy of it:
# enough to recognise "yes, that is ruff output showing zero violations", far
# too little to reconstruct the file.
HEAD_MAX_LINES = 20
HEAD_MAX_BYTES = 2048


def digest_of_text(text: str, *, source: str) -> dict:
    """Digest an inline evidence string (`tool_evidence`)."""
    raw = text.encode("utf-8", errors="replace")
    return {
        "source": source,
        "kind": "inline",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "head": _head(text),
    }


def digest_of_file(path: Path, *, source: str) -> dict:
    """Digest an on-disk evidence file (`tool_output`).

    Callers reach here only after S3 has confirmed the file exists and parsed as
    genuine tool output, so a read failure is a race, not the normal case — it is
    recorded as such rather than silently producing no digest, because "the gate
    could not digest its own evidence" is exactly the kind of thing that should
    not be invisible.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return {"source": source, "kind": "file", "error": f"unreadable: {exc}"}
    return {
        "source": source,
        "kind": "file",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "head": _head(raw.decode("utf-8", errors="replace")),
    }


def _head(text: str) -> str:
    head = "\n".join(text.splitlines()[:HEAD_MAX_LINES])
    if len(head.encode("utf-8", errors="replace")) > HEAD_MAX_BYTES:
        head = head.encode("utf-8", errors="replace")[:HEAD_MAX_BYTES].decode(
            "utf-8", errors="replace"
        )
    return head


def digest_coverage(digests: dict, dimensions: list) -> tuple[int, int]:
    """(dimensions carrying a digest, dimensions that should) for reporting."""
    want = {
        d.get("name") for d in dimensions
        if isinstance(d, dict) and d.get("requires_tool_execution", False)
    }
    return len(want & set(digests or {})), len(want)
