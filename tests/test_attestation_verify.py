"""PR 3: trace attestation + verifier tests.

Confirms:
  - build_attestation produces a stable SHA-256 over canonical JSON
  - verify_attestation exits 0 when matrix unchanged
  - exit 1 on matrix drift (someone forgot to re-run --write)
  - exit 2 when attestation.json missing
  - exit 3 on schema mismatch / malformed JSON
  - canonical JSON ordering does not affect SHA (canonical form is stable)
"""
import json
import subprocess
from pathlib import Path

import pytest


# Playbook §6: dynamic mutation-oracle marker
pytestmark = pytest.mark.mutation_oracle


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """Minimal repo with FR-01/02 traced; attestation can be built/verified."""
    arch = tmp_path / "02-architecture"
    arch.mkdir()
    (arch / "SAD.md").write_text("FR-01: alpha\nFR-02: beta\n")
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text('"""[FR-01]"""\n')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text('"""[FR-01]"""\n')
    # .methodology/trace is required by write_attestation
    (tmp_path / ".methodology" / "trace").mkdir(parents=True)
    return tmp_path


def _init_git(project: Path) -> None:
    """Initialize a git repo so _git_sha returns a real SHA."""
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "test@x"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=project, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=project, check=True)


# ---------------------------------------------------------------------------
# build_attestation
# ---------------------------------------------------------------------------

def test_build_attestation_has_expected_fields(fixture_repo):
    from scripts.build_trace_attestation import build_attestation
    att = build_attestation(fixture_repo)
    assert att["schema"] == "harness/traceability/attestation/v1"
    assert "git_sha" in att
    assert "content_sha256" in att
    assert att["tool"].endswith("build_trace_attestation.py")
    assert "matrix" in att
    assert "FR-01" in att["matrix"]["requirements"]


def test_build_attestation_canonical_json_is_stable(fixture_repo):
    """SHA is over canonical JSON; key order in source must not affect it."""
    from scripts.build_trace_attestation import _canonical_json

    a = {"x": 1, "y": [1, 2], "z": {"a": 1, "b": 2}}
    b = {"z": {"b": 2, "a": 1}, "y": [1, 2], "x": 1}
    assert _canonical_json(a) == _canonical_json(b)


def test_build_attestation_sha_changes_when_matrix_changes(fixture_repo):
    from scripts.build_trace_attestation import build_attestation
    att1 = build_attestation(fixture_repo)
    # Mutate: add a [FR-02] annotation
    (fixture_repo / "core" / "b.py").write_text('"""[FR-02]"""\n')
    att2 = build_attestation(fixture_repo)
    assert att1["content_sha256"] != att2["content_sha256"]


def test_build_attestation_with_overlay(fixture_repo):
    from scripts.build_trace_attestation import build_attestation
    overlay = fixture_repo / "TRACEABILITY_MATRIX.overlay.yaml"
    overlay.write_text(
        "schema: harness/traceability/overlay/v1\n"
        "overrides:\n"
        "  - fr_id: FR-99\n"
        "    status: verified\n"
        "    code_files: [core/manual.py]\n"
    )
    att = build_attestation(fixture_repo, overlay_path=overlay)
    assert "FR-99" in att["matrix"]["requirements"]
    assert att["overlay_errors"] == []


def test_write_attestation_creates_both_files(fixture_repo):
    from scripts.build_trace_attestation import build_attestation, write_attestation
    att = build_attestation(fixture_repo)
    canonical, latest = write_attestation(fixture_repo, att)
    assert canonical.exists()
    assert latest.exists()
    canonical_text = canonical.read_text()
    latest_text = latest.read_text()
    assert canonical_text == latest_text


# ---------------------------------------------------------------------------
# verify_attestation exit codes
# ---------------------------------------------------------------------------

def test_verify_exits_0_on_clean(fixture_repo):
    from scripts.build_trace_attestation import build_attestation, write_attestation
    from scripts.verify_trace_attestation import (
        verify_attestation, EXIT_CLEAN,
    )
    att = build_attestation(fixture_repo)
    write_attestation(fixture_repo, att)
    code, msg = verify_attestation(fixture_repo)
    assert code == EXIT_CLEAN
    assert "matches" in msg.lower()


def test_verify_exits_1_on_sha_mismatch(fixture_repo):
    from scripts.build_trace_attestation import build_attestation, write_attestation
    from scripts.verify_trace_attestation import (
        verify_attestation, EXIT_MISMATCH,
    )
    att = build_attestation(fixture_repo)
    write_attestation(fixture_repo, att)
    # Now mutate: add a new [FR-XX] annotation
    (fixture_repo / "core" / "b.py").write_text('"""[FR-02]"""\n')
    code, msg = verify_attestation(fixture_repo)
    assert code == EXIT_MISMATCH
    assert "mismatch" in msg.lower()


