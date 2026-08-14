"""What `make verify-system` actually executed, not what it imported (Round 52 站0).

Round 51 站3 named the modules the test suite replaces with an `autouse`
stand-in before any test can observe them. It stopped there: the number was
marked `stubbed_boundary` and the run continued. The layer under it is the
question that marker raises and does not answer — *does anything, anywhere,
run the real one?*

The framework already runs one thing that is not the test suite: the project's
own `make verify-system`. So the obligation writes itself, with no threshold
to choose: **whatever the suite replaced, the verification target has to
execute for real.** A project that stubs nothing owes nothing.

Station 0's premise P3 measured taskq-api and refuted the obvious criterion.
Running its verify-system's product step under coverage:

    taskq_api/repository/session.py   executed=  8 lines, 0 inside a function
    taskq_api/service/auth.py         executed= 21 lines, 2 inside a function
    taskq_api/service/runner.py       executed=  0 lines

`repository/session.py` shows up in the coverage report at 27% from
`-m taskq_api --help` alone — imports and `def` headers. A module-granularity
check would have called that reached. The two lines inside a function in
`auth.py` are `install_log_redaction`'s body, which a module-level call runs at
import; `verify_key`, the function the fixtures replace, is not among them.

Hence attribute granularity: the obligation is `module.attr`, and it is met
only when a statement *inside that function's body* executed. Premise P4
confirmed the other half — `grep` finds no path at all from `migrations/` to
`service.auth` or `get_session`, so the three alembic steps cannot reach them
either.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_SAB = {"version": "1.0", "high_risk_modules": ["demo.session"]}

_STUBBING_CONFTEST = '''\
import pytest


@pytest.fixture(autouse=True)
def _stub_get_session(monkeypatch):
    from demo import session as _session

    monkeypatch.setattr(_session, "get_session", lambda: object())
'''

# Line 1 is the import, 4 is the `def`, 5-6 are the body. A caller that merely
# imports this module executes 1 and 4 and nothing else.
_SESSION_SRC = '''\
import os


def get_session():
    url = os.environ["DEMO_DB_URL"]
    return url
'''


def _project(tmp_path, *, with_conftest: bool = True):
    src = tmp_path / "03-development" / "src" / "demo"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "session.py").write_text(_SESSION_SRC)
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir(parents=True)
    if with_conftest:
        (tests / "conftest.py").write_text(_STUBBING_CONFTEST)
    meth = tmp_path / ".methodology"
    meth.mkdir()
    (meth / "SAB.json").write_text(json.dumps(_SAB))
    (tmp_path / ".sessi-work").mkdir()
    return tmp_path


def _coverage_json(project, executed: list[int], *, relative: bool = True):
    """A coverage JSON report naming demo/session.py with *executed* lines.

    Relative by default, because that is what coverage.py writes: its paths are
    relative to the run's cwd — the project root — not to wherever the harness
    process is. An absolute-only fixture is what let the first implementation
    of `_dotted` resolve every path against `Path.cwd()` and map a real
    28-file report to an empty reach map without a test noticing.
    """
    path = project / ".sessi-work" / "verify_system_cov.json"
    src = Path("03-development") / "src" / "demo" / "session.py"
    key = str(project / src) if not relative else str(src)
    path.write_text(json.dumps({
        "files": {key: {"executed_lines": executed, "missing_lines": []}}
    }))
    return path


@pytest.mark.parametrize("relative", [True, False])
def test_the_reach_artifact_records_executed_lines_per_delivered_module(
    tmp_path, relative
):
    """The artifact is keyed by dotted module, because the obligation is.

    Both path shapes, because coverage.py writes the relative one and the
    absolute one is what a hand-written fixture reaches for.
    """
    from core.quality_gate.verify_system_reach import write_reach

    project = _project(tmp_path)
    out = write_reach(
        project, _coverage_json(project, [1, 4, 5, 6], relative=relative))

    assert out == project / ".sessi-work" / "verify_system_reach.json"
    data = json.loads(out.read_text())
    assert data["status"] == "measured"
    assert data["modules"]["demo.session"]["executed_lines"] == [1, 4, 5, 6]


def test_a_stubbed_function_that_never_ran_for_real_is_named(tmp_path):
    """Import-surface lines do not discharge the obligation; body lines do.

    Both halves are asserted against the same module, so the test fails if the
    check regresses to module granularity in either direction.
    """
    from core.quality_gate.verify_system_reach import unmet_obligations, write_reach

    imports_only = _project(tmp_path / "imports_only")
    write_reach(imports_only, _coverage_json(imports_only, [1, 4]))
    verdict = unmet_obligations(imports_only)
    assert verdict["status"] == "measured"
    assert [(r["module"], r["attr"]) for r in verdict["unmet"]] == [
        ("demo.session", "get_session")
    ]

    for_real = _project(tmp_path / "for_real")
    write_reach(for_real, _coverage_json(for_real, [1, 4, 5, 6]))
    assert unmet_obligations(for_real)["unmet"] == []


def test_a_project_that_stubs_nothing_owes_nothing(tmp_path):
    """Five of the six projects are here — the obligation set is empty."""
    from core.quality_gate.verify_system_reach import unmet_obligations, write_reach

    project = _project(tmp_path, with_conftest=False)
    write_reach(project, _coverage_json(project, [1, 4]))

    verdict = unmet_obligations(project)
    assert verdict["status"] == "measured"
    assert verdict["unmet"] == []


def test_reach_that_could_not_be_measured_is_not_reported_as_clean(tmp_path):
    """No artifact is not an empty artifact (Round 46 站1, Round 35 站2).

    This check blocks a gate. A measurement that did not happen must therefore
    produce neither a pass nor a fail — `unmet` is absent, not `[]`, so no
    caller can read "nothing unmet" out of "nothing measured".
    """
    from core.quality_gate.verify_system_reach import unmet_obligations

    project = _project(tmp_path)  # obligations exist, no reach artifact written

    verdict = unmet_obligations(project)
    assert verdict["status"] == "unmeasured"
    assert verdict.get("unmet") is None
    assert verdict["reason"]
