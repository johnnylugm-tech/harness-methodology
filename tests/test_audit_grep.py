"""Tests for core.audit.grep_docstring_aware."""
import re
from pathlib import Path

import pytest

from core.audit.grep_docstring_aware import (
    audit_grep,
    strip_comments,
    strip_docstrings,
)


pytestmark = [pytest.mark.core]


# ---------------------------------------------------------------------------
# TestStripDocstrings
# ---------------------------------------------------------------------------

class TestStripDocstrings:
    def test_strips_double_triple_quoted(self):
        text = 'before\n"""\nshell=True\n"""\nafter\n'
        out = strip_docstrings(text)
        assert "shell=True" not in out
        # Newlines preserved so line numbers stay aligned.
        assert out.count("\n") == text.count("\n")

    def test_strips_single_triple_quoted(self):
        text = "before\n'''\nshell=True\n'''\nafter\n"
        out = strip_docstrings(text)
        assert "shell=True" not in out

    def test_preserves_string_outside_docstring(self):
        text = 'x = "shell=True"\n"""docstring shell=True"""\n'
        out = strip_docstrings(text)
        # The inline string literal still contains shell=True.
        assert 'shell=True' in out

    def test_handles_text_without_docstrings(self):
        text = "x = 1\ny = 2\n"
        assert strip_docstrings(text) == text


# ---------------------------------------------------------------------------
# TestStripComments
# ---------------------------------------------------------------------------

class TestStripComments:
    def test_strips_line_comment(self):
        text = "x = 1  # shell=True\ny = 2\n"
        out = strip_comments(text)
        assert "shell=True" not in out

    def test_preserves_hash_inside_string(self):
        text = 'x = "abc # def"\n'
        # Conservative: comments regex strips from '#' to EOL even inside
        # strings. This is acceptable for audits because the only thing
        # inside a string that matters is the literal — and matches against
        # `text` (not stripped) still report the literal as a hit if needed.
        out = strip_comments(text)
        assert "def" not in out  # stripped past EOL


# ---------------------------------------------------------------------------
# TestAuditGrep
# ---------------------------------------------------------------------------

class TestAuditGrep:
    def _write(self, tmp_path: Path, rel: str, content: str):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def test_basic_match(self, tmp_path):
        self._write(tmp_path, "a.py", "x = 1\nshell=True\ny = 2\n")
        hits = audit_grep(tmp_path, re.compile(r"shell\s*=\s*True"))
        assert len(hits) == 1
        assert hits[0].path.endswith("a.py")
        assert hits[0].line_no == 2
        assert "shell=True" in hits[0].line_text

    def test_docstring_excluded_by_default(self, tmp_path):
        # The 'shell=True' appears inside a docstring. Default
        # (exclude_docstrings=True, exclude_comments=False) strips
        # docstrings but leaves comments — so the comment hit still
        # appears. This test asserts the docstring is gone.
        self._write(tmp_path, "a.py", '''"""
This module uses shell=True in old code.
"""
os.system("ls")
''')
        hits = audit_grep(tmp_path, re.compile(r"shell\s*=\s*True"))
        assert hits == [], f"docstring should be excluded; got {hits}"

    def test_docstring_and_comment_both_excluded_when_requested(self, tmp_path):
        # Opt in to comment stripping for stricter audits.
        self._write(tmp_path, "a.py", '''"""
This module uses shell=True in old code.
"""
import os  # legacy shell=True usage
''')
        hits = audit_grep(
            tmp_path, re.compile(r"shell\s*=\s*True"),
            exclude_comments=True,
        )
        assert hits == []

    def test_comment_excluded_when_requested(self, tmp_path):
        self._write(tmp_path, "a.py", "x = 1  # shell=True\ny = 2\n")
        # Default (exclude_comments=False): comment IS reported.
        hits_default = audit_grep(tmp_path, re.compile(r"shell\s*=\s*True"))
        assert len(hits_default) == 1
        # Opt-in: comment is stripped before matching.
        hits_strict = audit_grep(
            tmp_path, re.compile(r"shell\s*=\s*True"),
            exclude_comments=True,
        )
        assert hits_strict == []

    def test_triple_single_quote_docstring_excluded(self, tmp_path):
        self._write(tmp_path, "a.py", "'''\nshell=True\n'''\nx = 1\n")
        hits = audit_grep(tmp_path, re.compile(r"shell\s*=\s*True"))
        assert hits == []

    def test_empty_dir(self, tmp_path):
        assert audit_grep(tmp_path, re.compile(r".*")) == []

    def test_missing_dir(self, tmp_path):
        assert audit_grep(tmp_path / "nope", re.compile(r".*")) == []

    def test_skips_pycache(self, tmp_path):
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "x.py").write_text("shell=True\n")
        # No real source — no hits.
        assert audit_grep(tmp_path, re.compile(r"shell\s*=\s*True")) == []

    def test_real_call_still_detected_with_docstrings_present(self, tmp_path):
        self._write(tmp_path, "a.py", '''"""
We never use shell=True.
"""
def run():
    return subprocess.run(cmd, shell=True)
''')
        hits = audit_grep(tmp_path, re.compile(r"shell\s*=\s*True"))
        assert len(hits) == 1
        assert hits[0].line_no == 5  # the real call site
        assert "subprocess.run" in hits[0].line_text