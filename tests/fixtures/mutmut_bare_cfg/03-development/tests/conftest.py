"""The project reaches its own source here, not through setup.cfg.

This is the whole point of the fixture: nothing in setup.cfg tells pytest
where the tests or the source live, exactly as on the project that exposed
the workdir bootstrap defect.
"""
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
