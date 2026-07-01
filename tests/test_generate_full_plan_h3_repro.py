"""Regression tests for Bug H3 in scripts/generate_full_plan.py.

Bug H3: parse_srs_fr_nfr_xref() dropped empty cells when building the
`cells` list, so column indices no longer lined up with the header's
nfr_col_idx. A row like `| FR-01 | something | | NFR-02 |` would
associate FR-01 with "something" (column 1) instead of NFR-02 (column 3),
silently dropping the FR-NFR linkage for sparse rows.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture
def parse_fn():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    mod = importlib.import_module("generate_full_plan")
    return mod.parse_srs_fr_nfr_xref


def _write_srs(tmp_path: Path, body: str) -> Path:
    srs = tmp_path / "SRS.md"
    srs.write_text(body, encoding="utf-8")
    return srs


def test_sparse_row_with_empty_cell_in_middle_keeps_fr_nfr_link(parse_fn, tmp_path):
    """Row with empty middle cell — NFR in column 3 must still associate."""
    srs = _write_srs(tmp_path, """
## Cross-Reference

| FR ID | Description | Notes | NFR Association |
|-------|-------------|-------|-----------------|
| FR-01 | alpha | something | NFR-02 |
| FR-02 | beta | | NFR-03 |
""")
    result = parse_fn(srs)
    assert result == {
        "FR-01": ["NFR-02"],
        "FR-02": ["NFR-03"],
    }, f"sparse row dropped FR-NFR link: {result}"


def test_sparse_row_with_empty_nfr_cell(parse_fn, tmp_path):
    """Row where the NFR cell itself is empty must NOT associate anything."""
    srs = _write_srs(tmp_path, """
## Cross-Reference

| FR ID | Description | NFR Association |
|-------|-------------|-----------------|
| FR-01 | alpha | NFR-02 |
| FR-02 | beta | |
""")
    result = parse_fn(srs)
    assert result == {"FR-01": ["NFR-02"]}, f"empty-NFR row leaked: {result}"


def test_dense_row_unchanged(parse_fn, tmp_path):
    """Existing dense rows must keep working."""
    srs = _write_srs(tmp_path, """
## Cross-Reference

| FR ID | Description | NFR Association |
|-------|-------------|-----------------|
| FR-01 | alpha | NFR-02 |
| FR-02 | beta | NFR-03 |
""")
    result = parse_fn(srs)
    assert result == {
        "FR-01": ["NFR-02"],
        "FR-02": ["NFR-03"],
    }


def test_sparse_row_does_not_misread_intermediate_column(parse_fn, tmp_path):
    """The H3 regression: with `if c.strip()` filtering, a row like
    `| FR-01 | something | | NFR-02 |` would associate FR-01 with
    "something" (col 1) instead of NFR-02 (col 3). After the fix the
    NFR column must be read at its true index 3."""
    srs = _write_srs(tmp_path, """
## Cross-Reference

| FR ID | Description | Notes | NFR Association |
|-------|-------------|-------|-----------------|
| FR-01 | alpha | something | NFR-02 |
""")
    result = parse_fn(srs)
    assert "FR-01" in result, "FR-01 should be associated with NFR-02"
    assert result["FR-01"] == ["NFR-02"], (
        f"sparse row should read column 3 (NFR-02), not column 1: {result}"
    )
    # The Description or Notes must NOT appear as if they were an NFR id.
    assert all("NFR-" in nfr for nfr in result["FR-01"])