"""Real-mutmut integration smoke for the mutation_enforcer pipeline.

mutation_enforcer is this repo's highest-churn module (21 fix commits; #43
needed a v2, #106 needed #106b) and its unit tests mock every subprocess —
they encode our ASSUMPTIONS about mutmut, which is exactly what those bugs
were wrong about. This test runs the real tool end to end on a mini fixture
project: workdir isolation, setup.cfg generation/rewrite, cache publication,
and sqlite-cache counting all execute for real.
"""

import shutil
from pathlib import Path

import pytest

from core.quality_gate.mutation_enforcer import (
    _count_mutmut_results,
    compute_mutation_score,
)

FIXTURE = Path(__file__).parent / "fixtures" / "mutmut_smoke"
BARE_CFG_FIXTURE = Path(__file__).parent / "fixtures" / "mutmut_bare_cfg"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        shutil.which("mutmut") is None,
        reason="mutmut not on PATH — pip install 'mutmut<3' "
               "(requirements.txt pins 2.5.1; activate .venv locally)",
    ),
]


def test_real_mutmut_end_to_end(tmp_path):
    project = tmp_path / "proj"
    shutil.copytree(FIXTURE, project)

    ok, score, message = compute_mutation_score(project)

    assert ok, f"pipeline failed: {message}"
    # The fixture leaves the `value > hi` branch untested by design: the
    # real run must report BOTH kills and at least one survivor. Exact
    # mutant counts are mutmut-version-dependent — pinning them would test
    # the tool, not the pipeline.
    assert 0.0 < score < 100.0, (score, message)

    cache = project / ".mutmut-cache"
    assert cache.is_file(), "cache must be PUBLISHED to project root (Bug #105)"
    killed, survived = _count_mutmut_results(cache)
    assert killed >= 1, (killed, survived)
    assert survived >= 1, (killed, survived)


def test_real_mutmut_on_a_project_that_declares_no_pytest_config(tmp_path):
    """Round 35 站0 — the same pipeline on the shape that broke.

    `mutmut_smoke` declares `[tool:pytest] testpaths` and `pythonpath`; every
    run of the test above therefore exercised the well-configured project.
    `mutmut_bare_cfg` copies a live project: a setup.cfg that exists and says
    nothing about pytest, with the source reached through a conftest.py
    sys.path insertion. The workdir bootstrap wrote no pytest target for it,
    mutmut's baseline collected nothing, and the dimension reported 0.0.
    """
    project = tmp_path / "proj"
    shutil.copytree(BARE_CFG_FIXTURE, project)

    ok, score, message = compute_mutation_score(project)

    assert ok, f"pipeline failed on a project with no [tool:pytest]: {message}"
    assert score is not None and 0.0 < score < 100.0, (score, message)
