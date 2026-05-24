import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

from core.pre_flight import (
    check_env_vars,
    check_cli_tools,
    check_database_connectivity,
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


# ---------------------------------------------------------------------------
# cmd_finalize_env_check — staleness check
# ---------------------------------------------------------------------------

def _make_env_check_args(project_path):
    """Minimal argparse.Namespace for cmd_finalize_env_check."""
    class Args:
        pass
    a = Args()
    a.project = str(project_path)
    a.phase = 3
    a.fr_id = None
    return a


def _write_sentinel(project_path, ts: datetime) -> None:
    sf = project_path / ".sessi-work" / "sentinels" / "env_check.flag"
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(ts.isoformat() + "\n", encoding="utf-8")


def _write_result(project_path, checked_at: datetime, ready: bool = True) -> None:
    sessi = project_path / ".sessi-work"
    sessi.mkdir(parents=True, exist_ok=True)
    data = {
        "ready": ready,
        "checked_at": checked_at.isoformat(),
        "summary": "ok",
        "env_vars": {"required": []},
        "cli_tools": {"required": []},
        "infra_services": {"required": []},
    }
    (sessi / "env_check_result.json").write_text(json.dumps(data), encoding="utf-8")


def test_finalize_env_check_fresh_result_no_warn(tmp_path, capsys):
    """Result written after sentinel → no staleness warning."""
    from harness_cli import cmd_finalize_env_check

    sentinel_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    checked_at = sentinel_time + timedelta(seconds=30)  # 30 s after sentinel

    _write_sentinel(tmp_path, sentinel_time)
    _write_result(tmp_path, checked_at)

    rc = cmd_finalize_env_check(_make_env_check_args(tmp_path))
    captured = capsys.readouterr()
    assert rc == 0
    assert "[WARN]" not in captured.out


def test_finalize_env_check_stale_result_warns(tmp_path, capsys):
    """Result predates sentinel by >10 s → staleness warning printed."""
    from harness_cli import cmd_finalize_env_check

    sentinel_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    checked_at = sentinel_time - timedelta(seconds=60)  # 60 s before sentinel

    _write_sentinel(tmp_path, sentinel_time)
    _write_result(tmp_path, checked_at)

    rc = cmd_finalize_env_check(_make_env_check_args(tmp_path))
    captured = capsys.readouterr()
    assert rc == 0  # still passes (warn, not block)
    assert "[WARN]" in captured.out
    assert "stale" in captured.out.lower() or "previous run" in captured.out.lower()


def test_finalize_env_check_within_tolerance_no_warn(tmp_path, capsys):
    """Result is 5 s before sentinel (within 10 s tolerance) → no warning."""
    from harness_cli import cmd_finalize_env_check

    sentinel_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    checked_at = sentinel_time - timedelta(seconds=5)  # within tolerance

    _write_sentinel(tmp_path, sentinel_time)
    _write_result(tmp_path, checked_at)

    rc = cmd_finalize_env_check(_make_env_check_args(tmp_path))
    captured = capsys.readouterr()
    assert rc == 0
    assert "[WARN]" not in captured.out
