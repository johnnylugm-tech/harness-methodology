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
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.file_loader import (
    RELAY_MAX_BYTES,
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


class TestRelayEnvelope:
    """Round 86: the relay carries a receipt, and stops carrying what it cannot.

    `loadFileViaPython` moves file content through a sub-agent: `read-file`
    writes the file, the agent `cat`s it and re-emits it as its final message.
    Bash stdout above ~30KB is replaced by a 2KB preview plus a persisted-file
    path (measured 2026-09-02: 27,009 intact, 35,300 and 49,300 replaced), so
    the agent never saw the content — and the JS-side checks it faced,
    `length >= 50` and a first-line anchor, both pass on a truncated prefix.
    Every corpus SRS.md is over that cliff (taskq-new's is 86,338 bytes).
    """

    def _spec(self, tmp_path: Path, sections: int, level: int = 3) -> Path:
        f = tmp_path / "SPEC.md"
        body = ["# Canonical spec", ""]
        for i in range(sections):
            body += ["#" * level + f" FR-{i:03d}: {'requirement text ' * 4}", "", "x" * 200, ""]
        f.write_text("\n".join(body), encoding="utf-8")
        return f

    def test_a_file_under_the_ceiling_relays_its_content_verbatim(self, tmp_path: Path):
        f = tmp_path / "SPEC.md"
        f.write_text("# Small spec\n\nbody\n", encoding="utf-8")
        r = load_file(f, relay=True)
        assert r["relay_mode"] == "content"
        assert "# Small spec\n\nbody" in r["content"]

    def test_a_file_over_the_ceiling_relays_an_index_not_its_content(self, tmp_path: Path):
        f = self._spec(tmp_path, 200)
        assert f.stat().st_size > RELAY_MAX_BYTES
        r = load_file(f, relay=True)
        assert r["relay_mode"] == "index"
        assert "x" * 200 not in r["content"]
        assert "FR-000:" in r["content"]
        assert "FR-199:" in r["content"]

    def test_the_envelope_ends_with_the_sha_it_opened_with(self, tmp_path: Path):
        f = self._spec(tmp_path, 200)
        r = load_file(f, relay=True)
        head, *_, tail = r["content"].rstrip("\n").split("\n")
        assert head.startswith("<<<HARNESS-RELAY v1 mode=index ")
        assert f"sha256={r['content_sha256']}" in head
        assert tail == f"<<<HARNESS-RELAY-END sha256={r['content_sha256']}>>>"

    def test_a_truncated_relay_loses_its_end_marker(self, tmp_path: Path):
        # The whole point of the frame: a short relay is now distinguishable
        # from a short file, which is the distinction the JS could not make.
        f = self._spec(tmp_path, 200)
        relayed = load_file(f, relay=True)["content"]
        assert not relayed[: len(relayed) // 2].rstrip().endswith(">>>")

    def test_an_index_never_exceeds_the_ceiling_the_envelope_rides_inside_it(
        self, tmp_path: Path,
    ):
        # 4000 level-3 headings: the level-3 index does not fit, so the depth
        # loop has to fall back. Without the fallback the payload is ~400KB.
        f = self._spec(tmp_path, 4000)
        r = load_file(f, relay=True)
        assert r["relay_mode"] == "index"
        assert len(r["content"].encode("utf-8")) <= RELAY_MAX_BYTES

    def test_a_file_with_no_markdown_headings_indexes_its_head_not_an_empty_table(
        self, tmp_path: Path,
    ):
        # Not hypothetical: srs_vs_spec_diff.json is a Phase 1 SRS-review DOC
        # and reached 27,762 bytes on taskq-new with no heading in it.
        f = tmp_path / "diff.json"
        f.write_text(
            '{\n  "summary": {"total_ac": 124},\n  "per_ac": [\n'
            + "".join(f'    {{"label": "AC-{i}"}},\n' for i in range(3000))
            + "  ]\n}\n",
            encoding="utf-8",
        )
        r = load_file(f, relay=True)
        assert r["relay_mode"] == "index"
        assert '"summary": {"total_ac": 124}' in r["content"]
        assert len(r["content"].encode("utf-8")) <= RELAY_MAX_BYTES

    def test_index_line_ranges_stay_inside_the_file(self, tmp_path: Path):
        # An out-of-range end line is the off-by-one `buildBPrompt` tells
        # Agent B never to write in a citation; the index must not model it.
        f = self._spec(tmp_path, 200)
        r = load_file(f, relay=True)
        ends = [
            int(m.group(1))
            for m in (re.match(r"^\d+-(\d+)\s", ln) for ln in r["content"].split("\n"))
            if m
        ]
        assert ends and max(ends) <= r["line_count"]

    def test_the_index_carries_the_first_line_so_the_anchor_check_survives(
        self, tmp_path: Path,
    ):
        f = self._spec(tmp_path, 200)
        r = load_file(f, relay=True)
        assert "\nFIRST-LINE: # Canonical spec\n" in r["content"]

    def test_a_file_whose_shallowest_index_still_overflows_is_refused(
        self, tmp_path: Path,
    ):
        f = self._spec(tmp_path, 600, level=1)
        r = load_file(f, relay=True)
        assert r["status"] == "READ_ERROR"
        assert "no index fits" in r["diagnostic"]

    def test_relay_is_off_by_default_so_existing_callers_are_untouched(
        self, tmp_path: Path,
    ):
        f = self._spec(tmp_path, 200)
        r = load_file(f, include_content=True)
        assert r["relay_mode"] is None
        assert not r["content"].startswith("<<<HARNESS-RELAY")


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