def test_verify_exits_2_when_attestation_missing(tmp_path):
    from scripts.verify_trace_attestation import (
        verify_attestation, EXIT_MISSING,
    )
    code, msg = verify_attestation(tmp_path)
    assert code == EXIT_MISSING
    assert "not found" in msg.lower() or "build-trace-attestation" in msg


def test_verify_exits_3_on_malformed_json(fixture_repo):
    from scripts.verify_trace_attestation import (
        verify_attestation, EXIT_SCHEMA,
    )
    att_path = fixture_repo / ".methodology" / "trace" / "attestation.json"
    att_path.write_text("{ not valid json", encoding="utf-8")
    code, msg = verify_attestation(fixture_repo)
    assert code == EXIT_SCHEMA
    assert "malformed" in msg.lower() or "schema" in msg.lower()


def test_verify_exits_3_on_wrong_schema(fixture_repo):
    from scripts.verify_trace_attestation import (
        verify_attestation, EXIT_SCHEMA,
    )
    att_path = fixture_repo / ".methodology" / "trace" / "attestation.json"
    att_path.write_text(
        json.dumps({"schema": "wrong/schema/v9", "content_sha256": "x"}),
        encoding="utf-8",
    )
    code, msg = verify_attestation(fixture_repo)
    assert code == EXIT_SCHEMA


# ---------------------------------------------------------------------------
# CLI wrapper
# ---------------------------------------------------------------------------

def test_cli_verify_trace_exits_0_after_build(fixture_repo):
    """End-to-end: build then verify via harness_cli subprocess."""
    import sys
    cli = Path(__file__).resolve().parent.parent / "harness_cli.py"
    py = sys.executable
    # Build
    r1 = subprocess.run(
        [py, str(cli), "build-trace-attestation",
         "--project", str(fixture_repo)],
        capture_output=True, text=True,
    )
    assert r1.returncode == 0
    # Verify
    r2 = subprocess.run(
        [py, str(cli), "verify-trace", "--project", str(fixture_repo)],
        capture_output=True, text=True,
    )
    assert r2.returncode == 0
    assert "matches" in r2.stderr.lower() or "matches" in r2.stdout.lower()


def test_cli_verify_trace_exits_2_when_missing(tmp_path):
    import sys
    cli = Path(__file__).resolve().parent.parent / "harness_cli.py"
    py = sys.executable
    r = subprocess.run(
        [py, str(cli), "verify-trace", "--project", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert r.returncode == 2


# ---------------------------------------------------------------------------
# Preflight integration
# ---------------------------------------------------------------------------

def test_preflight_includes_attestation_status(fixture_repo, monkeypatch):
    """preflight_traceability returns attestation status field."""
    from scripts.build_trace_attestation import build_attestation, write_attestation

    write_attestation(fixture_repo, build_attestation(fixture_repo))

    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)

    from core.phase_hooks import PhaseHooks
    h = PhaseHooks(str(fixture_repo), phase=5, enable_kill_switch=False)
    result = h.preflight_traceability()
    assert "attestation" in result
    assert result["attestation"] in ("clean", "missing", "mismatch",
                                     "schema-error", "error", "skipped")


def test_preflight_p5_blocks_on_missing_attestation(fixture_repo):
    """P5+ with missing attestation must NOT silently pass."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    # Ensure no attestation exists
    att = fixture_repo / ".methodology" / "trace" / "attestation.json"
    if att.exists():
        att.unlink()
    from core.phase_hooks import PhaseHooks
    h = PhaseHooks(str(fixture_repo), phase=5, enable_kill_switch=False)
    result = h.preflight_traceability()
    # P5 blocking + missing attestation = passed False
    assert result["blocking"] is True
    assert result["passed"] is False
    assert result["attestation"] in ("missing", "error")


def test_preflight_p3_passes_with_missing_attestation(fixture_repo):
    """P3 is informational; missing attestation must not block."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    att = fixture_repo / ".methodology" / "trace" / "attestation.json"
    if att.exists():
        att.unlink()
    from core.phase_hooks import PhaseHooks
    h = PhaseHooks(str(fixture_repo), phase=3, enable_kill_switch=False)
    result = h.preflight_traceability()
    assert result["blocking"] is False
    assert result["passed"] is True
