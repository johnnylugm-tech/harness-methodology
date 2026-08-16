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


# ── Round 53 站4/站5a ────────────────────────────────────────────────────────
#
# Two defects in the station above, both found by running it against a real
# project instead of this file's fixtures.
#
# taskq-super is the first project to complete P1-P8 under Round 52. Its
# `make verify-system` is the whole pytest suite plus `-m taskq_api --help`,
# and its conftest replaces `taskq_api.repository.session.transactional` with
# an in-memory stand-in in an `autouse` fixture whose own comment still says
# "GREEN TODO: replace this fixture with a real session fixture ... once
# `taskq_api.repository.session` is implemented".
#
# At Gates 2, 3 and 4 this station reported `reach_status: measured` and
# `obligations_unmet: []` (git show 7c9bda8/e07a6ee/5535033 of
# .methodology/delivery_fingerprint.json). The function body *was* executed —
# inside the very pytest run that stubs it. The obligation was written to mean
# "something the test suite did not configure has to run this"; when
# verify-system contains the test suite, the discharging process and the
# stubbing process are the same process. The check is circular, and it is my
# defect, not the project's.
#
# Station 0's premise P2 measured the fix and refuted half of it. coverage's
# parallel data files do encode the writing pid
# (`cov.data.<host>.pid13390.<rand>`), and a `.pth` can record a pid->process
# map without disturbing `process_startup()`. But `sys.argv` at `.pth` time for
# `python -m pytest x` is `["-m", "x"]` — the module name is gone — so argv at
# startup cannot identify the runner. An `atexit` hook sees the completed argv
# and `sys.modules`, which names it outright.
#
# The second defect is arithmetic. `gate:verify-system-reach` fired 116 times
# on that run, every one "no reach artifact", and correlating each row's `ts`
# against `gate_timestamps.jsonl` puts all 116 at Gate 1 and none at Gate 2, 3
# or 4. Gate 1's config has no `execute_verification_target` dimension, so no
# reach artifact can exist there and the question is not this gate's to ask.
# 18.5% of that project's whole degradation ledger is this station asking a
# structurally unanswerable question, filed under owner `harness`.


def test_the_stubbing_suite_may_not_discharge_its_own_obligation(tmp_path):
    """A process that ran the test suite cannot be the witness that it works."""
    from core.quality_gate.verify_system_reach import (
        harvest_selection,
        suite_pids,
        write_sidecar_row,
    )

    project = _project(tmp_path)
    work = project / ".sessi-work"
    for pid, mods, argv in (
        (111, ["pytest"], ["/venv/lib/pytest/__main__.py", "03-development/tests"]),
        (222, [], ["/venv/bin/python", "-m", "demo", "--help"]),
    ):
        write_sidecar_row(project, {"pid": pid, "argv": argv, "mods": mods})
    for pid in (111, 222):
        (work / f"verify_system.coverage.host.pid{pid}.abc").write_text("x")

    assert suite_pids(project) == {111}

    kept, reason = harvest_selection(project, work / "verify_system.coverage")
    assert [p.name for p in kept] == ["verify_system.coverage.host.pid222.abc"]
    assert reason is None


def test_a_target_that_is_only_the_test_suite_is_unmeasured_not_discharged(tmp_path):
    """taskq-super's shape: nothing outside the suite ran, so nothing is proven.

    Round 35 站2 again, and it has to be `unmeasured` rather than `unmet` —
    the project may well be fine; the framework simply has no witness. A
    verdict of "unmet" here would be the framework charging a project for its
    own blind spot (Round 32 站4).
    """
    from core.quality_gate.verify_system_reach import (
        harvest_selection,
        unmet_obligations,
        write_reach_unmeasured,
        write_sidecar_row,
    )

    project = _project(tmp_path)
    work = project / ".sessi-work"
    write_sidecar_row(project, {
        "pid": 111, "argv": ["/venv/lib/pytest/__main__.py", "03-development/tests"],
        "mods": ["pytest"],
    })
    (work / "verify_system.coverage.host.pid111.abc").write_text("x")

    kept, reason = harvest_selection(project, work / "verify_system.coverage")
    assert kept == []
    assert reason and "test suite" in reason

    write_reach_unmeasured(project, reason)
    verdict = unmet_obligations(project)
    assert verdict["status"] == "unmeasured"
    assert verdict.get("unmet") is None
    assert "test suite" in verdict["reason"]


def test_a_gate_without_the_dimension_is_not_asked_the_question(tmp_path):
    """Gate 1 cannot run verify-system, so it records nothing about it.

    Not "quieten the log" — the gate config is the single source of which
    dimensions a gate has, and a question outside that set has no answer to
    record. Round 46 站1's rule cuts the other way here: abstaining is not
    passing, but a question that was never in scope was never abstained from.
    """
    from harness.harness_bridge import _verify_system_reach_block

    project = _project(tmp_path)
    ledger = project / ".methodology" / "degradations.jsonl"

    class _Ctx:
        project_root = str(project)
        gate_num = 1
        config = {"dimensions": [{"name": "linting"}, {"name": "test_coverage"}]}

    assert _verify_system_reach_block(_Ctx()) == []
    rows = ledger.read_text(encoding="utf-8") if ledger.exists() else ""
    assert "verify-system-reach" not in rows, (
        "116 of taskq-super's 626 ledger rows were this question asked at "
        "Gate 1, where the dimension does not exist"
    )

    class _Gate4Ctx(_Ctx):
        gate_num = 4
        config = {"dimensions": [{"name": "execute_verification_target"}]}

    assert _verify_system_reach_block(_Gate4Ctx()) == []
    rows = ledger.read_text(encoding="utf-8") if ledger.exists() else ""
    assert "verify-system-reach" in rows, (
        "at a gate that does have the dimension, an unmeasured reach is still "
        "recorded — the positive control for the scoping above"
    )
