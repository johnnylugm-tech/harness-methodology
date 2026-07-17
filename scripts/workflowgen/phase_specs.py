"""Per-phase workflow assembly — facade.

Round 15 split this into one module per phase (spec_phase1.py .. spec_phase8.py)
plus spec_shared.py for the one genuinely cross-phase renderer (_render_meta).
This module now only re-exports generate_phase1..generate_phase8 so existing
consumers (scripts/workflowgen/generate_workflows.py's GENERATORS dict,
tests/test_workflowgen.py's direct `phase_specs.generate_phase8()` call) keep
working unchanged. See each spec_phaseN.py module for that phase's renderers.
"""
from __future__ import annotations

from .spec_phase1 import generate_phase1  # noqa: F401
from .spec_phase2 import generate_phase2  # noqa: F401
from .spec_phase3 import generate_phase3  # noqa: F401
from .spec_phase4 import generate_phase4  # noqa: F401
from .spec_phase5 import generate_phase5  # noqa: F401
from .spec_phase6 import generate_phase6  # noqa: F401
from .spec_phase7 import generate_phase7  # noqa: F401
from .spec_phase8 import generate_phase8  # noqa: F401
