"""The witness turned up, but it was a stand-in (Round 51 站0).

Round 46 站1 settled the case where a requirement's witness never ran: a
skipped test does not verify anything. This is the layer under it — the test
ran, passed, and was never in the same room as the thing it certifies.

Measured 2026-08-14 across the six projects on this machine, scanning for
pytest fixtures marked `autouse=True` whose body patches a module the project's
own `SAB.json` lists under `high_risk_modules`:

    taskq                  high_risk=2   autouse-patches-target=0
    taskq-plus             high_risk=3   autouse-patches-target=0
    taskq-renew            high_risk=3   autouse-patches-target=0
    taskq-advance          high_risk=4   autouse-patches-target=0
    taskq-api              high_risk=4   autouse-patches-target=17
    run-all-by-workflow    high_risk=2   autouse-patches-target=0

Seventeen hits across ten files, including both files named `*_e2e.py`. The
control that matters is taskq-advance: same SPEC.md, same language, same four
high-risk modules, zero hits. The signal is not "this project uses mocks" — it
is "the module the architecture calls high-risk is replaced before every test
in the file, so no test in that file can observe it".

What that certified: taskq-api's `test_coverage` scored 100.0 and
`integration_coverage` scored 80.0 over a suite in which
`repository.session.get_session` — whose body is a bare `raise RuntimeError` —
is monkeypatched away by an autouse fixture in seven test modules, and
`service.auth.verify_key` is replaced with `lambda raw, hashed: bool(raw) and
bool(hashed)` inside the integration tests that carry the NFR-10 evidence and
the T-02/T-03 threat verification.

The rule these tests encode: a dimension scored over a suite that replaced the
declared boundary is not a dimension the framework measured. Round 50 站2 gave
that idea its vocabulary — `score_source` — and this adds the third reason a
number is not `SCORE_SOURCE_FRAMEWORK`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_SAB = {
    "version": "1.0",
    "high_risk_modules": [
        "taskq_api.repository.session",
        "taskq_api.service.auth",
        "taskq_api.service.runner",
    ],
}

# taskq-api/03-development/tests/integration/test_task_lifecycle_e2e.py:23-40,
# reduced to the two fixtures and nothing else.
_STUBBING_CONFTEST = '''\
import pytest


@pytest.fixture(autouse=True)
def _stub_verify_key(monkeypatch):
    from taskq_api.service import auth as _auth
    monkeypatch.setattr(
        _auth, "verify_key", lambda raw, hashed: bool(raw) and bool(hashed),
    )


@pytest.fixture(autouse=True)
def _stub_get_session(monkeypatch):
    from taskq_api.repository import session as _session
    monkeypatch.setattr(_session, "get_session", lambda: _FakeSession())
'''

# taskq-advance/03-development/tests/conftest.py:50-60, same reduction. Opt-in,
# and it hands the test a real SQLite file rather than replacing the module.
_REAL_CONFTEST = '''\
import os
import pytest


@pytest.fixture()
def sqlite_db_url(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("TASKQ_"):
            monkeypatch.delenv(name, raising=False)
    url = f"sqlite:///{tmp_path / 'taskq.db'}"
    monkeypatch.setenv("TASKQ_DB_URL", url)
    return url
'''


def _project(tmp_path: Path, conftest: str) -> Path:
    (tmp_path / ".methodology").mkdir(parents=True)
    (tmp_path / ".methodology" / "SAB.json").write_text(
        json.dumps(_SAB), encoding="utf-8")
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir(parents=True)
    (tests / "conftest.py").write_text(conftest, encoding="utf-8")
    (tests / "test_fr01.py").write_text(
        "def test_fr01_smoke():\n    assert True\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def stubbing_project(tmp_path: Path) -> Path:
    return _project(tmp_path, _STUBBING_CONFTEST)


@pytest.fixture()
def real_project(tmp_path: Path) -> Path:
    return _project(tmp_path, _REAL_CONFTEST)


def test_an_autouse_fixture_that_replaces_a_declared_boundary_is_named(stubbing_project):
    from core.quality_gate.boundary_realism import stubbed_boundaries

    rows = stubbed_boundaries(stubbing_project)
    modules = {r["module"] for r in rows}
    assert "taskq_api.repository.session" in modules, (
        "an autouse fixture replaces the SAB's own high-risk session module "
        "for every test in the file and the framework has no record of it"
    )
    assert "taskq_api.service.auth" in modules
    for row in rows:
        assert row["fixture"] and row["file"], (
            "a finding has to say which fixture in which file, or nobody can "
            "act on it (Round 48: a halt that names nobody)"
        )


def test_an_opt_in_fixture_holding_a_real_dependency_is_not_a_finding(real_project):
    """The control. taskq-advance scores zero on this scan and must keep doing so."""
    from core.quality_gate.boundary_realism import stubbed_boundaries

    assert stubbed_boundaries(real_project) == [], (
        "a function-scoped fixture that points TASKQ_DB_URL at a real SQLite "
        "file is the opposite of the defect — flagging it would make the "
        "signal useless"
    )


def test_a_dimension_scored_over_a_stubbed_suite_is_not_framework_measured():
    """`weight_covered` must not count a number whose witness was a stand-in.

    Same machinery as Round 50 站2: `measurement_scope` selects on
    `score_source`, not on `score is not None`. This adds the third value that
    keeps a dimension out of the covered weight.
    """
    from harness.harness_bridge import (
        SCORE_SOURCE_FRAMEWORK,
        SCORE_SOURCE_STUBBED_BOUNDARY,
        DimResult,
        measurement_scope,
    )

    assert SCORE_SOURCE_STUBBED_BOUNDARY != SCORE_SOURCE_FRAMEWORK

    dims = [
        DimResult(name="linting", score=100.0, threshold=80.0,
                  score_source=SCORE_SOURCE_FRAMEWORK),
        DimResult(name="test_coverage", score=100.0, threshold=80.0,
                  score_source=SCORE_SOURCE_STUBBED_BOUNDARY),
    ]
    scope = measurement_scope(dims, {"linting": 0.5, "test_coverage": 0.5})
    assert "test_coverage" in scope["dimensions_unscored"], (
        "a 100.0 produced by a suite that replaced the boundary under test "
        "still counted toward weight_covered"
    )
    assert scope["weight_covered"] == pytest.approx(0.5)


def test_the_marker_reaches_the_gate_artifact_and_the_ledger(stubbing_project):
    """The producer, not just the vocabulary.

    Round 30's lesson: a constant nobody sets and a reader nobody feeds is a
    mechanism that is half built. This drives the function finalize_gate calls
    and checks both of its outputs — the score_source written into the
    breakdown, and the ledger row naming which fixture in which file.
    """
    import json

    from harness.harness_bridge import (
        SCORE_SOURCE_STUBBED_BOUNDARY,
        GateContext,
        _mark_stubbed_boundary_dimensions,
    )

    ctx = GateContext(
        gate_num=4, config={}, project_root=str(stubbing_project), phase=6,
        fr_id=None, ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
        work_dir=str(stubbing_project / ".sessi-work"), sab_data={},
    )
    raw = {"breakdown": {
        "test_coverage": {"score": 100.0},
        "integration_coverage": {"score": 80.0},
        "linting": {"score": 100.0},
        "mutation_testing": {"score": 79.0},
    }}

    findings = _mark_stubbed_boundary_dimensions(ctx, raw)
    assert findings

    assert raw["breakdown"]["test_coverage"]["score_source"] == SCORE_SOURCE_STUBBED_BOUNDARY
    assert raw["breakdown"]["integration_coverage"]["score_source"] == SCORE_SOURCE_STUBBED_BOUNDARY
    assert raw["breakdown"]["test_coverage"]["score"] == 100.0, (
        "the marker records where the number came from; it does not change "
        "the number (Round 32 站4)"
    )
    assert "score_source" not in raw["breakdown"]["linting"]
    assert "score_source" not in raw["breakdown"]["mutation_testing"], (
        "a mutant inside a patched-away module survives, so a stubbed "
        "boundary already lowers the mutation score — marking it would "
        "describe the wrong direction"
    )

    ledger = stubbing_project / ".methodology" / "degradations.jsonl"
    rows = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    row = next(r for r in rows if r["component"] == "gate:stubbed-boundary")
    assert row["owner"] == "project"
    assert "taskq_api.repository.session" in row["data"]["modules"]
    assert sorted(row["data"]["dimensions_marked"]) == [
        "integration_coverage", "test_coverage"]


def test_a_clean_suite_produces_no_marker_and_no_ledger_row(real_project):
    from harness.harness_bridge import GateContext, _mark_stubbed_boundary_dimensions

    ctx = GateContext(
        gate_num=4, config={}, project_root=str(real_project), phase=6,
        fr_id=None, ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
        work_dir=str(real_project / ".sessi-work"), sab_data={},
    )
    raw = {"breakdown": {"test_coverage": {"score": 100.0}}}
    assert _mark_stubbed_boundary_dimensions(ctx, raw) == []
    assert "score_source" not in raw["breakdown"]["test_coverage"]
    assert not (real_project / ".methodology" / "degradations.jsonl").exists()
