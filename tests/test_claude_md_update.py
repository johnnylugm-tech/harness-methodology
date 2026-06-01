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

from harness_cli import (  # noqa: E402
    _CLAUDE_AUTO_END,
    _CLAUDE_AUTO_START,
    _build_claude_md_auto_section,
    _update_claude_md,
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
