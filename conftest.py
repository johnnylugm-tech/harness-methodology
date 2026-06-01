# conftest.py — pytest configuration for harness-methodology self-tests
import sys
from pathlib import Path

import pytest

# Ensure repo root is on sys.path so `from harness.xxx import` works.
# When running under mutmut, this conftest is copied to mutants/ and __file__
# points to mutants/conftest.py — we need the PARENT of mutants/ (the real repo
# root) on sys.path so all imports resolve correctly.
_this_dir = Path(__file__).resolve().parent
if _this_dir.name == "mutants":
    sys.path.insert(0, str(_this_dir.parent))
else:
    sys.path.insert(0, str(_this_dir))


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


# Test files that cover the mutated modules (see setup.cfg [mutmut] paths_to_mutate).
# All other test files are ignored when mutmut runs pytest from mutants/.
_MUTMUT_TEST_SCOPE = frozenset({
    "test_tool_runners.py",
    "test_sab_parser.py",
})


def pytest_ignore_collect(collection_path, config):
    """When running under mutmut (rootdir = mutants/), only collect test files
    that directly cover the mutated modules.  All other test files import
    modules that were not copied into mutants/ and would raise ImportError,
    aborting the entire stats-collection run before any mutant is tested.
    """
    rootdir = Path(str(config.rootdir))
    if rootdir.name == "mutants":
        if collection_path.is_file() and collection_path.suffix == ".py":
            if collection_path.name not in _MUTMUT_TEST_SCOPE:
                return True  # tell pytest to skip this file
    return None


def pytest_collection_modifyitems(config, items):
    """Auto-assign 'core' marker to unmarked tests (Item 10)."""
    known = {"core", "extended", "integration", "gate", "slow", "constitution",
             "auto_fix", "smoke", "parametrize", "skip", "skipif", "xfail",
             "usefixtures", "filterwarnings"}
    for item in items:
        item_markers = {m.name for m in item.iter_markers()}
        if not (item_markers & known):
            item.add_marker(pytest.mark.core)
