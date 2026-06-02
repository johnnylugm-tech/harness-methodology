"""PR 2: overlay merge tests.

Confirms the non-destructive split between auto-generated and manual
matrix content. After PR 2, `scripts/build_traceability.py` regenerates
TRACEABILITY_MATRIX.md without wiping manual rows (FR-06, FR-ENF-01..03)
that live in TRACEABILITY_MATRIX.overlay.yaml.
"""
from pathlib import Path

import pytest


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """Minimal repo: SAD.md with FR-01/02/03, code with [FR-01] + [FR-02],
    test for FR-01 only. FR-03 is uncoded+untested; FR-02 is uncoded test."""
    arch = tmp_path / "02-architecture"
    arch.mkdir()
    (arch / "SAD.md").write_text(
        "FR-01: alpha\nFR-02: beta\nFR-03: gamma\n"
    )
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text('"""[FR-01]""" def f(): pass\n')
    (tmp_path / "core" / "b.py").write_text('"""[FR-02]""" def g(): pass\n')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text('"""[FR-01]"""\n')
    return tmp_path


def _build_model(project: Path):
    from scripts.build_traceability import build_traceability
    return build_traceability(project)


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def test_validate_overlay_empty():
    from core.traceability.overlay import validate_overlay
    assert validate_overlay({}) == []


def test_validate_overlay_correct_schema():
    from core.traceability.overlay import validate_overlay
    data = {
        "schema": "harness/traceability/overlay/v1",
        "overrides": [
            {"fr_id": "FR-06", "status": "verified",
             "code_files": ["a.py"], "test_files": ["t.py"]}
        ],
        "annotations": [{"fr_id": "FR-04", "note": "impl FR"}],
    }
    assert validate_overlay(data) == []


def test_validate_overlay_wrong_schema():
    from core.traceability.overlay import validate_overlay
    data = {"schema": "some/other/schema/v9"}
    errs = validate_overlay(data)
    assert any("schema" in e for e in errs)


def test_validate_overlay_missing_fr_id():
    from core.traceability.overlay import validate_overlay
    data = {"overrides": [{"status": "verified"}]}
    errs = validate_overlay(data)
    assert any("fr_id" in e for e in errs)


def test_validate_overlay_unknown_key():
    from core.traceability.overlay import validate_overlay
    data = {"overrides": [{"fr_id": "FR-01", "bogus": True}]}
    errs = validate_overlay(data)
    assert any("unknown" in e for e in errs)


def test_validate_overlay_annotations_shape():
    from core.traceability.overlay import validate_overlay
    data = {"annotations": [{"fr_id": "FR-01"}]}  # missing note
    errs = validate_overlay(data)
    assert any("note" in e for e in errs)


# ---------------------------------------------------------------------------
# Load + merge
# ---------------------------------------------------------------------------

def test_load_overlay_missing_file(tmp_path):
    from core.traceability.overlay import load_overlay
    assert load_overlay(tmp_path / "nope.yaml") == {}


def test_load_overlay_empty_file(tmp_path):
    from core.traceability.overlay import load_overlay
    p = tmp_path / "empty.yaml"
    p.write_text("")
    assert load_overlay(p) == {}


def test_load_overlay_valid_yaml(tmp_path):
    from core.traceability.overlay import load_overlay
    p = tmp_path / "ov.yaml"
    p.write_text(
        "schema: harness/traceability/overlay/v1\n"
        "overrides:\n"
        "  - fr_id: FR-06\n"
        "    status: verified\n"
    )
    data = load_overlay(p)
    assert data["schema"] == "harness/traceability/overlay/v1"
    assert data["overrides"][0]["fr_id"] == "FR-06"


def test_merge_overlay_atomic_wins_by_default():
    from core.traceability.overlay import merge_overlay
    atomic = {"requirements": {"FR-01": {"status": "pending"}}}
    overlay: dict = {}
    merged = merge_overlay(atomic, overlay)
    assert merged["requirements"]["FR-01"]["status"] == "pending"


