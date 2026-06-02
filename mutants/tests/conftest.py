"""Global test fixtures and mocks for harness-methodology test suite."""

import sys
from unittest.mock import MagicMock

# CRG is mandatory — mcp_tools module only exists inside Claude Code runtime.
# Mock it here so all tests that transitively import CRG code can run.
# Tests that need real MCP tool behavior for non-CRG paths (e.g. Hermes/Gemini)
# should patch those paths explicitly.
if "mcp_tools" not in sys.modules:
    sys.modules["mcp_tools"] = MagicMock()
