"""The framework runs a tool that decides this, and says nobody does (Round 54 站0).

Round 51 站2 classified each SAB `architecture_constraints` entry as `enforced`
or `declared_only`, and its docstring drew the right conclusion from the wrong
premise: "when a check has no executor, the fix is not to invent one, it is to
write down that there is none". True — but
`CONSTRAINT_EXECUTOR_CANDIDATES` holds **two** entries, both import-linter,
while the gate runs a dozen tools. So "no executor" was measured against a
two-tool world.

Station 0 classified all 23 declared_only rows across the seven projects here
and the two-state reading is wrong for 16 of them.

**bandit is already deciding five of them.** `security` resolves to bandit for
Python (`harness/toolchains/registry.py:524`), the gate runs it and scores it,
and bandit enables every test unless the project opts out. Measured on a
fixture, bandit flags:

    B602  subprocess(..., shell=True)         no_shell_true
    B307  eval(...)                           no_eval_exec_*
    B102  exec(...)                           no_eval_exec_*
    B608  f-string and +-concatenated SQL     no_string_sql_concatenation

taskq-super's `.bandit` is `skips = []` — nothing disabled — and its Gate 4
scored security 100.0 while reporting that `no_shell_true_no_eval_no_exec` and
`no_string_sql_concatenation` have no executor. Six of the seven projects have
no bandit config at all, which for bandit means *everything is on*: the
polarity is the opposite of import-linter, where an absent config means nothing
is checked.

**`independence` is a contract kind the registry never learned.** Three
constraints are independence-shaped and two projects have an independence
contract.

**The rest are a missing project config, not a missing tool.** taskq-super's
`no_circular_dependencies` needs a `layers` contract (it has only
`independence`) and `sqlalchemy_only_in_repository` needs a `forbidden` one.
That is actionable — the framework can name the tool and the contract to write —
and it is a different fact from `fr07_round_trip_must_preserve_data`, which
nothing in this repository can decide at all.

So the classification needs three states, not two:

    enforced       a tool the framework runs decides this, evidence names it
    unconfigured   such a tool exists and this project has not enabled it
    declared_only  nothing in this framework can decide it

Only the middle one is blockable, because only for it can the message say what
to do. Blocking `declared_only` would make projects delete true statements.

**What `enforced` claims, and what it does not.** The same fixture that shows
bandit catching `subprocess.run(cmd, shell=True)` shows it missing
`subprocess.run(cmd, **{"shell": True})` (reported as B603, not B602) and
`fn = eval; fn(x)` (not reported at all). bandit is a syntactic scanner and
`enforced` inherits its reach. That is the existing meaning of the word here —
`contract_coverage_gap` already reports separately that an import-linter
contract can be kept while leaving modules unconstrained — and the evidence
string has to carry the boundary rather than imply completeness.
"""

from __future__ import annotations

import json
from pathlib import Path


def _project(tmp_path: Path, *, importlinter: str = "", bandit: str = "") -> Path:
    (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".methodology" / "state.json").write_text(
        json.dumps({"language": "python"}), encoding="utf-8")
    if importlinter:
        (tmp_path / ".importlinter").write_text(importlinter, encoding="utf-8")
    if bandit:
        (tmp_path / ".bandit").write_text(bandit, encoding="utf-8")
    return tmp_path


_INDEPENDENCE = """\
[importlinter]
root_packages =
    demo

[importlinter:contract:indep]
name = config and errors independence
type = independence
modules =
    demo.config
    demo.errors
"""


def test_a_constraint_bandit_decides_is_enforced_and_names_the_tool(tmp_path):
    """bandit runs by default, so a shell/eval/exec constraint has an executor."""
    from core.quality_gate.arch_constraints import (
        STATUS_ENFORCED, classify_constraints,
    )

    project = _project(tmp_path)  # no bandit config at all = every test enabled
    rows = {r["constraint"]: r for r in classify_constraints(
        ["no_shell_true_no_eval_no_exec", "no_string_sql_concatenation"], project)}

    shell = rows["no_shell_true_no_eval_no_exec"]
    assert shell["status"] == STATUS_ENFORCED
    assert shell["executor"] == "bandit"
    assert "B602" in shell["evidence"] and "B307" in shell["evidence"]

    sql = rows["no_string_sql_concatenation"]
    assert sql["status"] == STATUS_ENFORCED
    assert "B608" in sql["evidence"]


def test_a_check_the_project_switched_off_is_unconfigured_not_enforced(tmp_path):
    """`skips` is the project turning the executor off — that is not enforcement.

    Partial disablement counts: a constraint saying "no shell, no eval, no
    exec" is not enforced by a bandit that has been told to ignore eval.
    """
    from core.quality_gate.arch_constraints import (
        STATUS_UNCONFIGURED, classify_constraints,
    )

    project = _project(tmp_path, bandit="[bandit]\nskips = B307\n")
    row = classify_constraints(["no_shell_true_no_eval_no_exec"], project)[0]

    assert row["status"] == STATUS_UNCONFIGURED
    assert "B307" in row["evidence"], (
        "the row must name which test the project disabled, or the operator "
        "cannot tell this from 'bandit is not installed'"
    )


def test_an_independence_constraint_with_an_independence_contract_is_enforced(
    tmp_path,
):
    """The registry knew `layers` and `forbidden` and not the third kind."""
    from core.quality_gate.arch_constraints import (
        STATUS_ENFORCED, STATUS_UNCONFIGURED, classify_constraints,
    )

    with_contract = _project(tmp_path / "with", importlinter=_INDEPENDENCE)
    row = classify_constraints(
        ["errors_and_config_are_independence_modules"], with_contract)[0]
    assert row["status"] == STATUS_ENFORCED
    assert row["executor"] == "import-linter"

    without = _project(tmp_path / "without", importlinter="""\
[importlinter]
root_packages =
    demo

[importlinter:contract:forbid]
name = no sqlalchemy outside repository
type = forbidden
source_modules =
    demo.api
""")
    row2 = classify_constraints(
        ["errors_and_config_are_independence_modules"], without)[0]
    assert row2["status"] == STATUS_UNCONFIGURED, (
        "a project with contracts but not this kind has not enabled it"
    )


def test_only_the_unconfigured_state_blocks(tmp_path):
    """The gate stops for what the project can fix, and records the rest.

    Positive and negative control in one place: `unconfigured` must produce a
    reason naming the tool and what to configure; `declared_only` must produce
    none, because the only way a project could satisfy a block on it is by
    deleting a true statement from its own SAB.
    """
    from core.quality_gate.arch_constraints import (
        classify_constraints, unconfigured_blocking_reason,
    )

    project = _project(tmp_path)
    rows = classify_constraints(
        ["no_circular_dependencies",                    # unconfigured
         "fr07_round_trip_must_preserve_data"],         # declared_only
        project,
    )
    reason = unconfigured_blocking_reason(rows)
    assert reason is not None
    assert "no_circular_dependencies" in reason
    assert "import-linter" in reason and "layers" in reason
    assert "fr07_round_trip_must_preserve_data" not in reason

    declared_only_rows = [r for r in rows
                          if r["constraint"].startswith("fr07")]
    assert unconfigured_blocking_reason(declared_only_rows) is None
