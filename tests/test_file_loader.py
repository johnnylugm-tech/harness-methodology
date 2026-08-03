"""Unit tests for scripts/file_loader.py — deterministic file I/O for workflow JS.

Bug class F (improvement F of convergence plan): workflow JS used `agent()`
LLM calls for file I/O. Each call was a failure surface (haiku preamble,
LLM paraphrase, fabrication). file_loader.py is the framework-side
deterministic replacement. These tests verify:

  - Status enum covers all cases (OK / MISSING / PREFIX_MISMATCH /
    TOO_SHORT / TOO_LONG / READ_ERROR)
  - SHA-256 fingerprint is stable for identical content (so workflow JS
    can detect mid-loop edits without re-reading)
  - expect_prefix is an ANCHOR on the first line (`first_line.startswith`),
    not a substring search anywhere in it
  - min_length is byte-size, not char-count
  - max_length truncates with suffix and reports content_truncated=true
  - CLI exit codes match status (0 OK / 1 recoverable / 2 fatal)
  - Refusal of multi-GB files (DEFAULT_MAX_BYTES guard)

Commonality: phase-agnostic. Used by all 8 phase workflow JS files.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.file_loader import (
    TRUNCATION_SUFFIX,
    _first_line,
    _sha256_bytes,
    load_file,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestSha256Bytes:
    def test_empty(self):
        assert _sha256_bytes(b"") == hashlib.sha256(b"").hexdigest()

    def test_known_string(self):
        assert _sha256_bytes(b"hello") == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

    def test_stable_across_calls(self):
        assert _sha256_bytes(b"abc") == _sha256_bytes(b"abc")


class TestFirstLine:
    def test_simple(self):
        assert _first_line("hello\nworld\n") == "hello"

    def test_no_newline_at_eof(self):
        assert _first_line("hello") == "hello"

    def test_empty(self):
        assert _first_line("") == ""

    def test_blank_first_line(self):
        # Leading blank line — first splitlines entry is empty
        assert _first_line("\nfoo\n") == ""


# ---------------------------------------------------------------------------
# load_file — happy path
# ---------------------------------------------------------------------------


class TestLoadFileOK:
    def test_existing_file_no_constraints(self, tmp_path: Path):
        f = tmp_path / "hello.txt"
        f.write_text("hello world\n")
        result = load_file(f)
        assert result["status"] == "OK"
        assert result["byte_size"] == 12
        assert result["line_count"] == 1
        assert result["first_line"] == "hello world"
        assert result["content_sha256"] == hashlib.sha256(b"hello world\n").hexdigest()
        assert result["content_truncated"] is False

    def test_content_not_included_by_default(self, tmp_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("secret")
        result = load_file(f, include_content=False)
        assert result["content"] is None

    def test_content_included_when_requested(self, tmp_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("hello\n")
        result = load_file(f, include_content=True)
        assert result["content"] == "hello\n"

    def test_multiline_file_line_count(self, tmp_path: Path):
        f = tmp_path / "multi.txt"
        f.write_text("a\nb\nc\nd\n")
        result = load_file(f)
        assert result["line_count"] == 4
        assert result["byte_size"] == 8

    def test_utf8_content(self, tmp_path: Path):
        f = tmp_path / "utf8.txt"
        f.write_text("# 測試 H1\nbody\n", encoding="utf-8")
        result = load_file(f)
        assert result["status"] == "OK"
        assert result["first_line"] == "# 測試 H1"
        # SHA is over bytes, so it depends on encoding
        assert result["content_sha256"] == hashlib.sha256("# 測試 H1\nbody\n".encode("utf-8")).hexdigest()

    def test_checked_at_is_iso8601(self, tmp_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("x")
        result = load_file(f)
        # e.g. "2026-06-28T20:30:00+00:00"
        assert "T" in result["checked_at"]
        assert result["checked_at"].endswith("+00:00")


# ---------------------------------------------------------------------------
# load_file — failure modes
# ---------------------------------------------------------------------------


class TestLoadFileMissing:
    def test_nonexistent_file(self, tmp_path: Path):
        result = load_file(tmp_path / "does_not_exist.txt")
        assert result["status"] == "MISSING"
        assert result["byte_size"] is None
        assert "does not exist" in result["diagnostic"]

    def test_directory_not_file(self, tmp_path: Path):
        d = tmp_path / "subdir"
        d.mkdir()
        result = load_file(d)
        assert result["status"] == "MISSING"
        assert "not a regular file" in result["diagnostic"]

    def test_symlink_to_nowhere(self, tmp_path: Path):
        link = tmp_path / "broken_link"
        link.symlink_to(tmp_path / "does_not_exist")
        result = load_file(link)
        assert result["status"] == "MISSING"


class TestLoadFilePrefixMismatch:
    def test_wrong_prefix(self, tmp_path: Path):
        f = tmp_path / "x.md"
        f.write_text("# Not the expected prefix\nbody\n")
        result = load_file(f, expect_prefix="# Expected")
        assert result["status"] == "PREFIX_MISMATCH"
        assert "first line" in result["diagnostic"]
        assert "# Not the expected" in result["diagnostic"]

    def test_correct_prefix(self, tmp_path: Path):
        f = tmp_path / "x.md"
        f.write_text("# Expected heading\nbody\n")
        result = load_file(f, expect_prefix="# Expected")
        assert result["status"] == "OK"

    def test_prefix_with_special_chars(self, tmp_path: Path):
        # YAML anchor uses '# TEST_INVENTORY.yaml'
        f = tmp_path / "x.yaml"
        f.write_text("# TEST_INVENTORY.yaml\nkey: value\n")
        result = load_file(f, expect_prefix="# TEST_INVENTORY.yaml")
        assert result["status"] == "OK"

    def test_prefix_is_not_substring_search(self, tmp_path: Path):
        # Bug v8 regression: prefix MUST be at start of first line, not anywhere
        f = tmp_path / "x.md"
        f.write_text("Some preamble\n# SPEC_TRACKING\nbody\n")
        result = load_file(f, expect_prefix="# SPEC_TRACKING")
        assert result["status"] == "PREFIX_MISMATCH"
        assert result["first_line"] == "Some preamble"

    def test_prefix_must_anchor_the_first_line_not_appear_inside_it(self, tmp_path: Path):
        """Round 33 站1 — the case the test above does not reach.

        `test_prefix_is_not_substring_search` puts the phrase on a LATER line,
        so it also passes under a naive `expect_prefix in text`. This one puts
        it on the first line but not at its start, which only `startswith`
        rejects. It is the behavioural statement of the contract that three
        prose descriptions of this rule got wrong at once.
        """
        f = tmp_path / "x.md"
        f.write_text("Draft: # SPEC_TRACKING\nbody\n")
        result = load_file(f, expect_prefix="# SPEC_TRACKING")
        assert result["status"] == "PREFIX_MISMATCH"

    def test_empty_expect_prefix_ignored(self, tmp_path: Path):
        # Empty string is treated as no prefix check
        f = tmp_path / "x.md"
        f.write_text("# Any heading\n")
        result = load_file(f, expect_prefix="")
        assert result["status"] == "OK"


class TestLoadFileTooShort:
    def test_below_min_length(self, tmp_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("hi")  # 2 bytes
        result = load_file(f, min_length=10)
        assert result["status"] == "TOO_SHORT"
        assert result["byte_size"] == 2
        assert result["diagnostic"].startswith("file size 2 < min_length 10")

    def test_at_min_length(self, tmp_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("hello")  # 5 bytes
        result = load_file(f, min_length=5)
        assert result["status"] == "OK"


class TestLoadFileTooLong:
    def test_truncates_content(self, tmp_path: Path):
        f = tmp_path / "big.txt"
        content = "x" * 1000
        f.write_text(content)
        result = load_file(f, max_length=100, include_content=True)
        assert result["status"] == "OK"
        assert result["content_truncated"] is True
        # Content has truncation suffix appended
        assert result["content"].endswith(TRUNCATION_SUFFIX)
        # Original total is preserved in byte_size
        assert result["byte_size"] == 1000
        # SHA is over ORIGINAL content (not truncated) — useful for change detection
        assert result["content_sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()

    def test_within_max_length_not_truncated(self, tmp_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("short")
        result = load_file(f, max_length=1000)
        assert result["content_truncated"] is False


class TestLoadFileRefusesGiant:
    def test_refuses_over_default_max(self, tmp_path: Path, monkeypatch):
        # We can't actually write a 8 MiB+ file cheaply in a unit test,
        # so patch DEFAULT_MAX_BYTES to a small value, then test refusal.
        from scripts import file_loader

        monkeypatch.setattr(file_loader, "DEFAULT_MAX_BYTES", 100)
        big = tmp_path / "big.txt"
        big.write_text("x" * 200)
        result = load_file(big)
        assert result["status"] == "READ_ERROR"
        assert "DEFAULT_MAX_BYTES" in result["diagnostic"]


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


class TestCLI:
    @pytest.fixture
    def script_path(self) -> Path:
        # Tests run with harness/ as cwd; script is at scripts/<name>.
        return Path("scripts/file_loader.py").resolve()

    def test_cli_ok_exit_zero(self, tmp_path: Path, script_path: Path):
        f = tmp_path / "ok.md"
        f.write_text("# Heading\nbody\n")
        result = subprocess.run(
            [sys.executable, str(script_path), "--file", str(f)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        out = json.loads(result.stdout)
        assert out["status"] == "OK"
        assert out["line_count"] == 2
        assert "OK" in result.stderr

    def test_cli_missing_exit_one(self, tmp_path: Path, script_path: Path):
        result = subprocess.run(
            [sys.executable, str(script_path), "--file", str(tmp_path / "nope")],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        out = json.loads(result.stdout)
        assert out["status"] == "MISSING"

    def test_cli_prefix_mismatch_exit_one(self, tmp_path: Path, script_path: Path):
        f = tmp_path / "x.md"
        f.write_text("# Wrong\n")
        result = subprocess.run(
            [sys.executable, str(script_path), "--file", str(f),
             "--expect-prefix", "# Right"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        out = json.loads(result.stdout)
        assert out["status"] == "PREFIX_MISMATCH"

    def test_cli_json_out(self, tmp_path: Path, script_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("hello\n")
        json_out = tmp_path / "out.json"
        result = subprocess.run(
            [sys.executable, str(script_path), "--file", str(f),
             "--json-out", str(json_out), "--quiet"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert result.stdout == ""  # quiet mode + json-out means no stdout
        assert json_out.exists()
        out = json.loads(json_out.read_text())
        assert out["status"] == "OK"
        assert out["content"] is None  # --content not requested

    def test_cli_content_out_writes_separate_file(self, tmp_path: Path, script_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("payload body\n")
        content_out = tmp_path / "body.txt"
        result = subprocess.run(
            [sys.executable, str(script_path), "--file", str(f),
             "--content", "--content-out", str(content_out), "--quiet"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert content_out.read_text() == "payload body\n"

    def test_cli_quiet_suppresses_status_line(self, tmp_path: Path, script_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("x")
        result = subprocess.run(
            [sys.executable, str(script_path), "--file", str(f), "--quiet"],
            capture_output=True, text=True,
        )
        assert result.stderr == ""


# ---------------------------------------------------------------------------
# Determinism — workflow JS can rely on stable sha256 across calls
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_content_same_sha(self, tmp_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("deterministic content\n")
        r1 = load_file(f)
        r2 = load_file(f)
        assert r1["content_sha256"] == r2["content_sha256"]
        assert r1["line_count"] == r2["line_count"]

    def test_modified_file_different_sha(self, tmp_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("v1")
        r1 = load_file(f)
        f.write_text("v2")
        r2 = load_file(f)
        assert r1["content_sha256"] != r2["content_sha256"]

    def test_checked_at_varies(self, tmp_path: Path):
        # Sanity: timestamp changes (workflow JS should NOT depend on it)
        import time as _time
        f = tmp_path / "x.txt"
        f.write_text("x")
        r1 = load_file(f)
        _time.sleep(1.05)
        r2 = load_file(f)
        assert r1["checked_at"] != r2["checked_at"]
