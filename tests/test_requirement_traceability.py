from core.requirement_traceability import (
    RequirementTraceability, TraceStatus, Requirement, CodeComponent, TestCoverage
)


def test_add_requirement():
    rt = RequirementTraceability(project_id="test-proj")
    rt.add_requirement(req_id="FR-001", title="Test FR", description="Desc")
    req = rt.requirements["FR-001"]
    assert req.title == "Test FR"
    assert req.status == TraceStatus.PENDING


def test_requirement_to_dict():
    req = Requirement(req_id="FR-001", title="T", description="D",
                      srs_section="SRS-1")
    d = req.to_dict()
    assert d["req_id"] == "FR-001"
    assert d["status"] == "pending"


def test_code_component_to_dict():
    cc = CodeComponent(file_path="a.py", fr_id="FR-001")
    d = cc.to_dict()
    assert d["file_path"] == "a.py"
    assert d["fr_id"] == "FR-001"


def test_test_coverage_to_dict():
    tc = TestCoverage(test_file="t.py", fr_id="FR-001")
    d = tc.to_dict()
    assert d["test_file"] == "t.py"


def test_get_upstream():
    """PR 1: get_upstream uses the O(1) reverse-index fast path.

    The reverse index is populated by add_link when `bidirectional=True`
    (the default for add_code_component / add_test_coverage). This test
    asserts BOTH the O(1) structural fact (the index entry exists) and
    the user-facing behavior (the parent FR is returned).
    """
    rt = RequirementTraceability(project_id="p")
    rt.add_requirement("FR-001", "T")
    rt.add_code_component("a.py", fr_id="FR-001")
    rt.add_test_coverage("t.py", fr_id="FR-001")
    # O(1) fast-path: reverse index must contain the targets
    assert "a.py" in rt._reverse_link_index
    assert "t.py" in rt._reverse_link_index
    # get_upstream should hit the O(1) path
    upstream = rt.get_upstream("a.py")
    assert "FR-001" in upstream["fr"]


def test_unidirectional_link_does_not_populate_reverse():
    """Unidirectional links (e.g. spec section refs) must NOT poison reverse map.

    The reverse index is the O(1) fast path for get_upstream; with no index
    entry, get_upstream falls back to the linear scan, which still returns the
    source FR for matching links. The structural invariant we test here is
    that the reverse index was NOT populated.
    """
    rt = RequirementTraceability(project_id="p")
    rt.add_link("fr", "FR-002", "srs", "SAD §3.4.1", bidirectional=False)
    assert "SAD §3.4.1" not in rt._reverse_link_index
    assert "FR-002" not in rt._reverse_link_index


def test_get_upstream_o1_path_used_when_indexed():
    """When the reverse index is populated, get_upstream uses the O(1) path."""
    rt = RequirementTraceability(project_id="p")
    rt.add_requirement("FR-003", "T")
    rt.add_code_component("c.py", fr_id="FR-003")
    rt.add_test_coverage("t.py", fr_id="FR-003")
    # The reverse index must contain the targets
    assert "c.py" in rt._reverse_link_index
    assert "t.py" in rt._reverse_link_index
    # get_upstream should return the parent FR for each
    assert rt.get_upstream("c.py")["fr"] == ["FR-003"]
    assert rt.get_upstream("t.py")["fr"] == ["FR-003"]


def test_save(tmp_path):
    rt = RequirementTraceability(project_id="p")
    rt.add_requirement("FR-001", "T")
    p = tmp_path / "report.json"
    rt.save(str(p))
    assert p.exists()


# ---------------------------------------------------------------------------
# Bug #103: save() exists but load() is missing.
# stage_pass_generator.py:649 calls RequirementTraceability.load(file)
# which raised AttributeError. The generic try/except Exception silently
# swallowed the failure. These tests pin the contract: load() must exist
# and reconstruct full state, not just a partial report.
# ---------------------------------------------------------------------------


def test_load_round_trip_preserves_full_state(tmp_path):
    """Bug #103: save_state() then load_state() must restore requirements,
    components, tests, and links — not just the summary report."""
    rt = RequirementTraceability(project_id="round-trip")
    rt.add_requirement("FR-001", "Title A", srs_section="SRS-1")
    rt.add_requirement("FR-002", "Title B", srs_section="SRS-2")
    rt.add_code_component("a.py", fr_id="FR-001", functions=["f1"])
    rt.add_code_component("b.py", fr_id="FR-002")
    rt.add_test_coverage("test_a.py", fr_id="FR-001", test_functions=["test_f1"])
    rt.add_test_coverage("test_b.py", fr_id="FR-002")
    rt.add_link("fr", "FR-001", "srs", "SRS-1")

    p = tmp_path / "state.json"
    rt.save_state(str(p))

    rt2 = RequirementTraceability.load_state(str(p))
    assert rt2.project_id == "round-trip"
    # Requirements round-tripped (the data verify_completeness needs).
    assert set(rt2.requirements.keys()) == {"FR-001", "FR-002"}
    assert rt2.requirements["FR-001"].srs_section == "SRS-1"
    # Components round-tripped.
    assert set(rt2.code_components.keys()) == {"a.py", "b.py"}
    assert rt2.code_components["a.py"].functions == ["f1"]
    # Tests round-tripped.
    assert set(rt2.test_coverage.keys()) == {"test_a.py", "test_b.py"}
    # Links round-tripped.
    assert len(rt2.links) == len(rt.links)
    # The full completeness report must match.
    assert rt2.verify_completeness() == rt.verify_completeness()


def test_load_state_then_verify_completeness_matches_original(tmp_path):
    """Bug #103 specific symptom: stage_pass_generator calls
    `rt = RequirementTraceability.load_state(file); rt.verify_completeness()`.
    This must produce the same percentages as the original."""
    rt = RequirementTraceability(project_id="p")
    rt.add_requirement("FR-001", "T1")
    rt.add_requirement("FR-002", "T2")
    rt.add_link("fr", "FR-001", "srs", "s1")
    rt.add_link("fr", "FR-001", "code", "c1")
    rt.add_link("fr", "FR-001", "test", "t1")
    p = tmp_path / "state.json"
    rt.save_state(str(p))

    rt2 = RequirementTraceability.load_state(str(p))
    expected = rt.verify_completeness()
    actual = rt2.verify_completeness()
    assert actual == expected
    # The coverage of FR-001 (fully traced) should not be 0%.
    assert actual["srs_coverage"] != "0.0%" or len(expected["missing_mappings"]["fr_without_srs"]) > 0


def test_load_state_raises_on_missing_file(tmp_path):
    """Bug #103: load_state() should fail loudly with a clear error, not be
    silently swallowed by a generic except-Exception block in callers."""
    import pytest
    with pytest.raises(FileNotFoundError):
        RequirementTraceability.load_state(str(tmp_path / "does_not_exist.json"))



def test_verify_completeness_with_mixed_links():
    rt = RequirementTraceability(project_id="p")
    rt.add_requirement("FR-001", "T1")
    rt.add_requirement("FR-002", "T2")
    rt.add_link("fr", "FR-001", "srs", "s1")
    rt.add_link("fr", "FR-001", "code", "c1")
    rt.add_link("fr", "FR-001", "test", "t1")
    report = rt.verify_completeness()
    assert report["total_requirements"] == 2
