"""Shared FR-ID heading fragment — SRS subsection-number prefix.

Extracted so the same regex fragment isn't hand-copied at every FR-heading
call site (drift risk: fixing the pattern in one place and forgetting the
others). See 2026-07-14 phase1-requirements E2E: this exact bug class — a
heading regex that only recognized canonical `### FR-01:` and missed the
natural SRS-authoring form `### 3.1 FR-01` (TOC-numbered subsection) — was
independently found and fixed 5 times across different call sites.

Only import this into call sites that anchor on markdown headings (`^#{1,6}
...FR-NN`). Call sites that intentionally scan FR-NN as bare prose tokens
anywhere in the document (`\\bFR-\\d+\\b`, e.g. phase_auditor.py's depth
checks) are a different, unaffected semantic — do not import this there.
"""

from __future__ import annotations

# Optional subsection-number prefix between the heading hashes and `FR-NN`,
# e.g. `### 3.1 FR-01` (one level) or `### 3.1.1 FR-01` (nested levels) —
# the natural form when an SRS uses §3 Functional Requirements / §3.1 FR-01
# / §3.2 FR-02 TOC numbering. Without this prefix, a heading regex
# false-positives a structurally complete SRS as having zero FR sections.
SRS_SUBSECTION_PREFIX = r"(?:\d+(?:\.\d+)*\.?\s+)?"
