"""What the project's own end-to-end verification actually runs (Round 52 站0).

`execute_verification_target` is the only one of Gate 4's sixteen dimensions
that executes the delivered system rather than reading its text or running its
test suite — and its weight is 0.00. What it executes is
`harness/toolchains/registry.py:288`'s `make verify-system`, whose recipe the
judged project writes.

Round 46 站5 fixed WHEN that target runs (gate 2 only → gates 2, 3 and 4).
Nothing has ever looked at WHAT it runs. `tests/test_verify_target_regated.py`
records the consequence in its own docstring — taskq-advance's target "chains
`test lint coverage`, none of the four steps its own SPEC §NFR-12 requires" —
and that observation produced no check.

Measured 2026-08-14 with `make -n verify-system` on the six projects on this
machine:

    project               swallows a verdict          invokes the product
    taskq                 —                           -m taskq --help + suite
    taskq-plus            —                           submit/run/status/graph/…
    taskq-renew           ruff … --exit-zero          NO
    taskq-advance         ruff … --exit-zero          NO
    taskq-api             --help >/dev/null 2>&1 ||true  -m taskq_api --help
    run-all-by-workflow   coverage combine … || true  -m taskq submit/list/clear

Two of the six re-run dimensions the gate has already scored and never touch
the delivered package at all. A third invokes it behind `|| true`, which make
cannot fail on. `--exit-zero` is the third swallowing idiom and it is the one
no round had noticed: Round 37 站4 removed `|| true` from the framework's own
CI template, and nothing has ever read a project's Makefile.

The rule these tests encode: a verification target whose verdict cannot be
reached is not a verdict, and a verification target that never invokes the
delivered entry point is not end-to-end verification of anything.

Round 72 站4 re-ran the same measurement on nine projects after removing the
`$(shell …)` refusal, and it reproduces the table above exactly — taskq-renew
and taskq-advance tautological, taskq-api's product line swallowed, the rest
clean — with taskq-new joining it as clean. Before that round taskq-new was
`unmeasured` and had 127 `owner=harness` ledger rows saying so.
"""

from __future__ import annotations

import shutil

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("make") is None, reason="make is the expansion SSOT"
)

# taskq-api's readyz-smoke and taskq-advance's lint step, reduced to the two
# lines that carry the idiom, plus make's own leading-dash form.
_SWALLOWING_MAKEFILE = """\
verify-system: lint smoke
\t@echo "verify-system: PASS"

lint:
\t.venv/bin/python -m ruff check . --exit-zero

smoke:
\t-rm -f /tmp/does-not-exist
\tPYTHONPATH=03-development/src python -m demo --help >/dev/null 2>&1 || true
"""

# taskq-advance's shape: every line is a tool some gate dimension already
# scored, and the delivered package is never named.
_TAUTOLOGICAL_MAKEFILE = """\
verify-system: test lint coverage
\t@echo "verify-system: PASS"

test:
\t.venv/bin/python -m pytest -q

lint:
\t.venv/bin/python -m ruff check .

coverage:
\t.venv/bin/python -m pytest --cov=03-development/src -q
"""

# taskq-plus's shape: a real end-to-end exercise of the delivered CLI.
_HONEST_MAKEFILE = """\
verify-system: test smoke
\t@echo "verify-system: PASS"

test:
\t.venv/bin/python -m pytest -q

smoke:
\t.venv/bin/python -m demo submit 'echo alpha'
\t.venv/bin/python -m demo status
"""

# An `include` puts recipe lines outside the text closure this module computes,
# so the leading-dash scan would be reading half a program.
_UNEXPANDABLE_MAKEFILE = """\
include other.mk

verify-system:
\t.venv/bin/python -m demo run
"""

# taskq-new's shape, reduced: `$(shell …)` in a variable assignment, and a
# target that runs the delivered app through a server runner. Round 72 站4
# removed the `$(shell)` refusal — it fired on `ROOT := $(shell pwd)` and cost
# taskq-new 127 "recipe not examined" ledger rows against a target that starts
# its FastAPI app and exits 1 on a bad /healthz.
_SHELL_ASSIGNMENT_MAKEFILE = """\
ROOT := $(shell pwd)
PYTHON ?= $(ROOT)/.venv/bin/python

verify-system:
\t@$(PYTHON) -m uvicorn demo.app:create_app --factory --host 127.0.0.1 &
\t@curl -fs http://127.0.0.1:8765/healthz
"""

# The same `$(shell)` Makefile with the product line swallowed. Before 站4 this
# was reported `unmeasured` and `blocking_reason` returned None for it, so the
# idiom the whole module exists to find went unexamined.
_SHELL_AND_SWALLOWED_MAKEFILE = """\
ROOT := $(shell pwd)
PYTHON ?= $(ROOT)/.venv/bin/python

verify-system:
\t@$(PYTHON) -m demo --help >/dev/null 2>&1 || true
"""


