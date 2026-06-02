"""Traceability — bidirectional FR → code → test link management.

Public surface (PR 1):
- `scanner.check_traceability` — content-level check used by preflight.
- `scanner.extract_fr_ids_from_sad`, `scan_python_fr_annotations`,
  `scan_test_fr_coverage`, `scan_sad_fr_modules` — pure-regex scanners.

PR 2 adds `overlay`. PR 5 adds `auto_fix_propose`. Submodules keep their own
imports; this package-level __init__ only re-exports for convenience.
"""
