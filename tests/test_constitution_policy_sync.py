"""Tests for constitution_policy_sync module."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from enforcement.constitution_policy_sync import (
    ConstitutionPolicyGenerator,
    main,
)
from enforcement.policy_engine import PolicyEngine, Policy, EnforcementLevel


# ── Fixtures ──

@pytest.fixture
def generator():
    return ConstitutionPolicyGenerator()


@pytest.fixture
def temp_constitution():
    """Create a temporary Constitution.md with rules."""
    content = """# Constitution

## Rule: commit_task_id
[SEVERITY: critical]
[THRESHOLD: 90]
All commits must contain a task_id, format: [TASK-XXX]

## Rule: quality_gate
[SEVERITY: high]
[THRESHOLD: 85]
Quality gate must pass with score >= 85

## Rule: coverage_check
[SEVERITY: low]
[THRESHOLD: 80]
Coverage must meet 80% threshold
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(content)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def temp_output():
    """Temporary output path."""
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
        path = f.name
    yield path
    os.unlink(path)


# ── find_constitution ──

def test_find_constitution_found(generator, temp_constitution):
    with patch.object(generator, "DEFAULT_CONSTITUTION_PATHS", [temp_constitution]):
        assert generator.find_constitution() == temp_constitution


def test_find_constitution_not_found(generator):
    generator.DEFAULT_CONSTITUTION_PATHS = ["/nonexistent/path.md"]
    assert generator.find_constitution() is None


# ── parse_constitution ──

def test_parse_rule_format(generator, temp_constitution):
    rules = generator.parse_constitution(temp_constitution)
    ids = [r["id"] for r in rules]
    assert "commit_task_id" in ids
    assert "quality_gate" in ids
    assert "coverage_check" in ids
    assert len(rules) == 3


def test_parse_severity_extraction(generator, temp_constitution):
    rules = generator.parse_constitution(temp_constitution)
    by_id = {r["id"]: r for r in rules}
    assert by_id["commit_task_id"]["severity"] == "critical"
    assert by_id["quality_gate"]["severity"] == "high"
    assert by_id["coverage_check"]["severity"] == "low"


def test_parse_threshold_extraction(generator, temp_constitution):
    rules = generator.parse_constitution(temp_constitution)
    by_id = {r["id"]: r for r in rules}
    assert by_id["commit_task_id"]["threshold"] == 90.0
    assert by_id["quality_gate"]["threshold"] == 85.0
    assert by_id["coverage_check"]["threshold"] == 80.0


def test_parse_empty_file(generator):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("# No rules here\n\nJust content.\n")
        path = f.name
    try:
        rules = generator.parse_constitution(path)
        assert rules == []
    finally:
        os.unlink(path)


def test_parse_rules_populated_on_generator(generator, temp_constitution):
    generator.parse_constitution(temp_constitution)
    assert len(generator.rules) == 3


# ── create_check_fn ──

def test_create_check_fn_commit_message(generator):
    rule = {"check_type": "commit_message", "threshold": None}
    fn = generator.create_check_fn(rule)
    assert callable(fn)

    # Simulate missing commit file → returns True
    with patch.dict(os.environ, {"COMMIT_MSG_FILE": "/nonexistent/commit_msg"}, clear=True):
        assert fn() is True

    # Simulate existing commit file with task ID → returns True
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as tf:
        tf.write("fix: [TASK-123] do something")
        tmp_path = tf.name
    try:
        with patch.dict(os.environ, {"COMMIT_MSG_FILE": tmp_path}):
            assert fn() is True
    finally:
        os.unlink(tmp_path)


def test_create_check_fn_quality_gate(generator):
    rule = {"check_type": "quality_gate", "threshold": 85}
    fn = generator.create_check_fn(rule)
    assert callable(fn)


def test_create_check_fn_coverage(generator):
    rule = {"check_type": "coverage", "threshold": 80}
    fn = generator.create_check_fn(rule)
    assert callable(fn)


def test_create_check_fn_security(generator):
    rule = {"check_type": "security", "threshold": 95}
    fn = generator.create_check_fn(rule)
    assert callable(fn)


def test_create_check_fn_generic_fallback(generator):
    rule = {"check_type": "unknown_type", "threshold": None}
    fn = generator.create_check_fn(rule)
    assert fn() is True


# ── generate ──

def test_generate_no_constitution(generator):
    generator.DEFAULT_CONSTITUTION_PATHS = ["/nonexistent/path.md"]
    policies = generator.generate()
    assert policies == []


