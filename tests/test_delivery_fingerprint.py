"""The product-side facts a delivery leaves behind (Round 52 站0).

Every guard the framework applies to a judged project is a unary predicate:
`f(tree) ≥ threshold`. "Regression" is a binary relation, `f(new) < f(old)`,
so a system built only from unary predicates cannot express it. The one
exception in the whole repository is
`harness/harness_bridge.py:1409`'s `_architecture_regression_reason` — Gate 4
only, the same project's own P4 baseline only, CRG structural metrics only.
It cannot see a function body that is one `raise`.

Meanwhile the harness ratchets *itself*: line counts, swallowed exceptions,
the guard registry, golden bytes. It knows the shape and has never applied it
to what it judges.

This is the first half of that, and deliberately only the first half: the
facts get written down. There is no outlier verdict, because there is nowhere
for a cross-project corpus to live — the harness is a submodule of each
project and cannot see the others' runs. Inventing a checked-in reference
distribution would be one more thing declared with no executor (Round 43), so
the reopen condition is recorded in the adjudication ledger instead.

Nothing here measures anything new. Every field is a value some existing
producer already computed, which is what the test below asserts: the
fingerprint is rendered from those producers, not recomputed beside them.
"""

from __future__ import annotations

import json

_SAB = {"version": "1.0", "high_risk_modules": ["demo.session"]}

_STUBBING_CONFTEST = '''\
import pytest


@pytest.fixture(autouse=True)
def _stub_get_session(monkeypatch):
    from demo import session as _session

    monkeypatch.setattr(_session, "get_session", lambda: object())
'''


def _project(tmp_path):
    src = tmp_path / "03-development" / "src" / "demo"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "session.py").write_text("def get_session():\n    return 1\n")
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir(parents=True)
    (tests / "conftest.py").write_text(_STUBBING_CONFTEST)
    meth = tmp_path / ".methodology"
    meth.mkdir()
    (meth / "SAB.json").write_text(json.dumps(_SAB))
    (tmp_path / ".sessi-work").mkdir()
    (tmp_path / ".coveragerc").write_text(
        "[run]\nsource = 03-development/src\nomit = 03-development/src/demo/cli.py\n"
    )
    return tmp_path


def test_the_fingerprint_is_rendered_from_the_producers_not_recomputed(tmp_path):
    """Each field equals what its existing producer returns, called directly.

    Round 39 站3's rule: a table that restates a value is a table that will
    one day disagree with it. The assertion is equality against the producer,
    so a future edit that starts computing a field here fails the test.
    """
    from core.quality_gate.boundary_realism import stubbed_boundaries
    from core.quality_gate.cov_utils import read_coveragerc_omit
    from core.quality_gate.delivery_fingerprint import build_fingerprint

    project = _project(tmp_path)
    fp = build_fingerprint(project)

    assert fp["stubbed_boundaries"]["count"] == len(stubbed_boundaries(project))
    assert fp["stubbed_boundaries"]["modules"] == ["demo.session"]
    assert fp["coverage"]["omit"] == read_coveragerc_omit(project)
    # Station 1 and station 2's verdicts travel with the rest of the facts.
    assert "verify_system" in fp
    assert set(fp["verify_system"]) >= {"tautological", "swallowed", "reach_status"}


def test_the_fingerprint_lands_beside_the_other_gate_records(tmp_path):
    """`.methodology/` — the same place the SAB and the CRG baselines live."""
    from core.quality_gate.delivery_fingerprint import write_fingerprint

    project = _project(tmp_path)
    out = write_fingerprint(project)

    assert out == project / ".methodology" / "delivery_fingerprint.json"
    assert json.loads(out.read_text())["stubbed_boundaries"]["count"] == 1
