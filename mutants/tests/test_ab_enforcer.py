from core.quality_gate.ab_enforcer import ABEnforcer

def test_ab_enforcer_log_not_found(tmp_path):
    enforcer = ABEnforcer(tmp_path)
    result = enforcer.verify_developer_reviewer_separation("phase_1")
    assert result["separated"] is False
    # ab_enforcer.py doesn't return 'error' key on some paths, check logic
    # In success case it returns 'details', in 'not found' case it returns 'error'
    assert "error" in result
    assert "DEVELOPMENT_LOG.md not found" in result["error"]

def test_ab_enforcer_separation_success(tmp_path):
    log_file = tmp_path / "DEVELOPMENT_LOG.md"
    # Looking at _extract_session logic, it looks for "role.*?[Ss]ession[:]\s*([a-zA-Z0-9-]+)"
    # or "developer agent" as a marker
    content = """
## Phase 1
developer session: sess-123
reviewer session: sess-456
"""
    log_file.write_text(content)
    enforcer = ABEnforcer(tmp_path)
    result = enforcer.verify_developer_reviewer_separation("phase_1")
    assert result["separated"] is True
    assert result["developer_session"] == "sess-123"
    assert result["reviewer_session"] == "sess-456"

def test_ab_enforcer_separation_fail_same_session(tmp_path):
    log_file = tmp_path / "DEVELOPMENT_LOG.md"
    content = """
## Phase 1
developer session: sess-123
reviewer session: sess-123
"""
    log_file.write_text(content)
    enforcer = ABEnforcer(tmp_path)
    result = enforcer.verify_developer_reviewer_separation("phase_1")
    assert result["separated"] is False

def test_ab_enforcer_phase_not_found(tmp_path):
    log_file = tmp_path / "DEVELOPMENT_LOG.md"
    log_file.write_text("## Phase 2\n...")
    enforcer = ABEnforcer(tmp_path)
    result = enforcer.verify_developer_reviewer_separation("phase_1")
    assert result["separated"] is False
    assert "error" in result
    assert "Phase phase_1 not found in DEVELOPMENT_LOG" in result["error"]


def test_verify_qa_not_developer_separated(tmp_path):
    log_file = tmp_path / "DEVELOPMENT_LOG.md"
    content = """
## Phase 3
developer session: dev-123
reviewer session: rev-456
## Phase 4
developer session: dev-789
tester session: qa-999
"""
    log_file.write_text(content)
    enforcer = ABEnforcer(tmp_path)
    result = enforcer.verify_qa_not_developer()
    assert result["separated"] is True


def test_verify_ab_dialogue_exists(tmp_path):
    log_file = tmp_path / "DEVELOPMENT_LOG.md"
    content = """
## Phase 3
developer session: dev-123
Reviewer raised several issues with the design.
The Developer responded to Reviewer feedback and revised.
"""
    log_file.write_text(content)
    enforcer = ABEnforcer(tmp_path)
    result = enforcer.verify_ab_dialogue_exists("phase_3")
    assert result["has_dialogue"] is True


def test_verify_ab_dialogue_missing(tmp_path):
    enforcer = ABEnforcer(tmp_path)
    result = enforcer.verify_ab_dialogue_exists("phase_1")
    assert result["has_dialogue"] is False
