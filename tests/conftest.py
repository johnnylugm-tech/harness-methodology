"""Global test fixtures and mocks for harness-methodology test suite."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# CRG is mandatory — mcp_tools module only exists inside Claude Code runtime.
# Mock it here so all tests that transitively import CRG code can run.
# Tests that need real MCP tool behavior for non-CRG paths (e.g. Hermes/Gemini)
# should patch those paths explicitly.
if "mcp_tools" not in sys.modules:
    sys.modules["mcp_tools"] = MagicMock()


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
        sab_path.write_text(json.dumps(sab.to_dict()))
        return sab_path

    return _writer
