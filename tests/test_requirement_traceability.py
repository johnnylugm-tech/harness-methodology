from core.requirement_traceability import RequirementTraceability, TraceStatus

def test_add_requirement():
    rt = RequirementTraceability(project_id="test-proj")
    rt.add_requirement(req_id="FR-001", title="Test FR", description="Desc")
    req = rt.requirements["FR-001"]
    assert req.title == "Test FR"
    assert req.status == TraceStatus.PENDING

def test_add_link_bidirectional():
    rt = RequirementTraceability(project_id="test-proj")
    rt.add_requirement("FR-001", "FR 1")
    # add_code_component implicitly adds a link if fr_id is provided
    rt.add_code_component("core/engine.py", fr_id="FR-001")
    
    links = rt.links
    assert len(links) == 1
    assert links[0].source_id == "FR-001"

def test_verify_completeness_report():
    rt = RequirementTraceability(project_id="test-proj")
    rt.add_requirement("FR-001", "FR 1")
    # Missing link to code/test
    report = rt.verify_completeness()
    assert report["total_requirements"] == 1
    assert "FR-001" in report["missing_mappings"]["fr_without_code"]
    assert report["code_coverage"] == "0.0%"

def test_aspice_compliance_report():
    rt = RequirementTraceability(project_id="test-proj")
    rt.add_requirement("FR-001", "FR 1")
    # Add SRS, Code, and Test links to make it compliant
    rt.add_link("fr", "FR-001", "srs", "SRS-001")
    rt.add_code_component("core/engine.py", fr_id="FR-001")
    rt.add_test_coverage("tests/test_engine.py", fr_id="FR-001", coverage_percentage=100.0)
    
    report = rt.export_report(format="aspice")
    assert report["aspice_compliance"]["SWE_3_B_SP1"] is True
    assert report["aspice_compliance"]["SWE_3_B_SP2"] is True
    assert report["aspice_compliance"]["SWE_3_B_SP3"] is True
