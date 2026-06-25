"""Audit helpers — generic building blocks for source-code audits.

Currently provides:
  - grep_docstring_aware.audit_grep(): regex search that strips
    triple-quoted docstrings (and optionally comments) so audits
    like NFR-02 'no shell=True' do not false-positive on docstring
    text mentioning the forbidden API.

When ``scripts/shell_audit.py`` lands (NFR-02 enforcement), it should
use this helper so all audits share the same docstring/comment
exclusion logic. Until then the helper is exposed for tests and
future audits.
"""
from core.audit.grep_docstring_aware import (
    Hit,
    audit_grep,
    strip_docstrings,
    strip_comments,
)

__all__ = ["Hit", "audit_grep", "strip_docstrings", "strip_comments"]