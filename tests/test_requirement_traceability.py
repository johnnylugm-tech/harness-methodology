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
    rt = RequirementTraceability(project_id="p")
    rt.add_requirement("FR-001", "T")
    rt.add_code_component("a.py", fr_id="FR-001")
    rt.add_test_coverage("t.py", fr_id="FR-001")
    upstream = rt.get_upstream("a.py")
    assert "FR-001" in upstream["fr"]


def test_reverse_link_index_populated_for_bidirectional():
    """PR 1: bidirectional links materialize a reverse index for O(1) lookup."""
    rt = RequirementTraceability(project_id="p")
    rt.add_requirement("FR-001", "T")
    rt.add_code_component("a.py", fr_id="FR-001")  # default bidirectional=True
    assert "a.py" in rt._reverse_link_index
    assert "FR-001" in rt._reverse_link_index
    # get_upstream should hit the O(1) path
    assert "FR-001" in rt.get_upstream("a.py")["fr"]


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


def test_verify_completeness_with_mixed_links():
    rt = RequirementTraceability(project_id="p")
    rt.add_requirement("FR-001", "T1")
    rt.add_requirement("FR-002", "T2")
    rt.add_link("fr", "FR-001", "srs", "s1")
    rt.add_link("fr", "FR-001", "code", "c1")
    rt.add_link("fr", "FR-001", "test", "t1")
    report = rt.verify_completeness()
    assert report["total_requirements"] == 2