def test_parse_r_format_standalone(generator):
    """Test ### R001: format when it's the first rule (no prior current_rule)."""
    content = """### R001: Test Rule
**Severity**: critical
**Threshold**: 95
This is a test rule description
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(content)
        path = f.name
    try:
        rules = generator.parse_constitution(path)
        assert len(rules) == 1
        assert rules[0]["id"] == "R001"
        assert rules[0]["severity"] == "critical"
        assert rules[0]["threshold"] == 95.0
    finally:
        os.unlink(path)


def test_create_check_fn_quality_gate_file_exists(generator):
    rule = {"check_type": "quality_gate", "threshold": 85}
    fn = generator.create_check_fn(rule)
    sf = Path(".methodology/.quality_score")
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text("90")
    try:
        assert fn() is True
    finally:
        sf.unlink(missing_ok=True)


def test_create_check_fn_coverage_file_exists(generator):
    rule = {"check_type": "coverage", "threshold": 70}
    fn = generator.create_check_fn(rule)
    cf = Path(".methodology/.coverage")
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_text("75")
    try:
        assert fn() is True
    finally:
        cf.unlink(missing_ok=True)


def test_create_check_fn_security_file_below_threshold(generator):
    rule = {"check_type": "security", "threshold": 95}
    fn = generator.create_check_fn(rule)
    sf = Path(".methodology/.security_score")
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text("50")
    try:
        assert fn() is False
    finally:
        sf.unlink(missing_ok=True)


def test_check_fn_quality_gate_file_missing(generator):
    """When .quality_score doesn't exist, check_fn returns True."""
    rule = {"check_type": "quality_gate", "threshold": 85}
    fn = generator.create_check_fn(rule)
    assert fn() is True  # file doesn't exist → pass

def test_check_fn_coverage_file_missing(generator):
    """When .coverage doesn't exist, check_fn returns True."""
    rule = {"check_type": "coverage", "threshold": 80}
    fn = generator.create_check_fn(rule)
    assert fn() is True

def test_check_fn_security_file_missing(generator):
    """When .security_score doesn't exist, check_fn returns True."""
    rule = {"check_type": "security", "threshold": 95}
    fn = generator.create_check_fn(rule)
    assert fn() is True


def test_main_sync_command():
    with patch("enforcement.constitution_policy_sync.ConstitutionPolicyGenerator.sync") as mock_sync, \
         patch("sys.argv", ["cp_sync.py", "sync"]):
        main()
        mock_sync.assert_called_once()


def test_main_preview_found():
    with patch("enforcement.constitution_policy_sync.ConstitutionPolicyGenerator.find_constitution") as mf, \
         patch("enforcement.constitution_policy_sync.ConstitutionPolicyGenerator.parse_constitution") as mp, \
         patch("sys.argv", ["cp_sync.py", "preview"]):
        mf.return_value = "/fake/constitution.md"
        mp.return_value = [{"id": "test", "description": "desc", "severity": "medium"}]
        main()
        mf.assert_called_once()


def test_main_preview_not_found():
    with patch("enforcement.constitution_policy_sync.ConstitutionPolicyGenerator.find_constitution") as mf, \
         patch("sys.argv", ["cp_sync.py", "preview"]):
        mf.return_value = None
        main()
        mf.assert_called_once()


def test_generate_with_rules(generator, temp_constitution):
    policies = generator.generate(temp_constitution)
    assert len(policies) == 3
    assert isinstance(policies[0], Policy)
    # critical severity → BLOCK
    assert policies[0].enforcement == EnforcementLevel.BLOCK
    assert policies[0].id == "commit_task_id"
    # low severity → WARN
    assert policies[2].enforcement == EnforcementLevel.WARN


# ── sync_to_engine ──

def test_sync_to_engine_returns_engine(generator, temp_constitution):
    engine = generator.sync_to_engine()
    assert isinstance(engine, PolicyEngine)


def test_sync_to_engine_clears_existing(generator, temp_constitution):
    engine = PolicyEngine()
    engine.policies.append(MagicMock())
    with patch.object(generator, "generate", return_value=[]):
        result = generator.sync_to_engine(engine)
        assert len(result.policies) == 0


def test_sync_to_engine_populates_policies(generator, temp_constitution):
    engine = PolicyEngine()
    with patch.object(generator, "generate", return_value=[
        Policy(id="test1", description="desc", check_fn=lambda: True,
               enforcement=EnforcementLevel.WARN, severity="medium", metadata={})
    ]):
        result = generator.sync_to_engine(engine)
        assert len(result.policies) == 1
        assert result.policies[0].id == "test1"


# ── sync ──

def test_sync_writes_file(generator, temp_constitution, temp_output):
    with patch.object(generator, "generate", return_value=[
        Policy(id="test-rule", description="A test rule", check_fn=lambda: True,
               enforcement=EnforcementLevel.BLOCK, severity="critical", metadata={})
    ]):
        policies = generator.sync(output_path=temp_output)
        assert len(policies) == 1
        content = Path(temp_output).read_text()
        assert "Auto-generated from Constitution.md" in content
        assert "test-rule" in content


# ── main ──

def test_main_sync_called():
    with patch("enforcement.constitution_policy_sync.ConstitutionPolicyGenerator.sync") as mock_sync, \
         patch("sys.argv", ["constitution_policy_sync.py"]):
        main()
        mock_sync.assert_called_once()


def test_main_generate_called():
    with patch("enforcement.constitution_policy_sync.ConstitutionPolicyGenerator.generate") as mock_gen, \
         patch("sys.argv", ["constitution_policy_sync.py", "generate"]):
        from enforcement.policy_engine import Policy, EnforcementLevel
        mock_gen.return_value = [
            Policy(id="p1", description="A test policy for generate command", check_fn=lambda: True,
                   enforcement=EnforcementLevel.BLOCK, severity="critical", metadata={})
        ]
        main()
        mock_gen.assert_called_once()
