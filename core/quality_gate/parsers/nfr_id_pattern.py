"""Shared NFR-ID normalization — canonical zero-padded `NFR-NN` form.

Extracted so `scanner.extract_nfr_ids_from_srs` (which force-zero-pads IDs
scanned from SRS.md) and `security_design.py`'s R7 cross-reference (which
compared threat.nfr / SAB nfr_traceability keys against that padded set
using raw, un-normalized strings) cannot silently disagree about which NFR
ID a human-written `NFR-2` refers to. See 2026-07-15: SRS.md `### NFR-02`
vs SAD.md `nfr: NFR-2` (a legal, common shorthand) produced two false
SEC-R7 BLOCKs even though both refer to the same requirement — the same
one-side-normalizes-the-other-doesn't bug class already fixed once for
SEC-R6's SAB module comparison (`sab_amender.normalize_sab_module_to_dotted`).
"""

from __future__ import annotations

import re

NFR_ID_RE = re.compile(r"^NFR-(\d+)$", re.IGNORECASE)


def normalize_nfr_id(raw: object) -> str | None:
    """Normalize an NFR identifier to canonical zero-padded `NFR-NN` form.

    Accepts `NFR-2`, `NFR-02`, `nfr-2` (case-insensitive) — all normalize to
    `NFR-02`. Returns None for non-string input or a string that doesn't
    match `NFR-<digits>` (e.g. `NFR-01a`), so callers treat it the same as
    "not found" rather than inventing a new tolerance range.
    """
    if not isinstance(raw, str):
        return None
    m = NFR_ID_RE.match(raw.strip())
    if not m:
        return None
    return f"NFR-{int(m.group(1)):02d}"
