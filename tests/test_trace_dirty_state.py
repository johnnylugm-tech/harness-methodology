"""PR 6: mtime-based trace dirty-state probe tests.

Confirms `_trace_dirty_state` returns passed=True only when:
  - `.methodology/trace/attestation.json` exists
  - `SAD.md` is older (or absent)
  - newest `tests/test_fr*.py` is older (or absent)

All tests must run in <100ms total — the probe is a fast inner-loop
guard for the pre-commit hook. Use `pytest --durations=0` to confirm.
"""
import os
import time
from pathlib import Path

import pytest


# Playbook §6: dynamic mutation-oracle marker
pytestmark = pytest.mark.mutation_oracle


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """Minimal repo: SAD.md + 1 test_fr file + attestation. All mtimes
    set so attestation is the newest by default (clean state)."""
    arch = tmp_path / "02-architecture"
    arch.mkdir()
    (arch / "SAD.md").write_text("FR-01: alpha\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_fr_01.py").write_text('"""[FR-01]"""\n')
    trace = tmp_path / ".methodology" / "trace"
    trace.mkdir(parents=True)
    # Set mtimes so attestation is newer than SAD and test by 2 seconds
    now = time.time()
    sad_t = now - 10
    test_t = now - 5
    att_t = now
    os.utime(arch / "SAD.md", (sad_t, sad_t))
    os.utime(tmp_path / "tests" / "test_fr_01.py", (test_t, test_t))
    (trace / "attestation.json").write_text("{}")
    os.utime(trace / "attestation.json", (att_t, att_t))
    return tmp_path


def test_clean_when_attestation_newer_than_sad_and_tests(fixture_repo):
    """The happy path: attestation is the newest, all sources are clean."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from cli.phase_cmds import _trace_dirty_state
    result = _trace_dirty_state(fixture_repo)
    assert result["passed"] is True
    assert result["reason"] == "trace attestation is current"


def test_fails_when_sad_modified_after_attestation(fixture_repo):
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    # Touch SAD.md to make it newer than attestation
    sad = fixture_repo / "02-architecture" / "SAD.md"
    new_mtime = (fixture_repo / ".methodology" / "trace"
                 / "attestation.json").stat().st_mtime + 5
    os.utime(sad, (new_mtime, new_mtime))

    from cli.phase_cmds import _trace_dirty_state
    result = _trace_dirty_state(fixture_repo)
    assert result["passed"] is False
    assert "SAD.md" in result["reason"]
    assert result["staler"] == "02-architecture/SAD.md"


def test_fails_when_test_file_modified_after_attestation(fixture_repo):
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    # Touch tests/test_fr_01.py to be newer than attestation
    test = fixture_repo / "tests" / "test_fr_01.py"
    new_mtime = (fixture_repo / ".methodology" / "trace"
                 / "attestation.json").stat().st_mtime + 5
    os.utime(test, (new_mtime, new_mtime))

    from cli.phase_cmds import _trace_dirty_state
    result = _trace_dirty_state(fixture_repo)
    assert result["passed"] is False
    assert "test_fr_01.py" in result["reason"]
    assert result["staler"] == "tests/test_fr_01.py"


def test_fails_when_attestation_missing(tmp_path):
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    # No .methodology/trace/attestation.json
    (tmp_path / "02-architecture").mkdir()
    (tmp_path / "02-architecture" / "SAD.md").write_text("FR-01\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_fr_01.py").write_text("# stub")

    from cli.phase_cmds import _trace_dirty_state
    result = _trace_dirty_state(tmp_path)
    assert result["passed"] is False
    assert "attestation.json missing" in result["reason"]
    assert "build-trace-attestation" in result["reason"]
