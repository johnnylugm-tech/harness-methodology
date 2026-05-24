import os
import shutil
import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from core.pre_flight import (
    check_env_vars,
    check_cli_tools,
    check_database_connectivity,
    run_phase3_pre_flight,
)

def test_check_env_vars():
    with patch.dict(os.environ, {"EXISTING_VAR": "123"}, clear=True):
        missing = check_env_vars(["EXISTING_VAR", "MISSING_VAR"])
        assert "MISSING_VAR" in missing
        assert "EXISTING_VAR" not in missing

def test_check_cli_tools():
    def mock_which(cmd):
        if cmd == "MISSING_TOOL":
            return None
        return f"/usr/bin/{cmd}"

    with patch("core.pre_flight.shutil.which", side_effect=mock_which):
        missing = check_cli_tools(["EXISTING_TOOL", "MISSING_TOOL"])
        assert "MISSING_TOOL" in missing
        assert "EXISTING_TOOL" not in missing

def test_check_database_connectivity_not_postgres():
    ok, diag = check_database_connectivity("mysql://localhost")
    assert ok is True
    assert diag is None

@patch("core.pre_flight.shutil.which")
def test_check_database_connectivity_no_psql(mock_which):
    mock_which.return_value = None
    ok, diag = check_database_connectivity("postgres://localhost")
    assert ok is False
    assert diag == "missing_psql"

@patch("core.pre_flight.shutil.which")
@patch("core.pre_flight.subprocess.run")
def test_check_database_connectivity_success(mock_run, mock_which):
    mock_which.return_value = "/usr/bin/psql"
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_run.return_value = mock_proc

    ok, diag = check_database_connectivity("postgres://localhost")
    assert ok is True
    assert diag is None

@patch("core.pre_flight.shutil.which")
@patch("core.pre_flight.subprocess.run")
def test_check_database_connectivity_failure(mock_run, mock_which):
    mock_which.return_value = "/usr/bin/psql"
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_run.return_value = mock_proc

    ok, diag = check_database_connectivity("postgres://localhost")
    assert ok is False
    assert diag is None  # non-zero returncode, no specific diag

@patch("core.pre_flight.shutil.which")
@patch("core.pre_flight.subprocess.run")
def test_check_database_connectivity_timeout(mock_run, mock_which):
    mock_which.return_value = "/usr/bin/psql"
    mock_run.side_effect = subprocess.TimeoutExpired("cmd", 5)

    ok, diag = check_database_connectivity("postgres://localhost")
    assert ok is False
    assert diag == "timeout"

@patch("core.pre_flight.check_cli_tools")
@patch("core.pre_flight.check_env_vars")
def test_run_phase3_pre_flight_missing_tools_and_vars(mock_check_vars, mock_check_tools):
    mock_check_tools.return_value = ["pytest"]
    mock_check_vars.return_value = ["DATABASE_URL"]

    passed, errors = run_phase3_pre_flight(Path("/tmp"))

    assert not passed
    assert len(errors) == 2
    assert "pytest" in errors[0]
    assert "DATABASE_URL" in errors[1]

@patch("core.pre_flight.check_cli_tools")
@patch("core.pre_flight.check_env_vars")
@patch("core.pre_flight.check_database_connectivity")
def test_run_phase3_pre_flight_success(mock_db, mock_check_vars, mock_check_tools):
    mock_check_tools.return_value = []
    mock_check_vars.return_value = []
    mock_db.return_value = (True, None)

    with patch.dict(os.environ, {"DATABASE_URL": "postgres://localhost"}):
        passed, errors = run_phase3_pre_flight(Path("/tmp"))

        assert passed
        assert len(errors) == 0

@patch("core.pre_flight.check_cli_tools")
@patch("core.pre_flight.check_env_vars")
@patch("core.pre_flight.check_database_connectivity")
def test_run_phase3_pre_flight_missing_psql(mock_db, mock_check_vars, mock_check_tools):
    mock_check_tools.return_value = []
    mock_check_vars.return_value = []
    mock_db.return_value = (False, "missing_psql")

    with patch.dict(os.environ, {"DATABASE_URL": "postgres://localhost"}):
        passed, errors = run_phase3_pre_flight(Path("/tmp"))

        assert not passed
        assert any("psql not installed" in e for e in errors)

@patch("core.pre_flight.check_cli_tools")
@patch("core.pre_flight.check_env_vars")
@patch("core.pre_flight.check_database_connectivity")
def test_run_phase3_pre_flight_db_timeout(mock_db, mock_check_vars, mock_check_tools):
    mock_check_tools.return_value = []
    mock_check_vars.return_value = []
    mock_db.return_value = (False, "timeout")

    with patch.dict(os.environ, {"DATABASE_URL": "postgres://localhost"}):
        passed, errors = run_phase3_pre_flight(Path("/tmp"))

        assert not passed
        assert any("timed out" in e for e in errors)