def test_merge_overlay_overrides_win():
    from core.traceability.overlay import merge_overlay
    atomic = {"requirements": {"FR-01": {"status": "pending",
                                        "code_files": ["a.py"]}}}
    overlay = {"overrides": [{"fr_id": "FR-01", "status": "verified",
                             "code_files": ["b.py", "c.py"]}]}
    merged = merge_overlay(atomic, overlay)
    assert merged["requirements"]["FR-01"]["status"] == "verified"
    assert merged["requirements"]["FR-01"]["code_files"] == ["b.py", "c.py"]


def test_merge_overlay_creates_missing_fr():
    from core.traceability.overlay import merge_overlay
    atomic: dict = {"requirements": {}}
    overlay = {"overrides": [{"fr_id": "FR-06", "status": "verified"}]}
    merged = merge_overlay(atomic, overlay)
    assert "FR-06" in merged["requirements"]
    assert merged["requirements"]["FR-06"]["status"] == "verified"


def test_merge_overlay_annotations_bucketed_by_fr():
    from core.traceability.overlay import merge_overlay
    atomic: dict = {"requirements": {}}
    overlay = {"annotations": [
        {"fr_id": "FR-04", "note": "impl FR"},
        {"fr_id": "FR-04", "note": "tracked for next scan"},
        {"fr_id": "FR-05", "note": "different FR"},
    ]}
    merged = merge_overlay(atomic, overlay)
    assert merged["annotations"]["FR-04"] == [
        "impl FR", "tracked for next scan"
    ]
    assert merged["annotations"]["FR-05"] == ["different FR"]


# ---------------------------------------------------------------------------
# Atomic → dict
# ---------------------------------------------------------------------------

def test_atomic_to_dict_includes_requirements_and_completeness(fixture_repo):
    from core.traceability.overlay import atomic_to_dict
    rt = _build_model(fixture_repo)
    atomic = atomic_to_dict(rt)
    assert "FR-01" in atomic["requirements"]
    assert atomic["requirements"]["FR-01"]["status"] == "verified"
    assert "FR-03" in atomic["requirements"]
    assert atomic["completeness"]["total_requirements"] >= 3


# ---------------------------------------------------------------------------
# Render: sentinels + content
# ---------------------------------------------------------------------------

def test_render_markdown_includes_sentinels():
    from core.traceability.overlay import render_markdown
    merged = {
        "project_id": "test",
        "completeness": {"total_requirements": 1, "srs_coverage": "100.0%",
                         "code_coverage": "100.0%", "test_coverage": "100.0%",
                         "verification_rate": "100.0%", "total_links": 1,
                         "missing_mappings": {"fr_without_srs": [],
                                              "fr_without_code": [],
                                              "fr_without_test": []}},
        "missing": {"fr_without_srs": [], "fr_without_code": [],
                    "fr_without_test": []},
        "requirements": {"FR-01": {"fr_id": "FR-01", "status": "verified",
                                   "code_files": ["a.py"],
                                   "test_files": ["t.py"],
                                   "sad_module": "—"}},
        "annotations": {},
    }
    out = render_markdown(merged)
    assert "<!-- AUTO-GEN:START -->" in out
    assert "<!-- AUTO-GEN:END -->" in out
    assert "FR-01" in out
    assert "verified" in out


def test_render_markdown_annotations_appear_in_row():
    from core.traceability.overlay import render_markdown
    merged = {
        "project_id": "t", "completeness": {}, "missing": {},
        "requirements": {"FR-04": {"fr_id": "FR-04", "status": "verified",
                                   "code_files": [], "test_files": [],
                                   "sad_module": "—"}},
        "annotations": {"FR-04": ["impl FR"]},
    }
    out = render_markdown(merged)
    assert "annotation: impl FR" in out


def test_render_merged_markdown_atomic_only_when_no_overlay(fixture_repo):
    from core.traceability.overlay import render_merged_markdown
    rt = _build_model(fixture_repo)
    md, errs = render_merged_markdown(rt, None)
    assert errs == []
    assert "AUTO-GEN:START" in md
    # FR-01 should be present from atomic
    assert "FR-01" in md


def test_render_merged_markdown_with_overlay(fixture_repo):
    from core.traceability.overlay import render_merged_markdown
    overlay = fixture_repo / "TRACEABILITY_MATRIX.overlay.yaml"
    overlay.write_text(
        "schema: harness/traceability/overlay/v1\n"
        "overrides:\n"
        "  - fr_id: FR-99\n"
        "    status: verified\n"
        "    code_files: [core/manual.py]\n"
        "    sad_module: \"§3.x Manual mapping\"\n"
    )
    rt = _build_model(fixture_repo)
    md, errs = render_merged_markdown(rt, overlay)
    assert errs == []
    assert "FR-99" in md
    assert "§3.x Manual mapping" in md


