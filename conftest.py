# conftest.py — pytest configuration for harness-methodology self-tests
import sys
from pathlib import Path

import pytest

# Ensure repo root is on sys.path so `from harness.xxx import` works
sys.path.insert(0, str(Path(__file__).parent))


def pytest_configure(config):
    """Register custom markers (Item 10)."""
    markers = [
        "core: Core pipeline tests (fast, always run)",
        "extended: Extended integration tests (medium, run on PR)",
        "integration: Full integration tests (slow, run on merge to main)",
        "gate: Gate evaluation tests (critical path)",
        "slow: Tests that take >10 seconds",
        "constitution: Constitution compliance tests",
        "auto_fix: Auto-fix engine tests",
        "smoke: Quick smoke tests (run on every commit)",
        "contract: Spec contract compliance tests",
        "quality: Quality gate dimension tests",
    ]
    for marker in markers:
        config.addinivalue_line("markers", marker)


def pytest_collection_modifyitems(config, items):
    """Auto-assign 'core' marker to unmarked tests (Item 10)."""
    known = {"core", "extended", "integration", "gate", "slow", "constitution",
             "auto_fix", "smoke", "parametrize", "skip", "skipif", "xfail",
             "usefixtures", "filterwarnings"}
    for item in items:
        item_markers = {m.name for m in item.iter_markers()}
        if not (item_markers & known):
            item.add_marker(pytest.mark.core)