def _project(tmp_path, makefile: str):
    (tmp_path / "03-development" / "src" / "demo").mkdir(parents=True)
    (tmp_path / "03-development" / "src" / "demo" / "__init__.py").write_text("")
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    (tmp_path / "Makefile").write_text(makefile)
    return tmp_path


def test_a_recipe_that_cannot_fail_is_named_line_by_line(tmp_path):
    """Three idioms, three findings, each carrying the line it was found on.

    A finding that cannot be acted on is a finding nobody acts on (Round 48):
    the operator has to be told which line to edit, not that "the target
    swallows its verdict somewhere".
    """
    from core.quality_gate.verify_target import swallowed_verdicts

    rows = swallowed_verdicts(_project(tmp_path, _SWALLOWING_MAKEFILE))

    idioms = {r["idiom"] for r in rows}
    assert idioms == {"|| true", "--exit-zero", "leading-dash"}, rows
    for row in rows:
        assert row["line"].strip(), f"finding without its line: {row}"


def test_a_verify_target_that_never_invokes_the_product_is_tautological(tmp_path):
    """Re-running the dimensions the gate already scored is not verification.

    The condition is deliberately a single one — "no recipe line invokes the
    delivered entry point" — and not "every line is a tool some gate scores".
    Station 0's premise P5 measured the second: the registry's `ToolSpec.cmd`
    heads are `pytest` / `ruff` / `pyright`, while every real recipe spells
    them `.venv/bin/python -m pytest`, and `coverage` and `alembic` are not in
    the registry at all. A classifier built on that list would be guessing.
    """
    from core.quality_gate.verify_target import verify_target_findings

    tautological = verify_target_findings(_project(tmp_path, _TAUTOLOGICAL_MAKEFILE))
    assert tautological["tautological"] is True
    assert tautological["entrypoint_lines"] == []

    honest = verify_target_findings(
        _project(tmp_path / "honest", _HONEST_MAKEFILE)
    )
    assert honest["tautological"] is False
    assert any("-m demo submit" in line for line in honest["entrypoint_lines"])


def test_a_recipe_make_cannot_expand_is_unmeasured_not_clean(tmp_path):
    """An `include` puts recipe lines outside the closure, so we do not judge.

    Reporting such a Makefile as clean would be Round 46's defect: a scan whose
    input it could not read has abstained, not passed.
    """
    from core.quality_gate.verify_target import verify_target_findings

    findings = verify_target_findings(_project(tmp_path, _UNEXPANDABLE_MAKEFILE))

    assert findings["status"] == "unmeasured"
    assert "include" in findings["reason"]
    assert findings["tautological"] is None
    assert findings["swallowed"] is None


def test_a_shell_assignment_does_not_stop_the_recipe_being_read(tmp_path):
    """Round 72 站4. The refusal was about side effects the gate pays anyway.

    `system-verification`'s ToolSpec RUNS `make verify-system` at gates 2, 3
    and 4, so `make -n`'s side effects are a subset of ones already incurred.
    Refusing on them abstained from the only analysis of what that target does
    — and `unmeasured` has no second enforcer behind it: `blocking_reason`
    returns None for it and `make verify-system` itself passes.
    """
    from core.quality_gate.verify_target import (
        blocking_reason, verify_target_findings,
    )

    project = _project(tmp_path, _SHELL_AND_SWALLOWED_MAKEFILE)
    findings = verify_target_findings(project)

    assert findings["status"] == "expanded", findings["reason"]
    assert blocking_reason(project), (
        "the product is invoked behind `|| true` and the whole recipe went "
        "unexamined because a variable assignment used $(shell …)"
    )


def test_a_server_runner_invokes_the_product_it_is_given(tmp_path):
    """`-m uvicorn pkg.app:factory` runs the delivered app, not uvicorn.

    Measured while removing the `$(shell)` refusal: with the recipe finally
    readable, taskq-new's target — which starts its FastAPI app, polls
    /healthz and /readyz and exits 1 on either — was reported as "never
    invokes the delivered entry point", because the module sits in the
    runner's first positional argument rather than after `-m`. Trading an
    abstention for a false accusation is not a fix.
    """
    from core.quality_gate.verify_target import verify_target_findings

    findings = verify_target_findings(
        _project(tmp_path, _SHELL_ASSIGNMENT_MAKEFILE)
    )

    assert findings["status"] == "expanded", findings["reason"]
    assert findings["tautological"] is False
    assert any("demo.app:create_app" in line
               for line in findings["entrypoint_lines"]), findings