def test_render_merged_markdown_invalid_overlay_falls_back(fixture_repo):
    from core.traceability.overlay import render_merged_markdown
    overlay = fixture_repo / "TRACEABILITY_MATRIX.overlay.yaml"
    overlay.write_text(
        "schema: wrong/schema\n"
        "overrides: [{fr_id: FR-99}]\n"
    )
    rt = _build_model(fixture_repo)
    md, errs = render_merged_markdown(rt, overlay)
    assert len(errs) > 0
    # Still rendered, but without FR-99 (which is in invalid overlay only)
    assert "AUTO-GEN:START" in md


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def test_migrate_existing_matrix_wraps_with_sentinels(tmp_path):
    from core.traceability.overlay import migrate_existing_matrix
    matrix = tmp_path / "TRACEABILITY_MATRIX.md"
    overlay = tmp_path / "TRACEABILITY_MATRIX.overlay.yaml"
    matrix.write_text("# Traceability Matrix\n\nold content\n")
    result = migrate_existing_matrix(matrix, overlay)
    assert result["status"] == "wrapped"
    assert "AUTO-GEN:START" in matrix.read_text()
    assert overlay.exists()


def test_migrate_existing_matrix_dry_run_does_not_write(tmp_path):
    from core.traceability.overlay import migrate_existing_matrix
    matrix = tmp_path / "TRACEABILITY_MATRIX.md"
    overlay = tmp_path / "TRACEABILITY_MATRIX.overlay.yaml"
    matrix.write_text("old\n")
    original = matrix.read_text()
    result = migrate_existing_matrix(matrix, overlay, dry_run=True)
    assert result["status"] == "wrapped"
    assert matrix.read_text() == original  # unchanged
    assert not overlay.exists()


def test_migrate_existing_matrix_idempotent(tmp_path):
    from core.traceability.overlay import migrate_existing_matrix
    matrix = tmp_path / "TRACEABILITY_MATRIX.md"
    overlay = tmp_path / "TRACEABILITY_MATRIX.overlay.yaml"
    matrix.write_text("<!-- AUTO-GEN:START -->\nAUTO\n<!-- AUTO-GEN:END -->\n")
    result = migrate_existing_matrix(matrix, overlay)
    assert result["status"] == "already-migrated"


def test_migrate_existing_matrix_missing_file(tmp_path):
    from core.traceability.overlay import migrate_existing_matrix
    result = migrate_existing_matrix(
        tmp_path / "nope.md", tmp_path / "ov.yaml"
    )
    assert result["status"] == "missing"


# ---------------------------------------------------------------------------
# End-to-end: build_traceability preserves overlay rows
# ---------------------------------------------------------------------------

def test_build_preserves_overlay_rows(fixture_repo, monkeypatch):
    """Re-running build_traceability.py must NOT delete FR-06/ENF rows
    that live in the overlay."""
    from scripts.build_traceability import generate_markdown_matrix

    overlay = fixture_repo / "TRACEABILITY_MATRIX.overlay.yaml"
    overlay.write_text(
        "schema: harness/traceability/overlay/v1\n"
        "overrides:\n"
        "  - fr_id: FR-06\n"
        "    status: verified\n"
        "    code_files: [core/manual.py]\n"
        "    test_files: [tests/test_manual.py]\n"
        "    sad_module: \"§3.x Manual mapping\"\n"
    )
    rt = _build_model(fixture_repo)
    matrix_path = fixture_repo / "TRACEABILITY_MATRIX.md"
    generate_markdown_matrix(rt, matrix_path, overlay)

    text = matrix_path.read_text()
    assert "FR-06" in text
    assert "core/manual.py" in text
    assert "§3.x Manual mapping" in text

    # Re-run: overlay rows must still be there
    generate_markdown_matrix(rt, matrix_path, overlay)
    text2 = matrix_path.read_text()
    assert "FR-06" in text2
    assert "§3.x Manual mapping" in text2
