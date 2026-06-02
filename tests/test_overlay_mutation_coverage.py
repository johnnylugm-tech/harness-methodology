"""Mutation testing regression tests for overlay.py.

Targeted tests for surviving mutants identified in the mutation
testing sprint. Each test kills a specific class of mutation that
current tests miss.

Note: tests for `_diff_append_to_existing` and `_closest_module`
live in `test_auto_fix_propose_mutation_coverage.py` — those
helpers belong to auto_fix_propose.py, not overlay.py.
"""
from pathlib import Path

import pytest


# Playbook §6: dynamic mutation-oracle marker
pytestmark = pytest.mark.mutation_oracle


# ---------------------------------------------------------------------------
# load_overlay: returns empty dict when root is not a mapping
# ---------------------------------------------------------------------------

def test_load_overlay_returns_empty_for_list_root(tmp_path):
    """YAML root is a list, not a dict. load_overlay must return {}."""
    from core.traceability.overlay import load_overlay
    p = tmp_path / "ov.yaml"
    p.write_text("- item1\n- item2\n")  # root is a list
    assert load_overlay(p) == {}


def test_load_overlay_returns_empty_for_string_root(tmp_path):
    """YAML root is a string (single scalar), not a dict. Return {}."""
    from core.traceability.overlay import load_overlay
    p = tmp_path / "ov.yaml"
    p.write_text("just a string\n")
    assert load_overlay(p) == {}


# ---------------------------------------------------------------------------
# render_markdown: annotations + justifications must appear in output
# ---------------------------------------------------------------------------

def test_render_includes_justification_in_row():
    """Kills mutants that drop `justification` from the row.

    When an override specifies `justification`, the rendered
    markdown must include it (e.g. as a <sub> tag). Without this
    test, a regression silently drops the human-readable context.
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
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
        "requirements": {"FR-06": {"fr_id": "FR-06", "status": "verified",
                                   "code_files": ["a.py"],
                                   "test_files": ["t.py"],
                                   "sad_module": "§3.x",
                                   "justification": "constitution profile"}},
        "annotations": {},
    }
    out = render_markdown(merged)
    assert "constitution profile" in out, (
        f"justification must appear in rendered markdown; got:\n{out[:500]}"
    )


def test_render_includes_annotations_in_row():
    """Kills mutants that drop annotations from the row.

    The merge_overlay function buckets annotations by FR. The
    renderer must show them. Without this test, a regression that
    loses the bucketing is silent.
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
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
        "requirements": {"FR-04": {"fr_id": "FR-04", "status": "pending",
                                   "code_files": [],
                                   "test_files": [],
                                   "sad_module": "—"}},
        "annotations": {"FR-04": ["impl FR", "tracked for next scan"]},
    }
    out = render_markdown(merged)
    # Both annotations should be visible
    assert "impl FR" in out
    assert "tracked for next scan" in out


# ---------------------------------------------------------------------------
# merge_overlay: overrides create rows that don't exist in atomic
# ---------------------------------------------------------------------------

def test_merge_overlay_creates_row_even_when_no_atomic():
    """Kills mutants that break setdefault path.

    When an override references an FR that's NOT in atomic (i.e.,
    not scanned by the scanner), merge_overlay must still create
    a row for it. Without this test, a regression that requires
    the FR to exist in atomic would silently drop manual entries.
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.traceability.overlay import merge_overlay
    atomic = {"requirements": {}}  # Empty atomic
    overlay = {"overrides": [{"fr_id": "FR-99", "status": "verified",
                              "code_files": ["x.py"]}]}
    merged = merge_overlay(atomic, overlay)
    assert "FR-99" in merged["requirements"]
    assert merged["requirements"]["FR-99"]["code_files"] == ["x.py"]


def test_merge_overlay_preserves_atomic_keys():
    """Kills mutants that confuse atomic + override iteration.

    The merged dict must have ALL atomic FRs (with override winning
    on overlapping keys) AND all override-only FRs.
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.traceability.overlay import merge_overlay
    atomic = {"requirements": {"FR-01": {"status": "pending",
                                         "code_files": ["atomic.py"]}}}
    overlay = {"overrides": [{"fr_id": "FR-01", "status": "verified",
                              "code_files": ["override.py"]},
                             {"fr_id": "FR-99", "status": "verified",
                              "code_files": ["new.py"]}]}
    merged = merge_overlay(atomic, overlay)
    # FR-01: override wins on status + code_files
    assert merged["requirements"]["FR-01"]["status"] == "verified"
    assert merged["requirements"]["FR-01"]["code_files"] == ["override.py"]
    # FR-99: created from overlay (not in atomic)
    assert "FR-99" in merged["requirements"]


# ---------------------------------------------------------------------------
# validate_overlay: known keys whitelist
# ---------------------------------------------------------------------------

def test_validate_overlay_rejects_unknown_keys():
    """Kills mutants that remove the unknown-key whitelist.

    The validator must reject overrides with keys outside the
    allowed set (status, code_files, test_files, sad_module,
    justification, fr_id).
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.traceability.overlay import validate_overlay
    data = {"overrides": [{"fr_id": "FR-01", "status": "verified",
                           "injection": "<script>"}]}
    errs = validate_overlay(data)
    assert any("unknown" in e for e in errs), \
        f"unknown key 'injection' must be rejected; errs={errs}"


def test_validate_overlay_annotation_requires_note():
    """Kills mutants that drop the `note` required check on annotations."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.traceability.overlay import validate_overlay
    data = {"annotations": [{"fr_id": "FR-01"}]}  # no `note`
    errs = validate_overlay(data)
    assert any("note" in e for e in errs), \
        f"annotation missing 'note' must be rejected; errs={errs}"
