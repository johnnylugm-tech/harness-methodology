"""Tests for CLAUDE.md dynamic update helpers.

Covers: _build_claude_md_auto_section, _update_claude_md.
Design rule: all expected values hard-coded; never re-derive from the source.
"""
import json
import sys
from pathlib import Path

# Ensure repo root on sys.path (mirrors conftest.py logic)
_repo = Path(__file__).resolve().parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

from core.claude_md import (  # noqa: E402
    CLAUDE_AUTO_END as _CLAUDE_AUTO_END,
    CLAUDE_AUTO_START as _CLAUDE_AUTO_START,
    STALE_HARNESS_RE as _STALE_HARNESS_RE,
    build_claude_md_auto_section as _build_claude_md_auto_section,
    llm_clean_stale_claude_md as _llm_clean_stale_claude_md,
    update_claude_md as _update_claude_md,
)


# ─── _build_claude_md_auto_section ───────────────────────────────────────────

def test_build_no_state_files(tmp_path):
    """No state.json / quality_manifest.json → no exception; output has expected headings."""
    content = _build_claude_md_auto_section(tmp_path)
    assert "Harness Status" in content
    assert "Gate Progress" in content
    assert "Gate 1" in content
    assert "Gate 2" in content


def test_build_reflects_current_phase(tmp_path):
    """state.json current_phase=4 → 'Testing' appears in auto section."""
    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "state.json").write_text(
        json.dumps({"current_phase": 4, "last_gate": 2})
    )
    content = _build_claude_md_auto_section(tmp_path)
    assert "Testing" in content
    assert "4" in content


def test_build_reflects_gate2_score(tmp_path):
    """quality_manifest with gate2 score=88.5 → '88.5' in auto section."""
    (tmp_path / ".methodology").mkdir()
    manifest = {
        "fr_ids": ["FR-01"],
        "gate_results": {
            "gate1": {"FR-01": {"score": 95.0, "quality_complete": True}},
            "gate2": {"score": 88.5, "quality_complete": True},
            "gate3": None,
            "gate4": None,
        },
    }
    (tmp_path / ".methodology" / "quality_manifest.json").write_text(json.dumps(manifest))
    content = _build_claude_md_auto_section(tmp_path)
    assert "88.5" in content
    assert "✅" in content


# ─── _update_claude_md ───────────────────────────────────────────────────────

def test_update_creates_claude_md_when_missing(tmp_path):
    """No CLAUDE.md → creates file containing markers and project name."""
    _update_claude_md(tmp_path)
    text = (tmp_path / "CLAUDE.md").read_text()
    assert _CLAUDE_AUTO_START in text
    assert _CLAUDE_AUTO_END in text
    assert tmp_path.name in text


def test_update_replaces_between_markers(tmp_path):
    """CLAUDE.md with markers → only content between markers is replaced;
    content outside markers (header + trailing section) is preserved."""
    (tmp_path / "CLAUDE.md").write_text(
        "# Custom Header\n"
        + _CLAUDE_AUTO_START + "\nold content line\n" + _CLAUDE_AUTO_END
        + "\n\n## My Section\ncustom notes here"
    )
    _update_claude_md(tmp_path)
    text = (tmp_path / "CLAUDE.md").read_text()
    # Outside-marker content preserved
    assert "# Custom Header" in text
    assert "## My Section" in text
    assert "custom notes here" in text
    # Old auto content replaced
    assert "old content line" not in text
    # New auto content present
    assert "Harness Status" in text


def test_update_prepends_to_legacy_claude_md(tmp_path):
    """Legacy CLAUDE.md without markers → auto block prepended; existing content kept."""
    (tmp_path / "CLAUDE.md").write_text("## Existing Section\nsome legacy content")
    _update_claude_md(tmp_path)
    text = (tmp_path / "CLAUDE.md").read_text()
    # Auto block comes before existing content
    assert text.index(_CLAUDE_AUTO_START) < text.index("## Existing Section")
    # Existing content fully preserved
    assert "some legacy content" in text
    # Markers properly closed
    assert _CLAUDE_AUTO_END in text


# ─── _STALE_HARNESS_RE ───────────────────────────────────────────────────────

def test_stale_re_detects_current_state_line():
    """'Current state: Phase 7' matches _STALE_HARNESS_RE."""
    assert _STALE_HARNESS_RE.search(
        "Current state: **Phase 7 (Risk Management)**, Gate 4 PASS (score 96.5)."
    )


def test_stale_re_detects_gate_pass_line():
    """'Gate 4 PASS' matches _STALE_HARNESS_RE."""
    assert _STALE_HARNESS_RE.search("Gate 4 PASS")


def test_stale_re_detects_working_in_phase():
    """'Working in Phase 7+' matches _STALE_HARNESS_RE."""
    assert _STALE_HARNESS_RE.search("### Working in Phase 7+")


def test_stale_re_ignores_generic_commands():
    """Generic commands and architecture text do NOT trigger _STALE_HARNESS_RE."""
    clean_lines = [
        "python3 harness_cli.py run-phase --phase N --project .",
        "State tracked in `.methodology/state.json`.",
        "### Phase FSM",
        "## Architecture",
        "| Gate | Trigger | Score |",  # table header without PASS/score value
    ]
    for line in clean_lines:
        assert not _STALE_HARNESS_RE.search(line), f"False positive: {line!r}"


# ─── _llm_clean_stale_claude_md ──────────────────────────────────────────────

def test_llm_clean_skips_when_no_stale_patterns(tmp_path, monkeypatch):
    """No stale harness patterns outside auto block → subprocess never called."""
    calls: list = []

    def mock_run(*_args, **_kwargs):
        calls.append(_args)

    monkeypatch.setattr("subprocess.run", mock_run)
    (tmp_path / "CLAUDE.md").write_text(
        _CLAUDE_AUTO_START + "\nHarness Status\n" + _CLAUDE_AUTO_END
        + "\n\n## Commands\n```bash\nnpm test\n```\n"
    )
    _llm_clean_stale_claude_md(tmp_path)
    assert len(calls) == 0  # LLM not called when content is already clean


def test_llm_clean_preserves_file_when_llm_drops_markers(tmp_path, monkeypatch):
    """LLM output missing auto markers → file left unchanged (safety check)."""
    original = (
        _CLAUDE_AUTO_START + "\nHarness Status\n" + _CLAUDE_AUTO_END
        + "\n\nCurrent state: Phase 7, Gate 4 PASS (score 96.5)\n"
    )
    (tmp_path / "CLAUDE.md").write_text(original)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")

    class _FakeResult:
        returncode = 0
        stdout = "## Some content without the auto markers\n"
        stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *_a, **_k: _FakeResult())
    _llm_clean_stale_claude_md(tmp_path)
    # File must be unchanged because LLM dropped auto markers
    assert (tmp_path / "CLAUDE.md").read_text() == original


def test_llm_clean_skips_when_no_claude_md(tmp_path):
    """No CLAUDE.md → no-op, no exception."""
    _llm_clean_stale_claude_md(tmp_path)  # must not raise
