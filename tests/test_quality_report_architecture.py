"""Tests for the CRG architecture section in QUALITY_REPORT (Stage 4a).

call_crg_tool is mocked — no real code-review-graph is invoked.
"""

import sys
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import generate_quality_report as gqr  # noqa: E402


def test_formats_overview_with_communities_and_warnings(tmp_path):
    fake = {
        "status": "ok",
        "summary": "Architecture: 4 communities, 1 warning(s)",
        "communities": [
            {"name": "core", "size": 10, "cohesion": 0.31},
            {"name": "utils", "size": 5, "cohesion": 0.5},
        ],
        "warnings": ["High coupling (44 edges) between 'a' and 'b'"],
    }
    with patch("harness.crg_api.call_crg_tool", return_value=fake):
        text = "\n".join(gqr._build_architecture_section(tmp_path))

    assert "Architecture: 4 communities" in text
    assert "| core | 10 | 0.31 |" in text
    assert "⚠ High coupling (44 edges) between 'a' and 'b'" in text


def test_largest_community_sorted_first(tmp_path):
    fake = {
        "status": "ok", "summary": "s",
        "communities": [
            {"name": "small", "size": 2, "cohesion": 0.9},
            {"name": "big", "size": 99, "cohesion": 0.1},
        ],
        "warnings": [],
    }
    with patch("harness.crg_api.call_crg_tool", return_value=fake):
        text = "\n".join(gqr._build_architecture_section(tmp_path))
    # Largest community listed first (sorted by size, descending).
    assert text.index("big") < text.index("small")


def test_graceful_when_crg_unavailable(tmp_path):
    with patch("harness.crg_api.call_crg_tool", side_effect=RuntimeError("no crg")):
        out = gqr._build_architecture_section(tmp_path)
    assert "unavailable" in "\n".join(out)


def test_graceful_when_status_not_ok(tmp_path):
    with patch("harness.crg_api.call_crg_tool", return_value={"status": "error"}):
        out = gqr._build_architecture_section(tmp_path)
    assert "unavailable" in "\n".join(out)
