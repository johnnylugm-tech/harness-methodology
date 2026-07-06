"""Global test fixtures and mocks for harness-methodology test suite."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# The mutmut smoke fixture is a standalone mini project: its test file only
# imports inside the mutation_enforcer's workdir run, never from this suite.
collect_ignore_glob = ["fixtures/mutmut_smoke/*"]

# CRG is mandatory — mcp_tools module only exists inside Claude Code runtime.
# Mock it here so all tests that transitively import CRG code can run.
# Tests that need real MCP tool behavior for non-CRG paths (e.g. Hermes/Gemini)
# should patch those paths explicitly.
if "mcp_tools" not in sys.modules:
    sys.modules["mcp_tools"] = MagicMock()


# ---------------------------------------------------------------------------
# CWD invariance (2026-07-02 incident)
# ---------------------------------------------------------------------------
#
# A host project ran bare `pytest` from ITS root; a stale testpaths made
# pytest fall back to rootdir collection, which swept in harness/tests/.
# Several tests here exercise production writers whose project_root argument
# has a CWD fallback (generate_quality_manifest, decision logs, effort
# tracker, steering history) — they wrote into the HOST project's
# .methodology/, truncating its quality_manifest.json (fr_ids 3→1, gate1
# wiped) and corrupting the pipeline state.
#
# This suite documents "tests run with harness/ as cwd" (see
# test_methodology_consistency.py). Pin that assumption for every test so
# the suite behaves identically regardless of the caller's cwd: any residual
# CWD-relative write can only land in harness/.methodology (gitignored
# scratch), never in a host project's live state.

_HARNESS_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _cwd_pinned_to_harness_root(monkeypatch):
    monkeypatch.chdir(_HARNESS_ROOT)


# ---------------------------------------------------------------------------
# Improvement I: make_sab_from_sad fixture factory
# ---------------------------------------------------------------------------
#
# Tests previously wrote inline JSON literals via
#     (method_dir / "SAB.json").write_text(__import__("json").dumps(sab_json))
# which drifts out of sync with the real SABSpec.to_dict() schema (added
# fields like fr_module_traceability would silently miss the test). This
# fixture builds SAB.json from a SAD.md snippet using the same parser the
# production pipeline uses (extract_sab_from_sad → SABSpec.to_dict()),
# so changes to the schema propagate to every test that uses it.

@pytest.fixture
def write_sab_from_sad(tmp_path):
    """Return a function: (sad_text) → Path (the SAB.json written).

    Usage:
        def test_x(write_sab_from_sad):
            sab_path = write_sab_from_sad(sad_text)
            ...
    """
    from core.quality_gate.sab_parser import extract_sab_from_sad

    def _writer(sad_text: str) -> Path:
        sad_path = tmp_path / "SAD.md"
        sad_path.write_text(sad_text)
        sab = extract_sab_from_sad(sad_path)
        sab_path = tmp_path / ".methodology" / "SAB.json"
        sab_path.parent.mkdir(parents=True, exist_ok=True)
        if sab is None:
            raise ValueError("extract_sab_from_sad returned None for test")
        sab_path.write_text(json.dumps(sab.to_dict()))
        return sab_path

    return _writer
